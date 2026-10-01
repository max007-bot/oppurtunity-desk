"""Call preparation, drafts and the gates around them.

A call brief is always producible for internal research: it is built from reviewed
fields and simply omits what is not known. A *contact-ready* draft additionally
requires a permitting channel policy, no suppression, export/reuse rights on the
underlying source and facts that are still fresh.

Every gate is re-evaluated when a draft is reopened, copied or exported, so a
draft that was valid yesterday becomes unusable once the account objects, the
offer expires or the supporting facts change.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from ..clock import Clock, is_stale, to_iso
from ..config import Config, ContactPolicyStatus, DraftStatus, FitStatus
from ..repositories import companies as companies_repo
from ..repositories import followup as followup_repo
from ..repositories import supply as supply_repo
from ..repositories.base import fingerprint
from ..repositories.companies import BuyerBrief, Company, Contact
from ..repositories.supply import Offer, Vehicle
from .matching import MatchResult, RuleOutcome, RuleResult
from .normalization import family_label, new_vehicle_indicator
from .source_policy import SourcePolicy


@dataclass
class GateResult:
    """Outcome of the contact-readiness gates, with every reason preserved."""

    status: DraftStatus
    reasons: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)

    @property
    def contact_ready(self) -> bool:
        return self.status is DraftStatus.CONTACT_READY


@dataclass
class CallBrief:
    """A deterministic brief. Unsupported claims are omitted, never invented."""

    company_name: str
    contact_label: str
    reason_for_relevance: str
    supply_status: str
    tax_status: str
    known_requirement: str
    opening: str
    questions: list[str]
    next_step: str
    missing_facts: list[str]
    assumptions: list[str] = field(default_factory=list)
    status: DraftStatus = DraftStatus.INTERNAL_RESEARCH
    gate_reasons: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"Company: {self.company_name}",
            f"Contact: {self.contact_label}",
            f"Reason for relevance: {self.reason_for_relevance}",
            f"Supply status: {self.supply_status}",
            f"Cross-border basis: {self.tax_status}",
            f"Known requirement: {self.known_requirement}",
            "",
            f"Opening: {self.opening}",
            "",
            "Qualification:",
        ]
        lines.extend(f"  {index}. {question}" for index, question in enumerate(self.questions, 1))
        lines.extend(["", f"Next step: {self.next_step}"])
        if self.missing_facts:
            lines.extend(["", "Missing facts:"])
            lines.extend(f"  - {fact}" for fact in self.missing_facts)
        if self.assumptions:
            lines.extend(["", "Assumptions shown to the salesperson:"])
            lines.extend(f"  - {item}" for item in self.assumptions)
        lines.extend(
            [
                "",
                f"Use status: {self.status.value}",
            ]
        )
        lines.extend(f"  - {reason}" for reason in self.gate_reasons)
        return "\n".join(lines)


def build_call_brief(
    *,
    company: Company,
    contact: Contact | None,
    offer: Offer | None,
    vehicle: Vehicle | None,
    brief: BuyerBrief | None,
    match: MatchResult | None,
    now: datetime,
    config: Config,
    authorised_company: str,
    salesperson: str = "Max",
    gate: GateResult | None = None,
    scenario_summary: str | None = None,
) -> CallBrief:
    """Assemble a call brief from reviewed facts only."""
    missing: list[str] = []
    assumptions: list[str] = []

    # -- reason for relevance
    if brief is not None and brief.is_confirmed:
        reason = (
            f"confirmed requirement for a {family_label(brief.model_family)} recorded on "
            f"{brief.conversation_date.date().isoformat()}, confirmed by {brief.confirmed_by}"
        )
    elif brief is not None:
        reason = (
            f"requirement for a {family_label(brief.model_family)} discussed on "
            f"{brief.conversation_date.date().isoformat()}, not yet confirmed"
        )
        missing.append("confirmation of the buying requirement")
    elif vehicle is not None:
        reason = (
            f"recorded as {company.category} and the vehicle is a "
            f"{family_label(vehicle.model_family)}; this is public category fit, not confirmed "
            f"demand"
        )
        missing.append("any recorded buying requirement")
    else:
        reason = f"recorded as {company.category} in {company.country}"
        missing.append("any recorded buying requirement")

    # -- supply status
    if offer is None:
        supply = "no specific vehicle attached to this brief"
    elif offer.last_observation_state == "fetch_failed":
        supply = "the last check of this offer failed; that is not evidence of a sale"
        missing.append("a successful availability check")
    elif offer.last_observation_state == "not_seen":
        supply = "not observed in the latest successful check; that is not evidence of a sale"
        missing.append("a successful availability check")
    elif is_stale(now, offer.availability_confirmed_at, hours=config.freshness.supply_hours):
        when = (
            "never confirmed"
            if offer.availability_confirmed_at is None
            else f"last confirmed {_moment(offer.availability_confirmed_at)}"
        )
        supply = (
            f"availability is stale ({when}); do not describe this car as currently available"
        )
        missing.append("a current availability confirmation from the seller")
    else:
        supply = (
            f"availability confirmed {_moment(offer.availability_confirmed_at)}"
            f"{', physical stock' if offer.is_physical_stock else ''}"
        )
        if offer.is_allocation:
            supply += "; this is a build slot or allocation, not a car on the ground"

    # -- cross-border tax basis
    #
    # This is on the brief rather than buried in a calculation because it decides
    # how the whole transaction is handled. A new means of transport supplied
    # across an EU border is treated differently from a used car, and the buyer
    # accounts for acquisition VAT at home. It is the first thing a trade buyer
    # asks about, so the salesperson should not have to go and look it up.
    indicator = (
        new_vehicle_indicator(
            first_registration=vehicle.first_registration,
            mileage_km=vehicle.mileage_km,
            at=now,
        )
        if vehicle is not None
        else None
    )
    if indicator is None:
        tax_status = "no vehicle attached, so the new-means-of-transport test cannot be applied"
    elif indicator.indicated_new is True:
        tax_status = (
            "meets the EU new-means-of-transport test (" + "; ".join(indicator.reasons) + ")"
        )
    elif indicator.indicated_new is False:
        tax_status = (
            "does not meet the EU new-means-of-transport test ("
            + "; ".join(indicator.reasons)
            + ")"
        )
    else:
        tax_status = "cannot be determined (" + "; ".join(indicator.reasons) + ")"
        missing.append(
            "the first registration date or mileage needed to settle the "
            "new-means-of-transport test"
        )

    # -- known requirement
    if brief is None:
        requirement = "none recorded"
    else:
        parts: list[str] = []
        if brief.budget is not None:
            parts.append(
                f"budget {brief.budget.format()} on a {brief.budget_basis}/"
                f"{brief.budget_vat_regime} basis"
            )
        else:
            missing.append("a stated budget")
        if brief.required_specs:
            parts.append(f"must have {describe_requirements(brief.required_specs)}")
        if brief.required_by is not None:
            parts.append(f"needed by {brief.required_by.date().isoformat()}")
        if brief.quantity > 1:
            parts.append(f"{brief.quantity} units")
        requirement = "; ".join(parts) if parts else "recorded, but with no specifics captured"

    # -- verified opener; only facts with a provenance route go in here
    verified_fact = _verified_fact(company=company, offer=offer, vehicle=vehicle, brief=brief)
    opening = (
        f"{salesperson} from {authorised_company}. I saw {verified_fact} and wanted to check "
        f"whether you buy {_category_phrase(vehicle, brief)} for stock or only against client "
        f"orders."
    )

    questions = [
        "Who approves the purchase on your side?",
        "Which specification and registration basis matter for this one?",
        "What timing are you working to, and would you buy from stock or to order?",
    ]
    if brief is not None and brief.required_specs:
        unknowns = (match.unknown if match else [])
        if unknowns:
            questions[1] = (
                f"Can you confirm {unknowns[0].name}? Our record does not have it, so I do not "
                f"want to state it either way."
            )

    next_step = "A 12-minute vehicle or requirements review, if that is useful to you."

    if match is not None:
        for rule in match.unknown:
            missing.append(f"{rule.name} ({rule.reason})")
        for rule in match.failed:
            assumptions.append(f"known conflict: {rule.render()}")
        if match.fit_status is FitStatus.CATEGORY_FIT_PROSPECT:
            assumptions.append(
                "category-fit prospect only: no buying brief exists, so this is research, not a "
                "qualified opportunity"
            )
        if match.fit_status is FitStatus.BRIEF_EXPIRED:
            assumptions.append(
                "the buying brief has expired and needs reconfirmation before it supports a "
                "current opportunity"
            )

    if scenario_summary:
        assumptions.append(scenario_summary)

    if contact is None or not (contact.business_phone or contact.business_email):
        missing.append("a published business contact route")
    elif contact.verified_at is None or is_stale(
        now, contact.verified_at, days=config.freshness.contact_days
    ):
        missing.append("contact verification within the freshness window")

    if company.buying_authority == "unknown":
        missing.append("who holds buying authority (recorded as unknown, not guessed)")

    gate = gate or GateResult(DraftStatus.INTERNAL_RESEARCH, ["no gate evaluation supplied"])

    return CallBrief(
        company_name=company.display_name,
        contact_label=_contact_label(contact),
        reason_for_relevance=reason,
        supply_status=supply,
        tax_status=tax_status,
        known_requirement=requirement,
        opening=opening,
        questions=questions,
        next_step=next_step,
        missing_facts=list(dict.fromkeys(missing)),
        assumptions=assumptions,
        status=gate.status,
        gate_reasons=[*gate.reasons, *gate.blocking],
    )


def _moment(value: datetime | None) -> str:
    """A timestamp as a person would read it, with the zone stated."""
    if value is None:
        return "unknown"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _contact_label(contact: Contact | None) -> str:
    if contact is None:
        return "unknown - no published business contact recorded"
    if contact.is_identified:
        role = f", {contact.role_title}" if contact.role_title else ""
        route = contact.business_phone or contact.business_email or "no route recorded"
        return f"{contact.full_name}{role} ({route})"
    route = contact.business_phone or contact.business_email or "no route recorded"
    return f"unknown person, {contact.contact_type} route: {route}"


def _verified_fact(
    *, company: Company, offer: Offer | None, vehicle: Vehicle | None, brief: BuyerBrief | None
) -> str:
    """Pick one fact that has a provenance route. Never a flattering guess."""
    if brief is not None and brief.is_confirmed:
        return (
            f"you were looking for a {family_label(brief.model_family)} when we spoke on "
            f"{brief.conversation_date.date().isoformat()}"
        )
    if company.network_claim:
        return f"your site states {company.network_claim}"
    if vehicle is not None and offer is not None and offer.listing_url:
        return f"you are advertising a {family_label(vehicle.model_family)}"
    if vehicle is not None:
        return f"you work with {family_label(vehicle.model_family)} stock"
    return f"you are listed as a {company.category} in {company.country}"


def _category_phrase(vehicle: Vehicle | None, brief: BuyerBrief | None) -> str:
    if brief is not None:
        return family_label(brief.model_family)
    if vehicle is not None:
        return family_label(vehicle.model_family)
    return "this category"


# Readable phrasing for the hard requirements, so a brief can be read aloud.
_REQUIREMENT_PHRASES = {
    "steering": lambda v: f"{str(v).upper()} steering",
    "seats": lambda v: f"{v} homologated seats",
    "min_seats": lambda v: f"at least {v} homologated seats",
    "powertrain": lambda v: f"a {v} powertrain",
    "max_mileage_km": lambda v: f"no more than {int(v):,} km",
    "max_registration_age_months": lambda v: f"first registered within {v} months",
    "stock_requirement": lambda v: (
        "physical stock, not an allocation" if v == "physical_stock" else f"{v} stock"
    ),
    "variant": lambda v: f"the {v} variant",
    "destination_country": lambda v: f"delivery to {v}",
}


def describe_requirements(required: dict[str, Any]) -> str:
    """Turn the stored requirement object into a sentence a person can say."""
    parts: list[str] = []
    for key, value in sorted(required.items()):
        if key == "must_have_options":
            options = ", ".join(str(option).replace("_", " ") for option in value)
            parts.append(f"the {options} option" + ("s" if len(value) > 1 else ""))
            continue
        phrase = _REQUIREMENT_PHRASES.get(key)
        parts.append(phrase(value) if phrase else f"{key.replace('_', ' ')} {value}")
    return ", ".join(parts)


# -- gates ---------------------------------------------------------------


def evaluate_gates(
    conn: sqlite3.Connection,
    *,
    company: Company,
    contact: Contact | None,
    channel: str,
    offer: Offer | None,
    brief: BuyerBrief | None,
    match: MatchResult | None,
    policy_service: SourcePolicy,
    now: datetime,
    config: Config,
) -> GateResult:
    """Decide whether a contact-ready draft may be produced right now."""
    reasons: list[str] = []
    blocking: list[str] = []

    # 1. Suppression overrides everything, including a high score.
    suppressions = companies_repo.suppressions_for(conn, company.id, channel=channel)
    if suppressions:
        blocking.append(
            f"the account is suppressed: {suppressions[0]['reason']}. An objection overrides "
            f"future outreach actions regardless of any match score."
        )
        return GateResult(DraftStatus.BLOCKED, reasons, blocking)

    # 2. Channel policy must be an explicit human decision.
    policy = companies_repo.policy_for(conn, company.id, channel, contact_id=contact.id if contact else None)
    if policy is None:
        blocking.append(
            f"no contact policy recorded for {channel}; a discovered address does not become "
            f"permitted use on its own"
        )
    else:
        effective = policy.effective_status(now)
        if effective == ContactPolicyStatus.PERMITTED_FOR_SCOPE.value:
            reasons.append(
                f"{channel} permitted for {policy.purpose} by {policy.reviewer} on "
                f"{policy.reviewed_at.date().isoformat()}"
            )
        elif effective == ContactPolicyStatus.DENIED.value:
            blocking.append(f"{channel} contact is recorded as denied for this account")
        elif effective == ContactPolicyStatus.EXPIRED.value:
            blocking.append(
                f"the {channel} permission expired on {policy.expires_at.date().isoformat()} and "
                f"needs review"
            )
        else:
            blocking.append(
                f"{channel} policy is {effective}; an internal research brief is available, but "
                f"this account is not in the actionable outreach list"
            )

    # 3. A contact route must actually exist.
    if contact is None:
        blocking.append("no published business contact is recorded")
    elif channel == "email" and not contact.business_email:
        blocking.append("no published business email is recorded")
    elif channel == "phone" and not contact.business_phone:
        blocking.append("no published business phone number is recorded")

    # 4. Source reuse rights on the underlying evidence.
    if offer is not None:
        export = policy_service.may_export(offer.source_id)
        if not export.allowed:
            blocking.append(f"source restriction: {export.reason}")
        else:
            reasons.append(export.reason)

    # 5. Facts must still be fresh and the offer still valid.
    if offer is not None:
        if offer.is_expired(now):
            blocking.append(
                f"the offer expired on {offer.valid_until.isoformat()}; its availability claim "
                f"needs reconfirmation before any contact-ready draft"
            )
        if is_stale(now, offer.availability_confirmed_at, hours=config.freshness.supply_hours):
            blocking.append(
                "availability is stale, so the draft may not claim the car is currently available"
            )
        if offer.last_observation_state in {"not_seen", "fetch_failed"}:
            blocking.append(
                f"the latest check state is {offer.last_observation_state}, which is neither a "
                f"sale nor a confirmation"
            )

    if brief is not None:
        if brief.is_expired(now):
            blocking.append(
                f"the buying brief expired on {brief.expires_at.date().isoformat()}"
            )
        if not brief.is_confirmed:
            blocking.append("the buying requirement is not confirmed")

    # 6. The match itself must be a clean specification fit.
    if match is not None and not match.contact_ready_eligible:
        unresolved = [r.name for r in (match.failed + match.unknown)]
        blocking.append(
            f"the match is {match.fit_status.value} with unresolved conditions "
            f"({', '.join(unresolved) or 'none listed'}); research matches never enter the "
            f"contact-ready queue"
        )

    if blocking:
        return GateResult(DraftStatus.INTERNAL_RESEARCH, reasons, blocking)
    reasons.append("all gates passed at this moment; they are re-checked on reopen, copy or export")
    return GateResult(DraftStatus.CONTACT_READY, reasons, blocking)


def facts_fingerprint(
    *, offer: Offer | None, brief: BuyerBrief | None, company: Company, contact: Contact | None
) -> str:
    """Fingerprint of the facts a draft relies on.

    If any of them changes, the stored draft no longer matches and is treated as
    stale rather than silently reused.
    """
    return fingerprint(
        company.id,
        company.legal_name,
        None if contact is None else contact.id,
        None if contact is None else contact.business_email,
        None if contact is None else contact.business_phone,
        None if offer is None else offer.id,
        None if offer is None else (offer.price.minor_units if offer.price else None),
        None if offer is None else offer.status,
        None if offer is None else offer.stock_kind,
        None
        if offer is None or offer.availability_confirmed_at is None
        else offer.availability_confirmed_at.isoformat(),
        None if offer is None or offer.valid_until is None else offer.valid_until.isoformat(),
        None if brief is None else brief.id,
        None if brief is None else (brief.budget.minor_units if brief.budget else None),
        None if brief is None or brief.confirmed_at is None else brief.confirmed_at.isoformat(),
        None if brief is None or brief.expires_at is None else brief.expires_at.isoformat(),
    )


def match_from_row(row: sqlite3.Row) -> MatchResult:
    """Rebuild a MatchResult from its stored explanation.

    The rule outcomes were already computed and stored, so the gates work from the
    same reasons the user can see rather than from a fresh, possibly different, run.
    """

    def rules(payload: str, outcome: RuleOutcome) -> list[RuleResult]:
        out: list[RuleResult] = []
        for item in json.loads(payload or "[]"):
            name, _, reason = str(item).partition(": ")
            out.append(RuleResult(name=name, outcome=outcome, reason=reason or name))
        return out

    return MatchResult(
        offer_id=row["offer_id"],
        company_id=row["company_id"],
        brief_id=row["brief_id"],
        fit_status=FitStatus(row["fit_status"]),
        rules=[
            *rules(row["passed_rules"], RuleOutcome.PASS),
            *rules(row["failed_rules"], RuleOutcome.FAIL),
            *rules(row["unknown_rules"], RuleOutcome.UNKNOWN),
        ],
        score=row["score"],
        score_breakdown=json.loads(row["score_breakdown"] or "{}"),
        rule_version=row["rule_version"],
    )


@dataclass
class DraftValidity:
    usable: bool
    status: DraftStatus
    reasons: list[str]


def revalidate_draft(
    conn: sqlite3.Connection,
    draft_row: sqlite3.Row,
    *,
    policy_service: SourcePolicy,
    now: datetime,
    config: Config,
    clock: Clock,
) -> DraftValidity:
    """Re-check a stored draft before it may be copied or exported again."""
    company = companies_repo.get_company(conn, draft_row["company_id"])
    if company is None:
        return DraftValidity(False, DraftStatus.BLOCKED, ["the company record no longer exists"])

    contact = (
        companies_repo.get_contact(conn, draft_row["contact_id"])
        if draft_row["contact_id"]
        else None
    )
    brief = (
        companies_repo.get_brief(conn, draft_row["brief_id"]) if draft_row["brief_id"] else None
    )

    offer = (
        supply_repo.get_offer(conn, draft_row["offer_id"]) if draft_row["offer_id"] else None
    )

    # Re-evaluate the match from its stored explanation rather than assuming the
    # state it had when the draft was written.
    match_result: MatchResult | None = None
    if draft_row["match_id"]:
        match_row = conn.execute(
            "SELECT * FROM matches WHERE id = ?", (draft_row["match_id"],)
        ).fetchone()
        if match_row is None:
            return DraftValidity(
                False,
                DraftStatus.BLOCKED,
                ["the match this draft was built from no longer exists; regenerate it"],
            )
        match_result = match_from_row(match_row)
        if offer is None:
            offer = supply_repo.get_offer(conn, match_row["offer_id"])

    gate = evaluate_gates(
        conn,
        company=company,
        contact=contact,
        channel=draft_row["channel"],
        offer=offer,
        brief=brief,
        match=match_result,
        policy_service=policy_service,
        now=now,
        config=config,
    )

    reasons = [*gate.reasons, *gate.blocking]

    current = facts_fingerprint(offer=offer, brief=brief, company=company, contact=contact)
    if current != draft_row["facts_fingerprint"]:
        reasons.append(
            "the supporting facts have changed since this draft was written; regenerate it "
            "rather than sending stale content"
        )
        return DraftValidity(False, DraftStatus.BLOCKED, reasons)

    expires = draft_row["expires_at"]
    if expires:
        from ..clock import from_iso

        expiry = from_iso(expires)
        if expiry is not None and expiry <= now:
            reasons.append(f"the draft expired on {expiry.isoformat()}")
            return DraftValidity(False, DraftStatus.BLOCKED, reasons)

    if gate.status is DraftStatus.CONTACT_READY:
        return DraftValidity(True, DraftStatus.CONTACT_READY, reasons)
    if gate.status is DraftStatus.BLOCKED:
        return DraftValidity(False, DraftStatus.BLOCKED, reasons)
    return DraftValidity(False, DraftStatus.INTERNAL_RESEARCH, reasons)


def store_draft(
    conn: sqlite3.Connection,
    *,
    brief_text: str,
    company: Company,
    contact: Contact | None,
    offer: Offer | None,
    buyer_brief: BuyerBrief | None,
    match_id: str | None,
    channel: str,
    gate: GateResult,
    facts_used: Sequence[str],
    unresolved: Sequence[str],
    now: datetime,
    is_demo: bool,
    expires_at: datetime | None = None,
) -> str:
    return followup_repo.save_draft(
        conn,
        offer_id=None if offer is None else offer.id,
        company_id=company.id,
        channel=channel,
        body=brief_text,
        status=gate.status.value,
        facts_used=list(facts_used),
        unresolved_fields=list(unresolved),
        block_reasons=list(gate.blocking),
        policy_checked_at=to_iso(now),
        facts_fingerprint=facts_fingerprint(
            offer=offer, brief=buyer_brief, company=company, contact=contact
        ),
        now=to_iso(now),
        is_demo=is_demo,
        match_id=match_id,
        brief_id=None if buyer_brief is None else buyer_brief.id,
        contact_id=None if contact is None else contact.id,
        expires_at=None if expires_at is None else to_iso(expires_at),
    )


# -- exports -------------------------------------------------------------

RESEARCH_EXPORT_COLUMNS = [
    "company",
    "country",
    "category",
    "buying_route",
    "registry_id",
    "model_family",
    "fit_status",
    "score",
    "failed_or_unknown",
    "contact_policy",
]

CONTACT_EXPORT_COLUMNS = [
    "company",
    "country",
    "contact_name",
    "contact_role",
    "business_phone",
    "business_email",
    "channel",
    "policy_basis",
    "reviewed_by",
    "purpose",
]


@dataclass
class ExportResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    excluded: list[str]
    kind: str

    def summary(self) -> str:
        return (
            f"{self.kind} export: {len(self.rows)} row(s) included, "
            f"{len(self.excluded)} excluded."
        )


def build_export(
    conn: sqlite3.Connection,
    *,
    kind: str,
    company_ids: Sequence[str],
    policy_service: SourcePolicy,
    now: datetime,
    config: Config,
    channel: str = "phone",
) -> ExportResult:
    """Build a research export or a contact-ready export.

    These are deliberately different outputs. Contact fields are never exported
    for a suppressed or restricted account, and neither is data whose licence
    forbids export.
    """
    if kind not in {"research", "contact_ready"}:
        raise ValueError("kind must be research or contact_ready")

    rows: list[dict[str, Any]] = []
    excluded: list[str] = []

    for company_id in company_ids:
        company = companies_repo.get_company(conn, company_id)
        if company is None:
            excluded.append(f"{company_id}: no such company")
            continue

        if companies_repo.is_suppressed(conn, company.id, channel=channel):
            excluded.append(
                f"{company.display_name}: suppressed, so it cannot appear in any outreach export"
            )
            continue

        contacts = companies_repo.list_contacts(conn, company.id)
        policy = companies_repo.policy_for(conn, company.id, channel)
        policy_status = "unknown" if policy is None else policy.effective_status(now)

        if kind == "contact_ready":
            if policy_status != ContactPolicyStatus.PERMITTED_FOR_SCOPE.value:
                excluded.append(
                    f"{company.display_name}: {channel} policy is {policy_status}, not permitted "
                    f"for this scope"
                )
                continue
            if company.source_id:
                export_decision = policy_service.may_export(company.source_id)
                if not export_decision.allowed:
                    excluded.append(f"{company.display_name}: {export_decision.reason}")
                    continue
            contact = contacts[0] if contacts else None
            if contact is None:
                excluded.append(f"{company.display_name}: no published business contact")
                continue
            rows.append(
                {
                    "company": company.display_name,
                    "country": company.country,
                    "contact_name": contact.full_name or "unknown",
                    "contact_role": contact.role_title or "unknown",
                    "business_phone": contact.business_phone or "",
                    "business_email": contact.business_email or "",
                    "channel": channel,
                    "policy_basis": policy.basis if policy else "",
                    "reviewed_by": policy.reviewer if policy else "",
                    "purpose": policy.purpose if policy else "",
                }
            )
            continue

        matches = conn.execute(
            "SELECT * FROM matches WHERE company_id = ? ORDER BY score DESC LIMIT 1",
            (company.id,),
        ).fetchone()
        rows.append(
            {
                "company": company.display_name,
                "country": company.country,
                "category": company.category,
                "buying_route": company.buying_route,
                "registry_id": company.registry_id if company.registry_verified else "unverified",
                "model_family": _top_model(conn, company.id),
                "fit_status": matches["fit_status"] if matches else "no match computed",
                "score": matches["score"] if matches else 0,
                "failed_or_unknown": _unresolved_summary(matches),
                "contact_policy": policy_status,
            }
        )

    return ExportResult(
        columns=CONTACT_EXPORT_COLUMNS if kind == "contact_ready" else RESEARCH_EXPORT_COLUMNS,
        rows=rows,
        excluded=excluded,
        kind=kind,
    )


def _top_model(conn: sqlite3.Connection, company_id: str) -> str:
    row = conn.execute(
        "SELECT model_family FROM buyer_briefs WHERE company_id = ? ORDER BY conversation_date DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return row["model_family"] if row else "no brief recorded"


def _unresolved_summary(match_row: sqlite3.Row | None) -> str:
    if match_row is None:
        return ""
    import json

    failed = json.loads(match_row["failed_rules"] or "[]")
    unknown = json.loads(match_row["unknown_rules"] or "[]")
    parts = [f"failed: {', '.join(failed)}"] if failed else []
    if unknown:
        parts.append(f"unknown: {', '.join(unknown)}")
    return "; ".join(parts)
