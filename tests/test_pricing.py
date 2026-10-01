"""Comparables, the asking-price gap and the mandatory scenario arithmetic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from desk.config import Config, PriceEvidenceType, ScenarioStatus
from desk.money import Money
from desk.repositories.supply import Offer, Vehicle
from desk.services.normalization import basis_compatible, new_vehicle_indicator
from desk.services.pricing import (
    ComparableCandidate,
    CostLine,
    ScenarioInput,
    asking_price_gap,
    build_comparable_set,
    compute_scenario,
    hypothetical_trade_price,
    sensitivity,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def eur(amount) -> Money:
    return Money.from_decimal(Decimal(str(amount)), "EUR")


# -- the worked example from section 7 -----------------------------------


def manual_costs() -> list[CostLine]:
    return [
        CostLine("Transport", eur(1500), paid_at=NOW),
        CostLine("Preparation", eur(1000), paid_at=NOW),
        CostLine("Documents and handling", eur(500), paid_at=NOW),
        CostLine("Funding cost", eur(2000), paid_at=NOW),
        CostLine("Risk allowance", eur(1500), paid_at=NOW),
    ]


def manual_scenario(retail: str) -> ScenarioInput:
    return ScenarioInput(
        acquisition=eur(200000),
        acquisition_basis="net",
        acquisition_vat_regime="standard",
        retail_assumption=eur(retail),
        dealer_downstream_cost=eur(2500),
        dealer_required_contribution=eur(12500),
        costs=manual_costs(),
        comparable_median=eur(235000),
    )


def test_manual_worked_example_exactly():
    """Mandatory case: 35,000 gap, 206,500 outlay, 8,500 contribution."""
    result = compute_scenario(manual_scenario("230000"))
    assert result.included_costs == eur(6500)
    assert result.total_outlay == eur(206500)
    assert result.asking_gap == eur(35000)
    assert result.hypothetical_trade_price == eur(215000)
    assert result.contribution == eur(8500)
    assert result.status is ScenarioStatus.COMPLETE


def test_manual_downside_example_exactly():
    """Mandatory case: retail falls 10,000, contribution becomes minus 1,500."""
    result = compute_scenario(manual_scenario("220000"))
    assert result.hypothetical_trade_price == eur(205000)
    assert result.contribution == eur(-1500)
    assert result.contribution.is_negative


def test_sensitivity_reproduces_the_downside_from_the_base_case():
    base = manual_scenario("230000")
    shifted = sensitivity(base, [eur(-10000)])
    assert len(shifted) == 1
    _delta, result = shifted[0]
    assert result.hypothetical_trade_price == eur(205000)
    assert result.contribution == eur(-1500)


def test_asking_gap_is_never_described_as_profit():
    result = compute_scenario(manual_scenario("230000"))
    assert result.asking_gap == eur(35000)
    # The contribution on the same inputs is far smaller, which is the whole point.
    assert result.contribution < result.asking_gap


def test_hypothetical_trade_price_helper():
    assert hypothetical_trade_price(
        retail=eur(230000), dealer_cost=eur(2500), dealer_contribution=eur(12500)
    ) == eur(215000)


# -- incompleteness ------------------------------------------------------


def test_unknown_cost_makes_the_scenario_incomplete_not_zero_cost():
    costs = manual_costs()
    costs[0] = CostLine("Transport", None)  # unknown, not zero
    data = ScenarioInput(
        acquisition=eur(200000),
        acquisition_basis="net",
        acquisition_vat_regime="standard",
        retail_assumption=eur(230000),
        dealer_downstream_cost=eur(2500),
        dealer_required_contribution=eur(12500),
        costs=costs,
    )
    result = compute_scenario(data)
    assert result.status is ScenarioStatus.INCOMPLETE
    assert any("Transport" in item for item in result.missing_inputs)
    assert result.contribution is None
    assert result.total_outlay is None


def test_confirmed_zero_differs_from_unknown():
    costs = manual_costs()
    costs[0] = CostLine("Transport", None, confirmed_zero=True, paid_at=NOW)
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur(200000),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            retail_assumption=eur(230000),
            dealer_downstream_cost=eur(2500),
            dealer_required_contribution=eur(12500),
            costs=costs,
        )
    )
    assert result.status is ScenarioStatus.COMPLETE
    assert result.included_costs == eur(5000)
    assert result.contribution == eur(10000)


def test_unknown_vat_regime_blocks_completion():
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur(200000),
            acquisition_basis="net",
            acquisition_vat_regime="unknown",
            retail_assumption=eur(230000),
            dealer_downstream_cost=eur(2500),
            dealer_required_contribution=eur(12500),
            costs=manual_costs(),
        )
    )
    assert result.status is ScenarioStatus.INCOMPLETE
    assert any("VAT regime" in item for item in result.missing_inputs)


def test_incompatible_purchase_and_sale_basis_blocks_completion():
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur(200000),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            sale=eur(230000),
            sale_basis="gross",
            sale_vat_regime="margin",
            sale_evidence_type=PriceEvidenceType.DEALER_BID.value,
            costs=manual_costs(),
        )
    )
    assert result.status is ScenarioStatus.INCOMPLETE
    assert any("compatible tax basis" in item for item in result.missing_inputs)


def test_costs_already_in_the_purchase_price_are_not_double_counted():
    costs = manual_costs()
    costs.append(
        CostLine("Delivery already in the invoice", eur(900), already_in_purchase_price=True, paid_at=NOW)
    )
    result = compute_scenario(manual_scenario("230000").__class__(
        acquisition=eur(200000),
        acquisition_basis="net",
        acquisition_vat_regime="standard",
        retail_assumption=eur(230000),
        dealer_downstream_cost=eur(2500),
        dealer_required_contribution=eur(12500),
        costs=costs,
    ))
    assert result.included_costs == eur(6500)


def test_expected_commission_stays_blank_without_an_approved_rule():
    result = compute_scenario(manual_scenario("230000"))
    assert result.expected_commission is None


# -- cash timing ---------------------------------------------------------


def test_recoverable_tax_is_cash_out_but_not_a_final_cost():
    costs = manual_costs()
    costs.append(
        CostLine(
            "Recoverable VAT paid up front",
            eur(42000),
            tax_treatment="recoverable",
            paid_at=NOW,
            refunded_at=NOW + timedelta(days=60),
        )
    )
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur(200000),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            retail_assumption=eur(230000),
            dealer_downstream_cost=eur(2500),
            dealer_required_contribution=eur(12500),
            costs=costs,
        )
    )
    # Recoverable tax leaves the final cost alone...
    assert result.included_costs == eur(6500)
    assert result.contribution == eur(8500)
    # ...but it is money out of the door before the refund arrives.
    assert result.recoverable_tax == eur(42000)
    assert result.cash_outflow == eur(248500)
    assert result.cash_schedule_status == "complete"


def test_missing_refund_date_makes_the_cash_schedule_incomplete():
    costs = manual_costs()
    costs.append(
        CostLine("Recoverable VAT paid up front", eur(42000), tax_treatment="recoverable", paid_at=NOW)
    )
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur(200000),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            retail_assumption=eur(230000),
            dealer_downstream_cost=eur(2500),
            dealer_required_contribution=eur(12500),
            costs=costs,
        )
    )
    assert result.cash_schedule_status == "incomplete"


def test_missing_payment_date_makes_the_cash_schedule_incomplete():
    costs = [CostLine("Transport", eur(1500))]  # known amount, no payment date
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur(200000),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            retail_assumption=eur(230000),
            dealer_downstream_cost=eur(2500),
            dealer_required_contribution=eur(12500),
            costs=costs,
        )
    )
    assert result.status is ScenarioStatus.COMPLETE
    assert result.cash_schedule_status == "incomplete"


# -- sale evidence types -------------------------------------------------


def test_derived_trade_price_is_labelled_an_assumption_not_a_bid():
    result = compute_scenario(manual_scenario("230000"))
    assert result.sale_evidence_type == PriceEvidenceType.ANALYST_ASSUMPTION.value
    assert "not a bid" in result.sale_evidence_label()


def test_a_real_bid_keeps_the_same_arithmetic_but_better_evidence():
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur(200000),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            sale=eur(215000),
            sale_basis="net",
            sale_vat_regime="standard",
            sale_evidence_type=PriceEvidenceType.DEALER_BID.value,
            costs=manual_costs(),
        )
    )
    assert result.contribution == eur(8500)
    assert "actual bid" in result.sale_evidence_label()


def test_a_budget_is_flagged_as_not_a_commitment():
    result = compute_scenario(
        ScenarioInput(
            acquisition=eur(200000),
            acquisition_basis="net",
            acquisition_vat_regime="standard",
            sale=eur(215000),
            sale_basis="net",
            sale_vat_regime="standard",
            sale_evidence_type=PriceEvidenceType.BUYER_BUDGET.value,
            costs=manual_costs(),
        )
    )
    assert "not a commitment" in result.sale_evidence_label()


# -- comparable sets -----------------------------------------------------


def offer(**overrides) -> Offer:
    base = dict(
        id="ofr_target",
        source_id="s",
        external_record_id="OFR-1",
        vehicle_id="veh_1",
        seller_company_id=None,
        seller_name="Seller",
        listing_url=None,
        price=eur(200000),
        price_basis="net",
        vat_regime="standard",
        price_evidence_type="supply_asking",
        raw_price_text=None,
        status="availability_confirmed",
        stock_kind="physical_stock",
        location_country="CZ",
        location_city=None,
        authority_to_sell="confirmed",
        available_from=NOW,
        valid_until=None,
        availability_confirmed_at=NOW,
        last_observation_state="seen",
        last_seen_at=NOW,
    )
    base.update(overrides)
    return Offer(**base)


def vehicle(**overrides) -> Vehicle:
    base = dict(
        id="veh_1",
        model_family="mercedes_g63",
        variant="G 63 4MATIC",
        generation=None,
        mileage_km=11500,
        powertrain="petrol",
        steering="lhd",
        seats=5,
        first_registration=NOW - timedelta(days=120),
    )
    base.update(overrides)
    return Vehicle(**base)


def candidate(offer_id: str, amount: str, **overrides) -> ComparableCandidate:
    base = dict(
        offer_id=offer_id,
        observation_id=f"obs_{offer_id}",
        price=eur(amount),
        observed_at=NOW - timedelta(days=1),
        vehicle_id=f"veh_{offer_id}",
        model_family="mercedes_g63",
        powertrain="petrol",
        steering="lhd",
        seats=5,
        mileage_km=12000,
        first_registration=NOW - timedelta(days=125),
        location_country="DE",
        price_basis="net",
        vat_regime="standard",
        price_evidence_type="retail_asking",
    )
    base.update(overrides)
    return ComparableCandidate(**base)


def build(candidates, config: Config, **kwargs):
    return build_comparable_set(
        target_offer=offer(),
        target_vehicle=vehicle(),
        candidates=candidates,
        now=NOW,
        config=config,
        **kwargs,
    )


# -- variant, which a generation does not imply --------------------------


def s_class_vehicle(variant: str) -> Vehicle:
    return vehicle(model_family="mercedes_s_class", variant=variant, generation="w223")


def s_class_candidate(offer_id: str, amount: str, variant: str) -> ComparableCandidate:
    return candidate(
        offer_id,
        amount,
        model_family="mercedes_s_class",
        generation="w223",
        variant=variant,
    )


def build_s_class(target_variant: str, candidates, config: Config, **kwargs):
    return build_comparable_set(
        target_offer=offer(),
        target_vehicle=s_class_vehicle(target_variant),
        candidates=candidates,
        now=NOW,
        config=config,
        **kwargs,
    )


def test_a_different_variant_in_the_same_generation_is_excluded(demo_config):
    """The case this rule exists for.

    An S 450 and an S 580 share the W223 body, so the generation filter lets both
    through. Their asking prices differ by tens of thousands, and a median across
    the two describes no car that anyone can actually buy.
    """
    result = build_s_class(
        "S 450",
        [
            s_class_candidate("a", "138000", "S 450"),
            s_class_candidate("b", "142000", "S 450"),
            s_class_candidate("c", "188000", "S 580"),
        ],
        demo_config,
    )
    excluded = {d.candidate.offer_id: d.reason for d in result.excluded}
    assert "c" in excluded
    assert "different variant" in excluded["c"]
    assert "S 580" in excluded["c"]


def test_the_variant_rule_survives_a_difference_in_spelling(demo_config):
    """Sources punctuate a variant inconsistently; that is not a different car."""
    result = build_s_class(
        "S 450",
        [
            s_class_candidate("a", "138000", "S-450"),
            s_class_candidate("b", "142000", "s 450"),
            s_class_candidate("c", "140000", "S 450"),
        ],
        demo_config,
    )
    assert result.has_median
    assert len(result.included) == 3


def test_a_suffix_somebody_typed_is_not_a_different_car(demo_config):
    """The failure this rule had to avoid.

    "G 63" and "G 63 4MATIC" are the same car described at two levels of detail.
    An exact string comparison threw one of them out of the demo set and moved
    the median, which is precisely the kind of silent change the tool exists to
    prevent.
    """
    result = build(
        [
            candidate("a", "228000", variant="G 63 4MATIC"),
            candidate("b", "232000", variant="G 63"),
            candidate("c", "240000", variant="G 63 4MATIC Edition"),
        ],
        demo_config,
    )
    assert result.has_median
    assert len(result.included) == 3


def test_a_variant_with_no_designation_at_all_is_not_a_mismatch(demo_config):
    result = build(
        [
            candidate("a", "228000", variant="AMG Line"),
            candidate("b", "232000", variant="G 63 4MATIC"),
            candidate("c", "240000", variant="G 63"),
        ],
        demo_config,
    )
    assert len(result.included) == 3


def test_an_unknown_variant_is_not_treated_as_a_mismatch(demo_config):
    """Unknown blocks a claim; it does not manufacture an exclusion reason.

    Saying "different variant" about a car whose variant nobody recorded would be
    inventing a fact. The candidate stays in, and the usual filters still apply.
    """
    result = build_s_class(
        "S 450",
        [
            s_class_candidate("a", "138000", "S 450"),
            candidate("b", "142000", model_family="mercedes_s_class", generation="w223"),
            candidate("c", "140000", model_family="mercedes_s_class", generation="w223"),
        ],
        demo_config,
    )
    assert result.has_median
    assert len(result.included) == 3


def test_the_variant_rule_can_be_turned_off_by_the_analyst(demo_config):
    from desk.services.pricing import ComparableFilters

    result = build_s_class(
        "S 450",
        [
            s_class_candidate("a", "138000", "S 450"),
            s_class_candidate("b", "142000", "S 450"),
            s_class_candidate("c", "188000", "S 580"),
        ],
        demo_config,
        filters=ComparableFilters(require_same_variant=False),
    )
    assert len(result.included) == 3
    assert result.filters["require_same_variant"] is False


def test_the_variant_decision_is_recorded_in_the_filters(demo_config):
    result = build_s_class("S 450", [s_class_candidate("a", "138000", "S 450")], demo_config)
    assert result.filters["require_same_variant"] is True


def test_median_needs_three_distinct_vehicles(demo_config):
    result = build([candidate("a", "228000"), candidate("b", "232000")], demo_config)
    assert result.status == "insufficient_comparables"
    assert result.median is None
    assert "at least 3" in " ".join(result.notes)


def test_only_one_usable_comparable_shows_the_observation_not_a_median(demo_config):
    result = build(
        [candidate("a", "228000"), candidate("b", "232000", vat_regime="margin", price_basis="gross")],
        demo_config,
    )
    assert result.status == "insufficient_comparables"
    assert result.distinct_vehicles == 1
    assert len(result.included) == 1


def test_median_from_five_matches_the_manual(demo_config):
    result = build(
        [
            candidate("a", "228000"),
            candidate("b", "232000"),
            candidate("c", "235000"),
            candidate("d", "239000"),
            candidate("e", "243000"),
        ],
        demo_config,
    )
    assert result.status == "median_available"
    assert result.median == eur(235000)
    assert result.low == eur(228000)
    assert result.high == eur(243000)
    assert result.distinct_vehicles == 5


def test_same_vin_on_two_sources_counts_once(demo_config):
    shared = dict(vin="WDB4632761X200003", vin_verified=True, vehicle_id="veh_shared")
    result = build(
        [
            candidate("a", "235000", **shared),
            candidate("b", "236500", **shared),
            candidate("c", "232000"),
            candidate("d", "239000"),
        ],
        demo_config,
    )
    assert result.distinct_vehicles == 3
    excluded = [d for d in result.excluded if "same vehicle identity" in d.reason]
    assert len(excluded) == 1


def test_margin_scheme_gross_is_excluded_not_divided_by_a_vat_rate(demo_config):
    result = build(
        [
            candidate("a", "249000", price_basis="gross", vat_regime="margin"),
            candidate("b", "232000"),
            candidate("c", "235000"),
            candidate("d", "239000"),
        ],
        demo_config,
    )
    assert result.median == eur(235000)
    reasons = [d.reason for d in result.excluded]
    assert any("tax basis is not comparable" in reason for reason in reasons)


def test_mixed_currency_is_excluded_until_a_dated_rate_is_entered(demo_config):
    gbp = ComparableCandidate(
        offer_id="g",
        observation_id="obs_g",
        price=Money.from_decimal("205000", "GBP"),
        observed_at=NOW - timedelta(days=1),
        model_family="mercedes_g63",
        price_basis="net",
        vat_regime="standard",
        price_evidence_type="retail_asking",
        mileage_km=12000,
        powertrain="petrol",
        steering="lhd",
    )
    result = build(
        [gbp, candidate("b", "232000"), candidate("c", "235000"), candidate("d", "239000")],
        demo_config,
    )
    assert result.median == eur(235000)
    assert any("dated FX rate" in d.reason for d in result.excluded)


def test_evidence_types_are_never_blended(demo_config):
    result = build(
        [
            candidate("bid", "212000", price_evidence_type="dealer_bid"),
            candidate("sold", "233000", price_evidence_type="completed_transaction"),
            candidate("b", "232000"),
            candidate("c", "235000"),
            candidate("d", "239000"),
        ],
        demo_config,
    )
    assert result.median == eur(235000)
    blended = [d for d in result.excluded if "price evidence type" in d.reason]
    assert len(blended) == 2


def test_same_badge_is_not_enough_when_the_car_is_very_different(demo_config):
    result = build(
        [
            candidate("old", "165000", mileage_km=95000),
            candidate("b", "232000"),
            candidate("c", "235000"),
            candidate("d", "239000"),
        ],
        demo_config,
    )
    assert result.median == eur(235000)
    assert any("mileage differs" in d.reason for d in result.excluded)


def test_stale_observations_drop_out_of_the_comparison(demo_config):
    result = build(
        [
            candidate("stale", "231000", observed_at=NOW - timedelta(days=40)),
            candidate("b", "232000"),
            candidate("c", "235000"),
            candidate("d", "239000"),
        ],
        demo_config,
    )
    assert result.median == eur(235000)
    assert any("freshness window" in d.reason for d in result.excluded)


def test_unknown_target_basis_produces_no_comparable_price(demo_config):
    result = build_comparable_set(
        target_offer=offer(price_basis="unknown", vat_regime="unknown"),
        target_vehicle=vehicle(),
        candidates=[candidate("b", "232000"), candidate("c", "235000"), candidate("d", "239000")],
        now=NOW,
        config=demo_config,
    )
    assert result.status == "insufficient_comparables"
    assert result.median is None


def test_gap_is_unavailable_without_a_median(demo_config):
    result = build([candidate("a", "228000")], demo_config)
    gap = asking_price_gap(target_offer=offer(), comparables=result)
    assert not gap.available
    assert gap.status == "insufficient_comparables"


def test_gap_explanation_refuses_the_word_profit(demo_config):
    result = build(
        [candidate("b", "232000"), candidate("c", "235000"), candidate("d", "239000")],
        demo_config,
    )
    gap = asking_price_gap(target_offer=offer(), comparables=result)
    assert gap.available
    assert "not profit" in gap.explanation


def test_no_automatic_monetary_adjustment_is_applied(demo_config):
    """Prices go into the median exactly as observed."""
    result = build(
        [
            candidate("b", "232000", mileage_km=25000),
            candidate("c", "235000", mileage_km=5000),
            candidate("d", "239000", mileage_km=18000),
        ],
        demo_config,
    )
    assert result.median == eur(235000)
    assert any("no automatic monetary adjustment" in note for note in result.notes)


# -- VAT and the new-vehicle indicator ----------------------------------


@pytest.mark.parametrize(
    "left,right,expected",
    [
        (("net", "standard"), ("net", "standard"), True),
        (("gross", "standard"), ("gross", "standard"), True),
        (("net", "standard"), ("gross", "standard"), False),
        (("net", "standard"), ("net", "margin"), False),
        (("net", "unknown"), ("net", "standard"), False),
        (("unknown", "standard"), ("net", "standard"), False),
        (("net", "other"), ("net", "other"), False),
    ],
)
def test_basis_compatibility(left, right, expected):
    assert basis_compatible(left[0], left[1], right[0], right[1]).compatible is expected


def test_new_vehicle_indicator_either_limb_is_enough():
    recent = new_vehicle_indicator(
        first_registration=NOW - timedelta(days=30), mileage_km=20000, at=NOW
    )
    assert recent.indicated_new is True

    low_mileage = new_vehicle_indicator(
        first_registration=NOW - timedelta(days=400), mileage_km=4000, at=NOW
    )
    assert low_mileage.indicated_new is True

    neither = new_vehicle_indicator(
        first_registration=NOW - timedelta(days=400), mileage_km=40000, at=NOW
    )
    assert neither.indicated_new is False


def test_new_vehicle_indicator_is_undeterminable_when_a_limb_is_unknown():
    result = new_vehicle_indicator(first_registration=None, mileage_km=40000, at=NOW)
    assert result.indicated_new is None
    assert not result.determinable
