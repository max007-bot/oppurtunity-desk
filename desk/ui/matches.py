"""The Matches screen: explained matches, comparables and the cost worksheet."""

from __future__ import annotations

import json
from decimal import Decimal

import streamlit as st

from ..config import FitStatus, PriceEvidenceType
from ..money import Money
from ..repositories import analysis as analysis_repo
from ..repositories import companies as companies_repo
from ..repositories import supply as supply_repo
from ..services import adjustments, cost_templates, pricing
from ..services.pricing import ComparableFilters, CostLine
from ..services.workflow import Workflow
from .components import (
    FIT_HELP,
    day,
    empty_state,
    fit_badge,
    freshness_chip,
    get_clock,
    get_config,
    get_db,
    local,
    mode_banner,
    money,
    stock_badge,
)

# The manual's worked example, offered as a one-click starting point so the
# arithmetic on screen can be checked against the document.
WORKED_EXAMPLE = {
    "acquisition": 200000,
    "transport": 1500,
    "preparation": 1000,
    "documents": 500,
    "funding": 2000,
    "risk": 1500,
    "retail": 230000,
    "dealer_cost": 2500,
    "dealer_contribution": 12500,
}


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()
    now = clock.now()

    st.title("Matches and deal scenarios")
    mode_banner(config)

    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    research_tab, prospects_tab = st.tabs(
        ["Matches against a buying brief", "Category-fit prospects"]
    )

    with research_tab:
        _matches_tab(db, config, now)
    with prospects_tab:
        _prospects_tab(db, config)


def _matches_tab(db, config, now) -> None:
    statuses = st.multiselect(
        "Fit status",
        [
            FitStatus.SPECIFICATION_FIT.value,
            FitStatus.NEEDS_VERIFICATION.value,
            FitStatus.BRIEF_EXPIRED.value,
            FitStatus.NO_MATCH.value,
        ],
        default=[FitStatus.SPECIFICATION_FIT.value, FitStatus.NEEDS_VERIFICATION.value],
        format_func=fit_badge,
    )
    if not statuses:
        empty_state("Select at least one fit status.")
        return

    with db.open() as conn:
        rows = [
            row
            for row in analysis_repo.list_matches(conn, fit_statuses=tuple(statuses))
            if row["brief_id"]
        ]

    st.caption(
        f"{len(rows)} match(es). A specification fit means every mandatory fact matches; it is "
        f"not evidence that the buyer will buy or that the economics work."
    )
    if not rows:
        empty_state("No match in these states.")
        return

    for row in rows:
        _match_card(row, db, config, now)


def _match_card(row, db, config, now) -> None:
    with db.open() as conn:
        offer = supply_repo.get_offer(conn, row["offer_id"])
        brief = companies_repo.get_brief(conn, row["brief_id"])
        company = companies_repo.get_company(conn, row["company_id"])

    # Checked before anything is read off them: a recompute can outlive a record.
    if offer is None or brief is None or company is None:
        return

    header = (
        f"{fit_badge(row['fit_status'])} · {company.display_name} wants "
        f"{brief.model_family} · offer {offer.external_record_id} at {money(offer.price)}"
    )
    with st.expander(header, expanded=row["fit_status"] == FitStatus.SPECIFICATION_FIT.value):
        st.caption(FIT_HELP.get(row["fit_status"], ""))
        st.markdown(
            " ".join(
                [
                    stock_badge(offer.stock_kind, offer.status),
                    freshness_chip(
                        offer.availability_confirmed_at,
                        hours=config.freshness.supply_hours,
                        now=now,
                    ),
                    f":grey-badge[score {row['score']}]",
                ]
            )
        )

        # An allocation is a build slot, not a car. A buyer who needs stock this
        # month cannot use one, and a badge in a row of badges is too easy to
        # read past, so it is said in a sentence as well.
        if offer.is_allocation:
            deadline = (
                f" The buyer needs one by {brief.required_by.date().isoformat()}."
                if brief.required_by is not None
                else ""
            )
            st.warning(
                "This is an allocation or build slot, not a car on the ground. Anything said "
                "about delivery depends on the factory date, not on this record." + deadline,
                icon=":material/schedule:",
            )

        passed = json.loads(row["passed_rules"] or "[]")
        failed = json.loads(row["failed_rules"] or "[]")
        unknown = json.loads(row["unknown_rules"] or "[]")

        columns = st.columns(3)
        with columns[0]:
            st.markdown("**Matched**")
            for item in passed:
                st.markdown(f":material/check: {item}")
            if not passed:
                st.markdown("Nothing matched.")
        with columns[1]:
            st.markdown("**Failed**")
            for item in failed:
                st.markdown(f":material/close: {item}")
            if not failed:
                st.markdown("No known conflict.")
        with columns[2]:
            st.markdown("**Unverified**")
            for item in unknown:
                st.markdown(f":material/help: {item}")
            if not unknown:
                st.markdown("Nothing outstanding.")

        breakdown = json.loads(row["score_breakdown"] or "{}")
        st.caption(
            "Score components: "
            + ", ".join(f"{key} {value}" for key, value in breakdown.items())
            + ". These weights are product choices to test, not predictors of closing. Contact "
            "availability and contact permission contribute nothing to this score."
        )
        st.caption(
            f"Last supply confirmation: {local(row['supply_confirmed_at'], config)} · "
            f"computed {local(row['computed_at'], config)} · "
            f"expires {local(row['expires_at'], config)} · rules `{row['rule_version']}`"
        )

        st.divider()
        _comparables_block(offer, db, config, key=row["id"])
        st.divider()
        _worksheet(offer, brief, company, row, db, config)


def _comparables_block(offer, db, config, *, key: str) -> None:
    clock = get_clock()
    workflow = Workflow(db, config, clock)

    st.markdown("**Comparable prices**")
    columns = st.columns(5)
    mileage_tolerance = columns[0].number_input(
        "Mileage tolerance (km)", min_value=0, max_value=200_000, value=20_000, step=1_000,
        key=f"mt_{key}",
    )
    registration_tolerance = columns[1].number_input(
        "Registration tolerance (days)", min_value=0, max_value=3_650, value=730, step=30,
        key=f"rt_{key}",
    )
    same_generation = columns[2].checkbox(
        "Same generation", value=True, key=f"sg_{key}"
    )
    same_variant = columns[3].checkbox(
        "Same variant",
        value=True,
        key=f"sv_{key}",
        help=(
            "An S 450 and an S 580 share a generation and not a price. Leave this on unless "
            "you have a reason to treat the variants as one market."
        ),
    )
    evidence = columns[4].selectbox(
        "Compare against",
        [PriceEvidenceType.RETAIL_ASKING.value, PriceEvidenceType.COMPLETED_TRANSACTION.value,
         PriceEvidenceType.DEALER_BID.value, PriceEvidenceType.SUPPLY_ASKING.value],
        key=f"ev_{key}",
        help=(
            "One evidence type at a time. Supply quotes, dealer-to-dealer bids, retail asking "
            "prices and completed transactions are never blended into a single market price."
        ),
    )

    filters = ComparableFilters(
        mileage_tolerance_km=int(mileage_tolerance),
        registration_tolerance_days=int(registration_tolerance),
        require_same_generation=same_generation,
        require_same_variant=same_variant,
    )
    with db.open() as conn:
        result = workflow.comparables_for(
            conn, offer, filters=filters, evidence_types=(evidence,), persist=False
        )

    if result.has_median:
        metrics = st.columns(4)
        metrics[0].metric(
            "Median",
            result.median.format(),
            delta=(
                None
                if not result.adjusted or result.adjustment_effect is None
                else f"{result.adjustment_effect.format()} from analyst adjustments"
            ),
            delta_color="off",
            help=(
                "The median of the figures actually used. Where an analyst adjustment applies, "
                "the median before those assumptions is shown beneath."
            ),
        )
        metrics[1].metric("Low", result.low.format())
        metrics[2].metric("High", result.high.format())
        metrics[3].metric("Distinct vehicles", result.distinct_vehicles)
        if result.adjusted and result.median_before_adjustments is not None:
            st.caption(
                f"Median before any analyst adjustment: "
                f"**{result.median_before_adjustments.format()}**. The difference is the effect "
                f"of assumptions a person entered, not of new evidence."
            )
    else:
        st.warning(
            f"**Insufficient comparables.** {result.summary()} The individual observations are "
            f"listed below instead of a fabricated median.",
            icon=":material/query_stats:",
        )

    gap = pricing.asking_price_gap(target_offer=offer, comparables=result)
    if gap.available:
        st.info(f"**Observed asking-price gap: {gap.gap.format()}.** {gap.explanation}", icon=":material/insights:")

    for note in result.notes:
        st.caption(note)

    included, excluded = st.tabs(
        [f"Included ({len(result.included)})", f"Excluded ({len(result.excluded)})"]
    )
    with included:
        if not result.included:
            empty_state("Nothing passed the filters.")
        else:
            st.dataframe(
                [
                    {
                        "as observed": d.observed_price.format(),
                        "rate applied": (
                            "" if d.conversion is None else d.conversion.rate.label
                        ),
                        "analyst adjustment": (
                            "" if d.adjustment is None else d.adjustment.amount.format()
                        ),
                        "figure used": d.price_used.format(),
                        "mileage": d.candidate.mileage_km,
                        "first registration": day(d.candidate.first_registration),
                        "country": d.candidate.location_country,
                        "observed": day(d.candidate.observed_at),
                        "basis": f"{d.candidate.price_basis}/{d.candidate.vat_regime}",
                        "why included": d.reason,
                    }
                    for d in result.included
                ],
                width="stretch",
                hide_index=True,
            )
            if result.assumptions:
                with st.expander(
                    f"Assumptions behind these figures ({len(result.assumptions)})",
                    expanded=True,
                ):
                    for item in result.assumptions:
                        st.markdown(f"- {item}")
    with excluded:
        st.caption(
            "Every exclusion is shown with its reason: the same badge is not enough to make two "
            "cars comparable."
        )
        st.dataframe(
            [
                {
                    "price": d.candidate.price.format(),
                    "mileage": d.candidate.mileage_km,
                    "observed": day(d.candidate.observed_at),
                    "why excluded": d.reason,
                }
                for d in result.excluded
            ],
            width="stretch",
            hide_index=True,
        )

    _adjustment_controls(offer, result, db, config, key=key)

    st.session_state[f"comparable_median_{key}"] = (
        result.median if result.has_median else None
    )


def _adjustment_controls(offer, result, db, config, *, key: str) -> None:
    """Let an analyst adjust one comparable, as a labelled assumption.

    Nothing is adjusted automatically. An adjustment needs a reason and an author,
    and its effect on the median is reported separately from the evidence.
    """
    clock = get_clock()
    with db.open() as conn:
        stored = adjustments.list_for_target(conn, offer.id)

    with st.expander(
        f"Analyst adjustments ({len(stored)} applied)", expanded=bool(stored)
    ):
        st.caption(
            "No monetary adjustment is ever applied automatically for options or mileage. If you "
            "believe a comparable needs one, enter it here with a reason: it is recorded as your "
            "assumption, its effect on the median is shown separately, and it can be removed "
            "without erasing that it was made."
        )

        if stored:
            for item in stored:
                columns = st.columns([5, 1])
                columns[0].markdown(
                    f"- **{item.amount.format()}** on offer `{_external(db, item.comparable_offer_id)}` "
                    f"- {item.reason} ({item.created_by}, {day(item.created_at)})"
                )
                if columns[1].button("Remove", key=f"rm_{key}_{item.id}"):
                    with db.write() as conn:
                        adjustments.retire(
                            conn,
                            target_offer_id=offer.id,
                            comparable_offer_id=item.comparable_offer_id,
                            now=clock.now(),
                        )
                    st.rerun()

        candidates = result.included
        if not candidates:
            empty_state("No included comparable to adjust.")
            return

        with st.form(f"adjust_{key}"):
            labels = {
                d.candidate.offer_id: (
                    f"{d.candidate.external_record_id or d.candidate.offer_id} - "
                    f"{d.observed_price.format()}"
                    f"{'' if d.candidate.mileage_km is None else f', {d.candidate.mileage_km:,} km'}"
                )
                for d in candidates
            }
            columns = st.columns([3, 2, 2])
            target = columns[0].selectbox(
                "Comparable", list(labels), format_func=lambda oid: labels[oid]
            )
            amount = columns[1].number_input(
                "Adjustment",
                value=0.0,
                step=500.0,
                help=(
                    "Signed. Negative means this comparable is worth less than our car for the "
                    "stated reason, so its price is reduced before the median."
                ),
            )
            author = columns[2].text_input("Entered by", value="Max")
            reason = st.text_input(
                "Reason",
                placeholder="for example: it has the carbon package, which our car does not",
            )
            submitted = st.form_submit_button(
                "Record adjustment", icon=":material/tune:"
            )

        if not submitted:
            return

        currency = (
            result.median.currency
            if result.median is not None
            else (offer.price.currency if offer.price else config.base_currency)
        )
        chosen = next(d for d in candidates if d.candidate.offer_id == target)
        try:
            with db.write() as conn:
                adjustments.record(
                    conn,
                    target_offer_id=offer.id,
                    comparable_offer_id=target,
                    amount=Money.from_decimal(Decimal(str(amount)), currency),
                    reason=reason,
                    created_by=author,
                    now=clock.now(),
                    is_demo=config.is_demo,
                    comparable_price=chosen.converted_price,
                )
        except (adjustments.AdjustmentError, ValueError) as exc:
            st.error(str(exc), icon=":material/error:")
            return
        st.rerun()


def _external(db, offer_id: str) -> str:
    with db.open() as conn:
        found = supply_repo.get_offer(conn, offer_id)
    return found.external_record_id if found else offer_id


def _offer_template(db, offer, brief, match_row, prefill: bool):
    """Offer the reviewed template for this route, if one exists.

    Nothing is applied automatically. The template only changes the defaults the
    form starts from, and every line stays editable afterwards, because the car in
    front of you may not match the assumption.
    """
    if prefill:
        return None

    clock = get_clock()
    destination = brief.destination_country if brief is not None else None
    with db.open() as conn:
        template = cost_templates.best_for_route(
            conn,
            origin_country=offer.location_country,
            destination_country=destination,
            model_family=brief.model_family if brief is not None else None,
        )
    if template is None:
        if destination:
            st.caption(
                f"No reviewed cost template exists for {offer.location_country or 'unknown'} to "
                f"{destination}. You can record one on Sources and imports, under Cost templates."
            )
        return None

    use = st.checkbox(
        f"Start the cost lines from the reviewed template: {template.name}",
        key=f"tpl_{match_row['id']}",
        help=template.describe(clock.now()),
    )
    if not use:
        return None

    st.caption(template.assumption(clock.now()))
    if template.is_stale(clock.now()):
        st.warning(
            f"That template's review lapsed on {template.review_due_at.date().isoformat()}. "
            f"Registration taxes change; check every figure before relying on it.",
            icon=":material/event_busy:",
        )
    if template.unknown_lines:
        st.warning(
            "The template knows these lines apply but not what they cost, so they arrive as "
            "unknown and still block a complete scenario: "
            + ", ".join(template.unknown_lines),
            icon=":material/help:",
        )
    return template


def _worksheet(offer, brief, company, match_row, db, config) -> None:
    """Acquisition price, costs, sale basis and the resulting scenario."""
    clock = get_clock()
    st.markdown("**Cost worksheet and deal scenario**")

    prefill = st.toggle(
        "Load the build manual's worked example instead of this offer's figures",
        key=f"wex_{match_row['id']}",
        help=(
            "Sets 200,000 EUR acquisition, the manual's five cost lines, a 235,000 EUR comparable "
            "median and the downstream dealer requirements, so the arithmetic on screen can be "
            "checked against section 7 of the manual."
        ),
    )

    template = _offer_template(db, offer, brief, match_row, prefill)

    def default(name: str, offer_value) -> float:
        return float(WORKED_EXAMPLE[name]) if prefill else float(offer_value)

    acquisition_default = default(
        "acquisition", offer.price.decimal if offer.price else Decimal(0)
    )

    # Once a keyed widget exists in session state, Streamlit ignores a changed
    # ``value`` argument on rerun. Varying the keys with the prefill state is what
    # makes the toggle actually reload the form with different defaults.
    variant = "wex" if prefill else (f"tpl-{template.id}" if template is not None else "own")
    form_id = f"{match_row['id']}_{variant}"

    with st.form(f"scenario_{form_id}"):
        columns = st.columns(3)
        acquisition = columns[0].number_input(
            "Acquisition price",
            min_value=0.0,
            value=acquisition_default,
            step=500.0,
            key=f"acq_{form_id}",
            help=f"Recorded evidence type: {offer.price_evidence_type}.",
        )
        basis = columns[1].selectbox(
            "Acquisition basis",
            ["net", "gross", "unknown"],
            index=["net", "gross", "unknown"].index(offer.price_basis),
            key=f"basis_{form_id}",
        )
        regime = columns[2].selectbox(
            "Acquisition VAT regime",
            ["standard", "margin", "other", "unknown"],
            index=["standard", "margin", "other", "unknown"].index(offer.vat_regime),
            key=f"regime_{form_id}",
        )

        st.markdown("Direct cost lines. Leave a box empty to record the cost as **unknown**, "
                    "which blocks completion. Tick *confirmed zero* when someone has checked "
                    "that it really is nothing.")
        cost_inputs: list[tuple[str, float | None, bool, str, bool]] = []
        # Either the manual's five lines, or the reviewed template's lines. A
        # template only supplies the starting values; each one is still editable,
        # and a line the template could not price arrives unknown.
        if template is not None:
            labels = [
                (
                    line.label,
                    _slug(line.label),
                    line.tax_treatment,
                    None if line.amount is None else float(line.amount.decimal),
                    line.is_known,
                    line.already_in_purchase_price,
                )
                for line in template.lines
            ]
        else:
            labels = [
                ("Transport", "transport", "non_recoverable", WORKED_EXAMPLE["transport"], True, False),
                ("Preparation", "preparation", "non_recoverable", WORKED_EXAMPLE["preparation"], True, False),
                ("Documents and handling", "documents", "non_recoverable", WORKED_EXAMPLE["documents"], True, False),
                ("Funding cost", "funding", "non_recoverable", WORKED_EXAMPLE["funding"], True, False),
                ("Risk allowance", "risk", "non_recoverable", WORKED_EXAMPLE["risk"], True, False),
            ]

        for label, key, treatment, suggested, starts_known, in_price in labels:
            row = st.columns([3, 2, 2, 2])
            row[0].markdown(label)
            known = row[1].checkbox(
                "known",
                value=bool(starts_known),
                key=f"k_{form_id}_{key}",
                label_visibility="collapsed",
            )
            if prefill:
                start = float(suggested or 0.0)
            elif template is not None:
                start = float(suggested or 0.0)
            else:
                start = 0.0
            amount = row[2].number_input(
                label,
                min_value=0.0,
                value=start,
                step=100.0,
                key=f"a_{form_id}_{key}",
                label_visibility="collapsed",
            )
            zero = row[3].checkbox(
                "confirmed zero", key=f"z_{form_id}_{key}", label_visibility="collapsed"
            )
            cost_inputs.append((label, amount if known else None, zero, treatment, in_price))

        vat_row = st.columns([3, 2, 2, 2])
        vat_row[0].markdown("Recoverable VAT paid up front")
        vat_known = vat_row[1].checkbox(
            "known", value=False, key=f"k_{form_id}_vat", label_visibility="collapsed"
        )
        vat_amount = vat_row[2].number_input(
            "Recoverable VAT",
            min_value=0.0,
            value=0.0,
            step=500.0,
            key=f"a_{form_id}_vat",
            label_visibility="collapsed",
        )
        vat_row[3].caption("cash, not final cost")

        st.markdown("Downstream picture, used to derive a **hypothetical** trade price:")
        downstream = st.columns(3)
        retail = downstream[0].number_input(
            "Assumed retail outcome",
            min_value=0.0,
            value=float(WORKED_EXAMPLE["retail"]) if prefill else 0.0,
            step=1000.0,
            key=f"retail_{form_id}",
        )
        dealer_cost = downstream[1].number_input(
            "Buying dealer's own downstream costs",
            min_value=0.0,
            value=float(WORKED_EXAMPLE["dealer_cost"]) if prefill else 0.0,
            step=500.0,
            key=f"dcost_{form_id}",
        )
        dealer_contribution = downstream[2].number_input(
            "Contribution that dealer requires",
            min_value=0.0,
            value=float(WORKED_EXAMPLE["dealer_contribution"]) if prefill else 0.0,
            step=500.0,
            key=f"dcontrib_{form_id}",
        )

        st.markdown("Or state the sale side directly:")
        sale_columns = st.columns(3)
        use_sale = sale_columns[0].checkbox(
            "Use a stated sale amount instead", key=f"usesale_{form_id}"
        )
        sale_amount = sale_columns[1].number_input(
            "Sale amount", min_value=0.0, value=0.0, step=1000.0, key=f"sale_{form_id}"
        )
        sale_evidence = sale_columns[2].selectbox(
            "What kind of evidence is that amount?",
            [
                PriceEvidenceType.DEALER_BID.value,
                PriceEvidenceType.BUYER_BUDGET.value,
                PriceEvidenceType.ANALYST_ASSUMPTION.value,
            ],
            key=f"saleev_{form_id}",
            help="A firm bid, a stated budget and an analyst assumption are not the same thing.",
        )

        downside = st.number_input(
            "Also show the downside if the retail outcome falls by",
            min_value=0.0,
            value=10000.0,
            step=1000.0,
            key=f"downside_{form_id}",
        )
        submitted = st.form_submit_button("Calculate scenario", icon=":material/calculate:")

    if not submitted:
        st.caption("The scenario is calculated only when you press the button.")
        return

    currency = offer.price.currency if offer.price else config.base_currency
    E = lambda value: Money.from_decimal(Decimal(str(value)), currency)

    costs = [
        CostLine(
            label=label,
            amount=None if amount is None else E(amount),
            confirmed_zero=zero,
            tax_treatment=treatment,
            already_in_purchase_price=in_price,
            paid_at=clock.now(),
        )
        for label, amount, zero, treatment, in_price in cost_inputs
    ]
    if vat_known:
        costs.append(
            CostLine(
                label="Recoverable VAT paid up front",
                amount=E(vat_amount),
                tax_treatment="recoverable",
                paid_at=clock.now(),
                refunded_at=None,
                notes="A cash outflow before refund, not a final cost.",
            )
        )

    median = st.session_state.get(f"comparable_median_{match_row['id']}")
    data = pricing.ScenarioInput(
        acquisition=E(acquisition),
        acquisition_basis=basis,
        acquisition_vat_regime=regime,
        acquisition_evidence_type=offer.price_evidence_type,
        sale=E(sale_amount) if use_sale else None,
        sale_basis=basis if use_sale else "unknown",
        sale_vat_regime=regime if use_sale else "unknown",
        sale_evidence_type=sale_evidence if use_sale else None,
        retail_assumption=E(retail) if retail else None,
        dealer_downstream_cost=E(dealer_cost) if retail else None,
        dealer_required_contribution=E(dealer_contribution) if retail else None,
        costs=costs,
        comparable_median=(
            Money.from_decimal(WORKED_EXAMPLE_MEDIAN, currency) if prefill else median
        ),
        currency=currency,
        market=offer.location_country,
    )
    result = pricing.compute_scenario(data)
    _render_result(result, data, downside, currency, offer, company, match_row, db, config)


WORKED_EXAMPLE_MEDIAN = Decimal("235000")


def _slug(label: str) -> str:
    """A stable widget-key fragment for a template line label."""
    return "".join(ch if ch.isalnum() else "_" for ch in label.lower())


def _render_result(result, data, downside, currency, offer, company, match_row, db, config) -> None:
    if not result.is_complete:
        st.error(
            "**Incomplete.** This is not a zero-cost profit; it is a calculation that cannot be "
            "finished. Missing: " + "; ".join(result.missing_inputs),
            icon=":material/report:",
        )
    else:
        st.success("Scenario complete on a compatible tax basis.", icon=":material/task_alt:")

    metrics = st.columns(4)
    metrics[0].metric(
        "Acquisition plus included costs",
        money(result.total_outlay),
        help="Acquisition price plus every non-recoverable direct cost not already inside it.",
    )
    metrics[1].metric(
        "Observed asking-price gap",
        money(result.asking_gap),
        help="Comparable median minus the acquisition price. An investigation signal, not profit.",
    )
    metrics[2].metric(
        "Hypothetical trade price",
        money(result.hypothetical_trade_price),
        help="What that dealer could pay and still meet its own stated requirements. Not a bid.",
    )
    metrics[3].metric(
        "Scenario contribution",
        money(result.contribution),
        help="Before unmodelled overhead, referral fees and anything not listed.",
    )

    if result.contribution is not None and result.contribution.is_negative:
        st.error(
            f"On these inputs the contribution is **{result.contribution.format()}** - a loss.",
            icon=":material/trending_down:",
        )

    st.markdown(f"Sale evidence: **{result.sale_evidence_label()}**.")

    cash = st.columns(3)
    cash[0].metric("Cash out before collections", money(result.cash_outflow))
    cash[1].metric("Of which recoverable tax", money(result.recoverable_tax))
    cash[2].metric("Cash schedule", result.cash_schedule_status)
    if result.cash_schedule_status != "complete":
        st.caption(
            "The cash schedule is incomplete: recoverable tax can tie money up without being a "
            "final cost, and that cannot be scheduled without payment and refund dates."
        )

    if downside and data.retail_assumption is not None:
        shifted = pricing.sensitivity(data, [Money.from_decimal(Decimal(f"-{downside}"), currency)])
        for delta, downside_result in shifted:
            st.warning(
                f"**Downside:** if the retail outcome falls by {abs(delta.decimal):,.0f} "
                f"{currency} and the dealer keeps the same cost and profit requirements, the "
                f"hypothetical trade price becomes "
                f"{money(downside_result.hypothetical_trade_price)} and the contribution becomes "
                f"**{money(downside_result.contribution)}**.",
                icon=":material/trending_down:",
            )

    with st.expander("Assumptions and what is excluded", expanded=True):
        for item in result.assumptions:
            st.markdown(f"- {item}")
        for item in result.notes:
            st.markdown(f"- {item}")
        st.markdown(
            "- Expected commission is blank because no approved compensation rule has been "
            "entered. No prototype income forecast is produced."
        )
        st.markdown(
            "- A risk allowance is a provision. It is not evidence that the risks have been "
            "covered."
        )

    if st.button("Save this scenario", key=f"save_{match_row['id']}", icon=":material/save:"):
        clock = get_clock()
        workflow = Workflow(db, config, clock)
        with db.write() as conn:
            scenario_id = workflow.save_scenario(
                conn,
                offer=offer,
                data=data,
                result=result,
                match_id=match_row["id"],
                buyer_company_id=company.id,
            )
        st.success(
            f"Saved as `{scenario_id}`. Recompute to let the reviewed economics count towards "
            f"the match score."
        )


def _prospects_tab(db, config) -> None:
    st.caption(
        "These companies carry similar stock or sit in a relevant category, but none of them has "
        "a recorded buying requirement. They can be researched or qualified. They are not "
        "confirmed buyers and they cannot enter a contact-ready queue as buyers."
    )
    with db.open() as conn:
        rows = analysis_repo.list_matches(
            conn, fit_statuses=(FitStatus.CATEGORY_FIT_PROSPECT.value,)
        )
        companies = {
            row["company_id"]: companies_repo.get_company(conn, row["company_id"]) for row in rows
        }
        offers = {row["offer_id"]: supply_repo.get_offer(conn, row["offer_id"]) for row in rows}
        vehicles = {
            offer_id: (
                supply_repo.get_vehicle(conn, offer.vehicle_id)
                if offer and offer.vehicle_id
                else None
            )
            for offer_id, offer in offers.items()
        }

    if not rows:
        empty_state("No category-fit prospects.")
        return

    st.dataframe(
        [
            {
                "company": companies[row["company_id"]].display_name,
                "country": companies[row["company_id"]].country,
                "category": companies[row["company_id"]].category,
                "model family in stock": (
                    vehicles[row["offer_id"]].model_family if vehicles[row["offer_id"]] else ""
                ),
                "buying brief": "none recorded",
                "status": "research prospect only",
            }
            for row in rows
        ],
        width="stretch",
        hide_index=True,
    )
