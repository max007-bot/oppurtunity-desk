"""Command-line interface.

``refresh-source`` is deliberately unavailable in demo mode, and no command starts
a background job. Nothing here schedules anything: a later scheduled refresh should
call ``refresh-source`` through the operating system's own scheduler.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .clock import SystemClock
from .config import Mode, load_config
from .db import Database, ModeViolation
from .services import demo as demo_service
from .services.imports import Importer, read_rows, template_csv, validate_batch
from .services.source_policy import PolicyDenied, SourcePolicy
from .services.workflow import Workflow, dashboard_counts


def _env() -> tuple[Database, object, object]:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass
    config = load_config()
    clock = SystemClock()
    return Database(config, clock), config, clock


def _with_mode(mode: str | None):
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass
    config = load_config(mode=mode)
    clock = SystemClock()
    return Database(config, clock), config, clock


# -- commands ------------------------------------------------------------


def cmd_init_db(args: argparse.Namespace) -> int:
    db, config, _ = _with_mode(args.mode)
    applied = db.initialise()
    print(f"Database: {db.path}")
    print(f"Mode:     {config.mode.value}")
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Schema already up to date.")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    db, config, clock = _with_mode("demo")
    db.initialise()
    report = demo_service.seed(db, config, clock)
    print(report.summary())
    print("\nAll of the above is synthetic demonstration data.")
    return 1 if report.problems else 0


def cmd_replay_demo(args: argparse.Namespace) -> int:
    db, config, clock = _with_mode("demo")
    db.initialise()
    report = demo_service.replay_updates(db, config, clock)
    print(report.summary())
    print("\nA replay is a demonstration, not a live market event.")
    return 1 if report.problems else 0


def cmd_reset_demo(args: argparse.Namespace) -> int:
    db, config, _ = _with_mode("demo")
    if not args.yes:
        print(
            "Refusing to reset without --yes. This deletes only the demo database "
            f"({db.path}) after checking its own mode stamp."
        )
        return 2
    path = demo_service.reset(db, config)
    print(f"Demo database reset: {path}")
    return 0


def cmd_validate_import(args: argparse.Namespace) -> int:
    kind, rows = read_rows(args.path, kind=args.kind)
    report = validate_batch(kind, rows)
    print(report.summary())
    for problem in report.problems:
        print(f"  - {problem.render()}")
    return 0 if report.ok else 1


def cmd_import_file(args: argparse.Namespace) -> int:
    db, config, clock = _with_mode(args.mode)
    db.initialise()
    kind, rows = read_rows(args.path, kind=args.kind)
    importer = Importer(db, config, clock)
    report = importer.apply(
        kind,
        rows,
        source_id=args.source,
        company_source_id=args.company_source or args.source,
        allow_partial=args.allow_partial,
    )
    print(report.summary())
    for note in report.notes:
        print(f"  note: {note}")
    for problem in report.problems:
        print(f"  - {problem.render()}")
    return 0 if report.applied and not report.problems else 1


def cmd_template(args: argparse.Namespace) -> int:
    sys.stdout.write(template_csv(args.kind))
    return 0


def cmd_recompute(args: argparse.Namespace) -> int:
    db, config, clock = _with_mode(args.mode)
    db.initialise()
    report = Workflow(db, config, clock).recompute()
    print(report.summary())
    return 0


def cmd_refresh_source(args: argparse.Namespace) -> int:
    """Refresh one approved source. Never available in demo mode."""
    db, config, clock = _with_mode(args.mode)
    if config.mode is Mode.DEMO:
        print(
            "refresh-source is unavailable in demo mode. Demo mode performs no live fetches; "
            "switch to live mode with an approved source register entry."
        )
        return 2
    db.initialise()
    policy = SourcePolicy(db, config, clock)
    decision = policy.may_fetch(args.source)
    if not decision.allowed:
        print(f"Refused: {decision.reason}")
        for check in decision.checks:
            print(f"  checked: {check}")
        return 2
    print(f"Allowed: {decision.reason}")

    from .services import refresh as refresh_service

    request = _refresh_request(args)
    if request is None:
        return 2

    if not args.yes:
        print()
        print(f"This will make a live request to {args.source} with: {request}.")
        print("Re-run with --yes to proceed. Nothing has been fetched.")
        return 0

    report = refresh_service.refresh(
        db, config, clock, source_id=args.source, request=request
    )
    print(report.summary())
    for note in report.notes:
        print(f"  note: {note}")
    for error in report.errors:
        print(f"  error: {error}")
    return 0 if report.ok else 1


def _refresh_request(args: argparse.Namespace) -> dict | None:
    """Turn the command-line arguments into the adapter's own request."""
    if args.source == "osm_overpass":
        if not args.bbox:
            print("Refused: --bbox south,west,north,east is required for a bounded query.")
            return None
        try:
            south, west, north, east = (float(part) for part in args.bbox.split(","))
        except ValueError:
            print("Refused: --bbox must be four numbers, south,west,north,east.")
            return None
        from .connectors.overpass import BoundingBox

        box = BoundingBox(south=south, west=west, north=north, east=east)
        try:
            box.validate()
        except ValueError as exc:
            print(f"Refused: {exc}")
            return None
        return {"bbox": box}

    if args.source == "cz_ares":
        if not args.ico:
            print("Refused: --ico is required; this connector verifies a company you already know.")
            return None
        return {"ico": args.ico}

    if args.source == "fr_recherche_entreprises":
        if not args.query:
            print("Refused: --query is required.")
            return None
        return {"query": args.query}

    if args.source == "approved_website":
        if not args.company or not args.url:
            print("Refused: --company and at least one --url are required.")
            return None
        return {"company_external_id": args.company, "urls": list(args.url)}

    print(f"Refused: no adapter is implemented for {args.source!r}.")
    return None


def cmd_runs(args: argparse.Namespace) -> int:
    """What the live refreshes did, including the ones that failed."""
    db, config, clock = _with_mode(args.mode)
    if not db.exists():
        print(f"No database at {db.path}. Run init-db first.")
        return 2

    from .services import refresh as refresh_service

    rows = refresh_service.recent_runs(db)
    if not rows:
        print("No live refresh has been run.")
        return 0
    for row in rows:
        print(
            f"{row['started_at']}  {row['connector_id']:<28} {row['status']:<12} "
            f"seen {row['records_seen']}, created {row['records_created']}, "
            f"updated {row['records_updated']}"
        )
        if row["request_summary"]:
            print(f"    requested: {row['request_summary']}")
        errors = json.loads(row["errors"] or "[]")
        for error in errors:
            print(f"    error: {error}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db, config, clock = _with_mode(args.mode)
    if not db.exists():
        print(f"No database at {db.path}. Run init-db first.")
        return 2
    with db.open() as conn:
        counts = dashboard_counts(conn, now=clock.now(), config=config)
    print(f"Mode: {config.mode.value}   Database: {db.path}")
    width = max(len(key) for key in counts)
    for key, value in counts.items():
        print(f"  {key.ljust(width)} : {value}")

    policy = SourcePolicy(db, config, clock)
    print("\nSources:")
    for source in policy.list_sources():
        effective = policy.effective_status(source)
        print(f"  {source.id.ljust(26)} {source.access_mode.ljust(18)} {effective}")
    return 0


def cmd_fx(args: argparse.Namespace) -> int:
    """List dated rates, or record one."""
    db, config, clock = _with_mode(args.mode)
    db.initialise()
    from decimal import Decimal

    from .services import fx

    if args.rate is None:
        with db.open() as conn:
            rates = fx.list_rates(conn)
        if not rates:
            print(
                "No exchange rate recorded. Cross-currency comparisons stay excluded until one "
                "is entered."
            )
            return 0
        print(json.dumps(fx.rates_summary(rates, at=clock.now()), indent=2))
        return 0

    if not args.source_note:
        print("Refused: state where the rate came from; an unattributed rate is not evidence.")
        return 2
    try:
        with db.write() as conn:
            fx.record_rate(
                conn,
                from_currency=args.from_currency,
                to_currency=args.to_currency,
                rate=Decimal(args.rate),
                rate_date=args.date or clock.now().date(),
                source_note=args.source_note,
                now=clock.now_iso(),
                is_demo=config.is_demo,
            )
    except (fx.FxError, ValueError) as exc:
        print(f"Refused: {exc}")
        return 2
    print(
        f"Recorded 1 {args.from_currency.upper()} = {args.rate} {args.to_currency.upper()} as at "
        f"{args.date or clock.now().date().isoformat()}. The inverse is derived, not stored."
    )
    return 0


def cmd_contradictions(args: argparse.Namespace) -> int:
    """List offers where a source disagrees with the reviewed record."""
    db, config, clock = _with_mode(args.mode)
    if not db.exists():
        print(f"No database at {db.path}. Run init-db first.")
        return 2

    from .repositories import supply as supply_repo
    from .services import corrections

    found = 0
    with db.open() as conn:
        for row in conn.execute(
            "SELECT id, external_record_id, vehicle_id FROM offers WHERE vehicle_id IS NOT NULL"
        ).fetchall():
            vehicle = supply_repo.get_vehicle(conn, row["vehicle_id"])
            for issue in corrections.find_contradictions(conn, vehicle, row["id"]):
                found += 1
                print(f"{row['external_record_id']}: {issue.describe()}")
                print(f"  {issue.consequence()}")
                print(
                    "  Resolve it on the Vehicles screen: the import did not overwrite the "
                    "reviewed fact."
                )
    if not found:
        print("No unresolved contradictions.")
    return 0


def cmd_review_source(args: argparse.Namespace) -> int:
    """Show a source's review history and what a new review would need."""
    db, config, clock = _with_mode(args.mode)
    if not db.exists():
        print(f"No database at {db.path}. Run init-db first.")
        return 2

    from .services import source_review
    from .services.source_policy import SourcePolicy

    policy = SourcePolicy(db, config, clock)
    source = policy.get(args.source)
    if source is None:
        print(f"No source register entry for {args.source!r}.")
        return 2

    print(f"{source.name} ({source.id})")
    print(f"  access mode      : {source.access_mode}")
    print(f"  stored status    : {source.status}")
    print(f"  effective status : {policy.effective_status(source)}")
    print(f"  may fetch now    : {policy.may_fetch(source.id).reason}")
    if source.documentation_url:
        print(f"  documentation    : {source.documentation_url}")

    with db.open() as conn:
        history = source_review.history(conn, args.source)
    if history:
        print("  review history:")
        for row in history:
            print(
                f"    {row['reviewed_at'][:10]} {row['previous_status']} -> {row['status']} "
                f"by {row['reviewer']} ({row['evidence_kind']})"
            )
    else:
        print("  review history: none recorded")

    print()
    print(
        "Recording a review is a deliberate decision with a named person attached, so it is "
        "done on the Sources and imports screen, under 'Review a source'."
    )
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    db, config, clock = _with_mode(args.mode)
    db.initialise()
    policy = SourcePolicy(db, config, clock)
    rows = []
    for source in policy.list_sources():
        rows.append(
            {
                "id": source.id,
                "name": source.name,
                "category": source.category,
                "access_mode": source.access_mode,
                "stored_status": source.status,
                "effective_status": policy.effective_status(source),
                "export_allowed": source.export_allowed,
                "may_fetch": policy.may_fetch(source.id).reason,
            }
        )
    print(json.dumps(rows, indent=2))
    return 0


# -- parser --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desk",
        description=(
            "Luxury Car Opportunity Desk - local single-user research prototype. "
            "Demo mode is offline by default; no command sends a message or contacts anyone."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create or migrate a database")
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("seed-demo", help="load the synthetic demo snapshot (demo mode only)")
    p.set_defaults(func=cmd_seed_demo)

    p = sub.add_parser(
        "replay-demo", help="replay the second synthetic snapshot (price and availability changes)"
    )
    p.set_defaults(func=cmd_replay_demo)

    p = sub.add_parser("reset-demo", help="delete and recreate the demo database only")
    p.add_argument("--yes", action="store_true", help="required confirmation")
    p.set_defaults(func=cmd_reset_demo)

    p = sub.add_parser("validate-import", help="validate a file without writing anything")
    p.add_argument("path", type=Path)
    p.add_argument(
        "--kind",
        choices=["companies", "offers", "buyer_briefs", "comparables", "interactions", "contact_policies"],
        default=None,
        help="required for CSV; inferred for a keyed JSON object",
    )
    p.set_defaults(func=cmd_validate_import)

    p = sub.add_parser("import-file", help="validate then apply a file transactionally")
    p.add_argument("path", type=Path)
    p.add_argument("--source", required=True, help="source register id the rows are attributed to")
    p.add_argument(
        "--company-source",
        default=None,
        help="source id to resolve company_external_id against (defaults to --source)",
    )
    p.add_argument(
        "--kind",
        choices=["companies", "offers", "buyer_briefs", "comparables", "interactions", "contact_policies"],
        default=None,
    )
    p.add_argument(
        "--allow-partial",
        action="store_true",
        help="apply only the valid rows instead of rejecting the whole batch",
    )
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_import_file)

    p = sub.add_parser("template", help="print the CSV column template for one import kind")
    p.add_argument(
        "kind",
        choices=["companies", "offers", "buyer_briefs", "comparables", "interactions", "contact_policies"],
    )
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("recompute", help="recompute matches, comparables and tasks")
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_recompute)

    p = sub.add_parser(
        "refresh-source", help="refresh one approved live source (unavailable in demo mode)"
    )
    p.add_argument("--source", required=True)
    p.add_argument(
        "--yes",
        action="store_true",
        help="required to actually fetch; without it the command only reports what it would do",
    )
    p.add_argument("--bbox", default=None, help="Overpass: south,west,north,east")
    p.add_argument("--ico", default=None, help="ARES: the eight-digit company identifier")
    p.add_argument("--query", default=None, help="French business search: the search text")
    p.add_argument("--company", default=None, help="approved website: the company external id")
    p.add_argument(
        "--url", action="append", default=None, help="approved website: repeatable, max five"
    )
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_refresh_source)

    p = sub.add_parser("runs", help="print recent live refresh runs")
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("status", help="print record counts and source status")
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "fx", help="list dated exchange rates, or record one"
    )
    p.add_argument("--from", dest="from_currency", default="EUR")
    p.add_argument("--to", dest="to_currency", default="GBP")
    p.add_argument("--rate", default=None, help="omit to list the stored rates")
    p.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to today")
    p.add_argument(
        "--source-note", default=None, help="where the rate came from; required to record one"
    )
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_fx)

    p = sub.add_parser(
        "contradictions",
        help="list offers where a source disagrees with the reviewed vehicle record",
    )
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_contradictions)

    p = sub.add_parser(
        "review-source", help="show a source's status and review history"
    )
    p.add_argument("--source", required=True)
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_review_source)

    p = sub.add_parser("sources", help="print the source register as JSON")
    p.add_argument("--mode", choices=["demo", "live"], default=None)
    p.set_defaults(func=cmd_sources)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ModeViolation, PolicyDenied) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
