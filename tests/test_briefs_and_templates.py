"""Capturing a requirement on a call, and reusable reviewed cost templates."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from desk.config import ScenarioStatus
from desk.money import Money
from desk.repositories import companies as companies_repo
from desk.services import briefs, cost_templates
from desk.services.pricing import ScenarioInput, compute_scenario
from tests.conftest import FROZEN_NOW, company_row


def eur(amount) -> Money:
    return Money.from_decimal(Decimal(str(amount)), "EUR")


# ======================================================================
# Capturing a requirement
# ======================================================================


@pytest.fixture
def buyer(importer, source, db):
    importer.apply("companies", [company_row("buyer")], source_id=source)
    with db.open() as conn:
        return companies_repo.find_company_by_external(conn, source, "buyer")


def row(**overrides):
    payload = dict(
        company_external_id="buyer",
        model_family="mercedes_g63",
        conversation_date=FROZEN_NOW,
        required_specs={"steering": "lhd", "seats": 5, "stock_requirement": "physical_stock"},
        budget=eur("215000"),
        budget_basis="net",
        budget_vat_regime="standard",
        quantity=1,
        destination_country="de",
        route="buy_for_stock",
        required_by=FROZEN_NOW + timedelta(days=21),
        confirmed=True,
        confirmed_by="Tomas Havel, Purchasing Manager",
        evidence_note="recorded call",
        contact_name="Tomas Havel",
    )
    payload.update(overrides)
    return briefs.build_row(**payload)


def capture(db, source, config, clock, **overrides):
    with db.write() as conn:
        return briefs.capture(
            conn,
            row=row(**overrides),
            source_id=source,
            company_source_id=source,
            config=config,
            now=clock.now(),
        )


def test_a_captured_requirement_is_stored_and_matchable(db, source, demo_config, clock, buyer):
    result = capture(db, source, demo_config, clock)
    assert result.ok
    assert result.created

    with db.open() as conn:
        stored = companies_repo.list_briefs(conn, company_id=buyer.id)[0]

    assert stored.model_family == "mercedes_g63"
    assert stored.budget == eur("215000")
    assert stored.budget_basis == "net"
    assert stored.required_specs["seats"] == 5
    assert stored.destination_country == "DE", "the country code is normalised"
    assert stored.is_confirmed


def test_a_captured_requirement_expires_by_default(db, source, demo_config, clock, buyer):
    capture(db, source, demo_config, clock)
    with db.open() as conn:
        stored = companies_repo.list_briefs(conn, company_id=buyer.id)[0]
    assert stored.expires_at == FROZEN_NOW + timedelta(days=briefs.DEFAULT_VALIDITY_DAYS)
    assert not stored.is_expired(clock.now())


def test_capturing_the_same_call_twice_updates_rather_than_duplicates(
    db, source, demo_config, clock, buyer
):
    first = capture(db, source, demo_config, clock)
    second = capture(db, source, demo_config, clock)
    assert first.created
    assert not second.created
    with db.open() as conn:
        assert len(companies_repo.list_briefs(conn, company_id=buyer.id)) == 1


def test_a_confirmed_requirement_raises_an_alert(db, source, demo_config, clock, buyer):
    from desk.repositories import supply as supply_repo

    capture(db, source, demo_config, clock)
    with db.open() as conn:
        events = supply_repo.recent_change_events(conn)
    assert any(event["kind"] == "requirement_confirmed" for event in events)


def test_a_confirmed_requirement_must_name_who_confirmed_it(
    db, source, demo_config, clock, buyer
):
    result = capture(db, source, demo_config, clock, confirmed=True, confirmed_by=None)
    assert not result.ok
    assert "who confirmed it" in " ".join(p.message for p in result.problems)


def test_a_requirement_is_never_attached_to_an_unknown_company(
    db, source, demo_config, clock, buyer
):
    result = capture(db, source, demo_config, clock, company_external_id="nobody")
    assert not result.ok
    assert "never attached to an invented company" in " ".join(
        p.message for p in result.problems
    )


def test_blank_requirements_are_dropped_rather_than_stored_as_unknown(
    db, source, demo_config, clock, buyer
):
    """A field the salesperson did not ask about must not become a rule."""
    result = capture(
        db,
        source,
        demo_config,
        clock,
        required_specs={"steering": "unknown", "powertrain": "", "seats": 5},
    )
    assert result.ok
    with db.open() as conn:
        stored = companies_repo.list_briefs(conn, company_id=buyer.id)[0]
    assert stored.required_specs == {"seats": 5}


def test_a_budget_with_no_basis_is_flagged_as_uncomparable(
    db, source, demo_config, clock, buyer
):
    result = capture(db, source, demo_config, clock, budget_basis="unknown")
    assert result.ok
    assert any("cannot be compared with an asking price" in w for w in result.warnings)


def test_a_missing_budget_is_allowed_but_noted(db, source, demo_config, clock, buyer):
    result = capture(db, source, demo_config, clock, budget=None)
    assert result.ok
    assert any("No budget was captured" in w for w in result.warnings)


def test_no_hard_requirement_is_flagged_as_too_loose(db, source, demo_config, clock, buyer):
    result = capture(db, source, demo_config, clock, required_specs={})
    assert result.ok
    assert any("almost any car of this family will match" in w for w in result.warnings)


def test_an_unconfirmed_requirement_says_it_cannot_reach_contact_ready(
    db, source, demo_config, clock, buyer
):
    result = capture(db, source, demo_config, clock, confirmed=False, confirmed_by=None)
    assert result.ok
    assert any("will not reach a contact-ready state" in w for w in result.warnings)


def test_a_multi_unit_requirement_warns_about_the_remainder(
    db, source, demo_config, clock, buyer
):
    result = capture(db, source, demo_config, clock, quantity=3)
    assert result.ok
    assert any("remainder as unsourced" in w for w in result.warnings)


def test_a_captured_requirement_reaches_the_matching_rules(
    db, source, demo_config, clock, buyer, importer
):
    """The point of the screen: a typed requirement becomes a real match."""
    from desk.repositories import analysis as analysis_repo
    from desk.services.workflow import Workflow
    from tests.conftest import offer_row

    importer.apply("offers", [offer_row(seller_external_id="buyer")], source_id=source)
    capture(db, source, demo_config, clock)
    Workflow(db, demo_config, clock).recompute()

    with db.open() as conn:
        matches = [m for m in analysis_repo.list_matches(conn) if m["brief_id"]]

    assert matches
    assert matches[0]["fit_status"] == "specification_fit"


# ======================================================================
# Cost templates
# ======================================================================


def lines(**overrides) -> list[cost_templates.TemplateLine]:
    base = [
        cost_templates.TemplateLine("Transport", eur("1500"), evidence_note="transporter quote"),
        cost_templates.TemplateLine("Preparation", eur("1000")),
        cost_templates.TemplateLine("Documents and handling", eur("500")),
        cost_templates.TemplateLine("Funding cost", eur("2000")),
        cost_templates.TemplateLine("Risk allowance", eur("1500")),
    ]
    return overrides.get("lines", base)


def save(db, clock, **overrides):
    payload = dict(
        name="CZ to DE, G-Class",
        destination_country="de",
        basis="transporter quotes of 2026-09-20 and the published registration tax table",
        reviewer="Max Watkinson",
        reviewed_at=FROZEN_NOW,
        lines=lines(),
        now=clock.now(),
        is_demo=True,
        origin_country="cz",
        model_family="mercedes_g63",
    )
    payload.update(overrides)
    with db.write() as conn:
        return cost_templates.save(conn, **payload)


def test_a_template_stores_its_review_trail(db, clock):
    template_id = save(db, clock)
    with db.open() as conn:
        template = cost_templates.get(conn, template_id)

    assert template.reviewer == "Max Watkinson"
    assert template.basis.startswith("transporter quotes")
    assert template.review_due_at == FROZEN_NOW + timedelta(
        days=cost_templates.DEFAULT_REVIEW_PERIOD_DAYS
    )
    assert template.destination_country == "DE"
    assert template.origin_country == "CZ"
    assert len(template.lines) == 5


def test_a_template_needs_a_reviewer_and_a_basis(db, clock):
    for field in ("reviewer", "basis", "name"):
        with pytest.raises(cost_templates.TemplateError, match="has to be possible to see"):
            save(db, clock, **{field: "  "})


def test_a_template_with_no_lines_is_refused(db, clock):
    with pytest.raises(cost_templates.TemplateError, match="nothing to contribute"):
        save(db, clock, lines=[])


def test_duplicate_line_labels_are_refused(db, clock):
    with pytest.raises(cost_templates.TemplateError, match="duplicate cost line"):
        save(
            db,
            clock,
            lines=[
                cost_templates.TemplateLine("Transport", eur("1500")),
                cost_templates.TemplateLine("Transport", eur("900")),
            ],
        )


def test_a_line_in_the_wrong_currency_is_refused(db, clock):
    with pytest.raises(cost_templates.TemplateError, match="but the template is in"):
        save(
            db,
            clock,
            lines=[cost_templates.TemplateLine("Transport", Money.from_decimal("1500", "GBP"))],
        )


def test_a_template_turns_into_the_manuals_worked_example(db, clock):
    """The five lines a template supplies compute exactly as before."""
    template_id = save(db, clock)
    with db.open() as conn:
        template = cost_templates.get(conn, template_id)

    result = compute_scenario(
        ScenarioInput(
            acquisition=eur("200000"),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            retail_assumption=eur("230000"),
            dealer_downstream_cost=eur("2500"),
            dealer_required_contribution=eur("12500"),
            costs=template.to_cost_lines(),
        )
    )
    assert result.included_costs == eur("6500")
    assert result.total_outlay == eur("206500")
    assert result.contribution == eur("8500")


def test_a_template_line_with_no_amount_arrives_unknown_and_blocks_completion(db, clock):
    """A template must not turn an unknown into a number."""
    template_id = save(
        db,
        clock,
        lines=[
            cost_templates.TemplateLine("Transport", eur("1500")),
            cost_templates.TemplateLine("Registration tax", None),
        ],
    )
    with db.open() as conn:
        template = cost_templates.get(conn, template_id)

    assert template.unknown_lines == ["Registration tax"]
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur("200000"),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            retail_assumption=eur("230000"),
            dealer_downstream_cost=eur("2500"),
            dealer_required_contribution=eur("12500"),
            costs=template.to_cost_lines(),
        )
    )
    assert result.status is ScenarioStatus.INCOMPLETE
    assert any("Registration tax" in item for item in result.missing_inputs)


def test_a_confirmed_zero_line_survives_the_round_trip(db, clock):
    template_id = save(
        db,
        clock,
        lines=[
            cost_templates.TemplateLine("Transport", eur("1500")),
            cost_templates.TemplateLine("Registration tax", None, confirmed_zero=True),
        ],
    )
    with db.open() as conn:
        template = cost_templates.get(conn, template_id)
    assert template.unknown_lines == []
    assert template.lines[1].confirmed_zero


def test_a_recoverable_line_keeps_its_treatment(db, clock):
    template_id = save(
        db,
        clock,
        lines=[
            cost_templates.TemplateLine("Transport", eur("1500")),
            cost_templates.TemplateLine(
                "Recoverable VAT paid up front", eur("42000"), tax_treatment="recoverable"
            ),
        ],
    )
    with db.open() as conn:
        template = cost_templates.get(conn, template_id)
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur("200000"),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            retail_assumption=eur("230000"),
            dealer_downstream_cost=eur("2500"),
            dealer_required_contribution=eur("12500"),
            costs=template.to_cost_lines(),
        )
    )
    assert result.included_costs == eur("1500"), "recoverable tax is not a final cost"
    assert result.recoverable_tax == eur("42000")


def test_the_most_specific_template_is_offered(db, clock):
    save(db, clock, name="Anything to DE", origin_country=None, model_family=None)
    specific = save(db, clock, name="CZ to DE, G-Class")

    with db.open() as conn:
        best = cost_templates.best_for_route(
            conn,
            origin_country="CZ",
            destination_country="DE",
            model_family="mercedes_g63",
        )
    assert best.id == specific
    assert best.name == "CZ to DE, G-Class"


def test_a_template_for_another_route_is_not_offered(db, clock):
    save(db, clock)
    with db.open() as conn:
        assert (
            cost_templates.best_for_route(
                conn, origin_country="CZ", destination_country="IT", model_family="mercedes_g63"
            )
            is None
        )


def test_no_destination_means_no_template_is_offered(db, clock):
    save(db, clock)
    with db.open() as conn:
        assert (
            cost_templates.best_for_route(
                conn, origin_country="CZ", destination_country=None
            )
            is None
        )


def test_a_lapsed_template_says_so_loudly(db, clock):
    template_id = save(db, clock, review_due_at=FROZEN_NOW - timedelta(days=1))
    with db.open() as conn:
        template = cost_templates.get(conn, template_id)

    assert template.is_stale(clock.now())
    described = template.describe(clock.now())
    assert "lapsed" in described
    assert "registration taxes change" in described.lower()


def test_a_lapsed_template_is_still_offered_rather_than_hidden(db, clock):
    """Hiding it would silently produce an empty cost sheet instead of a warning."""
    save(db, clock, review_due_at=FROZEN_NOW - timedelta(days=1))
    with db.open() as conn:
        best = cost_templates.best_for_route(
            conn, origin_country="CZ", destination_country="DE", model_family="mercedes_g63"
        )
    assert best is not None
    assert best.is_stale(clock.now())


def test_a_retired_template_is_not_offered_but_survives(db, clock):
    template_id = save(db, clock)
    with db.write() as conn:
        cost_templates.retire(conn, template_id, now=clock.now())

    with db.open() as conn:
        assert cost_templates.list_templates(conn) == []
        archived = cost_templates.list_templates(conn, include_retired=True)
        best = cost_templates.best_for_route(
            conn, origin_country="CZ", destination_country="DE", model_family="mercedes_g63"
        )
    assert len(archived) == 1
    assert best is None


def test_templates_due_for_review_are_listed(db, clock):
    save(db, clock, review_due_at=FROZEN_NOW + timedelta(days=5))
    with db.open() as conn:
        due = cost_templates.due_for_review(conn, now=clock.now(), within_days=30)
    assert len(due) == 1


def test_the_assumption_text_names_the_reviewer_and_the_basis(db, clock):
    template_id = save(db, clock)
    with db.open() as conn:
        template = cost_templates.get(conn, template_id)
    text = template.assumption(clock.now())
    assert "Max Watkinson" in text
    assert "transporter quotes" in text
    assert "CZ to DE" in text
