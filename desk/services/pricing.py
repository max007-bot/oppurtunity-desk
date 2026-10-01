"""Comparable prices and deal scenarios.

Three different financial outputs are produced and never conflated:

1. an observed asking-price gap, which is an investigation signal;
2. a scenario contribution before unmodelled overhead, conditional on its inputs;
3. a cash requirement and timing, which can differ from the final cost.

Any missing required input returns ``Incomplete`` rather than a zero-cost profit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from ..clock import is_stale
from ..config import Config, PriceBasis, PriceEvidenceType, ScenarioStatus, VatRegime
from ..money import BASE_CURRENCY, Money, median as money_median
from ..repositories.supply import Offer, Vehicle
from .normalization import basis_compatible, variant_designation

RULE_VERSION = "pricing-2026-09-27"

# Price evidence that may be compared with a retail asking price.
RETAIL_EVIDENCE = (PriceEvidenceType.RETAIL_ASKING.value,)
SUPPLY_EVIDENCE = (PriceEvidenceType.SUPPLY_ASKING.value,)


# -- comparables ---------------------------------------------------------


@dataclass
class ComparableCandidate:
    """One candidate observation considered for the comparable set."""

    offer_id: str
    observation_id: str
    price: Money
    observed_at: datetime
    # The source's own record id. Used as the deduplication tie-breaker because it
    # is stable across re-seedings, unlike an internal identifier.
    external_record_id: str = ""
    vehicle_id: str | None = None
    vin: str | None = None
    vin_verified: bool = False
    model_family: str | None = None
    variant: str | None = None
    generation: str | None = None
    powertrain: str | None = None
    steering: str | None = None
    seats: int | None = None
    mileage_km: int | None = None
    first_registration: datetime | None = None
    location_country: str | None = None
    price_basis: str = PriceBasis.UNKNOWN.value
    vat_regime: str = VatRegime.UNKNOWN.value
    price_evidence_type: str = PriceEvidenceType.RETAIL_ASKING.value
    seller_name: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ComparableCandidate":
        return cls(
            offer_id=row["offer_id_ref"],
            observation_id=row["id"],
            external_record_id=row["external_record_id"] or "",
            price=Money(row["price_minor"], row["price_currency"]),
            observed_at=_parse(row["observed_at"]),
            vehicle_id=row["vehicle_id"],
            vin=row["vin"],
            vin_verified=bool(row["vin_verified"]),
            model_family=row["model_family"],
            variant=row["variant"],
            generation=row["generation"],
            powertrain=row["powertrain"] or row["vehicle_powertrain"],
            steering=row["steering"],
            seats=row["seats"] if row["seats"] is not None else row["vehicle_seats"],
            mileage_km=row["mileage_km"] if row["mileage_km"] is not None else row["vehicle_mileage"],
            first_registration=_parse(row["first_registration"]),
            location_country=row["location_country"],
            price_basis=row["price_basis"],
            vat_regime=row["vat_regime"],
            price_evidence_type=row["price_evidence_type"],
            seller_name=row["seller_name"],
        )


@dataclass
class ComparableDecision:
    """One candidate's fate, with everything that changed its figure.

    ``price_used`` is what entered the statistic. It differs from the observed
    price only when a dated conversion or a labelled analyst adjustment applied,
    and both are recorded here so the change is never invisible.
    """

    candidate: ComparableCandidate
    included: bool
    reason: str
    conversion: Any = None  # fx.Conversion, kept untyped to avoid a cycle
    adjustment: "Adjustment | None" = None

    @property
    def observed_price(self) -> Money:
        return self.candidate.price

    @property
    def converted_price(self) -> Money:
        return self.conversion.converted if self.conversion is not None else self.candidate.price

    @property
    def price_used(self) -> Money:
        base = self.converted_price
        if self.adjustment is None:
            return base
        return base + self.adjustment.amount

    @property
    def provenance(self) -> list[str]:
        """Human-readable trail from the observed price to the figure used."""
        trail: list[str] = []
        if self.conversion is not None:
            trail.append(self.conversion.assumption)
        if self.adjustment is not None:
            trail.append(self.adjustment.describe(self.converted_price))
        return trail


@dataclass(frozen=True)
class Adjustment:
    """An analyst's reasoned monetary adjustment to one comparable.

    Always an assumption. It is applied only when someone has entered it with a
    reason, and its effect on the median is reported separately.
    """

    comparable_offer_id: str
    amount: Money
    reason: str
    created_by: str

    def describe(self, base: Money) -> str:
        direction = "increased" if self.amount.minor_units > 0 else "reduced"
        return (
            f"analyst assumption by {self.created_by}: {base.format()} {direction} by "
            f"{abs(self.amount.decimal):,.2f} {self.amount.currency} to "
            f"{(base + self.amount).format()} because {self.reason}"
        )


@dataclass
class ComparableFilters:
    """Analyst-selected tolerances. No automatic monetary adjustments are applied."""

    mileage_tolerance_km: int | None = 20_000
    registration_tolerance_days: int | None = 730
    require_same_generation: bool = True
    # A generation is not a variant. Seven cars can share the W223 body and split
    # between an S 450 and an S 580 with fifty thousand euros between them, so a
    # median taken across both describes no car anyone can buy.
    require_same_variant: bool = True
    require_same_powertrain: bool = True
    require_same_steering: bool = True
    require_same_seats: bool = False
    countries: tuple[str, ...] | None = None
    max_observation_age_days: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mileage_tolerance_km": self.mileage_tolerance_km,
            "registration_tolerance_days": self.registration_tolerance_days,
            "require_same_generation": self.require_same_generation,
            "require_same_variant": self.require_same_variant,
            "require_same_powertrain": self.require_same_powertrain,
            "require_same_steering": self.require_same_steering,
            "require_same_seats": self.require_same_seats,
            "countries": list(self.countries) if self.countries else None,
            "max_observation_age_days": self.max_observation_age_days,
        }


@dataclass
class ComparableSetResult:
    target_offer_id: str
    decisions: list[ComparableDecision]
    status: str  # median_available | insufficient_comparables
    tax_basis: str
    vat_regime: str
    median: Money | None = None
    low: Money | None = None
    high: Money | None = None
    distinct_vehicles: int = 0
    observation_dates: list[datetime] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    rule_version: str = RULE_VERSION
    # The median the same set would have produced with no analyst adjustment, so
    # the effect of the assumptions is always visible next to the result.
    median_before_adjustments: Money | None = None
    assumptions: list[str] = field(default_factory=list)

    @property
    def included(self) -> list[ComparableDecision]:
        return [d for d in self.decisions if d.included]

    @property
    def adjusted(self) -> list[ComparableDecision]:
        return [d for d in self.included if d.adjustment is not None]

    @property
    def converted(self) -> list[ComparableDecision]:
        return [d for d in self.included if d.conversion is not None]

    @property
    def adjustment_effect(self) -> Money | None:
        """How much the analyst's assumptions moved the median."""
        if self.median is None or self.median_before_adjustments is None:
            return None
        if self.median.currency != self.median_before_adjustments.currency:
            return None
        return self.median - self.median_before_adjustments

    @property
    def excluded(self) -> list[ComparableDecision]:
        return [d for d in self.decisions if not d.included]

    @property
    def has_median(self) -> bool:
        return self.status == "median_available" and self.median is not None

    def summary(self) -> str:
        if not self.has_median:
            return (
                f"Insufficient comparables: {self.distinct_vehicles} distinct vehicle(s) passed "
                f"the filters. The individual observations are listed instead of a median."
            )
        oldest = min(self.observation_dates).date().isoformat()
        newest = max(self.observation_dates).date().isoformat()
        text = (
            f"Median {self.median.format()} from {self.distinct_vehicles} distinct vehicles, "
            f"range {self.low.format()} to {self.high.format()}, observed {oldest} to {newest}, "
            f"all on a {self.tax_basis}/{self.vat_regime} basis."
        )
        if self.converted:
            text += f" {len(self.converted)} of them were converted at a dated rate."
        if self.adjusted:
            effect = self.adjustment_effect
            text += (
                f" {len(self.adjusted)} carry an analyst adjustment, moving the median by "
                f"{effect.format() if effect else 'an unknown amount'} from "
                f"{self.median_before_adjustments.format()}."
            )
        return text


def build_comparable_set(
    *,
    target_offer: Offer,
    target_vehicle: Vehicle | None,
    candidates: Sequence[ComparableCandidate],
    now: datetime,
    config: Config,
    filters: ComparableFilters | None = None,
    evidence_types: tuple[str, ...] = RETAIL_EVIDENCE,
    rate_book: Any = None,
    adjustments: Mapping[str, Adjustment] | None = None,
) -> ComparableSetResult:
    """Select genuinely similar cars and report the median only when justified.

    Every exclusion carries its reason so a user can see why "same badge" was not
    enough. Same-VIN offers are deduplicated before any statistic is computed.

    ``rate_book`` is an optional ``fx.RateBook``. Without one, a differently
    priced currency is excluded, as before. With one, a candidate is converted
    only when a dated rate covers it, and the conversion is recorded on the
    decision so it is visible wherever the figure is shown.

    ``adjustments`` maps a comparable's offer id to an analyst's reasoned
    adjustment. Nothing is ever adjusted automatically.
    """
    filters = filters or ComparableFilters()
    adjustments = adjustments or {}
    decisions: list[ComparableDecision] = []
    notes: list[str] = []
    assumptions: list[str] = []

    basis = target_offer.price_basis
    regime = target_offer.vat_regime
    if PriceBasis(basis) is PriceBasis.UNKNOWN or VatRegime(regime) is VatRegime.UNKNOWN:
        notes.append(
            "the target offer's price basis or VAT regime is unknown, so no comparable price "
            "can be computed automatically"
        )
        return ComparableSetResult(
            target_offer_id=target_offer.id,
            decisions=[
                ComparableDecision(
                    c, False, "target offer has an unknown price basis or VAT regime"
                )
                for c in candidates
            ],
            status="insufficient_comparables",
            tax_basis=basis,
            vat_regime=regime,
            filters=filters.as_dict(),
            notes=notes,
        )

    seen_vehicle_keys: dict[str, str] = {}

    # Deduplication keeps the first candidate it meets for a vehicle identity, so
    # the order has to be fully determined rather than left to the database. Newest
    # observation wins; an exact tie is broken by the source's own record id, which
    # is stable across re-seedings, so the same inputs always give the same median.
    ordered = sorted(
        candidates,
        key=lambda c: (-c.observed_at.timestamp(), c.external_record_id, c.offer_id),
    )

    target_currency = target_offer.price.currency if target_offer.price else BASE_CURRENCY

    for candidate in ordered:
        reason = _exclusion_reason(
            candidate=candidate,
            target_offer=target_offer,
            target_vehicle=target_vehicle,
            filters=filters,
            evidence_types=evidence_types,
            now=now,
            config=config,
        )
        if reason is not None:
            decisions.append(ComparableDecision(candidate, False, reason))
            continue

        # A different currency is only ever brought in by a dated rate someone
        # entered. Without one it stays excluded rather than converted at a guess.
        conversion = None
        if candidate.price.currency != target_currency:
            conversion = rate_book.convert(candidate.price, target_currency) if rate_book else None
            if conversion is None:
                decisions.append(
                    ComparableDecision(
                        candidate,
                        False,
                        _missing_rate_reason(
                            candidate.price.currency, target_currency, rate_book
                        ),
                    )
                )
                continue

        key = _identity_key(candidate)
        if key in seen_vehicle_keys:
            decisions.append(
                ComparableDecision(
                    candidate,
                    False,
                    f"same vehicle identity as offer {seen_vehicle_keys[key]}, which carries "
                    f"the newer observation; counted once so it is not treated as independent "
                    f"inventory",
                    conversion=conversion,
                )
            )
            continue
        seen_vehicle_keys[key] = candidate.offer_id

        adjustment = adjustments.get(candidate.offer_id)
        if adjustment is not None and adjustment.amount.currency != target_currency:
            # An adjustment in the wrong currency is ignored rather than guessed at.
            notes.append(
                f"an adjustment on offer {candidate.external_record_id or candidate.offer_id} is "
                f"in {adjustment.amount.currency} but the comparison is in {target_currency}; it "
                f"was not applied"
            )
            adjustment = None

        decision = ComparableDecision(
            candidate,
            True,
            f"{candidate.model_family} on a {candidate.price_basis}/{candidate.vat_regime} "
            f"basis, observed {candidate.observed_at.date().isoformat()}",
            conversion=conversion,
            adjustment=adjustment,
        )
        assumptions.extend(decision.provenance)
        decisions.append(decision)

    included_decisions = [d for d in decisions if d.included]
    included = [d.candidate for d in included_decisions]
    distinct = len(included)

    if distinct < config.min_comparables_for_median:
        notes.append(
            f"a median needs at least {config.min_comparables_for_median} sufficiently "
            f"comparable distinct vehicles; this is a conservative prototype setting, not a "
            f"valuation standard"
        )
        return ComparableSetResult(
            target_offer_id=target_offer.id,
            decisions=decisions,
            status="insufficient_comparables",
            tax_basis=basis,
            vat_regime=regime,
            distinct_vehicles=distinct,
            observation_dates=[c.observed_at for c in included],
            filters=filters.as_dict(),
            notes=notes,
        )

    prices = [d.price_used for d in included_decisions]
    unadjusted = [d.converted_price for d in included_decisions]
    currencies = {price.currency for price in prices}
    if len(currencies) > 1:
        notes.append(
            f"mixed currencies {sorted(currencies)} cannot be combined without a dated "
            f"conversion rule; the observations are shown individually"
        )
        return ComparableSetResult(
            target_offer_id=target_offer.id,
            decisions=decisions,
            status="insufficient_comparables",
            tax_basis=basis,
            vat_regime=regime,
            distinct_vehicles=distinct,
            observation_dates=[c.observed_at for c in included],
            filters=filters.as_dict(),
            notes=notes,
        )

    adjusted_decisions = [d for d in included_decisions if d.adjustment is not None]
    if adjusted_decisions:
        notes.append(
            f"{len(adjusted_decisions)} comparable(s) carry an analyst adjustment. These are "
            f"assumptions entered by a person, not automatic corrections, and the median before "
            f"them is shown alongside."
        )
    else:
        notes.append(
            "no automatic monetary adjustment has been applied for options or mileage; any "
            "analyst adjustment is recorded as a labelled assumption"
        )

    converted_decisions = [d for d in included_decisions if d.conversion is not None]
    if converted_decisions:
        notes.append(
            f"{len(converted_decisions)} comparable(s) were converted at a dated rate. A rate "
            f"makes prices arithmetically comparable; it does not account for why a car is priced "
            f"differently in another market."
        )

    return ComparableSetResult(
        target_offer_id=target_offer.id,
        decisions=decisions,
        status="median_available",
        tax_basis=basis,
        vat_regime=regime,
        median=money_median(prices),
        low=min(prices),
        high=max(prices),
        distinct_vehicles=distinct,
        observation_dates=[c.observed_at for c in included],
        filters=filters.as_dict(),
        notes=notes,
        median_before_adjustments=money_median(unadjusted),
        assumptions=assumptions,
    )


def _missing_rate_reason(from_currency: str, to_currency: str, rate_book: Any) -> str:
    """Say precisely why a differently priced car could not be brought in."""
    if rate_book is None:
        return (
            f"priced in {from_currency} while the target is in {to_currency}; excluded until a "
            f"dated FX rate and direction are entered"
        )
    from .fx import missing_rate_message

    return missing_rate_message(
        from_currency, to_currency, max_age_days=getattr(rate_book, "_max_age_days", None)
    )


def _identity_key(candidate: ComparableCandidate) -> str:
    """A verified VIN is the identity; otherwise the offer stands alone."""
    if candidate.vin and candidate.vin_verified:
        return f"vin:{candidate.vin}"
    if candidate.vehicle_id:
        return f"veh:{candidate.vehicle_id}"
    return f"ofr:{candidate.offer_id}"


def _exclusion_reason(
    *,
    candidate: ComparableCandidate,
    target_offer: Offer,
    target_vehicle: Vehicle | None,
    filters: ComparableFilters,
    evidence_types: tuple[str, ...],
    now: datetime,
    config: Config,
) -> str | None:
    if candidate.offer_id == target_offer.id:
        return "this is the target offer itself"

    if candidate.price_evidence_type not in evidence_types:
        return (
            f"price evidence type is {candidate.price_evidence_type}; supply quotes, "
            f"dealer-to-dealer bids, retail asking prices and completed transactions are not "
            f"blended into one market price"
        )

    compatibility = basis_compatible(
        target_offer.price_basis, target_offer.vat_regime, candidate.price_basis, candidate.vat_regime
    )
    if not compatibility.compatible:
        return f"tax basis is not comparable: {compatibility.reason}"

    # Currency is deliberately not checked here. The caller handles it, because a
    # dated rate may be able to bring the candidate in, and the reason given has
    # to say which of those two situations applies.

    if target_vehicle is not None:
        if candidate.model_family != target_vehicle.model_family:
            return (
                f"different model family ({candidate.model_family} versus "
                f"{target_vehicle.model_family}); the same badge is not enough"
            )
        if (
            filters.require_same_generation
            and target_vehicle.generation
            and candidate.generation
            and candidate.generation != target_vehicle.generation
        ):
            return (
                f"different generation ({candidate.generation} versus "
                f"{target_vehicle.generation})"
            )
        target_designation = variant_designation(target_vehicle.variant)
        candidate_designation = variant_designation(candidate.variant)
        if (
            filters.require_same_variant
            and target_designation
            and candidate_designation
            and target_designation != candidate_designation
        ):
            return (
                f"different variant ({candidate.variant} versus {target_vehicle.variant}); "
                f"the same generation is not the same car"
            )
        if filters.require_same_powertrain and target_vehicle.powertrain and candidate.powertrain:
            if candidate.powertrain != target_vehicle.powertrain:
                return (
                    f"different powertrain ({candidate.powertrain} versus "
                    f"{target_vehicle.powertrain})"
                )
        if filters.require_same_steering and target_vehicle.steering not in (None, "unknown"):
            if candidate.steering not in (None, "unknown") and candidate.steering != target_vehicle.steering:
                return f"different steering side ({candidate.steering} versus {target_vehicle.steering})"
        if filters.require_same_seats and target_vehicle.seats and candidate.seats:
            if candidate.seats != target_vehicle.seats:
                return f"different seat count ({candidate.seats} versus {target_vehicle.seats})"
        if (
            filters.mileage_tolerance_km is not None
            and target_vehicle.mileage_km is not None
            and candidate.mileage_km is not None
        ):
            gap = abs(candidate.mileage_km - target_vehicle.mileage_km)
            if gap > filters.mileage_tolerance_km:
                return (
                    f"mileage differs by {gap:,} km, beyond the selected "
                    f"{filters.mileage_tolerance_km:,} km tolerance"
                )
        if (
            filters.registration_tolerance_days is not None
            and target_vehicle.first_registration is not None
            and candidate.first_registration is not None
        ):
            gap_days = abs((candidate.first_registration - target_vehicle.first_registration).days)
            if gap_days > filters.registration_tolerance_days:
                return (
                    f"first registration differs by {gap_days} days, beyond the selected "
                    f"{filters.registration_tolerance_days}-day tolerance"
                )

    if filters.countries and (candidate.location_country or "").upper() not in filters.countries:
        return f"location {candidate.location_country} is outside the selected countries"

    max_age = (
        filters.max_observation_age_days
        if filters.max_observation_age_days is not None
        else config.freshness.comparable_days
    )
    if is_stale(now, candidate.observed_at, days=max_age):
        return (
            f"observed {candidate.observed_at.date().isoformat()}, older than the "
            f"{max_age:g}-day freshness window"
        )
    return None


# -- asking-price gap ----------------------------------------------------


@dataclass
class AskingGap:
    """A research signal. It is not profit and must never be labelled as such."""

    gap: Money | None
    status: str
    explanation: str
    comparable_median: Money | None = None
    supply_price: Money | None = None

    @property
    def available(self) -> bool:
        return self.gap is not None


def asking_price_gap(
    *, target_offer: Offer, comparables: ComparableSetResult
) -> AskingGap:
    if target_offer.price is None:
        return AskingGap(None, "incomplete", "the target offer has no recorded price")
    if not comparables.has_median:
        return AskingGap(
            None,
            "insufficient_comparables",
            comparables.summary(),
            supply_price=target_offer.price,
        )
    if comparables.median.currency != target_offer.price.currency:
        return AskingGap(
            None,
            "incomplete",
            "comparable median and supply price are in different currencies",
            comparable_median=comparables.median,
            supply_price=target_offer.price,
        )
    gap = comparables.median - target_offer.price
    return AskingGap(
        gap,
        "available",
        f"compatible comparable asking median {comparables.median.format()} minus observed "
        f"supply asking {target_offer.price.format()}. This is an investigation signal, not "
        f"profit: it excludes every cost and assumes a sale that has not happened.",
        comparable_median=comparables.median,
        supply_price=target_offer.price,
    )


# -- scenario ------------------------------------------------------------


@dataclass
class CostLine:
    """One cost line.

    ``amount is None`` means unknown and blocks completion. An amount of zero with
    ``confirmed_zero`` set means someone checked and it really is nothing.
    """

    label: str
    amount: Money | None = None
    tax_treatment: str = "non_recoverable"
    confirmed_zero: bool = False
    already_in_purchase_price: bool = False
    required: bool = True
    paid_at: datetime | None = None
    refunded_at: datetime | None = None
    notes: str | None = None

    @property
    def is_known(self) -> bool:
        return self.amount is not None or self.confirmed_zero

    @property
    def effective(self) -> Money:
        if self.confirmed_zero and self.amount is None:
            return Money.zero()
        return self.amount if self.amount is not None else Money.zero()

    @property
    def counts_toward_cost(self) -> bool:
        """Recoverable tax and costs already inside the purchase price are excluded.

        Excluding the latter is what prevents double counting.
        """
        return self.tax_treatment == "non_recoverable" and not self.already_in_purchase_price

    @property
    def counts_toward_cash(self) -> bool:
        """Cash includes recoverable tax that is temporarily tied up."""
        return not self.already_in_purchase_price and self.tax_treatment != "outside_scope"

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "amount_minor": None if self.amount is None else self.amount.minor_units,
            "currency": (self.amount or Money.zero()).currency,
            "is_confirmed_zero": self.confirmed_zero,
            "tax_treatment": self.tax_treatment,
            "already_in_purchase_price": self.already_in_purchase_price,
            "required": self.required,
            "paid_at": None if self.paid_at is None else self.paid_at.isoformat(),
            "refunded_at": None if self.refunded_at is None else self.refunded_at.isoformat(),
            "notes": self.notes,
        }


@dataclass
class ScenarioInput:
    """Everything a scenario needs, with each price's evidence type declared."""

    acquisition: Money | None = None
    acquisition_basis: str = PriceBasis.UNKNOWN.value
    acquisition_vat_regime: str = VatRegime.UNKNOWN.value
    acquisition_evidence_type: str = PriceEvidenceType.SUPPLY_ASKING.value

    # Either state the sale directly...
    sale: Money | None = None
    sale_basis: str = PriceBasis.UNKNOWN.value
    sale_vat_regime: str = VatRegime.UNKNOWN.value
    sale_evidence_type: str | None = None

    # ...or derive a hypothetical trade price from the downstream retail picture.
    retail_assumption: Money | None = None
    dealer_downstream_cost: Money | None = None
    dealer_required_contribution: Money | None = None

    costs: list[CostLine] = field(default_factory=list)
    comparable_median: Money | None = None
    currency: str = BASE_CURRENCY
    market: str | None = None
    assumptions: list[str] = field(default_factory=list)
    commission_rule: str | None = None


@dataclass
class ScenarioResult:
    status: ScenarioStatus
    currency: str
    included_costs: Money | None = None
    total_outlay: Money | None = None
    hypothetical_trade_price: Money | None = None
    contribution: Money | None = None
    asking_gap: Money | None = None
    cash_outflow: Money | None = None
    recoverable_tax: Money | None = None
    cash_schedule_status: str = "incomplete"
    missing_inputs: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    sale_evidence_type: str | None = None
    expected_commission: Money | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.status is ScenarioStatus.COMPLETE

    def sale_evidence_label(self) -> str:
        labels = {
            PriceEvidenceType.DEALER_BID.value: "an actual bid from the buyer",
            PriceEvidenceType.BUYER_BUDGET.value: "the buyer's stated budget, which is not a commitment",
            PriceEvidenceType.ANALYST_ASSUMPTION.value: "an analyst assumption, not a bid",
            PriceEvidenceType.RETAIL_ASKING.value: "a retail asking price, not an agreed sale",
            PriceEvidenceType.COMPLETED_TRANSACTION.value: "evidence of a completed transaction",
        }
        if self.sale_evidence_type is None:
            return "no sale evidence recorded"
        return labels.get(self.sale_evidence_type, self.sale_evidence_type)


def compute_scenario(data: ScenarioInput) -> ScenarioResult:
    """Compute the scenario, returning Incomplete when a required input is missing."""
    currency = data.currency
    missing: list[str] = []
    notes: list[str] = []
    assumptions = list(data.assumptions)

    if data.acquisition is None:
        missing.append("acquisition price")
    if PriceBasis(data.acquisition_basis) is PriceBasis.UNKNOWN:
        missing.append("acquisition price basis (net or gross)")
    if VatRegime(data.acquisition_vat_regime) is VatRegime.UNKNOWN:
        missing.append("acquisition VAT regime")

    unknown_costs = [line.label for line in data.costs if line.required and not line.is_known]
    missing.extend(f"cost: {label}" for label in unknown_costs)

    # Derive the hypothetical trade price when the downstream picture is given.
    trade_price: Money | None = None
    if (
        data.retail_assumption is not None
        and data.dealer_downstream_cost is not None
        and data.dealer_required_contribution is not None
    ):
        trade_price = (
            data.retail_assumption - data.dealer_downstream_cost - data.dealer_required_contribution
        )
        assumptions.append(
            f"hypothetical maximum trade purchase price {trade_price.format()} = assumed retail "
            f"{data.retail_assumption.format()} minus the dealer's own downstream costs "
            f"{data.dealer_downstream_cost.format()} minus its required contribution "
            f"{data.dealer_required_contribution.format()}. This is not a real bid."
        )

    sale = data.sale if data.sale is not None else trade_price
    sale_evidence = data.sale_evidence_type
    if data.sale is None and trade_price is not None:
        sale_evidence = PriceEvidenceType.ANALYST_ASSUMPTION.value
    if sale is None:
        missing.append("proposed sale price, buyer bid or retail assumption set")
    elif data.sale is not None and PriceBasis(data.sale_basis) is PriceBasis.UNKNOWN:
        missing.append("sale price basis (net or gross)")
    elif data.sale is not None and VatRegime(data.sale_vat_regime) is VatRegime.UNKNOWN:
        missing.append("sale VAT regime")

    if data.sale is not None and data.acquisition is not None:
        compatibility = basis_compatible(
            data.acquisition_basis, data.acquisition_vat_regime, data.sale_basis, data.sale_vat_regime
        )
        if not compatibility.compatible:
            missing.append(f"compatible tax basis between purchase and sale ({compatibility.reason})")

    included_costs = _sum_costs(data.costs, currency, predicate=lambda line: line.counts_toward_cost)
    recoverable = _sum_costs(
        data.costs, currency, predicate=lambda line: line.tax_treatment == "recoverable"
    )
    cash_lines = _sum_costs(data.costs, currency, predicate=lambda line: line.counts_toward_cash)

    total_outlay: Money | None = None
    contribution: Money | None = None
    cash_outflow: Money | None = None

    if data.acquisition is not None and not unknown_costs:
        total_outlay = data.acquisition + included_costs
        cash_outflow = data.acquisition + cash_lines
        if sale is not None:
            contribution = sale - total_outlay

    asking_gap: Money | None = None
    if data.comparable_median is not None and data.acquisition is not None:
        if data.comparable_median.currency == data.acquisition.currency:
            asking_gap = data.comparable_median - data.acquisition

    # Cash timing: missing payment dates make the schedule incomplete, even when
    # the totals are computable.
    cash_relevant = [line for line in data.costs if line.counts_toward_cash and line.is_known]
    undated = [line.label for line in cash_relevant if line.paid_at is None]
    unrefunded = [
        line.label
        for line in data.costs
        if line.tax_treatment == "recoverable" and line.is_known and line.refunded_at is None
    ]
    cash_status = "complete"
    if cash_outflow is None:
        cash_status = "incomplete"
    elif undated:
        cash_status = "incomplete"
        notes.append(
            f"cash schedule incomplete: no payment date for {', '.join(undated)}"
        )
    elif unrefunded:
        cash_status = "incomplete"
        notes.append(
            f"cash schedule incomplete: recoverable tax on {', '.join(unrefunded)} has no "
            f"expected refund date, so the money tied up cannot be scheduled"
        )

    if recoverable.minor_units:
        notes.append(
            f"{recoverable.format()} of recoverable tax is a cash outflow before refund, not a "
            f"final cost"
        )

    status = ScenarioStatus.INCOMPLETE if missing else ScenarioStatus.COMPLETE
    if status is ScenarioStatus.COMPLETE:
        notes.append(
            "contribution is before unmodelled overhead, referral fees and any cost not "
            "listed above; a risk allowance is a provision, not proof the risk is covered"
        )

    return ScenarioResult(
        status=status,
        currency=currency,
        included_costs=included_costs if not unknown_costs else None,
        total_outlay=total_outlay,
        hypothetical_trade_price=trade_price,
        contribution=contribution,
        asking_gap=asking_gap,
        cash_outflow=cash_outflow,
        recoverable_tax=recoverable,
        cash_schedule_status=cash_status,
        missing_inputs=missing,
        assumptions=assumptions,
        sale_evidence_type=sale_evidence,
        expected_commission=_commission(data),
        notes=notes,
    )


def _sum_costs(costs: Iterable[CostLine], currency: str, *, predicate) -> Money:
    result = Money.zero(currency)
    for line in costs:
        if not predicate(line):
            continue
        if line.amount is None:
            continue
        result = result + line.amount
    return result


def _commission(data: ScenarioInput) -> Money | None:
    """Commission stays blank unless a real approved rule has been entered.

    No prototype income forecast is produced.
    """
    if not data.commission_rule:
        return None
    return None


def _parse(value: Any) -> datetime | None:
    from ..clock import from_iso

    if value is None or isinstance(value, datetime):
        return value
    return from_iso(str(value))


def hypothetical_trade_price(
    *, retail: Money, dealer_cost: Money, dealer_contribution: Money
) -> Money:
    """What a dealer could pay and still meet its own stated requirements."""
    return retail - dealer_cost - dealer_contribution


def sensitivity(data: ScenarioInput, deltas: Sequence[Money]) -> list[tuple[Money, ScenarioResult]]:
    """Recompute the scenario across retail-outcome movements.

    Used to show the downside explicitly rather than presenting a single number.
    """
    out: list[tuple[Money, ScenarioResult]] = []
    if data.retail_assumption is None:
        return out
    for delta in deltas:
        shifted = ScenarioInput(
            **{
                **data.__dict__,
                "retail_assumption": data.retail_assumption + delta,
                "assumptions": list(data.assumptions),
                "costs": list(data.costs),
            }
        )
        out.append((delta, compute_scenario(shifted)))
    return out


def decimal_ratio(numerator: Money, denominator: Money) -> Decimal | None:
    if denominator.minor_units == 0:
        return None
    return Decimal(numerator.minor_units) / Decimal(denominator.minor_units)
