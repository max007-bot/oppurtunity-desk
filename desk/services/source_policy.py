"""The single gate every live request must pass.

Nothing in this project reaches the network without a decision from this module:
it checks the application mode, the source's approval and expiry, the host and
path, the resolved IP address, and the retention and export rules. When a check
fails the answer is "stop", never "try the browser instead".
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence
from urllib.parse import urlparse

from ..clock import Clock, from_iso
from ..config import Config, Mode, SourceStatus
from ..db import Database


class PolicyDenied(PermissionError):
    """Raised when a caller tries to act without a permitting decision."""


@dataclass
class SourceRecord:
    """The stored register entry for one source."""

    id: str
    name: str
    category: str
    access_mode: str
    status: str
    base_url: str | None = None
    documentation_url: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    approved_use: str | None = None
    attribution: str | None = None
    retention_rule: str | None = None
    retention_days: int | None = None
    raw_retention_allowed: bool = True
    export_allowed: bool = False
    rate_limit_per_minute: int | None = None
    max_concurrency: int = 1
    approval_evidence: str | None = None
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    review_due_at: datetime | None = None
    last_fetch_at: datetime | None = None
    last_fetch_status: str | None = None
    is_demo: bool = True
    notes: str | None = None

    @classmethod
    def from_row(cls, row) -> "SourceRecord":
        return cls(
            id=row["id"],
            name=row["name"],
            category=row["category"],
            access_mode=row["access_mode"],
            status=row["status"],
            base_url=row["base_url"],
            documentation_url=row["documentation_url"],
            allowed_hosts=json.loads(row["allowed_hosts"] or "[]"),
            allowed_paths=json.loads(row["allowed_paths"] or "[]"),
            approved_use=row["approved_use"],
            attribution=row["attribution"],
            retention_rule=row["retention_rule"],
            retention_days=row["retention_days"],
            raw_retention_allowed=bool(row["raw_retention_allowed"]),
            export_allowed=bool(row["export_allowed"]),
            rate_limit_per_minute=row["rate_limit_per_minute"],
            max_concurrency=row["max_concurrency"],
            approval_evidence=row["approval_evidence"],
            reviewer=row["reviewer"],
            reviewed_at=from_iso(row["reviewed_at"]),
            review_due_at=from_iso(row["review_due_at"]),
            last_fetch_at=from_iso(row["last_fetch_at"]),
            last_fetch_status=row["last_fetch_status"],
            is_demo=bool(row["is_demo"]),
            notes=row["notes"],
        )


@dataclass
class Decision:
    """The result of a policy check, always with a displayable reason."""

    allowed: bool
    reason: str
    checks: list[str] = field(default_factory=list)

    def require(self) -> "Decision":
        if not self.allowed:
            raise PolicyDenied(self.reason)
        return self


class SourcePolicy:
    def __init__(self, db: Database, config: Config, clock: Clock) -> None:
        self.db = db
        self.config = config
        self.clock = clock

    # -- register access -------------------------------------------------
    def get(self, source_id: str) -> SourceRecord | None:
        row = self.db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
        return None if row is None else SourceRecord.from_row(row)

    def list_sources(self) -> list[SourceRecord]:
        rows = self.db.query("SELECT * FROM sources ORDER BY category, name")
        return [SourceRecord.from_row(row) for row in rows]

    def effective_status(self, source: SourceRecord) -> str:
        """Approval lapses on its own review-due date without anyone editing it."""
        if source.status != SourceStatus.APPROVED.value:
            return source.status
        if source.review_due_at is not None and source.review_due_at <= self.clock.now():
            return SourceStatus.EXPIRED.value
        return SourceStatus.APPROVED.value

    # -- gates -----------------------------------------------------------
    def may_fetch(self, source_id: str, url: str | None = None) -> Decision:
        """Whether a live fetch from this source may proceed right now."""
        checks: list[str] = []

        if self.config.mode is Mode.DEMO:
            return Decision(
                False,
                "demo mode never performs live fetches; switch to live mode with an "
                "approved source record",
                ["mode=demo"],
            )
        checks.append("mode=live")

        if not self.config.allow_network:
            return Decision(False, "network access is disabled by configuration", checks)
        checks.append("network enabled")

        source = self.get(source_id)
        if source is None:
            return Decision(False, f"no source register entry for {source_id!r}", checks)

        if source.is_demo:
            return Decision(
                False, f"source {source_id!r} is a demo fixture and has no live route", checks
            )

        status = self.effective_status(source)
        if status != SourceStatus.APPROVED.value:
            return Decision(
                False,
                f"source {source.name} is {status}; live fetching stays disabled until it is "
                f"reviewed again (it must not fall back to scraping)",
                checks,
            )
        checks.append(f"source approved by {source.reviewer} on {_date(source.reviewed_at)}")

        if source.access_mode in {"fixture", "file_import", "human_entry"}:
            return Decision(
                False,
                f"source {source.name} is a {source.access_mode} source and has no fetch route",
                checks,
            )

        if url is not None:
            url_decision = self.check_url(source, url)
            checks.extend(url_decision.checks)
            if not url_decision.allowed:
                return Decision(False, url_decision.reason, checks)

        return Decision(True, f"{source.name}: approved for {source.approved_use}", checks)

    def check_url(self, source: SourceRecord, url: str) -> Decision:
        """Host, scheme, path and address checks for one candidate URL."""
        checks: list[str] = []
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return Decision(False, f"scheme {parsed.scheme!r} is not permitted", checks)
        host = parsed.hostname
        if not host:
            return Decision(False, "URL has no host", checks)

        if source.allowed_hosts and host.lower() not in {h.lower() for h in source.allowed_hosts}:
            return Decision(
                False,
                f"host {host} is not in the approved host list for {source.name}; a successful "
                f"HTTP response would not create a right to fetch it",
                checks,
            )
        checks.append(f"host {host} approved")

        if source.allowed_paths:
            path = parsed.path or "/"
            if not any(path.startswith(prefix) for prefix in source.allowed_paths):
                return Decision(
                    False,
                    f"path {path} is outside the approved routes for {source.name}; this adapter "
                    f"does not crawl a whole site",
                    checks,
                )
            checks.append(f"path {parsed.path} approved")

        address_decision = check_address(host)
        checks.extend(address_decision.checks)
        if not address_decision.allowed:
            return Decision(False, address_decision.reason, checks)

        return Decision(True, f"{url} is within the approved scope", checks)

    def may_retain_raw(self, source_id: str) -> Decision:
        source = self.get(source_id)
        if source is None:
            return Decision(False, f"unknown source {source_id!r}")
        if not source.raw_retention_allowed:
            return Decision(
                False,
                f"{source.name} does not permit storing raw content; keep only the allowed "
                f"fields and a reference",
            )
        return Decision(True, f"{source.name} permits retaining the fetched content")

    def may_export(self, source_id: str) -> Decision:
        source = self.get(source_id)
        if source is None:
            return Decision(False, f"unknown source {source_id!r}")
        status = self.effective_status(source)
        if status == SourceStatus.EXPIRED.value:
            return Decision(
                False,
                f"{source.name}: the licence review expired on {_date(source.review_due_at)}, "
                f"so export is blocked; stored data stays under its retention rule",
            )
        if not source.export_allowed:
            return Decision(
                False, f"{source.name} does not permit exporting data derived from it"
            )
        return Decision(True, f"{source.name} permits export")

    def retention_expiry(self, source_id: str, observed_at: datetime) -> datetime | None:
        source = self.get(source_id)
        if source is None or source.retention_days is None:
            return None
        from datetime import timedelta

        return observed_at + timedelta(days=source.retention_days)

    def attribution_for(self, source_ids: Sequence[str]) -> list[str]:
        out: list[str] = []
        for source_id in dict.fromkeys(source_ids):
            source = self.get(source_id)
            if source and source.attribution:
                out.append(source.attribution)
        return out

    def record_fetch(self, source_id: str, status: str) -> None:
        with self.db.write() as conn:
            conn.execute(
                "UPDATE sources SET last_fetch_at = ?, last_fetch_status = ?, updated_at = ? "
                "WHERE id = ?",
                (self.clock.now_iso(), status, self.clock.now_iso(), source_id),
            )


# -- address safety ------------------------------------------------------

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "instance-data",
}


def check_address(host: str, *, resolver=None) -> Decision:
    """Refuse loopback, private, link-local and metadata destinations.

    Applied to the submitted host and again to every redirect target, so a
    redirect cannot turn an approved fetch into a request against a local service.
    The address is only resolved here; nothing is contacted.
    """
    checks: list[str] = []
    lowered = host.lower().rstrip(".")

    if lowered in BLOCKED_HOSTNAMES or lowered.endswith(".localhost") or lowered.endswith(".internal"):
        return Decision(False, f"host {host} is a local or metadata name", checks)

    resolve = resolver or _resolve
    try:
        addresses = resolve(lowered)
    except OSError as exc:
        return Decision(False, f"could not resolve {host}: {exc}", checks)
    if not addresses:
        return Decision(False, f"host {host} resolved to no address", checks)

    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return Decision(False, f"host {host} resolved to an unusable address {raw!r}", checks)
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return Decision(
                False,
                f"host {host} resolves to the non-public address {address}; the request is "
                f"blocked without contacting it",
                checks,
            )
        checks.append(f"{host} resolves to public {address}")
    return Decision(True, f"{host} resolves to public addresses only", checks)


def _resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return sorted({info[4][0] for info in infos})


def _date(value: datetime | None) -> str:
    return "unknown" if value is None else value.date().isoformat()
