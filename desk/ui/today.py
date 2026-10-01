"""The Today screen: what changed, what is due, what is stale."""

from __future__ import annotations


import streamlit as st

from ..repositories import companies as companies_repo
from ..repositories import followup as followup_repo
from ..repositories import supply as supply_repo
from ..services import tasks as tasks_service
from ..services.workflow import Workflow, dashboard_counts
from .components import (
    coverage_notice,
    day,
    empty_state,
    get_clock,
    get_config,
    get_db,
    kpi_row,
    local,
    mode_banner,
)


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()
    now = clock.now()

    st.title("Today")
    mode_banner(config)

    if not db.exists():
        st.error(
            f"No database at `{db.path}`. Run `python -m desk.cli init-db --mode "
            f"{config.mode.value}` and then `seed-demo`."
        )
        return

    with db.open() as conn:
        counts = dashboard_counts(conn, now=now, config=config)
        alerts = supply_repo.recent_change_events(conn, limit=25)
        open_tasks = followup_repo.list_tasks(conn, state="open")
        stale = tasks_service.stale_confirmations(conn, now=now, config=config)
        briefs_due = tasks_service.briefs_due_for_review(conn, now=now, config=config)
        duplicates = companies_repo.pending_duplicate_reviews(conn)

    kpi_row(
        [
            ("Research alerts", counts["new_alerts"], "Unacknowledged change events."),
            (
                "Confirmed requirements",
                counts["confirmed_briefs"],
                "Buying briefs a named person actually confirmed. "
                "Category-fit prospects are counted separately.",
            ),
            (
                "Specification fits",
                counts["specification_fits"],
                "Every mandatory fact matches. Not proof the buyer will buy.",
            ),
            (
                "Needs verification",
                counts["needs_verification"],
                "A required fact is missing, so the match has not passed.",
            ),
            ("Tasks due", counts["tasks_due"], "Open tasks at or past their due time."),
        ]
    )

    st.caption(
        "There is no pipeline-revenue figure here on purpose: adding up the retail value of "
        "every advert would not represent money anyone has agreed to pay, and no approved "
        "compensation rule has been entered, so estimated commission stays blank."
    )
    coverage_notice()

    st.divider()
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Research alerts")
        if not alerts:
            empty_state("No change alerts. Nothing has been invented to fill this list.")
        for row in alerts:
            _alert_card(row)

    with right:
        st.subheader("Next actions")
        if not open_tasks:
            empty_state("No open tasks.")
        for task in open_tasks[:12]:
            with st.container(border=True):
                st.markdown(f"**{task['label']}** · due {local(task['due_at'], config)}")
                if task["notes"]:
                    st.caption(task["notes"])

        st.subheader("Stale vehicle confirmations")
        if not stale:
            empty_state("Every offer has a confirmation inside the freshness window.")
        else:
            st.caption(
                f"{len(stale)} offer(s) have no confirmation newer than "
                f"{config.freshness.supply_hours:g} hours. They stay visible for research but "
                f"cannot be described as currently available."
            )
            for row in stale[:8]:
                st.markdown(
                    f"- `{row['external_record_id']}` "
                    f"{row['seller_name'] or 'seller unknown'} - last confirmed "
                    f"{day(row['availability_confirmed_at'])}"
                )

        st.subheader("Requirements due for review")
        if not briefs_due:
            empty_state("No buying brief is due for review.")
        else:
            for row in briefs_due[:8]:
                st.markdown(
                    f"- `{row['external_record_id']}` {row['model_family']} - last discussed "
                    f"{day(row['conversation_date'])}"
                )

        if counts["pending_contradictions"]:
            st.subheader("Facts to confirm")
            st.caption(
                f"{counts['pending_contradictions']} offer(s) have a source stating something "
                f"different from the reviewed record. Matching still uses the reviewed value "
                f"until someone decides, on the Vehicles screen."
            )

        if counts["sources_due_for_review"]:
            st.subheader("Source approvals lapsing")
            st.caption(
                f"{counts['sources_due_for_review']} source approval(s) are due for review. An "
                f"approval lapses on its own date, after which live fetching and export stop."
            )

        if duplicates:
            st.subheader("Possible duplicates for review")
            st.caption(
                "A fuzzy match is a review suggestion, never an automatic merge."
            )
            for row in duplicates[:6]:
                st.markdown(f"- {row['entity_type']}: {row['reason']}")

    st.divider()
    _recompute_control(db, config, clock)


def _alert_card(row) -> None:
    config = get_config()
    kinds = {
        "price_change": ("Price change", ":material/trending_down:"),
        "availability_change": ("Availability change", ":material/event_available:"),
        "specification_correction": ("Specification correction", ":material/build:"),
        "not_observed": ("Not observed in the latest successful check", ":material/visibility_off:"),
        "fetch_failed": ("Fetch failed", ":material/error:"),
        "company_signal": ("Company research signal", ":material/business:"),
        "requirement_confirmed": ("Requirement confirmed", ":material/handshake:"),
        "follow_up_recorded": ("Follow-up recorded", ":material/call:"),
    }
    title, icon = kinds.get(row["kind"], (row["kind"], ":material/info:"))
    with st.container(border=True):
        st.markdown(f"{icon} **{title}**")
        if row["old_value"] and row["new_value"]:
            st.markdown(
                f"`{row['field_name'] or 'field'}`: {row['old_value']} "
                f"→ **{row['new_value']}**"
            )
        elif row["new_value"]:
            # A confirmation or a new signal has no previous value; showing
            # "unknown → x" would imply one was checked and found missing.
            st.markdown(f"**{row['new_value']}**")
        st.caption(
            f"Stated by the source {local(row['observed_at'], config)} · "
            f"recorded by this desk {local(row['detected_at'], config)}"
        )
        if row["notes"]:
            st.caption(row["notes"])


def _recompute_control(db, config, clock) -> None:
    """Recompute only when asked.

    A Streamlit rerun happens on every interaction, so this must never be
    triggered merely by opening the page.
    """
    st.subheader("Recompute")
    st.caption(
        "Matches, comparable sets and tasks are recomputed only when you press this. "
        "Opening or refreshing a page never starts work, and nothing here contacts anyone."
    )
    if st.button("Recompute matches and comparables", icon=":material/calculate:"):
        report = Workflow(db, config, clock).recompute()
        st.success(report.summary())
