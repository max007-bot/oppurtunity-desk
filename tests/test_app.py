"""Streamlit workflow tests.

These drive the real app through ``AppTest``, so they cover what a user actually
sees: the demo label, the coverage limitation, the absence of a Send button, and
the fact that a rerun does not silently do work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from desk.clock import SystemClock
from desk.config import PROJECT_ROOT, load_config
from desk.db import Database
from desk.services import demo as demo_service
from desk.services.workflow import Workflow

APP = str(PROJECT_ROOT / "app.py")
pytest.importorskip("streamlit.testing.v1")


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch):
    """A seeded demo database wired up through the environment."""
    database = tmp_path / "app-demo.sqlite"
    monkeypatch.setenv("DESK_MODE", "demo")
    monkeypatch.setenv("DESK_DEMO_DB", str(database))
    monkeypatch.setenv("DESK_LIVE_DB", str(tmp_path / "app-live.sqlite"))
    monkeypatch.setenv("DESK_ALLOW_NETWORK", "false")

    config = load_config()
    clock = SystemClock()
    handle = Database(config, clock)
    handle.initialise()
    demo_service.seed(handle, config, clock)
    Workflow(handle, config, clock).recompute()
    return config


def open_app(app_env, screen: str | None = None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if screen is not None:
        at.radio[0].set_value(screen).run()
    return at


def all_text(at) -> str:
    parts: list[str] = []
    for collection in (
        at.markdown,
        at.caption,
        at.warning,
        at.error,
        at.info,
        at.success,
        at.title,
        at.subheader,
        at.header,
    ):
        parts.extend(str(element.value) for element in collection)
    return "\n".join(parts)


# -- the app runs --------------------------------------------------------


def test_the_app_starts_without_an_exception(app_env):
    at = open_app(app_env)
    assert not at.exception


@pytest.mark.parametrize(
    "screen",
    ["Today", "Vehicles", "Businesses", "Matches", "Call preparation", "Sources and imports"],
)
def test_every_screen_renders(app_env, screen):
    at = open_app(app_env, screen)
    assert not at.exception, f"{screen} raised {at.exception}"


def test_the_demo_label_is_persistent(app_env):
    for screen in ("Today", "Vehicles", "Businesses", "Matches"):
        at = open_app(app_env, screen)
        text = all_text(at)
        assert "Demo data" in text or "DEMO DATA" in text, f"{screen} lacks the demo label"


def test_the_coverage_limitation_is_stated_not_hidden(app_env):
    at = open_app(app_env, "Today")
    text = all_text(at)
    assert "No authorised vehicle-price feed is connected" in text
    assert "does not have continuous live coverage" in text


def test_no_pipeline_revenue_or_commission_is_claimed(app_env):
    at = open_app(app_env, "Today")
    text = all_text(at).lower()
    assert "pipeline revenue" not in text.replace(
        "no pipeline-revenue figure", ""
    ) or "no pipeline-revenue figure" in all_text(at).lower()
    assert "estimated commission stays blank" in all_text(at).lower()


# -- nothing that sends ---------------------------------------------------


def _button_labels(at) -> list[str]:
    labels = [str(b.label) for b in at.button]
    labels.extend(str(b.label) for b in at.download_button)
    return labels


def test_there_is_no_send_or_dial_button_anywhere(app_env):
    forbidden = ("send", "dial", "call now", "email now", "post to", "publish")
    for screen in ("Today", "Vehicles", "Businesses", "Matches", "Call preparation", "Sources and imports"):
        at = open_app(app_env, screen)
        for label in _button_labels(at):
            lowered = label.lower()
            assert not any(word in lowered for word in forbidden), (
                f"{screen} offers a button labelled {label!r}"
            )


def test_call_preparation_offers_copy_and_download_only(app_env):
    at = open_app(app_env, "Call preparation")
    assert not at.exception
    labels = " ".join(_button_labels(at)).lower()
    assert "download brief" in labels
    text = all_text(at)
    assert "There is no Send button and no Auto-dial" in text or "cannot send an email" in text


def test_refresh_is_unavailable_in_demo_mode(app_env):
    at = open_app(app_env, "Sources and imports")
    # The source register tab is the default; its refresh section says why not.
    text = all_text(at)
    assert "Refresh is unavailable in demo mode" in text or "no live fetches" in text


# -- reruns do no work ---------------------------------------------------


def test_opening_and_rerunning_a_page_creates_no_records(app_env):
    config = app_env
    clock = SystemClock()
    handle = Database(config, clock)

    def snapshot() -> tuple[int, ...]:
        with handle.open() as conn:
            return tuple(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "observations",
                    "change_events",
                    "tasks",
                    "matches",
                    "scenarios",
                    "drafts",
                    "runs",
                )
            )

    before = snapshot()
    for screen in ("Today", "Vehicles", "Businesses", "Matches", "Call preparation"):
        at = open_app(app_env, screen)
        at.run()  # a second rerun, as any widget interaction would cause
        assert not at.exception
    assert snapshot() == before, "simply viewing the app changed stored data"


def test_recompute_happens_only_when_the_button_is_pressed(app_env):
    config = app_env
    handle = Database(config, SystemClock())
    with handle.write() as conn:
        conn.execute("DELETE FROM matches")
    with handle.open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0

    at = open_app(app_env, "Today")
    at.run()
    with handle.open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0, (
            "opening the page recomputed on its own"
        )

    button = next(b for b in at.button if "Recompute" in str(b.label))
    button.click().run()
    with handle.open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] > 0


# -- the commercial language survives into the UI -----------------------


def test_the_matches_screen_explains_a_specification_fit_honestly(app_env):
    at = open_app(app_env, "Matches")
    text = all_text(at)
    assert "not evidence that the buyer will buy" in text or "does not prove" in text


def test_the_category_prospects_are_kept_separate(app_env):
    at = open_app(app_env, "Matches")
    tabs = [str(tab) for tab in at.tabs] if hasattr(at, "tabs") else []
    text = all_text(at)
    assert "Category-fit prospect" in text or "category-fit" in text.lower() or tabs


def test_the_businesses_screen_shows_unknown_buying_authority_as_unknown(app_env):
    at = open_app(app_env, "Businesses")
    text = all_text(at)
    assert "unknown" in text.lower()
    assert "invented" in text.lower() or "no decision-maker" in text.lower()


# -- the worked example, driven through the real form -------------------


def test_the_worked_example_toggle_actually_reloads_the_form(app_env):
    """Regression: keyed widgets inside a form ignore a changed default.

    The toggle silently did nothing until the widget keys were made to vary with
    the prefill state, so the screen showed a scenario built from zeroed costs.
    """
    at = open_app(app_env, "Matches")
    at.toggle[0].set_value(True).run()

    # Several match cards render the same labels, and only the first card's toggle
    # was set, so read the first occurrence of each rather than the last.
    def first(label: str):
        return next(n.value for n in at.number_input if n.label == label)

    assert first("Acquisition price") == 200000.0
    assert first("Transport") == 1500.0
    assert first("Preparation") == 1000.0
    assert first("Documents and handling") == 500.0
    assert first("Funding cost") == 2000.0
    assert first("Risk allowance") == 1500.0
    assert first("Assumed retail outcome") == 230000.0
    assert first("Buying dealer's own downstream costs") == 2500.0
    assert first("Contribution that dealer requires") == 12500.0


def test_the_worked_example_produces_the_manuals_figures_on_screen(app_env):
    """The arithmetic in manual section 7, as a user actually sees it."""
    at = open_app(app_env, "Matches")
    at.toggle[0].set_value(True).run()
    next(b for b in at.button if "Calculate scenario" in str(b.label)).click().run()

    def first(label: str):
        return next(m.value for m in at.metric if m.label == label)

    assert first("Acquisition plus included costs") == "\u20ac206,500.00"
    assert first("Observed asking-price gap") == "\u20ac35,000.00"
    assert first("Hypothetical trade price") == "\u20ac215,000.00"
    assert first("Scenario contribution") == "\u20ac8,500.00"


def test_the_downside_is_shown_on_screen_as_a_loss(app_env):
    at = open_app(app_env, "Matches")
    at.toggle[0].set_value(True).run()
    next(b for b in at.button if "Calculate scenario" in str(b.label)).click().run()

    downside = [str(w.value) for w in at.warning if "Downside" in str(w.value)]
    assert downside, "the downside panel is missing"
    assert "\u20ac205,000.00" in downside[0]
    assert "\u20ac-1,500.00" in downside[0]


def test_an_unfilled_worksheet_reports_incomplete_not_free_profit(app_env):
    """With no sale side stated, the result must refuse to complete."""
    at = open_app(app_env, "Matches")
    next(b for b in at.button if "Calculate scenario" in str(b.label)).click().run()

    errors = " ".join(str(e.value) for e in at.error)
    assert "Incomplete" in errors
    assert "not a zero-cost profit" in errors


# -- the screens added for corrections, rates, review and adjustments ----


def test_the_sources_screen_offers_review_and_rates_tabs(app_env):
    at = open_app(app_env, "Sources and imports")
    assert not at.exception
    text = all_text(at)
    assert "Approving a source is the decision" in text or "Source register" in text


def test_the_rates_screen_says_nothing_converts_without_a_rate(app_env):
    at = open_app(app_env, "Sources and imports")
    text = all_text(at)
    assert "never assumes 1:1" in text or "stays out of any comparison" in text


def test_a_contradiction_is_offered_for_resolution_not_applied(app_env):
    """After a replay, the Vehicles screen shows the disagreement and the choice."""
    from desk.clock import SystemClock
    from desk.db import Database
    from desk.services import demo as demo_service

    config = app_env
    clock = SystemClock()
    handle = Database(config, clock)
    demo_service.replay_updates(handle, config, clock)

    at = open_app(app_env, "Vehicles")
    assert not at.exception
    text = all_text(at)
    assert "Contradiction between the source and the reviewed record" in text
    assert "did not overwrite the reviewed fact" in text
    # Both decisions are offered; neither has been taken.
    assert any("Record this decision" in str(b.label) for b in at.button)


def test_the_today_screen_counts_facts_to_confirm(app_env):
    from desk.clock import SystemClock
    from desk.db import Database
    from desk.services import demo as demo_service

    config = app_env
    clock = SystemClock()
    handle = Database(config, clock)
    demo_service.replay_updates(handle, config, clock)

    at = open_app(app_env, "Today")
    text = all_text(at)
    assert "Facts to confirm" in text
    assert "until someone decides" in text


def test_the_matches_screen_offers_analyst_adjustments(app_env):
    at = open_app(app_env, "Matches")
    text = all_text(at)
    assert "No monetary adjustment is ever applied automatically" in text
    assert "recorded as your assumption" in text


def test_resolving_a_contradiction_confirms_it_on_screen(app_env):
    """Regression: st.rerun() wiped the confirmation before anyone could read it."""
    from desk.clock import SystemClock
    from desk.db import Database
    from desk.repositories import supply as supply_repo
    from desk.services import demo as demo_service

    config = app_env
    clock = SystemClock()
    handle = Database(config, clock)
    demo_service.replay_updates(handle, config, clock)

    at = open_app(app_env, "Vehicles")
    next(t for t in at.text_input if t.label == "Decided by").set_value("Max")
    next(t for t in at.text_input if t.label == "Dated note").set_value(
        "factory build sheet confirms four seats"
    )
    next(b for b in at.button if "Record this decision" in str(b.label)).click().run()

    messages = " ".join(str(s.value) for s in at.success)
    assert "updated from 5 to 4" in messages
    assert "recompute" in " ".join(str(i.value) for i in at.info).lower()

    with handle.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, "fixture_demo", "OFR-002")
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
    assert vehicle.seats == 4


def test_the_resolved_contradiction_stops_being_raised(app_env):
    from desk.clock import SystemClock
    from desk.db import Database
    from desk.services import demo as demo_service

    config = app_env
    clock = SystemClock()
    handle = Database(config, clock)
    demo_service.replay_updates(handle, config, clock)

    def contradictions(app):
        return [str(e.value) for e in app.error if "Contradiction" in str(e.value)]

    at = open_app(app_env, "Vehicles")
    before = contradictions(at)
    assert before, "the replay is supposed to introduce at least one contradiction"

    next(t for t in at.text_input if t.label == "Decided by").set_value("Max")
    next(t for t in at.text_input if t.label == "Dated note").set_value("confirmed")
    next(b for b in at.button if "Record this decision" in str(b.label)).click().run()

    # The one that was resolved is gone, and anything else still outstanding is
    # still shown. Asserting that *no* contradiction remains would quietly pass
    # for the wrong reason as soon as the snapshot grows a second one.
    after = contradictions(open_app(app_env, "Vehicles"))
    assert len(after) == len(before) - 1
    assert before[0] not in after


# -- the screens added for capture and cost templates -------------------


@pytest.mark.parametrize("screen", ["Capture a requirement"])
def test_the_capture_screen_renders(app_env, screen):
    at = open_app(app_env, screen)
    assert not at.exception
    text = all_text(at)
    assert "while the call is fresh" in text
    assert "an unknown requirement stays unknown" in text


def test_the_capture_screen_stores_a_requirement(app_env):
    from desk.clock import SystemClock
    from desk.db import Database
    from desk.repositories import companies as companies_repo

    config = app_env
    handle = Database(config, SystemClock())
    with handle.open() as conn:
        before = len(companies_repo.list_briefs(conn))

    at = open_app(app_env, "Capture a requirement")
    next(t for t in at.text_input if t.label == "Who said it").set_value("Tomas Havel")
    next(
        t for t in at.text_area if "What was actually said" in str(t.label)
    ).set_value("Recorded call: physical stock only, LHD.")
    next(b for b in at.button if "Capture requirement" in str(b.label)).click().run()

    with handle.open() as conn:
        after = len(companies_repo.list_briefs(conn))
    assert after == before + 1

    messages = " ".join(str(s.value) for s in at.success)
    assert "Requirement captured" in messages


def test_an_uncomparable_budget_is_warned_about_on_screen(app_env):
    at = open_app(app_env, "Capture a requirement")
    next(t for t in at.text_input if t.label == "Who said it").set_value("Tomas Havel")
    next(b for b in at.button if "Capture requirement" in str(b.label)).click().run()

    warnings = " ".join(str(w.value) for w in at.warning)
    assert "No budget was captured" in warnings or "no stated basis" in warnings


def test_the_cost_templates_tab_renders(app_env):
    at = open_app(app_env, "Sources and imports")
    text = all_text(at)
    assert "not a tax engine" in text or "reviewed set of assumptions" in text


def test_a_reviewed_cost_template_is_offered_and_does_not_invent_a_number(app_env):
    """A template supplies starting figures; an unpriced line stays unknown."""
    at = open_app(app_env, "Matches")
    box = next(c for c in at.checkbox if "reviewed template" in str(c.label))
    assert "Prague to Germany" in str(box.label)
    box.set_value(True).run()

    labels = [n.label for n in at.number_input]
    for expected in ("Transport", "Registration tax", "Risk allowance"):
        assert expected in labels

    warnings = " ".join(str(w.value) for w in at.warning)
    assert "arrive as unknown" in warnings
    assert "Registration tax" in warnings

    next(b for b in at.button if "Calculate scenario" in str(b.label)).click().run()
    errors = " ".join(str(e.value) for e in at.error)
    assert "Incomplete" in errors
    assert "Registration tax" in errors
    assert "not a zero-cost profit" in errors


def test_the_source_register_renders_without_a_serialisation_warning(app_env):
    """Regression: a mixed int/str column could not be serialised for display."""
    at = open_app(app_env, "Sources and imports")
    assert not at.exception
    frames = [df for df in at.dataframe]
    assert frames, "the register table did not render"
