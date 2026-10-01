"""The Sources and imports screen.

Technical settings live here so the sales screens stay simple. Imports follow
preview, then validate, then confirm, and an invalid batch is never partly applied.
"""

from __future__ import annotations

import json

import streamlit as st

from ..config import Mode
from ..connectors import fixtures as fixture_connector
from ..services import demo as demo_service
from ..services.imports import (
    Importer,
    TEMPLATES,
    template_csv,
    validate_batch,
)
from ..services.source_policy import SourcePolicy
from . import source_admin, templates_admin
from .components import (
    empty_state,
    get_clock,
    get_config,
    get_db,
    local,
    mode_banner,
)


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()

    st.title("Sources and imports")
    mode_banner(config)

    register_tab, review_tab, rates_tab, templates_tab, import_tab, demo_tab = st.tabs(
        [
            "Source register",
            "Review a source",
            "Exchange rates",
            "Cost templates",
            "Import a file",
            "Demo data",
        ]
    )
    with register_tab:
        _register_tab(db, config, clock)
    with review_tab:
        source_admin.review_tab(db, config, clock)
    with rates_tab:
        source_admin.rates_tab(db, config, clock)
    with templates_tab:
        templates_admin.render(db, config, clock)
    with import_tab:
        _import_tab(db, config, clock)
    with demo_tab:
        _demo_tab(db, config, clock)


def _register_tab(db, config, clock) -> None:
    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    policy = SourcePolicy(db, config, clock)
    sources = policy.list_sources()

    st.caption(
        "Every live request passes this register first: mode, approval and its expiry, permitted "
        "host and path, the resolved IP address, and the retention and export rules. A missing or "
        "lapsed approval disables live fetching; it never falls back to reading the website "
        "instead. Credentials live in the environment, never in these records."
    )

    if not sources:
        empty_state("No source recorded.")
        return

    rows = []
    for source in sources:
        decision = policy.may_fetch(source.id)
        rows.append(
            {
                "id": source.id,
                "name": source.name,
                "category": source.category,
                "access": source.access_mode,
                "stored status": source.status,
                "effective status": policy.effective_status(source),
                "may fetch now": "yes" if decision.allowed else "no",
                "export permitted": "yes" if source.export_allowed else "no",
                "raw retention": "yes" if source.raw_retention_allowed else "no",
                # Kept as text so the column has one type: a mixed int/str
                # column cannot be serialised for display.
                "retention days": (
                    "" if not source.retention_days else str(source.retention_days)
                ),
                "last fetch": local(source.last_fetch_at, config),
                "last result": source.last_fetch_status or "",
                "review due": local(source.review_due_at, config),
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)

    st.subheader("Why each source can or cannot be fetched right now")
    for source in sources:
        decision = policy.may_fetch(source.id)
        icon = ":material/check_circle:" if decision.allowed else ":material/block:"
        with st.container(border=True):
            st.markdown(f"{icon} **{source.name}** (`{source.id}`)")
            st.caption(decision.reason)
            if source.approved_use:
                st.caption(f"Approved use: {source.approved_use}")
            if source.attribution:
                st.caption(f"Attribution: {source.attribution}")
            if source.retention_rule:
                st.caption(f"Retention: {source.retention_rule}")
            if source.notes:
                st.caption(f"Notes: {source.notes}")
            if source.documentation_url:
                st.caption(f"Documentation: {source.documentation_url}")

    st.subheader("Refresh a source")
    if config.mode is Mode.DEMO:
        st.info(
            "Refresh is unavailable in demo mode. Demo mode performs no live fetches at all. "
            "Switch to live mode with an approved register entry, and use "
            "`python -m desk.cli refresh-source --source <id>`.",
            icon=":material/cloud_off:",
        )
    else:
        st.caption(
            "Refresh runs only when you ask for it. Opening a page never starts a fetch, and "
            "there is no scheduler in this prototype: a scheduled refresh should call the CLI "
            "command from the operating system's own scheduler, with a single-run lock."
        )
        candidate = st.selectbox("Source", [s.id for s in sources])
        if st.button("Check whether this source may be fetched", icon=":material/policy:"):
            decision = policy.may_fetch(candidate)
            if decision.allowed:
                st.success(decision.reason)
            else:
                st.error(decision.reason)
            for check in decision.checks:
                st.caption(f"checked: {check}")


def _import_tab(db, config, clock) -> None:
    st.caption(
        "Preview, then validate, then confirm. The default on any error is to reject the whole "
        "batch, so a file can never be half applied."
    )

    kind = st.selectbox("What kind of rows does the file contain?", list(TEMPLATES))
    with st.expander("Column template"):
        st.code(template_csv(kind), language="csv")
        st.download_button(
            "Download template",
            data=template_csv(kind).encode("utf-8"),
            file_name=f"{kind}-template.csv",
            mime="text/csv",
        )

    uploaded = st.file_uploader("CSV or JSON file", type=["csv", "json"], key=f"up_{kind}")
    if uploaded is None:
        st.caption("Nothing is read until you choose a file.")
        return

    import csv as csv_module
    import io

    text = uploaded.getvalue().decode("utf-8-sig")
    try:
        if uploaded.name.lower().endswith(".json"):
            payload = json.loads(text)
            raw_rows = payload.get(kind, payload) if isinstance(payload, dict) else payload
        else:
            raw_rows = [dict(row) for row in csv_module.DictReader(io.StringIO(text))]
    except Exception as exc:
        st.error(f"Could not read the file: {exc}")
        return

    report = validate_batch(kind, raw_rows)
    st.markdown(f"**{report.summary()}**")

    st.markdown("**Preview of the first rows as read**")
    st.dataframe(raw_rows[:10], width="stretch", hide_index=True)

    if report.problems:
        st.error("**Row-level problems.** Nothing has been written.", icon=":material/error:")
        st.dataframe(
            [
                {
                    "row": p.row_number,
                    "external id": p.external_id or "",
                    "field": p.field or "",
                    "problem": p.message,
                }
                for p in report.problems
            ],
            width="stretch",
            hide_index=True,
        )

    with db.open() as conn:
        source_ids = [row["id"] for row in conn.execute("SELECT id FROM sources ORDER BY id")]

    if not source_ids:
        st.error("No source register entry exists to attribute these rows to.")
        return

    columns = st.columns(2)
    source_id = columns[0].selectbox("Attribute these rows to", source_ids)
    company_source = columns[1].selectbox(
        "Resolve company references against",
        source_ids,
        help=(
            "Briefs, interactions and policies refer to companies by their external id. This is "
            "the source those company records were imported under."
        ),
    )

    allow_partial = False
    if report.problems:
        allow_partial = st.checkbox(
            f"Apply only the {len(report.valid)} valid row(s) and skip the rest",
            help="Off by default: the whole batch is rejected unless you explicitly choose this.",
        )

    can_apply = report.ok or allow_partial
    if st.button("Confirm and apply", disabled=not can_apply, icon=":material/upload:"):
        importer = Importer(db, config, clock)
        outcome = importer.apply(
            kind,
            raw_rows,
            source_id=source_id,
            company_source_id=company_source,
            allow_partial=allow_partial,
        )
        if outcome.applied:
            st.success(outcome.summary())
        else:
            st.error(outcome.summary())
        for note in outcome.notes:
            st.caption(note)
        for problem in outcome.problems:
            st.caption(problem.render())


def _demo_tab(db, config, clock) -> None:
    if config.mode is not Mode.DEMO:
        st.info(
            "Demo seeding and replay are unavailable in live mode. A sample replay must never "
            "write to live data.",
            icon=":material/lock:",
        )
        return

    st.caption(
        "The demo fixtures are dated against a stored anchor, so seeding or replaying the same "
        "snapshot twice appends no observation and raises no second alert."
    )

    seed = fixture_connector.demo_seed()
    updates = fixture_connector.demo_updates()

    for snapshot in (seed, updates):
        with st.container(border=True):
            st.markdown(f"**{snapshot.name}** — {', '.join(snapshot.kinds())}")
            for note in snapshot.notes:
                st.caption(note)

    columns = st.columns(3)
    if columns[0].button("Seed demo data", icon=":material/dataset:"):
        report = demo_service.seed(db, config, clock)
        st.code(report.summary())
    if columns[1].button("Replay the second snapshot", icon=":material/replay:"):
        report = demo_service.replay_updates(db, config, clock)
        st.code(report.summary())
        st.caption("A replay is a demonstration, not a live market event.")

    with columns[2].popover("Reset demo data"):
        st.warning(
            f"This deletes only `{db.path}`, after checking that the file is itself stamped as a "
            f"demo database. The live database is never touched."
        )
        if st.button("I understand, reset it", icon=":material/delete_forever:"):
            path = demo_service.reset(db, config)
            st.success(f"Reset {path}. Seed it again to continue.")
