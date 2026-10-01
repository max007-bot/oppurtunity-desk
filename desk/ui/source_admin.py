"""Source administration: recording a review, and recording dated exchange rates.

Kept apart from the source register view because these two screens write, and the
register only reads. Both are deliberately dull forms: the point is that enabling
a source or converting a currency is a decision someone made, with their name on
it, rather than a value that appeared.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

import streamlit as st

from ..config import SourceStatus
from ..services import fx, source_review
from ..services.source_policy import SourcePolicy
from .components import day, empty_state


def review_tab(db, config, clock) -> None:
    """Record an approval, its scope and its expiry, instead of editing the row."""
    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    policy = SourcePolicy(db, config, clock)
    sources = policy.list_sources()
    if not sources:
        empty_state("No source to review.")
        return

    st.caption(
        "Approving a source is the decision that lets this application talk to somebody "
        "else's service. It is recorded with a named reviewer, a stated scope, what the "
        "permission rests on, and a date it lapses. Reviews are kept as history, never "
        "overwritten, so the next reviewer can see what the last one relied on."
    )

    with db.open() as conn:
        overdue = source_review.due_for_review(conn, now=clock.now())
    if overdue:
        st.warning(
            "Review due or overdue: "
            + ", ".join(f"{row['name']} ({day(row['review_due_at'])})" for row in overdue),
            icon=":material/event_busy:",
        )

    chosen = st.selectbox(
        "Source",
        [source.id for source in sources],
        format_func=lambda sid: next(s.name for s in sources if s.id == sid),
        key="review_source",
    )
    source = policy.get(chosen)
    if source is None:
        return

    st.markdown(
        f"Currently **{policy.effective_status(source)}** "
        f"(stored as `{source.status}`), access mode `{source.access_mode}`."
    )
    if source.documentation_url:
        st.caption(f"Read the current documentation before approving: {source.documentation_url}")
    if source.access_mode == "licensed_api":
        st.info(
            "This is a licensed source. Reading its published terms is not the same as holding "
            "the account scope those terms describe, so an approval here has to rest on a "
            "contract or an activated account.",
            icon=":material/gavel:",
        )

    with db.open() as conn:
        past = source_review.history(conn, chosen)
    if past:
        with st.expander(f"Review history ({len(past)})"):
            for row in past:
                st.markdown(
                    f"- {day(row['reviewed_at'])}: **{row['previous_status']} to "
                    f"{row['status']}** by {row['reviewer']} ({row['evidence_kind']})"
                    + (f" - {row['notes']}" if row["notes"] else "")
                )

    statuses = [s.value for s in SourceStatus]
    evidence_kinds = list(source_review.EVIDENCE_KINDS)

    with st.form(f"review_{chosen}"):
        columns = st.columns(3)
        status = columns[0].selectbox(
            "New status", statuses, index=statuses.index(source.status)
        )
        reviewer = columns[1].text_input("Reviewer", value=source.reviewer or "Max Watkinson")
        review_days = columns[2].number_input(
            "Review again in (days)",
            min_value=1,
            max_value=3650,
            value=source_review.DEFAULT_REVIEW_PERIOD_DAYS,
            help="Approval lapses on this date by itself, without anyone editing the row.",
        )

        evidence_kind = st.selectbox(
            "What does the permission rest on?",
            evidence_kinds,
            index=evidence_kinds.index("unknown"),
            format_func=lambda kind: (
                f"{kind.replace('_', ' ')} - {source_review.EVIDENCE_KINDS[kind]}"
            ),
        )
        approved_use = st.text_area(
            "Approved use",
            value=source.approved_use or "",
            help="What this source may be used for, in plain words.",
        )
        approval_evidence = st.text_area(
            "Approval evidence",
            value=source.approval_evidence or "",
            help=(
                "Where the permission comes from: which terms were read and when, which "
                "contract or account, or who authorised it."
            ),
        )

        scope = st.columns(2)
        hosts = scope[0].text_area(
            "Allowed hosts (one per line)", value="\n".join(source.allowed_hosts)
        )
        paths = scope[1].text_area(
            "Allowed path prefixes (one per line)", value="\n".join(source.allowed_paths)
        )

        limits = st.columns(4)
        rate_limit = limits[0].number_input(
            "Requests per minute",
            min_value=0,
            max_value=6000,
            value=int(source.rate_limit_per_minute or 0),
        )
        concurrency = limits[1].number_input(
            "Max concurrency", min_value=1, max_value=32, value=int(source.max_concurrency or 1)
        )
        retention_days = limits[2].number_input(
            "Retention (days, 0 if none stated)",
            min_value=0,
            max_value=3650,
            value=int(source.retention_days or 0),
        )
        export_allowed = limits[3].checkbox("Export permitted", value=source.export_allowed)

        raw_allowed = st.checkbox(
            "Raw content may be retained", value=source.raw_retention_allowed
        )
        retention_rule = st.text_input("Retention rule", value=source.retention_rule or "")
        notes = st.text_area("Notes", value=source.notes or "")

        submitted = st.form_submit_button("Record this review", icon=":material/gavel:")

    if not submitted:
        st.caption("Nothing is written until you record the review.")
        return

    now = clock.now()
    request = source_review.ReviewRequest(
        source_id=chosen,
        status=status,
        reviewer=reviewer,
        reviewed_at=now,
        approved_use=approved_use or None,
        approval_evidence=approval_evidence or None,
        evidence_kind=evidence_kind,
        allowed_hosts=[line.strip() for line in hosts.splitlines() if line.strip()],
        allowed_paths=[line.strip() for line in paths.splitlines() if line.strip()],
        retention_rule=retention_rule or None,
        retention_days=int(retention_days) or None,
        raw_retention_allowed=raw_allowed,
        export_allowed=export_allowed,
        rate_limit_per_minute=int(rate_limit) or None,
        max_concurrency=int(concurrency),
        review_due_at=now + timedelta(days=int(review_days)),
        notes=notes or None,
    )

    try:
        with db.write() as conn:
            result = source_review.apply_review(
                conn, request=request, source=source, config=config, now=now
            )
    except source_review.ReviewRefused as exc:
        st.error(f"**Review refused.** {exc}", icon=":material/block:")
        return

    st.success(result.summary(), icon=":material/task_alt:")
    for warning in result.warnings:
        st.warning(warning, icon=":material/info:")


def rates_tab(db, config, clock) -> None:
    """Dated exchange rates. Nothing converts without one."""
    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    st.caption(
        "A non-EUR price is stored but stays out of any comparison until a rate with a "
        "direction and a date is entered here. The prototype never assumes 1:1, never reuses "
        "an undated rate, and shows the rate and its date wherever a converted figure appears. "
        "A rate makes two prices arithmetically comparable, not commercially comparable."
    )

    notice = st.session_state.pop("fx_notice", None)
    if notice is not None:
        st.success(notice, icon=":material/task_alt:")

    now = clock.now()
    with db.open() as conn:
        rates = fx.list_rates(conn)

    if rates:
        st.dataframe(fx.rates_summary(rates, at=now), width="stretch", hide_index=True)
        st.caption(
            f"A rate older than {fx.DEFAULT_MAX_RATE_AGE_DAYS} days is not used for a "
            f"conversion; the affected comparable is excluded and says so. That window is a "
            f"product assumption, not a market standard. The inverse direction is derived, so "
            f"only one direction needs entering."
        )
    else:
        empty_state(
            "No exchange rate recorded. Cross-currency comparisons stay excluded until one is."
        )

    with st.form("fx_rate"):
        st.markdown("**Record a dated rate**")
        columns = st.columns(4)
        from_currency = columns[0].text_input("From", value="EUR", max_chars=3)
        to_currency = columns[1].text_input("To", value="GBP", max_chars=3)
        rate_text = columns[2].text_input(
            "Rate",
            value="",
            placeholder="0.8450",
            help="How many units of the target currency one unit of the source currency buys.",
        )
        rate_day = columns[3].date_input("Rate date", value=now.date(), max_value=now.date())
        source_note = st.text_input(
            "Where the rate came from",
            placeholder="for example: ECB reference rate for this date, read by Max",
            help="A rate with no stated origin is not evidence.",
        )
        submitted = st.form_submit_button("Record rate", icon=":material/currency_exchange:")

    if not submitted:
        return

    try:
        value = Decimal(str(rate_text).strip())
    except (InvalidOperation, AttributeError):
        st.error("Enter the rate as a decimal number, for example 0.8450.")
        return

    try:
        with db.write() as conn:
            fx.record_rate(
                conn,
                from_currency=from_currency,
                to_currency=to_currency,
                rate=value,
                rate_date=rate_day,
                source_note=source_note,
                now=clock.now_iso(),
                is_demo=config.is_demo,
            )
    except (fx.FxError, ValueError) as exc:
        st.error(str(exc), icon=":material/error:")
        return

    st.session_state["fx_notice"] = (
        f"Recorded 1 {from_currency.upper()} = {value} {to_currency.upper()} as at "
        f"{rate_day.isoformat()}. Recompute to let existing analyses use it."
    )
    st.rerun()
