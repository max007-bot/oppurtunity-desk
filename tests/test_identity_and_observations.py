"""Identity resolution, duplicate review and the observation history."""

from __future__ import annotations


from desk.repositories import companies as companies_repo
from desk.repositories import supply as supply_repo
from desk.services import observations as observations_service
from tests.conftest import company_row, iso, offer_row

VIN = "WDB4632761X100001"


# -- identity ------------------------------------------------------------


def test_one_vin_on_two_sources_is_one_vehicle_and_two_offers(importer, source, db):
    importer.apply("companies", [company_row("a"), company_row("b")], source_id=source)
    importer.apply(
        "offers",
        [
            offer_row("OFR-A", vin=VIN, vin_verified=True, seller_external_id="a"),
            offer_row("OFR-B", vin=VIN, vin_verified=True, seller_external_id="b", price={"amount": "203500", "currency": "EUR"}),
        ],
        source_id=source,
    )
    with db.open() as conn:
        first = supply_repo.find_offer_by_external(conn, source, "OFR-A")
        second = supply_repo.find_offer_by_external(conn, source, "OFR-B")
        vehicles = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
        siblings = supply_repo.offers_for_vehicle(conn, first.vehicle_id)

    assert first.vehicle_id == second.vehicle_id
    assert vehicles == 1
    assert len(siblings) == 2
    # The offers stay distinct: different prices and different sellers.
    assert first.price != second.price


def test_similar_spec_without_a_vin_creates_a_review_not_a_merge(importer, source, db):
    importer.apply(
        "offers",
        [
            offer_row("OFR-A", mileage_km=31000, first_registration="2025-08-01T00:00:00Z"),
            offer_row("OFR-B", mileage_km=31500, first_registration="2025-08-06T00:00:00Z"),
        ],
        source_id=source,
    )
    with db.open() as conn:
        reviews = companies_repo.pending_duplicate_reviews(conn)
        vehicles = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]

    assert vehicles == 2, "no automatic merge"
    assert len(reviews) == 1
    assert reviews[0]["decision"] == "pending"
    assert "not identity proof" in reviews[0]["reason"]


def test_clearly_different_cars_do_not_raise_a_duplicate_review(importer, source, db):
    importer.apply(
        "offers",
        [
            offer_row("OFR-A", mileage_km=9000, first_registration="2026-05-01T00:00:00Z"),
            offer_row("OFR-B", mileage_km=42000, first_registration="2024-02-01T00:00:00Z"),
        ],
        source_id=source,
    )
    with db.open() as conn:
        assert companies_repo.pending_duplicate_reviews(conn) == []


def test_an_allocation_without_a_vin_is_not_a_confirmed_identity(importer, source, db):
    importer.apply(
        "offers",
        [offer_row("OFR-ALLOC", stock_kind="allocation", status="allocation", availability_confirmed_at=None)],
        source_id=source,
    )
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-ALLOC")
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
    assert vehicle.identity_review_status == "allocation_no_vin"
    assert "cannot be a confirmed single identity" in (vehicle.notes or "")


def test_a_lookalike_model_goes_to_review_and_does_not_join_the_g63_family(importer, source, db):
    importer.apply(
        "offers",
        [offer_row("OFR-LOOK", model_family="Mercedes-Benz G 63 AMG Line styling", variant="G 500 AMG Line")],
        source_id=source,
    )
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-LOOK")
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
    assert vehicle.identity_review_status == "review_required"
    assert vehicle.model_family != "mercedes_g63"
    assert "exact model review" in (vehicle.notes or "") or "confirm the exact model" in (
        vehicle.notes or ""
    )


def test_companies_sharing_a_domain_raise_a_review(importer, source, db):
    importer.apply(
        "companies",
        [
            company_row("parent", website="https://shared.example"),
            company_row("branch", website="https://shared.example", legal_name="Demo Test Dealer A GmbH, Munich"),
        ],
        source_id=source,
    )
    with db.open() as conn:
        reviews = [
            row
            for row in companies_repo.pending_duplicate_reviews(conn)
            if row["entity_type"] == "company"
        ]
    assert len(reviews) == 1
    assert "branch or the same purchasing entity" in reviews[0]["reason"]


def test_a_reviewed_vehicle_fact_is_filled_but_never_overwritten(importer, source, db):
    importer.apply(
        "offers", [offer_row("OFR-A", vin=VIN, vin_verified=True, seats=5, powertrain="petrol")],
        source_id=source,
    )
    # A second source states a different seat count for the same verified VIN.
    importer.apply(
        "offers",
        [offer_row("OFR-B", vin=VIN, vin_verified=True, seats=4, powertrain=None)],
        source_id=source,
    )
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-A")
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
        second = supply_repo.find_offer_by_external(conn, source, "OFR-B")
        latest = supply_repo.latest_observation(conn, second.id)

    assert vehicle.seats == 5, "the reviewed fact was not overwritten by an import"
    assert latest.seats == 4, "but the contradicting observation is preserved"


# -- observation history -------------------------------------------------


def test_observations_are_appended_not_replaced(importer, source, db, clock):
    importer.apply("offers", [offer_row()], source_id=source)
    for price in ("198000", "195000", "193000"):
        later = clock.advance(hours=1)
        importer.apply(
            "offers",
            [offer_row(price={"amount": price, "currency": "EUR"}, observed_at=iso(later))],
            source_id=source,
        )

    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
        history = supply_repo.observations_for_offer(conn, offer.id)

    assert len(history) == 4
    assert [o.version for o in history] == [4, 3, 2, 1]
    assert history[-1].price.decimal == 200000, "the original observation survives"
    assert offer.price.decimal == 193000, "the offer carries the current price"


def test_price_change_alert_carries_old_new_currency_and_basis(importer, source, db, clock):
    importer.apply("offers", [offer_row()], source_id=source)
    later = clock.advance(hours=2)
    importer.apply(
        "offers",
        [offer_row(price={"amount": "193000", "currency": "EUR"}, observed_at=iso(later))],
        source_id=source,
    )
    with db.open() as conn:
        events = supply_repo.recent_change_events(conn)

    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "price_change"
    assert "200,000" in event["old_value"]
    assert "193,000" in event["new_value"]
    assert event["currency"] == "EUR"
    assert event["price_basis"] == "net"
    assert "not evidence that the seller is distressed" in event["notes"]


def test_an_unchanged_response_creates_no_alert(importer, source, db, clock):
    importer.apply("offers", [offer_row()], source_id=source)
    later = clock.advance(hours=1)
    importer.apply("offers", [offer_row(observed_at=iso(later))], source_id=source)
    with db.open() as conn:
        assert supply_repo.recent_change_events(conn) == []


def test_a_disappeared_advert_is_not_observed_never_sold(importer, source, db, clock):
    importer.apply("offers", [offer_row()], source_id=source)
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")

    later = clock.advance(hours=1)
    with db.write() as conn:
        observations_service.mark_not_observed(
            conn,
            offer_id=offer.id,
            source_id=source,
            external_record_id="OFR-T1",
            observed_at=later,
            now=later,
            is_demo=True,
        )

    with db.open() as conn:
        refreshed = supply_repo.get_offer(conn, offer.id)
        events = supply_repo.recent_change_events(conn)

    assert refreshed.last_observation_state == "not_seen"
    assert refreshed.status != "unavailable", "the seller never said it was sold"
    assert events[0]["kind"] == "not_observed"
    assert "not proof of a sale" in events[0]["notes"]


def test_a_failed_fetch_leaves_the_last_successful_observation_intact(importer, source, db, clock):
    importer.apply("offers", [offer_row()], source_id=source)
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
        before = supply_repo.latest_price_observation(conn, offer.id)

    later = clock.advance(hours=1)
    with db.write() as conn:
        observations_service.mark_fetch_failed(
            conn,
            offer_id=offer.id,
            source_id=source,
            external_record_id="OFR-T1",
            now=later,
            is_demo=True,
            error="connection reset",
        )

    with db.open() as conn:
        refreshed = supply_repo.get_offer(conn, offer.id)
        after = supply_repo.latest_price_observation(conn, offer.id)
        events = supply_repo.recent_change_events(conn)

    assert refreshed.last_observation_state == "fetch_failed"
    assert refreshed.price == offer.price, "the stored price is untouched"
    assert after.id == before.id, "the last successful observation is still the latest priced one"
    assert events[0]["kind"] == "fetch_failed"
    assert "not evidence the car was sold" in events[0]["notes"]


def test_a_specification_correction_is_recorded_as_its_own_event(importer, source, db, clock):
    importer.apply("offers", [offer_row(seats=5)], source_id=source)
    later = clock.advance(hours=1)
    importer.apply("offers", [offer_row(seats=4, observed_at=iso(later))], source_id=source)
    with db.open() as conn:
        events = supply_repo.recent_change_events(conn)
    kinds = {event["kind"] for event in events}
    assert "specification_correction" in kinds


def test_availability_change_is_separate_from_a_price_change(importer, source, db, clock):
    importer.apply("offers", [offer_row()], source_id=source)
    later = clock.advance(hours=1)
    importer.apply(
        "offers",
        [offer_row(status="reserved", observed_at=iso(later), availability_confirmed_at=None)],
        source_id=source,
    )
    with db.open() as conn:
        events = supply_repo.recent_change_events(conn)
    assert {event["kind"] for event in events} == {"availability_change"}


def test_a_company_research_signal_is_labelled_as_research(importer, source, db, clock):
    importer.apply("companies", [company_row("a")], source_id=source)
    with db.open() as conn:
        company = companies_repo.find_company_by_external(conn, source, "a")
    with db.write() as conn:
        observations_service.record_company_signal(
            conn,
            company_id=company.id,
            description="announced a new prestige showroom",
            observed_at=clock.now(),
            now=clock.now(),
            is_demo=True,
            source_id=source,
            external_record_id="signal-1",
        )
    with db.open() as conn:
        events = supply_repo.recent_change_events(conn)
    assert events[0]["kind"] == "company_signal"
    assert "not evidence of ownership or demand" in events[0]["notes"]


def test_snapshot_hash_is_stable_for_identical_payloads():
    payload = {"price": "200000", "status": "advertised_available"}
    assert observations_service.snapshot_hash(payload) == observations_service.snapshot_hash(
        dict(reversed(list(payload.items())))
    )
