"""Running an approved live source, and storing what came back.

This is the path `desk refresh-source` takes. It exists so that enabling a source
is a real capability rather than a button that reports success and does nothing.

The order is deliberate and never varies:

1. ask ``SourcePolicy`` whether this fetch may happen at all;
2. open a run record, so a failure is still accounted for;
3. call the connector, which is the only thing that touches the network;
4. map its records into the ordinary import contract and apply them in one
   transaction, exactly as a file import would be;
5. record field-level evidence for everything the source asserted;
6. close the run.

A refusal or a failure stops at the earliest possible step and leaves existing
records, and the last successful observation, untouched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..clock import Clock, to_iso
from ..config import Config
from ..connectors.approved_website import ApprovedWebsiteConnector
from ..connectors.ares import AresConnector
from ..connectors.base import Connector, ConnectorUnavailable
from ..connectors.france import FranceConnector
from ..connectors.overpass import OverpassConnector
from ..db import Database, new_id
from ..models import AdapterRecord, AdapterResult
from ..repositories import companies as companies_repo
from ..repositories import supply as supply_repo
from ..repositories.base import dumps
from . import observations as observations_service
from .imports import ImportReport, Importer, finish_run, start_run
from .source_policy import PolicyDenied, SourcePolicy

# Which adapter serves which register entry. A source with no entry here has no
# live route at all, whatever its status says.
CONNECTORS: dict[str, type[Connector]] = {
    "osm_overpass": OverpassConnector,
    "cz_ares": AresConnector,
    "fr_recherche_entreprises": FranceConnector,
    "approved_website": ApprovedWebsiteConnector,
}

# Fields of an adapter record that map onto a company import row. Anything else
# the source returned is kept as evidence rather than forced into a column.
COMPANY_FIELDS = {
    "external_id",
    "legal_name",
    "trading_name",
    "country",
    "region",
    "city",
    "address",
    "registry_id",
    "registry_verified",
    "website",
    "category",
    "buying_route",
    "buying_authority",
    "notes",
    "contact_name",
    "contact_role",
    "contact_email",
    "contact_phone",
    "contact_type",
}


@dataclass
class RefreshReport:
    source_id: str
    status: str  # ok | refused | unavailable | error
    reason: str = ""
    connector: str = ""
    connector_version: str = ""
    records_returned: int = 0
    created: int = 0
    updated: int = 0
    observations: int = 0
    events: int = 0
    evidence: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attribution: str | None = None
    run_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def summary(self) -> str:
        if self.status == "refused":
            return f"Refused: {self.reason}"
        if self.status == "unavailable":
            return f"{self.source_id} was not reachable: {self.reason}"
        if self.status == "error":
            return f"{self.source_id} failed: {self.reason}"
        parts = [
            f"{self.records_returned} record(s) returned",
            f"{self.created} created",
            f"{self.updated} updated",
        ]
        if self.observations:
            parts.append(f"{self.observations} observation(s)")
        if self.evidence:
            parts.append(f"{self.evidence} evidence entr(ies)")
        text = ", ".join(parts) + "."
        if self.attribution:
            text += f" Attribution: {self.attribution}"
        return text


def connector_for(
    source_id: str, *, policy: SourcePolicy, config: Config, clock: Clock
) -> Connector:
    if source_id not in CONNECTORS:
        raise ConnectorUnavailable(
            f"no adapter is implemented for {source_id!r}. That is a deliberate gap, not an "
            f"oversight: a source without an adapter has no live route, and none is invented."
        )
    return CONNECTORS[source_id](policy=policy, config=config, clock=clock)


def refresh(
    db: Database,
    config: Config,
    clock: Clock,
    *,
    source_id: str,
    request: Mapping[str, Any] | None = None,
    connector: Connector | None = None,
) -> RefreshReport:
    """Fetch from one approved source and store what it returned."""
    request = dict(request or {})
    policy = SourcePolicy(db, config, clock)

    decision = policy.may_fetch(source_id)
    if not decision.allowed:
        # Nothing is opened, nothing is written. A refusal is not a failed run.
        return RefreshReport(source_id=source_id, status="refused", reason=decision.reason)

    try:
        adapter = connector or connector_for(
            source_id, policy=policy, config=config, clock=clock
        )
    except ConnectorUnavailable as exc:
        return RefreshReport(source_id=source_id, status="refused", reason=str(exc))

    run_id = start_run(
        db,
        config,
        clock,
        kind="refresh",
        connector_id=source_id,
        connector_version=adapter.version,
    )
    _record_request(db, clock, run_id, request)

    report = RefreshReport(
        source_id=source_id,
        status="ok",
        connector=type(adapter).__name__,
        connector_version=adapter.version,
        run_id=run_id,
    )

    try:
        result = adapter.fetch(**request)
    except (ConnectorUnavailable, PolicyDenied) as exc:
        report.status = "unavailable"
        report.reason = str(exc)
        _mark_supply_unreachable(db, config, clock, source_id, str(exc), report)
        _close(db, clock, run_id, report)
        return report
    except Exception as exc:  # an adapter bug must not corrupt stored records
        report.status = "error"
        report.reason = f"{type(exc).__name__}: {exc}"
        _close(db, clock, run_id, report)
        return report

    report.records_returned = len(result.records)
    report.errors.extend(result.errors)
    report.attribution = result.attribution

    if not result.ok and not result.records:
        report.status = "unavailable" if result.fetch_status != "ok" else "error"
        report.reason = "; ".join(result.errors) or result.fetch_status
        _mark_supply_unreachable(db, config, clock, source_id, report.reason, report)
        _close(db, clock, run_id, report)
        return report

    _apply(db, config, clock, source_id, result, report, run_id)
    _close(db, clock, run_id, report)
    return report


# -- applying ------------------------------------------------------------


def _apply(
    db: Database,
    config: Config,
    clock: Clock,
    source_id: str,
    result: AdapterResult,
    report: RefreshReport,
    run_id: str,
) -> None:
    """Map records onto the import contract and apply them transactionally."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    contacts: list[AdapterRecord] = []

    for record in result.records:
        if record.entity_type == "company":
            grouped.setdefault("companies", []).append(_company_row(record))
        elif record.entity_type == "contact":
            contacts.append(record)
        elif record.entity_type == "offer":
            grouped.setdefault("offers", []).append(dict(record.fields))
        elif record.entity_type == "observation":
            grouped.setdefault("comparables", []).append(dict(record.fields))
        else:
            report.notes.append(
                f"record type {record.entity_type!r} has no import route and was not stored"
            )

    importer = Importer(db, config, clock)
    for kind, rows in grouped.items():
        outcome: ImportReport = importer.apply(
            kind, rows, source_id=source_id, run_id=run_id
        )
        report.created += outcome.created
        report.updated += outcome.updated
        report.observations += outcome.observations
        report.events += outcome.events
        report.errors.extend(problem.render() for problem in outcome.problems)
        report.notes.extend(outcome.notes)

    if contacts:
        report.notes.append(
            f"{len(contacts)} published contact record(s) were returned. A discovered contact is "
            f"stored with a review-required policy; it never becomes permitted use."
        )

    # Evidence is written after the entities exist, so it can point at them.
    with db.write() as conn:
        report.evidence += _write_evidence(
            conn, source_id, result, config=config, clock=clock, contacts=contacts
        )


def _company_row(record: AdapterRecord) -> dict[str, Any]:
    """Keep the fields a company import understands; the rest becomes evidence."""
    fields = dict(record.fields)
    row = {key: value for key, value in fields.items() if key in COMPANY_FIELDS}
    row["external_id"] = fields.get("external_id") or record.external_record_id

    extra = {
        key: value
        for key, value in fields.items()
        if key not in COMPANY_FIELDS and value not in (None, "")
    }
    if extra:
        detail = "; ".join(f"{key}: {value}" for key, value in sorted(extra.items()))
        row["notes"] = f"{row.get('notes', '')} Source also reported {detail}.".strip()
    return row


def _write_evidence(
    conn: sqlite3.Connection,
    source_id: str,
    result: AdapterResult,
    *,
    config: Config,
    clock: Clock,
    contacts: list[AdapterRecord],
) -> int:
    """Store field-level provenance for everything the source asserted."""
    written = 0
    now = clock.now_iso()

    for record in result.records:
        entity_id = _resolve_entity(conn, source_id, record)
        for item in record.evidence:
            conn.execute(
                """INSERT INTO evidence
                   (id, source_id, entity_type, entity_id, field_name, value_text, excerpt,
                    url, file_ref, row_ref, source_record_id, content_hash, observed_at,
                    recorded_at, confidence, confirmed_by, expires_at, is_demo)
                   VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,NULL,NULL,?)""",
                (
                    new_id("evd"),
                    source_id,
                    record.entity_type,
                    entity_id,
                    item.get("field_name", "unknown"),
                    None if item.get("value_text") is None else str(item["value_text"]),
                    item.get("excerpt"),
                    item.get("url") or record.raw_reference,
                    record.external_record_id,
                    record.raw_hash,
                    to_iso(record.observed_at),
                    now,
                    item.get("confidence", record.confidence.value),
                    1 if config.is_demo else 0,
                ),
            )
            written += 1
    return written


def _resolve_entity(
    conn: sqlite3.Connection, source_id: str, record: AdapterRecord
) -> str | None:
    """Find the row a record produced, so its evidence can point at it."""
    if record.entity_type == "company":
        external = record.fields.get("external_id") or record.external_record_id
        found = companies_repo.find_company_by_external(conn, source_id, external)
        return None if found is None else found.id
    if record.entity_type in {"offer", "observation"}:
        external = record.fields.get("external_id") or record.external_record_id
        found = supply_repo.find_offer_by_external(conn, source_id, external)
        return None if found is None else found.id
    return None


# -- failure handling ----------------------------------------------------


def _mark_supply_unreachable(
    db: Database,
    config: Config,
    clock: Clock,
    source_id: str,
    error: str,
    report: RefreshReport,
) -> None:
    """Record a failed check against this source's offers, and nothing more.

    A failed fetch is its own state. It is never a sale, it never changes a price,
    and it never disturbs the last successful observation.
    """
    with db.open() as conn:
        row = conn.execute("SELECT category FROM sources WHERE id = ?", (source_id,)).fetchone()
        category = None if row is None else row["category"]
        offers = (
            supply_repo.list_offers(conn)
            if category in {"vehicle_supply", "comparables", "mixed"}
            else []
        )
        offers = [offer for offer in offers if offer.source_id == source_id]

    if not offers:
        return

    now = clock.now()
    with db.write() as conn:
        for offer in offers:
            observations_service.mark_fetch_failed(
                conn,
                offer_id=offer.id,
                source_id=source_id,
                external_record_id=offer.external_record_id,
                now=now,
                is_demo=config.is_demo,
                error=error,
            )
    report.notes.append(
        f"{len(offers)} offer(s) from this source were marked as a failed check. That is not "
        f"evidence any of them were sold, and their last successful observation is unchanged."
    )


def _record_request(
    db: Database, clock: Clock, run_id: str, request: Mapping[str, Any]
) -> None:
    with db.write() as conn:
        conn.execute(
            "UPDATE runs SET request_summary = ? WHERE id = ?",
            (dumps({key: str(value) for key, value in request.items()}), run_id),
        )


def _close(db: Database, clock: Clock, run_id: str, report: RefreshReport) -> None:
    summary = ImportReport(
        kind="refresh",
        created=report.created,
        updated=report.updated,
        observations=report.observations,
        events=report.events,
    )
    finish_run(db, clock, run_id, report=summary, status=report.status)
    with db.write() as conn:
        conn.execute(
            "UPDATE runs SET errors = ?, attribution = ? WHERE id = ?",
            (dumps(report.errors), report.attribution, run_id),
        )


def recent_runs(db: Database, *, limit: int = 20) -> list[sqlite3.Row]:
    with db.open() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE kind = 'refresh' ORDER BY started_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
