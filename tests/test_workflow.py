"""End-to-end workflow over the real demo fixtures, plus the demo/live boundary."""

from __future__ import annotations


import pytest

from desk.config import FitStatus
from desk.money import Money
from desk.repositories import analysis as analysis_repo
from desk.repositories import companies as companies_repo
from desk.repositories import followup as followup_repo
from desk.repositories import supply as supply_repo
from desk.services import demo as demo_service
from desk.services import pricing
from desk.services.workflow import Workflow, dashboard_counts


@pytest.fixture
def seeded(db, demo_config, clock):
    demo_service.seed(db, demo_config, clock)
    return db


@pytest.fixture
def computed(seeded, demo_config, clock):
    Workflow(seeded, demo_config, clock).recompute()
    return seeded


def find_offer(db, external_id: str):
    with db.open() as conn:
        return supply_repo.find_offer_by_external(conn, "fixture_demo", external_id)


# -- the fixtures are what the manual asks for --------------------------


def test_the_demo_snapshot_has_the_documented_shape(seeded):
    with seeded.open() as conn:
        counts = {
            "companies": conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "supply_offers": conn.execute(
                "SELECT COUNT(*) FROM offers WHERE price_evidence_type = 'supply_asking'"
            ).fetchone()[0],
            "briefs": conn.execute("SELECT COUNT(*) FROM buyer_briefs").fetchone()[0],
            "comparables": conn.execute(
                "SELECT COUNT(*) FROM observations WHERE price_evidence_type != 'supply_asking'"
            ).fetchone()[0],
        }
    assert counts["companies"] == 14
    assert counts["supply_offers"] == 23
    assert counts["briefs"] == 7
    assert counts["comparables"] == 51


def test_every_demo_company_uses_a_reserved_example_domain(seeded):
    with seeded.open() as conn:
        websites = [
            row["website"]
            for row in conn.execute("SELECT website FROM companies WHERE website IS NOT NULL")
        ]
        emails = [
            row["business_email"]
            for row in conn.execute(
                "SELECT business_email FROM contacts WHERE business_email IS NOT NULL"
            )
        ]
    assert websites
    assert all(site.endswith(".example") for site in websites)
    assert all(email.endswith(".example") for email in emails)


def test_no_demo_phone_number_is_routable(seeded):
    with seeded.open() as conn:
        phones = [
            row["business_phone"]
            for row in conn.execute(
                "SELECT business_phone FROM contacts WHERE business_phone IS NOT NULL"
            )
        ]
    assert phones
    # +999 is not an assigned country code, so none of these can be dialled.
    assert all(phone.startswith("+999") for phone in phones)


def test_every_demo_row_is_marked_as_demo_data(seeded):
    tables = ("companies", "contacts", "offers", "vehicles", "observations", "buyer_briefs")
    with seeded.open() as conn:
        for table in tables:
            live_rows = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE is_demo = 0"
            ).fetchone()[0]
            assert live_rows == 0, f"{table} contains rows not marked as demo"


# -- the demonstration narrative ----------------------------------------


def test_the_confirmed_brief_matches_the_physical_stock_car(computed):
    with computed.open() as conn:
        rows = analysis_repo.list_matches(
            conn, fit_statuses=(FitStatus.SPECIFICATION_FIT.value,)
        )
        offers = {row["id"]: supply_repo.get_offer(conn, row["offer_id"]) for row in rows}

    assert rows, "the demonstration needs at least one specification fit"
    assert any(offers[row["id"]].external_record_id == "OFR-001" for row in rows)


def test_the_allocation_cannot_satisfy_a_physical_stock_requirement(computed):
    with computed.open() as conn:
        allocation = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-002")
        matches = analysis_repo.list_matches(conn, offer_id=allocation.id)
        brief_matches = [m for m in matches if m["brief_id"]]

    assert brief_matches
    for match in brief_matches:
        assert match["fit_status"] == FitStatus.NO_MATCH.value
        assert "allocation" in match["failed_rules"]


def test_the_four_seat_car_is_rejected_for_a_five_seat_requirement(computed):
    with computed.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-007")
        matches = [m for m in analysis_repo.list_matches(conn, offer_id=offer.id) if m["brief_id"]]
    assert matches
    assert all(m["fit_status"] == FitStatus.NO_MATCH.value for m in matches)
    assert any("homologated seats" in m["failed_rules"] for m in matches)


def test_the_unknown_option_needs_verification_and_is_not_assumed(computed):
    with computed.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-008")
        matches = [m for m in analysis_repo.list_matches(conn, offer_id=offer.id) if m["brief_id"]]
    relevant = [m for m in matches if "rear_seat_comfort" in m["unknown_rules"]]
    assert relevant
    assert relevant[0]["fit_status"] == FitStatus.NEEDS_VERIFICATION.value


def test_the_expired_brief_needs_reconfirmation(computed):
    with computed.open() as conn:
        matches = analysis_repo.list_matches(
            conn, fit_statuses=(FitStatus.BRIEF_EXPIRED.value,)
        )
    assert matches
    assert matches[0]["score"] == 0


def test_the_margin_scheme_car_produces_no_automatic_comparison(computed, demo_config, clock):
    workflow = Workflow(computed, demo_config, clock)
    with computed.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-003")
        result = workflow.comparables_for(conn, offer, persist=False)
    assert not result.has_median
    gap = pricing.asking_price_gap(target_offer=offer, comparables=result)
    assert not gap.available


def test_the_g63_median_matches_the_manual_before_any_replay(computed, demo_config, clock):
    """Five compatible retail comparables give a 235,000 EUR median."""
    workflow = Workflow(computed, demo_config, clock)
    with computed.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-001")
        result = workflow.comparables_for(conn, offer, persist=False)

    assert result.has_median
    assert result.distinct_vehicles == 5
    assert result.median == Money.from_decimal("235000", "EUR")

    gap = pricing.asking_price_gap(target_offer=offer, comparables=result)
    assert gap.gap == Money.from_decimal("35000", "EUR")
    assert "not profit" in gap.explanation


def test_a_car_with_too_few_peers_gets_no_median(computed, demo_config, clock):
    """The insufficient-comparables path, on a car the snapshot keeps thin.

    OFR-021 states no VAT basis, so nothing is comparable with it at all. This
    used to point at the S-Class, which has since been given a full set of peers
    so the S 450 / S 580 variant split could be demonstrated; the behaviour under
    test is the same one.
    """
    workflow = Workflow(computed, demo_config, clock)
    with computed.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-021")
        result = workflow.comparables_for(conn, offer, persist=False)
    assert result.status == "insufficient_comparables"
    assert result.distinct_vehicles < 3
    assert result.median is None


def test_the_two_s_class_variants_get_separate_medians(computed, demo_config, clock):
    """The reason the S-Class now carries a full comparable set.

    An S 450 and an S 580 share the W223 body. Pooling them would produce a
    median describing no car anyone can buy, so each is priced against its own
    variant and the two medians are tens of thousands apart.
    """
    workflow = Workflow(computed, demo_config, clock)
    with computed.open() as conn:
        s450 = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-011")
        s580 = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-012")
        low = workflow.comparables_for(conn, s450, persist=False)
        high = workflow.comparables_for(conn, s580, persist=False)
    assert low.has_median and high.has_median
    assert low.median < high.median
    assert (high.median - low.median).minor_units > 2_000_000  # more than 20,000 EUR apart


def test_one_vin_links_two_offers_in_the_demo_data(computed):
    with computed.open() as conn:
        first = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-001")
        second = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-004")
        siblings = supply_repo.offers_for_vehicle(conn, first.vehicle_id)
    assert first.vehicle_id == second.vehicle_id
    assert len(siblings) == 2


def test_the_network_claim_is_recorded_without_attributing_stock(computed):
    with computed.open() as conn:
        company = companies_repo.find_company_by_external(conn, "fixture_demo", "demo-platform-a")
        briefs = companies_repo.list_briefs(conn, company_id=company.id)
    assert "700-car partner network" in company.network_claim
    assert briefs == [], "a network claim is not a buying requirement"


def test_the_referral_partner_is_never_a_category_fit_buyer(computed):
    with computed.open() as conn:
        partner = companies_repo.find_company_by_external(conn, "fixture_demo", "demo-referral-a")
        matches = analysis_repo.list_matches(conn, company_id=partner.id)
    assert partner.is_referral_partner
    assert matches == [], "an introduction route is not presented as a fleet buyer"


def test_the_objecting_account_is_suppressed(computed):
    with computed.open() as conn:
        company = companies_repo.find_company_by_external(conn, "fixture_demo", "demo-dealer-f")
        assert companies_repo.is_suppressed(conn, company.id)


# -- replay --------------------------------------------------------------


def test_the_replay_shows_the_price_drop_with_both_values(seeded, demo_config, clock):
    demo_service.replay_updates(seeded, demo_config, clock)
    with seeded.open() as conn:
        events = [
            row for row in supply_repo.recent_change_events(conn) if row["kind"] == "price_change"
        ]
    assert len(events) == 1
    assert "200,000" in events[0]["old_value"]
    assert "193,000" in events[0]["new_value"]


def test_the_replay_preserves_the_original_observation(seeded, demo_config, clock):
    demo_service.replay_updates(seeded, demo_config, clock)
    offer = find_offer(seeded, "OFR-001")
    with seeded.open() as conn:
        history = supply_repo.observations_for_offer(conn, offer.id)
    prices = [o.price.decimal for o in history if o.price]
    assert 200000 in prices
    assert 193000 in prices
    assert offer.price.decimal == 193000


def test_replaying_twice_changes_nothing(seeded, demo_config, clock):
    demo_service.replay_updates(seeded, demo_config, clock)
    with seeded.open() as conn:
        before = (
            conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0],
        )
    demo_service.replay_updates(seeded, demo_config, clock)
    with seeded.open() as conn:
        after = (
            conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0],
        )
    assert before == after


def test_the_reserved_sibling_does_not_disturb_the_other_offer(seeded, demo_config, clock):
    demo_service.replay_updates(seeded, demo_config, clock)
    first = find_offer(seeded, "OFR-001")
    second = find_offer(seeded, "OFR-004")
    assert second.status == "reserved"
    assert first.status == "availability_confirmed"


def test_a_source_specification_correction_does_not_overwrite_the_reviewed_vehicle(
    seeded, demo_config, clock
):
    offer = find_offer(seeded, "OFR-002")
    with seeded.open() as conn:
        before = supply_repo.get_vehicle(conn, offer.vehicle_id).seats

    demo_service.replay_updates(seeded, demo_config, clock)

    with seeded.open() as conn:
        after = supply_repo.get_vehicle(conn, offer.vehicle_id).seats
        latest = supply_repo.latest_observation(conn, offer.id)
        events = [
            row
            for row in supply_repo.recent_change_events(conn)
            if row["kind"] == "specification_correction"
        ]
    assert before == 5
    assert after == 5, "the reviewed fact is unchanged by an import"
    assert latest.seats == 4, "the correction is preserved as an observation"
    assert events, "and it is raised as an alert for a human to act on"


# -- recompute and tasks -------------------------------------------------


def test_recompute_is_idempotent(seeded, demo_config, clock):
    workflow = Workflow(seeded, demo_config, clock)
    first = workflow.recompute()
    with seeded.open() as conn:
        matches_after_first = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        tasks_after_first = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    second = workflow.recompute()
    with seeded.open() as conn:
        matches_after_second = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        tasks_after_second = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    assert first.matches == second.matches
    assert matches_after_first == matches_after_second
    assert tasks_after_first == tasks_after_second
    assert second.tasks_created == 0, "a rerun invents no new work"


def test_comparable_observations_are_not_treated_as_sellable_supply(computed):
    with computed.open() as conn:
        rows = conn.execute(
            """SELECT COUNT(*) FROM matches m JOIN offers o ON o.id = m.offer_id
               WHERE o.price_evidence_type != 'supply_asking'"""
        ).fetchone()[0]
    assert rows == 0


def test_tasks_have_a_reason_and_a_due_date(computed):
    with computed.open() as conn:
        tasks = followup_repo.list_tasks(conn, state="open")
    assert tasks
    for task in tasks:
        assert task["notes"], f"{task['action']} has no stated reason"
        assert task["due_at"]


def test_the_dashboard_reports_zero_accurately(db, demo_config, clock):
    """An empty database shows zeroes, not invented activity."""
    with db.open() as conn:
        counts = dashboard_counts(conn, now=clock.now(), config=demo_config)
    assert counts["new_alerts"] == 0
    assert counts["specification_fits"] == 0
    assert counts["companies"] == 0
    assert all(isinstance(value, int) for value in counts.values())


def test_the_dashboard_separates_research_from_confirmed_demand(computed, demo_config, clock):
    with computed.open() as conn:
        counts = dashboard_counts(conn, now=clock.now(), config=demo_config)
    assert counts["confirmed_briefs"] > 0
    assert counts["category_prospects"] > 0
    assert "pipeline_revenue" not in counts
    assert "expected_commission" not in counts


# -- demo and live stay apart -------------------------------------------


def test_seeding_refuses_to_run_in_live_mode(live_config, clock):
    from desk.db import Database, ModeViolation

    handle = Database(live_config, clock)
    handle.initialise()
    with pytest.raises(ModeViolation, match="never write to live data"):
        demo_service.seed(handle, live_config, clock)


def test_a_demo_reset_leaves_the_live_database_alone(seeded, demo_config, live_config, clock):
    from desk.db import Database

    live = Database(live_config, clock)
    live.initialise()
    with live.write() as conn:
        conn.execute(
            """INSERT INTO sources (id, name, category, access_mode, status, is_demo,
               created_at, updated_at)
               VALUES ('real','Real source','mixed','file_import','approved',0,?,?)""",
            (clock.now_iso(), clock.now_iso()),
        )

    seeded.reset_demo()

    with live.open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    with seeded.open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0


def test_the_demo_time_anchor_is_stable_across_seeds(db, demo_config, clock):
    first = demo_service.time_anchor(db, clock)
    clock.advance(days=3)
    second = demo_service.time_anchor(db, clock)
    assert first == second, "the fixtures stay dated against one instant"


def test_an_import_crash_leaves_no_half_applied_batch(db, demo_config, clock, source, monkeypatch):
    """A failure mid-batch rolls the whole transaction back."""
    from desk.services.imports import Importer
    from tests.conftest import company_row

    importer = Importer(db, demo_config, clock)
    importer.apply("companies", [company_row("first")], source_id=source)

    original = companies_repo.upsert_company
    calls = {"n": 0}

    def explode(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash midway through the batch")
        return original(*args, **kwargs)

    monkeypatch.setattr(companies_repo, "upsert_company", explode)
    with pytest.raises(RuntimeError):
        importer.apply(
            "companies",
            [company_row("second"), company_row("third"), company_row("fourth")],
            source_id=source,
        )

    with db.open() as conn:
        names = {c.external_record_id for c in companies_repo.list_companies(conn)}
    assert names == {"first"}, "nothing from the failed batch was applied"


# -- schema upgrades -----------------------------------------------------


def test_a_later_migration_lands_on_an_existing_database_without_losing_data(
    demo_config, clock, tmp_path
):
    """Anyone already holding data must be able to take a schema change safely."""
    import shutil
    from dataclasses import replace

    from desk.db import MIGRATIONS_DIR, Database

    later = sorted(MIGRATIONS_DIR.glob("*.sql"))[-1]
    stash = tmp_path / later.name
    config = replace(demo_config, demo_db=tmp_path / "existing.sqlite")

    shutil.move(str(later), str(stash))
    try:
        db = Database(config, clock)
        applied_first = db.initialise()
        # Write through the ordinary import path rather than seeding the demo
        # snapshot: the snapshot now includes cost templates, whose table only
        # arrives with the migration this test is deliberately withholding.
        from desk.services.imports import Importer, upsert_sources
        from tests.conftest import company_row, offer_row

        upsert_sources(
            db,
            config,
            clock,
            [
                {
                    "id": "older_source",
                    "name": "Source under the older schema",
                    "category": "mixed",
                    "access_mode": "fixture",
                    "status": "approved",
                    "reviewer": "test",
                    "reviewed_at": clock.now().isoformat(),
                }
            ],
        )
        importer = Importer(db, config, clock)
        importer.apply("companies", [company_row("a")], source_id="older_source")
        importer.apply(
            "offers", [offer_row("OFR-OLD")], source_id="older_source"
        )
        with db.open() as conn:
            before = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    finally:
        shutil.move(str(stash), str(later))

    db = Database(config, clock)
    applied_second = db.initialise()

    with db.open() as conn:
        after = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert later.stem not in applied_first
    assert applied_second == [later.stem], "only the pending migration was applied"
    assert before > 0, "the older schema held data to begin with"
    assert after == before, "the upgrade lost data"
    assert {"cost_templates", "cost_template_lines"} <= tables
    assert list(tmp_path.glob("existing.backup-*.sqlite")), "no backup was taken"


# -- the seeded cost template -------------------------------------------


def test_the_demo_seeds_reviewed_cost_templates(seeded, clock):
    """Two routes, deliberately: one that completes and one that cannot.

    Every template has to name a reviewer and state its basis, whichever it is.
    """
    from desk.services import cost_templates

    with seeded.open() as conn:
        templates = cost_templates.list_templates(conn)
    assert len(templates) == 2
    routes = {template.route_label() for template in templates}
    assert routes == {"CZ to DE", "CZ to AT"}
    for template in templates:
        assert template.reviewer
        assert template.basis


def test_one_seeded_template_completes_and_one_cannot(seeded, clock):
    """The contrast the demonstration rests on.

    The Austrian route is fully priced. The German one leaves registration tax
    unknown on purpose, and an unknown line has to keep blocking completion -
    otherwise the whole claim about unknowns is decorative.
    """
    from desk.services import cost_templates

    with seeded.open() as conn:
        complete = cost_templates.best_for_route(
            conn, origin_country="CZ", destination_country="AT"
        )
        blocked = cost_templates.best_for_route(
            conn, origin_country="CZ", destination_country="DE"
        )
    assert all(line.is_known for line in complete.lines)
    assert any(not line.is_known for line in blocked.lines)


def test_the_seeded_template_leaves_registration_tax_unknown(seeded, clock):
    """A template must not turn an unknown into a number."""
    from desk.services import cost_templates

    with seeded.open() as conn:
        template = cost_templates.best_for_route(
            conn,
            origin_country="CZ",
            destination_country="DE",
            model_family="mercedes_g63",
        )
    assert template is not None
    assert template.unknown_lines == ["Registration tax"]
    assert "will arrive as unknown" in template.describe(clock.now())


def test_seeding_twice_does_not_duplicate_the_template(db, demo_config, clock):
    from desk.services import cost_templates

    demo_service.seed(db, demo_config, clock)
    demo_service.seed(db, demo_config, clock)
    with db.open() as conn:
        assert len(cost_templates.list_templates(conn)) == 2
