"""Shared UI helpers.

Nothing here performs a fetch, an import or an AI call. A Streamlit rerun happens
on every widget interaction, so side effects only ever sit behind an explicit
button press in a page module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import streamlit as st

from ..clock import Clock, SystemClock, from_iso, is_stale
from ..config import Config, load_config
from ..db import Database
from ..money import Money

FIT_LABELS = {
    "specification_fit": "Specification fit",
    "needs_verification": "Needs verification",
    "no_match": "No match",
    "category_fit_prospect": "Category-fit prospect",
    "brief_expired": "Brief expired",
}

FIT_HELP = {
    "specification_fit": (
        "Every mandatory fact matches. It does not prove the buyer will buy or that the "
        "economics work."
    ),
    "needs_verification": "A required fact is missing. It has not passed.",
    "no_match": "A known conflict. The reason is shown.",
    "category_fit_prospect": (
        "Carries similar stock but has no recorded buying requirement. Research only."
    ),
    "brief_expired": "The requirement has expired and needs reconfirmation.",
}


def get_config() -> Config:
    """Configuration for this session.

    Cached per session rather than per process, so the mode cannot be changed out
    from under a rerun.
    """
    if "config" not in st.session_state:
        try:
            from dotenv import load_dotenv

            load_dotenv(override=False)
        except ImportError:
            pass
        st.session_state["config"] = load_config()
    return st.session_state["config"]


def get_clock() -> Clock:
    return SystemClock()


def get_db() -> Database:
    """A fresh handle each rerun; connections themselves are short-lived."""
    return Database(get_config(), get_clock())


def local(value: datetime | str | None, config: Config | None = None) -> str:
    """Render a stored UTC timestamp in the user's display time zone."""
    config = config or get_config()
    parsed = from_iso(value) if isinstance(value, str) else value
    if parsed is None:
        return "unknown"
    try:
        zone = ZoneInfo(config.timezone)
    except Exception:
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    return parsed.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def day(value: datetime | str | None) -> str:
    parsed = from_iso(value) if isinstance(value, str) else value
    return "unknown" if parsed is None else parsed.date().isoformat()


def money(value: Money | None, *, unknown: str = "unknown") -> str:
    return unknown if value is None else value.format()


def mode_banner(config: Config) -> None:
    """The persistent data-class label, on every screen."""
    if config.is_demo:
        st.warning(
            "**Demo data.** Every company, person, price and requirement on this screen is "
            "synthetic. Demo contacts are not prospects and a replay is not a live market event.",
            icon=":material/science:",
        )
    else:
        st.info(
            "**Live mode.** Only sources with a current approval record can be fetched.",
            icon=":material/database:",
        )


def coverage_notice() -> None:
    """The standing limitation, shown rather than papered over."""
    st.caption(
        "No authorised vehicle-price feed is connected. This desk analyses imported "
        "observations and replayed snapshots. It does not have continuous live coverage of the "
        "European market, and it does not fill gaps with estimated prices."
    )


def fit_badge(status: str) -> str:
    return FIT_LABELS.get(status, status)


def status_chip(label: str, tone: str = "grey") -> str:
    return f":{tone}-badge[{label}]"


def freshness_chip(
    timestamp: datetime | None, *, hours: float = 0, days: float = 0, now: datetime
) -> str:
    if timestamp is None:
        return ":orange-badge[never confirmed]"
    if is_stale(now, timestamp, hours=hours, days=days):
        return f":orange-badge[stale since {day(timestamp)}]"
    return f":green-badge[confirmed {day(timestamp)}]"


def stock_badge(stock_kind: str, status: str) -> str:
    """Physical stock, an allocation and an unknown location are different facts."""
    if stock_kind == "physical_stock":
        return ":green-badge[physical stock]"
    if stock_kind == "allocation" or status == "allocation":
        return ":violet-badge[allocation or build slot]"
    return ":orange-badge[location unknown]"


def observation_badge(state: str) -> str:
    if state == "not_seen":
        return ":orange-badge[not observed in the latest successful check]"
    if state == "fetch_failed":
        return ":red-badge[last check failed]"
    return ":green-badge[observed]"


def rule_list(items: Iterable[str], *, icon: str) -> None:
    for item in items:
        st.markdown(f"{icon} {item}")


def empty_state(message: str) -> None:
    """Show zero accurately. No placeholder activity is ever generated."""
    st.info(message, icon=":material/inbox:")


def kpi_row(items: list[tuple[str, Any, str | None]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value, help_text) in zip(columns, items):
        with column:
            st.metric(label, value, help=help_text)


def download_text(label: str, body: str, filename: str, *, key: str) -> None:
    st.download_button(
        label,
        data=body.encode("utf-8"),
        file_name=filename,
        mime="text/plain",
        key=key,
        icon=":material/download:",
    )


def copyable(body: str, *, language: str | None = None) -> None:
    """Show text the user can copy.

    There is deliberately no Send and no Auto-dial anywhere in this application.
    """
    st.code(body, language=language)


def json_table(rows: list[dict[str, Any]], *, empty: str) -> None:
    if not rows:
        empty_state(empty)
        return
    st.dataframe(rows, width="stretch", hide_index=True)
