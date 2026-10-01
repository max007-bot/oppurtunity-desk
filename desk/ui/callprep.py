"""The Call preparation screen.

There is no Send and no Auto-dial anywhere on this screen, by design. A brief can
be copied or downloaded; a person makes the call.
"""

from __future__ import annotations

import json

import streamlit as st

from ..config import DraftStatus, FitStatus
from ..repositories import analysis as analysis_repo
from ..repositories import companies as companies_repo
from ..repositories import followup as followup_repo
from ..repositories import supply as supply_repo
from ..services import outreach
from ..services.imports import export_csv
from ..services.source_policy import SourcePolicy
from .components import (
    copyable,
    day,
    download_text,
    empty_state,
    fit_badge,
    get_clock,
    get_config,
    get_db,
    local,
    mode_banner,
)

AUTHORISED_COMPANY = "the dealership I am working with"


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()
    now = clock.now()

    st.title("Call preparation and follow-up")
    mode_banner(config)
    st.caption(
        "This screen prepares a human for a conversation. It cannot send an email, post a "
        "message or dial a number, and it holds no credentials that would let it."
    )

    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    brief_tab, drafts_tab, export_tab = st.tabs(
        ["Prepare a call", "Saved drafts", "Exports"]
    )
    with brief_tab:
        _prepare_tab(db, config, clock, now)
    with drafts_tab:
        _drafts_tab(db, config, clock, now)
    with export_tab:
        _export_tab(db, config, clock, now)


def _prepare_tab(db, config, clock, now) -> None:
    with db.open() as conn:
        rows = [
            row
            for row in analysis_repo.list_matches(
                conn,
                fit_statuses=(
                    FitStatus.SPECIFICATION_FIT.value,
                    FitStatus.NEEDS_VERIFICATION.value,
                    FitStatus.BRIEF_EXPIRED.value,
                ),
            )
            if row["brief_id"]
        ]

    if not rows:
        empty_state("No match is in a state worth preparing a call for.")
        return

    labels: dict[str, str] = {}
    with db.open() as conn:
        for row in rows:
            company = companies_repo.get_company(conn, row["company_id"])
            offer = supply_repo.get_offer(conn, row["offer_id"])
            labels[row["id"]] = (
                f"{fit_badge(row['fit_status'])} · {company.display_name if company else '?'} · "
                f"offer {offer.external_record_id if offer else '?'}"
            )

    selected = st.selectbox(
        "Match", list(labels), format_func=lambda key: labels[key], key="callprep_match"
    )
    channel = st.radio("Channel", ["phone", "email"], horizontal=True, key="callprep_channel")

    match_row = next(row for row in rows if row["id"] == selected)

    with db.open() as conn:
        company = companies_repo.get_company(conn, match_row["company_id"])
        offer = supply_repo.get_offer(conn, match_row["offer_id"])
        vehicle = (
            supply_repo.get_vehicle(conn, offer.vehicle_id)
            if offer and offer.vehicle_id
            else None
        )
        brief = companies_repo.get_brief(conn, match_row["brief_id"])
        contacts = companies_repo.list_contacts(conn, company.id)
        contact = contacts[0] if contacts else None

        result = outreach.match_from_row(match_row)
        policy_service = SourcePolicy(db, config, clock)
        gate = outreach.evaluate_gates(
            conn,
            company=company,
            contact=contact,
            channel=channel,
            offer=offer,
            brief=brief,
            match=result,
            policy_service=policy_service,
            now=now,
            config=config,
        )
        scenario_row = analysis_repo.latest_scenario_for_offer(conn, offer.id) if offer else None

    scenario_summary = None
    if scenario_row is not None:
        if scenario_row["status"] == "complete":
            scenario_summary = (
                f"A saved scenario exists for this offer, computed "
                f"{day(scenario_row['computed_at'])}. It is conditional on its stated inputs and "
                f"is before unmodelled overhead."
            )
        else:
            scenario_summary = (
                "The saved scenario for this offer is incomplete, so the economics are not "
                "reviewed. Do not quote a figure on the call."
            )

    call_brief = outreach.build_call_brief(
        company=company,
        contact=contact,
        offer=offer,
        vehicle=vehicle,
        brief=brief,
        match=result,
        now=now,
        config=config,
        authorised_company=AUTHORISED_COMPANY,
        gate=gate,
        scenario_summary=scenario_summary,
    )

    if gate.status is DraftStatus.CONTACT_READY:
        st.success(
            "**Contact ready.** " + " ".join(gate.reasons),
            icon=":material/verified:",
        )
    elif gate.status is DraftStatus.BLOCKED:
        st.error(
            "**Blocked.** " + " ".join(gate.blocking),
            icon=":material/block:",
        )
    else:
        st.warning(
            "**Internal research brief only.** This account is not in the actionable outreach "
            "list for this channel. Reasons: " + " ".join(gate.blocking),
            icon=":material/visibility:",
        )

    if call_brief.missing_facts:
        st.warning(
            "**Missing facts.** These are left out of the brief rather than filled in: "
            + "; ".join(call_brief.missing_facts),
            icon=":material/help:",
        )

    body = call_brief.render()
    copyable(body)

    columns = st.columns(3)
    with columns[0]:
        download_text(
            "Download brief",
            body,
            f"call-brief-{company.display_name.replace(' ', '-').lower()}.txt",
            key=f"dl_{match_row['id']}",
        )
    with columns[1]:
        if st.button("Save as draft", key=f"save_draft_{match_row['id']}", icon=":material/save:"):
            with db.write() as conn:
                draft_id = outreach.store_draft(
                    conn,
                    brief_text=body,
                    company=company,
                    contact=contact,
                    offer=offer,
                    buyer_brief=brief,
                    match_id=match_row["id"],
                    channel=channel,
                    gate=gate,
                    facts_used=[
                        f"offer:{offer.external_record_id}" if offer else "offer:none",
                        f"brief:{brief.external_record_id}" if brief else "brief:none",
                        f"company:{company.id}",
                    ],
                    unresolved=call_brief.missing_facts,
                    now=now,
                    is_demo=config.is_demo,
                    expires_at=offer.valid_until if offer else None,
                )
            st.success(f"Saved as `{draft_id}`. It will be re-checked every time it is reopened.")
    with columns[2]:
        st.caption("There is no Send button and no Auto-dial. A person makes the call.")

    _task_block(db, config, match_row, company)


def _task_block(db, config, match_row, company) -> None:
    st.divider()
    st.markdown("**Open tasks for this account**")
    with db.open() as conn:
        tasks = [
            row
            for row in followup_repo.list_tasks(conn, state="open")
            if row["company_id"] == company.id
        ]
    if not tasks:
        empty_state("No open task for this account.")
        return
    for task in tasks:
        columns = st.columns([4, 1])
        columns[0].markdown(
            f"**{task['label']}** · due {local(task['due_at'], config)}"
            + (f"\n\n{task['notes']}" if task["notes"] else "")
        )
        if columns[1].button("Done", key=f"done_{task['id']}"):
            clock = get_clock()
            with db.write() as conn:
                followup_repo.complete_task(conn, task["id"], now=clock.now_iso())
            st.rerun()


def _drafts_tab(db, config, clock, now) -> None:
    st.caption(
        "Every stored draft is re-checked against the current records before it can be copied. "
        "A draft that was valid earlier becomes unusable once the account objects, the offer "
        "expires or the supporting facts change."
    )
    with db.open() as conn:
        drafts = followup_repo.list_drafts(conn)

    if not drafts:
        empty_state("No draft saved.")
        return

    policy_service = SourcePolicy(db, config, clock)
    for draft in drafts:
        with db.open() as conn:
            company = companies_repo.get_company(conn, draft["company_id"])
            validity = outreach.revalidate_draft(
                conn, draft, policy_service=policy_service, now=now, config=config, clock=clock
            )

        header = (
            f"{company.display_name if company else 'unknown company'} · {draft['channel']} · "
            f"stored {day(draft['created_at'])} · now {validity.status.value}"
        )
        with st.expander(header):
            if validity.usable:
                st.success("Still contact ready.", icon=":material/verified:")
                copyable(draft["body"])
                download_text(
                    "Download brief",
                    draft["body"],
                    f"draft-{draft['id']}.txt",
                    key=f"dd_{draft['id']}",
                )
            else:
                st.error(
                    "**Not usable.** " + " ".join(validity.reasons),
                    icon=":material/block:",
                )
                st.caption(
                    "The text is withheld from copy and export while it is blocked. Regenerate "
                    "it from the current records instead."
                )
                with st.popover("Show the stored text for internal review"):
                    st.caption(
                        "Internal research view. Do not send this: at least one gate currently "
                        "fails."
                    )
                    st.text(draft["body"])

            unresolved = json.loads(draft["unresolved_fields"] or "[]")
            if unresolved:
                st.caption("Unresolved when written: " + "; ".join(unresolved))


def _export_tab(db, config, clock, now) -> None:
    st.caption(
        "A research export and a contact-ready export are different outputs. Contact fields are "
        "never exported for a suppressed account, for a channel without a recorded permission, or "
        "where the source licence forbids export. Cells that a spreadsheet could read as a "
        "formula are neutralised."
    )
    kind = st.radio(
        "Export", ["research", "contact_ready"], horizontal=True,
        format_func=lambda k: "Research export" if k == "research" else "Contact-ready export",
    )
    channel = st.radio("Channel", ["phone", "email"], horizontal=True, key="export_channel")

    with db.open() as conn:
        companies = companies_repo.list_companies(conn)

    chosen = st.multiselect(
        "Companies",
        [c.id for c in companies],
        default=[c.id for c in companies],
        format_func=lambda cid: next(c.display_name for c in companies if c.id == cid),
    )

    if not st.button("Build export", icon=":material/table:"):
        st.caption("The export is built only when you press the button.")
        return

    policy_service = SourcePolicy(db, config, clock)
    with db.open() as conn:
        result = outreach.build_export(
            conn,
            kind=kind,
            company_ids=chosen,
            policy_service=policy_service,
            now=now,
            config=config,
            channel=channel,
        )

    st.markdown(f"**{result.summary()}**")
    if result.rows:
        st.dataframe(result.rows, width="stretch", hide_index=True)
        csv_body = export_csv(result.rows, result.columns)
        st.download_button(
            "Download CSV",
            data=csv_body.encode("utf-8"),
            file_name=f"{kind}-export.csv",
            mime="text/csv",
            icon=":material/download:",
        )
    else:
        empty_state("No row qualified for this export.")

    if result.excluded:
        st.markdown("**Excluded, with the reason**")
        for reason in result.excluded:
            st.markdown(f"- {reason}")
