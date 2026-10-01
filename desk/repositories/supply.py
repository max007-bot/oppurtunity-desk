"""Vehicles, offers, append-only observations and change events."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..clock import from_iso
from ..config import ObservationState, OfferStatus, StockKind
from ..db import new_id
from ..money import Money
from .base import dumps, loads, money_from


# -- vehicles ------------------------------------------------------------


@dataclass
class Vehicle:
    id: str
    model_family: str
    variant: str | None = None
    generation: str | None = None
    vin: str | None = None
    vin_verified: bool = False
    identity_review_status: str = "review_required"
    model_year: int | None = None
    first_registration: datetime | None = None
    mileage_km: int | None = None
    powertrain: str | None = None
    steering: str | None = None
    seats: int | None = None
    specification: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Vehicle":
        return cls(
            id=row["id"],
            model_family=row["model_family"],
            variant=row["variant"],
            generation=row["generation"],
            vin=row["vin"],
            vin_verified=bool(row["vin_verified"]),
            identity_review_status=row["identity_review_status"],
            model_year=row["model_year"],
            first_registration=from_iso(row["first_registration"]),
            mileage_km=row["mileage_km"],
            powertrain=row["powertrain"],
            steering=row["steering"],
            seats=row["seats"],
            specification=loads(row["specification"], {}),
            notes=row["notes"],
        )


def find_vehicle_by_vin(conn: sqlite3.Connection, vin: str) -> Vehicle | None:
    row = conn.execute(
        "SELECT * FROM vehicles WHERE vin = ? AND vin_verified = 1", (vin,)
    ).fetchone()
    return None if row is None else Vehicle.from_row(row)


def get_vehicle(conn: sqlite3.Connection, vehicle_id: str) -> Vehicle | None:
    row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    return None if row is None else Vehicle.from_row(row)


def insert_vehicle(
    conn: sqlite3.Connection, *, now: str, is_demo: bool, **fields: Any
) -> str:
    vehicle_id = new_id("veh")
    conn.execute(
        """INSERT INTO vehicles
           (id, vin, vin_verified, identity_review_status, model_family, variant, generation,
            model_year, first_registration, mileage_km, powertrain, steering, seats,
            specification, notes, is_demo, created_at, updated_at)
           VALUES (:id, :vin, :vin_verified, :identity_review_status, :model_family, :variant,
            :generation, :model_year, :first_registration, :mileage_km, :powertrain, :steering,
            :seats, :specification, :notes, :is_demo, :created_at, :updated_at)""",
        {
            "id": vehicle_id,
            "vin": fields.get("vin"),
            "vin_verified": 1 if fields.get("vin_verified") else 0,
            "identity_review_status": fields.get("identity_review_status", "review_required"),
            "model_family": fields["model_family"],
            "variant": fields.get("variant"),
            "generation": fields.get("generation"),
            "model_year": fields.get("model_year"),
            "first_registration": fields.get("first_registration"),
            "mileage_km": fields.get("mileage_km"),
            "powertrain": fields.get("powertrain"),
            "steering": fields.get("steering", "unknown"),
            "seats": fields.get("seats"),
            "specification": dumps(fields.get("specification") or {}),
            "notes": fields.get("notes"),
            "is_demo": 1 if is_demo else 0,
            "created_at": now,
            "updated_at": now,
        },
    )
    return vehicle_id


def update_vehicle(conn: sqlite3.Connection, vehicle_id: str, *, now: str, **fields: Any) -> None:
    if not fields:
        return
    payload = dict(fields)
    if "specification" in payload and isinstance(payload["specification"], dict):
        payload["specification"] = dumps(payload["specification"])
    if "vin_verified" in payload:
        payload["vin_verified"] = 1 if payload["vin_verified"] else 0
    payload["updated_at"] = now
    assignments = ", ".join(f"{key} = :{key}" for key in payload)
    conn.execute(
        f"UPDATE vehicles SET {assignments} WHERE id = :id", {**payload, "id": vehicle_id}
    )


# -- offers --------------------------------------------------------------


@dataclass
class Offer:
    id: str
    source_id: str
    external_record_id: str
    vehicle_id: str | None
    seller_company_id: str | None
    seller_name: str | None
    listing_url: str | None
    price: Money | None
    price_basis: str
    vat_regime: str
    price_evidence_type: str
    raw_price_text: str | None
    status: str
    stock_kind: str
    location_country: str | None
    location_city: str | None
    authority_to_sell: str
    available_from: datetime | None
    valid_until: datetime | None
    availability_confirmed_at: datetime | None
    last_observation_state: str
    last_seen_at: datetime | None
    notes: str | None = None

    @property
    def is_allocation(self) -> bool:
        return self.stock_kind == StockKind.ALLOCATION.value or self.status == OfferStatus.ALLOCATION.value

    @property
    def is_physical_stock(self) -> bool:
        return self.stock_kind == StockKind.PHYSICAL_STOCK.value

    def is_expired(self, now: datetime) -> bool:
        return self.valid_until is not None and self.valid_until <= now

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Offer":
        return cls(
            id=row["id"],
            source_id=row["source_id"],
            external_record_id=row["external_record_id"],
            vehicle_id=row["vehicle_id"],
            seller_company_id=row["seller_company_id"],
            seller_name=row["seller_name"],
            listing_url=row["listing_url"],
            price=money_from(row["price_minor"], row["price_currency"]),
            price_basis=row["price_basis"],
            vat_regime=row["vat_regime"],
            price_evidence_type=row["price_evidence_type"],
            raw_price_text=row["raw_price_text"],
            status=row["status"],
            stock_kind=row["stock_kind"],
            location_country=row["location_country"],
            location_city=row["location_city"],
            authority_to_sell=row["authority_to_sell"],
            available_from=from_iso(row["available_from"]),
            valid_until=from_iso(row["valid_until"]),
            availability_confirmed_at=from_iso(row["availability_confirmed_at"]),
            last_observation_state=row["last_observation_state"],
            last_seen_at=from_iso(row["last_seen_at"]),
            notes=row["notes"],
        )


def find_offer_by_external(
    conn: sqlite3.Connection, source_id: str, external_id: str
) -> Offer | None:
    """(source_id, external_record_id) is how a repeated source record is recognised."""
    row = conn.execute(
        "SELECT * FROM offers WHERE source_id = ? AND external_record_id = ?",
        (source_id, external_id),
    ).fetchone()
    return None if row is None else Offer.from_row(row)


def get_offer(conn: sqlite3.Connection, offer_id: str) -> Offer | None:
    row = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
    return None if row is None else Offer.from_row(row)


def list_offers(
    conn: sqlite3.Connection,
    *,
    model_family: str | None = None,
    location_country: str | None = None,
    price_basis: str | None = None,
    statuses: tuple[str, ...] | None = None,
    evidence_types: tuple[str, ...] | None = None,
) -> list[Offer]:
    clauses: list[str] = []
    params: list[Any] = []
    joins = ""
    if model_family:
        joins = "LEFT JOIN vehicles v ON v.id = offers.vehicle_id"
        clauses.append("v.model_family = ?")
        params.append(model_family)
    if location_country:
        clauses.append("offers.location_country = ?")
        params.append(location_country.upper())
    if price_basis:
        clauses.append("offers.price_basis = ?")
        params.append(price_basis)
    if statuses:
        clauses.append(f"offers.status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    if evidence_types:
        clauses.append(f"offers.price_evidence_type IN ({','.join('?' * len(evidence_types))})")
        params.extend(evidence_types)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT offers.* FROM offers {joins} {where} ORDER BY offers.updated_at DESC", params
    ).fetchall()
    return [Offer.from_row(row) for row in rows]


def upsert_offer(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    external_record_id: str,
    now: str,
    is_demo: bool,
    **fields: Any,
) -> tuple[str, bool, dict[str, tuple[Any, Any]]]:
    """Insert or update an offer, returning the changed fields.

    The caller uses the change map to raise explainable alerts. Reviewed facts on
    the vehicle are untouched here: this only updates the offer's own columns.
    """
    existing = find_offer_by_external(conn, source_id, external_record_id)
    price: Money | None = fields.get("price")

    payload = {
        "vehicle_id": fields.get("vehicle_id"),
        "seller_company_id": fields.get("seller_company_id"),
        "seller_name": fields.get("seller_name"),
        "listing_url": fields.get("listing_url"),
        "price_minor": None if price is None else price.minor_units,
        "price_currency": None if price is None else price.currency,
        "price_basis": fields.get("price_basis", "unknown"),
        "vat_regime": fields.get("vat_regime", "unknown"),
        "price_evidence_type": fields.get("price_evidence_type", "supply_asking"),
        "raw_price_text": fields.get("raw_price_text"),
        "status": fields.get("status", "unknown"),
        "stock_kind": fields.get("stock_kind", "unknown"),
        "location_country": fields.get("location_country"),
        "location_city": fields.get("location_city"),
        "authority_to_sell": fields.get("authority_to_sell", "unknown"),
        "available_from": fields.get("available_from"),
        "valid_until": fields.get("valid_until"),
        "availability_confirmed_at": fields.get("availability_confirmed_at"),
        "last_observation_state": fields.get("last_observation_state", "seen"),
        "last_seen_at": fields.get("last_seen_at"),
        "notes": fields.get("notes"),
        "updated_at": now,
    }

    if existing is None:
        offer_id = new_id("ofr")
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(f":{key}" for key in payload)
        conn.execute(
            f"""INSERT INTO offers
                (id, source_id, external_record_id, is_demo, created_at, {columns})
                VALUES (:id, :source_id, :external_record_id, :is_demo, :created_at,
                        {placeholders})""",
            {
                **payload,
                "id": offer_id,
                "source_id": source_id,
                "external_record_id": external_record_id,
                "is_demo": 1 if is_demo else 0,
                "created_at": now,
            },
        )
        return offer_id, True, {}

    before = conn.execute("SELECT * FROM offers WHERE id = ?", (existing.id,)).fetchone()
    changed: dict[str, tuple[Any, Any]] = {}
    for key, value in payload.items():
        if key == "updated_at":
            continue
        old = before[key]
        if old != value and value is not None:
            changed[key] = (old, value)

    assignments = ", ".join(f"{key} = :{key}" for key in payload)
    conn.execute(
        f"UPDATE offers SET {assignments} WHERE id = :id", {**payload, "id": existing.id}
    )
    return existing.id, False, changed


def offers_for_vehicle(conn: sqlite3.Connection, vehicle_id: str) -> list[Offer]:
    rows = conn.execute(
        "SELECT * FROM offers WHERE vehicle_id = ? ORDER BY created_at", (vehicle_id,)
    ).fetchall()
    return [Offer.from_row(row) for row in rows]


# -- observations --------------------------------------------------------


@dataclass
class Observation:
    id: str
    offer_id: str | None
    source_id: str
    external_record_id: str | None
    observed_at: datetime
    fetched_at: datetime
    state: str
    price: Money | None
    price_basis: str
    vat_regime: str
    price_evidence_type: str
    raw_price_text: str | None
    status: str | None
    stock_kind: str | None
    mileage_km: int | None
    seats: int | None
    powertrain: str | None
    location_country: str | None
    parsed_fields: dict[str, Any]
    response_hash: str | None
    fetch_status: str | None
    version: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Observation":
        return cls(
            id=row["id"],
            offer_id=row["offer_id"],
            source_id=row["source_id"],
            external_record_id=row["external_record_id"],
            observed_at=from_iso(row["observed_at"]),
            fetched_at=from_iso(row["fetched_at"]),
            state=row["state"],
            price=money_from(row["price_minor"], row["price_currency"]),
            price_basis=row["price_basis"],
            vat_regime=row["vat_regime"],
            price_evidence_type=row["price_evidence_type"],
            raw_price_text=row["raw_price_text"],
            status=row["status"],
            stock_kind=row["stock_kind"],
            mileage_km=row["mileage_km"],
            seats=row["seats"],
            powertrain=row["powertrain"],
            location_country=row["location_country"],
            parsed_fields=loads(row["parsed_fields"], {}),
            response_hash=row["response_hash"],
            fetch_status=row["fetch_status"],
            version=row["version"],
        )


def insert_observation(
    conn: sqlite3.Connection, *, source_id: str, now: str, is_demo: bool, **fields: Any
) -> str | None:
    """Append one observation.

    Returns ``None`` when an identical observation is already stored, which is how
    replaying the same snapshot twice stays idempotent.
    """
    price: Money | None = fields.get("price")
    observation_id = new_id("obs")
    try:
        conn.execute(
            """INSERT INTO observations
               (id, offer_id, source_id, external_record_id, run_id, observed_at, fetched_at,
                state, raw_price_text, price_minor, price_currency, price_basis, vat_regime,
                price_evidence_type, status, stock_kind, mileage_km, seats, powertrain,
                location_country, parsed_fields, response_hash, fetch_status, evidence_id,
                version, is_demo, created_at)
               VALUES (:id, :offer_id, :source_id, :external_record_id, :run_id, :observed_at,
                :fetched_at, :state, :raw_price_text, :price_minor, :price_currency, :price_basis,
                :vat_regime, :price_evidence_type, :status, :stock_kind, :mileage_km, :seats,
                :powertrain, :location_country, :parsed_fields, :response_hash, :fetch_status,
                :evidence_id, :version, :is_demo, :created_at)""",
            {
                "id": observation_id,
                "offer_id": fields.get("offer_id"),
                "source_id": source_id,
                "external_record_id": fields.get("external_record_id"),
                "run_id": fields.get("run_id"),
                "observed_at": fields["observed_at"],
                "fetched_at": fields["fetched_at"],
                "state": fields.get("state", ObservationState.SEEN.value),
                "raw_price_text": fields.get("raw_price_text"),
                "price_minor": None if price is None else price.minor_units,
                "price_currency": None if price is None else price.currency,
                "price_basis": fields.get("price_basis", "unknown"),
                "vat_regime": fields.get("vat_regime", "unknown"),
                "price_evidence_type": fields.get("price_evidence_type", "supply_asking"),
                "status": fields.get("status"),
                "stock_kind": fields.get("stock_kind"),
                "mileage_km": fields.get("mileage_km"),
                "seats": fields.get("seats"),
                "powertrain": fields.get("powertrain"),
                "location_country": fields.get("location_country"),
                "parsed_fields": dumps(fields.get("parsed_fields") or {}),
                "response_hash": fields.get("response_hash"),
                "fetch_status": fields.get("fetch_status"),
                "evidence_id": fields.get("evidence_id"),
                "version": fields.get("version", 1),
                "is_demo": 1 if is_demo else 0,
                "created_at": now,
            },
        )
    except sqlite3.IntegrityError as exc:
        if "idx_observations_dedupe" in str(exc) or "UNIQUE" in str(exc):
            return None
        raise
    return observation_id


def observations_for_offer(
    conn: sqlite3.Connection, offer_id: str, *, limit: int | None = None
) -> list[Observation]:
    sql = "SELECT * FROM observations WHERE offer_id = ? ORDER BY observed_at DESC, created_at DESC"
    params: list[Any] = [offer_id]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [Observation.from_row(row) for row in conn.execute(sql, params).fetchall()]


def latest_observation(conn: sqlite3.Connection, offer_id: str) -> Observation | None:
    rows = observations_for_offer(conn, offer_id, limit=1)
    return rows[0] if rows else None


def latest_price_observation(conn: sqlite3.Connection, offer_id: str) -> Observation | None:
    """The most recent observation that actually carried a price."""
    row = conn.execute(
        "SELECT * FROM observations WHERE offer_id = ? AND price_minor IS NOT NULL "
        "AND state = 'seen' ORDER BY observed_at DESC, created_at DESC LIMIT 1",
        (offer_id,),
    ).fetchone()
    return None if row is None else Observation.from_row(row)


def comparable_observations(
    conn: sqlite3.Connection,
    *,
    model_family: str,
    evidence_types: tuple[str, ...],
    exclude_offer_id: str | None = None,
) -> list[sqlite3.Row]:
    """Latest priced observation per offer for one model family.

    Deduplicating to one row per offer here is what stops a repeatedly observed
    advert counting several times in a median.
    """
    placeholders = ",".join("?" * len(evidence_types))
    params: list[Any] = [model_family, *evidence_types]
    exclusion = ""
    if exclude_offer_id:
        exclusion = "AND o.id != ?"
        params.append(exclude_offer_id)
    sql = f"""
        SELECT obs.*, o.id AS offer_id_ref, o.vehicle_id, o.seller_company_id, o.seller_name,
               v.vin, v.vin_verified, v.model_family, v.variant, v.generation, v.steering,
               v.seats AS vehicle_seats, v.powertrain AS vehicle_powertrain,
               v.first_registration, v.mileage_km AS vehicle_mileage
        FROM observations obs
        JOIN offers o ON o.id = obs.offer_id
        JOIN vehicles v ON v.id = o.vehicle_id
        WHERE v.model_family = ?
          AND obs.price_minor IS NOT NULL
          AND obs.state = 'seen'
          AND obs.price_evidence_type IN ({placeholders})
          {exclusion}
          AND obs.id = (
              SELECT inner_obs.id FROM observations inner_obs
              WHERE inner_obs.offer_id = obs.offer_id AND inner_obs.price_minor IS NOT NULL
                AND inner_obs.state = 'seen'
              ORDER BY inner_obs.observed_at DESC, inner_obs.created_at DESC,
                       inner_obs.id LIMIT 1
          )
        -- The id tie-breaker matters: two observations can share a timestamp, and
        -- a median must not depend on which one the engine happened to return first.
        ORDER BY obs.observed_at DESC, obs.id
    """
    return conn.execute(sql, params).fetchall()


# -- change events -------------------------------------------------------


def insert_change_event(
    conn: sqlite3.Connection, *, dedupe_key: str, now: str, is_demo: bool, **fields: Any
) -> str | None:
    """Record an alert once. An unchanged response produces no new alert."""
    event_id = new_id("evt")
    try:
        conn.execute(
            """INSERT INTO change_events
               (id, offer_id, company_id, kind, field_name, old_value, new_value, currency,
                price_basis, detected_at, observed_at, observation_id, dedupe_key,
                acknowledged_at, notes, is_demo)
               VALUES (:id, :offer_id, :company_id, :kind, :field_name, :old_value, :new_value,
                :currency, :price_basis, :detected_at, :observed_at, :observation_id, :dedupe_key,
                NULL, :notes, :is_demo)""",
            {
                "id": event_id,
                "offer_id": fields.get("offer_id"),
                "company_id": fields.get("company_id"),
                "kind": fields["kind"],
                "field_name": fields.get("field_name"),
                "old_value": fields.get("old_value"),
                "new_value": fields.get("new_value"),
                "currency": fields.get("currency"),
                "price_basis": fields.get("price_basis"),
                "detected_at": fields.get("detected_at", now),
                "observed_at": fields["observed_at"],
                "observation_id": fields.get("observation_id"),
                "dedupe_key": dedupe_key,
                "notes": fields.get("notes"),
                "is_demo": 1 if is_demo else 0,
            },
        )
    except sqlite3.IntegrityError:
        return None
    return event_id


def recent_change_events(conn: sqlite3.Connection, *, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM change_events ORDER BY detected_at DESC, observed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
