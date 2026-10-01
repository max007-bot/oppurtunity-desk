"""Recording a source review.

Approving a source is the decision that lets this application talk to somebody
else's service, so it is made deliberately, with a named reviewer, a stated
scope, a stated evidence kind and a date it lapses. Every review is kept as
history rather than overwriting the last one.

The two evidence kinds the manual insists on distinguishing:

* ``published_terms_reviewed`` - the project owner read the published route and
  its conditions. This does **not** mean a provider or a government approved this
  project, and the wording here must never imply that.
* ``contract_or_account_scope`` - a commercial agreement or account scope exists.

Approving a source in demo mode is a rehearsal: demo sources still cannot fetch.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..clock import to_iso
from ..config import Config, Mode, SourceStatus
from ..db import new_id, record_change
from ..repositories.base import dumps
from .source_policy import SourceRecord

EVIDENCE_KINDS = {
    "published_terms_reviewed": (
        "The published route and its conditions were read and recorded by the project owner. "
        "This is not an individual approval by the provider or by any government."
    ),
    "contract_or_account_scope": (
        "A contract, licence or activated account defines the permitted scope."
    ),
    "owner_authorisation": (
        "The owner of the data authorised this specific use in writing."
    ),
    "unknown": "No evidence recorded.",
}

# Approvals do not run forever. A review with no stated end date gets this one.
DEFAULT_REVIEW_PERIOD_DAYS = 180


@dataclass
class ReviewRequest:
    """What a reviewer is asserting."""

    source_id: str
    status: str
    reviewer: str
    reviewed_at: datetime
    approved_use: str | None = None
    approval_evidence: str | None = None
    evidence_kind: str = "unknown"
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    retention_rule: str | None = None
    retention_days: int | None = None
    raw_retention_allowed: bool = True
    export_allowed: bool = False
    rate_limit_per_minute: int | None = None
    max_concurrency: int = 1
    review_due_at: datetime | None = None
    notes: str | None = None


@dataclass
class ReviewResult:
    review_id: str
    source_id: str
    previous_status: str
    status: str
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = f"{self.source_id}: {self.previous_status} → {self.status}."
        if self.warnings:
            text += " " + " ".join(self.warnings)
        return text


class ReviewRefused(ValueError):
    """Raised when a review would assert something it has not established."""


def validate(request: ReviewRequest, source: SourceRecord, config: Config) -> list[str]:
    """Check a review before it is written. Returns non-blocking warnings.

    Anything that would let an unevidenced approval through raises instead.
    """
    if request.status not in {s.value for s in SourceStatus}:
        raise ReviewRefused(f"unknown status {request.status!r}")
    if not str(request.reviewer).strip():
        raise ReviewRefused("a review must name its reviewer")
    if request.evidence_kind not in EVIDENCE_KINDS:
        raise ReviewRefused(f"unknown evidence kind {request.evidence_kind!r}")

    warnings: list[str] = []

    if request.status != SourceStatus.APPROVED.value:
        return warnings

    # From here on the reviewer is approving something.
    missing = [
        name
        for name, value in (
            ("approved use", request.approved_use),
            ("approval evidence", request.approval_evidence),
        )
        if not (value and str(value).strip())
    ]
    if missing:
        raise ReviewRefused(
            f"an approval must state {' and '.join(missing)}: what this source may be used for, "
            f"and what that permission rests on"
        )

    if request.evidence_kind == "unknown":
        raise ReviewRefused(
            "state what kind of evidence the approval rests on: published terms that were read, "
            "a contract or account scope, or the data owner's written authorisation"
        )

    if source.access_mode == "licensed_api" and request.evidence_kind == "published_terms_reviewed":
        raise ReviewRefused(
            f"{source.name} is a licensed source. Reading its published terms is not the same as "
            f"holding the account scope they describe; record the contract or account scope "
            f"instead, or leave it blocked"
        )

    if request.review_due_at is None:
        warnings.append(
            f"no review-due date was given, so one was set {DEFAULT_REVIEW_PERIOD_DAYS} days out; "
            f"approval lapses on its own."
        )
    elif request.review_due_at <= request.reviewed_at:
        raise ReviewRefused("the review-due date must be after the review date")

    if source.access_mode in {"public_api", "approved_website"} and not request.allowed_hosts:
        raise ReviewRefused(
            "list the hosts this source may be fetched from; an approval without a host scope "
            "would permit any address"
        )

    if source.access_mode == "approved_website" and not request.allowed_paths:
        warnings.append(
            "no path prefixes were listed, so every path on the approved hosts is in scope. "
            "This adapter still does not crawl, but consider narrowing it."
        )

    if request.export_allowed and not request.retention_rule:
        warnings.append(
            "export is permitted but no retention rule was recorded; derived data will be kept "
            "indefinitely until one is."
        )

    if config.mode is Mode.DEMO:
        warnings.append(
            "This is demo mode, so the approval is a rehearsal: demo sources still cannot fetch."
        )

    return warnings


def apply_review(
    conn: sqlite3.Connection,
    *,
    request: ReviewRequest,
    source: SourceRecord,
    config: Config,
    now: datetime,
) -> ReviewResult:
    """Validate, record the review as history, then update the source row."""
    warnings = validate(request, source, config)

    review_due = request.review_due_at
    if request.status == SourceStatus.APPROVED.value and review_due is None:
        review_due = request.reviewed_at + timedelta(days=DEFAULT_REVIEW_PERIOD_DAYS)

    stamp = to_iso(now)
    review_id = new_id("rev")
    conn.execute(
        """INSERT INTO source_reviews
           (id, source_id, previous_status, status, approved_use, approval_evidence,
            evidence_kind, allowed_hosts, allowed_paths, retention_rule, retention_days,
            raw_retention_allowed, export_allowed, rate_limit_per_minute, max_concurrency,
            reviewer, reviewed_at, review_due_at, notes, mode, is_demo, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            review_id,
            request.source_id,
            source.status,
            request.status,
            request.approved_use,
            request.approval_evidence,
            request.evidence_kind,
            dumps(request.allowed_hosts),
            dumps(request.allowed_paths),
            request.retention_rule,
            request.retention_days,
            1 if request.raw_retention_allowed else 0,
            1 if request.export_allowed else 0,
            request.rate_limit_per_minute,
            request.max_concurrency,
            request.reviewer,
            to_iso(request.reviewed_at),
            None if review_due is None else to_iso(review_due),
            request.notes,
            config.mode.value,
            1 if config.is_demo else 0,
            stamp,
        ),
    )

    conn.execute(
        """UPDATE sources SET
             status = :status,
             approved_use = :approved_use,
             approval_evidence = :approval_evidence,
             allowed_hosts = :allowed_hosts,
             allowed_paths = :allowed_paths,
             retention_rule = :retention_rule,
             retention_days = :retention_days,
             raw_retention_allowed = :raw_retention_allowed,
             export_allowed = :export_allowed,
             rate_limit_per_minute = :rate_limit_per_minute,
             max_concurrency = :max_concurrency,
             reviewer = :reviewer,
             reviewed_at = :reviewed_at,
             review_due_at = :review_due_at,
             notes = :notes,
             updated_at = :updated_at
           WHERE id = :id""",
        {
            "id": request.source_id,
            "status": request.status,
            "approved_use": request.approved_use,
            "approval_evidence": request.approval_evidence,
            "allowed_hosts": dumps(request.allowed_hosts),
            "allowed_paths": dumps(request.allowed_paths),
            "retention_rule": request.retention_rule,
            "retention_days": request.retention_days,
            "raw_retention_allowed": 1 if request.raw_retention_allowed else 0,
            "export_allowed": 1 if request.export_allowed else 0,
            "rate_limit_per_minute": request.rate_limit_per_minute,
            "max_concurrency": request.max_concurrency,
            "reviewer": request.reviewer,
            "reviewed_at": to_iso(request.reviewed_at),
            "review_due_at": None if review_due is None else to_iso(review_due),
            "notes": request.notes,
            "updated_at": stamp,
        },
    )

    record_change(
        conn,
        entity_type="source",
        entity_id=request.source_id,
        action="reviewed",
        field_name="status",
        old_value=source.status,
        new_value=request.status,
        actor=request.reviewer,
        occurred_at=stamp,
        is_demo=config.is_demo,
    )

    return ReviewResult(
        review_id=review_id,
        source_id=request.source_id,
        previous_status=source.status,
        status=request.status,
        warnings=warnings,
    )


def history(conn: sqlite3.Connection, source_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM source_reviews WHERE source_id = ? ORDER BY reviewed_at DESC",
        (source_id,),
    ).fetchall()


def due_for_review(
    conn: sqlite3.Connection, *, now: datetime, within_days: int = 30
) -> list[sqlite3.Row]:
    """Approved sources whose review date has passed or is close."""
    horizon = to_iso(now + timedelta(days=within_days))
    return conn.execute(
        "SELECT * FROM sources WHERE status = 'approved' AND review_due_at IS NOT NULL "
        "AND review_due_at <= ? ORDER BY review_due_at",
        (horizon,),
    ).fetchall()
