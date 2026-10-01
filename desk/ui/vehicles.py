"""The Vehicles screen: offers, their observation history and their contradictions."""

from __future__ import annotations

import streamlit as st

from ..config import PriceEvidenceType
from ..repositories import supply as supply_repo
from ..services import corrections
from ..services.normalization import new_vehicle_indicator
from .components import (
    coverage_notice,
    day,
    empty_state,
    freshness_chip,
    get_clock,
    get_config,
    get_db,
    local,
    mode_banner,
    money,
    observation_badge,
    stock_badge,
)


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()
    now = clock.now()

    st.title("Vehicles")
    mode_banner(config)

    notice = st.session_state.pop("correction_notice", None)
    if notice is not None:
        st.success(notice["summary"], icon=":material/task_alt:")
        if notice["accepted"]:
            st.info(
                "Matching still shows the previous result until you recompute, on the Today "
                "screen or with `desk recompute`.",
                icon=":material/calculate:",
            )

    coverage_notice()

    if not db.exists():
        st.error("No database yet. Run the setup commands in the README.")
        return

    with db.open() as conn:
        offers = supply_repo.list_offers(conn)
        vehicles = {
            offer.id: (
                supply_repo.get_vehicle(conn, offer.vehicle_id) if offer.vehicle_id else None
            )
            for offer in offers
        }

    families = sorted({v.model_family for v in vehicles.values() if v})
    countries = sorted({o.location_country for o in offers if o.location_country})

    with st.expander("Filters", expanded=True):
        columns = st.columns(5)
        family = columns[0].selectbox("Model family", ["all", *families])
        country = columns[1].selectbox("Location", ["all", *countries])
        basis = columns[2].selectbox("Price basis", ["all", "net", "gross", "unknown"])
        evidence = columns[3].selectbox(
            "Price evidence",
            ["all", *[kind.value for kind in PriceEvidenceType]],
            help=(
                "A supply asking price, a retail asking price, a dealer bid and a completed "
                "transaction are different kinds of evidence and are never blended."
            ),
        )
        freshness = columns[4].selectbox(
            "Evidence freshness", ["all", "confirmed within window", "stale or never confirmed"]
        )

    selected = []
    for offer in offers:
        vehicle = vehicles[offer.id]
        if family != "all" and (vehicle is None or vehicle.model_family != family):
            continue
        if country != "all" and offer.location_country != country:
            continue
        if basis != "all" and offer.price_basis != basis:
            continue
        if evidence != "all" and offer.price_evidence_type != evidence:
            continue
        stale = offer.availability_confirmed_at is None or (
            (now - offer.availability_confirmed_at).total_seconds()
            > config.freshness.supply_hours * 3600
        )
        if freshness == "confirmed within window" and stale:
            continue
        if freshness == "stale or never confirmed" and not stale:
            continue
        selected.append(offer)

    st.caption(f"{len(selected)} of {len(offers)} offer(s) match these filters.")
    if not selected:
        empty_state("No offer matches these filters.")
        return

    for offer in selected:
        _offer_card(offer, vehicles[offer.id], now, config, db)


def _offer_card(offer, vehicle, now, config, db) -> None:
    label = vehicle.model_family if vehicle else "identity under review"
    variant = f" {vehicle.variant}" if vehicle and vehicle.variant else ""
    header = (
        f"{label}{variant} · {money(offer.price)} "
        f"({offer.price_basis}/{offer.vat_regime}) · {offer.seller_name or 'seller unknown'}"
    )
    with st.expander(header):
        st.markdown(
            " ".join(
                [
                    stock_badge(offer.stock_kind, offer.status),
                    observation_badge(offer.last_observation_state),
                    freshness_chip(
                        offer.availability_confirmed_at,
                        hours=config.freshness.supply_hours,
                        now=now,
                    ),
                    f":grey-badge[{offer.price_evidence_type}]",
                ]
            )
        )

        columns = st.columns(3)
        with columns[0]:
            st.markdown("**Offer**")
            st.markdown(
                f"- Source record: `{offer.external_record_id}`\n"
                f"- Seller status: {offer.status}\n"
                f"- Authority to sell: {offer.authority_to_sell}\n"
                f"- Location: {offer.location_city or 'unknown'}, "
                f"{offer.location_country or 'unknown'}\n"
                f"- Available from: {day(offer.available_from)}\n"
                f"- Offer valid until: {day(offer.valid_until)}"
            )
            if offer.listing_url:
                st.markdown(f"- Source link: {offer.listing_url}")
            if offer.raw_price_text:
                st.caption(f"Raw price text preserved from the source: {offer.raw_price_text!r}")

        with columns[1]:
            st.markdown("**Reviewed vehicle facts**")
            if vehicle is None:
                st.markdown("No reviewed vehicle identity is attached yet.")
            else:
                st.markdown(
                    f"- Identity review: `{vehicle.identity_review_status}`\n"
                    f"- VIN: {vehicle.vin or 'not known'}"
                    f"{' (verified)' if vehicle.vin_verified else ''}\n"
                    f"- Generation: {vehicle.generation or 'unknown'}\n"
                    f"- First registration: {day(vehicle.first_registration)}\n"
                    f"- Mileage: "
                    f"{f'{vehicle.mileage_km:,} km' if vehicle.mileage_km is not None else 'unknown'}\n"
                    f"- Powertrain: {vehicle.powertrain or 'unknown'}\n"
                    f"- Steering: {vehicle.steering or 'unknown'}\n"
                    f"- Homologated seats: {vehicle.seats if vehicle.seats is not None else 'unknown'}"
                )
                _new_vehicle_note(vehicle, now)
                if vehicle.notes:
                    st.warning(vehicle.notes, icon=":material/rule:")

        with columns[2]:
            st.markdown("**Reviewed specification**")
            if vehicle is None or not vehicle.specification:
                st.markdown("Nothing recorded. Absent options are unknown, not absent.")
            else:
                for key, value in sorted(vehicle.specification.items()):
                    mark = "yes" if value in (True, "true", "yes", 1) else str(value)
                    st.markdown(f"- {key}: **{mark}**")
                st.caption(
                    "An option that does not appear here is unknown and must be confirmed with "
                    "the seller, never assumed fitted."
                )

        if offer.vehicle_id:
            with db.open() as conn:
                siblings = [
                    other
                    for other in supply_repo.offers_for_vehicle(conn, offer.vehicle_id)
                    if other.id != offer.id
                ]
                history = supply_repo.observations_for_offer(conn, offer.id, limit=12)

            if siblings:
                st.info(
                    f"This vehicle identity also appears on {len(siblings)} other offer(s): "
                    + ", ".join(
                        f"`{s.external_record_id}` ({s.seller_name or 'seller unknown'}, "
                        f"{money(s.price)})"
                        for s in siblings
                    )
                    + ". One car, separate offers: it is not counted twice as independent "
                    "inventory.",
                    icon=":material/link:",
                )

            _contradictions(conn_db=db, vehicle=vehicle, offer=offer, config=config)

            st.markdown("**Observation history** (appended, never overwritten)")
            st.dataframe(
                [
                    {
                        "stated by source": local(o.observed_at, config),
                        "checked": local(o.fetched_at, config),
                        "state": o.state,
                        "price": money(o.price, unknown=""),
                        "basis": f"{o.price_basis}/{o.vat_regime}",
                        "seller status": o.status or "",
                        "stock": o.stock_kind or "",
                        "seats": o.seats if o.seats is not None else "",
                        "version": o.version,
                    }
                    for o in history
                ],
                width="stretch",
                hide_index=True,
            )


def _new_vehicle_note(vehicle, now) -> None:
    """Show the EU new-means-of-transport test on the vehicle itself.

    For a business that moves nearly-new cars across borders this is not a
    footnote in a calculation, it is the basis on which the deal is written, so
    it belongs next to the mileage and the registration date that decide it.
    """
    indicator = new_vehicle_indicator(
        first_registration=vehicle.first_registration,
        mileage_km=vehicle.mileage_km,
        at=now,
    )
    detail = "; ".join(indicator.reasons)
    if indicator.indicated_new is True:
        st.success(
            f"EU new means of transport: **yes** — {detail}. The buyer accounts for "
            f"acquisition VAT in the destination country.",
            icon=":material/local_shipping:",
        )
    elif indicator.indicated_new is False:
        st.info(f"EU new means of transport: **no** — {detail}.", icon=":material/local_shipping:")
    else:
        st.warning(
            f"EU new means of transport: **cannot be determined** — {detail}. "
            f"This has to be settled before a cross-border basis is quoted.",
            icon=":material/help:",
        )


def _contradictions(*, conn_db, vehicle, offer, config) -> None:
    """Show where the newest observation disagrees with the reviewed record.

    A connector never overwrites a reviewed fact, so the disagreement waits for a
    person. Both outcomes are offered and both are recorded: accept the source's
    value, or deliberately keep the reviewed one.
    """
    if vehicle is None:
        return

    with conn_db.open() as conn:
        issues = corrections.find_contradictions(conn, vehicle, offer.id)
        decided = corrections.history(conn, vehicle.id)

    if decided:
        with st.expander(f"Resolved fact reviews ({len(decided)})"):
            for row in decided:
                verb = (
                    "accepted the source's"
                    if row["decision"] == "accepted_source"
                    else "kept the reviewed"
                )
                st.markdown(
                    f"- **{row['field_name']}**: {row['decided_by']} {verb} value on "
                    f"{day(row['decided_at'])} "
                    f"({row['previous_value']} versus {row['observed_value']}) - "
                    f"{row['note']}"
                )

    if not issues:
        return

    for issue in issues:
        st.error(
            f"**Contradiction between the source and the reviewed record.** "
            f"{issue.describe()}. The import did not overwrite the reviewed fact, so matching "
            f"still uses {issue.reviewed_value}. {issue.consequence().capitalize()}.",
            icon=":material/warning:",
        )
        _resolution_form(conn_db, vehicle, issue, config)


def _resolution_form(conn_db, vehicle, issue, config) -> None:
    """The two decisions, each requiring a named person and a dated note."""
    form_key = f"corr_{vehicle.id}_{issue.field}_{issue.observed_value}"
    with st.form(form_key):
        st.markdown(
            f"Resolve **{issue.label}**: the source says `{issue.observed_value}`, "
            f"the reviewed record says `{issue.reviewed_value}`."
        )
        columns = st.columns([2, 3])
        decision = columns[0].radio(
            "Decision",
            ["accepted_source", "kept_reviewed"],
            format_func=lambda value: (
                f"Accept the source: set {issue.label} to {issue.observed_value}"
                if value == "accepted_source"
                else f"Keep the reviewed {issue.label} of {issue.reviewed_value}"
            ),
            key=f"{form_key}_decision",
        )
        decided_by = columns[1].text_input("Decided by", value="Max", key=f"{form_key}_who")
        note = columns[1].text_input(
            "Dated note",
            placeholder="how this was confirmed, for example: seller confirmed by phone",
            key=f"{form_key}_note",
        )
        submitted = st.form_submit_button("Record this decision", icon=":material/gavel:")

    if not submitted:
        return
    clock = get_clock()
    try:
        with conn_db.write() as conn:
            outcome = corrections.resolve(
                conn,
                vehicle_id=vehicle.id,
                contradiction=issue,
                decision=decision,
                decided_by=decided_by,
                note=note,
                now=clock.now(),
                is_demo=config.is_demo,
            )
    except ValueError as exc:
        st.error(str(exc), icon=":material/error:")
        return

    # The rerun below would wipe a message rendered now, so it is carried across
    # and shown at the top of the next pass instead.
    st.session_state["correction_notice"] = {
        "summary": outcome.summary(),
        "accepted": outcome.accepted,
    }
    st.rerun()
