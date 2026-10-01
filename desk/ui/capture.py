"""Capturing a buying requirement while the call is still fresh.

Deliberately one screen with one purpose. The fields are the ones the matching
rules actually use, so what a salesperson types turns into something the desk can
reason about rather than a free-text note nobody reads again.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation

import streamlit as st

from ..config import BuyingRoute
from ..money import Money
from ..repositories import companies as companies_repo
from ..services import briefs as briefs_service
from ..services.demo import COMPANY_SOURCE
from .components import day, empty_state, get_clock, get_config, get_db, mode_banner

HUMAN_SOURCE = "human_entry_demo"

MODEL_FAMILIES = [
    "mercedes_g63",
    "mercedes_s_class",
    "bmw_7_series",
    "bmw_x5",
    "bmw_x7",
]


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()

    st.title("Capture a requirement")
    mode_banner(config)
    st.caption(
        "Type what the buyer actually said, while the call is fresh. Leave anything you did not "
        "ask blank: an unknown requirement stays unknown, and that is more useful than a guess "
        "that quietly rules cars in or out."
    )

    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    notice = st.session_state.pop("capture_notice", None)
    if notice is not None:
        st.success(notice["summary"], icon=":material/task_alt:")
        for warning in notice["warnings"]:
            st.warning(warning, icon=":material/help:")

    with db.open() as conn:
        companies = [c for c in companies_repo.list_companies(conn) if not c.is_referral_partner]

    if not companies:
        empty_state("No company to attach a requirement to. Import or discover one first.")
        return

    _form(db, config, clock, companies)
    _recent(db, config, clock)


def _form(db, config, clock, companies) -> None:
    now = clock.now()
    labels = {c.id: f"{c.display_name} ({c.country}, {c.category})" for c in companies}

    with st.form("capture_brief"):
        st.markdown("**Who, and for what**")
        columns = st.columns([3, 2, 2])
        company_id = columns[0].selectbox(
            "Company", list(labels), format_func=lambda cid: labels[cid]
        )
        model_family = columns[1].selectbox("Model family", MODEL_FAMILIES)
        variant = columns[2].text_input("Variant, if they were specific", value="")

        contact_columns = st.columns([2, 2, 2])
        contact_name = contact_columns[0].text_input("Who said it", value="")
        conversation_day = contact_columns[1].date_input(
            "Date of the conversation", value=now.date(), max_value=now.date()
        )
        confirmed = contact_columns[2].checkbox(
            "They confirmed it, not just discussed it",
            help=(
                "Only a confirmed requirement can reach a contact-ready state. If you are not "
                "sure they committed to the specification, leave this off."
            ),
        )

        st.markdown("**Budget**")
        budget_columns = st.columns(4)
        budget_amount = budget_columns[0].text_input(
            "Budget", value="", placeholder="215000",
            help="A ceiling to test against an asking price. It is never treated as a commitment.",
        )
        budget_currency = budget_columns[1].selectbox("Currency", ["EUR", "GBP", "CZK", "PLN", "CHF"])
        budget_basis = budget_columns[2].selectbox(
            "Net or gross?", ["unknown", "net", "gross"],
            help="Without this the budget cannot be compared with an asking price at all.",
        )
        budget_regime = budget_columns[3].selectbox(
            "VAT regime", ["unknown", "standard", "margin", "other"]
        )

        st.markdown("**What would rule a car out**")
        st.caption(
            "These are hard requirements. A conflict is a rejection; a missing one is a question "
            "to go back with, never a pass."
        )
        required: dict[str, object] = {}
        grid = st.columns(3)
        required["steering"] = grid[0].selectbox("Steering side", ["unknown", "lhd", "rhd"])
        required["powertrain"] = grid[1].selectbox(
            "Powertrain", ["unknown", "petrol", "diesel", "hybrid", "electric"]
        )
        required["stock_requirement"] = grid[2].selectbox(
            "Stock", ["unknown", "physical_stock", "allocation_ok"],
            help="Physical stock means an allocation or build slot will not satisfy them.",
        )

        grid2 = st.columns(3)
        seats = grid2[0].number_input("Homologated seats (0 if not stated)", 0, 9, 0)
        max_mileage = grid2[1].number_input("Maximum mileage in km (0 if not stated)", 0, 500_000, 0, step=1000)
        max_age = grid2[2].number_input("Maximum age in months (0 if not stated)", 0, 240, 0)

        options_text = st.text_input(
            "Options they said are essential (comma separated)",
            placeholder="rear_entertainment, night_package",
        )

        st.markdown("**Quantity, destination and timing**")
        logistics = st.columns(4)
        quantity = logistics[0].number_input("Units", 1, 500, 1)
        destination = logistics[1].text_input("Destination country", value="", max_chars=2)
        route = logistics[2].selectbox("How they buy", [r.value for r in BuyingRoute])
        required_by_day = logistics[3].date_input("Needed by (optional)", value=None)

        evidence_note = st.text_area(
            "What was actually said",
            placeholder=(
                "Recorded call. Buyer stated physical stock only, LHD, five seats, rear "
                "entertainment required, ceiling 215,000 EUR net."
            ),
            help="This is the evidence for the requirement. Quote them rather than paraphrasing.",
        )

        submitted = st.form_submit_button("Capture requirement", icon=":material/save:")

    if not submitted:
        st.caption("Nothing is saved until you press the button.")
        return

    if seats:
        required["seats"] = int(seats)
    if max_mileage:
        required["max_mileage_km"] = int(max_mileage)
    if max_age:
        required["max_registration_age_months"] = int(max_age)
    options = [part.strip() for part in options_text.split(",") if part.strip()]
    if options:
        required["must_have_options"] = options

    budget = None
    if budget_amount.strip():
        try:
            budget = Money.from_decimal(Decimal(budget_amount.strip()), budget_currency)
        except (InvalidOperation, ValueError) as exc:
            st.error(f"Could not read the budget: {exc}")
            return

    company = next(c for c in companies if c.id == company_id)
    if company.external_record_id is None:
        st.error(
            "This company has no source record id, so a requirement cannot be attached to it "
            "reliably. Re-import it or pick another."
        )
        return

    conversation_at = datetime.combine(conversation_day, time(12, 0), tzinfo=timezone.utc)
    row = briefs_service.build_row(
        company_external_id=company.external_record_id,
        model_family=model_family,
        conversation_date=conversation_at,
        variant=variant or None,
        required_specs=required,
        budget=budget,
        budget_basis=budget_basis,
        budget_vat_regime=budget_regime,
        quantity=int(quantity),
        destination_country=destination or None,
        route=route,
        required_by=(
            None
            if required_by_day is None
            else datetime.combine(required_by_day, time(12, 0), tzinfo=timezone.utc)
        ),
        confirmed=confirmed,
        confirmed_by=contact_name or None,
        evidence_note=evidence_note or None,
        contact_name=contact_name or None,
    )

    with db.write() as conn:
        result = briefs_service.capture(
            conn,
            row=row,
            source_id=HUMAN_SOURCE,
            company_source_id=company.source_id or COMPANY_SOURCE,
            config=config,
            now=clock.now(),
        )

    if not result.ok:
        st.error(result.summary(), icon=":material/error:")
        return

    st.session_state["capture_notice"] = {
        "summary": result.summary(),
        "warnings": result.warnings,
    }
    st.rerun()


def _recent(db, config, clock) -> None:
    st.divider()
    st.markdown("**Recently captured requirements**")
    now = clock.now()
    with db.open() as conn:
        briefs = companies_repo.list_briefs(conn)[:10]
        names = {
            brief.id: (companies_repo.get_company(conn, brief.company_id) or None)
            for brief in briefs
        }

    if not briefs:
        empty_state("No requirement recorded yet.")
        return

    st.dataframe(
        [
            {
                "company": names[b.id].display_name if names[b.id] else "unknown",
                "model": b.model_family,
                "budget": "unknown" if b.budget is None else b.budget.format(),
                "basis": f"{b.budget_basis}/{b.budget_vat_regime}",
                "hard requirements": len(b.required_specs),
                "discussed": day(b.conversation_date),
                "confirmed by": b.confirmed_by or "not confirmed",
                "expires": day(b.expires_at),
                "state": "expired" if b.is_expired(now) else "current",
            }
            for b in briefs
        ],
        width="stretch",
        hide_index=True,
    )
