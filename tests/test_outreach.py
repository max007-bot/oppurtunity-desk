"""Call briefs, contact-readiness gates, draft invalidation and scoped exports."""

from __future__ import annotations

from datetime import timedelta

import pytest

from desk.config import DraftStatus, FitStatus
from desk.repositories import companies as companies_repo
from desk.repositories import followup as followup_repo
from desk.repositories import supply as supply_repo
from desk.services import outreach
from desk.services.matching import MatchResult, RuleOutcome, RuleResult
from desk.services.source_policy import SourcePolicy
from tests.conftest import FROZEN_NOW, brief_row, company_row, iso, offer_row

PURPOSE = "business enquiry about G-Class stock the buyer asked us to call about"


@pytest.fixture
def prepared(importer, source, db, demo_config, clock):
    """One company, contact, offer, brief and a permitting phone policy."""
    importer.apply(
        "companies",
        [
            company_row(
                "buyer",
                contact_name="Test Buyer",
                contact_role="Purchasing Manager",
                contact_phone="+999 100 0002",
                contact_email="buying@buyer.example",
                contact_type="published_business",
                buying_authority="purchasing manager",
            )
        ],
        source_id=source,
    )
    importer.apply("offers", [offer_row(seller_external_id="buyer")], source_id=source)
    importer.apply("buyer_briefs", [brief_row(company="buyer")], source_id=source)
    importer.apply(
        "contact_policies",
        [
            {
                "company_external_id": "buyer",
                "channel": "phone",
                "purpose": PURPOSE,
                "status": "permitted_for_scope",
                "market": "DE",
                "basis": "The buyer asked on the recorded call to be phoned about G 63 stock.",
                "reviewer": "Max Watkinson",
                "reviewed_at": iso(FROZEN_NOW),
                "expires_at": iso(FROZEN_NOW + timedelta(days=60)),
            }
        ],
        source_id=source,
    )
    with db.open() as conn:
        company = companies_repo.find_company_by_external(conn, source, "buyer")
        contact = companies_repo.list_contacts(conn, company.id)[0]
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
        brief = companies_repo.list_briefs(conn, company_id=company.id)[0]
    return {
        "company": company,
        "contact": contact,
        "offer": offer,
        "vehicle": vehicle,
        "brief": brief,
        "policy": SourcePolicy(db, demo_config, clock),
    }


def clean_match(prepared) -> MatchResult:
    return MatchResult(
        offer_id=prepared["offer"].id,
        company_id=prepared["company"].id,
        brief_id=prepared["brief"].id,
        fit_status=FitStatus.SPECIFICATION_FIT,
        rules=[RuleResult("model family", RuleOutcome.PASS, "both are mercedes_g63")],
        score=90,
        score_breakdown={},
    )


def gates(db, prepared, demo_config, clock, *, channel="phone", match=None, **overrides):
    payload = {
        "company": prepared["company"],
        "contact": prepared["contact"],
        "channel": channel,
        "offer": prepared["offer"],
        "brief": prepared["brief"],
        "match": match,
        "policy_service": prepared["policy"],
        "now": clock.now(),
        "config": demo_config,
    }
    payload.update(overrides)
    with db.open() as conn:
        return outreach.evaluate_gates(conn, **payload)


# -- the permitting path -------------------------------------------------


def test_a_reviewed_record_on_a_permitted_channel_is_contact_ready(db, prepared, demo_config, clock):
    result = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    assert result.status is DraftStatus.CONTACT_READY
    assert any("permitted for" in reason for reason in result.reasons)


def test_a_call_brief_is_built_from_verified_facts(db, prepared, demo_config, clock):
    gate = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    brief = outreach.build_call_brief(
        company=prepared["company"],
        contact=prepared["contact"],
        offer=prepared["offer"],
        vehicle=prepared["vehicle"],
        brief=prepared["brief"],
        match=clean_match(prepared),
        now=clock.now(),
        config=demo_config,
        authorised_company="the dealership I am working with",
        gate=gate,
    )
    body = brief.render()
    assert "Test Buyer" in body
    assert "Purchasing Manager" in body
    assert len(brief.questions) == 3
    assert "12-minute" in brief.next_step
    assert "availability confirmed" in brief.supply_status
    # No invented contact details or claims.
    assert "@example.com" not in body


def test_an_unconfirmed_requirement_is_listed_as_a_missing_fact(db, prepared, demo_config, clock):
    prepared["brief"].confirmed_at = None
    prepared["brief"].confirmed_by = None
    gate = gates(db, prepared, demo_config, clock)
    brief = outreach.build_call_brief(
        company=prepared["company"],
        contact=prepared["contact"],
        offer=prepared["offer"],
        vehicle=prepared["vehicle"],
        brief=prepared["brief"],
        match=None,
        now=clock.now(),
        config=demo_config,
        authorised_company="the dealership I am working with",
        gate=gate,
    )
    assert any("confirmation of the buying requirement" in fact for fact in brief.missing_facts)


def test_an_internal_brief_is_still_produced_when_a_gate_fails(db, prepared, demo_config, clock):
    """A blocked channel never blocks research; it blocks contact."""
    gate = gates(db, prepared, demo_config, clock, channel="email", match=clean_match(prepared))
    assert gate.status is DraftStatus.INTERNAL_RESEARCH
    brief = outreach.build_call_brief(
        company=prepared["company"],
        contact=prepared["contact"],
        offer=prepared["offer"],
        vehicle=prepared["vehicle"],
        brief=prepared["brief"],
        match=clean_match(prepared),
        now=clock.now(),
        config=demo_config,
        authorised_company="the dealership I am working with",
        gate=gate,
    )
    assert brief.render()
    assert brief.status is DraftStatus.INTERNAL_RESEARCH


# -- the gates -----------------------------------------------------------


def test_an_unreviewed_channel_yields_internal_research_only(db, prepared, demo_config, clock):
    result = gates(db, prepared, demo_config, clock, channel="email", match=clean_match(prepared))
    assert result.status is DraftStatus.INTERNAL_RESEARCH
    assert any("not in the actionable outreach list" in reason for reason in result.blocking)


def test_a_discovered_public_email_does_not_become_permitted(db, prepared, demo_config, clock):
    with db.open() as conn:
        policy = companies_repo.policy_for(conn, prepared["company"].id, "email")
    assert policy.status == "review_required"


def test_a_suppressed_account_is_blocked_outright(db, prepared, demo_config, clock):
    with db.write() as conn:
        companies_repo.add_suppression(
            conn,
            company_id=prepared["company"].id,
            reason="objection recorded: asked not to be contacted",
            now=clock.now_iso(),
            is_demo=True,
        )
    result = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    assert result.status is DraftStatus.BLOCKED
    assert any("overrides future outreach actions regardless" in item for item in result.blocking)


def test_a_denied_channel_is_blocked(db, prepared, source, importer, demo_config, clock):
    importer.apply(
        "contact_policies",
        [
            {
                "company_external_id": "buyer",
                "channel": "phone",
                "purpose": PURPOSE,
                "status": "denied",
            }
        ],
        source_id=source,
    )
    result = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    assert any("denied" in item for item in result.blocking)


def test_an_expired_permission_is_blocked(db, prepared, demo_config, clock):
    clock.advance(days=90)
    result = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    assert any("expired" in item for item in result.blocking)


def test_an_expired_offer_blocks_a_contact_ready_draft(db, prepared, demo_config, clock):
    prepared["offer"].valid_until = clock.now() - timedelta(hours=1)
    result = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    assert any("needs reconfirmation" in item for item in result.blocking)


def test_stale_availability_blocks_a_contact_ready_draft(db, prepared, demo_config, clock):
    prepared["offer"].availability_confirmed_at = clock.now() - timedelta(hours=48)
    result = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    assert any("availability is stale" in item for item in result.blocking)


def test_a_research_match_can_never_enter_the_contact_ready_queue(db, prepared, demo_config, clock):
    unresolved = MatchResult(
        offer_id=prepared["offer"].id,
        company_id=prepared["company"].id,
        brief_id=prepared["brief"].id,
        fit_status=FitStatus.NEEDS_VERIFICATION,
        rules=[
            RuleResult("required option rear_entertainment", RuleOutcome.UNKNOWN, "not recorded")
        ],
        score=60,
        score_breakdown={},
    )
    result = gates(db, prepared, demo_config, clock, match=unresolved)
    assert result.status is DraftStatus.INTERNAL_RESEARCH
    assert any("research matches never enter" in item for item in result.blocking)


def test_a_source_that_forbids_export_blocks_the_draft(db, prepared, demo_config, clock, source):
    with db.write() as conn:
        conn.execute("UPDATE sources SET export_allowed = 0 WHERE id = ?", (source,))
    result = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    assert any("source restriction" in item for item in result.blocking)


def test_a_missing_contact_route_blocks_the_channel(db, prepared, demo_config, clock):
    prepared["contact"].business_phone = None
    result = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    assert any("no published business phone" in item for item in result.blocking)


# -- draft invalidation --------------------------------------------------


def store(db, prepared, gate, clock, demo_config):
    with db.write() as conn:
        return outreach.store_draft(
            conn,
            brief_text="Draft body",
            company=prepared["company"],
            contact=prepared["contact"],
            offer=prepared["offer"],
            buyer_brief=prepared["brief"],
            match_id=None,
            channel="phone",
            gate=gate,
            facts_used=["offer:OFR-T1"],
            unresolved=[],
            now=clock.now(),
            is_demo=True,
            expires_at=prepared["offer"].valid_until,
        )


def revalidate(db, draft_id, prepared, demo_config, clock):
    with db.open() as conn:
        row = followup_repo.get_draft(conn, draft_id)
        return outreach.revalidate_draft(
            conn,
            row,
            policy_service=prepared["policy"],
            now=clock.now(),
            config=demo_config,
            clock=clock,
        )


def test_a_stored_draft_is_usable_while_nothing_has_changed(db, prepared, demo_config, clock):
    gate = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    draft_id = store(db, prepared, gate, clock, demo_config)
    assert revalidate(db, draft_id, prepared, demo_config, clock).usable


def test_an_objection_after_generation_blocks_the_draft_immediately(
    db, prepared, demo_config, clock
):
    gate = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    draft_id = store(db, prepared, gate, clock, demo_config)
    assert revalidate(db, draft_id, prepared, demo_config, clock).usable

    with db.write() as conn:
        companies_repo.add_suppression(
            conn,
            company_id=prepared["company"].id,
            reason="objection recorded after the draft was written",
            now=clock.now_iso(),
            is_demo=True,
        )
    result = revalidate(db, draft_id, prepared, demo_config, clock)
    assert not result.usable
    assert result.status is DraftStatus.BLOCKED


def test_an_offer_expiring_after_generation_blocks_the_draft(db, prepared, demo_config, clock):
    gate = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    draft_id = store(db, prepared, gate, clock, demo_config)
    clock.advance(days=20)  # past the offer's valid_until
    result = revalidate(db, draft_id, prepared, demo_config, clock)
    assert not result.usable


def test_a_changed_price_makes_the_stored_draft_stale(db, prepared, demo_config, clock, source):
    gate = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    draft_id = store(db, prepared, gate, clock, demo_config)
    with db.write() as conn:
        conn.execute(
            "UPDATE offers SET price_minor = ? WHERE id = ?",
            (19_300_000, prepared["offer"].id),
        )
    result = revalidate(db, draft_id, prepared, demo_config, clock)
    assert not result.usable
    assert any("supporting facts have changed" in reason for reason in result.reasons)


def test_the_facts_fingerprint_changes_with_the_facts(prepared):
    before = outreach.facts_fingerprint(
        offer=prepared["offer"],
        brief=prepared["brief"],
        company=prepared["company"],
        contact=prepared["contact"],
    )
    prepared["offer"].status = "reserved"
    after = outreach.facts_fingerprint(
        offer=prepared["offer"],
        brief=prepared["brief"],
        company=prepared["company"],
        contact=prepared["contact"],
    )
    assert before != after


# -- exports -------------------------------------------------------------


def test_a_suppressed_account_cannot_enter_an_outreach_export(db, prepared, demo_config, clock):
    with db.write() as conn:
        companies_repo.add_suppression(
            conn,
            company_id=prepared["company"].id,
            reason="objection recorded",
            now=clock.now_iso(),
            is_demo=True,
        )
    with db.open() as conn:
        result = outreach.build_export(
            conn,
            kind="contact_ready",
            company_ids=[prepared["company"].id],
            policy_service=prepared["policy"],
            now=clock.now(),
            config=demo_config,
        )
    assert result.rows == []
    assert any("suppressed" in reason for reason in result.excluded)


def test_a_contact_export_needs_a_permitting_policy(db, prepared, demo_config, clock):
    with db.open() as conn:
        allowed = outreach.build_export(
            conn,
            kind="contact_ready",
            company_ids=[prepared["company"].id],
            policy_service=prepared["policy"],
            now=clock.now(),
            config=demo_config,
            channel="phone",
        )
        refused = outreach.build_export(
            conn,
            kind="contact_ready",
            company_ids=[prepared["company"].id],
            policy_service=prepared["policy"],
            now=clock.now(),
            config=demo_config,
            channel="email",
        )
    assert len(allowed.rows) == 1
    assert refused.rows == []
    assert any("not permitted for this scope" in reason for reason in refused.excluded)


def test_a_research_export_carries_no_contact_fields(db, prepared, demo_config, clock):
    with db.open() as conn:
        result = outreach.build_export(
            conn,
            kind="research",
            company_ids=[prepared["company"].id],
            policy_service=prepared["policy"],
            now=clock.now(),
            config=demo_config,
        )
    assert len(result.rows) == 1
    row = result.rows[0]
    assert "business_phone" not in row
    assert "business_email" not in row
    assert row["contact_policy"] in {"permitted_for_scope", "review_required", "unknown"}


def test_an_export_forbidden_by_the_source_licence_is_refused(
    db, prepared, demo_config, clock, source
):
    with db.write() as conn:
        conn.execute("UPDATE sources SET export_allowed = 0 WHERE id = ?", (source,))
    with db.open() as conn:
        result = outreach.build_export(
            conn,
            kind="contact_ready",
            company_ids=[prepared["company"].id],
            policy_service=prepared["policy"],
            now=clock.now(),
            config=demo_config,
        )
    assert result.rows == []
    assert any("does not permit exporting" in reason for reason in result.excluded)


# -- the brief has to be readable by a person ---------------------------


def test_the_brief_contains_no_internal_slug_or_raw_dict(db, prepared, demo_config, clock):
    """A brief is read aloud on a call, so it must not leak internals."""
    gate = gates(db, prepared, demo_config, clock, match=clean_match(prepared))
    body = outreach.build_call_brief(
        company=prepared["company"],
        contact=prepared["contact"],
        offer=prepared["offer"],
        vehicle=prepared["vehicle"],
        brief=prepared["brief"],
        match=clean_match(prepared),
        now=clock.now(),
        config=demo_config,
        authorised_company="the dealership I am working with",
        gate=gate,
    ).render()

    assert "mercedes_g63" not in body, "an internal family key reached the brief"
    assert "{'" not in body and '{"' not in body, "a raw dict reached the brief"
    assert "stock_requirement" not in body
    assert "max_mileage_km" not in body
    assert "Mercedes-AMG G 63" in body
    assert "physical stock, not an allocation" in body
    assert "+00:00" not in body, "an ISO timestamp reached the brief"


def test_requirements_are_described_in_words():
    from desk.services.outreach import describe_requirements

    text = describe_requirements(
        {
            "steering": "lhd",
            "seats": 5,
            "max_mileage_km": 25000,
            "must_have_options": ["rear_entertainment", "night_package"],
            "stock_requirement": "physical_stock",
        }
    )
    assert "LHD steering" in text
    assert "5 homologated seats" in text
    assert "no more than 25,000 km" in text
    assert "rear entertainment, night package options" in text
    assert "physical stock, not an allocation" in text


# -- the cross-border tax basis on the brief -----------------------------


def call_brief(prepared, demo_config, clock, **overrides):
    payload = dict(
        company=prepared["company"],
        contact=prepared["contact"],
        offer=prepared["offer"],
        vehicle=prepared["vehicle"],
        brief=prepared["brief"],
        match=None,
        now=clock.now(),
        config=demo_config,
        authorised_company="the dealership I am working with",
    )
    payload.update(overrides)
    return outreach.build_call_brief(**payload)


def test_the_brief_states_the_new_means_of_transport_basis(prepared, demo_config, clock):
    """It decides how the deal is written, so it is on the brief, not buried.

    Almost every car this kind of business moves is new or nearly new, which puts
    the EU new-means-of-transport test at the centre of the transaction rather
    than at the edge of it.
    """
    brief = call_brief(prepared, demo_config, clock)
    assert "new-means-of-transport" in brief.tax_status
    assert "Cross-border basis:" in brief.render()


def test_an_undeterminable_tax_basis_becomes_a_missing_fact(prepared, demo_config, clock):
    """Unknown blocks a claim. It never becomes a convenient assumption."""
    prepared["vehicle"].first_registration = None
    prepared["vehicle"].mileage_km = None
    brief = call_brief(prepared, demo_config, clock)
    assert "cannot be determined" in brief.tax_status
    assert any("new-means-of-transport" in fact for fact in brief.missing_facts)


def test_a_brief_with_no_vehicle_says_so_rather_than_guessing(prepared, demo_config, clock):
    brief = call_brief(prepared, demo_config, clock, vehicle=None, offer=None)
    assert "no vehicle attached" in brief.tax_status
