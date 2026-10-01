"""Connector adapters, exercised against saved fixtures with no internet.

Every test here runs offline. The live paths are covered by asserting that they
refuse to run without an approved source record, never by contacting anything.
"""

from __future__ import annotations

import pytest

from desk.connectors.approved_website import ApprovedWebsiteConnector
from desk.connectors.ares import AresConnector
from desk.connectors.base import ConnectorUnavailable
from desk.connectors.fixtures import FIXTURES_DIR, source_response
from desk.connectors.france import FranceConnector
from desk.connectors.overpass import BoundingBox, OverpassConnector
from desk.services.source_policy import SourcePolicy

PRAGUE = BoundingBox(south=50.03, west=14.35, north=50.12, east=14.50)


@pytest.fixture
def policy(db, demo_config, clock) -> SourcePolicy:
    return SourcePolicy(db, demo_config, clock)


def build(cls, policy, demo_config, clock):
    return cls(policy=policy, config=demo_config, clock=clock)


# -- Overpass ------------------------------------------------------------


def test_overpass_query_is_bounded(policy, demo_config, clock):
    connector = build(OverpassConnector, policy, demo_config, clock)
    query = connector.build_query(PRAGUE)
    assert "[out:json]" in query
    assert "50.03,14.35,50.12,14.5" in query
    assert 'nwr["shop"="car"]' in query
    assert 'nwr["amenity"="car_rental"]' in query
    assert "out tags center 200;" in query


def test_overpass_refuses_an_oversized_area(policy, demo_config, clock):
    connector = build(OverpassConnector, policy, demo_config, clock)
    with pytest.raises(ValueError, match="degree limit"):
        connector.build_query(BoundingBox(south=40.0, west=0.0, north=55.0, east=20.0))


def test_overpass_parses_the_fixture_into_company_candidates(policy, demo_config, clock):
    connector = build(OverpassConnector, policy, demo_config, clock)
    result = connector.parse(source_response("overpass_sample.json"))

    assert len(result.records) == 3, "the unnamed object is not a usable candidate"
    first = result.records[0]
    assert first.external_record_id == "node/900000001"
    assert first.fields["legal_name"] == "Demo Prestige Dealer A"
    assert first.fields["country"] == "CZ"
    assert first.fields["website"] == "https://demo-dealer-a.example"
    assert first.entity_type == "company"


def test_overpass_never_infers_buying_intent(policy, demo_config, clock):
    connector = build(OverpassConnector, policy, demo_config, clock)
    result = connector.parse(source_response("overpass_sample.json"))
    for record in result.records:
        assert record.fields["buying_route"] == "unknown"
        assert record.fields["buying_authority"] == "unknown"
        assert "not a buyer" in record.fields["notes"]


def test_overpass_preserves_attribution_and_licence(policy, demo_config, clock):
    connector = build(OverpassConnector, policy, demo_config, clock)
    result = connector.parse(source_response("overpass_sample.json"))
    assert "OpenStreetMap" in result.attribution
    assert "ODbL" in result.attribution
    assert all("OpenStreetMap" in (r.attribution or "") for r in result.records)


def test_overpass_uses_the_source_timestamp_not_the_fetch_time(policy, demo_config, clock):
    connector = build(OverpassConnector, policy, demo_config, clock)
    result = connector.parse(source_response("overpass_sample.json"))
    record = result.records[0]
    assert record.observed_at.isoformat().startswith("2026-09-26T18:00")
    assert record.fetched_at == clock.now()


def test_overpass_keeps_field_level_evidence(policy, demo_config, clock):
    connector = build(OverpassConnector, policy, demo_config, clock)
    record = connector.parse(source_response("overpass_sample.json")).records[0]
    fields = {item["field_name"] for item in record.evidence}
    assert {"legal_name", "website", "contact_phone"} <= fields
    assert all(item["url"].startswith("https://www.openstreetmap.org/") for item in record.evidence)


def test_overpass_live_fetch_is_refused_in_demo_mode(policy, demo_config, clock):
    connector = build(OverpassConnector, policy, demo_config, clock)
    with pytest.raises(ConnectorUnavailable):
        connector.fetch(bbox=PRAGUE)
    assert "unavailable" in connector.availability()


# -- ARES ----------------------------------------------------------------


def test_ares_parses_a_registry_identity(policy, demo_config, clock):
    connector = build(AresConnector, policy, demo_config, clock)
    result = connector.parse(source_response("ares_sample.json"), url="https://example/ico/10000001")

    assert len(result.records) == 1
    fields = result.records[0].fields
    assert fields["registry_id"] == "10000001"
    assert fields["legal_name"] == "Demo Prestige Dealer A s.r.o."
    assert fields["registry_verified"] is True
    assert fields["country"] == "CZ"
    assert fields["primary_activity"] == "45110"


def test_ares_identity_is_not_evidence_of_demand(policy, demo_config, clock):
    connector = build(AresConnector, policy, demo_config, clock)
    record = connector.parse(source_response("ares_sample.json")).records[0]
    assert "not that it buys vehicles" in record.fields["notes"]
    assert "buying_route" not in record.fields
    assert "contact_email" not in record.fields


def test_ares_not_found_verifies_nothing(policy, demo_config, clock):
    connector = build(AresConnector, policy, demo_config, clock)
    result = connector.parse(source_response("ares_not_found.json"))
    assert result.records == []
    assert result.fetch_status == "not_found"
    assert "nothing is verified" in " ".join(result.errors)


def test_ares_rejects_a_malformed_ico_without_a_request(policy, demo_config, clock, live_config):
    from dataclasses import replace

    from desk.db import Database
    from desk.services.imports import upsert_sources

    config = replace(live_config, allow_network=True)
    handle = Database(config, clock)
    handle.initialise()
    upsert_sources(
        handle,
        config,
        clock,
        [
            {
                "id": "cz_ares",
                "name": "ARES",
                "category": "identity_verification",
                "access_mode": "public_api",
                "status": "approved",
                "base_url": "https://ares.example.gov",
                "allowed_hosts": ["ares.example.gov"],
                "reviewer": "test",
                "reviewed_at": clock.now().isoformat(),
                "review_due_at": "2027-01-01T00:00:00Z",
            }
        ],
    )
    connector = AresConnector(
        policy=SourcePolicy(handle, config, clock), config=config, clock=clock
    )
    result = connector.fetch(ico="not-an-ico")
    assert result.records == []
    assert result.fetch_status == "invalid_request"


def test_ares_without_a_recorded_base_url_refuses_rather_than_guessing(
    live_config, clock
):
    from dataclasses import replace

    from desk.db import Database
    from desk.services.imports import upsert_sources

    config = replace(live_config, allow_network=True)
    handle = Database(config, clock)
    handle.initialise()
    upsert_sources(
        handle,
        config,
        clock,
        [
            {
                "id": "cz_ares",
                "name": "ARES",
                "category": "identity_verification",
                "access_mode": "public_api",
                "status": "approved",
                "reviewer": "test",
                "reviewed_at": clock.now().isoformat(),
                "review_due_at": "2027-01-01T00:00:00Z",
            }
        ],
    )
    connector = AresConnector(
        policy=SourcePolicy(handle, config, clock), config=config, clock=clock
    )
    with pytest.raises(ConnectorUnavailable, match="rather than guessing an endpoint"):
        connector.fetch(ico="10000001")


def test_ares_documents_the_fields_it_actually_reads(policy, demo_config, clock):
    connector = build(AresConnector, policy, demo_config, clock)
    assert "ico" in connector.supported_fields()
    assert "obchodniJmeno" in connector.supported_fields()


# -- France --------------------------------------------------------------


def test_france_parses_a_search_response(policy, demo_config, clock):
    connector = build(FranceConnector, policy, demo_config, clock)
    result = connector.parse(source_response("france_sample.json"))
    assert len(result.records) == 1, "the diffusion-restricted record is not ingested"
    fields = result.records[0].fields
    assert fields["registry_id"] == "900000001"
    assert fields["country"] == "FR"


def test_france_skips_diffusion_restricted_records_and_says_so(policy, demo_config, clock):
    connector = build(FranceConnector, policy, demo_config, clock)
    result = connector.parse(source_response("france_sample.json"))
    assert any("diffusion restriction" in error for error in result.errors)


def test_france_reports_its_pagination_cursor(policy, demo_config, clock):
    connector = build(FranceConnector, policy, demo_config, clock)
    result = connector.parse(source_response("france_sample.json"))
    assert result.next_cursor == "2"


def test_france_is_not_confused_with_api_entreprise(policy, demo_config, clock):
    connector = build(FranceConnector, policy, demo_config, clock)
    assert "not API Entreprise" in connector.parse(source_response("france_sample.json")).attribution


# -- approved website ----------------------------------------------------


def website_html() -> str:
    return (FIXTURES_DIR / "source_responses" / "approved_website_sample.html").read_text(
        encoding="utf-8"
    )


def test_website_adapter_extracts_published_business_contacts(policy, demo_config, clock):
    connector = build(ApprovedWebsiteConnector, policy, demo_config, clock)
    result = connector.parse(
        website_html(), url="https://demo-dealer-b.example/contact", company_external_id="b"
    )
    fields = result.records[0].fields
    assert fields["contact_email"] == "buying@demo-dealer-b.example"
    assert fields["contact_phone"] == "+9991000002"


def test_website_adapter_never_grants_a_contact_permission(policy, demo_config, clock):
    connector = build(ApprovedWebsiteConnector, policy, demo_config, clock)
    result = connector.parse(
        website_html(), url="https://demo-dealer-b.example/contact", company_external_id="b"
    )
    fields = result.records[0].fields
    assert fields["contact_policy_status"] == "review_required"
    assert "not consent to contact" in fields["notes"]


def test_website_adapter_does_not_execute_scripts_or_obey_page_text(policy, demo_config, clock):
    """Page content is data. An instruction inside it changes nothing."""
    connector = build(ApprovedWebsiteConnector, policy, demo_config, clock)
    result = connector.parse(
        website_html(), url="https://demo-dealer-b.example/contact", company_external_id="b"
    )
    record = result.records[0]
    # The injected instruction did not become a field, a recipient or a policy.
    assert "attacker@example.invalid" not in str(record.fields)
    assert record.fields["contact_email"] == "buying@demo-dealer-b.example"
    assert "this script must never run" not in str(record.fields)


def test_website_adapter_caps_the_urls_per_business(policy, demo_config, clock):
    connector = build(ApprovedWebsiteConnector, policy, demo_config, clock)
    result = connector.fetch(
        company_external_id="b",
        urls=[f"https://demo-dealer-b.example/{n}" for n in range(9)],
    )
    assert result.records == []
    assert "at most 5 approved pages" in " ".join(result.errors)


def test_website_adapter_is_unavailable_in_demo_mode(policy, demo_config, clock):
    connector = build(ApprovedWebsiteConnector, policy, demo_config, clock)
    with pytest.raises(ConnectorUnavailable):
        connector.fetch(company_external_id="b", urls=["https://demo-dealer-b.example/contact"])


# -- no live source is enabled by default -------------------------------


def test_no_connector_can_fetch_from_the_seeded_demo_register(db, demo_config, clock):
    """Every live adapter is disabled until its register entry is approved."""
    from desk.services import demo as demo_service

    demo_service.seed(db, demo_config, clock)
    policy = SourcePolicy(db, demo_config, clock)
    for source in policy.list_sources():
        assert not policy.may_fetch(source.id).allowed
