"""Buyer matching.

Hard requirements are applied first and fail closed: a known conflict is a
rejection, a missing required fact is "needs verification", and only a complete
set of satisfied mandatory facts is a specification fit. A specification fit still
proves nothing about whether the buyer will buy or whether the economics work.

Contact availability and contact permission are deliberately absent from the
score. They are separate gates applied in ``outreach``, so a company that happens
to have a published phone number can never outrank a genuine requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Iterable

from ..clock import is_stale
from ..config import Config, FitStatus, OfferStatus, StockKind
from ..money import Money
from ..repositories.companies import BuyerBrief, Company
from ..repositories.supply import Offer, Vehicle
from .normalization import basis_compatible, slugify

RULE_VERSION = "match-2026-09-27"

# Weights are product choices to test, not empirical predictors of closing.
WEIGHT_SPECIFICATION = 40
WEIGHT_CONFIRMED_REQUIREMENT = 25
WEIGHT_TIMING = 15
WEIGHT_REVIEWED_ECONOMICS = 10
WEIGHT_FRESHNESS = 10


class RuleOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass
class RuleResult:
    name: str
    outcome: RuleOutcome
    reason: str
    evidence_refs: list[str] = field(default_factory=list)

    def render(self) -> str:
        return f"{self.name}: {self.reason}"


@dataclass
class MatchResult:
    """A fully explained match. Every rule stays visible, including the failures."""

    offer_id: str
    company_id: str
    brief_id: str | None
    fit_status: FitStatus
    rules: list[RuleResult]
    score: int
    score_breakdown: dict[str, int]
    notes: list[str] = field(default_factory=list)
    supply_confirmed_at: datetime | None = None
    expires_at: datetime | None = None
    rule_version: str = RULE_VERSION

    @property
    def passed(self) -> list[RuleResult]:
        return [r for r in self.rules if r.outcome is RuleOutcome.PASS]

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.rules if r.outcome is RuleOutcome.FAIL]

    @property
    def unknown(self) -> list[RuleResult]:
        return [r for r in self.rules if r.outcome is RuleOutcome.UNKNOWN]

    @property
    def contact_ready_eligible(self) -> bool:
        """Only a clean specification fit may ever reach the contact-ready queue.

        Anything with an open failure or an unverified requirement stays research.
        """
        return self.fit_status is FitStatus.SPECIFICATION_FIT and not self.failed and not self.unknown

    def explain(self) -> list[str]:
        return [rule.render() for rule in self.rules]


# -- hard requirement evaluation ----------------------------------------


def evaluate_requirements(
    *,
    offer: Offer,
    vehicle: Vehicle | None,
    brief: BuyerBrief,
    now: datetime,
    config: Config,
    rate_book: Any = None,
) -> list[RuleResult]:
    """Apply every hard requirement, returning one result per rule."""
    rules: list[RuleResult] = []
    required: dict[str, Any] = dict(brief.required_specs or {})

    rules.append(_rule_model(offer, vehicle, brief))
    rules.append(_rule_variant(vehicle, brief, required))
    rules.append(_rule_steering(vehicle, required))
    rules.append(_rule_seats(vehicle, required))
    rules.append(_rule_powertrain(vehicle, required))
    rules.append(_rule_mileage(vehicle, required))
    rules.append(_rule_registration_age(vehicle, required, now))
    rules.extend(_rules_must_have_options(vehicle, required))
    rules.append(_rule_stock(offer, required))
    rules.append(_rule_delivery(offer, brief, now))
    rules.append(_rule_quantity(brief))
    rules.append(_rule_basis(offer, brief))
    rules.append(_rule_destination(offer, brief, required))
    rules.append(_rule_budget(offer, brief, rate_book))
    rules.append(_rule_supply_freshness(offer, now, config))
    return [rule for rule in rules if rule is not None]


def _rule_model(offer: Offer, vehicle: Vehicle | None, brief: BuyerBrief) -> RuleResult:
    if vehicle is None:
        return RuleResult(
            "model family",
            RuleOutcome.UNKNOWN,
            "the offer has no reviewed vehicle identity yet",
        )
    if vehicle.identity_review_status == "review_required":
        return RuleResult(
            "model family",
            RuleOutcome.UNKNOWN,
            f"vehicle identity is still awaiting review "
            f"(recorded as {vehicle.model_family!r})",
        )
    if vehicle.model_family != brief.model_family:
        return RuleResult(
            "model family",
            RuleOutcome.FAIL,
            f"buyer requires {brief.model_family}, the car is {vehicle.model_family}",
        )
    return RuleResult(
        "model family", RuleOutcome.PASS, f"both are {vehicle.model_family}"
    )


def _rule_variant(
    vehicle: Vehicle | None, brief: BuyerBrief, required: dict[str, Any]
) -> RuleResult | None:
    wanted = required.get("variant") or brief.variant
    if not wanted:
        return None
    if vehicle is None or not vehicle.variant:
        return RuleResult(
            "variant", RuleOutcome.UNKNOWN, f"buyer requires variant {wanted!r}, the car's is unknown"
        )
    if slugify(vehicle.variant) != slugify(wanted):
        return RuleResult(
            "variant",
            RuleOutcome.FAIL,
            f"buyer requires {wanted!r}, the car is {vehicle.variant!r}",
        )
    return RuleResult("variant", RuleOutcome.PASS, f"both are {wanted!r}")


def _rule_steering(vehicle: Vehicle | None, required: dict[str, Any]) -> RuleResult | None:
    wanted = required.get("steering")
    if not wanted:
        return None
    actual = (vehicle.steering if vehicle else None) or "unknown"
    if actual == "unknown":
        return RuleResult(
            "steering side", RuleOutcome.UNKNOWN, f"buyer requires {wanted}, the car's side is unknown"
        )
    if actual != wanted:
        return RuleResult(
            "steering side", RuleOutcome.FAIL, f"buyer requires {wanted}, the car is {actual}"
        )
    return RuleResult("steering side", RuleOutcome.PASS, f"both are {wanted}")


def _rule_seats(vehicle: Vehicle | None, required: dict[str, Any]) -> RuleResult | None:
    wanted = required.get("seats") or required.get("homologated_seats")
    minimum = required.get("min_seats")
    if wanted is None and minimum is None:
        return None
    actual = vehicle.seats if vehicle else None
    if actual is None:
        target = wanted if wanted is not None else f"at least {minimum}"
        return RuleResult(
            "homologated seats",
            RuleOutcome.UNKNOWN,
            f"buyer requires {target} seats, the homologated count is unknown",
        )
    if wanted is not None and actual != int(wanted):
        return RuleResult(
            "homologated seats",
            RuleOutcome.FAIL,
            f"buyer requires {wanted} homologated seats, the car has {actual}",
        )
    if minimum is not None and actual < int(minimum):
        return RuleResult(
            "homologated seats",
            RuleOutcome.FAIL,
            f"buyer requires at least {minimum} seats, the car has {actual}",
        )
    return RuleResult("homologated seats", RuleOutcome.PASS, f"the car has {actual} seats")


def _rule_powertrain(vehicle: Vehicle | None, required: dict[str, Any]) -> RuleResult | None:
    wanted = required.get("powertrain")
    if not wanted:
        return None
    actual = vehicle.powertrain if vehicle else None
    if not actual:
        return RuleResult(
            "powertrain", RuleOutcome.UNKNOWN, f"buyer requires {wanted}, the car's is unknown"
        )
    if slugify(actual) != slugify(wanted):
        return RuleResult(
            "powertrain", RuleOutcome.FAIL, f"buyer requires {wanted}, the car is {actual}"
        )
    return RuleResult("powertrain", RuleOutcome.PASS, f"both are {wanted}")


def _rule_mileage(vehicle: Vehicle | None, required: dict[str, Any]) -> RuleResult | None:
    limit = required.get("max_mileage_km")
    if limit is None:
        return None
    actual = vehicle.mileage_km if vehicle else None
    if actual is None:
        return RuleResult(
            "maximum mileage",
            RuleOutcome.UNKNOWN,
            f"buyer accepts up to {int(limit):,} km, the car's mileage is unknown",
        )
    if actual > int(limit):
        return RuleResult(
            "maximum mileage",
            RuleOutcome.FAIL,
            f"buyer accepts up to {int(limit):,} km, the car has {actual:,} km",
        )
    return RuleResult(
        "maximum mileage", RuleOutcome.PASS, f"{actual:,} km is within {int(limit):,} km"
    )


def _rule_registration_age(
    vehicle: Vehicle | None, required: dict[str, Any], now: datetime
) -> RuleResult | None:
    months = required.get("max_registration_age_months")
    if months is None:
        return None
    registered = vehicle.first_registration if vehicle else None
    if registered is None:
        return RuleResult(
            "registration age",
            RuleOutcome.UNKNOWN,
            f"buyer accepts up to {months} months, the first registration date is unknown",
        )
    limit = timedelta(days=int(months) * 30.4375)
    actual_days = (now - registered).days
    if (now - registered) > limit:
        return RuleResult(
            "registration age",
            RuleOutcome.FAIL,
            f"buyer accepts up to {months} months, the car was first registered "
            f"{registered.date().isoformat()} ({actual_days} days ago)",
        )
    return RuleResult(
        "registration age",
        RuleOutcome.PASS,
        f"first registered {registered.date().isoformat()}, within {months} months",
    )


def _rules_must_have_options(
    vehicle: Vehicle | None, required: dict[str, Any]
) -> list[RuleResult]:
    """One rule per must-have option.

    An option absent from the specification record is unknown, never assumed
    present: this is the rear-seat-option case from the acceptance table.
    """
    wanted: Iterable[str] = required.get("must_have_options") or []
    results: list[RuleResult] = []
    spec = (vehicle.specification if vehicle else {}) or {}
    for option in wanted:
        key = str(option)
        if key not in spec:
            results.append(
                RuleResult(
                    f"required option {key}",
                    RuleOutcome.UNKNOWN,
                    "not recorded in the reviewed specification; confirm with the seller "
                    "rather than assuming it is fitted",
                )
            )
            continue
        value = spec[key]
        if value in (False, "false", "no", 0, None, "unknown"):
            results.append(
                RuleResult(
                    f"required option {key}",
                    RuleOutcome.FAIL,
                    f"recorded as {value!r}, the buyer requires it",
                )
            )
        else:
            results.append(
                RuleResult(f"required option {key}", RuleOutcome.PASS, f"recorded as {value!r}")
            )
    return results


def _rule_stock(offer: Offer, required: dict[str, Any]) -> RuleResult | None:
    requirement = required.get("stock_requirement")
    if not requirement:
        return None
    if requirement == "physical_stock":
        if offer.is_allocation:
            return RuleResult(
                "stock requirement",
                RuleOutcome.FAIL,
                "buyer requires immediate physical stock; this offer is a build slot or "
                "allocation, so there is no current fulfilment",
            )
        if offer.stock_kind == StockKind.UNKNOWN.value:
            return RuleResult(
                "stock requirement",
                RuleOutcome.UNKNOWN,
                "buyer requires immediate physical stock; whether the car physically exists "
                "is not confirmed",
            )
        return RuleResult(
            "stock requirement", RuleOutcome.PASS, "recorded as physical stock"
        )
    return RuleResult(
        "stock requirement", RuleOutcome.PASS, f"buyer accepts {requirement}"
    )


def _rule_delivery(offer: Offer, brief: BuyerBrief, now: datetime) -> RuleResult | None:
    if brief.required_by is None:
        return None
    if offer.available_from is None:
        if offer.is_allocation:
            return RuleResult(
                "delivery date",
                RuleOutcome.UNKNOWN,
                f"buyer needs it by {brief.required_by.date().isoformat()}; this allocation has "
                f"no stated availability date",
            )
        return RuleResult(
            "delivery date",
            RuleOutcome.UNKNOWN,
            f"buyer needs it by {brief.required_by.date().isoformat()}; the offer states no "
            f"availability date",
        )
    if offer.available_from > brief.required_by:
        return RuleResult(
            "delivery date",
            RuleOutcome.FAIL,
            f"available from {offer.available_from.date().isoformat()}, buyer needs it by "
            f"{brief.required_by.date().isoformat()}",
        )
    return RuleResult(
        "delivery date",
        RuleOutcome.PASS,
        f"available {offer.available_from.date().isoformat()}, before "
        f"{brief.required_by.date().isoformat()}",
    )


def _rule_quantity(brief: BuyerBrief) -> RuleResult | None:
    if brief.quantity <= 1:
        return None
    return RuleResult(
        "quantity",
        RuleOutcome.UNKNOWN,
        f"the buyer requires {brief.quantity} units; this offer covers one, so "
        f"{brief.quantity - 1} remain unsourced",
    )


def _rule_basis(offer: Offer, brief: BuyerBrief) -> RuleResult:
    compatibility = basis_compatible(
        offer.price_basis, offer.vat_regime, brief.budget_basis, brief.budget_vat_regime
    )
    if compatibility.compatible:
        return RuleResult("price and VAT basis", RuleOutcome.PASS, compatibility.reason)
    if "unknown" in compatibility.reason:
        return RuleResult("price and VAT basis", RuleOutcome.UNKNOWN, compatibility.reason)
    return RuleResult("price and VAT basis", RuleOutcome.FAIL, compatibility.reason)


def _rule_destination(offer: Offer, brief: BuyerBrief, required: dict[str, Any]) -> RuleResult | None:
    destination = brief.destination_country or required.get("destination_country")
    if not destination:
        return None
    if not offer.location_country:
        return RuleResult(
            "destination eligibility",
            RuleOutcome.UNKNOWN,
            f"buyer delivers to {destination}; the car's current country is unknown, so "
            f"eligibility and route costs cannot be checked",
        )
    if offer.location_country.upper() == destination.upper():
        return RuleResult(
            "destination eligibility",
            RuleOutcome.PASS,
            f"the car is already in {destination}",
        )
    return RuleResult(
        "destination eligibility",
        RuleOutcome.PASS,
        f"cross-border route {offer.location_country} to {destination}; registration tax and "
        f"recoverability stay explicit inputs in the scenario",
    )


def _rule_budget(offer: Offer, brief: BuyerBrief, rate_book: Any = None) -> RuleResult | None:
    """A budget is a ceiling to test, never a commitment to buy.

    When the two are in different currencies, the rule stays unknown unless a
    dated rate has been entered. With one, the comparison is made and the rate and
    its date are stated in the reason, because a converted comparison is an
    assumption the reader should be able to see.
    """
    if brief.budget is None or offer.price is None:
        return None
    compatibility = basis_compatible(
        offer.price_basis, offer.vat_regime, brief.budget_basis, brief.budget_vat_regime
    )
    if not compatibility.compatible:
        return RuleResult(
            "budget headroom",
            RuleOutcome.UNKNOWN,
            f"cannot compare the asking price with the stated budget: {compatibility.reason}",
        )

    budget = brief.budget
    converted_note = ""
    if offer.price.currency != budget.currency:
        conversion = (
            rate_book.convert(budget, offer.price.currency) if rate_book is not None else None
        )
        if conversion is None:
            return RuleResult(
                "budget headroom",
                RuleOutcome.UNKNOWN,
                f"asking price is {offer.price.currency} and the budget is {budget.currency}; "
                f"a dated FX rate is required before comparing",
            )
        budget = conversion.converted
        converted_note = (
            f", after converting the {brief.budget.format()} budget at "
            f"{conversion.rate.label}"
        )

    if offer.price > budget:
        return RuleResult(
            "budget headroom",
            RuleOutcome.FAIL,
            f"asking {offer.price.format()} exceeds the stated budget "
            f"{budget.format()}{converted_note}",
        )
    return RuleResult(
        "budget headroom",
        RuleOutcome.PASS,
        f"asking {offer.price.format()} is within the stated budget {budget.format()}"
        f"{converted_note} (a budget is not a commitment)",
    )


def _rule_supply_freshness(offer: Offer, now: datetime, config: Config) -> RuleResult:
    hours = config.freshness.supply_hours
    if offer.last_observation_state == "fetch_failed":
        return RuleResult(
            "supply confirmation",
            RuleOutcome.UNKNOWN,
            "the last check failed, which is not evidence the car was sold",
        )
    if offer.last_observation_state == "not_seen":
        return RuleResult(
            "supply confirmation",
            RuleOutcome.UNKNOWN,
            "not observed in the latest successful check, which is not evidence of a sale",
        )
    if offer.status in {OfferStatus.UNAVAILABLE.value, OfferStatus.RESERVED.value}:
        return RuleResult(
            "supply confirmation",
            RuleOutcome.FAIL,
            f"the seller states the car is {offer.status}",
        )
    if is_stale(now, offer.availability_confirmed_at, hours=hours):
        confirmed = (
            "never confirmed"
            if offer.availability_confirmed_at is None
            else f"last confirmed {offer.availability_confirmed_at.isoformat()}"
        )
        return RuleResult(
            "supply confirmation",
            RuleOutcome.UNKNOWN,
            f"availability is stale ({confirmed}, threshold {hours:g} h); it can stay visible "
            f"for research but must not be described as currently available",
        )
    return RuleResult(
        "supply confirmation",
        RuleOutcome.PASS,
        f"availability confirmed {offer.availability_confirmed_at.isoformat()}",
    )


# -- scoring and status --------------------------------------------------


def match_offer_to_brief(
    *,
    offer: Offer,
    vehicle: Vehicle | None,
    brief: BuyerBrief,
    company: Company,
    now: datetime,
    config: Config,
    economics_reviewed: bool = False,
    rate_book: Any = None,
) -> MatchResult:
    """Evaluate one offer against one buying brief."""
    rules = evaluate_requirements(
        offer=offer, vehicle=vehicle, brief=brief, now=now, config=config, rate_book=rate_book
    )
    notes: list[str] = []

    failed = [r for r in rules if r.outcome is RuleOutcome.FAIL]
    unknown = [r for r in rules if r.outcome is RuleOutcome.UNKNOWN]

    if brief.is_expired(now):
        status = FitStatus.BRIEF_EXPIRED
        notes.append(
            f"the buying brief expired on {brief.expires_at.date().isoformat()} and needs "
            f"reconfirmation before it can support a current opportunity"
        )
    elif failed:
        status = FitStatus.NO_MATCH
    elif unknown:
        status = FitStatus.NEEDS_VERIFICATION
    else:
        status = FitStatus.SPECIFICATION_FIT
        notes.append(
            "every mandatory fact matches; this does not prove the buyer will buy or that "
            "the economics work"
        )

    if is_stale(now, brief.conversation_date, days=config.freshness.brief_days):
        notes.append(
            f"the requirement was last discussed {brief.conversation_date.date().isoformat()}, "
            f"beyond the {config.freshness.brief_days:g}-day review window"
        )

    breakdown = _score(
        rules=rules,
        brief=brief,
        offer=offer,
        now=now,
        config=config,
        status=status,
        economics_reviewed=economics_reviewed,
    )
    notes.append(
        "contact availability and contact permission are separate gates and contribute "
        "nothing to this score"
    )

    return MatchResult(
        offer_id=offer.id,
        company_id=company.id,
        brief_id=brief.id,
        fit_status=status,
        rules=rules,
        score=sum(breakdown.values()),
        score_breakdown=breakdown,
        notes=notes,
        supply_confirmed_at=offer.availability_confirmed_at,
        expires_at=_match_expiry(offer, brief, now, config),
    )


def _score(
    *,
    rules: list[RuleResult],
    brief: BuyerBrief,
    offer: Offer,
    now: datetime,
    config: Config,
    status: FitStatus,
    economics_reviewed: bool,
) -> dict[str, int]:
    """A transparent rule score, not a probability of closing."""
    zero = {
        "specification": 0,
        "confirmed_requirement": 0,
        "timing": 0,
        "reviewed_economics": 0,
        "freshness": 0,
    }
    # A rejection scores nothing. Leaving points on a known conflict would let a
    # rejected pair out-rank a weaker but genuinely open one in any shared list.
    if status is FitStatus.NO_MATCH:
        return zero
    # An expired brief earns no credit for being confirmed: it needs reconfirming
    # before it supports a current opportunity at all.
    if status is FitStatus.BRIEF_EXPIRED:
        return zero

    total_rules = len(rules) or 1
    passed = len([r for r in rules if r.outcome is RuleOutcome.PASS])

    if status is FitStatus.SPECIFICATION_FIT:
        specification = WEIGHT_SPECIFICATION
    else:
        specification = int(WEIGHT_SPECIFICATION * passed / total_rules)

    confirmed = WEIGHT_CONFIRMED_REQUIREMENT if brief.is_confirmed else 0

    timing = 0
    if brief.required_by is not None and offer.available_from is not None:
        if offer.available_from <= brief.required_by:
            timing = WEIGHT_TIMING
    elif brief.required_by is None:
        # No stated deadline: nothing to score, and nothing to claim either.
        timing = 0

    economics = WEIGHT_REVIEWED_ECONOMICS if economics_reviewed else 0

    freshness = 0
    if not is_stale(now, offer.availability_confirmed_at, hours=config.freshness.supply_hours):
        freshness = WEIGHT_FRESHNESS

    return {
        "specification": specification,
        "confirmed_requirement": confirmed,
        "timing": timing,
        "reviewed_economics": economics,
        "freshness": freshness,
    }


def _match_expiry(
    offer: Offer, brief: BuyerBrief, now: datetime, config: Config
) -> datetime | None:
    """A match is no fresher than the earliest expiry among its inputs."""
    candidates = [
        offer.valid_until,
        brief.expires_at,
        (
            offer.availability_confirmed_at + timedelta(hours=config.freshness.supply_hours)
            if offer.availability_confirmed_at
            else None
        ),
    ]
    known = [c for c in candidates if c is not None]
    return min(known) if known else None


def category_fit_prospect(
    *,
    offer: Offer,
    vehicle: Vehicle | None,
    company: Company,
    now: datetime,
    config: Config,
) -> MatchResult:
    """A company carrying similar stock but with no recorded requirement.

    This can be researched or qualified. It can never be presented as a confirmed
    buyer, and it is kept in a separate view from real briefs.
    """
    rules = [
        RuleResult(
            "confirmed requirement",
            RuleOutcome.UNKNOWN,
            f"{company.display_name} has no recorded buying brief for this model; public "
            f"category fit is not confirmed demand",
        )
    ]
    if vehicle is not None:
        rules.append(
            RuleResult(
                "category evidence",
                RuleOutcome.PASS,
                f"the company is recorded as {company.category} and the car is a "
                f"{vehicle.model_family}",
            )
        )
    return MatchResult(
        offer_id=offer.id,
        company_id=company.id,
        brief_id=None,
        fit_status=FitStatus.CATEGORY_FIT_PROSPECT,
        rules=rules,
        score=0,
        score_breakdown={
            "specification": 0,
            "confirmed_requirement": 0,
            "timing": 0,
            "reviewed_economics": 0,
            "freshness": 0,
        },
        notes=[
            "research prospect only: no buying brief exists, so this is not a qualified "
            "opportunity and cannot enter a contact-ready queue as a buyer"
        ],
    )


def rank(results: Iterable[MatchResult]) -> list[MatchResult]:
    """Rank compatible records. Specification fits always precede weaker states."""
    order = {
        FitStatus.SPECIFICATION_FIT: 0,
        FitStatus.NEEDS_VERIFICATION: 1,
        FitStatus.BRIEF_EXPIRED: 2,
        FitStatus.CATEGORY_FIT_PROSPECT: 3,
        FitStatus.NO_MATCH: 4,
    }
    return sorted(results, key=lambda r: (order[r.fit_status], -r.score))


def budget_headroom(offer: Offer, brief: BuyerBrief) -> Money | None:
    """Difference between a stated budget and an asking price, when comparable."""
    if offer.price is None or brief.budget is None:
        return None
    if offer.price.currency != brief.budget.currency:
        return None
    if not basis_compatible(
        offer.price_basis, offer.vat_regime, brief.budget_basis, brief.budget_vat_regime
    ).compatible:
        return None
    return brief.budget - offer.price
