"""The live refresh path, exercised entirely offline.

The connectors are given a stub fetcher that returns the saved synthetic provider
responses, so these tests prove the ingestion path without contacting anything.
The network itself is covered by the policy tests, which assert the refusals.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from desk.connectors.ares import AresConnector
from desk.connectors.base import ConnectorUnavailable, FetchResponse
from desk.connectors.fixtures import FIXTURES_DIR
from desk.connectors.overpass import BoundingBox, OverpassConnector
from desk.db import Database
from desk.repositories import companies as companies_repo
from desk.repositories import supply as supply_repo
from desk.services import refresh
from desk.services.imports import upsert_sources
from desk.services.source_policy import SourcePolicy
from tests.conftest import FROZEN_NOW, offer_row

PRAGUE = BoundingBox(south=50.03, west=14.35, north=50.12, east=14.50)


class StubFetcher:
    """Returns a saved response, and records what it was asked for."""

    def __init__(self, body: str, *, fail: Exception | None = None) -> None:
        self.body = body
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def _respond(self, source_id: str, url: str) -> FetchResponse:
        self.calls.append((source_id, url))
        if self.fail is not None:
            raise self.fail
        return FetchResponse(status_code=200, text=self.body, headers={}, url=url)

    def get(self, source_id, url, **kwargs):
        return self._respond(source_id, url)

    def post(self, source_id, url, **kwargs):
        return self._respond(source_id, url)


def fixture_text(name: str) -> str:
    return (FIXTURES_DIR / "source_responses" / name).read_text(encoding="utf-8")


@pytest.fixture
def live(tmp_path, demo_config, clock):
    """A live-mode database with the network flag on."""
    config = replace(
        demo_config,
        mode=__import__("desk.config", fromlist=["Mode"]).Mode.LIVE,
        allow_network=True,
        live_db=tmp_path / "live.sqlite",
    )
    handle = Database(config, clock)
    handle.initialise()
    return handle, config


def approve(handle, config, clock, source_id: str, **overrides):
    row = {
        "id": source_id,
        "name": f"Test {source_id}",
        "category": "company_discovery",
        "access_mode": "public_api",
        "status": "approved",
        "base_url": "https://api.example.com",
        "allowed_hosts": ["api.example.com", "overpass-api.de"],
        "approved_use": "tests",
        "reviewer": "test",
        "reviewed_at": FROZEN_NOW.isoformat(),
        "review_due_at": datetime(2027, 1, 1, tzinfo=timezone.utc).isoformat(),
        "attribution": "Test attribution",
    }
    row.update(overrides)
    upsert_sources(handle, config, clock, [row])


def run(handle, config, clock, source_id, *, connector, request):
    return refresh.refresh(
        handle, config, clock, source_id=source_id, request=request, connector=connector
    )


def overpass(handle, config, clock, *, body: str | None = None, fail=None):
    policy = SourcePolicy(handle, config, clock)
    return OverpassConnector(
        policy=policy,
        config=config,
        clock=clock,
        fetcher=StubFetcher(body or fixture_text("overpass_sample.json"), fail=fail),
    )


# -- refusals happen before anything is opened --------------------------


def test_demo_mode_refuses_without_opening_a_run(db, demo_config, clock):
    report = refresh.refresh(
        db, demo_config, clock, source_id="osm_overpass", request={"bbox": PRAGUE}
    )
    assert report.status == "refused"
    assert "demo mode never performs live fetches" in report.reason
    assert report.run_id is None
    with db.open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_an_unapproved_source_is_refused(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass", status="review_required")
    report = refresh.refresh(
        handle, config, clock, source_id="osm_overpass", request={"bbox": PRAGUE}
    )
    assert report.status == "refused"
    assert "must not fall back to scraping" in report.reason


def test_a_source_with_no_adapter_is_refused_rather_than_improvised(live, clock):
    handle, config = live
    approve(handle, config, clock, "mobile_de", access_mode="licensed_api")
    report = refresh.refresh(handle, config, clock, source_id="mobile_de", request={})
    assert report.status == "refused"
    assert "no adapter is implemented" in report.reason
    assert "none is invented" in report.reason


# -- a successful run ----------------------------------------------------


def test_a_successful_run_stores_company_candidates(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass")
    report = run(
        handle,
        config,
        clock,
        "osm_overpass",
        connector=overpass(handle, config, clock),
        request={"bbox": PRAGUE},
    )

    assert report.ok
    assert report.records_returned == 3
    assert report.created == 3

    with handle.open() as conn:
        companies = companies_repo.list_companies(conn)
    names = {company.legal_name for company in companies}
    assert "Demo Prestige Dealer A" in names


def test_a_discovered_company_is_a_candidate_not_a_buyer(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass")
    run(
        handle,
        config,
        clock,
        "osm_overpass",
        connector=overpass(handle, config, clock),
        request={"bbox": PRAGUE},
    )

    with handle.open() as conn:
        company = companies_repo.find_company_by_external(
            conn, "osm_overpass", "node/900000001"
        )
        briefs = companies_repo.list_briefs(conn, company_id=company.id)
        policies = companies_repo.list_policies(conn, company.id)

    assert company.buying_route == "unknown"
    assert company.buying_authority == "unknown"
    assert briefs == [], "discovery never creates demand"
    assert policies and all(p.status == "review_required" for p in policies)


def test_field_level_evidence_is_recorded(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass")
    report = run(
        handle,
        config,
        clock,
        "osm_overpass",
        connector=overpass(handle, config, clock),
        request={"bbox": PRAGUE},
    )

    assert report.evidence > 0
    with handle.open() as conn:
        rows = conn.execute(
            "SELECT * FROM evidence WHERE source_id = 'osm_overpass'"
        ).fetchall()
        company = companies_repo.find_company_by_external(
            conn, "osm_overpass", "node/900000001"
        )

    linked = [row for row in rows if row["entity_id"] == company.id]
    assert linked, "evidence points at the company it describes"
    assert all(row["observed_at"] for row in linked)
    assert any(row["field_name"] == "legal_name" for row in linked)
    assert all(row["url"].startswith("https://www.openstreetmap.org/") for row in linked)


def test_the_run_records_what_was_asked_for(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass")
    report = run(
        handle,
        config,
        clock,
        "osm_overpass",
        connector=overpass(handle, config, clock),
        request={"bbox": PRAGUE},
    )

    with handle.open() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (report.run_id,)).fetchone()

    assert row["status"] == "ok"
    assert row["connector_id"] == "osm_overpass"
    assert row["mode"] == "live"
    assert "50.03" in row["request_summary"]
    assert row["finished_at"]


def test_attribution_survives_into_the_report(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass")
    report = run(
        handle,
        config,
        clock,
        "osm_overpass",
        connector=overpass(handle, config, clock),
        request={"bbox": PRAGUE},
    )
    assert "OpenStreetMap" in (report.attribution or "")
    assert "OpenStreetMap" in report.summary()


def test_running_twice_creates_no_duplicates(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass")
    first = run(
        handle,
        config,
        clock,
        "osm_overpass",
        connector=overpass(handle, config, clock),
        request={"bbox": PRAGUE},
    )
    second = run(
        handle,
        config,
        clock,
        "osm_overpass",
        connector=overpass(handle, config, clock),
        request={"bbox": PRAGUE},
    )

    assert first.created == 3
    assert second.created == 0
    assert second.updated == 3
    with handle.open() as conn:
        assert len(companies_repo.list_companies(conn)) == 3


def test_ares_verification_marks_the_registry_id_as_verified(live, clock):
    handle, config = live
    approve(handle, config, clock, "cz_ares", category="identity_verification")
    policy = SourcePolicy(handle, config, clock)
    connector = AresConnector(
        policy=policy,
        config=config,
        clock=clock,
        fetcher=StubFetcher(fixture_text("ares_sample.json")),
    )
    report = run(handle, config, clock, "cz_ares", connector=connector, request={"ico": "10000001"})

    assert report.ok
    with handle.open() as conn:
        company = companies_repo.find_company_by_external(conn, "cz_ares", "ico/10000001")
    assert company.registry_id == "10000001"
    assert company.registry_verified
    assert company.buying_route == "unknown", "identity is not demand"


# -- failure leaves everything alone ------------------------------------


def test_a_failed_fetch_records_the_run_and_changes_no_records(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass")
    run(
        handle,
        config,
        clock,
        "osm_overpass",
        connector=overpass(handle, config, clock),
        request={"bbox": PRAGUE},
    )
    with handle.open() as conn:
        before = len(companies_repo.list_companies(conn))

    failing = overpass(
        handle, config, clock, fail=ConnectorUnavailable("the provider is throttling requests")
    )
    report = run(
        handle, config, clock, "osm_overpass", connector=failing, request={"bbox": PRAGUE}
    )

    with handle.open() as conn:
        after = len(companies_repo.list_companies(conn))
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (report.run_id,)).fetchone()

    assert report.status == "unavailable"
    assert "throttling" in report.reason
    assert after == before, "a failed fetch changed stored records"
    assert row["status"] == "unavailable"


def test_a_failed_supply_fetch_marks_offers_without_claiming_a_sale(
    live, clock, demo_config
):
    """The manual's rule, enforced on the live path too."""
    handle, config = live
    approve(handle, config, clock, "osm_overpass", category="vehicle_supply")

    from desk.services.imports import Importer

    Importer(handle, config, clock).apply(
        "offers", [offer_row("OFR-LIVE")], source_id="osm_overpass"
    )
    with handle.open() as conn:
        before = supply_repo.find_offer_by_external(conn, "osm_overpass", "OFR-LIVE")

    failing = overpass(handle, config, clock, fail=ConnectorUnavailable("connection reset"))
    report = run(
        handle, config, clock, "osm_overpass", connector=failing, request={"bbox": PRAGUE}
    )

    with handle.open() as conn:
        after = supply_repo.find_offer_by_external(conn, "osm_overpass", "OFR-LIVE")
        latest_priced = supply_repo.latest_price_observation(conn, after.id)

    assert after.last_observation_state == "fetch_failed"
    assert after.status == before.status, "the seller never said anything changed"
    assert after.price == before.price
    assert latest_priced.price == before.price, "the last successful observation survives"
    assert any("not evidence any of them were sold" in note for note in report.notes)


def test_an_adapter_bug_does_not_corrupt_stored_records(live, clock):
    handle, config = live
    approve(handle, config, clock, "osm_overpass")

    class Exploding(OverpassConnector):
        def fetch(self, **kwargs):
            raise RuntimeError("an adapter bug")

    connector = Exploding(
        policy=SourcePolicy(handle, config, clock), config=config, clock=clock
    )
    report = run(handle, config, clock, "osm_overpass", connector=connector, request={"bbox": PRAGUE})

    assert report.status == "error"
    assert "an adapter bug" in report.reason
    with handle.open() as conn:
        assert companies_repo.list_companies(conn) == []
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (report.run_id,)).fetchone()
    assert row["status"] == "error"


def test_a_partly_broken_response_reports_its_errors(live, clock):
    handle, config = live
    approve(handle, config, clock, "fr_recherche_entreprises", category="identity_verification")
    from desk.connectors.france import FranceConnector

    connector = FranceConnector(
        policy=SourcePolicy(handle, config, clock),
        config=config,
        clock=clock,
        fetcher=StubFetcher(fixture_text("france_sample.json")),
    )
    report = run(
        handle,
        config,
        clock,
        "fr_recherche_entreprises",
        connector=connector,
        request={"query": "demo"},
    )

    assert report.ok
    assert report.created == 1, "the diffusion-restricted record was not ingested"
    assert any("diffusion restriction" in error for error in report.errors)
