"""The Opportunities screen: one ranked list, with the reasoning on every card.

Each card answers four questions in the order a person actually asks them. What
is the car and where did it come from? Is it cheap against cars that are genuinely
comparable, and which ones were thrown out? What is left after costs, or what is
blocking that? And who has asked for one?

The arithmetic lives in ``services/opportunities.py``. This module only decides
what is worth showing and in what order.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

import streamlit as st

from ..config import ScenarioStatus
from ..repositories import companies as companies_repo
from ..money import Money
from ..services import cost_templates, drafting, opportunities as opp_service, pricing
from ..services.workflow import Workflow
from .components import (
    coverage_notice,
    day,
    empty_state,
    get_clock,
    get_config,
    get_db,
    mode_banner,
    money,
    status_chip,
)

TIER_TONE = {
    opp_service.TIER_VERIFIED_WITH_BUYER.key: "green",
    opp_service.TIER_VERIFIED.key: "green",
    opp_service.TIER_SIGNAL_WITH_BUYER.key: "orange",
    opp_service.TIER_SIGNAL.key: "orange",
    opp_service.TIER_INSUFFICIENT.key: "grey",
}


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()
    now = clock.now()
    workflow = Workflow(db, config, clock)

    st.markdown("## Opportunities")
    st.caption(
        "Every car currently on file, ranked by how much is actually known about it. "
        "A gap is a reason to look. A contribution is what is left after costs."
    )
    mode_banner(config)

    with db.open() as conn:
        items = opp_service.build(conn, workflow=workflow, config=config, now=now)

    if not items:
        empty_state("No supply offers on file. Import a stock file on Sources and imports.")
        return

    _summary(items)

    tier_names = {tier.key: tier.label for tier in opp_service.ALL_TIERS}
    chosen = st.multiselect(
        "Show",
        options=list(tier_names),
        default=[
            opp_service.TIER_VERIFIED_WITH_BUYER.key,
            opp_service.TIER_VERIFIED.key,
            opp_service.TIER_SIGNAL_WITH_BUYER.key,
            opp_service.TIER_SIGNAL.key,
        ],
        format_func=lambda key: tier_names[key],
        help=(
            "Ranking is by tier, not by a blended score. A verified contribution always "
            "outranks a larger unverified gap, because they are different kinds of claim."
        ),
    )
    visible = [item for item in items if item.tier.key in chosen]
    st.caption(f"{len(visible)} of {len(items)} shown.")
    coverage_notice()

    for index, item in enumerate(visible):
        _card(item, index=index, now=now, config=config, workflow=workflow, db=db)


# -- summary ----------------------------------------------------------------


def _summary(items: list[opp_service.Opportunity]) -> None:
    counts = {tier.key: 0 for tier in opp_service.ALL_TIERS}
    for item in items:
        counts[item.tier.key] += 1

    verified = counts[opp_service.TIER_VERIFIED_WITH_BUYER.key] + counts[
        opp_service.TIER_VERIFIED.key
    ]
    signals = counts[opp_service.TIER_SIGNAL_WITH_BUYER.key] + counts[
        opp_service.TIER_SIGNAL.key
    ]
    columns = st.columns(4)
    columns[0].metric("Verified contribution", verified, help="Costs complete.")
    columns[1].metric("Signal only", signals, help="Priced below comparables; costs unknown.")
    columns[2].metric("Not enough evidence", counts[opp_service.TIER_INSUFFICIENT.key])
    columns[3].metric("On file", len(items))


# -- one card ---------------------------------------------------------------


def _card(
    item: opp_service.Opportunity,
    *,
    index: int,
    now: datetime,
    config,
    workflow: Workflow,
    db,
) -> None:
    key = item.offer.external_record_id or item.offer.id
    headline = f"{item.label} · {money(item.asking)}"
    if item.contribution is not None:
        headline += f" · contribution {item.contribution.format()}"
    elif item.signal is not None:
        headline += f" · gap {item.signal.format()}"

    with st.expander(f"{item.tier.label} — {headline}", expanded=index == 0):
        st.markdown(
            " ".join(
                [
                    status_chip(item.tier.label, TIER_TONE.get(item.tier.key, "grey")),
                    status_chip(f"source: {item.source_label}", "grey"),
                    status_chip(
                        f"{item.offer.price_basis}/{item.offer.vat_regime}",
                        "grey" if opp_service.basis_is_known(item.offer) else "red",
                    ),
                ]
            )
        )
        st.caption(item.why_ranked())

        columns = st.columns([1.15, 1, 1])

        # -- the car ----------------------------------------------------
        with columns[0]:
            st.markdown("**The car**")
            vehicle = item.vehicle
            lines = [f"- Reference: `{key}`", f"- Seller: {item.offer.seller_name or 'unknown'}"]
            if vehicle is not None:
                lines += [
                    f"- Generation: {vehicle.generation or 'unknown'}",
                    f"- Mileage: "
                    f"{f'{vehicle.mileage_km:,} km' if vehicle.mileage_km is not None else 'unknown'}",
                    f"- First registered: {day(vehicle.first_registration)}",
                    f"- Powertrain: {vehicle.powertrain or 'unknown'}",
                    f"- Steering: {(vehicle.steering or 'unknown').upper()}",
                ]
            lines.append(
                f"- Location: {item.offer.location_city or 'unknown'}, "
                f"{item.offer.location_country or 'unknown'}"
            )
            st.markdown("\n".join(lines))
            st.caption(opp_service.freshness_note(item.offer, now=now, config=config))
            note, verdict = opp_service.tax_note(item.vehicle, now=now)
            if verdict is True:
                st.success(note, icon=":material/local_shipping:")
            elif verdict is None:
                st.warning(note, icon=":material/help:")
            else:
                st.caption(note)

        # -- the comparison ---------------------------------------------
        with columns[1]:
            st.markdown("**Against comparable cars**")
            if item.comparable_median is None:
                st.markdown(item.comparables.summary())
            else:
                st.metric("Comparable median", item.comparable_median.format())
                st.markdown(
                    f"- From **{item.comparables.distinct_vehicles}** distinct vehicles\n"
                    f"- Range {money(item.comparables.low)} – {money(item.comparables.high)}"
                )
                if item.signal is not None:
                    st.markdown(f"- **Gap {item.signal.format()}**")
                    st.caption(
                        "This is an investigation signal, not profit: it excludes every cost "
                        "and assumes a sale that has not happened."
                    )
            excluded = item.comparables.excluded
            if excluded:
                with st.popover(f"Why {len(excluded)} were excluded"):
                    for decision in excluded[:20]:
                        st.markdown(
                            f"- `{decision.candidate.external_record_id or decision.candidate.offer_id}` "
                            f"— {decision.reason}"
                        )

        # -- the money --------------------------------------------------
        with columns[2]:
            st.markdown("**After costs**")
            if item.contribution is not None:
                st.metric("Contribution", item.contribution.format())
                if item.scenario and item.scenario.sale_evidence_type:
                    st.caption(f"Sale side: {item.scenario.sale_evidence_label()}.")
            else:
                st.warning("Incomplete", icon=":material/block:")
                for fact in item.blocking_facts[:3]:
                    st.markdown(f"- {fact}")
                st.caption("An unknown cost is never treated as no cost.")
            if item.template_used:
                st.caption(f"Costs started from the reviewed template: {item.template_used}.")

        st.caption(f"**Next:** {item.what_would_move_it_up()}")

        _complete_scenario(item, key=key, now=now, workflow=workflow, db=db)
        st.divider()
        _buyers(item)
        st.divider()
        _drafts(item, key=key, now=now, config=config, db=db)


# -- fill the gap and recompute ---------------------------------------------


def _complete_scenario(
    item: opp_service.Opportunity, *, key: str, now: datetime, workflow: Workflow, db
) -> None:
    """Let a person supply the missing figure and see the answer change.

    Deliberately not persisted from this screen. A number typed to see what would
    happen is not a reviewed cost, and the scenario worksheet on Matches is where
    a figure is recorded with its basis.
    """
    if item.contribution is not None or item.asking is None:
        return

    with st.form(f"complete_{key}"):
        st.markdown("**Complete the scenario**")
        st.caption(
            "Enter what is missing to see the contribution. Nothing is saved here — "
            "record a reviewed figure on the Matches worksheet."
        )
        with db.open() as conn:
            template = cost_templates.best_for_route(
                conn,
                origin_country=item.offer.location_country,
                destination_country=_destination(item),
            )

        entered: dict[str, str] = {}
        if template is not None:
            unknown_lines = [line for line in template.lines if not line.is_known]
            for line in unknown_lines:
                entered[line.label] = st.text_input(
                    f"{line.label} ({item.asking.currency})", key=f"cost_{key}_{line.label}"
                )
        sale_text = ""
        sale, sale_evidence = opp_service.stated_sale(item.offer, item.matches)
        if sale is None:
            sale_text = st.text_input(
                f"Expected sale amount ({item.asking.currency})", key=f"sale_{key}"
            )

        if not st.form_submit_button("Calculate", type="primary"):
            return

        lines: list[pricing.CostLine] = []
        problems: list[str] = []
        if template is not None:
            for line in template.lines:
                amount = line.amount
                if amount is None:
                    raw = (entered.get(line.label) or "").strip()
                    if raw:
                        amount = _parse(raw, item.asking.currency, line.label, problems)
                lines.append(
                    pricing.CostLine(
                        line.label,
                        amount,
                        tax_treatment=line.tax_treatment,
                        confirmed_zero=line.confirmed_zero,
                        required=line.required,
                        paid_at=now,
                    )
                )
        if sale is None and sale_text.strip():
            sale = _parse(sale_text, item.asking.currency, "sale amount", problems)
            sale_evidence = "analyst_assumption"

        for problem in problems:
            st.error(problem)
        if problems:
            return

        with db.open() as conn:
            _, result = workflow.scenario_from_offer(
                conn,
                item.offer,
                comparables=item.comparables,
                costs=lines,
                sale=sale,
                sale_evidence_type=sale_evidence,
            )
        if result.status is ScenarioStatus.COMPLETE and result.contribution is not None:
            st.success(
                f"Contribution {result.contribution.format()} — "
                f"outlay {money(result.total_outlay)}, sale side "
                f"{result.sale_evidence_label()}.",
                icon=":material/check:",
            )
        else:
            st.warning(
                "Still incomplete: " + "; ".join(result.missing_inputs or ["unknown reason"]),
                icon=":material/block:",
            )


def _parse(raw: str, currency: str, label: str, problems: list[str]) -> Money | None:
    try:
        return Money.from_decimal(Decimal(raw.replace(",", "").strip()), currency)
    except (InvalidOperation, ValueError):
        problems.append(f"{label}: {raw!r} is not a number. It is left unknown rather than guessed.")
        return None


def _destination(item: opp_service.Opportunity) -> str | None:
    for match in item.matches:
        if match.brief is not None and match.brief.destination_country:
            return match.brief.destination_country
    return None


# -- buyers -----------------------------------------------------------------


def _buyers(item: opp_service.Opportunity) -> None:
    st.markdown("**Who has asked for one**")
    if not item.matches:
        st.caption(
            "No recorded requirement matches this specification. That is an absence of "
            "evidence, not evidence that nobody wants it."
        )
        _prospects(item)
        return

    for match in item.matches[:4]:
        with st.container(border=True):
            top = st.columns([3, 1])
            top[0].markdown(
                f"**{match.company.display_name}** · {match.company.country} · "
                f"score {match.score}"
            )
            top[1].markdown(
                status_chip(
                    "specification fit" if match.is_specification_fit else "needs verification",
                    "green" if match.is_specification_fit else "orange",
                )
            )
            columns = st.columns(3)
            columns[0].caption("Matched")
            for rule in match.matched[:8]:
                columns[0].markdown(f":material/check: {rule}")
            columns[1].caption("Failed")
            for rule in match.failed[:8] or ["No known conflict."]:
                columns[1].markdown(f":material/close: {rule}" if match.failed else rule)
            columns[2].caption("Unverified")
            for rule in match.unknown[:8] or ["Nothing outstanding."]:
                columns[2].markdown(f":material/help: {rule}" if match.unknown else rule)

    _prospects(item)


def _prospects(item: opp_service.Opportunity) -> None:
    """Category fit, kept visually and verbally separate from recorded demand."""
    if not item.prospects:
        return
    with st.expander(
        f"{len(item.prospects)} companies fit the category — none has asked for anything"
    ):
        st.caption(
            "These are businesses whose line of work makes this kind of car plausible. "
            "Nobody has spoken to them and none has stated a requirement. They are research, "
            "not demand, and they contribute nothing to the ranking."
        )
        for prospect in item.prospects[:12]:
            st.markdown(
                f"- **{prospect.company.display_name}** · {prospect.company.country} · "
                f"{prospect.company.category}"
            )


# -- drafts -----------------------------------------------------------------


def _drafts(item: opp_service.Opportunity, *, key: str, now: datetime, config, db) -> None:
    st.markdown("**Draft a message**")
    st.caption(drafting.HUMAN_IN_THE_LOOP)

    buy_tab, sell_tab = st.tabs(["To the seller (buy)", "To the buyer (sell)"])

    with buy_tab:
        draft = drafting.build_buy_side_draft(
            offer=item.offer,
            vehicle=item.vehicle,
            now=now,
            config=config,
            comparable_median=item.comparable_median,
            brief=item.best_match.brief if item.best_match else None,
        )
        _show_draft(draft, key=f"buy_{key}")

    with sell_tab:
        match = item.best_match
        if match is None or match.brief is None:
            st.caption(
                "No recorded requirement to write to. A draft needs a buyer who asked for "
                "something, not a company that looks plausible."
            )
        else:
            with db.open() as conn:
                contact = (
                    companies_repo.get_contact(conn, match.brief.contact_id)
                    if match.brief.contact_id
                    else None
                )
            draft = drafting.build_sell_side_draft(
                company=match.company,
                contact=contact,
                brief=match.brief,
                offer=item.offer,
                vehicle=item.vehicle,
                now=now,
                config=config,
                matched_rules=match.matched,
                unverified_rules=match.unknown,
            )
            _show_draft(draft, key=f"sell_{key}")


def _show_draft(draft: drafting.Draft, *, key: str) -> None:
    st.text_area(
        "Message",
        value=draft.message,
        height=260,
        key=f"draft_{key}",
        help="Edit it here, then copy. Nothing leaves this screen.",
    )
    columns = st.columns(2)
    with columns[0]:
        if draft.missing_facts:
            st.caption("**Asked rather than assumed**")
            for fact in draft.missing_facts[:5]:
                st.markdown(f"- {fact}")
    with columns[1]:
        if draft.withheld:
            st.caption("**Deliberately not claimed**")
            for claim in draft.withheld[:5]:
                st.markdown(f"- {claim}")
