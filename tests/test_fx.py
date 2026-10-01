"""Dated currency conversion: nothing converts without evidence."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from desk.money import Money
from desk.repositories import supply as supply_repo
from desk.services import fx
from desk.services.workflow import Workflow
from tests.conftest import FROZEN_NOW, comparable_row, offer_row


def eur(amount) -> Money:
    return Money.from_decimal(Decimal(str(amount)), "EUR")


def gbp(amount) -> Money:
    return Money.from_decimal(Decimal(str(amount)), "GBP")


@pytest.fixture
def rate(db, clock):
    with db.write() as conn:
        fx.record_rate(
            conn,
            from_currency="EUR",
            to_currency="GBP",
            rate=Decimal("0.8450"),
            rate_date=FROZEN_NOW.date(),
            source_note="ECB reference rate, read by the test",
            now=clock.now_iso(),
            is_demo=True,
        )
    return Decimal("0.8450")


# -- recording -----------------------------------------------------------


def test_a_rate_needs_a_stated_origin(db, clock):
    with db.write() as conn:
        with pytest.raises(fx.FxError, match="not evidence"):
            fx.record_rate(
                conn,
                from_currency="EUR",
                to_currency="GBP",
                rate=Decimal("0.845"),
                rate_date=FROZEN_NOW.date(),
                source_note="   ",
                now=clock.now_iso(),
                is_demo=True,
            )


def test_a_non_positive_rate_is_refused(db, clock):
    with db.write() as conn:
        for bad in ("0", "-1.2"):
            with pytest.raises(fx.FxError, match="must be positive"):
                fx.record_rate(
                    conn,
                    from_currency="EUR",
                    to_currency="GBP",
                    rate=Decimal(bad),
                    rate_date=FROZEN_NOW.date(),
                    source_note="test",
                    now=clock.now_iso(),
                    is_demo=True,
                )


def test_a_currency_is_not_converted_to_itself(db, clock):
    with db.write() as conn:
        with pytest.raises(fx.FxError):
            fx.record_rate(
                conn,
                from_currency="EUR",
                to_currency="EUR",
                rate=Decimal("1"),
                rate_date=FROZEN_NOW.date(),
                source_note="test",
                now=clock.now_iso(),
                is_demo=True,
            )


def test_an_unknown_currency_is_refused(db, clock):
    from desk.money import MoneyError

    with db.write() as conn:
        with pytest.raises(MoneyError):
            fx.record_rate(
                conn,
                from_currency="EUR",
                to_currency="XYZ",
                rate=Decimal("1.1"),
                rate_date=FROZEN_NOW.date(),
                source_note="test",
                now=clock.now_iso(),
                is_demo=True,
            )


# -- looking a rate up ---------------------------------------------------


def test_the_inverse_direction_is_derived_not_stored(db, clock, rate):
    with db.open() as conn:
        forward = fx.find_rate(conn, from_currency="EUR", to_currency="GBP", at=clock.now())
        backward = fx.find_rate(conn, from_currency="GBP", to_currency="EUR", at=clock.now())
        assert len(fx.list_rates(conn)) == 1, "only one direction is stored"
    assert forward.rate == Decimal("0.8450")
    assert backward.rate == Decimal(1) / Decimal("0.8450")
    assert "inverse of" in backward.source_note


def test_a_rate_dated_after_the_moment_is_not_used(db, clock):
    """Using tomorrow's rate for today's analysis would be hindsight."""
    with db.write() as conn:
        fx.record_rate(
            conn,
            from_currency="EUR",
            to_currency="GBP",
            rate=Decimal("0.9"),
            rate_date=FROZEN_NOW.date() + timedelta(days=3),
            source_note="a future rate",
            now=clock.now_iso(),
            is_demo=True,
        )
    with db.open() as conn:
        assert fx.find_rate(conn, from_currency="EUR", to_currency="GBP", at=clock.now()) is None


def test_a_stale_rate_is_refused_rather_than_used(db, clock):
    with db.write() as conn:
        fx.record_rate(
            conn,
            from_currency="EUR",
            to_currency="GBP",
            rate=Decimal("0.9"),
            rate_date=FROZEN_NOW.date() - timedelta(days=90),
            source_note="an old rate",
            now=clock.now_iso(),
            is_demo=True,
        )
    with db.open() as conn:
        assert fx.find_rate(conn, from_currency="EUR", to_currency="GBP", at=clock.now()) is None
        generous = fx.find_rate(
            conn, from_currency="EUR", to_currency="GBP", at=clock.now(), max_age_days=365
        )
    assert generous is not None, "the window is a setting, not a hard rule"


def test_the_most_recent_usable_rate_wins(db, clock):
    with db.write() as conn:
        for offset, value in ((5, "0.80"), (1, "0.85"), (3, "0.82")):
            fx.record_rate(
                conn,
                from_currency="EUR",
                to_currency="GBP",
                rate=Decimal(value),
                rate_date=FROZEN_NOW.date() - timedelta(days=offset),
                source_note=f"rate from {offset} days ago",
                now=clock.now_iso(),
                is_demo=True,
            )
    with db.open() as conn:
        found = fx.find_rate(conn, from_currency="EUR", to_currency="GBP", at=clock.now())
    assert found.rate == Decimal("0.85")


# -- converting ----------------------------------------------------------


def test_conversion_is_exact_and_carries_its_evidence(db, clock, rate):
    with db.open() as conn:
        found = fx.find_rate(conn, from_currency="EUR", to_currency="GBP", at=clock.now())
    result = fx.convert(eur(94000), found)
    assert result.converted == gbp("79430")
    assert "0.845" in result.assumption
    assert FROZEN_NOW.date().isoformat() in result.assumption
    assert "not commercially comparable" in result.assumption


def test_a_rate_cannot_be_applied_in_the_wrong_direction(db, clock, rate):
    with db.open() as conn:
        found = fx.find_rate(conn, from_currency="EUR", to_currency="GBP", at=clock.now())
    with pytest.raises(fx.FxError, match="correct direction"):
        fx.convert(gbp(78500), found)


def test_the_rate_book_reports_what_it_used(db, clock, rate):
    with db.open() as conn:
        book = fx.RateBook(conn, at=clock.now())
        converted = book.convert(eur(94000), "GBP")
        assert converted is not None
        assert book.convert(gbp(1000), "GBP") is None, "no conversion needed"
    assert len(book.used) == 1
    assert "0.845" in book.assumptions[0]


# -- inside a comparable set --------------------------------------------


def gbp_target(importer, source, db, clock):
    importer.apply(
        "offers",
        [
            offer_row(
                "OFR-GBP",
                model_family="bmw_7_series",
                variant="740d xDrive",
                steering="rhd",
                powertrain="diesel",
                price={"amount": "78500", "currency": "GBP"},
                location_country="GB",
                specification={},
            )
        ],
        source_id=source,
    )
    with db.open() as conn:
        return supply_repo.find_offer_by_external(conn, source, "OFR-GBP")


def seven_series(external_id: str, amount: str, currency: str, **overrides) -> dict:
    row = comparable_row(external_id, amount)
    row.update(
        {
            "model_family": "bmw_7_series",
            "variant": "740d xDrive",
            "powertrain": "diesel",
            "steering": "rhd",
            "price": {"amount": amount, "currency": currency},
        }
    )
    row.update(overrides)
    return row


def test_a_foreign_comparable_stays_excluded_without_a_rate(
    importer, source, db, demo_config, clock
):
    target = gbp_target(importer, source, db, clock)
    importer.apply(
        "comparables",
        [
            seven_series("C1", "76000", "GBP", mileage_km=22000),
            seven_series("C2", "81500", "GBP", mileage_km=18400),
            seven_series("C3", "94000", "EUR", mileage_km=25100),
        ],
        source_id=source,
    )
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        result = workflow.comparables_for(conn, target, persist=False)

    assert result.status == "insufficient_comparables"
    assert result.distinct_vehicles == 2
    reasons = [d.reason for d in result.excluded]
    assert any("no rate within" in reason for reason in reasons)
    assert any("rather than converted at an assumed rate" in reason for reason in reasons)


def test_a_dated_rate_brings_it_in_and_labels_it(
    importer, source, db, demo_config, clock, rate
):
    target = gbp_target(importer, source, db, clock)
    importer.apply(
        "comparables",
        [
            seven_series("C1", "76000", "GBP", mileage_km=22000),
            seven_series("C2", "81500", "GBP", mileage_km=18400),
            seven_series("C3", "94000", "EUR", mileage_km=25100),
        ],
        source_id=source,
    )
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        result = workflow.comparables_for(conn, target, persist=False)

    assert result.status == "median_available"
    assert result.distinct_vehicles == 3
    assert len(result.converted) == 1

    converted = result.converted[0]
    assert converted.observed_price == eur(94000)
    assert converted.price_used == gbp("79430")
    assert "converted at a dated rate" in " ".join(result.notes)
    assert any("0.845" in item for item in result.assumptions)


def test_a_conversion_does_not_excuse_a_real_difference(
    importer, source, db, demo_config, clock, rate
):
    """A rate fixes the currency, not the car."""
    target = gbp_target(importer, source, db, clock)
    importer.apply(
        "comparables",
        [
            seven_series("C1", "76000", "GBP", mileage_km=22000),
            seven_series("C2", "81500", "GBP", mileage_km=18400),
            # Euro-priced but left-hand drive: still excluded, on steering.
            seven_series("C3", "94000", "EUR", mileage_km=25100, steering="lhd"),
        ],
        source_id=source,
    )
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        result = workflow.comparables_for(conn, target, persist=False)

    assert result.status == "insufficient_comparables"
    assert any("steering" in d.reason for d in result.excluded)


def test_the_stored_set_keeps_both_the_observed_and_the_used_figure(
    importer, source, db, demo_config, clock, rate
):
    target = gbp_target(importer, source, db, clock)
    importer.apply(
        "comparables",
        [
            seven_series("C1", "76000", "GBP", mileage_km=22000),
            seven_series("C2", "81500", "GBP", mileage_km=18400),
            seven_series("C3", "94000", "EUR", mileage_km=25100),
        ],
        source_id=source,
    )
    workflow = Workflow(db, demo_config, clock)
    with db.write() as conn:
        workflow.comparables_for(conn, target, persist=True)

    with db.open() as conn:
        row = conn.execute(
            "SELECT * FROM comparable_members WHERE fx_rate IS NOT NULL"
        ).fetchone()
        stored_set = conn.execute("SELECT * FROM comparable_sets").fetchone()

    assert row["observed_price_minor"] == 9_400_000
    assert row["observed_currency"] == "EUR"
    assert row["price_minor"] == 7_943_000
    assert row["currency"] == "GBP"
    assert row["fx_rate"] == "0.8450"
    assert stored_set["conversions_applied"] == 1


# -- a cross-currency budget --------------------------------------------


def test_a_cross_currency_budget_is_unknown_without_a_rate(demo_config, clock):
    from tests.test_matching import make_brief, run

    result = run(demo_config, brief=make_brief(budget=gbp("215000")))
    unknown = " ".join(r.reason for r in result.unknown)
    assert "dated FX rate is required" in unknown


def test_a_dated_rate_resolves_a_cross_currency_budget(db, demo_config, clock, rate):
    from tests.test_matching import make_brief, make_offer, run

    with db.open() as conn:
        book = fx.RateBook(conn, at=clock.now())
        result = run(
            demo_config,
            offer=make_offer(price=gbp("170000")),
            brief=make_brief(budget=eur("215000")),
            rate_book=book,
        )

    budget_rule = next(r for r in result.rules if r.name == "budget headroom")
    assert budget_rule.outcome.value == "pass"
    assert "after converting" in budget_rule.reason
    assert "0.845" in budget_rule.reason
