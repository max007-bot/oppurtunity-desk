"""The hosted build: the deployment guard, the mobile.de adapter, drafts, ranking.

These cover the parts added to make the prototype hostable. The theme running
through them is that none of the new surface is allowed to weaken a claim the
tool already makes: a hosted copy cannot reach real data, an adapter without
credentials refuses rather than substituting samples, and a draft asks about
anything nobody has checked instead of asserting it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from desk.config import Mode, PriceEvidenceType
from desk.connectors import mobile_de
from desk.connectors.base import ConnectorUnavailable
from desk.money import Money
from desk.services import deployment
from desk.services.normalization import display_name
from desk.services.source_policy import SourcePolicy


def eur(amount) -> Money:
    return Money.from_decimal(Decimal(str(amount)), "EUR")


# -- the deployment guard ---------------------------------------------------


def test_a_local_instance_is_unrestricted(demo_config):
    posture = deployment.guard(demo_config, environ={})
    assert posture.public is False
    assert "Local instance" in posture.label


def test_a_public_instance_in_live_mode_is_refused(demo_config):
    """The whole basis for hosting this is that the hosted copy holds no real data."""
    from dataclasses import replace

    live = replace(demo_config, mode=Mode.LIVE)
    with pytest.raises(deployment.DeploymentRefused) as error:
        deployment.guard(live, environ={"DESK_PUBLIC": "1"})
    assert "may only run in demo mode" in str(error.value)


def test_a_public_instance_with_network_access_is_refused(demo_config):
    from dataclasses import replace

    networked = replace(demo_config, allow_network=True)
    with pytest.raises(deployment.DeploymentRefused) as error:
        deployment.guard(networked, environ={"DESK_PUBLIC": "1"})
    assert "network access is enabled" in str(error.value)


def test_a_public_instance_with_runtime_ai_is_refused(demo_config):
    """The hosted page says every figure comes from a readable rule."""
    from dataclasses import replace

    with_ai = replace(demo_config, ai_enabled=True)
    with pytest.raises(deployment.DeploymentRefused) as error:
        deployment.guard(with_ai, environ={"DESK_PUBLIC": "1"})
    assert "runtime AI is enabled" in str(error.value)


def test_a_public_demo_instance_is_allowed(demo_config):
    posture = deployment.guard(demo_config, environ={"DESK_PUBLIC": "1"})
    assert posture.public is True
    assert "sample stock only" in posture.label


def test_the_public_flag_is_not_set_by_an_empty_string(demo_config):
    assert deployment.is_public_deployment({"DESK_PUBLIC": ""}) is False
    assert deployment.is_public_deployment({"DESK_PUBLIC": "0"}) is False
    assert deployment.is_public_deployment({"DESK_PUBLIC": "true"}) is True


def test_seeding_works_with_no_database_file_at_all(demo_config, clock):
    """What a fresh deployment actually looks like.

    ``data/`` is gitignored, so a host checks out the repository with no database
    file — not an empty one, none. Seeding therefore has to migrate before it can
    open anything. This never shows up locally, because the file has existed
    since the first time anybody ran the CLI, which is exactly why it reached a
    deploy before being caught.
    """
    from desk.db import Database

    assert not demo_config.db_path.exists()
    handle = Database(demo_config, clock)
    note = deployment.ensure_demo_data(handle, demo_config, clock)

    assert demo_config.db_path.exists()
    assert note and "Sample stock loaded" in note
    with handle.open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] > 0


def test_seeding_runs_once_and_never_overwrites(db, demo_config, clock, importer, source):
    """A redeploy must not wipe what is already there."""
    first = deployment.ensure_demo_data(db, demo_config, clock)
    assert first is None or "Sample stock loaded" in first
    with db.open() as conn:
        before = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    assert deployment.ensure_demo_data(db, demo_config, clock) is None
    with db.open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == before


# -- the mobile.de adapter --------------------------------------------------


def test_the_adapter_reports_that_it_is_not_connected(monkeypatch):
    for key in mobile_de.CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert mobile_de.credential_status() == mobile_de.STATUS_BUILT_NO_CREDENTIALS
    label = mobile_de.status_label()
    assert "not connected" in label
    # The word that must never appear while the adapter has never connected.
    assert "Connected ·" not in label


def test_a_blank_credential_is_not_a_credential(monkeypatch):
    monkeypatch.setenv("MOBILE_DE_CLIENT_ID", "   ")
    monkeypatch.setenv("MOBILE_DE_CLIENT_SECRET", "")
    assert mobile_de.credential_status() == mobile_de.STATUS_BUILT_NO_CREDENTIALS


def test_the_adapter_refuses_and_does_not_fall_back_to_samples(db, demo_config, clock):
    """The failure this guards against.

    Quietly returning sample data to a caller who asked for the market would make
    every downstream figure a fabrication wearing a real source's name.
    """
    with db.open() as conn:
        policy = SourcePolicy(conn, demo_config, clock)
        connector = mobile_de.MobileDeConnector(policy=policy, config=demo_config, clock=clock)
        with pytest.raises(ConnectorUnavailable) as error:
            connector.fetch()
    message = str(error.value)
    assert "sample" not in message.lower() or "does not substitute" in message


def test_the_policy_gate_runs_before_the_credential_check(db, demo_config, clock, monkeypatch):
    """Holding a key is not the same as being allowed to use it."""
    monkeypatch.setenv("MOBILE_DE_CLIENT_ID", "x")
    monkeypatch.setenv("MOBILE_DE_CLIENT_SECRET", "y")
    with db.open() as conn:
        policy = SourcePolicy(conn, demo_config, clock)
        connector = mobile_de.MobileDeConnector(policy=policy, config=demo_config, clock=clock)
        with pytest.raises(ConnectorUnavailable) as error:
            connector.fetch()
    assert "credentials" not in str(error.value).lower()


def test_a_published_payload_parses_into_offers(db, demo_config, clock):
    with db.open() as conn:
        policy = SourcePolicy(conn, demo_config, clock)
        connector = mobile_de.MobileDeConnector(policy=policy, config=demo_config, clock=clock)
        result = connector.parse(
            {
                "ads": [
                    {
                        "mobileAdId": "123456",
                        "make": "Mercedes-Benz",
                        "model": "S 580",
                        "modelDescription": "S 580 4MATIC lang",
                        "mileage": 14,
                        "firstRegistration": "2026-04",
                        "price": {
                            "consumerPriceGross": 228480,
                            "consumerPriceNet": 192000,
                            "currency": "EUR",
                            "vatRate": 19,
                            "vatDeductible": True,
                        },
                        "sellerName": "Example Export GmbH",
                        "country": "DE",
                    }
                ]
            }
        )
    assert not result.errors
    record = result.records[0]
    assert record.entity_type == "offer"
    assert record.fields["price_amount"] == 192000
    assert record.fields["price_basis"] == "net"
    assert record.fields["vat_regime"] == "standard"
    # An advert is supply, never a completed sale.
    assert record.fields["price_evidence_type"] == PriceEvidenceType.SUPPLY_ASKING.value
    assert record.fields["first_registration"] == "2026-04-01"


def test_a_price_without_a_currency_is_reported_not_assumed(db, demo_config, clock):
    with db.open() as conn:
        policy = SourcePolicy(conn, demo_config, clock)
        connector = mobile_de.MobileDeConnector(policy=policy, config=demo_config, clock=clock)
        result = connector.parse(
            {"ads": [{"mobileAdId": "9", "price": {"consumerPriceGross": 100000}}]}
        )
    assert any("no currency" in error for error in result.errors)
    assert "price_amount" not in result.records[0].fields


def test_an_advert_with_no_identifier_is_skipped_rather_than_invented(db, demo_config, clock):
    with db.open() as conn:
        policy = SourcePolicy(conn, demo_config, clock)
        connector = mobile_de.MobileDeConnector(policy=policy, config=demo_config, clock=clock)
        result = connector.parse({"ads": [{"make": "BMW"}]})
    assert result.records == []
    assert any("no identifier" in error for error in result.errors)


def test_an_unstated_vat_position_leaves_the_basis_unknown(db, demo_config, clock):
    """Unknown has to survive the import, because it blocks the comparison later."""
    with db.open() as conn:
        policy = SourcePolicy(conn, demo_config, clock)
        connector = mobile_de.MobileDeConnector(policy=policy, config=demo_config, clock=clock)
        result = connector.parse(
            {"ads": [{"mobileAdId": "7", "price": {"consumerPriceGross": 90000, "currency": "EUR"}}]}
        )
    fields = result.records[0].fields
    assert fields["price_basis"] == "unknown"
    assert fields["vat_regime"] == "unknown"


# -- display names ----------------------------------------------------------


def test_a_family_and_variant_that_overlap_are_written_once():
    assert display_name("mercedes_g63", "G 63 4MATIC") == "Mercedes-AMG G 63 4MATIC"
    assert display_name("mercedes_g63", "G 63") == "Mercedes-AMG G 63"
    assert display_name("bmw_7_series", "740d xDrive") == "BMW 7 Series 740d xDrive"
    assert display_name("mercedes_gle", None) == "Mercedes-Benz GLE"
