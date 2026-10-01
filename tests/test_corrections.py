"""Resolving a contradiction between a source and the reviewed record."""

from __future__ import annotations

import pytest

from desk.repositories import supply as supply_repo
from desk.services import corrections
from tests.conftest import iso, offer_row


@pytest.fixture
def contradicted(importer, source, db, clock):
    """An offer whose latest observation disagrees on the seat count."""
    importer.apply("offers", [offer_row(seats=5)], source_id=source)
    later = clock.advance(hours=1)
    importer.apply("offers", [offer_row(seats=4, observed_at=iso(later))], source_id=source)
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
    return offer, vehicle


def contradictions(db, vehicle, offer):
    with db.open() as conn:
        return corrections.find_contradictions(conn, vehicle, offer.id)


# -- detection -----------------------------------------------------------


def test_a_disagreement_is_detected_with_both_values(db, contradicted):
    offer, vehicle = contradicted
    issues = contradictions(db, vehicle, offer)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.field == "seats"
    assert issue.reviewed_value == 5
    assert issue.observed_value == 4
    assert "homologation" in issue.consequence()


def test_the_import_did_not_overwrite_the_reviewed_fact(db, contradicted):
    _offer, vehicle = contradicted
    assert vehicle.seats == 5, "matching still uses the reviewed value until a person decides"


def test_an_unknown_value_never_contradicts_anything(importer, source, db, clock):
    importer.apply("offers", [offer_row(seats=5)], source_id=source)
    later = clock.advance(hours=1)
    # The source simply does not mention seats this time.
    importer.apply("offers", [offer_row(seats=None, observed_at=iso(later))], source_id=source)
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
    assert contradictions(db, vehicle, offer) == []


def test_a_failed_check_does_not_create_a_contradiction(importer, source, db, clock):
    from desk.services import observations as observations_service

    importer.apply("offers", [offer_row(seats=5)], source_id=source)
    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
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
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
    assert contradictions(db, vehicle, offer) == []


# -- accepting -----------------------------------------------------------


def test_accepting_updates_the_reviewed_record(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]

    with db.write() as conn:
        outcome = corrections.resolve(
            conn,
            vehicle_id=vehicle.id,
            contradiction=issue,
            decision="accepted_source",
            decided_by="Max",
            note="seller confirmed four seats by phone",
            now=clock.now(),
            is_demo=True,
        )

    with db.open() as conn:
        updated = supply_repo.get_vehicle(conn, vehicle.id)
    assert outcome.accepted
    assert updated.seats == 4
    assert "Recompute" in outcome.summary()


def test_accepting_writes_an_audit_entry(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]
    with db.write() as conn:
        corrections.resolve(
            conn,
            vehicle_id=vehicle.id,
            contradiction=issue,
            decision="accepted_source",
            decided_by="Max",
            note="seller confirmed by phone",
            now=clock.now(),
            is_demo=True,
        )

    with db.open() as conn:
        log = conn.execute(
            "SELECT * FROM change_log WHERE entity_id = ? AND action = 'correction_accepted'",
            (vehicle.id,),
        ).fetchone()
        review = corrections.history(conn, vehicle.id)[0]

    assert log["actor"] == "Max"
    assert log["old_value"] == "5"
    assert log["new_value"] == "4"
    assert review["decision"] == "accepted_source"
    assert review["note"] == "seller confirmed by phone"
    assert review["observation_id"], "the deciding observation is recorded"


def test_the_original_observation_survives_acceptance(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]
    with db.write() as conn:
        corrections.resolve(
            conn,
            vehicle_id=vehicle.id,
            contradiction=issue,
            decision="accepted_source",
            decided_by="Max",
            note="confirmed",
            now=clock.now(),
            is_demo=True,
        )
    with db.open() as conn:
        history = supply_repo.observations_for_offer(conn, offer.id)
    assert [o.seats for o in history] == [4, 5], "both observations remain"


# -- keeping the reviewed value -----------------------------------------


def test_keeping_the_reviewed_value_changes_nothing_but_is_recorded(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]

    with db.write() as conn:
        outcome = corrections.resolve(
            conn,
            vehicle_id=vehicle.id,
            contradiction=issue,
            decision="kept_reviewed",
            decided_by="Max",
            note="the advert is wrong; we have the homologation document",
            now=clock.now(),
            is_demo=True,
        )

    with db.open() as conn:
        unchanged = supply_repo.get_vehicle(conn, vehicle.id)
        review = corrections.history(conn, vehicle.id)[0]

    assert not outcome.accepted
    assert unchanged.seats == 5
    assert review["decision"] == "kept_reviewed"
    assert "will not be raised again" in outcome.summary()


def test_a_decided_contradiction_stops_being_raised(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]
    with db.write() as conn:
        corrections.resolve(
            conn,
            vehicle_id=vehicle.id,
            contradiction=issue,
            decision="kept_reviewed",
            decided_by="Max",
            note="the advert is wrong",
            now=clock.now(),
            is_demo=True,
        )
    with db.open() as conn:
        refreshed = supply_repo.get_vehicle(conn, vehicle.id)
    assert contradictions(db, refreshed, offer) == []


def test_a_later_different_value_is_raised_again(db, contradicted, clock, importer, source):
    """Dismissing one claim does not silence the next, different one."""
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]
    with db.write() as conn:
        corrections.resolve(
            conn,
            vehicle_id=vehicle.id,
            contradiction=issue,
            decision="kept_reviewed",
            decided_by="Max",
            note="the advert is wrong",
            now=clock.now(),
            is_demo=True,
        )

    later = clock.advance(hours=2)
    importer.apply("offers", [offer_row(seats=7, observed_at=iso(later))], source_id=source)
    with db.open() as conn:
        refreshed = supply_repo.get_vehicle(conn, vehicle.id)
    issues = contradictions(db, refreshed, offer)
    assert len(issues) == 1
    assert issues[0].observed_value == 7


# -- guards --------------------------------------------------------------


def test_a_decision_requires_a_dated_note(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]
    with db.write() as conn:
        with pytest.raises(ValueError, match="note is required"):
            corrections.resolve(
                conn,
                vehicle_id=vehicle.id,
                contradiction=issue,
                decision="accepted_source",
                decided_by="Max",
                note="   ",
                now=clock.now(),
                is_demo=True,
            )


def test_a_decision_requires_a_named_person(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]
    with db.write() as conn:
        with pytest.raises(ValueError, match="who decided"):
            corrections.resolve(
                conn,
                vehicle_id=vehicle.id,
                contradiction=issue,
                decision="accepted_source",
                decided_by="",
                note="confirmed",
                now=clock.now(),
                is_demo=True,
            )


def test_an_unknown_decision_is_refused(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]
    with db.write() as conn:
        with pytest.raises(ValueError, match="accepted_source or kept_reviewed"):
            corrections.resolve(
                conn,
                vehicle_id=vehicle.id,
                contradiction=issue,
                decision="whatever",
                decided_by="Max",
                note="confirmed",
                now=clock.now(),
                is_demo=True,
            )


def test_resolving_acknowledges_the_alert(db, contradicted, clock):
    offer, vehicle = contradicted
    issue = contradictions(db, vehicle, offer)[0]
    with db.open() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM change_events WHERE acknowledged_at IS NULL"
        ).fetchone()[0]

    with db.write() as conn:
        corrections.resolve(
            conn,
            vehicle_id=vehicle.id,
            contradiction=issue,
            decision="accepted_source",
            decided_by="Max",
            note="confirmed",
            now=clock.now(),
            is_demo=True,
        )

    with db.open() as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM change_events WHERE acknowledged_at IS NULL"
        ).fetchone()[0]
    assert after < before


# -- the effect on matching ---------------------------------------------


def test_an_accepted_correction_changes_the_match_after_a_recompute(
    db, demo_config, clock, importer, source
):
    """The whole point: a corrected seat count reaches the buyer rules."""
    from desk.repositories import analysis as analysis_repo
    from desk.services.workflow import Workflow
    from tests.conftest import brief_row, company_row

    importer.apply("companies", [company_row("buyer")], source_id=source)
    importer.apply("offers", [offer_row(seats=5)], source_id=source)
    importer.apply(
        "buyer_briefs",
        [brief_row(company="buyer", required_specs={"seats": 5, "steering": "lhd"})],
        source_id=source,
    )

    workflow = Workflow(db, demo_config, clock)
    workflow.recompute()
    with db.open() as conn:
        before = analysis_repo.list_matches(conn)[0]
    assert before["fit_status"] == "specification_fit"

    later = clock.advance(hours=1)
    importer.apply("offers", [offer_row(seats=4, observed_at=iso(later))], source_id=source)

    # Still a fit: the reviewed fact has not changed.
    workflow.recompute()
    with db.open() as conn:
        during = analysis_repo.list_matches(conn)[0]
    assert during["fit_status"] == "specification_fit"

    with db.open() as conn:
        offer = supply_repo.find_offer_by_external(conn, source, "OFR-T1")
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id)
        issue = corrections.find_contradictions(conn, vehicle, offer.id)[0]
    with db.write() as conn:
        corrections.resolve(
            conn,
            vehicle_id=vehicle.id,
            contradiction=issue,
            decision="accepted_source",
            decided_by="Max",
            note="seller confirmed four seats",
            now=clock.now(),
            is_demo=True,
        )

    workflow.recompute()
    with db.open() as conn:
        after = analysis_repo.list_matches(conn)[0]
    assert after["fit_status"] == "no_match"
    assert "homologated seats" in after["failed_rules"]
