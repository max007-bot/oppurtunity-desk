"""Capturing a buying requirement during a call.

The manual's most valuable record is a real requirement a named person stated on a
recorded date. This service is the path for typing one in while it is still
fresh, instead of putting it in a spreadsheet first and losing the detail.

It reuses the ordinary import validation, so a requirement typed in by hand is
held to exactly the same standard as one imported from a file: a confirmed brief
must name who confirmed it, a budget must state its basis and regime, and unknown
stays unknown.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..clock import to_iso
from ..config import BuyingRoute, Config, PriceBasis, VatRegime
from ..models import BuyerBriefRow, RowProblem, validate_rows
from ..money import Money
from ..repositories import companies as companies_repo
from ..repositories import supply as supply_repo
from ..repositories.base import fingerprint

# The hard requirements the matching rules understand, with the shape each needs.
# Anything the buyer says that is not in this list belongs in the evidence note
# rather than being forced into a rule that does not exist.
REQUIREMENT_FIELDS: dict[str, dict[str, Any]] = {
    "steering": {"label": "Steering side", "kind": "choice", "options": ["lhd", "rhd"]},
    "seats": {"label": "Homologated seats (exactly)", "kind": "int", "min": 1, "max": 9},
    "min_seats": {"label": "Homologated seats (at least)", "kind": "int", "min": 1, "max": 9},
    "powertrain": {
        "label": "Powertrain",
        "kind": "choice",
        "options": ["petrol", "diesel", "hybrid", "electric"],
    },
    "max_mileage_km": {"label": "Maximum mileage (km)", "kind": "int", "min": 0, "max": 500_000},
    "max_registration_age_months": {
        "label": "Maximum age since first registration (months)",
        "kind": "int",
        "min": 0,
        "max": 240,
    },
    "stock_requirement": {
        "label": "Stock requirement",
        "kind": "choice",
        "options": ["physical_stock", "allocation_ok"],
    },
    "must_have_options": {"label": "Required options", "kind": "list"},
}

# How long a brief stays current before it needs reconfirming, unless the buyer
# gave their own date. A product assumption, matching the freshness window.
DEFAULT_VALIDITY_DAYS = 30


@dataclass
class CaptureResult:
    brief_id: str | None
    created: bool
    problems: list[RowProblem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.brief_id is not None and not self.problems

    def summary(self) -> str:
        if not self.ok:
            return "Nothing was saved. " + "; ".join(p.render() for p in self.problems)
        verb = "captured" if self.created else "updated"
        return f"Requirement {verb}. Recompute to match it against current supply."


def build_row(
    *,
    company_external_id: str,
    model_family: str,
    conversation_date: datetime,
    external_id: str | None = None,
    variant: str | None = None,
    required_specs: dict[str, Any] | None = None,
    preferred_specs: dict[str, Any] | None = None,
    budget: Money | None = None,
    budget_basis: str = PriceBasis.UNKNOWN.value,
    budget_vat_regime: str = VatRegime.UNKNOWN.value,
    quantity: int = 1,
    destination_country: str | None = None,
    route: str = BuyingRoute.UNKNOWN.value,
    required_by: datetime | None = None,
    confirmed: bool = False,
    confirmed_by: str | None = None,
    expires_at: datetime | None = None,
    evidence_note: str | None = None,
    contact_name: str | None = None,
) -> dict[str, Any]:
    """Assemble an import row from what a person typed."""
    row: dict[str, Any] = {
        "external_id": external_id
        or f"call-{fingerprint(company_external_id, model_family, to_iso(conversation_date))}",
        "company_external_id": company_external_id,
        "model_family": model_family,
        "required_specs": {
            key: value
            for key, value in (required_specs or {}).items()
            if value not in (None, "", [], "unknown")
        },
        "preferred_specs": {
            key: value
            for key, value in (preferred_specs or {}).items()
            if value not in (None, "", [], "unknown")
        },
        "quantity": quantity,
        "route": route,
        "conversation_date": conversation_date,
        "budget_basis": budget_basis,
        "budget_vat_regime": budget_vat_regime,
    }
    if variant:
        row["variant"] = variant
    if contact_name:
        row["contact_name"] = contact_name
    if destination_country:
        row["destination_country"] = destination_country.upper()
    if required_by is not None:
        row["required_by"] = required_by
    if budget is not None:
        row["budget"] = {"amount": str(budget.decimal), "currency": budget.currency}
    if confirmed:
        row["confirmed_at"] = conversation_date
        row["confirmed_by"] = confirmed_by
    if evidence_note:
        row["evidence_note"] = evidence_note
    row["expires_at"] = expires_at or (
        conversation_date + timedelta(days=DEFAULT_VALIDITY_DAYS)
    )
    return row


def check(row: dict[str, Any]) -> tuple[BuyerBriefRow | None, list[RowProblem], list[str]]:
    """Validate a captured row and point out what is missing but not fatal."""
    valid, problems = validate_rows(BuyerBriefRow, [row])
    warnings: list[str] = []

    if not valid:
        return None, problems, warnings

    brief: BuyerBriefRow = valid[0]

    if brief.budget is None:
        warnings.append(
            "No budget was captured. The requirement is still useful, but no budget headroom "
            "can be checked against an asking price."
        )
    elif brief.budget_basis is PriceBasis.UNKNOWN or brief.budget_vat_regime is VatRegime.UNKNOWN:
        warnings.append(
            "The budget has no stated basis or VAT regime, so it cannot be compared with an "
            "asking price. Ask whether the figure is net or gross."
        )

    if not brief.required_specs:
        warnings.append(
            "No hard requirement was captured, so almost any car of this family will match on "
            "specification. Ask what would rule a car out."
        )

    if brief.required_by is None:
        warnings.append("No date was captured, so delivery timing cannot be scored.")

    if brief.confirmed_at is None:
        warnings.append(
            "This is recorded as discussed but not confirmed, so it will not reach a "
            "contact-ready state. Confirm it with the buyer before quoting anything."
        )

    if brief.quantity > 1:
        warnings.append(
            f"{brief.quantity} units are required. A single offer will always show the "
            f"remainder as unsourced."
        )

    return brief, problems, warnings


def capture(
    conn: sqlite3.Connection,
    *,
    row: dict[str, Any],
    source_id: str,
    company_source_id: str,
    config: Config,
    now: datetime,
) -> CaptureResult:
    """Validate and store one requirement, and log the conversation that produced it."""
    brief, problems, warnings = check(row)
    if brief is None:
        return CaptureResult(brief_id=None, created=False, problems=problems)

    company = companies_repo.find_company_by_external(
        conn, company_source_id, brief.company_external_id
    )
    if company is None:
        return CaptureResult(
            brief_id=None,
            created=False,
            problems=[
                RowProblem(
                    row_number=1,
                    external_id=brief.external_id,
                    message=(
                        f"company {brief.company_external_id!r} is not in this database; a "
                        f"requirement is never attached to an invented company"
                    ),
                )
            ],
        )

    contact_id = None
    if brief.contact_name:
        for contact in companies_repo.list_contacts(conn, company.id):
            if contact.full_name == brief.contact_name:
                contact_id = contact.id
                break

    brief_id, created = companies_repo.upsert_brief(
        conn,
        source_id=source_id,
        external_record_id=brief.external_id,
        company_id=company.id,
        now=to_iso(now),
        is_demo=config.is_demo,
        contact_id=contact_id,
        model_family=brief.model_family,
        variant=brief.variant,
        required_specs=brief.required_specs,
        preferred_specs=brief.preferred_specs,
        budget=brief.budget.to_money() if brief.budget else None,
        budget_basis=brief.budget_basis.value,
        budget_vat_regime=brief.budget_vat_regime.value,
        quantity=brief.quantity,
        destination_country=brief.destination_country,
        route=brief.route.value,
        required_by=None if brief.required_by is None else to_iso(brief.required_by),
        conversation_date=to_iso(brief.conversation_date),
        confirmed_at=None if brief.confirmed_at is None else to_iso(brief.confirmed_at),
        confirmed_by=brief.confirmed_by,
        expires_at=None if brief.expires_at is None else to_iso(brief.expires_at),
        evidence_note=brief.evidence_note,
    )

    if brief.confirmed_at is not None:
        supply_repo.insert_change_event(
            conn,
            dedupe_key=fingerprint(
                "requirement_confirmed", brief_id, to_iso(brief.confirmed_at)
            ),
            now=to_iso(now),
            is_demo=config.is_demo,
            company_id=company.id,
            kind="requirement_confirmed",
            field_name="buying_brief",
            new_value=f"{brief.model_family} confirmed by {brief.confirmed_by}",
            observed_at=to_iso(brief.confirmed_at),
            detected_at=to_iso(now),
        )

    return CaptureResult(
        brief_id=brief_id, created=created, problems=problems, warnings=warnings
    )


def suggested_external_id(company_external_id: str, model_family: str, at: datetime) -> str:
    return f"call-{fingerprint(company_external_id, model_family, to_iso(at))}"
