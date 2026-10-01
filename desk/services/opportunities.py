"""One ranked view of every car worth a second look, and why.

This assembles work the rest of the project already does — comparable selection,
the asking-price gap, buyer matching, the cost scenario — into a single ordered
list, so a person opening the app sees the answer rather than four screens they
have to join up themselves.

**Ranking is by tier, not by a blended score.** A weighted total would let a large
unverified gap outrank a small verified contribution, which is precisely the
mistake the tool exists to prevent: a gap is an investigation signal that excludes
every cost, and a contribution is what is left after them. Those are different
kinds of claim and no weighting makes them commensurable. So they are separated
into tiers that are ordered by *how much is actually known*, and sorted by size
only within a tier.

That ordering is readable on screen. Every opportunity carries the tier it landed
in, in words, and the one fact that would move it up.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from ..clock import is_stale
from ..config import (
    Config,
    FitStatus,
    PriceBasis,
    PriceEvidenceType,
    ScenarioStatus,
    VatRegime,
)
from ..money import Money
from ..repositories import analysis as analysis_repo
from ..repositories import companies as companies_repo
from ..repositories import supply as supply_repo
from . import cost_templates, pricing
from .normalization import display_name, new_vehicle_indicator
from .pricing import AskingGap, ComparableSetResult, asking_price_gap
from .workflow import Workflow

#: Supply is what the business could buy. Retail and dealer-bid observations are
#: comparison evidence, never stock, and must not appear as opportunities.
SUPPLY_EVIDENCE = ("supply_asking",)


# -- tiers ------------------------------------------------------------------


@dataclass(frozen=True)
class Tier:
    key: str
    rank: int
    label: str
    meaning: str


TIER_VERIFIED_WITH_BUYER = Tier(
    "verified_with_buyer",
    0,
    "Verified contribution, buyer matched",
    "Costs are complete and a recorded requirement matches on every mandatory fact.",
)
TIER_VERIFIED = Tier(
    "verified",
    1,
    "Verified contribution, no buyer yet",
    "Costs are complete, so the contribution is real. Nobody has asked for this car.",
)
TIER_SIGNAL_WITH_BUYER = Tier(
    "signal_with_buyer",
    2,
    "Signal only, buyer matched",
    "A comparable median exists and a buyer matches, but the scenario is incomplete, "
    "so the gap is not a margin.",
)
TIER_SIGNAL = Tier(
    "signal",
    3,
    "Signal only",
    "A comparable median exists and the car is priced below it. Costs are unknown, so "
    "this is a reason to look, not a number to trade on.",
)
TIER_INSUFFICIENT = Tier(
    "insufficient",
    4,
    "Not enough evidence",
    "There are too few genuinely comparable cars, or a fact the comparison needs is "
    "missing. No claim is made either way.",
)

ALL_TIERS = (
    TIER_VERIFIED_WITH_BUYER,
    TIER_VERIFIED,
    TIER_SIGNAL_WITH_BUYER,
    TIER_SIGNAL,
    TIER_INSUFFICIENT,
)


# -- one opportunity ---------------------------------------------------------


@dataclass
class BuyerMatch:
    """A recorded requirement this car satisfies, with the rules behind it."""

    match_id: str
    company: companies_repo.Company
    brief: companies_repo.BuyerBrief | None
    fit_status: str
    score: int
    matched: list[str]
    failed: list[str]
    unknown: list[str]

    @property
    def is_specification_fit(self) -> bool:
        return self.fit_status == FitStatus.SPECIFICATION_FIT.value


@dataclass
class Opportunity:
    """One car, everything known about it, and what is still missing."""

    offer: supply_repo.Offer
    vehicle: supply_repo.Vehicle | None
    comparables: ComparableSetResult
    gap: AskingGap
    scenario: pricing.ScenarioResult | None
    #: Companies that recorded a requirement this car satisfies.
    matches: list[BuyerMatch] = field(default_factory=list)
    #: Companies that look plausible by category and have asked for nothing.
    #: Kept apart from ``matches`` because conflating them would turn "might buy
    #: this sort of car" into "asked for this car", which is the whole difference
    #: between a prospect list and a demand record.
    prospects: list[BuyerMatch] = field(default_factory=list)
    tier: Tier = TIER_INSUFFICIENT
    blocking_facts: list[str] = field(default_factory=list)
    source_label: str = "sample feed"
    template_used: str | None = None

    # -- the three numbers, kept distinct on purpose -----------------------

    @property
    def asking(self) -> Money | None:
        return self.offer.price

    @property
    def comparable_median(self) -> Money | None:
        return self.comparables.median if self.comparables.has_median else None

    @property
    def signal(self) -> Money | None:
        """The asking-price gap. Not profit, and never described as profit."""
        return self.gap.gap if self.gap.status == "available" else None

    @property
    def contribution(self) -> Money | None:
        """What is left after costs — only when the scenario actually completed."""
        if self.scenario is None or self.scenario.status is not ScenarioStatus.COMPLETE:
            return None
        return self.scenario.contribution

    @property
    def is_complete(self) -> bool:
        return self.contribution is not None

    @property
    def best_match(self) -> BuyerMatch | None:
        fits = [m for m in self.matches if m.is_specification_fit]
        pool = fits or self.matches
        return max(pool, key=lambda m: m.score) if pool else None

    @property
    def label(self) -> str:
        if self.vehicle is None:
            return f"Offer {self.offer.external_record_id or self.offer.id}"
        return display_name(self.vehicle.model_family, self.vehicle.variant)

    def why_ranked(self) -> str:
        """The sentence shown under the tier badge."""
        base = self.tier.meaning
        if self.tier in (TIER_VERIFIED_WITH_BUYER, TIER_VERIFIED) and self.contribution:
            basis = ""
            if self.scenario is not None and self.scenario.sale_evidence_type:
                basis = f" Sale side: {self.scenario.sale_evidence_label()}."
            return (
                f"{base} Contribution {self.contribution.format()} after all entered costs."
                f"{basis}"
            )
        if self.tier in (TIER_SIGNAL_WITH_BUYER, TIER_SIGNAL) and self.signal:
            missing = self.blocking_facts[0] if self.blocking_facts else "a required cost"
            return f"{base} Gap {self.signal.format()}; blocked on {missing}."
        return base

    def what_would_move_it_up(self) -> str:
        """The single most useful next action for this car."""
        if self.tier is TIER_INSUFFICIENT:
            if not self.comparables.has_median:
                return (
                    f"Three genuinely comparable cars are needed; "
                    f"{self.comparables.distinct_vehicles} passed the filters."
                )
            return "A recorded price basis, so the comparison can run."
        if self.blocking_facts:
            return f"Enter {self.blocking_facts[0]} to turn the signal into a contribution."
        if not self.matches:
            if self.prospects:
                return (
                    f"A recorded requirement. {len(self.prospects)} companies fit the "
                    f"category, but none of them has asked for anything."
                )
            return "A recorded buyer requirement that this specification satisfies."
        return "Nothing outstanding."


# -- building the list -------------------------------------------------------


def build(
    conn: sqlite3.Connection,
    *,
    workflow: Workflow,
    config: Config,
    now: datetime,
    limit: int | None = None,
    apply_cost_templates: bool = True,
) -> list[Opportunity]:
    """Assemble and rank every supply offer currently on file."""
    offers = supply_repo.list_offers(conn, evidence_types=SUPPLY_EVIDENCE)
    out: list[Opportunity] = []

    for offer in offers:
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id) if offer.vehicle_id else None

        comparables = workflow.comparables_for(
            conn, offer, evidence_types=("retail_asking",), persist=False
        )
        gap = asking_price_gap(target_offer=offer, comparables=comparables)
        matches, prospects = _matches_for(conn, offer)

        costs, template_label, blocking = _costs_for(
            conn, offer, destination=_destination_for(matches), now=now,
            apply_templates=apply_cost_templates,
        )

        sale, sale_evidence = stated_sale(offer, matches)

        scenario = None
        if offer.price is not None:
            _, scenario = workflow.scenario_from_offer(
                conn,
                offer,
                comparables=comparables,
                costs=costs,
                sale=sale,
                sale_evidence_type=sale_evidence,
                retail_assumption=comparables.median if comparables.has_median else None,
            )
            if scenario.status is not ScenarioStatus.COMPLETE:
                blocking = _blocking_from_scenario(scenario) or blocking

        opportunity = Opportunity(
            offer=offer,
            vehicle=vehicle,
            comparables=comparables,
            gap=gap,
            scenario=scenario,
            matches=matches,
            prospects=prospects,
            blocking_facts=blocking,
            source_label=_source_label(conn, offer),
            template_used=template_label,
        )
        opportunity.tier = _tier_for(opportunity, now=now, config=config)
        out.append(opportunity)

    out.sort(key=_sort_key)
    return out[:limit] if limit else out


def _sort_key(opportunity: Opportunity) -> tuple[int, int, str]:
    """Tier first, then size within the tier, then a stable identifier.

    The identifier tie-break matters for the same reason it does in the comparable
    set: two cars can produce the same figure, and the order a database happens to
    return them in must not decide which one a person sees first.
    """
    if opportunity.contribution is not None:
        size = -opportunity.contribution.minor_units
    elif opportunity.signal is not None:
        size = -opportunity.signal.minor_units
    else:
        size = 0
    return (
        opportunity.tier.rank,
        size,
        opportunity.offer.external_record_id or opportunity.offer.id,
    )


def _tier_for(opportunity: Opportunity, *, now: datetime, config: Config) -> Tier:
    has_buyer = any(m.is_specification_fit for m in opportunity.matches)

    if opportunity.is_complete and opportunity.contribution.minor_units > 0:
        return TIER_VERIFIED_WITH_BUYER if has_buyer else TIER_VERIFIED

    # A signal needs a median *and* a positive gap. A car priced above its
    # comparables is not an opportunity, and is not dressed up as one.
    if opportunity.signal is not None and opportunity.signal.minor_units > 0:
        return TIER_SIGNAL_WITH_BUYER if has_buyer else TIER_SIGNAL

    return TIER_INSUFFICIENT


# -- costs -------------------------------------------------------------------


def stated_sale(
    offer: supply_repo.Offer, matches: list["BuyerMatch"]
) -> tuple[Money | None, str | None]:
    """A matched buyer's confirmed budget, used as a stated sale amount.

    This is the only route to a complete scenario that does not require somebody
    to type a retail assumption, and it is legitimate because the figure came from
    the buyer rather than from us. It carries the ``buyer_budget`` evidence type
    all the way to the screen, which renders as "the buyer's stated budget, which
    is not a commitment" — because it is not one.

    The bases have to agree. A net budget against a gross asking price is not a
    comparison, so an unmatched basis yields nothing and the scenario stays
    incomplete.
    """
    if offer.price is None:
        return None, None
    for match in matches:
        if not match.is_specification_fit:
            continue
        brief = match.brief
        if brief is None or brief.budget is None or not brief.is_confirmed:
            continue
        if brief.budget.currency != offer.price.currency:
            continue
        if brief.budget_basis != offer.price_basis:
            continue
        if brief.budget_vat_regime != offer.vat_regime:
            continue
        return brief.budget, PriceEvidenceType.BUYER_BUDGET.value
    return None, None


def _destination_for(matches: list["BuyerMatch"]) -> str | None:
    """The destination country comes from the buyer, not from the car.

    A cost template is per route, and a route needs both ends. With no matched
    buyer there is no destination, so no template applies and the costs stay
    unknown — which is correct, not a gap to paper over.
    """
    for match in matches:
        if match.brief is not None and match.brief.destination_country:
            return match.brief.destination_country
    return None


def _costs_for(
    conn: sqlite3.Connection,
    offer: supply_repo.Offer,
    *,
    destination: str | None,
    now: datetime,
    apply_templates: bool,
) -> tuple[list[pricing.CostLine], str | None, list[str]]:
    """Start the cost lines from a reviewed template for the route, if one exists.

    A template supplies defaults, never facts. A template line with no amount
    arrives unknown and keeps blocking completion, which is exactly what the
    seeded demonstration relies on.
    """
    if not apply_templates:
        return [], None, ["transport, preparation and destination registration tax"]

    template = cost_templates.best_for_route(
        conn,
        origin_country=offer.location_country,
        destination_country=destination,
    )
    if template is None:
        return [], None, ["transport, preparation and destination registration tax"]

    lines = [line.to_cost_line() for line in template.lines]
    unknown = [line.label.lower() for line in template.lines if not line.is_known]
    return lines, template.name, unknown


def _blocking_from_scenario(scenario: pricing.ScenarioResult) -> list[str]:
    """What the scenario itself said it was waiting for."""
    return [str(item) for item in scenario.missing_inputs][:3]


# -- matches -----------------------------------------------------------------


def _matches_for(
    conn: sqlite3.Connection, offer: supply_repo.Offer
) -> tuple[list[BuyerMatch], list[BuyerMatch]]:
    """Recorded requirements, and separately the category-fit prospects.

    A category-fit prospect is a company whose line of business makes this kind
    of car plausible. It has not asked for anything, nobody has spoken to it, and
    putting it in the same list as a confirmed requirement would be the single
    easiest way for this tool to start lying.
    """
    rows = analysis_repo.list_matches(conn, offer_id=offer.id)
    out: list[BuyerMatch] = []
    prospects: list[BuyerMatch] = []
    for row in rows:
        if row["fit_status"] in (FitStatus.NO_MATCH.value, FitStatus.BRIEF_EXPIRED.value):
            continue
        company = companies_repo.get_company(conn, row["company_id"])
        if company is None:
            continue
        brief = companies_repo.get_brief(conn, row["brief_id"]) if row["brief_id"] else None
        entry = BuyerMatch(
            match_id=row["id"],
            company=company,
            brief=brief,
            fit_status=row["fit_status"],
            score=row["score"] or 0,
            matched=json.loads(row["passed_rules"] or "[]"),
            failed=json.loads(row["failed_rules"] or "[]"),
            unknown=json.loads(row["unknown_rules"] or "[]"),
        )
        if row["fit_status"] == FitStatus.CATEGORY_FIT_PROSPECT.value:
            prospects.append(entry)
        else:
            out.append(entry)

    key = lambda m: (-m.score, m.company.display_name)  # noqa: E731
    out.sort(key=key)
    prospects.sort(key=key)
    return out, prospects


# -- small lookups -----------------------------------------------------------


def _source_label(conn: sqlite3.Connection, offer: supply_repo.Offer) -> str:
    """Where this row came from. Stamped on every card, never only in a footer."""
    row = conn.execute(
        """SELECT s.name, s.access_mode FROM observations o
           JOIN sources s ON s.id = o.source_id
           WHERE o.offer_id = ? ORDER BY o.observed_at DESC LIMIT 1""",
        (offer.id,),
    ).fetchone()
    if row is None:
        return "sample feed"
    mode = (row["access_mode"] or "").strip()
    if mode in {"fixture", "file_import"}:
        return "sample feed"
    return row["name"] or "sample feed"


def freshness_note(offer: supply_repo.Offer, *, now: datetime, config: Config) -> str:
    if offer.last_observation_state == "fetch_failed":
        return "the last check failed; that is not evidence of a sale"
    if offer.last_observation_state == "not_seen":
        return "not seen in the latest check; that is not evidence of a sale"
    if is_stale(now, offer.availability_confirmed_at, hours=config.freshness.supply_hours):
        return "availability is stale; do not describe this car as currently available"
    return "availability confirmed within the freshness window"


def tax_note(
    vehicle: supply_repo.Vehicle | None, *, now: datetime
) -> tuple[str, bool | None]:
    if vehicle is None:
        return ("no vehicle record, so the new-means-of-transport test cannot run", None)
    indicator = new_vehicle_indicator(
        first_registration=vehicle.first_registration,
        mileage_km=vehicle.mileage_km,
        at=now,
    )
    if indicator.indicated_new is True:
        return ("EU new means of transport: the buyer accounts for acquisition VAT", True)
    if indicator.indicated_new is False:
        return ("not a new means of transport on either limb", False)
    return ("new-means-of-transport test cannot be determined from the record", None)


def basis_is_known(offer: supply_repo.Offer) -> bool:
    return (
        PriceBasis(offer.price_basis) is not PriceBasis.UNKNOWN
        and VatRegime(offer.vat_regime) is not VatRegime.UNKNOWN
    )


__all__ = [
    "ALL_TIERS",
    "BuyerMatch",
    "Opportunity",
    "SUPPLY_EVIDENCE",
    "Tier",
    "TIER_INSUFFICIENT",
    "TIER_SIGNAL",
    "TIER_SIGNAL_WITH_BUYER",
    "TIER_VERIFIED",
    "TIER_VERIFIED_WITH_BUYER",
    "basis_is_known",
    "build",
    "stated_sale",
    "freshness_note",
    "tax_note",
]
