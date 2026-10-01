"""Dated follow-up tasks derived from the state of the records.

Tasks are created only from real conditions - a stale confirmation, an unknown
requirement, an incomplete cost sheet - and are deduplicated, so opening a page or
recomputing never manufactures activity.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..clock import is_stale, to_iso
from ..config import Config, FitStatus
from ..repositories import companies as companies_repo
from ..repositories import followup as followup_repo
from ..repositories import supply as supply_repo
from .matching import MatchResult


def _describe(offer) -> str:
    """A short, stable way to name one offer in a task label."""
    if offer is None:
        return "this requirement"
    seller = offer.seller_name or "seller unknown"
    return f"{offer.external_record_id} ({seller})"


@dataclass
class TaskSuggestion:
    action: str
    label: str
    due_at: datetime
    reason: str
    company_id: str | None = None
    offer_id: str | None = None
    match_id: str | None = None


def suggest_tasks(
    conn: sqlite3.Connection,
    *,
    match: MatchResult,
    match_id: str | None,
    now: datetime,
    config: Config,
) -> list[TaskSuggestion]:
    """Work out what actually needs doing next for one match."""
    out: list[TaskSuggestion] = []
    offer = supply_repo.get_offer(conn, match.offer_id)
    brief = (
        companies_repo.get_brief(conn, match.brief_id) if match.brief_id else None
    )

    # Naming the car matters: an account with several offers would otherwise show
    # a column of identical, unactionable reminders.
    car = _describe(offer)

    if offer is not None and is_stale(
        now, offer.availability_confirmed_at, hours=config.freshness.supply_hours
    ):
        out.append(
            TaskSuggestion(
                action="check_availability",
                label=f"Check availability: {car}",
                due_at=now + timedelta(hours=4),
                reason=(
                    f"the supply confirmation for {car} is stale, so it cannot be described as "
                    f"currently available"
                ),
                company_id=match.company_id,
                offer_id=match.offer_id,
                match_id=match_id,
            )
        )

    if match.unknown:
        out.append(
            TaskSuggestion(
                action="ask_buyer",
                label=f"Ask buyer about {car}",
                due_at=now + timedelta(days=1),
                reason=(
                    f"{len(match.unknown)} requirement(s) are unverified: "
                    f"{', '.join(rule.name for rule in match.unknown)}"
                ),
                company_id=match.company_id,
                offer_id=match.offer_id,
                match_id=match_id,
            )
        )

    if brief is not None and brief.is_expired(now):
        out.append(
            TaskSuggestion(
                action="reconfirm_brief",
                label="Reconfirm requirement",
                due_at=now + timedelta(days=1),
                reason="the buying brief has expired and needs reconfirmation",
                company_id=match.company_id,
                match_id=match_id,
            )
        )
    elif brief is not None and is_stale(
        now, brief.conversation_date, days=config.freshness.brief_days
    ):
        out.append(
            TaskSuggestion(
                action="reconfirm_brief",
                label="Reconfirm requirement",
                due_at=now + timedelta(days=3),
                reason=(
                    f"last discussed {brief.conversation_date.date().isoformat()}, beyond the "
                    f"{config.freshness.brief_days:g}-day review window"
                ),
                company_id=match.company_id,
                match_id=match_id,
            )
        )

    if match.fit_status is FitStatus.SPECIFICATION_FIT:
        scenario = conn.execute(
            "SELECT status FROM scenarios WHERE offer_id = ? ORDER BY computed_at DESC LIMIT 1",
            (match.offer_id,),
        ).fetchone()
        if scenario is None or scenario["status"] != "complete":
            out.append(
                TaskSuggestion(
                    action="review_costs",
                    label="Review costs",
                    due_at=now + timedelta(days=1),
                    reason="the scenario is incomplete, so the economics are not yet reviewed",
                    company_id=match.company_id,
                    offer_id=match.offer_id,
                    match_id=match_id,
                )
            )
        elif not match.failed and not match.unknown:
            out.append(
                TaskSuggestion(
                    action="ready_for_call_preparation",
                    label="Ready for call preparation",
                    due_at=now + timedelta(hours=8),
                    reason="specification fit with reviewed economics and fresh supply",
                    company_id=match.company_id,
                    offer_id=match.offer_id,
                    match_id=match_id,
                )
            )

    return out


def persist_suggestions(
    conn: sqlite3.Connection,
    suggestions: list[TaskSuggestion],
    *,
    now: datetime,
    is_demo: bool,
) -> int:
    """Create the suggested tasks, skipping any that already exist."""
    created = 0
    for suggestion in suggestions:
        task_id = followup_repo.create_task(
            conn,
            action=suggestion.action,
            label=suggestion.label,
            due_at=to_iso(suggestion.due_at),
            now=to_iso(now),
            is_demo=is_demo,
            company_id=suggestion.company_id,
            match_id=suggestion.match_id,
            offer_id=suggestion.offer_id,
            notes=suggestion.reason,
        )
        if task_id:
            created += 1
    return created


def stale_confirmations(
    conn: sqlite3.Connection, *, now: datetime, config: Config
) -> list[sqlite3.Row]:
    """Offers whose availability confirmation has gone stale."""
    cutoff = to_iso(now - timedelta(hours=config.freshness.supply_hours))
    return conn.execute(
        "SELECT * FROM offers WHERE availability_confirmed_at IS NULL "
        "OR availability_confirmed_at <= ? ORDER BY updated_at DESC",
        (cutoff,),
    ).fetchall()


def briefs_due_for_review(
    conn: sqlite3.Connection, *, now: datetime, config: Config
) -> list[sqlite3.Row]:
    cutoff = to_iso(now - timedelta(days=config.freshness.brief_days))
    return conn.execute(
        "SELECT * FROM buyer_briefs WHERE conversation_date <= ? "
        "OR (expires_at IS NOT NULL AND expires_at <= ?) ORDER BY conversation_date",
        (cutoff, to_iso(now)),
    ).fetchall()


def contacts_due_for_verification(
    conn: sqlite3.Connection, *, now: datetime, config: Config
) -> list[sqlite3.Row]:
    cutoff = to_iso(now - timedelta(days=config.freshness.contact_days))
    return conn.execute(
        "SELECT * FROM contacts WHERE verified_at IS NULL OR verified_at <= ?", (cutoff,)
    ).fetchall()
