"""Reviewed, reusable cost assumptions for a route.

Registration tax, homologation, transport and the rest get retyped into every
scenario worksheet, which is slow and, worse, inconsistent. A template is a
reviewed set of figures for one route, established by a named person on a stated
date from a stated basis.

What a template is **not**:

* it is not a tax engine, and it does not compute anyone's liability;
* it is not authoritative once loaded - every line stays editable, because the
  car in front of you may not match the assumption;
* it does not turn an unknown into a number. A template line with no amount
  arrives as unknown and still blocks a complete scenario.

A template that has passed its review date is still loadable, but says so loudly:
registration taxes change, and a stale figure presented confidently is worse than
no figure at all.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..clock import from_iso, to_iso
from ..db import new_id
from ..money import Money
from .pricing import CostLine

DEFAULT_REVIEW_PERIOD_DAYS = 180


class TemplateError(ValueError):
    pass


@dataclass
class TemplateLine:
    label: str
    amount: Money | None = None
    confirmed_zero: bool = False
    tax_treatment: str = "non_recoverable"
    already_in_purchase_price: bool = False
    required: bool = True
    evidence_note: str | None = None

    def to_cost_line(self) -> CostLine:
        return CostLine(
            label=self.label,
            amount=self.amount,
            confirmed_zero=self.confirmed_zero,
            tax_treatment=self.tax_treatment,
            already_in_purchase_price=self.already_in_purchase_price,
            required=self.required,
            notes=self.evidence_note,
        )

    @property
    def is_known(self) -> bool:
        return self.amount is not None or self.confirmed_zero


@dataclass
class CostTemplate:
    id: str
    name: str
    destination_country: str
    basis: str
    reviewer: str
    reviewed_at: datetime
    currency: str = "EUR"
    origin_country: str | None = None
    model_family: str | None = None
    review_due_at: datetime | None = None
    notes: str | None = None
    lines: list[TemplateLine] = field(default_factory=list)

    def is_stale(self, at: datetime) -> bool:
        return self.review_due_at is not None and self.review_due_at <= at

    @property
    def unknown_lines(self) -> list[str]:
        return [line.label for line in self.lines if line.required and not line.is_known]

    def route_label(self) -> str:
        origin = self.origin_country or "any origin"
        return f"{origin} to {self.destination_country}"

    def describe(self, at: datetime) -> str:
        text = (
            f"{self.name}: {len(self.lines)} line(s) for {self.route_label()}, reviewed by "
            f"{self.reviewer} on {self.reviewed_at.date().isoformat()} from {self.basis}."
        )
        if self.is_stale(at):
            text += (
                f" This review lapsed on {self.review_due_at.date().isoformat()}; registration "
                f"taxes change, so check every figure before relying on it."
            )
        if self.unknown_lines:
            text += (
                f" {len(self.unknown_lines)} line(s) have no amount and will arrive as unknown: "
                f"{', '.join(self.unknown_lines)}."
            )
        return text

    def to_cost_lines(self) -> list[CostLine]:
        return [line.to_cost_line() for line in self.lines]

    def assumption(self, at: datetime) -> str:
        return (
            f"cost lines loaded from the reviewed template {self.name!r} for "
            f"{self.route_label()}, established by {self.reviewer} on "
            f"{self.reviewed_at.date().isoformat()} from {self.basis}"
            + (" (review lapsed)" if self.is_stale(at) else "")
        )


# -- storage -------------------------------------------------------------


def save(
    conn: sqlite3.Connection,
    *,
    name: str,
    destination_country: str,
    basis: str,
    reviewer: str,
    reviewed_at: datetime,
    lines: list[TemplateLine],
    now: datetime,
    is_demo: bool,
    currency: str = "EUR",
    origin_country: str | None = None,
    model_family: str | None = None,
    review_due_at: datetime | None = None,
    notes: str | None = None,
    template_id: str | None = None,
) -> str:
    """Store a template. A reviewer, a basis and at least one line are required."""
    for label, value in (
        ("a name", name),
        ("a destination country", destination_country),
        ("a basis", basis),
        ("a reviewer", reviewer),
    ):
        if not str(value or "").strip():
            raise TemplateError(
                f"a cost template needs {label}: these figures get reused, so it has to be "
                f"possible to see where they came from"
            )
    if not lines:
        raise TemplateError("a template with no cost lines has nothing to contribute")

    duplicates = [
        label
        for label in {line.label for line in lines}
        if [line.label for line in lines].count(label) > 1
    ]
    if duplicates:
        raise TemplateError(f"duplicate cost line(s): {', '.join(sorted(duplicates))}")

    for line in lines:
        if line.amount is not None and line.amount.currency != currency.upper():
            raise TemplateError(
                f"line {line.label!r} is in {line.amount.currency} but the template is in "
                f"{currency.upper()}"
            )
        if line.amount is not None and line.amount.minor_units < 0:
            raise TemplateError(f"line {line.label!r} has a negative amount")

    due = review_due_at or (reviewed_at + timedelta(days=DEFAULT_REVIEW_PERIOD_DAYS))
    stamp = to_iso(now)
    row_id = template_id or new_id("ctpl")

    if template_id:
        conn.execute(
            """UPDATE cost_templates SET name = ?, origin_country = ?, destination_country = ?,
               model_family = ?, currency = ?, basis = ?, reviewer = ?, reviewed_at = ?,
               review_due_at = ?, notes = ?, updated_at = ? WHERE id = ?""",
            (
                name,
                (origin_country or None) and origin_country.upper(),
                destination_country.upper(),
                model_family,
                currency.upper(),
                basis,
                reviewer,
                to_iso(reviewed_at),
                to_iso(due),
                notes,
                stamp,
                row_id,
            ),
        )
        conn.execute("DELETE FROM cost_template_lines WHERE template_id = ?", (row_id,))
    else:
        conn.execute(
            """INSERT INTO cost_templates
               (id, name, origin_country, destination_country, model_family, currency, basis,
                reviewer, reviewed_at, review_due_at, notes, retired_at, is_demo, created_at,
                updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)""",
            (
                row_id,
                name,
                (origin_country or None) and origin_country.upper(),
                destination_country.upper(),
                model_family,
                currency.upper(),
                basis,
                reviewer,
                to_iso(reviewed_at),
                to_iso(due),
                notes,
                1 if is_demo else 0,
                stamp,
                stamp,
            ),
        )

    for order, line in enumerate(lines):
        conn.execute(
            """INSERT INTO cost_template_lines
               (id, template_id, label, amount_minor, is_confirmed_zero, tax_treatment,
                already_in_purchase_price, required, evidence_note, sort_order, is_demo)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_id("ctl"),
                row_id,
                line.label,
                None if line.amount is None else line.amount.minor_units,
                1 if line.confirmed_zero else 0,
                line.tax_treatment,
                1 if line.already_in_purchase_price else 0,
                1 if line.required else 0,
                line.evidence_note,
                order,
                1 if is_demo else 0,
            ),
        )
    return row_id


def retire(conn: sqlite3.Connection, template_id: str, *, now: datetime) -> None:
    """Stop offering a template without deleting what it was."""
    conn.execute(
        "UPDATE cost_templates SET retired_at = ?, updated_at = ? WHERE id = ?",
        (to_iso(now), to_iso(now), template_id),
    )


def get(conn: sqlite3.Connection, template_id: str) -> CostTemplate | None:
    row = conn.execute("SELECT * FROM cost_templates WHERE id = ?", (template_id,)).fetchone()
    if row is None:
        return None
    return _hydrate(conn, row)


def list_templates(
    conn: sqlite3.Connection,
    *,
    destination_country: str | None = None,
    origin_country: str | None = None,
    model_family: str | None = None,
    include_retired: bool = False,
) -> list[CostTemplate]:
    """Templates that could apply, most specific route first."""
    clauses = [] if include_retired else ["retired_at IS NULL"]
    params: list[Any] = []
    if destination_country:
        clauses.append("destination_country = ?")
        params.append(destination_country.upper())
    if origin_country:
        clauses.append("(origin_country IS NULL OR origin_country = ?)")
        params.append(origin_country.upper())
    if model_family:
        clauses.append("(model_family IS NULL OR model_family = ?)")
        params.append(model_family)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM cost_templates {where} "
        f"ORDER BY origin_country IS NULL, model_family IS NULL, reviewed_at DESC, id",
        params,
    ).fetchall()
    return [_hydrate(conn, row) for row in rows]


def _hydrate(conn: sqlite3.Connection, row: sqlite3.Row) -> CostTemplate:
    currency = row["currency"]
    lines = [
        TemplateLine(
            label=line["label"],
            amount=(
                None
                if line["amount_minor"] is None
                else Money(line["amount_minor"], currency)
            ),
            confirmed_zero=bool(line["is_confirmed_zero"]),
            tax_treatment=line["tax_treatment"],
            already_in_purchase_price=bool(line["already_in_purchase_price"]),
            required=bool(line["required"]),
            evidence_note=line["evidence_note"],
        )
        for line in conn.execute(
            "SELECT * FROM cost_template_lines WHERE template_id = ? ORDER BY sort_order",
            (row["id"],),
        ).fetchall()
    ]
    return CostTemplate(
        id=row["id"],
        name=row["name"],
        destination_country=row["destination_country"],
        basis=row["basis"],
        reviewer=row["reviewer"],
        reviewed_at=from_iso(row["reviewed_at"]),
        currency=currency,
        origin_country=row["origin_country"],
        model_family=row["model_family"],
        review_due_at=from_iso(row["review_due_at"]),
        notes=row["notes"],
        lines=lines,
    )


def best_for_route(
    conn: sqlite3.Connection,
    *,
    origin_country: str | None,
    destination_country: str | None,
    model_family: str | None = None,
) -> CostTemplate | None:
    """The most specific applicable template, or nothing.

    Nothing is applied automatically; this only offers a default for the user to
    accept or ignore.
    """
    if not destination_country:
        return None
    candidates = list_templates(
        conn,
        destination_country=destination_country,
        origin_country=origin_country,
        model_family=model_family,
    )
    return candidates[0] if candidates else None


def due_for_review(
    conn: sqlite3.Connection, *, now: datetime, within_days: int = 30
) -> list[sqlite3.Row]:
    horizon = to_iso(now + timedelta(days=within_days))
    return conn.execute(
        "SELECT * FROM cost_templates WHERE retired_at IS NULL AND review_due_at IS NOT NULL "
        "AND review_due_at <= ? ORDER BY review_due_at",
        (horizon,),
    ).fetchall()
