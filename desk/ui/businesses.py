"""The Businesses screen: companies, contacts, permissions and buying briefs."""

from __future__ import annotations

import streamlit as st

from ..clock import to_iso
from ..config import INTERACTION_OUTCOMES, ContactPolicyStatus
from ..repositories import companies as companies_repo
from ..repositories import followup as followup_repo
from .components import (
    day,
    empty_state,
    freshness_chip,
    get_clock,
    get_config,
    get_db,
    local,
    mode_banner,
    money,
)


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()
    now = clock.now()

    st.title("Businesses and buyer requirements")
    mode_banner(config)

    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    with db.open() as conn:
        companies = companies_repo.list_companies(conn)

    if not companies:
        empty_state("No companies recorded.")
        return

    partners = [c for c in companies if c.is_referral_partner]
    buyers = [c for c in companies if not c.is_referral_partner]

    buyers_tab, partners_tab = st.tabs(
        [f"Companies ({len(buyers)})", f"Referral partners ({len(partners)})"]
    )

    with buyers_tab:
        columns = st.columns(3)
        category = columns[0].selectbox(
            "Category", ["all", *sorted({c.category for c in buyers})]
        )
        country = columns[1].selectbox("Country", ["all", *sorted({c.country for c in buyers})])
        route = columns[2].selectbox(
            "Confirmed buying route", ["all", *sorted({c.buying_route for c in buyers})]
        )

        shown = [
            c
            for c in buyers
            if (category == "all" or c.category == category)
            and (country == "all" or c.country == country)
            and (route == "all" or c.buying_route == route)
        ]
        st.caption(f"{len(shown)} of {len(buyers)} company record(s).")
        for company in shown:
            _company_card(company, db, config, now)

    with partners_tab:
        st.caption(
            "A concierge or property company can be a useful introduction route. It is kept as a "
            "partner subtype with introduction notes, and no fleet is attributed to it."
        )
        if not partners:
            empty_state("No referral partners recorded.")
        for company in partners:
            _company_card(company, db, config, now)


def _company_card(company, db, config, now) -> None:
    with db.open() as conn:
        contacts = companies_repo.list_contacts(conn, company.id)
        policies = companies_repo.list_policies(conn, company.id)
        briefs = companies_repo.list_briefs(conn, company_id=company.id)
        suppressions = companies_repo.suppressions_for(conn, company.id)
        interactions = followup_repo.list_interactions(conn, company_id=company.id, limit=8)
        parent = (
            companies_repo.get_company(conn, company.parent_company_id)
            if company.parent_company_id
            else None
        )

    confirmed = [b for b in briefs if b.is_confirmed and not b.is_expired(now)]
    header = f"{company.display_name} · {company.country} · {company.category}"
    if confirmed:
        header += f" · {len(confirmed)} confirmed requirement(s)"

    with st.expander(header):
        if suppressions:
            st.error(
                "**Suppressed from outreach.** "
                + "; ".join(row["reason"] for row in suppressions)
                + " This overrides any contact permission and any match score.",
                icon=":material/block:",
            )

        columns = st.columns(2)
        with columns[0]:
            st.markdown("**Identity**")
            st.markdown(
                f"- Legal name: {company.legal_name}\n"
                f"- Registry id: {company.registry_id or 'not known'}"
                f"{' (verified)' if company.registry_verified else ' (unverified)'}\n"
                f"- Location: {company.city or 'unknown'}, {company.country}\n"
                f"- Website: {company.website or 'unknown'}\n"
                f"- Review status: `{company.review_status}`"
            )
            if parent is not None:
                st.info(
                    f"A branch of {parent.display_name}. A branch is not automatically the "
                    f"purchasing entity.",
                    icon=":material/account_tree:",
                )
            if company.network_claim:
                st.warning(
                    f"The company states it {company.network_claim}. This is recorded as a claim: "
                    f"there is no evidence it owns those cars or purchases centrally.",
                    icon=":material/campaign:",
                )
            if company.introduction_notes:
                st.caption(company.introduction_notes)

        with columns[1]:
            st.markdown("**Buying route and authority**")
            st.markdown(
                f"- Confirmed route: `{company.buying_route}`\n"
                f"- Buying authority: {company.buying_authority}"
            )
            if company.buying_authority == "unknown":
                st.caption(
                    "Buying authority is recorded as unknown. No decision-maker has been "
                    "invented to fill the gap."
                )

            st.markdown("**Published business contacts**")
            if not contacts:
                st.markdown("None recorded.")
            for contact in contacts:
                name = contact.full_name or "unknown person"
                role = f", {contact.role_title}" if contact.role_title else ""
                st.markdown(
                    f"- {name}{role} · {contact.contact_type} · "
                    f"{contact.business_phone or contact.business_email or 'no route recorded'} "
                    + freshness_chip(
                        contact.verified_at, days=config.freshness.contact_days, now=now
                    )
                )

        st.markdown("**Contact permissions**")
        if not policies:
            st.markdown(
                "No channel policy recorded, so this account is not in any actionable outreach "
                "list."
            )
        for policy in policies:
            effective = policy.effective_status(now)
            tone = {
                ContactPolicyStatus.PERMITTED_FOR_SCOPE.value: ":green-badge",
                ContactPolicyStatus.DENIED.value: ":red-badge",
                ContactPolicyStatus.EXPIRED.value: ":orange-badge",
            }.get(effective, ":grey-badge")
            st.markdown(f"{tone}[{policy.channel}: {effective}] — {policy.purpose}")
            if policy.basis:
                st.caption(f"Basis: {policy.basis}")
            if policy.restrictions:
                st.caption(f"Restrictions: {policy.restrictions}")
            if policy.reviewer:
                st.caption(
                    f"Reviewed by {policy.reviewer} on {day(policy.reviewed_at)}"
                    + (f", expires {day(policy.expires_at)}" if policy.expires_at else "")
                )
        st.caption(
            "A published address discovered on a website never becomes a permission. Permission "
            "is an accountable human decision recorded here, not a legal conclusion from this "
            "application."
        )

        st.markdown("**Buying briefs**")
        if not briefs:
            st.markdown(
                "No buying requirement recorded. Public category fit is not confirmed demand."
            )
        for brief in briefs:
            _brief_block(brief, now, config)

        st.markdown("**Recorded outcomes**")
        if not interactions:
            st.markdown("No conversation recorded.")
        for row in interactions:
            st.markdown(
                f"- {local(row['occurred_at'], config)} · {row['channel']} · "
                f"**{row['outcome']}**"
                + (f" — {row['notes']}" if row["notes"] else "")
            )

        _record_outcome_form(company, db, config)


def _brief_block(brief, now, config) -> None:
    state = []
    if brief.is_expired(now):
        state.append(":orange-badge[expired, needs reconfirmation]")
    elif brief.is_confirmed:
        state.append(":green-badge[confirmed]")
    else:
        state.append(":grey-badge[discussed, not confirmed]")

    with st.container(border=True):
        st.markdown(f"**{brief.model_family}** {' '.join(state)}")
        st.markdown(
            f"- Budget: {money(brief.budget)} on a {brief.budget_basis}/"
            f"{brief.budget_vat_regime} basis — a ceiling to test, not a commitment\n"
            f"- Quantity: {brief.quantity}\n"
            f"- Destination: {brief.destination_country or 'unknown'}\n"
            f"- Route: `{brief.route}`\n"
            f"- Needed by: {day(brief.required_by)}\n"
            f"- Last discussed: {day(brief.conversation_date)}\n"
            f"- Confirmed by: {brief.confirmed_by or 'not confirmed'}\n"
            f"- Expires: {day(brief.expires_at)}"
        )
        if brief.required_specs:
            st.markdown("Hard requirements: " + ", ".join(f"`{k}={v}`" for k, v in brief.required_specs.items()))
        if brief.preferred_specs:
            st.caption(
                "Preferences (ranking only, never a pass or fail): "
                + ", ".join(f"{k}={v}" for k, v in brief.preferred_specs.items())
            )
        if brief.evidence_note:
            st.caption(f"Evidence: {brief.evidence_note}")


def _record_outcome_form(company, db, config) -> None:
    """Outcomes are typed in by a person. Nothing is inferred from silence."""
    with st.form(f"outcome_{company.id}"):
        st.markdown("**Record an outcome**")
        columns = st.columns(3)
        channel = columns[0].selectbox(
            "Channel", ["phone", "email", "post", "web_form", "in_person"], key=f"ch_{company.id}"
        )
        outcome = columns[1].selectbox("Outcome", INTERACTION_OUTCOMES, key=f"oc_{company.id}")
        recorded_by = columns[2].text_input("Recorded by", value="Max", key=f"rb_{company.id}")
        notes = st.text_area("Notes", key=f"nt_{company.id}")
        submitted = st.form_submit_button("Save outcome", icon=":material/save:")

    if submitted:
        clock = get_clock()
        now = to_iso(clock.now())
        with db.write() as conn:
            followup_repo.record_interaction(
                conn,
                company_id=company.id,
                channel=channel,
                occurred_at=now,
                outcome=outcome,
                now=now,
                is_demo=config.is_demo,
                notes=notes or None,
                recorded_by=recorded_by or None,
            )
            if outcome == "objection":
                companies_repo.add_suppression(
                    conn,
                    company_id=company.id,
                    reason=f"objection recorded on {now[:10]}: {notes or 'no detail given'}",
                    now=now,
                    is_demo=config.is_demo,
                    recorded_by=recorded_by or None,
                )
        if outcome == "objection":
            st.error(
                "Objection recorded. This account is now suppressed from every outreach list "
                "and export, and any existing contact-ready draft is blocked.",
                icon=":material/block:",
            )
        else:
            st.success("Outcome recorded.")
