"""Source policy: approval, expiry, host and address checks, demo/live separation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from desk.db import Database, ModeViolation
from desk.services.imports import upsert_sources
from desk.services.source_policy import PolicyDenied, SourcePolicy, check_address
from tests.conftest import FROZEN_NOW

FUTURE = datetime(2027, 1, 1, tzinfo=timezone.utc)


def register(db, config, clock, **overrides):
    row = {
        "id": "live_api",
        "name": "Test live API",
        "category": "company_discovery",
        "access_mode": "public_api",
        "status": "approved",
        "base_url": "https://api.example.com",
        "allowed_hosts": ["api.example.com"],
        "allowed_paths": ["/v1/search"],
        "approved_use": "tests",
        "reviewer": "test",
        "reviewed_at": FROZEN_NOW.isoformat(),
        "review_due_at": FUTURE.isoformat(),
        "export_allowed": True,
    }
    row.update(overrides)
    upsert_sources(db, config, clock, [row])
    return SourcePolicy(db, config, clock)


def live_db(live_config, clock) -> Database:
    handle = Database(live_config, clock)
    handle.initialise()
    return handle


# -- mode separation -----------------------------------------------------


def test_demo_mode_never_fetches(db, demo_config, clock):
    policy = register(db, demo_config, clock)
    decision = policy.may_fetch("live_api", "https://api.example.com/v1/search")
    assert not decision.allowed
    assert "demo mode never performs live fetches" in decision.reason


def test_network_disabled_blocks_even_an_approved_source(live_config, clock):
    handle = live_db(live_config, clock)
    policy = register(handle, live_config, clock)
    decision = policy.may_fetch("live_api")
    assert not decision.allowed
    assert "network access is disabled" in decision.reason


def test_a_demo_database_cannot_be_opened_as_live(tmp_path, demo_config, live_config, clock):
    Database(demo_config, clock).initialise()
    # The live handle points at a different file by design; force the collision.
    collided = replace(live_config, live_db=demo_config.demo_db)
    with pytest.raises(ModeViolation):
        Database(collided, clock).initialise()


def test_demo_reset_refuses_a_database_stamped_live(live_config, clock):
    handle = live_db(live_config, clock)
    with pytest.raises(ModeViolation, match="only available in demo mode"):
        handle.reset_demo()


def test_demo_reset_touches_only_the_demo_file(db, demo_config, live_config, clock):
    live = live_db(live_config, clock)
    assert live.path.exists()
    db.reset_demo()
    assert live.path.exists(), "the live database is untouched"
    assert db.path.exists(), "the demo database is recreated empty"


# -- approval and expiry -------------------------------------------------


def test_an_unapproved_source_is_refused_and_does_not_fall_back(live_config, clock):
    handle = live_db(live_config, clock)
    config = replace(live_config, allow_network=True)
    policy = register(handle, config, clock, status="review_required")
    decision = policy.may_fetch("live_api", "https://api.example.com/v1/search")
    assert not decision.allowed
    assert "must not fall back to scraping" in decision.reason


def test_approval_lapses_on_its_review_due_date_without_an_edit(live_config, clock):
    handle = live_db(live_config, clock)
    config = replace(live_config, allow_network=True)
    policy = register(
        handle,
        config,
        clock,
        reviewed_at=(FROZEN_NOW - timedelta(days=400)).isoformat(),
        review_due_at=(FROZEN_NOW - timedelta(days=1)).isoformat(),
    )
    source = policy.get("live_api")
    assert source.status == "approved"
    assert policy.effective_status(source) == "expired"
    assert not policy.may_fetch("live_api").allowed


def test_an_expired_licence_blocks_export(live_config, clock):
    handle = live_db(live_config, clock)
    policy = register(
        handle,
        live_config,
        clock,
        review_due_at=(FROZEN_NOW - timedelta(days=1)).isoformat(),
    )
    decision = policy.may_export("live_api")
    assert not decision.allowed
    assert "review expired" in decision.reason
    assert "retention rule" in decision.reason


def test_a_source_that_forbids_export_blocks_export(db, demo_config, clock):
    policy = register(db, demo_config, clock, export_allowed=False)
    assert not policy.may_export("live_api").allowed


def test_a_source_that_forbids_raw_retention_says_so(db, demo_config, clock):
    policy = register(db, demo_config, clock, raw_retention_allowed=False)
    decision = policy.may_retain_raw("live_api")
    assert not decision.allowed
    assert "keep only the allowed fields" in decision.reason


def test_a_fixture_source_has_no_fetch_route(live_config, clock):
    handle = live_db(live_config, clock)
    config = replace(live_config, allow_network=True)
    policy = register(handle, config, clock, access_mode="fixture")
    decision = policy.may_fetch("live_api")
    assert not decision.allowed
    assert "no fetch route" in decision.reason


def test_an_unknown_source_is_refused(db, demo_config, clock):
    policy = SourcePolicy(db, demo_config, clock)
    assert not policy.may_fetch("nothing_here").allowed


def test_retention_expiry_is_derived_from_the_source_rule(db, demo_config, clock):
    policy = register(db, demo_config, clock, retention_days=30)
    expiry = policy.retention_expiry("live_api", FROZEN_NOW)
    assert expiry == FROZEN_NOW + timedelta(days=30)


def test_attribution_is_preserved_for_display(db, demo_config, clock):
    policy = register(db, demo_config, clock, attribution="(c) OpenStreetMap contributors, ODbL")
    assert policy.attribution_for(["live_api"]) == ["(c) OpenStreetMap contributors, ODbL"]


# -- host, path and address checks --------------------------------------


def test_a_host_outside_the_approved_list_is_refused(db, demo_config, clock):
    policy = register(db, demo_config, clock)
    source = policy.get("live_api")
    decision = policy.check_url(source, "https://elsewhere.example.com/v1/search")
    assert not decision.allowed
    assert "not in the approved host list" in decision.reason
    assert "would not create a right to fetch it" in decision.reason


def test_a_path_outside_the_approved_routes_is_refused(db, demo_config, clock):
    policy = register(db, demo_config, clock)
    source = policy.get("live_api")
    decision = policy.check_url(source, "https://api.example.com/v1/everything")
    assert not decision.allowed
    assert "does not crawl a whole site" in decision.reason


def test_a_non_http_scheme_is_refused(db, demo_config, clock):
    policy = register(db, demo_config, clock)
    source = policy.get("live_api")
    assert not policy.check_url(source, "file:///etc/passwd").allowed


@pytest.mark.parametrize(
    "host,address",
    [
        ("internal.example.com", "127.0.0.1"),
        ("internal.example.com", "10.0.0.5"),
        ("internal.example.com", "192.168.1.1"),
        ("internal.example.com", "169.254.169.254"),
        ("internal.example.com", "::1"),
    ],
)
def test_a_host_resolving_to_a_private_address_is_blocked_without_contacting_it(host, address):
    """The SSRF guard: a redirect must not be able to reach a local service."""
    decision = check_address(host, resolver=lambda _host: [address])
    assert not decision.allowed
    assert "blocked without contacting it" in decision.reason


@pytest.mark.parametrize("host", ["localhost", "metadata.google.internal", "thing.localhost"])
def test_local_names_are_blocked_by_name(host):
    decision = check_address(host, resolver=lambda _host: ["93.184.216.34"])
    assert not decision.allowed


def test_a_public_address_passes():
    decision = check_address("example.com", resolver=lambda _host: ["93.184.216.34"])
    assert decision.allowed


def test_an_unresolvable_host_is_refused():
    def boom(_host):
        raise OSError("name or service not known")

    assert not check_address("nowhere.invalid", resolver=boom).allowed


def test_require_raises_rather_than_returning_a_soft_failure(db, demo_config, clock):
    policy = register(db, demo_config, clock)
    with pytest.raises(PolicyDenied):
        policy.may_fetch("live_api").require()
