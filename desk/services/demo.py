"""Demo seeding and snapshot replay.

The fixtures use relative time tokens (``@now``, ``@now-3h``, ``@now+21d``) which
are resolved at seed time. That keeps the demonstration reproducible whenever it
is run, instead of quietly rotting as hardcoded dates age past the freshness
windows.

Nothing here can write to live data: every entry point refuses unless the
configuration is in demo mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..clock import Clock, from_iso, to_iso
from ..config import Config, Mode
from ..connectors.fixtures import Snapshot, demo_seed, demo_updates
from ..db import Database, ModeViolation, get_meta, set_meta
from .imports import ImportReport, Importer, upsert_sources

TOKEN = re.compile(r"^@now(?:([+-])(\d+)([hdm]))?$")

# Order matters: a brief cannot attach to a company that does not exist yet.
APPLY_ORDER = (
    "companies",
    "contact_policies",
    "offers",
    "comparables",
    "buyer_briefs",
    "interactions",
)

# Which source each batch is attributed to.
BATCH_SOURCES = {
    "companies": "fixture_demo",
    "contact_policies": "human_entry_demo",
    "offers": "fixture_demo",
    "comparables": "fixture_demo_market",
    "buyer_briefs": "human_entry_demo",
    "interactions": "human_entry_demo",
}

COMPANY_SOURCE = "fixture_demo"

# The instant the demo fixtures are dated against. It is written once, on the first
# seed, and reused afterwards. Re-resolving the tokens against "now" on every run
# would give each row a new timestamp, and replaying the same snapshot would then
# append a second observation and a second alert instead of being a no-op.
ANCHOR_KEY = "demo_time_anchor"


def time_anchor(db: Database, clock: Clock) -> datetime:
    """Read the stored demo anchor, creating it on first use."""
    with db.open() as conn:
        stored = get_meta(conn, ANCHOR_KEY)
    if stored is not None:
        parsed = from_iso(stored)
        if parsed is not None:
            return parsed
    anchor = clock.now()
    with db.write() as conn:
        set_meta(conn, ANCHOR_KEY, to_iso(anchor))
    return anchor


@dataclass
class SeedReport:
    snapshot: str
    per_kind: dict[str, ImportReport] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Things loaded that are not entity batches, such as cost templates.
    extras: list[str] = field(default_factory=list)

    @property
    def total_created(self) -> int:
        return sum(report.created for report in self.per_kind.values())

    @property
    def total_observations(self) -> int:
        return sum(report.observations for report in self.per_kind.values())

    @property
    def total_events(self) -> int:
        return sum(report.events for report in self.per_kind.values())

    @property
    def problems(self) -> list[str]:
        out: list[str] = []
        for report in self.per_kind.values():
            out.extend(problem.render() for problem in report.problems)
        return out

    def summary(self) -> str:
        lines = [f"Snapshot {self.snapshot}:"]
        for kind, report in self.per_kind.items():
            lines.append(f"  {kind}: {report.summary()}")
        lines.extend(f"  {extra}" for extra in self.extras)
        if self.problems:
            lines.append("  problems:")
            lines.extend(f"    - {problem}" for problem in self.problems)
        return "\n".join(lines)


def resolve_tokens(value: Any, now: datetime) -> Any:
    """Replace relative time tokens anywhere in a nested structure."""
    if isinstance(value, dict):
        return {key: resolve_tokens(item, now) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_tokens(item, now) for item in value]
    if isinstance(value, str):
        match = TOKEN.match(value.strip())
        if match is None:
            return value
        sign, amount, unit = match.groups()
        if sign is None:
            return to_iso(now)
        delta = timedelta(
            **{{"h": "hours", "d": "days", "m": "minutes"}[unit]: int(amount)}
        )
        return to_iso(now + delta if sign == "+" else now - delta)
    return value


def _require_demo(config: Config) -> None:
    if config.mode is not Mode.DEMO:
        raise ModeViolation(
            "demo seeding and replay are only available in demo mode; a sample replay must "
            "never write to live data"
        )


def apply_snapshot(
    db: Database, config: Config, clock: Clock, snapshot: Snapshot
) -> SeedReport:
    """Apply one synthetic snapshot.

    Safe to run twice: the fixtures are dated against the stored anchor, so the
    second run appends no observation and raises no second alert.
    """
    _require_demo(config)
    now = time_anchor(db, clock)
    report = SeedReport(snapshot=snapshot.name, notes=list(snapshot.notes))

    if snapshot.sources:
        upsert_sources(db, config, clock, resolve_tokens(snapshot.sources, now))

    if snapshot.cost_templates:
        report.extras.append(
            _seed_templates(db, config, clock, resolve_tokens(snapshot.cost_templates, now))
        )

    importer = Importer(db, config, clock)
    for kind in APPLY_ORDER:
        rows = snapshot.batches.get(kind) or []
        if not rows:
            continue
        resolved = resolve_tokens(rows, now)
        report.per_kind[kind] = importer.apply(
            kind,
            resolved,
            source_id=BATCH_SOURCES[kind],
            company_source_id=COMPANY_SOURCE,
        )
    return report


def seed(db: Database, config: Config, clock: Clock) -> SeedReport:
    """Load the base demo snapshot."""
    return apply_snapshot(db, config, clock, demo_seed())


def replay_updates(db: Database, config: Config, clock: Clock) -> SeedReport:
    """Replay the second snapshot, which lowers one price and changes availability."""
    return apply_snapshot(db, config, clock, demo_updates())


def reset(db: Database, config: Config) -> str:
    """Delete only the demo database, after checking its own mode stamp."""
    _require_demo(config)
    path = db.reset_demo()
    return str(path)


def _seed_templates(
    db: Database, config: Config, clock: Clock, rows: list[dict[str, Any]]
) -> str:
    """Load the demo cost templates, skipping any that already exist by name."""
    from ..money import Money
    from .cost_templates import TemplateLine, list_templates, save

    created = 0
    with db.write() as conn:
        existing = {template.name for template in list_templates(conn)}
        for row in rows:
            if row["name"] in existing:
                continue
            currency = row.get("currency", "EUR")
            save(
                conn,
                name=row["name"],
                destination_country=row["destination_country"],
                basis=row["basis"],
                reviewer=row["reviewer"],
                reviewed_at=from_iso(row["reviewed_at"]),
                review_due_at=from_iso(row.get("review_due_at")),
                lines=[
                    TemplateLine(
                        label=line["label"],
                        amount=(
                            None
                            if line.get("amount") is None
                            else Money.from_decimal(str(line["amount"]), currency)
                        ),
                        confirmed_zero=bool(line.get("confirmed_zero")),
                        tax_treatment=line.get("tax_treatment", "non_recoverable"),
                        evidence_note=line.get("evidence_note"),
                    )
                    for line in row["lines"]
                ],
                now=clock.now(),
                is_demo=config.is_demo,
                currency=currency,
                origin_country=row.get("origin_country"),
                model_family=row.get("model_family"),
                notes=row.get("notes"),
            )
            created += 1
    return f"cost templates: {created} created, {len(rows) - created} already present"
