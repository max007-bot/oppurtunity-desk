"""Tasks, recorded interactions and message drafts."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import new_id
from .base import dumps, fingerprint


# -- tasks ---------------------------------------------------------------

TASK_LABELS = {
    "check_availability": "Check availability",
    "ask_buyer": "Ask buyer",
    "review_costs": "Review costs",
    "ready_for_call_preparation": "Ready for call preparation",
    "reconfirm_brief": "Reconfirm requirement",
    "verify_contact": "Verify contact",
    "review_duplicate": "Review possible duplicate",
}


def create_task(
    conn: sqlite3.Connection,
    *,
    action: str,
    due_at: str,
    now: str,
    is_demo: bool,
    company_id: str | None = None,
    match_id: str | None = None,
    offer_id: str | None = None,
    channel: str | None = None,
    notes: str | None = None,
    label: str | None = None,
) -> str | None:
    """Create a task unless the same one is already open.

    The dedupe key is what stops a Streamlit rerun or a repeated recompute piling
    up identical reminders.
    """
    dedupe_key = fingerprint("task", action, company_id, match_id, offer_id, due_at[:10])
    task_id = new_id("tsk")
    try:
        conn.execute(
            """INSERT INTO tasks
               (id, company_id, match_id, offer_id, action, label, due_at, channel, state,
                completed_at, notes, dedupe_key, created_at, is_demo)
               VALUES (?,?,?,?,?,?,?,?, 'open', NULL, ?, ?, ?, ?)""",
            (
                task_id,
                company_id,
                match_id,
                offer_id,
                action,
                label or TASK_LABELS.get(action, action),
                due_at,
                channel,
                notes,
                dedupe_key,
                now,
                1 if is_demo else 0,
            ),
        )
    except sqlite3.IntegrityError:
        return None
    return task_id


def list_tasks(
    conn: sqlite3.Connection, *, state: str = "open", due_before: str | None = None
) -> list[sqlite3.Row]:
    params: list[Any] = [state]
    clause = ""
    if due_before:
        clause = "AND due_at <= ?"
        params.append(due_before)
    return conn.execute(
        f"SELECT * FROM tasks WHERE state = ? {clause} ORDER BY due_at", params
    ).fetchall()


def complete_task(conn: sqlite3.Connection, task_id: str, *, now: str) -> None:
    conn.execute(
        "UPDATE tasks SET state = 'done', completed_at = ? WHERE id = ?", (now, task_id)
    )


# -- interactions --------------------------------------------------------


def record_interaction(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    channel: str,
    occurred_at: str,
    outcome: str,
    now: str,
    is_demo: bool,
    contact_id: str | None = None,
    match_id: str | None = None,
    brief_id: str | None = None,
    meeting_status: str | None = None,
    notes: str | None = None,
    recorded_by: str | None = None,
    evidence_id: str | None = None,
    source_id: str | None = None,
    external_record_id: str | None = None,
) -> str | None:
    """Record one conversation.

    Returns ``None`` when this source record is already stored, so re-importing a
    file or replaying a snapshot does not double-count a conversation.
    """
    if source_id and external_record_id:
        already = conn.execute(
            "SELECT id FROM interactions WHERE source_id = ? AND external_record_id = ?",
            (source_id, external_record_id),
        ).fetchone()
        if already is not None:
            return None

    interaction_id = new_id("int")
    conn.execute(
        """INSERT INTO interactions
           (id, source_id, external_record_id, company_id, contact_id, match_id, brief_id,
            channel, occurred_at, outcome, meeting_status, notes, evidence_id, recorded_by,
            created_at, is_demo)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            interaction_id,
            source_id,
            external_record_id,
            company_id,
            contact_id,
            match_id,
            brief_id,
            channel,
            occurred_at,
            outcome,
            meeting_status,
            notes,
            evidence_id,
            recorded_by,
            now,
            1 if is_demo else 0,
        ),
    )
    return interaction_id


def list_interactions(
    conn: sqlite3.Connection, *, company_id: str | None = None, limit: int = 100
) -> list[sqlite3.Row]:
    if company_id:
        return conn.execute(
            "SELECT * FROM interactions WHERE company_id = ? ORDER BY occurred_at DESC LIMIT ?",
            (company_id, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM interactions ORDER BY occurred_at DESC LIMIT ?", (limit,)
    ).fetchall()


# -- drafts --------------------------------------------------------------


def save_draft(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    channel: str,
    body: str,
    status: str,
    facts_used: list[str],
    unresolved_fields: list[str],
    block_reasons: list[str],
    policy_checked_at: str,
    facts_fingerprint: str,
    now: str,
    is_demo: bool,
    match_id: str | None = None,
    brief_id: str | None = None,
    offer_id: str | None = None,
    contact_id: str | None = None,
    expires_at: str | None = None,
) -> str:
    draft_id = new_id("drf")
    conn.execute(
        """INSERT INTO drafts
           (id, match_id, brief_id, offer_id, company_id, contact_id, channel, body, facts_used,
            unresolved_fields, status, block_reasons, policy_checked_at, facts_fingerprint,
            expires_at, created_at, is_demo)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            draft_id,
            match_id,
            brief_id,
            offer_id,
            company_id,
            contact_id,
            channel,
            body,
            dumps(facts_used),
            dumps(unresolved_fields),
            status,
            dumps(block_reasons),
            policy_checked_at,
            facts_fingerprint,
            expires_at,
            now,
            1 if is_demo else 0,
        ),
    )
    return draft_id


def get_draft(conn: sqlite3.Connection, draft_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()


def list_drafts(conn: sqlite3.Connection, *, company_id: str | None = None) -> list[sqlite3.Row]:
    if company_id:
        return conn.execute(
            "SELECT * FROM drafts WHERE company_id = ? ORDER BY created_at DESC", (company_id,)
        ).fetchall()
    return conn.execute("SELECT * FROM drafts ORDER BY created_at DESC").fetchall()


def mark_draft_blocked(
    conn: sqlite3.Connection, draft_id: str, *, reasons: list[str]
) -> None:
    conn.execute(
        "UPDATE drafts SET status = 'blocked', block_reasons = ? WHERE id = ?",
        (dumps(reasons), draft_id),
    )
