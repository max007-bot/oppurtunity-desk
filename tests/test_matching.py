"""Buyer matching: hard requirements fail closed and every reason stays visible."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from desk.config import FitStatus
from desk.money import Money
from desk.repositories.companies import BuyerBrief, Company
from desk.repositories.supply import Offer, Vehicle
from desk.services.matching import (
    budget_headroom,
    category_fit_prospect,
    match_offer_to_brief,
    rank,
)
from desk.services.normalization import neutralise_csv_cell, resolve_model, slugify

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def eur(amount) -> Money:
    return Money.from_decimal(Decimal(str(amount)), "EUR")


def make_offer(**overrides) -> Offer:
    base = dict(
        id="ofr_1",
        source_id="s",
        external_record_id="OFR-1",
        vehicle_id="veh_1",
        seller_company_id=None,
        seller_name="Demo Seller",
        listing_url=None,
        price=eur(200000),
        price_basis="net",
        vat_regime="standard",
        price_evidence_type="supply_asking",
        raw_price_text=None,
        status="availability_confirmed",
        stock_kind="physical_stock",
        location_country="CZ",
        location_city="Prague",
        authority_to_sell="confirmed",
        available_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=14),
        availability_confirmed_at=NOW - timedelta(hours=3),
        last_observation_state="seen",
        last_seen_at=NOW,
    )
    base.update(overrides)
    return Offer(**base)


def make_vehicle(**overrides) -> Vehicle:
    base = dict(
        id="veh_1",
        model_family="mercedes_g63",
        variant="G 63 4MATIC",
        generation="w463a",
        identity_review_status="reviewed",
        first_registration=NOW - timedelta(days=120),
        mileage_km=11500,
        powertrain="petrol",
        steering="lhd",
        seats=5,
        specification={"rear_entertainment": True, "night_package": True},
    )
    base.update(overrides)
    return Vehicle(**base)


def make_brief(**overrides) -> BuyerBrief:
    base = dict(
        id="brf_1",
        company_id="cmp_1",
        model_family="mercedes_g63",
        variant=None,
        required_specs={
            "steering": "lhd",
            "seats": 5,
            "powertrain": "petrol",
            "max_mileage_km": 25000,
            "max_registration_age_months": 24,
            "must_have_options": ["rear_entertainment"],
            "stock_requirement": "physical_stock",
        },
        preferred_specs={"night_package": True},
        budget=eur(215000),
        budget_basis="net",
        budget_vat_regime="standard",
        quantity=1,
        destination_country="DE",
        route="buy_for_stock",
        required_by=NOW + timedelta(days=21),
        conversation_date=NOW - timedelta(days=2),
        confirmed_at=NOW - timedelta(days=2),
        confirmed_by="Test Buyer, Purchasing Manager",
        expires_at=NOW + timedelta(days=28),
        evidence_note="recorded call",
    )
    base.update(overrides)
    return BuyerBrief(**base)


def make_company(**overrides) -> Company:
    base = dict(
        id="cmp_1",
        legal_name="Demo Test Dealer GmbH",
        country="DE",
        category="dealer",
        buying_route="buy_for_stock",
        buying_authority="purchasing manager",
    )
    base.update(overrides)
    return Company(**base)


def run(demo_config, *, offer=None, vehicle=None, brief=None, **kwargs):
    return match_offer_to_brief(
        offer=offer or make_offer(),
        vehicle=vehicle if vehicle is not None else make_vehicle(),
        brief=brief or make_brief(),
        company=make_company(),
        now=NOW,
        config=demo_config,
        **kwargs,
    )


# -- the happy path ------------------------------------------------------


def test_everything_matching_is_a_specification_fit_and_nothing_more(demo_config):
    result = run(demo_config)
    assert result.fit_status is FitStatus.SPECIFICATION_FIT
    assert not result.failed
    assert not result.unknown
    assert any("does not prove the buyer will buy" in note for note in result.notes)


def test_specification_fit_is_eligible_for_the_contact_ready_queue(demo_config):
    assert run(demo_config).contact_ready_eligible


def test_score_components_are_transparent(demo_config):
    result = run(demo_config, economics_reviewed=True)
    assert result.score_breakdown == {
        "specification": 40,
        "confirmed_requirement": 25,
        "timing": 15,
        "reviewed_economics": 10,
        "freshness": 10,
    }
    assert result.score == 100


def test_contact_availability_contributes_nothing_to_the_score(demo_config):
    result = run(demo_config)
    assert "contact" not in " ".join(result.score_breakdown)
    assert any("separate gates" in note for note in result.notes)


# -- known conflicts are rejections --------------------------------------


def test_four_seats_against_a_five_seat_requirement_is_rejected(demo_config):
    result = run(demo_config, vehicle=make_vehicle(seats=4))
    assert result.fit_status is FitStatus.NO_MATCH
    reasons = [r.reason for r in result.failed]
    assert any("requires 5 homologated seats, the car has 4" in reason for reason in reasons)
    assert not result.contact_ready_eligible


def test_allocation_cannot_fulfil_a_physical_stock_requirement(demo_config):
    result = run(
        demo_config,
        offer=make_offer(
            stock_kind="allocation",
            status="allocation",
            available_from=NOW + timedelta(days=120),
            availability_confirmed_at=None,
        ),
    )
    assert result.fit_status is FitStatus.NO_MATCH
    reasons = " ".join(r.reason for r in result.failed)
    assert "build slot or allocation" in reasons
    assert "no current fulfilment" in reasons


def test_wrong_steering_side_is_rejected(demo_config):
    result = run(demo_config, vehicle=make_vehicle(steering="rhd"))
    assert result.fit_status is FitStatus.NO_MATCH
    assert any("steering side" in r.name for r in result.failed)


def test_mileage_over_the_ceiling_is_rejected(demo_config):
    result = run(demo_config, vehicle=make_vehicle(mileage_km=31000))
    assert result.fit_status is FitStatus.NO_MATCH
    assert any("maximum mileage" in r.name for r in result.failed)


def test_registration_too_old_is_rejected(demo_config):
    result = run(demo_config, vehicle=make_vehicle(first_registration=NOW - timedelta(days=1200)))
    assert result.fit_status is FitStatus.NO_MATCH
    assert any("registration age" in r.name for r in result.failed)


def test_required_option_recorded_as_absent_is_rejected(demo_config):
    result = run(demo_config, vehicle=make_vehicle(specification={"rear_entertainment": False}))
    assert result.fit_status is FitStatus.NO_MATCH
    assert any("rear_entertainment" in r.name for r in result.failed)


def test_incompatible_vat_basis_is_rejected(demo_config):
    result = run(demo_config, offer=make_offer(price_basis="gross", vat_regime="margin"))
    assert result.fit_status is FitStatus.NO_MATCH
    assert any("price and VAT basis" in r.name for r in result.failed)


def test_asking_price_above_the_budget_is_rejected(demo_config):
    result = run(demo_config, offer=make_offer(price=eur(240000)))
    assert result.fit_status is FitStatus.NO_MATCH
    assert any("budget headroom" in r.name for r in result.failed)


def test_seller_stated_reserved_is_rejected_because_the_seller_said_so(demo_config):
    result = run(demo_config, offer=make_offer(status="reserved"))
    assert result.fit_status is FitStatus.NO_MATCH
    assert any("reserved" in r.reason for r in result.failed)


def test_delivery_after_the_deadline_is_rejected(demo_config):
    result = run(demo_config, offer=make_offer(available_from=NOW + timedelta(days=60)))
    assert result.fit_status is FitStatus.NO_MATCH
    assert any("delivery date" in r.name for r in result.failed)


def test_a_rejection_scores_nothing(demo_config):
    result = run(demo_config, vehicle=make_vehicle(seats=4))
    assert result.score == 0


# -- missing facts are never a pass --------------------------------------


def test_unknown_required_option_needs_verification_and_is_never_assumed(demo_config):
    result = run(demo_config, vehicle=make_vehicle(specification={}))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION
    unknown = " ".join(r.reason for r in result.unknown)
    assert "rather than assuming it is fitted" in unknown
    assert not result.contact_ready_eligible


def test_unknown_seat_count_needs_verification(demo_config):
    result = run(demo_config, vehicle=make_vehicle(seats=None))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION


def test_unknown_stock_state_needs_verification(demo_config):
    result = run(demo_config, offer=make_offer(stock_kind="unknown"))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION
    assert any("physically exists" in r.reason for r in result.unknown)


def test_stale_supply_confirmation_needs_verification(demo_config):
    result = run(demo_config, offer=make_offer(availability_confirmed_at=NOW - timedelta(hours=48)))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION
    assert any("must not be described as currently available" in r.reason for r in result.unknown)


def test_never_confirmed_supply_needs_verification(demo_config):
    result = run(demo_config, offer=make_offer(availability_confirmed_at=None))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION


def test_vehicle_awaiting_identity_review_needs_verification(demo_config):
    result = run(demo_config, vehicle=make_vehicle(identity_review_status="review_required"))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION
    assert any("awaiting review" in r.reason for r in result.unknown)


def test_quantity_shortfall_is_reported_not_silently_passed(demo_config):
    result = run(demo_config, brief=make_brief(quantity=5))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION
    assert any("4 remain unsourced" in r.reason for r in result.unknown)


def test_mixed_currency_budget_cannot_be_compared(demo_config):
    brief = make_brief(budget=Money.from_decimal("215000", "GBP"))
    result = run(demo_config, brief=brief)
    assert any("dated FX rate is required" in r.reason for r in result.unknown)


# -- disappearance and failure are not sales -----------------------------


def test_not_observed_is_not_a_sale(demo_config):
    result = run(demo_config, offer=make_offer(last_observation_state="not_seen"))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION
    assert any("not evidence of a sale" in r.reason for r in result.unknown)
    assert not any("sold" in r.reason.lower() for r in result.rules)


def test_fetch_failure_is_not_a_sale(demo_config):
    result = run(demo_config, offer=make_offer(last_observation_state="fetch_failed"))
    assert result.fit_status is FitStatus.NEEDS_VERIFICATION
    assert any("not evidence the car was sold" in r.reason for r in result.unknown)


# -- expiry --------------------------------------------------------------


def test_expired_brief_requires_reconfirmation_and_scores_nothing(demo_config):
    result = run(demo_config, brief=make_brief(expires_at=NOW - timedelta(days=2)))
    assert result.fit_status is FitStatus.BRIEF_EXPIRED
    assert result.score == 0
    assert any("needs reconfirmation" in note for note in result.notes)
    assert not result.contact_ready_eligible


def test_match_expiry_is_the_earliest_of_its_inputs(demo_config):
    result = run(demo_config, offer=make_offer(valid_until=NOW + timedelta(hours=2)))
    assert result.expires_at == NOW + timedelta(hours=2)


def test_an_unconfirmed_brief_earns_no_confirmation_points(demo_config):
    result = run(demo_config, brief=make_brief(confirmed_at=None, confirmed_by=None))
    assert result.score_breakdown["confirmed_requirement"] == 0


# -- category-fit prospects ---------------------------------------------


def test_category_fit_prospect_is_not_a_confirmed_buyer(demo_config):
    result = category_fit_prospect(
        offer=make_offer(), vehicle=make_vehicle(), company=make_company(), now=NOW, config=demo_config
    )
    assert result.fit_status is FitStatus.CATEGORY_FIT_PROSPECT
    assert result.brief_id is None
    assert result.score == 0
    assert not result.contact_ready_eligible
    assert any("not a qualified opportunity" in note for note in result.notes)


def test_ranking_puts_specification_fits_above_contactable_weak_records(demo_config):
    fit = run(demo_config)
    prospect = category_fit_prospect(
        offer=make_offer(), vehicle=make_vehicle(), company=make_company(), now=NOW, config=demo_config
    )
    needs = run(demo_config, vehicle=make_vehicle(specification={}))
    ordered = rank([prospect, needs, fit])
    assert [r.fit_status for r in ordered] == [
        FitStatus.SPECIFICATION_FIT,
        FitStatus.NEEDS_VERIFICATION,
        FitStatus.CATEGORY_FIT_PROSPECT,
    ]


def test_budget_headroom_needs_a_compatible_basis():
    assert budget_headroom(make_offer(), make_brief()) == eur(15000)
    assert budget_headroom(make_offer(price_basis="gross", vat_regime="margin"), make_brief()) is None
    assert budget_headroom(make_offer(price=None), make_brief()) is None


# -- model normalisation -------------------------------------------------


def test_amg_line_styling_is_never_equated_with_an_amg_g63():
    result = resolve_model("Mercedes-Benz G 63 AMG Line styling")
    assert not result.resolved
    assert result.status == "lookalike_conflict"
    assert "confirm the exact model" in " ".join(result.reasons)


def test_amg_line_on_a_lesser_model_does_not_resolve_to_g63():
    result = resolve_model("Mercedes-Benz G 500 AMG Line")
    assert not result.resolved
    assert result.family is None


def test_a_genuine_g63_resolves():
    result = resolve_model("Mercedes-AMG G 63 4MATIC")
    assert result.resolved
    assert result.family == "mercedes_g63"


def test_internal_family_keys_resolve_despite_underscores():
    for key in (
        "mercedes_g63",
        "mercedes_s_class",
        "bmw_7_series",
        "bmw_x5",
        "bmw_x7",
        "mercedes_gle",
        "mercedes_glc",
        "mercedes_gls",
    ):
        assert resolve_model(key).family == key


# The titles below are how real adverts for these cars are actually written. An
# earlier matcher was built from family names alone and missed every S-Class,
# because no advert says "S-Class"; it says "S 450".
REAL_ADVERT_TITLES = [
    ("Mercedes-Benz S 450 4MATIC L", "mercedes_s_class"),
    ("Mercedes-Benz S 580 4MATIC lang", "mercedes_s_class"),
    ("Mercedes-AMG G 63 4MATIC", "mercedes_g63"),
    ("BMW 740d xDrive M Sportpaket", "bmw_7_series"),
    ("BMW X5 xDrive40i", "bmw_x5"),
    ("BMW X7 M60i xDrive", "bmw_x7"),
    ("Mercedes-Benz GLE 350 de 4MATIC", "mercedes_gle"),
    ("Mercedes-AMG GLE 63 S 4MATIC+", "mercedes_gle"),
    ("Mercedes-Benz GLC 200 4MATIC", "mercedes_glc"),
    ("Mercedes-Benz GLS 450 d 4MATIC", "mercedes_gls"),
]


@pytest.mark.parametrize("title,expected", REAL_ADVERT_TITLES)
def test_model_text_written_the_way_adverts_write_it_resolves(title, expected):
    result = resolve_model(title)
    assert result.resolved, f"{title!r} went to review: {result.reasons}"
    assert result.family == expected


def test_a_gls_63_is_not_also_an_s_class():
    """The text "gls 63" contains "s 63", which is an S-Class designation.

    Matching on bare substrings made this car match two families at once and sent
    it to review as ambiguous. The family needles are matched at a word start.
    """
    result = resolve_model("Mercedes-AMG GLS 63 4MATIC+")
    assert result.resolved
    assert result.family == "mercedes_gls"


def test_the_word_single_does_not_make_a_car_a_gle():
    """The word "single" contains "gle". Prose must not resolve a family."""
    result = resolve_model("Single owner, full service history")
    assert not result.resolved
    assert result.status == "review_required"


def test_a_trailing_boundary_is_not_required():
    """A word-*start* boundary only: "740" still has to match "740d"."""
    assert resolve_model("BMW 740d xDrive").family == "bmw_7_series"


def test_unsupported_model_goes_to_review_rather_than_being_guessed():
    result = resolve_model("Bentley Continental GT")
    assert not result.resolved
    assert result.status == "review_required"


def test_declared_family_contradicting_the_text_is_flagged():
    result = resolve_model("BMW X5 xDrive40d", declared_family="mercedes_g63")
    assert not result.resolved
    assert "contradicts the source text" in " ".join(result.reasons)


def test_m_sport_package_does_not_become_an_m_model():
    result = resolve_model("BMW 740d M Sport")
    # The 7 Series family still resolves; the styling token is not treated as a
    # performance designation, and no M-model equivalence is invented.
    assert result.family in (None, "bmw_7_series")
    assert "m760" not in (result.family or "")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=SUM(A1:A9)", "'=SUM(A1:A9)"),
        ("+44 20 7000 0000", "'+44 20 7000 0000"),
        ("-Demo Dealer", "'-Demo Dealer"),
        ("@example", "'@example"),
        ("Demo Prestige Dealer A", "Demo Prestige Dealer A"),
        (None, ""),
    ],
)
def test_formula_like_export_cells_are_neutralised(value, expected):
    assert neutralise_csv_cell(value) == expected


def test_slugify_is_stable_across_accents_and_separators():
    assert slugify("Mercedes-Benz  S-Klasse") == "mercedes benz s klasse"
    assert slugify("Škoda") == "skoda"
