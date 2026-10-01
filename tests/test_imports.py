"""Imports: whole-batch validation, transactional apply, idempotent replay."""

from __future__ import annotations

from datetime import timedelta

import pytest

from desk.repositories import companies as companies_repo
from desk.repositories import supply as supply_repo
from desk.services.imports import export_csv, template_csv, validate_batch
from tests.conftest import FROZEN_NOW, brief_row, company_row, iso, offer_row


# -- validation ----------------------------------------------------------


def test_whole_batch_is_validated_before_anything_is_written():
    report = validate_batch(
        "companies",
        [company_row("a"), {"external_id": "b", "legal_name": "", "country": "DE"}, company_row("c")],
    )
    assert not report.ok
    assert len(report.valid) == 2
    assert report.problems[0].row_number == 2


def test_an_invalid_batch_writes_nothing_by_default(importer, source, db):
    report = importer.apply(
        "companies",
        [company_row("a"), {"external_id": "b", "country": "XX"}],
        source_id=source,
    )
    assert not report.applied
    assert "rejected in full" in " ".join(report.notes)
    with db.open() as conn:
        assert companies_repo.list_companies(conn) == []


def test_partial_apply_only_happens_when_explicitly_chosen(importer, source, db):
    report = importer.apply(
        "companies",
        [company_row("a"), {"external_id": "b", "country": "XX"}],
        source_id=source,
        allow_partial=True,
    )
    assert report.applied
    assert report.created == 1
    assert "at your explicit request" in " ".join(report.notes)
    with db.open() as conn:
        assert len(companies_repo.list_companies(conn)) == 1


@pytest.mark.parametrize(
    "bad,message",
    [
        ({"price": {"amount": "-5000", "currency": "EUR"}}, "negative"),
        ({"seats": 22}, "less than or equal to 9"),
        ({"mileage_km": -10}, "greater than or equal to 0"),
        ({"price": {"amount": "1000", "currency": "XYZ"}}, "unknown currency"),
    ],
)
def test_impossible_values_are_rejected(bad, message):
    report = validate_batch("offers", [offer_row(**bad)])
    assert not report.ok
    assert message in " ".join(p.message for p in report.problems)


def test_a_vin_is_never_derived_from_an_advert_id():
    report = validate_batch("offers", [offer_row(vin="39363519404128")])
    assert not report.ok
    assert "do not derive one from an advert id" in " ".join(p.message for p in report.problems)


def test_allocation_cannot_claim_confirmed_availability():
    report = validate_batch(
        "offers", [offer_row(stock_kind="allocation", status="availability_confirmed")]
    )
    assert not report.ok
    assert "allocation cannot be recorded as confirmed" in " ".join(
        p.message for p in report.problems
    )


def test_a_confirmed_brief_must_name_who_confirmed_it():
    report = validate_batch(
        "buyer_briefs", [brief_row(confirmed_at=iso(FROZEN_NOW), confirmed_by=None)]
    )
    assert not report.ok
    assert "who confirmed it" in " ".join(p.message for p in report.problems)


def test_a_permitted_policy_needs_channel_purpose_basis_and_reviewer():
    report = validate_batch(
        "contact_policies",
        [
            {
                "company_external_id": "a",
                "channel": "email",
                "purpose": "marketing",
                "status": "permitted_for_scope",
            }
        ],
    )
    assert not report.ok
    joined = " ".join(p.message for p in report.problems)
    assert "basis" in joined and "reviewer" in joined


def test_raw_price_text_is_preserved_beside_the_parsed_value(importer, source, db):
    importer.apply("companies", [company_row("a")], source_id=source)
    importer.apply(
        "offers",
        [offer_row(price={"raw_text": "€119.000", "locale": "eu"}, seller_external_id="a")],
        source_id=source,
    )
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
    assert offer.raw_price_text == "€119.000"
    assert offer.price.decimal == 119000


def test_an_ambiguous_price_without_a_locale_is_rejected():
    report = validate_batch("offers", [offer_row(price={"raw_text": "119.000"})])
    assert not report.ok
    assert "ambiguous separator" in " ".join(p.message for p in report.problems)


def test_a_future_observation_timestamp_is_rejected(importer, source):
    """A source cannot have observed something that has not happened yet."""
    future = iso(FROZEN_NOW + timedelta(days=2))
    report = importer.apply("offers", [offer_row(observed_at=future)], source_id=source)
    assert not report.applied
    assert "future" in " ".join(p.message for p in report.problems).lower()


# -- idempotency ---------------------------------------------------------


def test_importing_the_same_batch_twice_creates_no_duplicates(importer, source, db):
    rows = [company_row("a"), company_row("b")]
    first = importer.apply("companies", rows, source_id=source)
    second = importer.apply("companies", rows, source_id=source)
    assert first.created == 2
    assert second.created == 0
    assert second.updated == 2
    with db.open() as conn:
        assert len(companies_repo.list_companies(conn)) == 2


def test_replaying_an_identical_offer_snapshot_appends_no_observation(importer, source, db):
    importer.apply("companies", [company_row("a")], source_id=source)
    rows = [offer_row(seller_external_id="a")]
    first = importer.apply("offers", rows, source_id=source)
    second = importer.apply("offers", rows, source_id=source)
    assert first.observations == 1
    assert second.observations == 0
    assert second.skipped == 1
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
        assert len(supply_repo.observations_for_offer(conn, offer.id)) == 1


def test_replaying_an_identical_snapshot_raises_no_second_alert(importer, source, db, clock):
    importer.apply("companies", [company_row("a")], source_id=source)
    importer.apply("offers", [offer_row()], source_id=source)

    # An hour later the seller has dropped the price on the same offer.
    later = clock.advance(hours=1)
    changed = offer_row(price={"amount": "193000", "currency": "EUR"}, observed_at=iso(later))
    first = importer.apply("offers", [changed], source_id=source)
    second = importer.apply("offers", [changed], source_id=source)
    assert first.events == 1
    assert second.events == 0
    with db.open() as conn:
        assert len(supply_repo.recent_change_events(conn)) == 1


def test_recording_the_same_interaction_twice_does_not_double_count(importer, source, db):
    importer.apply("companies", [company_row("a")], source_id=source)
    rows = [
        {
            "external_id": "INT-1",
            "company_external_id": "a",
            "channel": "phone",
            "occurred_at": iso(FROZEN_NOW),
            "outcome": "no_answer",
        }
    ]
    first = importer.apply("interactions", rows, source_id=source)
    second = importer.apply("interactions", rows, source_id=source)
    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1


# -- relationships -------------------------------------------------------


def test_a_brief_is_never_attached_to_an_unknown_company(importer, source):
    report = importer.apply("buyer_briefs", [brief_row(company="does-not-exist")], source_id=source)
    assert report.created == 0
    assert "never attached to an invented company" in " ".join(
        p.message for p in report.problems
    )


def test_a_branch_link_is_left_unset_rather_than_guessed(importer, source, db):
    report = importer.apply(
        "companies",
        [company_row("child", parent_external_id="missing-parent", is_branch=True)],
        source_id=source,
    )
    assert report.applied
    assert "left unset rather than guessed" in " ".join(report.notes)
    with db.open() as conn:
        child = companies_repo.find_company_by_external(conn, source, "child")
    assert child.parent_company_id is None


def test_a_discovered_contact_never_becomes_a_permission(importer, source, db):
    importer.apply(
        "companies",
        [company_row("a", contact_name="Test Person", contact_email="x@a.example")],
        source_id=source,
    )
    with db.open() as conn:
        company = companies_repo.find_company_by_external(conn, source, "a")
        policies = companies_repo.list_policies(conn, company.id)
    assert policies
    assert all(p.status == "review_required" for p in policies)


def test_an_objection_suppresses_the_account(importer, source, db):
    importer.apply("companies", [company_row("a")], source_id=source)
    importer.apply(
        "interactions",
        [
            {
                "external_id": "INT-1",
                "company_external_id": "a",
                "channel": "phone",
                "occurred_at": iso(FROZEN_NOW),
                "outcome": "objection",
                "notes": "asked not to be contacted",
            }
        ],
        source_id=source,
    )
    with db.open() as conn:
        company = companies_repo.find_company_by_external(conn, source, "a")
        assert companies_repo.is_suppressed(conn, company.id)


def test_a_network_claim_is_stored_as_a_claim(importer, source, db):
    importer.apply(
        "companies",
        [company_row("platform", network_claim="advertises a 700-car partner network")],
        source_id=source,
    )
    with db.open() as conn:
        company = companies_repo.find_company_by_external(conn, source, "platform")
    assert company.network_claim == "advertises a 700-car partner network"
    # Nothing about the claim grants it stock or a confirmed buying route.
    assert company.buying_route == "buy_for_stock" or company.buying_route == "unknown"
    with db.open() as conn:
        assert companies_repo.list_briefs(conn, company_id=company.id) == []


# -- exports -------------------------------------------------------------


def test_export_neutralises_formula_like_cells():
    body = export_csv([{"company": "=cmd|' /c calc'!A1", "country": "DE"}], ["company", "country"])
    assert "'=cmd" in body
    assert not body.splitlines()[1].startswith("=")


def test_templates_exist_for_every_import_kind():
    for kind in ("companies", "offers", "buyer_briefs", "comparables", "interactions"):
        assert template_csv(kind).strip()
