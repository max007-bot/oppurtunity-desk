"""Identity and duplicate handling.

A verified VIN links different listings to one vehicle. Anything weaker - similar
specification, a matching price, the same photographs - produces a review
suggestion, never an automatic merge. Multiple offers from different sellers stay
distinct records even when they describe one car.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..models import OfferRow
from ..repositories import companies as companies_repo
from ..repositories import supply as supply_repo
from .normalization import resolve_model, slugify

# How close two VIN-less cars must be before a duplicate review is suggested.
# Deliberately tight: a loose threshold buries the real duplicates in a queue of
# genuinely different cars that merely share a badge and a rough age.
MILEAGE_SIMILARITY_KM = 1_000
REGISTRATION_SIMILARITY_DAYS = 14
# All three signals must agree before a human is asked to look.
MIN_DUPLICATE_SIGNALS = 3


@dataclass
class VehicleResolution:
    vehicle_id: str
    created: bool
    review_status: str
    duplicate_review_id: str | None = None
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


def resolve_vehicle(
    conn: sqlite3.Connection,
    row: OfferRow,
    *,
    now: str,
    is_demo: bool,
    declared_family: str | None = None,
) -> VehicleResolution:
    """Find or create the vehicle identity behind one offer row."""
    text = " ".join(part for part in [row.model_family, row.variant, row.generation] if part)
    resolution = resolve_model(text, declared_family=declared_family or None)

    if resolution.resolved:
        family = resolution.family
        review_status = "reviewed"
        notes: list[str] = []
    else:
        # Keep the source text as a slug so nothing is invented, and send it to review.
        family = slugify(row.model_family) or "unknown"
        review_status = "review_required"
        notes = list(resolution.reasons)

    if row.stock_kind.value == "allocation" and not row.vin:
        review_status = "allocation_no_vin"
        notes.append("an allocation with no unique VIN cannot be a confirmed single identity")

    # A verified VIN is the one strong link between listings.
    if row.vin and row.vin_verified:
        existing = supply_repo.find_vehicle_by_vin(conn, row.vin)
        if existing is not None:
            updates = _spec_updates(existing, row)
            if updates:
                supply_repo.update_vehicle(conn, existing.id, now=now, **updates)
            return VehicleResolution(
                existing.id,
                False,
                existing.identity_review_status,
                notes=[
                    f"linked to the existing reviewed vehicle {existing.id} by verified VIN; "
                    f"the two offers stay separate records"
                ],
            )

    vehicle_id = supply_repo.insert_vehicle(
        conn,
        now=now,
        is_demo=is_demo,
        vin=row.vin,
        vin_verified=row.vin_verified,
        identity_review_status=review_status,
        model_family=family,
        variant=row.variant,
        generation=resolution.generation or row.generation,
        model_year=row.model_year,
        first_registration=None if row.first_registration is None else row.first_registration.isoformat(),
        mileage_km=row.mileage_km,
        powertrain=row.powertrain,
        steering=row.steering,
        seats=row.seats,
        specification=row.specification,
        notes="; ".join(notes) or None,
    )

    duplicate_id = None
    if not (row.vin and row.vin_verified):
        duplicate_id = _suggest_duplicate(
            conn, vehicle_id=vehicle_id, row=row, family=family, now=now, is_demo=is_demo
        )

    return VehicleResolution(vehicle_id, True, review_status, duplicate_id, notes)


def _spec_updates(existing: supply_repo.Vehicle, row: OfferRow) -> dict[str, Any]:
    """Fill genuine gaps on a reviewed vehicle; never overwrite a known value."""
    updates: dict[str, Any] = {}
    if existing.mileage_km is None and row.mileage_km is not None:
        updates["mileage_km"] = row.mileage_km
    if existing.seats is None and row.seats is not None:
        updates["seats"] = row.seats
    if not existing.powertrain and row.powertrain:
        updates["powertrain"] = row.powertrain
    if existing.steering in (None, "unknown") and row.steering != "unknown":
        updates["steering"] = row.steering
    if existing.first_registration is None and row.first_registration is not None:
        updates["first_registration"] = row.first_registration.isoformat()
    if row.specification:
        merged = {**row.specification, **(existing.specification or {})}
        if merged != (existing.specification or {}):
            updates["specification"] = merged
    return updates


def _suggest_duplicate(
    conn: sqlite3.Connection,
    *,
    vehicle_id: str,
    row: OfferRow,
    family: str,
    now: str,
    is_demo: bool,
) -> str | None:
    """Flag a plausible same-car pair for a human to decide."""
    candidates = conn.execute(
        "SELECT * FROM vehicles WHERE model_family = ? AND id != ? AND (vin IS NULL OR vin_verified = 0)",
        (family, vehicle_id),
    ).fetchall()

    for candidate in candidates:
        signals: dict[str, Any] = {}
        score = 0

        if row.mileage_km is not None and candidate["mileage_km"] is not None:
            gap = abs(row.mileage_km - candidate["mileage_km"])
            if gap <= MILEAGE_SIMILARITY_KM:
                score += 1
                signals["mileage_gap_km"] = gap

        if row.first_registration is not None and candidate["first_registration"]:
            from ..clock import from_iso

            other = from_iso(candidate["first_registration"])
            if other is not None:
                gap_days = abs((row.first_registration - other).days)
                if gap_days <= REGISTRATION_SIMILARITY_DAYS:
                    score += 1
                    signals["registration_gap_days"] = gap_days

        if row.variant and candidate["variant"] and slugify(row.variant) == slugify(candidate["variant"]):
            score += 1
            signals["variant"] = row.variant

        if score >= MIN_DUPLICATE_SIGNALS:
            return companies_repo.record_duplicate_review(
                conn,
                entity_type="vehicle",
                left_id=vehicle_id,
                right_id=candidate["id"],
                reason=(
                    "near-identical specification, mileage and registration date with no "
                    "verified VIN on either record: a possible duplicate for review, not "
                    "identity proof"
                ),
                now=now,
                is_demo=is_demo,
                signals=signals,
            )
    return None


def suggest_company_duplicate(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    legal_name: str,
    country: str,
    website: str | None,
    now: str,
    is_demo: bool,
) -> str | None:
    """Suggest a company review on a shared domain or a near-identical name.

    Branches are expected to share a domain with their parent, so this is a
    suggestion for a human, not a merge.
    """
    if website:
        domain = _domain(website)
        if domain:
            rows = conn.execute(
                "SELECT id, legal_name, website FROM companies WHERE id != ? AND website LIKE ?",
                (company_id, f"%{domain}%"),
            ).fetchall()
            for row in rows:
                return companies_repo.record_duplicate_review(
                    conn,
                    entity_type="company",
                    left_id=company_id,
                    right_id=row["id"],
                    reason=(
                        f"shares the domain {domain} with {row['legal_name']}; confirm whether "
                        f"this is a branch or the same purchasing entity"
                    ),
                    now=now,
                    is_demo=is_demo,
                    signals={"domain": domain},
                )

    rows = conn.execute(
        "SELECT id, legal_name FROM companies WHERE id != ? AND country = ?",
        (company_id, country.upper()),
    ).fetchall()
    target = slugify(legal_name)
    for row in rows:
        if slugify(row["legal_name"]) == target:
            return companies_repo.record_duplicate_review(
                conn,
                entity_type="company",
                left_id=company_id,
                right_id=row["id"],
                reason=(
                    f"identical trading name in {country}; verify with the registry identifier "
                    f"before treating them as one buyer"
                ),
                now=now,
                is_demo=is_demo,
                signals={"name": legal_name},
            )
    return None


def _domain(website: str) -> str | None:
    from urllib.parse import urlparse

    text = website.strip()
    if "//" not in text:
        text = f"https://{text}"
    host = urlparse(text).hostname
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def merge_decision(
    conn: sqlite3.Connection, review_id: str, *, decision: str, decided_by: str, now: str
) -> None:
    """Record a human decision on a duplicate review.

    The application stores the decision; it does not merge automatically, so the
    two records and their separate offers remain inspectable.
    """
    if decision not in {"same", "different", "deferred"}:
        raise ValueError("decision must be same, different or deferred")
    conn.execute(
        "UPDATE duplicate_reviews SET decision = ?, decided_by = ?, decided_at = ? WHERE id = ?",
        (decision, decided_by, now, review_id),
    )
