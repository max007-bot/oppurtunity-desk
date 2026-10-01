"""Drafts a person can send, and the order the opportunities come out in.

The drafting tests all ask the same question in different ways: when a fact is
missing, does the message ask about it or assert it? The ranking tests exist for
one reason — a large unverified gap must never outrank a smaller verified
contribution, because they are different kinds of claim.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from desk.config import PriceBasis, VatRegime
from desk.money import Money
from desk.repositories import companies as companies_repo
from desk.repositories import supply as supply_repo
from desk.services import drafting, opportunities as opp_service
from desk.services.workflow import Workflow
from tests.conftest import brief_row, company_row, comparable_row, offer_row


def eur(amount) -> Money:
    return Money.from_decimal(Decimal(str(amount)), "EUR")


@pytest.fixture
def loaded(importer, source, db, demo_config, clock):
    """One car, one buyer who asked for it, and enough comparables for a median."""
    importer.apply(
        "companies",
        [company_row("buyer", contact_name="A Buyer", contact_role="Purchasing Manager")],
        source_id=source,
    )
    importer.apply(
        "offers",
        [offer_row("OFR-T1", price={"amount": "193000", "currency": "EUR"},
                   seller_external_id="buyer")],
        source_id=source,
    )
    importer.apply(
        "buyer_briefs",
        [brief_row("BRF-T1", company="buyer", destination_country="DE")],
        source_id=source,
    )
    importer.apply(
        "comparables",
        [
            comparable_row("CMP-T1", "228000"),
            comparable_row("CMP-T2", "233500"),
            comparable_row("CMP-T3", "243000"),
        ],
        source_id=source,
    )
    Workflow(db, demo_config, clock).recompute()

    with db.open() as conn:
        row = conn.execute(
            "SELECT * FROM offers WHERE external_record_id = 'OFR-T1'"
        ).fetchone()
        offer = supply_repo.Offer.from_row(row)
        vehicle = supply_repo.get_vehicle(conn, offer.vehicle_id) if offer.vehicle_id else None
        company = conn.execute(
            "SELECT * FROM companies WHERE external_record_id = 'buyer'"
        ).fetchone()
        brief_db = conn.execute(
            "SELECT * FROM buyer_briefs WHERE external_record_id = 'BRF-T1'"
        ).fetchone()
    return {
        "offer": offer,
        "vehicle": vehicle,
        "company": companies_repo.Company.from_row(company),
        "brief": companies_repo.BuyerBrief.from_row(brief_db),
    }


# -- buy-side ---------------------------------------------------------------


def test_the_buy_side_draft_is_specific_to_the_car(loaded, demo_config, clock):
    draft = drafting.build_buy_side_draft(
        offer=loaded["offer"], vehicle=loaded["vehicle"], now=clock.now(), config=demo_config
    )
    message = draft.message
    assert "OFR-T1" in message
    assert "€193,000.00" in message
    assert draft.kind == drafting.BUY_SIDE


def test_the_buy_side_draft_never_shows_the_seller_our_own_median(loaded, demo_config, clock):
    """Our comparable median is our analysis, not a fact about the seller's car."""
    draft = drafting.build_buy_side_draft(
        offer=loaded["offer"],
        vehicle=loaded["vehicle"],
        now=clock.now(),
        config=demo_config,
        comparable_median=eur(233500),
    )
    assert "233,500" not in draft.message
    assert any("233,500" in item for item in draft.withheld)


def test_the_buy_side_draft_does_not_reveal_a_waiting_buyer(loaded, demo_config, clock):
    draft = drafting.build_buy_side_draft(
        offer=loaded["offer"],
        vehicle=loaded["vehicle"],
        now=clock.now(),
        config=demo_config,
        brief=loaded["brief"],
    )
    assert any("buyer waiting" in item for item in draft.withheld)


def test_an_unknown_tax_basis_becomes_a_question(loaded, demo_config, clock):
    offer = loaded["offer"]
    offer.price_basis = PriceBasis.UNKNOWN.value
    offer.vat_regime = VatRegime.UNKNOWN.value
    draft = drafting.build_buy_side_draft(
        offer=offer, vehicle=loaded["vehicle"], now=clock.now(), config=demo_config
    )
    assert "net or gross" in draft.message
    assert "the price basis and VAT regime" in draft.missing_facts


# -- sell-side --------------------------------------------------------------


def test_the_sell_side_draft_quotes_the_buyer_s_own_requirement(loaded, demo_config, clock):
    draft = drafting.build_sell_side_draft(
        company=loaded["company"],
        contact=None,
        brief=loaded["brief"],
        offer=loaded["offer"],
        vehicle=loaded["vehicle"],
        now=clock.now(),
        config=demo_config,
    )
    assert loaded["brief"].conversation_date.date().isoformat() in draft.message
    assert draft.kind == drafting.SELL_SIDE


def test_a_budget_with_no_basis_blocks_the_price_comparison(loaded, demo_config, clock):
    brief = loaded["brief"]
    brief.budget_basis = PriceBasis.UNKNOWN.value
    draft = drafting.build_sell_side_draft(
        company=loaded["company"],
        contact=None,
        brief=brief,
        offer=loaded["offer"],
        vehicle=loaded["vehicle"],
        now=clock.now(),
        config=demo_config,
    )
    assert "inside the figure you mentioned" not in draft.message
    assert any("not comparable" in item for item in draft.withheld)


def test_a_stale_confirmation_is_never_described_as_available(loaded, demo_config, clock):
    offer = loaded["offer"]
    offer.availability_confirmed_at = clock.now() - timedelta(days=9)
    draft = drafting.build_sell_side_draft(
        company=loaded["company"],
        contact=None,
        brief=loaded["brief"],
        offer=offer,
        vehicle=loaded["vehicle"],
        now=clock.now(),
        config=demo_config,
    )
    assert any("available right now" in item for item in draft.withheld)
    assert "confirming current availability" in draft.message


def test_an_unconfirmed_requirement_is_not_described_as_confirmed(loaded, demo_config, clock):
    brief = loaded["brief"]
    brief.confirmed_at = None
    brief.confirmed_by = None
    draft = drafting.build_sell_side_draft(
        company=loaded["company"],
        contact=None,
        brief=brief,
        offer=loaded["offer"],
        vehicle=loaded["vehicle"],
        now=clock.now(),
        config=demo_config,
    )
    assert "you confirmed" not in draft.message
    assert any("discussed, not confirmed" in item for item in draft.withheld)


def test_no_draft_contains_anything_that_could_send_it(loaded, demo_config, clock):
    draft = drafting.build_sell_side_draft(
        company=loaded["company"],
        contact=None,
        brief=loaded["brief"],
        offer=loaded["offer"],
        vehicle=loaded["vehicle"],
        now=clock.now(),
        config=demo_config,
    )
    assert drafting.HUMAN_IN_THE_LOOP in draft.render()
    assert not hasattr(draft, "send")


# -- ranking ----------------------------------------------------------------


def test_the_feed_only_contains_supply(loaded, db, demo_config, clock):
    """Retail comparables are evidence, never stock to buy."""
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        items = opp_service.build(
            conn, workflow=workflow, config=demo_config, now=clock.now()
        )
    references = {item.offer.external_record_id for item in items}
    assert "OFR-T1" in references
    assert not any(ref and ref.startswith("CMP-") for ref in references)


def test_a_verified_contribution_outranks_a_bigger_unverified_gap():
    """The ordering rule, stated directly.

    A weighted score would let a large gap that excludes every cost beat a smaller
    figure that survives them. Tiers make that impossible by construction.
    """
    assert opp_service.TIER_VERIFIED.rank < opp_service.TIER_SIGNAL_WITH_BUYER.rank
    assert opp_service.TIER_VERIFIED_WITH_BUYER.rank < opp_service.TIER_VERIFIED.rank
    assert opp_service.TIER_SIGNAL.rank < opp_service.TIER_INSUFFICIENT.rank


def test_a_car_priced_above_its_comparables_is_not_an_opportunity(
    importer, source, db, demo_config, clock
):
    importer.apply(
        "offers",
        [offer_row("OFR-HIGH", price={"amount": "260000", "currency": "EUR"})],
        source_id=source,
    )
    importer.apply(
        "comparables",
        [
            comparable_row("CMP-H1", "228000"),
            comparable_row("CMP-H2", "233500"),
            comparable_row("CMP-H3", "243000"),
        ],
        source_id=source,
    )
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        items = opp_service.build(
            conn, workflow=workflow, config=demo_config, now=clock.now()
        )
    high = next(i for i in items if i.offer.external_record_id == "OFR-HIGH")
    assert high.tier is opp_service.TIER_INSUFFICIENT
    assert high.signal is None or high.signal.minor_units <= 0


def test_the_order_is_stable_across_rebuilds(loaded, db, demo_config, clock):
    """Two cars can produce the same figure; the database's row order must not decide."""
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        first = [i.offer.external_record_id for i in opp_service.build(
            conn, workflow=workflow, config=demo_config, now=clock.now())]
    with db.open() as conn:
        second = [i.offer.external_record_id for i in opp_service.build(
            conn, workflow=workflow, config=demo_config, now=clock.now())]
    assert first == second


def test_every_card_knows_what_would_move_it_up(loaded, db, demo_config, clock):
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        items = opp_service.build(
            conn, workflow=workflow, config=demo_config, now=clock.now()
        )
    for item in items:
        assert item.what_would_move_it_up()
        assert item.why_ranked()


def test_a_gap_is_never_called_a_contribution(loaded, db, demo_config, clock):
    """The two numbers are kept in separate attributes on purpose."""
    workflow = Workflow(db, demo_config, clock)
    with db.open() as conn:
        items = opp_service.build(
            conn, workflow=workflow, config=demo_config, now=clock.now()
        )
    for item in items:
        if item.contribution is None:
            assert not item.is_complete
        if item.signal is not None and item.contribution is None:
            assert "not profit" in item.gap.reason or "incomplete" in item.tier.meaning.lower()
