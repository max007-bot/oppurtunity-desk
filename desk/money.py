"""Exact money handling.

Money is stored as an integer number of minor units plus an ISO currency code and
computed with ``Decimal``. Binary floats never touch a price, cost or result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

# Currencies the prototype knows how to scale. Extending this list is a product
# decision; an unknown currency is rejected rather than guessed at 2 decimals.
MINOR_UNIT_EXPONENT: dict[str, int] = {
    "EUR": 2,
    "GBP": 2,
    "CZK": 2,
    "USD": 2,
    "CHF": 2,
    "PLN": 2,
    "SEK": 2,
    "NOK": 2,
    "DKK": 2,
    "HUF": 2,
    "JPY": 0,
}

BASE_CURRENCY = "EUR"


class MoneyError(ValueError):
    """Raised for an unusable currency, amount or mixed-currency operation."""


def exponent(currency: str) -> int:
    code = currency.upper()
    if code not in MINOR_UNIT_EXPONENT:
        raise MoneyError(f"unknown currency {currency!r}; add it deliberately, do not assume")
    return MINOR_UNIT_EXPONENT[code]


@dataclass(frozen=True, order=False)
class Money:
    """An exact amount in one currency."""

    minor_units: int
    currency: str = BASE_CURRENCY

    def __post_init__(self) -> None:
        if not isinstance(self.minor_units, int) or isinstance(self.minor_units, bool):
            raise MoneyError("minor_units must be an int")
        exponent(self.currency)
        object.__setattr__(self, "currency", self.currency.upper())

    # -- construction ----------------------------------------------------
    @classmethod
    def zero(cls, currency: str = BASE_CURRENCY) -> "Money":
        return cls(0, currency)

    @classmethod
    def from_decimal(cls, amount: Decimal | int | str, currency: str = BASE_CURRENCY) -> "Money":
        scale = Decimal(10) ** exponent(currency)
        value = Decimal(str(amount)) * scale
        rounded = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return cls(int(rounded), currency)

    # -- conversion ------------------------------------------------------
    @property
    def decimal(self) -> Decimal:
        return Decimal(self.minor_units) / (Decimal(10) ** exponent(self.currency))

    def __str__(self) -> str:
        return f"{self.decimal:,.{exponent(self.currency)}f} {self.currency}"

    def format(self, *, symbol: bool = True) -> str:
        symbols = {"EUR": "€", "GBP": "£", "USD": "$"}
        digits = exponent(self.currency)
        body = f"{self.decimal:,.{digits}f}"
        if symbol and self.currency in symbols:
            return f"{symbols[self.currency]}{body}"
        return f"{body} {self.currency}"

    # -- arithmetic ------------------------------------------------------
    def _same(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise MoneyError("can only combine Money with Money")
        if other.currency != self.currency:
            raise MoneyError(
                f"refusing to mix {self.currency} and {other.currency} "
                f"without an explicit dated FX rate"
            )

    def __add__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.minor_units, self.currency)

    def __mul__(self, factor: Decimal | int | str) -> "Money":
        value = Decimal(self.minor_units) * Decimal(str(factor))
        return Money(int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP)), self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor_units >= other.minor_units

    @property
    def is_negative(self) -> bool:
        return self.minor_units < 0


def total(amounts: Iterable[Money], currency: str = BASE_CURRENCY) -> Money:
    result = Money.zero(currency)
    for amount in amounts:
        result = result + amount
    return result


def median(amounts: list[Money]) -> Money:
    """Exact median.

    An even-sized sample averages the two middle values with half-up rounding on
    minor units, so the result stays an exact amount rather than a float.
    """
    if not amounts:
        raise MoneyError("median of an empty sample is undefined")
    currency = amounts[0].currency
    units = sorted(a.minor_units for a in amounts if a.currency == currency)
    if len(units) != len(amounts):
        raise MoneyError("median requires a single currency")
    mid = len(units) // 2
    if len(units) % 2 == 1:
        return Money(units[mid], currency)
    pair = Decimal(units[mid - 1] + units[mid]) / Decimal(2)
    return Money(int(pair.quantize(Decimal(1), rounding=ROUND_HALF_UP)), currency)


# -- parsing -------------------------------------------------------------

_CURRENCY_MARKS = {
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "$": "USD",
    "usd": "USD",
    "kč": "CZK",
    "czk": "CZK",
    "chf": "CHF",
    "zł": "PLN",
    "pln": "PLN",
}

_DIGITS = re.compile(r"[0-9]")


def detect_currency(raw: str) -> str | None:
    text = str(raw).strip().lower()
    for mark, code in _CURRENCY_MARKS.items():
        if mark in text:
            return code
    return None


def parse_money(
    raw: str,
    *,
    currency: str | None = None,
    locale: str | None = None,
) -> Money:
    """Parse a source price string, keeping the caller honest about ambiguity.

    ``locale`` is ``"eu"`` (1.234,56) or ``"uk"`` (1,234.56). It is required when
    the separators alone are ambiguous, because guessing silently would corrupt a
    price by three orders of magnitude.
    """
    if raw is None:
        raise MoneyError("no price text supplied")
    text = str(raw).strip()
    if not text:
        raise MoneyError("empty price text")

    code = (currency or detect_currency(text) or BASE_CURRENCY).upper()
    exponent(code)

    body = re.sub(r"[^0-9.,\-]", "", text)
    body = body.rstrip("-").strip()
    negative = body.startswith("-")
    body = body.lstrip("-")
    if not _DIGITS.search(body):
        raise MoneyError(f"no digits in price text {raw!r}")

    amount = _parse_number(body, locale=locale, raw=raw)
    value = Money.from_decimal(amount, code)
    return -value if negative else value


def _parse_number(body: str, *, locale: str | None, raw: str) -> Decimal:
    has_dot = "." in body
    has_comma = "," in body

    if has_dot and has_comma:
        # The rightmost separator is the decimal mark.
        if body.rfind(",") > body.rfind("."):
            return Decimal(body.replace(".", "").replace(",", "."))
        return Decimal(body.replace(",", ""))

    sep = "." if has_dot else ("," if has_comma else None)
    if sep is None:
        return Decimal(body)

    parts = body.split(sep)
    tail = parts[-1]

    # Repeated separators can only be thousands grouping.
    if len(parts) > 2:
        return Decimal(body.replace(sep, ""))

    if len(tail) == 3:
        # Genuinely ambiguous: 119.000 is 119000 in the EU and 119.0 in the UK.
        if locale == "eu":
            return Decimal(body.replace(sep, ""))
        if locale == "uk":
            if sep == ",":
                return Decimal(body.replace(",", ""))
            return Decimal(body)
        raise MoneyError(
            f"ambiguous separator in {raw!r}: state the source locale (eu or uk) "
            f"instead of guessing thousands versus decimals"
        )

    if len(tail) in (1, 2):
        return Decimal(body.replace(sep, "."))
    return Decimal(body.replace(sep, ""))
