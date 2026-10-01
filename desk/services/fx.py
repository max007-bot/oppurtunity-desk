"""Dated currency conversion.

Nothing converts without a rate someone entered, with a direction and a date. The
prototype never assumes 1:1, never reuses an undated rate, and never silently
converts: a converted figure carries the rate and the date it came from wherever
it is shown.

A conversion makes two prices arithmetically comparable. It does not make them
commercially comparable - a retail price in another country differs for reasons
that have nothing to do with the exchange rate - so every conversion is labelled
as an assumption.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from ..clock import from_iso
from ..db import new_id
from ..money import Money, exponent

# How old a rate may be before it is refused for a conversion. A product
# assumption, configurable per call, not a market standard.
DEFAULT_MAX_RATE_AGE_DAYS = 7


class FxError(ValueError):
    """Raised when a conversion is attempted without usable dated evidence."""


@dataclass(frozen=True)
class FxRate:
    """One dated rate, in one direction."""

    id: str
    from_currency: str
    to_currency: str
    rate: Decimal
    rate_date: date
    source_note: str

    @property
    def label(self) -> str:
        return (
            f"1 {self.from_currency} = {self.rate.normalize()} {self.to_currency} "
            f"as at {self.rate_date.isoformat()}"
        )

    def age_days(self, at: datetime) -> int:
        return (at.date() - self.rate_date).days

    def inverse(self) -> "FxRate":
        """The other direction.

        Derived rather than stored, so a user enters one direction and the app
        does not silently invent a second, differently-rounded figure.
        """
        if self.rate == 0:
            raise FxError("a rate of zero cannot be inverted")
        return FxRate(
            id=self.id,
            from_currency=self.to_currency,
            to_currency=self.from_currency,
            rate=Decimal(1) / self.rate,
            rate_date=self.rate_date,
            source_note=f"inverse of {self.source_note}",
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "FxRate":
        parsed = from_iso(row["rate_date"])
        return cls(
            id=row["id"],
            from_currency=row["from_currency"],
            to_currency=row["to_currency"],
            rate=Decimal(row["rate"]),
            rate_date=parsed.date() if parsed else date.min,
            source_note=row["source_note"],
        )


@dataclass(frozen=True)
class Conversion:
    """The result of converting one amount, with its evidence attached."""

    original: Money
    converted: Money
    rate: FxRate

    @property
    def assumption(self) -> str:
        return (
            f"{self.original.format()} converted to {self.converted.format()} at "
            f"{self.rate.label} ({self.rate.source_note}). An exchange rate makes two "
            f"prices arithmetically comparable, not commercially comparable."
        )


def convert(amount: Money, rate: FxRate) -> Conversion:
    """Apply a dated rate. The direction must match the amount's currency."""
    if amount.currency != rate.from_currency:
        raise FxError(
            f"rate converts {rate.from_currency} to {rate.to_currency}, but the amount is in "
            f"{amount.currency}; use the correct direction rather than assuming it is reversible"
        )
    scale = Decimal(10) ** exponent(rate.to_currency)
    value = (amount.decimal * rate.rate * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return Conversion(amount, Money(int(value), rate.to_currency), rate)


# -- storage -------------------------------------------------------------


def record_rate(
    conn: sqlite3.Connection,
    *,
    from_currency: str,
    to_currency: str,
    rate: Decimal | str,
    rate_date: date | datetime | str,
    source_note: str,
    now: str,
    is_demo: bool,
) -> str:
    """Store a dated rate.

    ``source_note`` is mandatory: a rate with no stated origin is not evidence,
    and the whole point of this table is that a figure can be traced.
    """
    from_code = str(from_currency).upper()
    to_code = str(to_currency).upper()
    exponent(from_code)
    exponent(to_code)
    if from_code == to_code:
        raise FxError("a currency does not need converting to itself")

    value = Decimal(str(rate))
    if value <= 0:
        raise FxError("an exchange rate must be positive")
    if not str(source_note).strip():
        raise FxError("state where the rate came from; an unattributed rate is not evidence")

    stamp = _as_date(rate_date)

    row_id = new_id("fx")
    conn.execute(
        """INSERT INTO fx_rates
           (id, from_currency, to_currency, rate, rate_date, source_note, created_at, is_demo)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(from_currency, to_currency, rate_date) DO UPDATE SET
             rate = excluded.rate,
             source_note = excluded.source_note,
             created_at = excluded.created_at""",
        (
            row_id,
            from_code,
            to_code,
            str(value),
            stamp.isoformat(),
            str(source_note).strip(),
            now,
            1 if is_demo else 0,
        ),
    )
    return row_id


def list_rates(conn: sqlite3.Connection) -> list[FxRate]:
    rows = conn.execute(
        "SELECT * FROM fx_rates ORDER BY rate_date DESC, from_currency, to_currency"
    ).fetchall()
    return [FxRate.from_row(row) for row in rows]


def find_rate(
    conn: sqlite3.Connection,
    *,
    from_currency: str,
    to_currency: str,
    at: datetime,
    max_age_days: int | None = DEFAULT_MAX_RATE_AGE_DAYS,
) -> FxRate | None:
    """The most recent usable rate at or before ``at``, in either direction.

    A rate dated after the moment being analysed is not used: it would be
    hindsight. A rate older than the window is not used either, and the caller
    reports that rather than converting anyway.
    """
    from_code = str(from_currency).upper()
    to_code = str(to_currency).upper()
    if from_code == to_code:
        return None

    cutoff = at.date().isoformat()
    row = conn.execute(
        "SELECT * FROM fx_rates WHERE from_currency = ? AND to_currency = ? AND rate_date <= ? "
        "ORDER BY rate_date DESC LIMIT 1",
        (from_code, to_code, cutoff),
    ).fetchone()
    direct = FxRate.from_row(row) if row is not None else None

    reversed_row = conn.execute(
        "SELECT * FROM fx_rates WHERE from_currency = ? AND to_currency = ? AND rate_date <= ? "
        "ORDER BY rate_date DESC LIMIT 1",
        (to_code, from_code, cutoff),
    ).fetchone()
    indirect = FxRate.from_row(reversed_row).inverse() if reversed_row is not None else None

    candidates = [rate for rate in (direct, indirect) if rate is not None]
    if not candidates:
        return None

    best = max(candidates, key=lambda rate: rate.rate_date)
    if max_age_days is not None and best.age_days(at) > max_age_days:
        return None
    return best


class RateBook:
    """A small resolver the pricing service can consult without touching SQL.

    It caches lookups for one analysis so a comparable set does not re-query per
    candidate, and it records which rates were actually used so they can be shown.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        at: datetime,
        max_age_days: int | None = DEFAULT_MAX_RATE_AGE_DAYS,
    ) -> None:
        self._conn = conn
        self._at = at
        self._max_age_days = max_age_days
        self._cache: dict[tuple[str, str], FxRate | None] = {}
        self.used: list[FxRate] = []

    def rate_for(self, from_currency: str, to_currency: str) -> FxRate | None:
        key = (from_currency.upper(), to_currency.upper())
        if key not in self._cache:
            self._cache[key] = find_rate(
                self._conn,
                from_currency=key[0],
                to_currency=key[1],
                at=self._at,
                max_age_days=self._max_age_days,
            )
        return self._cache[key]

    def convert(self, amount: Money, to_currency: str) -> Conversion | None:
        """Convert, or return ``None`` when no dated rate supports it."""
        if amount.currency == to_currency.upper():
            return None
        rate = self.rate_for(amount.currency, to_currency)
        if rate is None:
            return None
        result = convert(amount, rate)
        if all(existing.id != rate.id for existing in self.used):
            self.used.append(rate)
        return result

    @property
    def assumptions(self) -> list[str]:
        return [f"converted at {rate.label} ({rate.source_note})" for rate in self.used]


def missing_rate_message(
    from_currency: str, to_currency: str, *, max_age_days: int | None
) -> str:
    window = (
        "no rate has been entered"
        if max_age_days is None
        else f"no rate within {max_age_days} days has been entered"
    )
    return (
        f"priced in {from_currency} while the comparison is in {to_currency}, and {window}; "
        f"excluded rather than converted at an assumed rate"
    )


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    parsed = from_iso(text) if "T" in text or text.endswith("Z") else None
    if parsed is not None:
        return parsed.date()
    return date.fromisoformat(text[:10])


def rates_summary(rates: Iterable[FxRate], *, at: datetime) -> list[dict[str, object]]:
    """Rows for the rates table, including how stale each one is."""
    out: list[dict[str, object]] = []
    for rate in rates:
        age = rate.age_days(at)
        out.append(
            {
                "from": rate.from_currency,
                "to": rate.to_currency,
                "rate": str(rate.rate.normalize()),
                "dated": rate.rate_date.isoformat(),
                "age (days)": age,
                "usable now": "yes" if age <= DEFAULT_MAX_RATE_AGE_DAYS else "too old",
                "source": rate.source_note,
            }
        )
    return out
