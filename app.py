"""Opportunity Desk - cars worth a second look, and the buyers who asked for them.

Run it locally with:

    .\\.venv\\Scripts\\python.exe -m streamlit run app.py --server.address 127.0.0.1

A hosted copy sets ``DESK_PUBLIC=1``, which restricts it to demo mode on sample
stock and refuses to start otherwise. That guard is the reason hosting is safe
here: not that the app became secure, but that the hosted instance cannot hold
real data. There is still no authentication and no access control, so a live
database and real customer records stay on a local machine.
"""

from __future__ import annotations

import streamlit as st

from desk.services import deployment
from desk.ui import (
    businesses,
    callprep,
    capture,
    matches,
    opportunities,
    overview,
    sources,
    today,
    vehicles,
)
from desk.ui.components import get_clock, get_config, get_db

st.set_page_config(
    page_title="Opportunity Desk",
    page_icon=":material/directions_car:",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = {
    "Overview": overview.render,
    "Opportunities": opportunities.render,
    "Today": today.render,
    "Vehicles": vehicles.render,
    "Businesses": businesses.render,
    "Capture a requirement": capture.render,
    "Matches": matches.render,
    "Call preparation": callprep.render,
    "Sources and imports": sources.render,
}


def main() -> None:
    config = get_config()

    # Before a single screen renders. A public instance pointed at live data is a
    # configuration mistake, and it fails loudly rather than quietly degrading.
    try:
        posture = deployment.guard(config)
    except deployment.DeploymentRefused as error:
        st.error(str(error), icon=":material/gpp_bad:")
        st.stop()
        return

    # A freshly deployed copy has no database yet. Seeding it here, once, is the
    # difference between a working link and an empty screen with no explanation.
    seeded = deployment.ensure_demo_data(get_db(), config, get_clock())

    with st.sidebar:
        st.markdown("### Opportunity Desk")
        st.caption(
            "Cars worth a second look, the buyers who asked for them, and what is still "
            "unknown. It prepares conversations; it does not contact anyone."
        )
        if config.is_demo:
            st.warning("SAMPLE STOCK", icon=":material/science:")
        else:
            st.info("LIVE MODE", icon=":material/database:")

        choice = st.radio("Screen", list(PAGES), label_visibility="collapsed")
        if seeded:
            st.caption("Sample stock was loaded on first run.")

        st.divider()
        st.caption(f"{posture.label}.")
        st.caption(f"Mode: `{config.mode.value}` · database `{config.db_path.name}`")
        st.caption(f"Times shown in {config.timezone}; stored in UTC.")
        st.caption(
            "Live connectors: disabled until a source register entry is approved. "
            f"Runtime AI: {'enabled' if config.ai_enabled else 'disabled'}. "
            "Outbound messaging: not implemented."
        )

    PAGES[choice]()


main()
