"""Orchestration: recompute matches, comparables, scenarios and tasks.

This is the only place that runs the whole chain, so both the CLI and the UI get
identical results. Nothing here fetches anything; it works from stored records.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..clock import Clock, to_iso
from ..config import Config, FitStatus, PriceBasis, PriceEvidenceType, VatRegime
from ..db import Database
from ..money import Money
from ..repositories import analysis as analysis_repo
from ..repositories import companies as companies_repo
from ..repositories import supply as supply_repo
from . import adjustments as adjustments_service
from . import corrections, source_review
from . import fx, matching, pricing, tasks as tasks_service
from .pricing import ComparableCandidate, ComparableFilters, ComparableSetResult, ScenarioInput

# Only a supply asking price represents stock this desk could offer onward.
SUPPLY_EVIDENCE_TYPE = PriceEvidenceType.SUPPLY_ASKING.value
ALL_EVIDENCE_TYPES = tuple(kind.value for kind in PriceEvidenceType)


@dataclass
class RecomputeReport:
    matches: int = 0
    specification_fits: int = 0
    needs_verification: int = 0
    no_match: int = 0
    category_prospects: int = 0
    expired_briefs: int = 0
    comparable_sets: int = 0
    insufficient_comparables: int = 0
    tasks_created: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.matches} match(es) computed: {self.specification_fits} specification fit, "
            f"{self.needs_verification} needing verification, {self.no_match} rejected, "
            f"{self.expired_briefs} expired brief(s), {self.category_prospects} category-fit "
            f"prospect(s). {self.comparable_sets} comparable set(s), of which "
            f"{self.insufficient_comparables} had insufficient comparables. "
            f"{self.tasks_created} task(s) created."
        )


class Workflow:
    def __init__(self, db: Database, config: Config, clock: Clock) -> None:
        self.db = db
        self.config = config
        self.clock = clock

    # -- comparables ---------------------------------------------------
    def comparables_for(
        self,
        conn: sqlite3.Connection,
        offer: supply_repo.Offer,
        *,
        filters: ComparableFilters | None = None,
        evidence_types: tuple[str, ...] = pricing.RETAIL_EVIDENCE,
        persist: bool = True,
        use_fx: bool = True,
        use_adjustments: bool = True,
    ) -> ComparableSetResult:
        vehicle = (
            supply_repo.get_vehicle(conn, offer.vehicle_id) if offer.vehicle_id else None
        )
        candidates: list[ComparableCandidate] = []
        if vehicle is not None:
            # Fetch every priced observation for the family and let the pricing
            # service decide. Filtering evidence types in SQL would hide the
            # "this was a dealer bid, not a retail asking price" exclusions that
            # the comparables view exists to show.
            rows = supply_repo.comparable_observations(
                conn,
                model_family=vehicle.model_family,
                evidence_types=ALL_EVIDENCE_TYPES,
                exclude_offer_id=None,
            )
            candidates = [ComparableCandidate.from_row(row) for row in rows]

        # Dated rates and analyst adjustments both come from stored decisions a
        # person made. Neither is invented here, and without them the behaviour is
        # exactly as before: a foreign currency is excluded, nothing is adjusted.
        book = fx.RateBook(conn, at=self.clock.now()) if use_fx else None
        live_adjustments = (
            adjustments_service.live_for_target(conn, offer.id) if use_adjustments else {}
        )

        result = pricing.build_comparable_set(
            target_offer=offer,
            target_vehicle=vehicle,
            candidates=candidates,
            now=self.clock.now(),
            config=self.config,
            filters=filters,
            evidence_types=evidence_types,
            rate_book=book,
            adjustments=live_adjustments,
        )

        if persist:
            analysis_repo.save_comparable_set(
                conn,
                target_offer_id=offer.id,
                tax_basis=result.tax_basis,
                vat_regime=result.vat_regime,
                result_status=result.status,
                rule_version=result.rule_version,
                analysed_at=self.clock.now_iso(),
                is_demo=self.config.is_demo,
                filters=result.filters,
                median_minor=None if result.median is None else result.median.minor_units,
                low_minor=None if result.low is None else result.low.minor_units,
                high_minor=None if result.high is None else result.high.minor_units,
                currency=None if result.median is None else result.median.currency,
                included_count=len(result.included),
                distinct_vehicles=result.distinct_vehicles,
                median_before_adjustments_minor=(
                    None
                    if result.median_before_adjustments is None
                    else result.median_before_adjustments.minor_units
                ),
                adjustments_applied=len(result.adjusted),
                conversions_applied=len(result.converted),
                members=[
                    {
                        "offer_id": decision.candidate.offer_id,
                        "observation_id": decision.candidate.observation_id,
                        "included": decision.included,
                        "reason": decision.reason,
                        # The figure that entered the statistic...
                        "price_minor": decision.price_used.minor_units,
                        "currency": decision.price_used.currency,
                        # ...and what the source actually said, kept beside it.
                        "observed_price_minor": decision.observed_price.minor_units,
                        "observed_currency": decision.observed_price.currency,
                        "adjustment_minor": (
                            None
                            if decision.adjustment is None
                            else decision.adjustment.amount.minor_units
                        ),
                        "adjustment_reason": (
                            None if decision.adjustment is None else decision.adjustment.reason
                        ),
                        "fx_rate": (
                            None
                            if decision.conversion is None
                            else str(decision.conversion.rate.rate)
                        ),
                        "fx_rate_date": (
                            None
                            if decision.conversion is None
                            else decision.conversion.rate.rate_date.isoformat()
                        ),
                        "observed_at": to_iso(decision.candidate.observed_at),
                    }
                    for decision in result.decisions
                ],
            )
        return result

    # -- matching ------------------------------------------------------
    def recompute(self, *, persist_tasks: bool = True) -> RecomputeReport:
        """Recompute every match, comparable set and derived task.

        Only offers recorded as ``supply_asking`` are matched. A retail asking
        price, a dealer bid or a completed sale is market evidence for the
        comparable set, not stock this desk could offer to a buyer.
        """
        report = RecomputeReport()
        now = self.clock.now()

        with self.db.write() as conn:
            offers = supply_repo.list_offers(conn, evidence_types=(SUPPLY_EVIDENCE_TYPE,))
            briefs = companies_repo.list_briefs(conn)
            # One rate book for the whole pass, so a cross-currency budget is
            # compared consistently and only against rates someone entered.
            book = fx.RateBook(conn, at=now)
            # One representative offer per model family, for the category-fit view.
            representatives: dict[str, tuple[Any, Any]] = {}

            for offer in offers:
                vehicle = (
                    supply_repo.get_vehicle(conn, offer.vehicle_id) if offer.vehicle_id else None
                )
                if vehicle is None:
                    continue
                representatives.setdefault(vehicle.model_family, (offer, vehicle))

                comparables = self.comparables_for(conn, offer, persist=True)
                report.comparable_sets += 1
                if not comparables.has_median:
                    report.insufficient_comparables += 1

                economics_reviewed = self._economics_reviewed(conn, offer.id)

                relevant = [b for b in briefs if b.model_family == vehicle.model_family]
                for brief in relevant:
                    company = companies_repo.get_company(conn, brief.company_id)
                    if company is None:
                        continue
                    result = matching.match_offer_to_brief(
                        offer=offer,
                        vehicle=vehicle,
                        brief=brief,
                        company=company,
                        now=now,
                        config=self.config,
                        economics_reviewed=economics_reviewed,
                        rate_book=book,
                    )
                    match_id = self._save(conn, result)
                    report.matches += 1
                    _count(report, result.fit_status)

                    if persist_tasks:
                        suggestions = tasks_service.suggest_tasks(
                            conn, match=result, match_id=match_id, now=now, config=self.config
                        )
                        report.tasks_created += tasks_service.persist_suggestions(
                            conn, suggestions, now=now, is_demo=self.config.is_demo
                        )

            # Category fit is a property of a company and a model family, not of
            # every individual advert, so it is computed once per family against a
            # representative offer rather than once per offer.
            for family, (offer, vehicle) in representatives.items():
                relevant = [b for b in briefs if b.model_family == family]
                for company in self._category_candidates(conn, family, relevant):
                    result = matching.category_fit_prospect(
                        offer=offer,
                        vehicle=vehicle,
                        company=company,
                        now=now,
                        config=self.config,
                    )
                    self._save(conn, result)
                    report.matches += 1
                    report.category_prospects += 1

        return report

    def _category_candidates(
        self,
        conn: sqlite3.Connection,
        model_family: str,
        briefs: list[companies_repo.BuyerBrief],
    ) -> list[companies_repo.Company]:
        """Dealers and fleets with no brief for this model.

        Referral partners are excluded: a concierge or property company is an
        introduction route, not an invented fleet buyer.
        """
        with_briefs = {brief.company_id for brief in briefs}
        out: list[companies_repo.Company] = []
        for company in companies_repo.list_companies(conn):
            if company.id in with_briefs:
                continue
            if company.is_referral_partner:
                continue
            if company.category in {"dealer", "fleet", "rental", "chauffeur"}:
                out.append(company)
        return out

    def _economics_reviewed(self, conn: sqlite3.Connection, offer_id: str) -> bool:
        row = analysis_repo.latest_scenario_for_offer(conn, offer_id)
        return bool(row is not None and row["status"] == "complete")

    def _save(self, conn: sqlite3.Connection, result: matching.MatchResult) -> str:
        return analysis_repo.save_match(
            conn,
            offer_id=result.offer_id,
            brief_id=result.brief_id,
            company_id=result.company_id,
            fit_status=result.fit_status.value,
            score=result.score,
            score_breakdown=result.score_breakdown,
            passed_rules=[rule.render() for rule in result.passed],
            failed_rules=[rule.render() for rule in result.failed],
            unknown_rules=[rule.render() for rule in result.unknown],
            evidence_refs=[],
            computed_at=self.clock.now_iso(),
            rule_version=result.rule_version,
            is_demo=self.config.is_demo,
            supply_confirmed_at=(
                None if result.supply_confirmed_at is None else to_iso(result.supply_confirmed_at)
            ),
            expires_at=None if result.expires_at is None else to_iso(result.expires_at),
        )

    # -- scenarios -----------------------------------------------------
    def save_scenario(
        self,
        conn: sqlite3.Connection,
        *,
        offer: supply_repo.Offer,
        data: ScenarioInput,
        result: pricing.ScenarioResult,
        match_id: str | None = None,
        buyer_company_id: str | None = None,
        cost_template_id: str | None = None,
        cost_template_name: str | None = None,
    ) -> str:
        def minor(amount: Money | None) -> int | None:
            return None if amount is None else amount.minor_units

        return analysis_repo.save_scenario(
            conn,
            is_demo=self.config.is_demo,
            costs=[line.as_dict() for line in data.costs],
            offer_id=offer.id,
            match_id=match_id,
            buyer_company_id=buyer_company_id,
            market=data.market,
            currency=data.currency,
            acquisition_minor=minor(data.acquisition),
            acquisition_basis=data.acquisition_basis,
            acquisition_vat_regime=data.acquisition_vat_regime,
            acquisition_evidence_type=data.acquisition_evidence_type,
            sale_minor=minor(data.sale),
            sale_basis=data.sale_basis,
            sale_vat_regime=data.sale_vat_regime,
            sale_evidence_type=result.sale_evidence_type,
            retail_assumption_minor=minor(data.retail_assumption),
            dealer_downstream_cost_minor=minor(data.dealer_downstream_cost),
            dealer_required_contribution_minor=minor(data.dealer_required_contribution),
            included_costs_minor=minor(result.included_costs),
            total_outlay_minor=minor(result.total_outlay),
            hypothetical_trade_price_minor=minor(result.hypothetical_trade_price),
            contribution_minor=minor(result.contribution),
            asking_gap_minor=minor(result.asking_gap),
            cash_outflow_minor=minor(result.cash_outflow),
            recoverable_tax_minor=minor(result.recoverable_tax),
            cash_schedule_status=result.cash_schedule_status,
            status=result.status.value,
            missing_inputs=__import__("json").dumps(result.missing_inputs),
            assumptions=__import__("json").dumps(result.assumptions),
            commission_rule_id=data.commission_rule,
            cost_template_id=cost_template_id,
            cost_template_name=cost_template_name,
            computed_at=self.clock.now_iso(),
        )

    def scenario_from_offer(
        self,
        conn: sqlite3.Connection,
        offer: supply_repo.Offer,
        *,
        comparables: ComparableSetResult | None = None,
        costs: list[pricing.CostLine] | None = None,
        retail_assumption: Money | None = None,
        dealer_downstream_cost: Money | None = None,
        dealer_required_contribution: Money | None = None,
        sale: Money | None = None,
        sale_evidence_type: str | None = None,
    ) -> tuple[ScenarioInput, pricing.ScenarioResult]:
        """Build and compute a scenario from the offer's own recorded basis."""
        median = comparables.median if comparables and comparables.has_median else None
        data = ScenarioInput(
            acquisition=offer.price,
            acquisition_basis=offer.price_basis,
            acquisition_vat_regime=offer.vat_regime,
            acquisition_evidence_type=offer.price_evidence_type,
            sale=sale,
            sale_basis=offer.price_basis if sale is not None else PriceBasis.UNKNOWN.value,
            sale_vat_regime=offer.vat_regime if sale is not None else VatRegime.UNKNOWN.value,
            sale_evidence_type=sale_evidence_type,
            retail_assumption=retail_assumption,
            dealer_downstream_cost=dealer_downstream_cost,
            dealer_required_contribution=dealer_required_contribution,
            costs=costs or [],
            comparable_median=median,
            currency=offer.price.currency if offer.price else self.config.base_currency,
            market=offer.location_country,
        )
        return data, pricing.compute_scenario(data)


def _count(report: RecomputeReport, status: FitStatus) -> None:
    if status is FitStatus.SPECIFICATION_FIT:
        report.specification_fits += 1
    elif status is FitStatus.NEEDS_VERIFICATION:
        report.needs_verification += 1
    elif status is FitStatus.NO_MATCH:
        report.no_match += 1
    elif status is FitStatus.BRIEF_EXPIRED:
        report.expired_briefs += 1
    elif status is FitStatus.CATEGORY_FIT_PROSPECT:
        report.category_prospects += 1


def dashboard_counts(
    conn: sqlite3.Connection, *, now: datetime, config: Config
) -> dict[str, Any]:
    """Counts for the Today screen. Zero is reported accurately as zero."""
    def scalar(sql: str, params: tuple[Any, ...] = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return 0 if row is None else int(row[0])

    return {
        "new_alerts": scalar(
            "SELECT COUNT(*) FROM change_events WHERE acknowledged_at IS NULL"
        ),
        "open_tasks": scalar("SELECT COUNT(*) FROM tasks WHERE state = 'open'"),
        "tasks_due": scalar(
            "SELECT COUNT(*) FROM tasks WHERE state = 'open' AND due_at <= ?", (to_iso(now),)
        ),
        "stale_confirmations": len(
            tasks_service.stale_confirmations(conn, now=now, config=config)
        ),
        "briefs_due": len(tasks_service.briefs_due_for_review(conn, now=now, config=config)),
        "specification_fits": scalar(
            "SELECT COUNT(*) FROM matches WHERE fit_status = 'specification_fit'"
        ),
        "needs_verification": scalar(
            "SELECT COUNT(*) FROM matches WHERE fit_status = 'needs_verification'"
        ),
        "category_prospects": scalar(
            "SELECT COUNT(*) FROM matches WHERE fit_status = 'category_fit_prospect'"
        ),
        "pending_duplicates": len(companies_repo.pending_duplicate_reviews(conn)),
        # A source contradicting the reviewed record, and a source approval about
        # to lapse, are both work someone has to do rather than background noise.
        "pending_contradictions": corrections.pending_count(conn),
        "sources_due_for_review": len(source_review.due_for_review(conn, now=now)),
        "offers": scalar("SELECT COUNT(*) FROM offers"),
        "companies": scalar("SELECT COUNT(*) FROM companies"),
        "briefs": scalar("SELECT COUNT(*) FROM buyer_briefs"),
        "confirmed_briefs": scalar(
            "SELECT COUNT(*) FROM buyer_briefs WHERE confirmed_at IS NOT NULL"
        ),
        "observations": scalar("SELECT COUNT(*) FROM observations"),
    }
