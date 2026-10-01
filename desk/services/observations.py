"""Recording observations and deriving explainable change events.

Observations are appended, never replaced. Change events are derived by comparing
one observation with the previous one for the same source record, and are
deduplicated by (source record, field, observation version) so an unchanged
response produces no new alert.

Two states are carefully distinguished from "sold":

* ``not_seen``    - not observed in the latest successful check;
* ``fetch_failed`` - the check itself failed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import to_iso
from ..config import ObservationState
from ..models import ObservationRow, OfferRow
from ..money import Money
from ..repositories import supply as supply_repo
from ..repositories.base import dumps, fingerprint


@dataclass
class IngestOutcome:
    offer_id: str
    created: bool
    observation_id: str | None
    events: list[str]
    duplicate_review_id: str | None = None
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    @property
    def observation_was_new(self) -> bool:
        return self.observation_id is not None


def snapshot_hash(payload: Any) -> str:
    """Stable hash of a source record, used for idempotent replay."""
    return hashlib.sha256(dumps(payload).encode("utf-8")).hexdigest()[:32]


def record_offer_observation(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    row: OfferRow,
    offer_id: str,
    now: datetime,
    is_demo: bool,
    response_hash: str | None = None,
    run_id: str | None = None,
    state: str = ObservationState.SEEN.value,
    fetch_status: str | None = None,
) -> tuple[str | None, list[str]]:
    """Append an observation for an offer and return any new change events."""
    previous = supply_repo.latest_observation(conn, offer_id)
    price = row.price.to_money() if row.price else None

    observation_id = supply_repo.insert_observation(
        conn,
        source_id=source_id,
        now=to_iso(now),
        is_demo=is_demo,
        offer_id=offer_id,
        external_record_id=row.external_id,
        run_id=run_id,
        observed_at=to_iso(row.observed_at),
        fetched_at=to_iso(now),
        state=state,
        raw_price_text=None if row.price is None else row.price.raw_text,
        price=price,
        price_basis=row.price_basis.value,
        vat_regime=row.vat_regime.value,
        price_evidence_type=row.price_evidence_type.value,
        status=row.status.value,
        stock_kind=row.stock_kind.value,
        mileage_km=row.mileage_km,
        seats=row.seats,
        powertrain=row.powertrain,
        location_country=row.location_country,
        parsed_fields=row.specification,
        response_hash=response_hash,
        fetch_status=fetch_status,
        version=(previous.version + 1) if previous else 1,
    )

    if observation_id is None:
        # An identical observation is already stored: a replay, not a new event.
        return None, []

    events = detect_events(
        conn,
        offer_id=offer_id,
        source_id=source_id,
        external_record_id=row.external_id,
        previous=previous,
        observation_id=observation_id,
        observed_at=row.observed_at,
        now=now,
        is_demo=is_demo,
        price=price,
        price_basis=row.price_basis.value,
        status=row.status.value,
        stock_kind=row.stock_kind.value,
        seats=row.seats,
        powertrain=row.powertrain,
        available_from=row.available_from,
        state=state,
    )
    return observation_id, events


def record_comparable_observation(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    row: ObservationRow,
    offer_id: str,
    now: datetime,
    is_demo: bool,
    response_hash: str | None = None,
    run_id: str | None = None,
) -> str | None:
    price = row.price.to_money() if row.price else None
    previous = supply_repo.latest_observation(conn, offer_id)
    return supply_repo.insert_observation(
        conn,
        source_id=source_id,
        now=to_iso(now),
        is_demo=is_demo,
        offer_id=offer_id,
        external_record_id=row.external_id,
        run_id=run_id,
        observed_at=to_iso(row.observed_at),
        fetched_at=to_iso(now),
        state=row.state,
        raw_price_text=None if row.price is None else row.price.raw_text,
        price=price,
        price_basis=row.price_basis.value,
        vat_regime=row.vat_regime.value,
        price_evidence_type=row.price_evidence_type.value,
        status=row.status.value,
        stock_kind=row.stock_kind.value,
        mileage_km=row.mileage_km,
        seats=row.seats,
        powertrain=row.powertrain,
        location_country=row.location_country,
        response_hash=response_hash,
        version=(previous.version + 1) if previous else 1,
    )


def detect_events(
    conn: sqlite3.Connection,
    *,
    offer_id: str,
    source_id: str,
    external_record_id: str,
    previous: supply_repo.Observation | None,
    observation_id: str,
    observed_at: datetime,
    now: datetime,
    is_demo: bool,
    price: Money | None,
    price_basis: str,
    status: str | None,
    stock_kind: str | None,
    seats: int | None,
    powertrain: str | None,
    available_from: datetime | None,
    state: str,
) -> list[str]:
    """Compare against the previous observation and record only real changes."""
    events: list[str] = []

    def add(kind: str, **fields: Any) -> None:
        key = fingerprint(
            "evt",
            source_id,
            external_record_id,
            kind,
            fields.get("field_name"),
            fields.get("new_value"),
            observed_at.isoformat(),
        )
        event_id = supply_repo.insert_change_event(
            conn,
            dedupe_key=key,
            now=to_iso(now),
            is_demo=is_demo,
            offer_id=offer_id,
            kind=kind,
            observation_id=observation_id,
            observed_at=to_iso(observed_at),
            detected_at=to_iso(now),
            **fields,
        )
        if event_id:
            events.append(event_id)

    if state == ObservationState.FETCH_FAILED.value:
        add(
            "fetch_failed",
            field_name="fetch",
            old_value=None,
            new_value="fetch failed",
            notes="the check itself failed; this is not evidence the car was sold",
        )
        return events

    if state == ObservationState.NOT_SEEN.value:
        add(
            "not_observed",
            field_name="listing",
            old_value="seen",
            new_value="not observed in the latest successful check",
            notes="an absent advert is not proof of a sale",
        )
        return events

    if previous is None:
        return events

    if price is not None and previous.price is not None and price != previous.price:
        direction = "lower" if price < previous.price else "higher"
        add(
            "price_change",
            field_name="price",
            old_value=previous.price.format(),
            new_value=price.format(),
            currency=price.currency,
            price_basis=price_basis,
            notes=(
                f"the same identifiable offer is now {direction} on a {price_basis} basis; a "
                f"price drop is not evidence that the seller is distressed"
            ),
        )

    if status and previous.status and status != previous.status:
        add(
            "availability_change",
            field_name="status",
            old_value=previous.status,
            new_value=status,
            notes="availability as stated by the source; seller authority and physical location "
            "remain separate facts",
        )

    if stock_kind and previous.stock_kind and stock_kind != previous.stock_kind:
        add(
            "availability_change",
            field_name="stock_kind",
            old_value=previous.stock_kind,
            new_value=stock_kind,
            notes="physical stock, an allocation and an unknown location are different states",
        )

    if seats is not None and previous.seats is not None and seats != previous.seats:
        add(
            "specification_correction",
            field_name="seats",
            old_value=str(previous.seats),
            new_value=str(seats),
            notes="a seat-count correction can change homologation and buyer fit",
        )

    if powertrain and previous.powertrain and powertrain != previous.powertrain:
        add(
            "specification_correction",
            field_name="powertrain",
            old_value=previous.powertrain,
            new_value=powertrain,
        )

    return events


def mark_not_observed(
    conn: sqlite3.Connection,
    *,
    offer_id: str,
    source_id: str,
    external_record_id: str,
    observed_at: datetime,
    now: datetime,
    is_demo: bool,
    response_hash: str | None = None,
) -> tuple[str | None, list[str]]:
    """Record that a previously seen offer was absent from a successful check."""
    previous = supply_repo.latest_observation(conn, offer_id)
    observation_id = supply_repo.insert_observation(
        conn,
        source_id=source_id,
        now=to_iso(now),
        is_demo=is_demo,
        offer_id=offer_id,
        external_record_id=external_record_id,
        observed_at=to_iso(observed_at),
        fetched_at=to_iso(now),
        state=ObservationState.NOT_SEEN.value,
        response_hash=response_hash,
        version=(previous.version + 1) if previous else 1,
    )
    if observation_id is None:
        return None, []
    conn.execute(
        "UPDATE offers SET last_observation_state = 'not_seen', updated_at = ? WHERE id = ?",
        (to_iso(now), offer_id),
    )
    events = detect_events(
        conn,
        offer_id=offer_id,
        source_id=source_id,
        external_record_id=external_record_id,
        previous=previous,
        observation_id=observation_id,
        observed_at=observed_at,
        now=now,
        is_demo=is_demo,
        price=None,
        price_basis="unknown",
        status=None,
        stock_kind=None,
        seats=None,
        powertrain=None,
        available_from=None,
        state=ObservationState.NOT_SEEN.value,
    )
    return observation_id, events


def mark_fetch_failed(
    conn: sqlite3.Connection,
    *,
    offer_id: str,
    source_id: str,
    external_record_id: str,
    now: datetime,
    is_demo: bool,
    error: str,
) -> tuple[str | None, list[str]]:
    """Record a failed check without disturbing the last successful observation."""
    previous = supply_repo.latest_observation(conn, offer_id)
    observation_id = supply_repo.insert_observation(
        conn,
        source_id=source_id,
        now=to_iso(now),
        is_demo=is_demo,
        offer_id=offer_id,
        external_record_id=external_record_id,
        observed_at=to_iso(now),
        fetched_at=to_iso(now),
        state=ObservationState.FETCH_FAILED.value,
        fetch_status=error[:500],
        version=(previous.version + 1) if previous else 1,
    )
    conn.execute(
        "UPDATE offers SET last_observation_state = 'fetch_failed', updated_at = ? WHERE id = ?",
        (to_iso(now), offer_id),
    )
    if observation_id is None:
        return None, []
    events = detect_events(
        conn,
        offer_id=offer_id,
        source_id=source_id,
        external_record_id=external_record_id,
        previous=previous,
        observation_id=observation_id,
        observed_at=now,
        now=now,
        is_demo=is_demo,
        price=None,
        price_basis="unknown",
        status=None,
        stock_kind=None,
        seats=None,
        powertrain=None,
        available_from=None,
        state=ObservationState.FETCH_FAILED.value,
    )
    return observation_id, events


def record_company_signal(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    description: str,
    observed_at: datetime,
    now: datetime,
    is_demo: bool,
    source_id: str,
    external_record_id: str,
) -> str | None:
    """Permitted evidence about a company's category or announced expansion.

    Labelled as a research signal: a social post does not confirm ownership and a
    network claim is not a fleet.
    """
    key = fingerprint("company_signal", source_id, external_record_id, description)
    return supply_repo.insert_change_event(
        conn,
        dedupe_key=key,
        now=to_iso(now),
        is_demo=is_demo,
        company_id=company_id,
        kind="company_signal",
        field_name="category_evidence",
        new_value=description,
        observed_at=to_iso(observed_at),
        detected_at=to_iso(now),
        notes="research signal only; not evidence of ownership or demand",
    )
