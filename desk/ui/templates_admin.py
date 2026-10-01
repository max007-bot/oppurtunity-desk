"""Managing reviewed cost templates for a route.

Kept beside the other administrative screens, because a template is a review
decision like any other: it says who established these figures, when, and from
what, and it lapses.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import streamlit as st

from ..money import Money
from ..services import cost_templates
from .components import day, empty_state

# The lines a cross-border route usually needs. Offered as a starting point; the
# figures are always the reviewer's, never the application's.
SUGGESTED_LINES = [
    ("Transport", "non_recoverable"),
    ("Preparation", "non_recoverable"),
    ("Documents and handling", "non_recoverable"),
    ("Registration tax", "non_recoverable"),
    ("Homologation or conformity", "non_recoverable"),
    ("Funding cost", "non_recoverable"),
    ("Risk allowance", "non_recoverable"),
    ("Recoverable VAT paid up front", "recoverable"),
]


def render(db, config, clock) -> None:
    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    now = clock.now()
    st.caption(
        "A cost template is a reviewed set of assumptions for one route, so registration tax and "
        "the rest are not retyped differently into every worksheet. It is not a tax engine: every "
        "line stays editable after it is loaded, and a line with no amount arrives as unknown and "
        "still blocks a complete scenario."
    )

    notice = st.session_state.pop("template_notice", None)
    if notice is not None:
        st.success(notice, icon=":material/task_alt:")

    with db.open() as conn:
        existing = cost_templates.list_templates(conn)
        overdue = cost_templates.due_for_review(conn, now=now)

    if overdue:
        st.warning(
            "Review due or overdue: "
            + ", ".join(f"{row['name']} ({day(row['review_due_at'])})" for row in overdue)
            + ". Registration taxes change; a stale figure presented confidently is worse than "
            "no figure at all.",
            icon=":material/event_busy:",
        )

    if existing:
        st.dataframe(
            [
                {
                    "name": template.name,
                    "route": template.route_label(),
                    "model": template.model_family or "any",
                    "lines": len(template.lines),
                    "unknown lines": len(template.unknown_lines),
                    "currency": template.currency,
                    "reviewer": template.reviewer,
                    "reviewed": template.reviewed_at.date().isoformat(),
                    "review due": (
                        "" if template.review_due_at is None
                        else template.review_due_at.date().isoformat()
                    ),
                    "state": "lapsed" if template.is_stale(now) else "current",
                }
                for template in existing
            ],
            width="stretch",
            hide_index=True,
        )

        chosen = st.selectbox(
            "Inspect a template",
            [t.id for t in existing],
            format_func=lambda tid: next(t.name for t in existing if t.id == tid),
        )
        template = next(t for t in existing if t.id == chosen)
        st.caption(template.describe(now))
        st.dataframe(
            [
                {
                    "line": line.label,
                    "amount": "unknown" if line.amount is None else line.amount.format(),
                    "confirmed zero": "yes" if line.confirmed_zero else "",
                    "tax treatment": line.tax_treatment,
                    "in purchase price": "yes" if line.already_in_purchase_price else "",
                    "evidence": line.evidence_note or "",
                }
                for line in template.lines
            ],
            width="stretch",
            hide_index=True,
        )
        if st.button("Retire this template", key=f"retire_{template.id}"):
            with db.write() as conn:
                cost_templates.retire(conn, template.id, now=now)
            st.session_state["template_notice"] = (
                f"{template.name} retired. It is no longer offered, and the record of what it "
                f"was survives."
            )
            st.rerun()
    else:
        empty_state("No cost template recorded. Worksheets start from an empty cost sheet.")

    _form(db, config, clock, now)


def _form(db, config, clock, now: datetime) -> None:
    st.divider()
    st.markdown("**Record a template**")

    with st.form("cost_template"):
        columns = st.columns(4)
        name = columns[0].text_input("Name", placeholder="CZ to DE, G-Class")
        origin = columns[1].text_input("Origin country (blank for any)", max_chars=2)
        destination = columns[2].text_input("Destination country", max_chars=2)
        currency = columns[3].selectbox("Currency", ["EUR", "GBP", "CZK", "PLN", "CHF"])

        meta = st.columns(3)
        model_family = meta[0].text_input("Model family (blank for any)", value="")
        reviewer = meta[1].text_input("Reviewer", value="Max Watkinson")
        review_days = meta[2].number_input(
            "Review again in (days)",
            min_value=1,
            max_value=3650,
            value=cost_templates.DEFAULT_REVIEW_PERIOD_DAYS,
        )

        basis = st.text_area(
            "What these figures were established from",
            placeholder=(
                "for example: quotes from our usual transporter of 2026-09-20, and the "
                "registration tax table published by the destination authority, read on the "
                "same date"
            ),
            help="These figures get reused, so it has to be possible to see where they came from.",
        )

        st.markdown("Lines. Leave an amount blank to record it as **unknown**.")
        entered: list[cost_templates.TemplateLine] = []
        for label, treatment in SUGGESTED_LINES:
            row = st.columns([3, 2, 2, 3])
            include = row[0].checkbox(label, key=f"tpl_inc_{label}")
            amount_text = row[1].text_input(
                "amount", key=f"tpl_amt_{label}", label_visibility="collapsed", placeholder="blank = unknown"
            )
            zero = row[2].checkbox(
                "confirmed zero", key=f"tpl_zero_{label}", label_visibility="visible"
            )
            note = row[3].text_input(
                "evidence", key=f"tpl_note_{label}", label_visibility="collapsed",
                placeholder="where this figure came from",
            )
            if not include:
                continue
            amount = None
            if amount_text.strip():
                try:
                    amount = Money.from_decimal(Decimal(amount_text.strip()), currency)
                except (InvalidOperation, ValueError):
                    amount = None
            entered.append(
                cost_templates.TemplateLine(
                    label=label,
                    amount=amount,
                    confirmed_zero=zero,
                    tax_treatment=treatment,
                    evidence_note=note or None,
                )
            )

        notes = st.text_input("Notes", value="")
        submitted = st.form_submit_button("Save template", icon=":material/save:")

    if not submitted:
        return

    try:
        with db.write() as conn:
            cost_templates.save(
                conn,
                name=name,
                destination_country=destination,
                basis=basis,
                reviewer=reviewer,
                reviewed_at=now,
                lines=entered,
                now=now,
                is_demo=config.is_demo,
                currency=currency,
                origin_country=origin or None,
                model_family=model_family or None,
                review_due_at=now + timedelta(days=int(review_days)),
                notes=notes or None,
            )
    except cost_templates.TemplateError as exc:
        st.error(str(exc), icon=":material/error:")
        return
    except Exception as exc:
        st.error(f"Could not save the template: {exc}", icon=":material/error:")
        return

    st.session_state["template_notice"] = (
        f"Template {name!r} saved with {len(entered)} line(s). It is now offered on any "
        f"worksheet for that route."
    )
    st.rerun()
