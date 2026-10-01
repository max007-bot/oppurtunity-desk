"""Analyst adjustments on a comparable, and the recorded source review."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from desk.config import SourceStatus
from desk.money import Money
from desk.repositories import supply as supply_repo
from desk.services import adjustments, source_review
from desk.services.source_policy import SourcePolicy
from desk.services.workflow import Workflow
from tests.conftest import FROZEN_NOW, comparable_row, offer_row


def eur(amount) -> Money:
    return Money.from_decimal(Decimal(str(amount)), "EUR")


# ======================================================================
# Analyst adjustments
# ======================================================================


@pytest.fixture
def analysis(importer, source, db, demo_config, clock):
    """A target offer with five clean comparables at known prices."""
    importer.apply("offers", [offer_row("OFR-TARGET")], source_id=source)
    importer.apply(
        "comparables",
        [
            comparable_row("C1", "228000", mileage_km=13000),
            comparable_row("C2", "232000", mileage_km=9200),
            comparable_row("C3", "235000", mileage_km=17300),
            comparable_row("C4", "239000", mileage_km=6800),
            comparable_row("C5", "243000", mileage_km=21500),
        ],
        source_id=source,
    )
    with db.open() as conn:
        target = supply_repo.find_offer_by_external(conn, source, "OFR-TARGET")
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        result = workflow.comparables_for(conn, target, persist=False)
    return target, workflow, result


def offer_id_of(result, amount: str) -> str:
    return next(
        d.candidate.offer_id for d in result.included if d.observed_price == eur(amount)
    )


def test_nothing_is_adjusted_automatically(analysis):
    _target, _workflow, result = analysis
    assert result.median == eur(235000)
    assert result.adjusted == []
    assert result.median_before_adjustments == eur(235000)
    assert any("no automatic monetary adjustment" in note for note in result.notes)


def test_an_adjustment_moves_the_median_and_shows_the_effect(analysis, db, clock):
    target, workflow, result = analysis
    with db.write() as conn:
        adjustments.record(
            conn,
            target_offer_id=target.id,
            comparable_offer_id=offer_id_of(result, "235000"),
            amount=eur("-4000"),
            reason="it has the carbon package, which our car lacks",
            created_by="Max",
            now=clock.now(),
            is_demo=True,
        )

    with db.open() as conn:
        after = workflow.comparables_for(conn, target, persist=False)

    assert after.median_before_adjustments == eur(235000)
    assert after.median == eur(232000)
    assert after.adjustment_effect == eur(-3000)
    assert len(after.adjusted) == 1


def test_the_unadjusted_price_is_kept_beside_the_figure_used(analysis, db, clock):
    target, workflow, result = analysis
    with db.write() as conn:
        adjustments.record(
            conn,
            target_offer_id=target.id,
            comparable_offer_id=offer_id_of(result, "235000"),
            amount=eur("-4000"),
            reason="carbon package",
            created_by="Max",
            now=clock.now(),
            is_demo=True,
        )
    with db.open() as conn:
        after = workflow.comparables_for(conn, target, persist=False)

    adjusted = after.adjusted[0]
    assert adjusted.observed_price == eur(235000)
    assert adjusted.price_used == eur(231000)
    assert "analyst assumption by Max" in adjusted.provenance[0]
    assert "carbon package" in adjusted.provenance[0]


def test_an_adjustment_is_reported_as_an_assumption_not_evidence(analysis, db, clock):
    target, workflow, result = analysis
    with db.write() as conn:
        adjustments.record(
            conn,
            target_offer_id=target.id,
            comparable_offer_id=offer_id_of(result, "228000"),
            amount=eur("3000"),
            reason="ours has the night package",
            created_by="Max",
            now=clock.now(),
            is_demo=True,
        )
    with db.open() as conn:
        after = workflow.comparables_for(conn, target, persist=False)

    notes = " ".join(after.notes)
    assert "assumptions entered by a person, not automatic corrections" in notes
    assert "median before them is shown alongside" in notes


def test_an_adjustment_needs_a_reason(analysis, db, clock):
    target, _workflow, result = analysis
    with db.write() as conn:
        with pytest.raises(adjustments.AdjustmentError, match="state why"):
            adjustments.record(
                conn,
                target_offer_id=target.id,
                comparable_offer_id=offer_id_of(result, "235000"),
                amount=eur("-4000"),
                reason="  ",
                created_by="Max",
                now=clock.now(),
                is_demo=True,
            )


def test_an_adjustment_needs_an_author(analysis, db, clock):
    target, _workflow, result = analysis
    with db.write() as conn:
        with pytest.raises(adjustments.AdjustmentError, match="who entered"):
            adjustments.record(
                conn,
                target_offer_id=target.id,
                comparable_offer_id=offer_id_of(result, "235000"),
                amount=eur("-4000"),
                reason="carbon package",
                created_by="",
                now=clock.now(),
                is_demo=True,
            )


def test_an_implausibly_large_adjustment_is_refused(analysis, db, clock):
    """If a quarter of the price is the difference, they are not comparable."""
    target, _workflow, result = analysis
    with db.write() as conn:
        with pytest.raises(adjustments.AdjustmentError, match="not comparable in the first place"):
            adjustments.record(
                conn,
                target_offer_id=target.id,
                comparable_offer_id=offer_id_of(result, "235000"),
                amount=eur("-90000"),
                reason="it is a much better car",
                created_by="Max",
                now=clock.now(),
                is_demo=True,
                comparable_price=eur("235000"),
            )


def test_a_zero_adjustment_is_refused(analysis, db, clock):
    target, _workflow, result = analysis
    with db.write() as conn:
        with pytest.raises(adjustments.AdjustmentError, match="remove it instead"):
            adjustments.record(
                conn,
                target_offer_id=target.id,
                comparable_offer_id=offer_id_of(result, "235000"),
                amount=eur("0"),
                reason="no change",
                created_by="Max",
                now=clock.now(),
                is_demo=True,
            )


def test_retiring_an_adjustment_keeps_the_record_but_stops_applying_it(analysis, db, clock):
    target, workflow, result = analysis
    comparable = offer_id_of(result, "235000")
    with db.write() as conn:
        adjustments.record(
            conn,
            target_offer_id=target.id,
            comparable_offer_id=comparable,
            amount=eur("-4000"),
            reason="carbon package",
            created_by="Max",
            now=clock.now(),
            is_demo=True,
        )
    with db.write() as conn:
        removed = adjustments.retire(
            conn, target_offer_id=target.id, comparable_offer_id=comparable, now=clock.now()
        )

    with db.open() as conn:
        after = workflow.comparables_for(conn, target, persist=False)
        live = adjustments.list_for_target(conn, target.id)
        archived = adjustments.list_for_target(conn, target.id, include_retired=True)

    assert removed == 1
    assert after.median == eur(235000), "the adjustment no longer applies"
    assert live == []
    assert len(archived) == 1, "but the audit trail survives"


def test_recording_twice_replaces_rather_than_stacking(analysis, db, clock):
    target, workflow, result = analysis
    comparable = offer_id_of(result, "235000")
    for amount in ("-4000", "-2000"):
        with db.write() as conn:
            adjustments.record(
                conn,
                target_offer_id=target.id,
                comparable_offer_id=comparable,
                amount=eur(amount),
                reason="carbon package, revised",
                created_by="Max",
                now=clock.now(),
                is_demo=True,
            )
    with db.open() as conn:
        after = workflow.comparables_for(conn, target, persist=False)
        live = adjustments.list_for_target(conn, target.id)

    assert len(live) == 1
    assert after.adjusted[0].price_used == eur(233000)


def test_an_adjustment_in_the_wrong_currency_is_not_applied(analysis, db, clock):
    target, workflow, result = analysis
    with db.write() as conn:
        adjustments.record(
            conn,
            target_offer_id=target.id,
            comparable_offer_id=offer_id_of(result, "235000"),
            amount=Money.from_decimal("-4000", "GBP"),
            reason="carbon package",
            created_by="Max",
            now=clock.now(),
            is_demo=True,
        )
    with db.open() as conn:
        after = workflow.comparables_for(conn, target, persist=False)

    assert after.median == eur(235000)
    assert any("was not applied" in note for note in after.notes)


def test_the_stored_set_records_the_adjustment(analysis, db, clock):
    target, workflow, result = analysis
    with db.write() as conn:
        adjustments.record(
            conn,
            target_offer_id=target.id,
            comparable_offer_id=offer_id_of(result, "235000"),
            amount=eur("-4000"),
            reason="carbon package",
            created_by="Max",
            now=clock.now(),
            is_demo=True,
        )
    with db.write() as conn:
        workflow.comparables_for(conn, target, persist=True)

    with db.open() as conn:
        row = conn.execute(
            "SELECT * FROM comparable_members WHERE adjustment_minor IS NOT NULL"
        ).fetchone()
        stored = conn.execute(
            "SELECT * FROM comparable_sets ORDER BY analysed_at DESC LIMIT 1"
        ).fetchone()

    assert row["observed_price_minor"] == 23_500_000
    assert row["price_minor"] == 23_100_000
    assert row["adjustment_minor"] == -400_000
    assert row["adjustment_reason"] == "carbon package"
    assert stored["adjustments_applied"] == 1
    assert stored["median_before_adjustments_minor"] == 23_500_000


# ======================================================================
# Source review
# ======================================================================


def approved_request(source_id: str, **overrides) -> source_review.ReviewRequest:
    payload = dict(
        source_id=source_id,
        status=SourceStatus.APPROVED.value,
        reviewer="Max Watkinson",
        reviewed_at=FROZEN_NOW,
        approved_use="bounded discovery in one selected area",
        approval_evidence="read the published usage policy on 2026-09-28",
        evidence_kind="published_terms_reviewed",
        allowed_hosts=["overpass-api.de"],
        allowed_paths=["/api/interpreter"],
        review_due_at=FROZEN_NOW + timedelta(days=180),
    )
    payload.update(overrides)
    return source_review.ReviewRequest(**payload)


@pytest.fixture
def registered(db, demo_config, clock):
    from desk.services.imports import upsert_sources

    upsert_sources(
        db,
        demo_config,
        clock,
        [
            {
                "id": "osm_overpass",
                "name": "OpenStreetMap via Overpass",
                "category": "company_discovery",
                "access_mode": "public_api",
                "status": "review_required",
                "documentation_url": "https://wiki.openstreetmap.org/wiki/Overpass_API",
            },
            {
                "id": "licensed_thing",
                "name": "A licensed marketplace",
                "category": "vehicle_supply",
                "access_mode": "licensed_api",
                "status": "blocked",
            },
        ],
    )
    return SourcePolicy(db, demo_config, clock)


def review(db, registered, demo_config, clock, request):
    source = registered.get(request.source_id)
    with db.write() as conn:
        return source_review.apply_review(
            conn, request=request, source=source, config=demo_config, now=clock.now()
        )


def test_an_approval_records_reviewer_scope_and_expiry(db, registered, demo_config, clock):
    result = review(db, registered, demo_config, clock, approved_request("osm_overpass"))
    source = registered.get("osm_overpass")

    assert result.previous_status == "review_required"
    assert result.status == "approved"
    assert source.reviewer == "Max Watkinson"
    assert source.review_due_at is not None
    assert source.allowed_hosts == ["overpass-api.de"]
    assert source.approved_use


def test_an_approval_without_a_stated_use_is_refused(db, registered, demo_config, clock):
    with pytest.raises(source_review.ReviewRefused, match="approved use"):
        review(
            db,
            registered,
            demo_config,
            clock,
            approved_request("osm_overpass", approved_use=None),
        )


def test_an_approval_without_evidence_is_refused(db, registered, demo_config, clock):
    with pytest.raises(source_review.ReviewRefused, match="approval evidence"):
        review(
            db,
            registered,
            demo_config,
            clock,
            approved_request("osm_overpass", approval_evidence=None),
        )


def test_an_approval_must_say_what_kind_of_evidence_it_rests_on(
    db, registered, demo_config, clock
):
    with pytest.raises(source_review.ReviewRefused, match="what kind of evidence"):
        review(
            db,
            registered,
            demo_config,
            clock,
            approved_request("osm_overpass", evidence_kind="unknown"),
        )


def test_reading_published_terms_does_not_approve_a_licensed_source(
    db, registered, demo_config, clock
):
    """The distinction the manual insists on, enforced rather than described."""
    with pytest.raises(source_review.ReviewRefused, match="not the same as holding the account"):
        review(
            db,
            registered,
            demo_config,
            clock,
            approved_request(
                "licensed_thing",
                evidence_kind="published_terms_reviewed",
                allowed_hosts=["example.com"],
            ),
        )


def test_a_licensed_source_can_be_approved_on_a_contract(db, registered, demo_config, clock):
    result = review(
        db,
        registered,
        demo_config,
        clock,
        approved_request(
            "licensed_thing",
            evidence_kind="contract_or_account_scope",
            approval_evidence="account activated under agreement 12345",
            allowed_hosts=["services.example.com"],
        ),
    )
    assert result.status == "approved"


def test_a_public_api_approval_needs_a_host_scope(db, registered, demo_config, clock):
    with pytest.raises(source_review.ReviewRefused, match="list the hosts"):
        review(db, registered, demo_config, clock, approved_request("osm_overpass", allowed_hosts=[]))


def test_a_review_due_date_before_the_review_is_refused(db, registered, demo_config, clock):
    with pytest.raises(source_review.ReviewRefused, match="must be after"):
        review(
            db,
            registered,
            demo_config,
            clock,
            approved_request("osm_overpass", review_due_at=FROZEN_NOW - timedelta(days=1)),
        )


def test_an_approval_without_an_expiry_gets_one(db, registered, demo_config, clock):
    result = review(
        db, registered, demo_config, clock, approved_request("osm_overpass", review_due_at=None)
    )
    source = registered.get("osm_overpass")
    assert source.review_due_at == FROZEN_NOW + timedelta(
        days=source_review.DEFAULT_REVIEW_PERIOD_DAYS
    )
    assert any("lapses on its own" in warning for warning in result.warnings)


def test_a_review_in_demo_mode_says_it_is_a_rehearsal(db, registered, demo_config, clock):
    result = review(db, registered, demo_config, clock, approved_request("osm_overpass"))
    assert any("rehearsal" in warning for warning in result.warnings)


def test_export_without_a_retention_rule_warns(db, registered, demo_config, clock):
    result = review(
        db, registered, demo_config, clock, approved_request("osm_overpass", export_allowed=True)
    )
    assert any("no retention rule" in warning for warning in result.warnings)


def test_a_review_is_kept_as_history(db, registered, demo_config, clock):
    review(db, registered, demo_config, clock, approved_request("osm_overpass"))
    review(
        db,
        registered,
        demo_config,
        clock,
        approved_request("osm_overpass", status="blocked", approved_use=None,
                         approval_evidence=None, evidence_kind="unknown"),
    )
    with db.open() as conn:
        history = source_review.history(conn, "osm_overpass")
    assert len(history) == 2
    assert history[0]["status"] == "blocked"
    assert history[0]["previous_status"] == "approved"
    assert history[1]["status"] == "approved"


def test_a_review_writes_an_audit_entry(db, registered, demo_config, clock):
    review(db, registered, demo_config, clock, approved_request("osm_overpass"))
    with db.open() as conn:
        log = conn.execute(
            "SELECT * FROM change_log WHERE entity_type = 'source' AND action = 'reviewed'"
        ).fetchone()
    assert log["actor"] == "Max Watkinson"
    assert log["old_value"] == "review_required"
    assert log["new_value"] == "approved"


def test_approving_in_demo_mode_still_does_not_permit_a_fetch(
    db, registered, demo_config, clock
):
    """The rehearsal warning is not decoration: demo mode still cannot fetch."""
    review(db, registered, demo_config, clock, approved_request("osm_overpass"))
    assert not registered.may_fetch("osm_overpass").allowed


def test_sources_due_for_review_are_listed(db, registered, demo_config, clock):
    review(
        db,
        registered,
        demo_config,
        clock,
        approved_request("osm_overpass", review_due_at=FROZEN_NOW + timedelta(days=5)),
    )
    with db.open() as conn:
        due = source_review.due_for_review(conn, now=clock.now(), within_days=30)
    assert [row["id"] for row in due] == ["osm_overpass"]


def test_an_unknown_status_is_refused(db, registered, demo_config, clock):
    with pytest.raises(source_review.ReviewRefused, match="unknown status"):
        review(db, registered, demo_config, clock, approved_request("osm_overpass", status="fine"))


def test_a_review_must_name_its_reviewer(db, registered, demo_config, clock):
    with pytest.raises(source_review.ReviewRefused, match="name its reviewer"):
        review(db, registered, demo_config, clock, approved_request("osm_overpass", reviewer="  "))
