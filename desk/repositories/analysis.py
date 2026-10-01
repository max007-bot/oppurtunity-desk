"""Persistence for comparable sets, matches and deal scenarios."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import new_id
from .base import dumps


# -- comparable sets -----------------------------------------------------


def save_comparable_set(
    conn: sqlite3.Connection,
    *,
    target_offer_id: str,
    tax_basis: str,
    vat_regime: str,
    result_status: str,
    rule_version: str,
    analysed_at: str,
    is_demo: bool,
    filters: dict[str, Any] | None = None,
    median_minor: int | None = None,
    low_minor: int | None = None,
    high_minor: int | None = None,
    currency: str | None = None,
    included_count: int = 0,
    distinct_vehicles: int = 0,
    median_before_adjustments_minor: int | None = None,
    adjustments_applied: int = 0,
    conversions_applied: int = 0,
    members: list[dict[str, Any]] | None = None,
) -> str:
    set_id = new_id("cmp_set")
    conn.execute(
        """INSERT INTO comparable_sets
           (id, target_offer_id, filters, tax_basis, vat_regime, included_count,
            distinct_vehicles, median_minor, low_minor, high_minor, currency, result_status,
            rule_version, analysed_at, is_demo, median_before_adjustments_minor,
            adjustments_applied, conversions_applied)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            set_id,
            target_offer_id,
            dumps(filters or {}),
            tax_basis,
            vat_regime,
            included_count,
            distinct_vehicles,
            median_minor,
            low_minor,
            high_minor,
            currency,
            result_status,
            rule_version,
            analysed_at,
            1 if is_demo else 0,
            median_before_adjustments_minor,
            adjustments_applied,
            conversions_applied,
        ),
    )
    for member in members or []:
        conn.execute(
            """INSERT INTO comparable_members
               (id, comparable_set_id, offer_id, observation_id, included, reason, price_minor,
                currency, observed_at, is_demo, observed_price_minor, observed_currency,
                adjustment_minor, adjustment_reason, fx_rate, fx_rate_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_id("cmp_mem"),
                set_id,
                member.get("offer_id"),
                member.get("observation_id"),
                1 if member.get("included") else 0,
                member.get("reason", ""),
                member.get("price_minor"),
                member.get("currency"),
                member.get("observed_at"),
                1 if is_demo else 0,
                member.get("observed_price_minor"),
                member.get("observed_currency"),
                member.get("adjustment_minor"),
                member.get("adjustment_reason"),
                member.get("fx_rate"),
                member.get("fx_rate_date"),
            ),
        )
    return set_id


def latest_comparable_set(conn: sqlite3.Connection, offer_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM comparable_sets WHERE target_offer_id = ? ORDER BY analysed_at DESC LIMIT 1",
        (offer_id,),
    ).fetchone()


def comparable_members(conn: sqlite3.Connection, set_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM comparable_members WHERE comparable_set_id = ? "
        "ORDER BY included DESC, price_minor",
        (set_id,),
    ).fetchall()


# -- matches -------------------------------------------------------------


def save_match(
    conn: sqlite3.Connection,
    *,
    offer_id: str,
    brief_id: str | None,
    company_id: str,
    fit_status: str,
    score: int,
    score_breakdown: dict[str, Any],
    passed_rules: list[str],
    failed_rules: list[str],
    unknown_rules: list[str],
    evidence_refs: list[str],
    computed_at: str,
    rule_version: str,
    is_demo: bool,
    supply_confirmed_at: str | None = None,
    expires_at: str | None = None,
) -> str:
    """Store a match, replacing any earlier computation for the same triple.

    Recomputing is idempotent: one row per (offer, brief, company).
    """
    existing = conn.execute(
        "SELECT id FROM matches WHERE offer_id = ? AND IFNULL(brief_id,'') = IFNULL(?,'') "
        "AND company_id = ?",
        (offer_id, brief_id, company_id),
    ).fetchone()
    payload = {
        "company_id": company_id,
        "fit_status": fit_status,
        "score": score,
        "score_breakdown": dumps(score_breakdown),
        "passed_rules": dumps(passed_rules),
        "failed_rules": dumps(failed_rules),
        "unknown_rules": dumps(unknown_rules),
        "evidence_refs": dumps(evidence_refs),
        "supply_confirmed_at": supply_confirmed_at,
        "computed_at": computed_at,
        "expires_at": expires_at,
        "rule_version": rule_version,
    }
    if existing is not None:
        assignments = ", ".join(f"{key} = :{key}" for key in payload)
        conn.execute(
            f"UPDATE matches SET {assignments} WHERE id = :id", {**payload, "id": existing["id"]}
        )
        return existing["id"]

    match_id = new_id("mch")
    columns = ", ".join(payload.keys())
    placeholders = ", ".join(f":{key}" for key in payload)
    conn.execute(
        f"""INSERT INTO matches (id, offer_id, brief_id, is_demo, {columns})
            VALUES (:id, :offer_id, :brief_id, :is_demo, {placeholders})""",
        {
            **payload,
            "id": match_id,
            "offer_id": offer_id,
            "brief_id": brief_id,
            "is_demo": 1 if is_demo else 0,
        },
    )
    return match_id


def get_match(conn: sqlite3.Connection, match_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()


def list_matches(
    conn: sqlite3.Connection,
    *,
    fit_statuses: tuple[str, ...] | None = None,
    company_id: str | None = None,
    offer_id: str | None = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if fit_statuses:
        clauses.append(f"fit_status IN ({','.join('?' * len(fit_statuses))})")
        params.extend(fit_statuses)
    if company_id:
        clauses.append("company_id = ?")
        params.append(company_id)
    if offer_id:
        clauses.append("offer_id = ?")
        params.append(offer_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(
        f"SELECT * FROM matches {where} ORDER BY score DESC, computed_at DESC", params
    ).fetchall()


# -- scenarios -----------------------------------------------------------


def save_scenario(
    conn: sqlite3.Connection, *, is_demo: bool, costs: list[dict[str, Any]], **fields: Any
) -> str:
    scenario_id = new_id("scn")
    columns = list(fields.keys())
    placeholders = ", ".join(f":{key}" for key in columns)
    conn.execute(
        f"""INSERT INTO scenarios (id, is_demo, {', '.join(columns)})
            VALUES (:id, :is_demo, {placeholders})""",
        {**fields, "id": scenario_id, "is_demo": 1 if is_demo else 0},
    )
    for cost in costs:
        conn.execute(
            """INSERT INTO scenario_costs
               (id, scenario_id, label, amount_minor, currency, is_confirmed_zero, tax_treatment,
                already_in_purchase_price, required, paid_at, refunded_at, notes, is_demo)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_id("cst"),
                scenario_id,
                cost["label"],
                cost.get("amount_minor"),
                cost.get("currency", "EUR"),
                1 if cost.get("is_confirmed_zero") else 0,
                cost.get("tax_treatment", "unknown"),
                1 if cost.get("already_in_purchase_price") else 0,
                1 if cost.get("required", True) else 0,
                cost.get("paid_at"),
                cost.get("refunded_at"),
                cost.get("notes"),
                1 if is_demo else 0,
            ),
        )
    return scenario_id


def get_scenario(conn: sqlite3.Connection, scenario_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()


def latest_scenario_for_offer(conn: sqlite3.Connection, offer_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM scenarios WHERE offer_id = ? ORDER BY computed_at DESC LIMIT 1",
        (offer_id,),
    ).fetchone()


def scenario_costs(conn: sqlite3.Connection, scenario_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM scenario_costs WHERE scenario_id = ? ORDER BY label", (scenario_id,)
    ).fetchall()
