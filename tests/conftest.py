"""Shared fixtures.

Every test runs against a temporary database with a frozen clock, so freshness,
expiry and staleness are exercised deliberately rather than depending on when the
suite happens to run.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from desk.clock import FrozenClock
from desk.config import Config, Freshness, Mode
from desk.db import Database
from desk.money import Money
from desk.services.imports import Importer, upsert_sources

FROZEN_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(FROZEN_NOW)


@pytest.fixture
def demo_config(tmp_path: Path) -> Config:
    return Config(
        mode=Mode.DEMO,
        demo_db=tmp_path / "demo.sqlite",
        live_db=tmp_path / "live.sqlite",
        timezone="Europe/Prague",
        allow_network=False,
        ai_enabled=False,
        freshness=Freshness(),
    )


@pytest.fixture
def live_config(demo_config: Config) -> Config:
    return replace(demo_config, mode=Mode.LIVE)


@pytest.fixture
def db(demo_config: Config, clock: FrozenClock) -> Database:
    handle = Database(demo_config, clock)
    handle.initialise()
    return handle


@pytest.fixture
def importer(db: Database, demo_config: Config, clock: FrozenClock) -> Importer:
    return Importer(db, demo_config, clock)


@pytest.fixture
def source(db: Database, demo_config: Config, clock: FrozenClock) -> str:
    """One approved, exportable test source."""
    upsert_sources(
        db,
        demo_config,
        clock,
        [
            {
                "id": "test_source",
                "name": "Test fixture source",
                "category": "mixed",
                "access_mode": "fixture",
                "status": "approved",
                "approved_use": "tests",
                "export_allowed": True,
                "reviewer": "test",
                "reviewed_at": FROZEN_NOW.isoformat(),
                "review_due_at": datetime(2027, 1, 1, tzinfo=timezone.utc).isoformat(),
            }
        ],
    )
    return "test_source"


@pytest.fixture
def eur():
    def make(amount) -> Money:
        return Money.from_decimal(Decimal(str(amount)), "EUR")

    return make


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def company_row(external_id: str = "acme", **overrides) -> dict:
    row = {
        "external_id": external_id,
        "legal_name": "Demo Test Dealer A GmbH",
        "country": "DE",
        "category": "dealer",
        "buying_route": "buy_for_stock",
        "website": f"https://{external_id}.example",
    }
    row.update(overrides)
    return row


def offer_row(external_id: str = "OFR-T1", **overrides) -> dict:
    row = {
        "external_id": external_id,
        "model_family": "mercedes_g63",
        "variant": "G 63 4MATIC",
        "generation": "w463a",
        "mileage_km": 11500,
        "powertrain": "petrol",
        "steering": "lhd",
        "seats": 5,
        "specification": {"rear_entertainment": True},
        "price": {"amount": "200000", "currency": "EUR"},
        "price_basis": "net",
        "vat_regime": "standard",
        "price_evidence_type": "supply_asking",
        "status": "availability_confirmed",
        "stock_kind": "physical_stock",
        "location_country": "CZ",
        "availability_confirmed_at": iso(FROZEN_NOW),
        "available_from": iso(FROZEN_NOW),
        "observed_at": iso(FROZEN_NOW),
        "first_registration": "2026-06-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def brief_row(external_id: str = "BRF-T1", company: str = "acme", **overrides) -> dict:
    row = {
        "external_id": external_id,
        "company_external_id": company,
        "model_family": "mercedes_g63",
        "required_specs": {"steering": "lhd", "seats": 5, "stock_requirement": "physical_stock"},
        "budget": {"amount": "215000", "currency": "EUR"},
        "budget_basis": "net",
        "budget_vat_regime": "standard",
        "quantity": 1,
        "route": "buy_for_stock",
        "conversation_date": iso(FROZEN_NOW),
        "confirmed_at": iso(FROZEN_NOW),
        "confirmed_by": "Test Buyer, Purchasing Manager",
    }
    row.update(overrides)
    return row


def comparable_row(external_id: str, amount: str, **overrides) -> dict:
    row = {
        "external_id": external_id,
        "model_family": "mercedes_g63",
        "variant": "G 63 4MATIC",
        "price": {"amount": amount, "currency": "EUR"},
        "price_basis": "net",
        "vat_regime": "standard",
        "price_evidence_type": "retail_asking",
        "mileage_km": 12000,
        "seats": 5,
        "powertrain": "petrol",
        "steering": "lhd",
        "observed_at": iso(FROZEN_NOW),
        "status": "advertised_available",
        "stock_kind": "physical_stock",
    }
    row.update(overrides)
    return row
