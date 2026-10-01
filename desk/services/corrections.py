"""Resolving a contradiction between a source and the reviewed record.

An import never overwrites a reviewed vehicle fact. When a source later states
something different, the disagreement is surfaced and waits for a person. This
module is that person's two options, and both are recorded:

* **accept the source's value** - the reviewed record is updated, with who
  decided, when, from what observation, and a dated note;
* **keep the reviewed value** - equally a decision, and equally recorded, so the
  same contradiction does not keep asking.

Nothing here happens automatically, and the original observation is untouched
either way.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import to_iso
from ..db import new_id, record_change
from ..repositories import supply as supply_repo
from ..repositories.supply import Observation, Vehicle

# Fields a source observation can contradict on a reviewed vehicle. Anything not
# listed here is not resolvable this way and needs a deliberate edit.
COMPARABLE_FIELDS: dict[str, str] = {
    "seats": "homologated seats",
    "mileage_km": "mileage",
    "powertrain": "powertrain",
}


@dataclass
class Contradiction:
    """One field where the latest observation disagrees with the reviewed record."""

    field: str
    label: str
    reviewed_value: Any
    observed_value: Any
    observation_id: str
    observed_at: datetime
    source_id: str

    @property
    def key(self) -> str:
        return f"{self.field}:{self.observed_value}"

    def describe(self) -> str:
        return (
            f"the latest observation states {self.label} of {self.observed_value}, "
            f"the reviewed record says {self.reviewed_value}"
        )

    def consequence(self) -> str:
        if self.field == "seats":
            return "a seat-count change can alter homologation and therefore buyer fit"
        if self.field == "mileage_km":
            return "a mileage change can move the car in or out of a buyer's ceiling"
        return "a specification change can alter which buyers the car suits"


def find_contradictions(
    conn: sqlite3.Connection, vehicle: Vehicle | None, offer_id: str
) -> list[Contradiction]:
    """Compare the reviewed vehicle with the newest successful observation.

    Only a value the source actually stated counts. A missing field is unknown,
    and unknown never contradicts anything.
    """
    if vehicle is None:
        return []

    latest = _latest_seen(conn, offer_id)
    if latest is None:
        return []

    out: list[Contradiction] = []
    for field, label in COMPARABLE_FIELDS.items():
        observed = getattr(latest, field, None)
        reviewed = getattr(vehicle, field, None)
        if observed is None or reviewed is None or observed == reviewed:
            continue
        if _already_decided(conn, vehicle.id, field, observed):
            continue
        out.append(
            Contradiction(
                field=field,
                label=label,
                reviewed_value=reviewed,
                observed_value=observed,
                observation_id=latest.id,
                observed_at=latest.observed_at,
                source_id=latest.source_id,
            )
        )
    return out


def _latest_seen(conn: sqlite3.Connection, offer_id: str) -> Observation | None:
    history = supply_repo.observations_for_offer(conn, offer_id, limit=10)
    return next((o for o in history if o.state == "seen"), None)


def _already_decided(
    conn: sqlite3.Connection, vehicle_id: str, field: str, observed_value: Any
) -> bool:
    """A contradiction someone has ruled on does not keep reappearing."""
    row = conn.execute(
        "SELECT 1 FROM fact_reviews WHERE entity_type = 'vehicle' AND entity_id = ? "
        "AND field_name = ? AND IFNULL(observed_value,'') = ? LIMIT 1",
        (vehicle_id, field, "" if observed_value is None else str(observed_value)),
    ).fetchone()
    return row is not None


@dataclass
class CorrectionOutcome:
    decision: str
    review_id: str
    field: str
    previous_value: Any
    new_value: Any
    note: str

    @property
    def accepted(self) -> bool:
        return self.decision == "accepted_source"

    def summary(self) -> str:
        if self.accepted:
            return (
                f"{self.field} updated from {self.previous_value} to {self.new_value} on the "
                f"reviewed record. Recompute to let matching use the corrected fact."
            )
        return (
            f"{self.field} kept as {self.previous_value}. The source's {self.new_value} stays in "
            f"the observation history and will not be raised again."
        )


def resolve(
    conn: sqlite3.Connection,
    *,
    vehicle_id: str,
    contradiction: Contradiction,
    decision: str,
    decided_by: str,
    note: str,
    now: datetime,
    is_demo: bool,
) -> CorrectionOutcome:
    """Record a decision about one contradiction, and apply it if accepted.

    A dated note is required for both outcomes: a change to a reviewed fact
    without a stated reason is exactly the kind of untraceable edit this
    application exists to avoid.
    """
    if decision not in {"accepted_source", "kept_reviewed"}:
        raise ValueError("decision must be accepted_source or kept_reviewed")
    if not str(note).strip():
        raise ValueError("a dated note is required so the decision can be explained later")
    if not str(decided_by).strip():
        raise ValueError("record who decided; an anonymous change to a reviewed fact is not an audit trail")

    vehicle = supply_repo.get_vehicle(conn, vehicle_id)
    if vehicle is None:
        raise ValueError(f"no vehicle {vehicle_id!r}")

    previous = getattr(vehicle, contradiction.field, None)
    stamp = to_iso(now)

    if decision == "accepted_source":
        supply_repo.update_vehicle(
            conn, vehicle_id, now=stamp, **{contradiction.field: contradiction.observed_value}
        )
        record_change(
            conn,
            entity_type="vehicle",
            entity_id=vehicle_id,
            action="correction_accepted",
            field_name=contradiction.field,
            old_value=None if previous is None else str(previous),
            new_value=str(contradiction.observed_value),
            actor=decided_by,
            occurred_at=stamp,
            is_demo=is_demo,
        )
    else:
        record_change(
            conn,
            entity_type="vehicle",
            entity_id=vehicle_id,
            action="correction_rejected",
            field_name=contradiction.field,
            old_value=None if previous is None else str(previous),
            new_value=str(contradiction.observed_value),
            actor=decided_by,
            occurred_at=stamp,
            is_demo=is_demo,
        )

    review_id = new_id("fct")
    conn.execute(
        """INSERT INTO fact_reviews
           (id, entity_type, entity_id, field_name, previous_value, observed_value, decision,
            note, observation_id, change_event_id, decided_by, decided_at, is_demo)
           VALUES (?, 'vehicle', ?,?,?,?,?,?,?,?,?,?,?)""",
        (
            review_id,
            vehicle_id,
            contradiction.field,
            None if previous is None else str(previous),
            str(contradiction.observed_value),
            decision,
            str(note).strip(),
            contradiction.observation_id,
            _event_for(conn, contradiction),
            decided_by,
            stamp,
            1 if is_demo else 0,
        ),
    )

    # The alert has been dealt with, so it stops occupying the Today screen.
    _acknowledge(conn, contradiction, stamp)

    return CorrectionOutcome(
        decision=decision,
        review_id=review_id,
        field=contradiction.label,
        previous_value=previous,
        new_value=contradiction.observed_value,
        note=str(note).strip(),
    )


def _event_for(conn: sqlite3.Connection, contradiction: Contradiction) -> str | None:
    row = conn.execute(
        "SELECT id FROM change_events WHERE observation_id = ? AND field_name = ? LIMIT 1",
        (contradiction.observation_id, contradiction.field),
    ).fetchone()
    return None if row is None else row["id"]


def _acknowledge(conn: sqlite3.Connection, contradiction: Contradiction, stamp: str) -> None:
    conn.execute(
        "UPDATE change_events SET acknowledged_at = ? WHERE observation_id = ? "
        "AND field_name = ? AND acknowledged_at IS NULL",
        (stamp, contradiction.observation_id, contradiction.field),
    )


def history(conn: sqlite3.Connection, vehicle_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM fact_reviews WHERE entity_type = 'vehicle' AND entity_id = ? "
        "ORDER BY decided_at DESC",
        (vehicle_id,),
    ).fetchall()


def pending_count(conn: sqlite3.Connection) -> int:
    """How many offers currently show an unresolved contradiction."""
    total = 0
    for row in conn.execute("SELECT id, vehicle_id FROM offers WHERE vehicle_id IS NOT NULL"):
        vehicle = supply_repo.get_vehicle(conn, row["vehicle_id"])
        if find_contradictions(conn, vehicle, row["id"]):
            total += 1
    return total
