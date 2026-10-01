"""Analyst adjustments on a comparable.

The manual is explicit that no automatic monetary adjustment is applied for
options or mileage. An analyst may still say "this one has the rear entertainment
package, which is worth about 3,000 EUR less than our car" - and when they do, it
is stored as a labelled assumption with a reason and an author, its effect on the
median is reported separately, and it can be retired without being erased.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..clock import from_iso, to_iso
from ..db import new_id
from ..money import Money
from .pricing import Adjustment

# A guard against a typo turning a 3,000 EUR trim difference into 300,000. It is
# a configurable sanity limit, not a view about what an option is worth.
MAX_ADJUSTMENT_FRACTION = Decimal("0.25")


class AdjustmentError(ValueError):
    pass


@dataclass
class StoredAdjustment:
    id: str
    target_offer_id: str
    comparable_offer_id: str
    amount: Money
    reason: str
    created_by: str
    created_at: datetime

    def as_adjustment(self) -> Adjustment:
        return Adjustment(
            comparable_offer_id=self.comparable_offer_id,
            amount=self.amount,
            reason=self.reason,
            created_by=self.created_by,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "StoredAdjustment":
        return cls(
            id=row["id"],
            target_offer_id=row["target_offer_id"],
            comparable_offer_id=row["comparable_offer_id"],
            amount=Money(row["amount_minor"], row["currency"]),
            reason=row["reason"],
            created_by=row["created_by"],
            created_at=from_iso(row["created_at"]),
        )


def record(
    conn: sqlite3.Connection,
    *,
    target_offer_id: str,
    comparable_offer_id: str,
    amount: Money,
    reason: str,
    created_by: str,
    now: datetime,
    is_demo: bool,
    comparable_price: Money | None = None,
) -> str:
    """Store an adjustment, replacing any live one for the same pair.

    A reason and an author are mandatory. An adjustment with neither is
    indistinguishable from someone quietly moving a number.
    """
    if not str(reason).strip():
        raise AdjustmentError(
            "state why the adjustment applies; an unexplained adjustment is not an assumption, "
            "it is an unexplained number"
        )
    if not str(created_by).strip():
        raise AdjustmentError("record who entered the adjustment")
    if amount.minor_units == 0:
        raise AdjustmentError("an adjustment of zero has no effect; remove it instead")

    if comparable_price is not None:
        if comparable_price.currency != amount.currency:
            raise AdjustmentError(
                f"the adjustment is in {amount.currency} but the comparable is priced in "
                f"{comparable_price.currency}"
            )
        limit = abs(comparable_price.decimal) * MAX_ADJUSTMENT_FRACTION
        if abs(amount.decimal) > limit:
            raise AdjustmentError(
                f"an adjustment of {abs(amount.decimal):,.0f} {amount.currency} is more than "
                f"{MAX_ADJUSTMENT_FRACTION:.0%} of the comparable's price. If that is genuinely "
                f"right, the two cars are probably not comparable in the first place."
            )

    stamp = to_iso(now)
    retire(conn, target_offer_id=target_offer_id, comparable_offer_id=comparable_offer_id, now=now)

    row_id = new_id("adj")
    conn.execute(
        """INSERT INTO comparable_adjustments
           (id, target_offer_id, comparable_offer_id, amount_minor, currency, reason,
            created_by, created_at, retired_at, is_demo)
           VALUES (?,?,?,?,?,?,?,?,NULL,?)""",
        (
            row_id,
            target_offer_id,
            comparable_offer_id,
            amount.minor_units,
            amount.currency,
            str(reason).strip(),
            str(created_by).strip(),
            stamp,
            1 if is_demo else 0,
        ),
    )
    return row_id


def retire(
    conn: sqlite3.Connection,
    *,
    target_offer_id: str,
    comparable_offer_id: str,
    now: datetime,
) -> int:
    """Stop applying an adjustment, keeping the row for the audit trail."""
    cursor = conn.execute(
        "UPDATE comparable_adjustments SET retired_at = ? "
        "WHERE target_offer_id = ? AND comparable_offer_id = ? AND retired_at IS NULL",
        (to_iso(now), target_offer_id, comparable_offer_id),
    )
    return cursor.rowcount


def live_for_target(
    conn: sqlite3.Connection, target_offer_id: str
) -> dict[str, Adjustment]:
    """Current adjustments for one analysis, keyed by the comparable's offer id."""
    rows = conn.execute(
        "SELECT * FROM comparable_adjustments WHERE target_offer_id = ? AND retired_at IS NULL "
        "ORDER BY created_at",
        (target_offer_id,),
    ).fetchall()
    return {
        row["comparable_offer_id"]: StoredAdjustment.from_row(row).as_adjustment()
        for row in rows
    }


def list_for_target(
    conn: sqlite3.Connection, target_offer_id: str, *, include_retired: bool = False
) -> list[StoredAdjustment]:
    clause = "" if include_retired else "AND retired_at IS NULL"
    rows = conn.execute(
        f"SELECT * FROM comparable_adjustments WHERE target_offer_id = ? {clause} "
        f"ORDER BY created_at DESC",
        (target_offer_id,),
    ).fetchall()
    return [StoredAdjustment.from_row(row) for row in rows]
