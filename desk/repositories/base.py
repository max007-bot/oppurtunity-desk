"""Shared helpers for the repository layer."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any, Mapping

from ..clock import from_iso, to_iso
from ..money import Money


def dumps(value: Any) -> str:
    """Stable JSON for a small structured column."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def loads(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return {} if default is None else default
    return json.loads(value)


def ts(value: datetime | None) -> str | None:
    return None if value is None else to_iso(value)


def money_columns(amount: Money | None, currency_default: str = "EUR") -> tuple[int | None, str]:
    """Split a Money into its stored columns."""
    if amount is None:
        return None, currency_default
    return amount.minor_units, amount.currency


def money_from(minor: int | None, currency: str | None) -> Money | None:
    if minor is None or currency is None:
        return None
    return Money(int(minor), currency)


def row_money(row: Mapping[str, Any], minor_key: str, currency_key: str) -> Money | None:
    return money_from(row[minor_key], row[currency_key])


def row_time(row: Mapping[str, Any], key: str) -> datetime | None:
    return from_iso(row[key])


def fingerprint(*parts: Any) -> str:
    """Short stable hash used for dedupe keys and draft fact fingerprints."""
    body = "␟".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def upsert(
    conn: sqlite3.Connection,
    table: str,
    *,
    values: Mapping[str, Any],
    conflict: tuple[str, ...],
    update: tuple[str, ...],
) -> None:
    """Insert or update on a declared conflict target, with named parameters."""
    columns = list(values.keys())
    placeholders = ", ".join(f":{name}" for name in columns)
    assignments = ", ".join(f"{name} = excluded.{name}" for name in update)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({', '.join(conflict)}) DO UPDATE SET {assignments}"
    )
    conn.execute(sql, dict(values))


def exists(conn: sqlite3.Connection, table: str, where: str, params: tuple[Any, ...]) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", params).fetchone()
    return row is not None
