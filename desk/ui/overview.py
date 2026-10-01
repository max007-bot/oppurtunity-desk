"""The landing screen: what this is, in plain English, before anything else.

Someone opening a link has about fifteen seconds of patience. This screen spends
them saying what the tool does, what the data is, and what is not connected —
because finding that out later, from a caption, feels like something was hidden.
"""

from __future__ import annotations

import streamlit as st

from ..connectors import mobile_de
from ..services import deployment
from ..services.workflow import dashboard_counts
from .components import get_clock, get_config, get_db


def render() -> None:
    config = get_config()
    clock = get_clock()
    db = get_db()
    posture = deployment.describe(config)

    st.markdown("## Opportunity Desk")
    st.caption(
        "Cars worth a second look, the buyers who asked for them, and an honest account "
        "of what is still unknown."
    )

    st.markdown(
        """
**It does three things.**

1. **Finds cars priced below genuinely comparable ones** — same generation, same
   powertrain, same steering side, same VAT basis — and shows every car it threw out
   of the comparison, with the reason.
2. **Works out what is actually left** after transport, VAT, destination registration
   tax and the money tied up. If a figure it needs is missing, it says **Incomplete**
   and names the missing fact instead of guessing one.
3. **Matches each car to a buyer who asked for that specification**, and drafts the
   message — to the seller to buy it, or to the buyer to sell it. A person sends it.
        """
    )

    columns = st.columns(2)
    with columns[0]:
        st.info(
            f"**{posture.label}.** {posture.caveat}",
            icon=":material/public:",
        )
    with columns[1]:
        st.warning(
            f"**mobile.de:** {mobile_de.status_label()}. The adapter targets the official "
            f"API under a partner agreement. There is no scraping in this project, and no "
            f"live request has ever been made from it.",
            icon=":material/api:",
        )

    st.caption(
        "Stock shown here is representative sample data, labelled on every card. It is not "
        "anyone's real inventory, and no price on it is a real quotation."
    )

    with db.open() as conn:
        counts = dashboard_counts(conn, config=config, now=clock.now())

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Cars on file", counts.get("offers", 0))
    metrics[1].metric("Recorded requirements", counts.get("confirmed_briefs", 0))
    metrics[2].metric("Specification fits", counts.get("specification_fits", 0))
    metrics[3].metric("Open checks", counts.get("open_tasks", 0))

    st.divider()
    left, right = st.columns([2, 1])
    with left:
        st.markdown("**What it will not do**")
        st.markdown(
            "- Send anything. There is no dialler, no mail transport, no credentials.\n"
            "- Invent a price, a cost or a buyer's intent.\n"
            "- Treat a failed availability check as a sale.\n"
            "- Turn a found phone number into permission to use it.\n"
            "- Produce a figure from a model you cannot audit — every number here comes "
            "from a rule you can read."
        )
    with right:
        st.markdown("**Start here**")
        st.markdown(
            "Open **Opportunities** in the sidebar. The top card is the one with the most "
            "known about it, not the one with the biggest headline number."
        )
