"""Validated imports: preview, validate, then apply transactionally.

An import always validates the whole batch first. The default on any row-level
error is to reject the entire batch, so a file can never be half applied. The
caller may explicitly choose to apply only the valid rows instead.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal

from ..clock import Clock, to_iso
from ..config import Config
from ..db import Database, new_id, record_change
from ..models import (
    BuyerBriefRow,
    CompanyRow,
    ContactPolicyRow,
    DeskModel,
    InteractionRow,
    ObservationRow,
    OfferRow,
    RowProblem,
    SourceRow,
    validate_rows,
)
from ..repositories import companies as companies_repo
from ..repositories import followup as followup_repo
from ..repositories import supply as supply_repo
from ..repositories.base import dumps, fingerprint
from . import identity, observations

Kind = Literal["companies", "offers", "buyer_briefs", "comparables", "interactions", "contact_policies"]

MODELS: dict[str, type[DeskModel]] = {
    "companies": CompanyRow,
    "offers": OfferRow,
    "buyer_briefs": BuyerBriefRow,
    "comparables": ObservationRow,
    "interactions": InteractionRow,
    "contact_policies": ContactPolicyRow,
}

# Documented flat column templates for CSV imports.
TEMPLATES: dict[str, list[str]] = {
    "companies": [
        "external_id", "legal_name", "trading_name", "country", "region", "city", "address",
        "registry_id", "registry_verified", "website", "category", "is_referral_partner",
        "buying_route", "buying_authority", "network_claim", "introduction_notes", "notes",
        "contact_name", "contact_role", "contact_email", "contact_phone", "contact_type",
    ],
    "offers": [
        "external_id", "model_family", "variant", "generation", "vin", "vin_verified",
        "model_year", "first_registration", "mileage_km", "powertrain", "steering", "seats",
        "specification_json", "seller_name", "seller_external_id", "listing_url",
        "price_amount", "price_raw", "price_currency", "price_locale", "price_basis",
        "vat_regime", "price_evidence_type", "status", "stock_kind", "location_country",
        "location_city", "authority_to_sell", "available_from", "valid_until",
        "availability_confirmed_at", "observed_at", "notes",
    ],
    "buyer_briefs": [
        "external_id", "company_external_id", "contact_name", "model_family", "variant",
        "required_specs_json", "preferred_specs_json", "budget_amount", "budget_currency",
        "budget_basis", "budget_vat_regime", "quantity", "destination_country", "route",
        "required_by", "conversation_date", "confirmed_at", "confirmed_by", "expires_at",
        "evidence_note",
    ],
    "comparables": [
        "external_id", "offer_external_id", "model_family", "variant", "vin", "price_amount",
        "price_raw", "price_currency", "price_locale", "price_basis", "vat_regime",
        "price_evidence_type", "mileage_km", "seats", "powertrain", "steering",
        "first_registration", "location_country", "status", "stock_kind", "state",
        "observed_at", "seller_name", "listing_url", "notes",
    ],
    "interactions": [
        "external_id", "company_external_id", "channel", "occurred_at", "outcome",
        "meeting_status", "notes", "recorded_by",
    ],
    "contact_policies": [
        "company_external_id", "channel", "purpose", "status", "market", "basis",
        "restrictions", "reviewer", "reviewed_at", "expires_at",
    ],
}


class ImportError_(ValueError):
    """Raised when a batch cannot be applied."""


@dataclass
class ValidationReport:
    kind: str
    valid: list[Any]
    problems: list[RowProblem]
    total_rows: int

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        if self.ok:
            return f"{self.total_rows} {self.kind} row(s) validated with no errors."
        return (
            f"{len(self.problems)} problem(s) in {self.total_rows} {self.kind} row(s); "
            f"{len(self.valid)} row(s) would be accepted."
        )


@dataclass
class ImportReport:
    kind: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    observations: int = 0
    events: int = 0
    duplicate_reviews: int = 0
    problems: list[RowProblem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    applied: bool = False

    def merge(self, other: "ImportReport") -> "ImportReport":
        return ImportReport(
            kind=f"{self.kind}+{other.kind}",
            created=self.created + other.created,
            updated=self.updated + other.updated,
            skipped=self.skipped + other.skipped,
            observations=self.observations + other.observations,
            events=self.events + other.events,
            duplicate_reviews=self.duplicate_reviews + other.duplicate_reviews,
            problems=[*self.problems, *other.problems],
            notes=[*self.notes, *other.notes],
            applied=self.applied and other.applied,
        )

    def summary(self) -> str:
        if not self.applied:
            return f"Nothing applied. {len(self.problems)} problem(s) found."
        parts = [f"{self.created} created", f"{self.updated} updated"]
        if self.observations:
            parts.append(f"{self.observations} observation(s) appended")
        if self.events:
            parts.append(f"{self.events} change alert(s)")
        if self.duplicate_reviews:
            parts.append(f"{self.duplicate_reviews} duplicate review(s) raised")
        if self.skipped:
            parts.append(f"{self.skipped} unchanged")
        return ", ".join(parts) + "."


# -- reading files -------------------------------------------------------


def read_rows(path: Path | str, *, kind: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Read a CSV or JSON import file into raw row dictionaries."""
    target = Path(path)
    if not target.exists():
        raise ImportError_(f"file not found: {target}")
    text = target.read_text(encoding="utf-8-sig")

    if target.suffix.lower() == ".json":
        payload = json.loads(text)
        if isinstance(payload, dict):
            if kind and kind in payload:
                return kind, list(payload[kind])
            for candidate in MODELS:
                if candidate in payload:
                    return candidate, list(payload[candidate])
            raise ImportError_(
                f"JSON object must contain one of: {', '.join(MODELS)}"
            )
        if kind is None:
            raise ImportError_("a JSON list import must state its kind")
        return kind, list(payload)

    if kind is None:
        raise ImportError_("a CSV import must state its kind")
    reader = csv.DictReader(io.StringIO(text))
    return kind, [dict(row) for row in reader]


def normalise_raw_row(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Map a flat CSV row onto the nested model shape.

    Empty cells become absent rather than empty strings, so "unknown" stays
    unknown instead of becoming a blank value that looks confirmed.
    """
    row = {
        key: value
        for key, value in raw.items()
        if key is not None and not (isinstance(value, str) and value.strip() == "")
    }

    for json_field, target in (
        ("specification_json", "specification"),
        ("required_specs_json", "required_specs"),
        ("preferred_specs_json", "preferred_specs"),
    ):
        if json_field in row:
            value = row.pop(json_field)
            row[target] = json.loads(value) if isinstance(value, str) else value

    money = _collect_money(row, prefix="price")
    if money:
        row["price"] = money
    budget = _collect_money(row, prefix="budget")
    if budget:
        row["budget"] = budget

    for int_field in ("mileage_km", "seats", "model_year", "quantity"):
        if int_field in row and isinstance(row[int_field], str):
            row[int_field] = int(float(row[int_field]))

    return row


def _collect_money(row: dict[str, Any], *, prefix: str) -> dict[str, Any] | None:
    keys = {
        "amount": f"{prefix}_amount",
        "raw_text": f"{prefix}_raw",
        "currency": f"{prefix}_currency",
        "locale": f"{prefix}_locale",
    }
    if not any(key in row for key in keys.values()):
        return None
    payload: dict[str, Any] = {}
    for target, column in keys.items():
        if column in row:
            payload[target] = row.pop(column)
    return payload or None


# Timestamps that describe something that has already happened. A source cannot
# have observed, or a person recorded, something that is still in the future.
PAST_ONLY_FIELDS: dict[str, tuple[str, ...]] = {
    "offers": ("observed_at", "availability_confirmed_at", "first_registration"),
    "comparables": ("observed_at", "first_registration"),
    "buyer_briefs": ("conversation_date", "confirmed_at"),
    "interactions": ("occurred_at",),
    "contact_policies": ("reviewed_at",),
}

# Tolerance for ordinary clock skew between this machine and a source.
FUTURE_TOLERANCE = timedelta(minutes=5)


def _future_timestamp_problems(
    kind: str, records: list[Any], *, now: datetime
) -> list[RowProblem]:
    fields = PAST_ONLY_FIELDS.get(kind, ())
    if not fields:
        return []
    cutoff = now + FUTURE_TOLERANCE
    problems: list[RowProblem] = []
    for index, record in enumerate(records, start=1):
        for name in fields:
            value = getattr(record, name, None)
            if isinstance(value, datetime) and value > cutoff:
                problems.append(
                    RowProblem(
                        row_number=index,
                        external_id=getattr(record, "external_id", None),
                        field=name,
                        message=(
                            f"{name} is in the future ({value.isoformat()}); a source cannot "
                            f"have observed something that has not happened yet"
                        ),
                    )
                )
    return problems


def validate_batch(
    kind: str, rows: Iterable[dict[str, Any]], *, now: datetime | None = None
) -> ValidationReport:
    """Validate a whole batch without touching the database.

    Passing ``now`` additionally rejects timestamps in the future, which needs a
    clock and so cannot live in the row models themselves.
    """
    if kind not in MODELS:
        raise ImportError_(f"unknown import kind {kind!r}; expected one of {', '.join(MODELS)}")
    prepared: list[dict[str, Any]] = []
    problems: list[RowProblem] = []
    raw_rows = list(rows)
    for index, raw in enumerate(raw_rows, start=1):
        try:
            prepared.append(normalise_raw_row(kind, raw))
        except Exception as exc:
            problems.append(
                RowProblem(
                    row_number=index,
                    external_id=str(raw.get("external_id")) if raw.get("external_id") else None,
                    message=f"could not read the row: {exc}",
                )
            )
            prepared.append({})
    valid, model_problems = validate_rows(MODELS[kind], prepared)
    future_problems = (
        _future_timestamp_problems(kind, valid, now=now) if now is not None else []
    )
    if future_problems:
        rejected = {
            (problem.row_number, problem.external_id) for problem in future_problems
        }
        valid = [
            record
            for index, record in enumerate(valid, start=1)
            if (index, getattr(record, "external_id", None)) not in rejected
        ]
    return ValidationReport(
        kind=kind,
        valid=valid,
        problems=[*problems, *model_problems, *future_problems],
        total_rows=len(raw_rows),
    )


# -- applying ------------------------------------------------------------


class Importer:
    """Applies validated rows inside one transaction."""

    def __init__(self, db: Database, config: Config, clock: Clock) -> None:
        self.db = db
        self.config = config
        self.clock = clock

    @property
    def is_demo(self) -> bool:
        return self.config.is_demo

    def apply(
        self,
        kind: str,
        rows: Iterable[dict[str, Any]],
        *,
        source_id: str,
        company_source_id: str | None = None,
        allow_partial: bool = False,
        run_id: str | None = None,
    ) -> ImportReport:
        """Validate then apply. Rejects the whole batch on error by default."""
        report = validate_batch(kind, rows, now=self.clock.now())
        if report.problems and not allow_partial:
            return ImportReport(
                kind=kind,
                problems=report.problems,
                applied=False,
                notes=[
                    "the batch was rejected in full because it contained errors; nothing was "
                    "written. Fix the rows or explicitly choose to apply only the valid ones."
                ],
            )
        if not report.valid:
            return ImportReport(
                kind=kind,
                problems=report.problems,
                applied=False,
                notes=["no valid rows to apply"],
            )

        with self.db.write() as conn:
            outcome = self._apply_rows(
                conn,
                kind,
                report.valid,
                source_id=source_id,
                company_source_id=company_source_id or source_id,
                run_id=run_id,
            )
        # Problems the handlers found (an unresolvable company reference, for
        # example) must survive alongside the validation problems, not be replaced.
        outcome.problems = [*report.problems, *outcome.problems]
        outcome.applied = True
        if report.problems:
            outcome.notes.append(
                f"{len(report.problems)} invalid row(s) were skipped at your explicit request"
            )
        return outcome

    # -- per-kind dispatch ---------------------------------------------
    def _apply_rows(
        self,
        conn: sqlite3.Connection,
        kind: str,
        rows: list[Any],
        *,
        source_id: str,
        company_source_id: str,
        run_id: str | None,
    ) -> ImportReport:
        handlers = {
            "companies": self._apply_companies,
            "offers": self._apply_offers,
            "buyer_briefs": self._apply_briefs,
            "comparables": self._apply_comparables,
            "interactions": self._apply_interactions,
            "contact_policies": self._apply_policies,
        }
        return handlers[kind](
            conn, rows, source_id=source_id, company_source_id=company_source_id, run_id=run_id
        )

    def _apply_companies(
        self,
        conn: sqlite3.Connection,
        rows: list[CompanyRow],
        *,
        source_id: str,
        company_source_id: str,
        run_id: str | None,
    ) -> ImportReport:
        report = ImportReport(kind="companies")
        now = self.clock.now_iso()
        pending_parents: list[tuple[str, str]] = []

        for row in rows:
            company_id, created = companies_repo.upsert_company(
                conn,
                source_id=source_id,
                external_record_id=row.external_id,
                now=now,
                is_demo=self.is_demo,
                legal_name=row.legal_name,
                trading_name=row.trading_name,
                country=row.country,
                region=row.region,
                city=row.city,
                address=row.address,
                registry_id=row.registry_id,
                registry_verified=row.registry_verified,
                website=row.website,
                is_branch=row.is_branch,
                category=row.category.value,
                is_referral_partner=row.is_referral_partner,
                buying_route=row.buying_route.value,
                buying_authority=row.buying_authority,
                network_claim=row.network_claim,
                introduction_notes=row.introduction_notes,
                notes=row.notes,
                review_status="reviewed" if row.registry_verified else "review_required",
            )
            report.created += int(created)
            report.updated += int(not created)

            if row.parent_external_id:
                pending_parents.append((company_id, row.parent_external_id))

            if row.has_contact:
                companies_repo.upsert_contact(
                    conn,
                    company_id=company_id,
                    now=now,
                    is_demo=self.is_demo,
                    full_name=row.contact_name,
                    role_title=row.contact_role,
                    business_email=row.contact_email,
                    business_phone=row.contact_phone,
                    contact_type=row.contact_type,
                )
                # Discovering a published address never grants permission to use it.
                _policy_id, _policy_created = companies_repo.set_contact_policy(
                    conn,
                    company_id=company_id,
                    channel="email" if row.contact_email else "phone",
                    purpose="business enquiry about vehicle supply or requirements",
                    status="review_required",
                    now=now,
                    is_demo=self.is_demo,
                    basis=None,
                    restrictions="imported contact detail; a human must review before any use",
                )

            if created:
                if identity.suggest_company_duplicate(
                    conn,
                    company_id=company_id,
                    legal_name=row.legal_name,
                    country=row.country,
                    website=row.website,
                    now=now,
                    is_demo=self.is_demo,
                ):
                    report.duplicate_reviews += 1

            record_change(
                conn,
                entity_type="company",
                entity_id=company_id,
                action="created" if created else "updated",
                actor="import",
                run_id=run_id,
                occurred_at=now,
                is_demo=self.is_demo,
            )

        for child_id, parent_external in pending_parents:
            parent = companies_repo.find_company_by_external(conn, company_source_id, parent_external)
            if parent is not None:
                conn.execute(
                    "UPDATE companies SET parent_company_id = ?, is_branch = 1 WHERE id = ?",
                    (parent.id, child_id),
                )
            else:
                report.notes.append(
                    f"parent company {parent_external!r} was not found in this batch; the branch "
                    f"link was left unset rather than guessed"
                )
        return report

    def _apply_offers(
        self,
        conn: sqlite3.Connection,
        rows: list[OfferRow],
        *,
        source_id: str,
        company_source_id: str,
        run_id: str | None,
    ) -> ImportReport:
        report = ImportReport(kind="offers")
        now = self.clock.now()
        now_iso = to_iso(now)

        for row in rows:
            existing = supply_repo.find_offer_by_external(conn, source_id, row.external_id)
            vehicle_id = existing.vehicle_id if existing else None

            if vehicle_id is None:
                resolution = identity.resolve_vehicle(
                    conn, row, now=now_iso, is_demo=self.is_demo
                )
                vehicle_id = resolution.vehicle_id
                if resolution.duplicate_review_id:
                    report.duplicate_reviews += 1
                for note in resolution.notes:
                    report.notes.append(f"{row.external_id}: {note}")

            seller_company_id = None
            if row.seller_external_id:
                seller = companies_repo.find_company_by_external(
                    conn, company_source_id, row.seller_external_id
                )
                seller_company_id = seller.id if seller else None

            offer_id, created, changed = supply_repo.upsert_offer(
                conn,
                source_id=source_id,
                external_record_id=row.external_id,
                now=now_iso,
                is_demo=self.is_demo,
                vehicle_id=vehicle_id,
                seller_company_id=seller_company_id,
                seller_name=row.seller_name,
                listing_url=row.listing_url,
                price=row.price.to_money() if row.price else None,
                price_basis=row.price_basis.value,
                vat_regime=row.vat_regime.value,
                price_evidence_type=row.price_evidence_type.value,
                raw_price_text=None if row.price is None else row.price.raw_text,
                status=row.status.value,
                stock_kind=row.stock_kind.value,
                location_country=row.location_country,
                location_city=row.location_city,
                authority_to_sell=row.authority_to_sell,
                available_from=None if row.available_from is None else to_iso(row.available_from),
                valid_until=None if row.valid_until is None else to_iso(row.valid_until),
                availability_confirmed_at=(
                    None
                    if row.availability_confirmed_at is None
                    else to_iso(row.availability_confirmed_at)
                ),
                last_observation_state="seen",
                last_seen_at=to_iso(row.observed_at),
                notes=row.notes,
            )
            report.created += int(created)
            report.updated += int(not created)

            response_hash = observations.snapshot_hash(row.model_dump(mode="json"))
            observation_id, events = observations.record_offer_observation(
                conn,
                source_id=source_id,
                row=row,
                offer_id=offer_id,
                now=now,
                is_demo=self.is_demo,
                response_hash=response_hash,
                run_id=run_id,
            )
            if observation_id:
                report.observations += 1
            else:
                report.skipped += 1
            report.events += len(events)

            for field_name, (old, new) in changed.items():
                record_change(
                    conn,
                    entity_type="offer",
                    entity_id=offer_id,
                    action="updated",
                    field_name=field_name,
                    old_value=None if old is None else str(old),
                    new_value=None if new is None else str(new),
                    actor="import",
                    run_id=run_id,
                    occurred_at=now_iso,
                    is_demo=self.is_demo,
                )
        return report

    def _apply_briefs(
        self,
        conn: sqlite3.Connection,
        rows: list[BuyerBriefRow],
        *,
        source_id: str,
        company_source_id: str,
        run_id: str | None,
    ) -> ImportReport:
        report = ImportReport(kind="buyer_briefs")
        now = self.clock.now_iso()

        for row in rows:
            company = companies_repo.find_company_by_external(
                conn, company_source_id, row.company_external_id
            )
            if company is None:
                report.problems.append(
                    RowProblem(
                        row_number=0,
                        external_id=row.external_id,
                        message=(
                            f"company {row.company_external_id!r} is not in this database; a "
                            f"requirement is never attached to an invented company"
                        ),
                    )
                )
                continue

            contact_id = None
            if row.contact_name:
                for contact in companies_repo.list_contacts(conn, company.id):
                    if contact.full_name == row.contact_name:
                        contact_id = contact.id
                        break

            brief_id, created = companies_repo.upsert_brief(
                conn,
                source_id=source_id,
                external_record_id=row.external_id,
                company_id=company.id,
                now=now,
                is_demo=self.is_demo,
                contact_id=contact_id,
                model_family=row.model_family,
                variant=row.variant,
                required_specs=row.required_specs,
                preferred_specs=row.preferred_specs,
                budget=row.budget.to_money() if row.budget else None,
                budget_basis=row.budget_basis.value,
                budget_vat_regime=row.budget_vat_regime.value,
                quantity=row.quantity,
                destination_country=row.destination_country,
                route=row.route.value,
                required_by=None if row.required_by is None else to_iso(row.required_by),
                conversation_date=to_iso(row.conversation_date),
                confirmed_at=None if row.confirmed_at is None else to_iso(row.confirmed_at),
                confirmed_by=row.confirmed_by,
                expires_at=None if row.expires_at is None else to_iso(row.expires_at),
                evidence_note=row.evidence_note,
            )
            report.created += int(created)
            report.updated += int(not created)

            if row.confirmed_at:
                supply_repo.insert_change_event(
                    conn,
                    dedupe_key=fingerprint("requirement_confirmed", brief_id, to_iso(row.confirmed_at)),
                    now=now,
                    is_demo=self.is_demo,
                    company_id=company.id,
                    kind="requirement_confirmed",
                    field_name="buying_brief",
                    new_value=f"{row.model_family} confirmed by {row.confirmed_by}",
                    observed_at=to_iso(row.confirmed_at),
                    detected_at=now,
                )
        return report

    def _apply_comparables(
        self,
        conn: sqlite3.Connection,
        rows: list[ObservationRow],
        *,
        source_id: str,
        company_source_id: str,
        run_id: str | None,
    ) -> ImportReport:
        """Comparable observations.

        Each comparable is an offer in its own right, so it gets its own offer
        record plus an appended observation. That is what lets the comparable set
        deduplicate by vehicle identity later.
        """
        report = ImportReport(kind="comparables")
        now = self.clock.now()
        now_iso = to_iso(now)

        for row in rows:
            offer_external = row.offer_external_id or row.external_id
            existing = supply_repo.find_offer_by_external(conn, source_id, offer_external)

            if existing is None:
                offer_row = OfferRow(
                    external_id=offer_external,
                    model_family=row.model_family or "unknown",
                    variant=row.variant,
                    vin=row.vin,
                    vin_verified=bool(row.vin),
                    mileage_km=row.mileage_km,
                    seats=row.seats,
                    powertrain=row.powertrain,
                    steering=row.steering,
                    first_registration=row.first_registration,
                    seller_name=row.seller_name,
                    listing_url=row.listing_url,
                    price=row.price,
                    price_basis=row.price_basis,
                    vat_regime=row.vat_regime,
                    price_evidence_type=row.price_evidence_type,
                    status=row.status,
                    stock_kind=row.stock_kind,
                    location_country=row.location_country,
                    observed_at=row.observed_at,
                    notes=row.notes,
                )
                resolution = identity.resolve_vehicle(
                    conn, offer_row, now=now_iso, is_demo=self.is_demo
                )
                if resolution.duplicate_review_id:
                    report.duplicate_reviews += 1
                offer_id, created, _ = supply_repo.upsert_offer(
                    conn,
                    source_id=source_id,
                    external_record_id=offer_external,
                    now=now_iso,
                    is_demo=self.is_demo,
                    vehicle_id=resolution.vehicle_id,
                    seller_name=row.seller_name,
                    listing_url=row.listing_url,
                    price=row.price.to_money() if row.price else None,
                    price_basis=row.price_basis.value,
                    vat_regime=row.vat_regime.value,
                    price_evidence_type=row.price_evidence_type.value,
                    raw_price_text=None if row.price is None else row.price.raw_text,
                    status=row.status.value,
                    stock_kind=row.stock_kind.value,
                    location_country=row.location_country,
                    last_observation_state=row.state,
                    last_seen_at=to_iso(row.observed_at),
                )
                report.created += int(created)
            else:
                offer_id = existing.id
                report.updated += 1

            observation_id = observations.record_comparable_observation(
                conn,
                source_id=source_id,
                row=row,
                offer_id=offer_id,
                now=now,
                is_demo=self.is_demo,
                response_hash=observations.snapshot_hash(row.model_dump(mode="json")),
                run_id=run_id,
            )
            if observation_id:
                report.observations += 1
            else:
                report.skipped += 1
        return report

    def _apply_interactions(
        self,
        conn: sqlite3.Connection,
        rows: list[InteractionRow],
        *,
        source_id: str,
        company_source_id: str,
        run_id: str | None,
    ) -> ImportReport:
        report = ImportReport(kind="interactions")
        now = self.clock.now_iso()
        for row in rows:
            company = companies_repo.find_company_by_external(
                conn, company_source_id, row.company_external_id
            )
            if company is None:
                report.problems.append(
                    RowProblem(
                        row_number=0,
                        external_id=row.external_id,
                        message=f"company {row.company_external_id!r} is not in this database",
                    )
                )
                continue
            interaction_id = followup_repo.record_interaction(
                conn,
                company_id=company.id,
                channel=row.channel,
                occurred_at=to_iso(row.occurred_at),
                outcome=row.outcome,
                now=now,
                is_demo=self.is_demo,
                meeting_status=row.meeting_status,
                notes=row.notes,
                recorded_by=row.recorded_by,
                source_id=source_id,
                external_record_id=row.external_id,
            )
            if interaction_id is None:
                report.skipped += 1
                continue
            report.created += 1

            # An objection overrides future outreach actions regardless of score.
            if row.outcome == "objection":
                companies_repo.add_suppression(
                    conn,
                    company_id=company.id,
                    reason=f"objection recorded on {row.occurred_at.date().isoformat()}: {row.notes or 'no detail given'}",
                    now=now,
                    is_demo=self.is_demo,
                    recorded_by=row.recorded_by,
                )
                report.notes.append(
                    f"{company.legal_name}: objection recorded, so the account is suppressed "
                    f"from outreach regardless of any match score"
                )
        return report

    def _apply_policies(
        self,
        conn: sqlite3.Connection,
        rows: list[ContactPolicyRow],
        *,
        source_id: str,
        company_source_id: str,
        run_id: str | None,
    ) -> ImportReport:
        report = ImportReport(kind="contact_policies")
        now = self.clock.now_iso()
        for row in rows:
            company = companies_repo.find_company_by_external(
                conn, company_source_id, row.company_external_id
            )
            if company is None:
                report.problems.append(
                    RowProblem(
                        row_number=0,
                        external_id=row.company_external_id,
                        message="company not found; a policy is never attached to an unknown company",
                    )
                )
                continue
            _policy_id, created = companies_repo.set_contact_policy(
                conn,
                company_id=company.id,
                channel=row.channel,
                purpose=row.purpose,
                status=row.status.value,
                now=now,
                is_demo=self.is_demo,
                market=row.market,
                basis=row.basis,
                restrictions=row.restrictions,
                reviewer=row.reviewer,
                reviewed_at=None if row.reviewed_at is None else to_iso(row.reviewed_at),
                expires_at=None if row.expires_at is None else to_iso(row.expires_at),
            )
            report.created += int(created)
            report.updated += int(not created)
        return report


# -- sources -------------------------------------------------------------


def upsert_sources(
    db: Database, config: Config, clock: Clock, rows: Iterable[dict[str, Any]]
) -> list[str]:
    """Write source register entries. Credentials are never stored here."""
    validated = [SourceRow.model_validate(row) for row in rows]
    now = clock.now_iso()
    ids: list[str] = []
    with db.write() as conn:
        for row in validated:
            conn.execute(
                """INSERT INTO sources
                   (id, name, category, access_mode, base_url, documentation_url, allowed_hosts,
                    allowed_paths, approved_use, attribution, retention_rule, retention_days,
                    raw_retention_allowed, export_allowed, rate_limit_per_minute, max_concurrency,
                    status, approval_evidence, reviewer, reviewed_at, review_due_at, notes,
                    is_demo, created_at, updated_at)
                   VALUES (:id,:name,:category,:access_mode,:base_url,:documentation_url,
                    :allowed_hosts,:allowed_paths,:approved_use,:attribution,:retention_rule,
                    :retention_days,:raw_retention_allowed,:export_allowed,:rate_limit_per_minute,
                    :max_concurrency,:status,:approval_evidence,:reviewer,:reviewed_at,
                    :review_due_at,:notes,:is_demo,:created_at,:updated_at)
                   ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, category=excluded.category,
                    access_mode=excluded.access_mode, base_url=excluded.base_url,
                    documentation_url=excluded.documentation_url,
                    allowed_hosts=excluded.allowed_hosts, allowed_paths=excluded.allowed_paths,
                    approved_use=excluded.approved_use, attribution=excluded.attribution,
                    retention_rule=excluded.retention_rule, retention_days=excluded.retention_days,
                    raw_retention_allowed=excluded.raw_retention_allowed,
                    export_allowed=excluded.export_allowed,
                    rate_limit_per_minute=excluded.rate_limit_per_minute,
                    max_concurrency=excluded.max_concurrency, status=excluded.status,
                    approval_evidence=excluded.approval_evidence, reviewer=excluded.reviewer,
                    reviewed_at=excluded.reviewed_at, review_due_at=excluded.review_due_at,
                    notes=excluded.notes, updated_at=excluded.updated_at""",
                {
                    "id": row.id,
                    "name": row.name,
                    "category": row.category,
                    "access_mode": row.access_mode,
                    "base_url": row.base_url,
                    "documentation_url": row.documentation_url,
                    "allowed_hosts": dumps(row.allowed_hosts),
                    "allowed_paths": dumps(row.allowed_paths),
                    "approved_use": row.approved_use,
                    "attribution": row.attribution,
                    "retention_rule": row.retention_rule,
                    "retention_days": row.retention_days,
                    "raw_retention_allowed": 1 if row.raw_retention_allowed else 0,
                    "export_allowed": 1 if row.export_allowed else 0,
                    "rate_limit_per_minute": row.rate_limit_per_minute,
                    "max_concurrency": row.max_concurrency,
                    "status": row.status,
                    "approval_evidence": row.approval_evidence,
                    "reviewer": row.reviewer,
                    "reviewed_at": None if row.reviewed_at is None else to_iso(row.reviewed_at),
                    "review_due_at": None
                    if row.review_due_at is None
                    else to_iso(row.review_due_at),
                    "notes": row.notes,
                    "is_demo": 1 if config.is_demo else 0,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            ids.append(row.id)
    return ids


# -- runs ----------------------------------------------------------------


def start_run(db: Database, config: Config, clock: Clock, *, kind: str, connector_id: str | None = None,
              connector_version: str | None = None) -> str:
    run_id = new_id("run")
    with db.write() as conn:
        conn.execute(
            """INSERT INTO runs
               (id, kind, connector_id, connector_version, mode, started_at, status, is_demo)
               VALUES (?,?,?,?,?,?, 'running', ?)""",
            (
                run_id,
                kind,
                connector_id,
                connector_version,
                config.mode.value,
                clock.now_iso(),
                1 if config.is_demo else 0,
            ),
        )
    return run_id


def finish_run(
    db: Database,
    clock: Clock,
    run_id: str,
    *,
    report: ImportReport,
    status: str = "ok",
) -> None:
    with db.write() as conn:
        conn.execute(
            """UPDATE runs SET finished_at = ?, records_seen = ?, records_created = ?,
               records_updated = ?, records_skipped = ?, errors = ?, status = ? WHERE id = ?""",
            (
                clock.now_iso(),
                report.created + report.updated + report.skipped,
                report.created,
                report.updated,
                report.skipped,
                dumps([p.render() for p in report.problems]),
                status,
                run_id,
            ),
        )


# -- exports -------------------------------------------------------------


def export_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Render rows as CSV with formula-like cells neutralised."""
    from .normalization import neutralise_csv_cell

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([neutralise_csv_cell(row.get(column)) for column in columns])
    return buffer.getvalue()


def template_csv(kind: str) -> str:
    """The documented column template for one import kind."""
    if kind not in TEMPLATES:
        raise ImportError_(f"no template for {kind!r}")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TEMPLATES[kind])
    return buffer.getvalue()
