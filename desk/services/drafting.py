"""Written messages for a person to review, edit and send themselves.

Two directions, because an opportunity has two ends:

* a **buy-side** enquiry to whoever is offering the car, and
* a **sell-side** offer to a buyer whose recorded requirement it matches.

Three rules shape every draft here, and they are the reason it is worth having at
all rather than typing something from memory.

**Only reviewed facts go in.** A draft never states a specification nobody has
checked, never describes a car as available when the last confirmation is stale,
and never quotes a price on a basis the record does not carry. What is missing
becomes a question in the message instead of a claim, which is also how a good
salesperson writes.

**No model writes the words.** These are templates filled from stored fields.
That keeps a promise the rest of the project makes — every line on screen comes
from a rule you can read — and it means the draft is identical offline, in a test,
and in front of a customer.

**Nothing is sent.** There is no transport in this module, no address book, no
credentials. :class:`Draft` renders to text for a human to copy. A person decides
whether any of it is true enough to send.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..clock import is_stale
from ..config import Config, DraftStatus, PriceBasis, VatRegime
from ..money import Money
from ..repositories.companies import BuyerBrief, Company, Contact
from ..repositories.supply import Offer, Vehicle
from .normalization import display_name, family_label, new_vehicle_indicator
from .outreach import describe_requirements

BUY_SIDE = "buy_side"
SELL_SIDE = "sell_side"

#: Shown with every draft, everywhere it is rendered.
HUMAN_IN_THE_LOOP = (
    "A person reviews and sends this. The tool does not send anything and holds no "
    "credentials that would let it."
)


@dataclass
class Draft:
    """One message, plus an honest account of what it had to leave out."""

    kind: str
    subject: str
    greeting: str
    body: list[str]
    sign_off: str
    recipient_label: str
    about: str
    #: Facts a person should obtain. They are questions in the message, not claims.
    missing_facts: list[str] = field(default_factory=list)
    #: Claims that were deliberately not made, and why.
    withheld: list[str] = field(default_factory=list)
    status: DraftStatus = DraftStatus.INTERNAL_RESEARCH

    @property
    def message(self) -> str:
        """Just the message, which is what a person copies."""
        parts = [self.greeting, "", *_spaced(self.body), "", self.sign_off]
        return "\n".join(parts).strip() + "\n"

    def render(self) -> str:
        """The message with its context, for the screen and for tests."""
        lines = [
            f"Direction: {'enquiry to the seller' if self.kind == BUY_SIDE else 'offer to the buyer'}",
            f"To: {self.recipient_label}",
            f"About: {self.about}",
            f"Subject: {self.subject}",
            "",
            self.message.rstrip(),
        ]
        if self.missing_facts:
            lines += ["", "Asked rather than assumed:"]
            lines += [f"  - {item}" for item in self.missing_facts]
        if self.withheld:
            lines += ["", "Deliberately not claimed:"]
            lines += [f"  - {item}" for item in self.withheld]
        lines += ["", f"Use status: {self.status.value}", HUMAN_IN_THE_LOOP]
        return "\n".join(lines) + "\n"


# -- buy side ---------------------------------------------------------------


def build_buy_side_draft(
    *,
    offer: Offer,
    vehicle: Vehicle | None,
    now: datetime,
    config: Config,
    sender_name: str = "Max",
    sender_company: str = "the dealership I am working with",
    comparable_median: Money | None = None,
    brief: BuyerBrief | None = None,
) -> Draft:
    """An enquiry to whoever is offering the car.

    The purpose is to establish the facts a decision needs, so the message is
    mostly questions. It never opens with a number: the comparable median is our
    analysis, and leading a seller with it would be negotiating against a figure
    they have not seen and cannot check.
    """
    missing: list[str] = []
    withheld: list[str] = []

    label = _car_label(offer, vehicle)
    seller = offer.seller_name or "there"
    reference = offer.external_record_id or offer.id

    body: list[str] = []

    opening = (
        f"I am {sender_name} at {sender_company}. I am looking at your {label}"
        f"{f' (your reference {reference})' if offer.external_record_id else ''}"
        f" and would like to check a few details before I take it further."
    )
    body.append(opening)

    # Availability. A stale confirmation becomes the first question rather than an
    # assumption, because the common failure is treating an advert as stock.
    if offer.last_observation_state in {"fetch_failed", "not_seen"} or is_stale(
        now, offer.availability_confirmed_at, hours=config.freshness.supply_hours
    ):
        body.append("First, is the car still available, and is it physically in stock with you?")
        missing.append("a current availability confirmation from the seller")
        withheld.append(
            "that the car is currently available — the last confirmation is stale or absent"
        )
    elif offer.is_allocation:
        body.append(
            "I understand this is a build slot rather than a car on the ground. Could you "
            "confirm the expected delivery window and what is fixed at this stage?"
        )
    else:
        body.append("Could you confirm the car is still available and physically with you?")

    questions: list[str] = []

    if vehicle is None or not vehicle.vin:
        questions.append("the VIN and build sheet")
        missing.append("the VIN")
    if vehicle is None or vehicle.mileage_km is None:
        questions.append("the current mileage")
        missing.append("the mileage")
    if vehicle is None or vehicle.first_registration is None:
        questions.append("the date of first registration")
        missing.append("the first registration date")

    # The tax basis decides the whole shape of a cross-border deal, so it is asked
    # explicitly whenever the record does not already carry it.
    if (
        PriceBasis(offer.price_basis) is PriceBasis.UNKNOWN
        or VatRegime(offer.vat_regime) is VatRegime.UNKNOWN
    ):
        questions.append(
            "whether the price is net or gross, and whether the car is VAT-qualifying or "
            "sold under the margin scheme"
        )
        missing.append("the price basis and VAT regime")
        withheld.append(
            "any comparison against our own figures — without a stated basis the prices are "
            "not comparable"
        )
    else:
        questions.append(
            f"that the price is {offer.price_basis} under the {offer.vat_regime} regime"
        )

    questions.append("the selling entity and the invoicing country")
    questions.append("which options are on the car, by build code where possible")

    body.append("Could you also confirm " + _sentence_list(questions) + "?")

    if offer.price is not None:
        body.append(
            f"Your advertised figure is {offer.price.format()}. Once the above is confirmed I "
            f"can come back quickly with a firm position."
        )
    if comparable_median is not None:
        withheld.append(
            f"our comparable median of {comparable_median.format()} — that is our own analysis "
            f"and is not a fact about this seller's car"
        )
    if brief is not None:
        withheld.append(
            "that we have a buyer waiting — a confirmed requirement is not a confirmed sale, "
            "and naming it weakens the position for nothing"
        )

    return Draft(
        kind=BUY_SIDE,
        subject=f"Enquiry: {label}" + (f" ({reference})" if offer.external_record_id else ""),
        greeting=f"Hello {seller},",
        body=body,
        sign_off=f"Many thanks,\n{sender_name}\n{sender_company}",
        recipient_label=offer.seller_name or "the seller (no contact recorded)",
        about=f"{label} · {offer.price.format() if offer.price else 'price not recorded'}",
        missing_facts=_dedupe(missing),
        withheld=_dedupe(withheld),
        status=DraftStatus.INTERNAL_RESEARCH,
    )


# -- sell side --------------------------------------------------------------


def build_sell_side_draft(
    *,
    company: Company,
    contact: Contact | None,
    brief: BuyerBrief,
    offer: Offer,
    vehicle: Vehicle | None,
    now: datetime,
    config: Config,
    sender_name: str = "Max",
    sender_company: str = "the dealership I am working with",
    matched_rules: list[str] | None = None,
    unverified_rules: list[str] | None = None,
    status: DraftStatus = DraftStatus.INTERNAL_RESEARCH,
) -> Draft:
    """An offer to a buyer whose recorded requirement this car matches.

    The strength of this message is that it quotes the buyer's own words back to
    them with a date attached. That is only possible because the requirement was
    recorded rather than remembered, and it is the single most persuasive thing
    the tool produces.
    """
    missing: list[str] = []
    withheld: list[str] = []

    label = _car_label(offer, vehicle)
    body: list[str] = []

    when = brief.conversation_date.date().isoformat()
    if brief.is_confirmed:
        body.append(
            f"I am {sender_name} at {sender_company}. When we spoke on {when} you confirmed "
            f"you were looking for a {family_label(brief.model_family)}. I have one that fits "
            f"what you described."
        )
    else:
        body.append(
            f"I am {sender_name} at {sender_company}. We discussed a "
            f"{family_label(brief.model_family)} on {when}. I have one that looks like a fit, "
            f"if that requirement is still live."
        )
        missing.append("confirmation that the requirement is still live")
        withheld.append("that the requirement is confirmed — it was discussed, not confirmed")

    if brief.is_expired(now):
        missing.append("reconfirmation — the recorded requirement has expired")
        withheld.append(
            "that this is a current requirement — the recorded one has passed its expiry"
        )

    # The specification, stated only from reviewed facts.
    specifics = _reviewed_specifics(offer, vehicle)
    if specifics:
        body.append("The car: " + _sentence_list(specifics) + ".")
    else:
        missing.append("reviewed vehicle facts to describe the car with")

    # What was asked for, matched back. Only rules that actually passed.
    if matched_rules:
        body.append(
            "Against what you asked for, this matches on "
            + _sentence_list(matched_rules[:6])
            + "."
        )
    elif brief.required_specs:
        body.append(
            f"You asked for {describe_requirements(brief.required_specs)}, and I have checked "
            f"this car against that."
        )

    if unverified_rules:
        body.append(
            "Still to confirm before you rely on it: " + _sentence_list(unverified_rules) + "."
        )
        missing.extend(unverified_rules)

    # Price, only where the bases are actually comparable.
    body.extend(_price_paragraph(offer, brief, missing, withheld))

    # The cross-border basis, which for nearly-new export stock is the mechanism.
    if vehicle is not None:
        indicator = new_vehicle_indicator(
            first_registration=vehicle.first_registration,
            mileage_km=vehicle.mileage_km,
            at=now,
        )
        if indicator.indicated_new is True:
            body.append(
                "On the tax side, the car meets the EU new-means-of-transport test, so you "
                "would account for acquisition VAT in your own country."
            )
        elif indicator.indicated_new is None:
            missing.append("the registration date or mileage needed to settle the tax basis")
            withheld.append(
                "the cross-border tax treatment — the test cannot be settled from the record"
            )

    # Availability, stated honestly or asked about.
    if is_stale(now, offer.availability_confirmed_at, hours=config.freshness.supply_hours):
        body.append("I am confirming current availability and will come straight back to you.")
        missing.append("a current availability confirmation from the seller")
        withheld.append("that the car is available right now — the confirmation is stale")
    elif offer.is_allocation:
        body.append(
            "To be clear, this is a build slot rather than a car on the ground, so delivery "
            "depends on the factory date."
        )

    if brief.required_by is not None:
        body.append(
            f"You mentioned needing one by {brief.required_by.date().isoformat()}; that is "
            f"what I am working to."
        )

    body.append("Would it be useful to send the full specification and photographs?")

    return Draft(
        kind=SELL_SIDE,
        subject=f"{family_label(brief.model_family)} — the specification you described",
        greeting=f"Hello {contact.full_name if contact and contact.full_name else company.display_name},",
        body=body,
        sign_off=f"Best regards,\n{sender_name}\n{sender_company}",
        recipient_label=_recipient(company, contact),
        about=f"{label} → {company.display_name}",
        missing_facts=_dedupe(missing),
        withheld=_dedupe(withheld),
        status=status,
    )


# -- helpers ----------------------------------------------------------------


def _price_paragraph(
    offer: Offer, brief: BuyerBrief, missing: list[str], withheld: list[str]
) -> list[str]:
    """Quote a price only when the two bases can actually be compared."""
    if offer.price is None:
        missing.append("a price on the offer")
        return []

    offer_unknown = (
        PriceBasis(offer.price_basis) is PriceBasis.UNKNOWN
        or VatRegime(offer.vat_regime) is VatRegime.UNKNOWN
    )
    if offer_unknown:
        missing.append("the price basis and VAT regime on the offer")
        withheld.append(
            "a price comparison against the stated budget — the offer's basis is unknown"
        )
        return [
            "I will confirm the price together with its VAT basis, so the figure you see is "
            "the one you can actually work from."
        ]

    line = (
        f"The asking figure is {offer.price.format()} {offer.price_basis} under the "
        f"{offer.vat_regime} regime."
    )

    if brief.budget is None:
        return [line]

    budget_unknown = (
        PriceBasis(brief.budget_basis) is PriceBasis.UNKNOWN
        or VatRegime(brief.budget_vat_regime) is VatRegime.UNKNOWN
    )
    if budget_unknown:
        withheld.append(
            "that the car is within budget — the recorded budget has no stated basis, so the "
            "two figures are not comparable"
        )
        missing.append("the basis of the stated budget")
        return [line]

    if brief.budget.currency != offer.price.currency:
        withheld.append(
            f"a budget comparison — the budget is in {brief.budget.currency} and the car is "
            f"priced in {offer.price.currency}"
        )
        return [line]

    if (
        brief.budget_basis == offer.price_basis
        and brief.budget_vat_regime == offer.vat_regime
        and offer.price.minor_units <= brief.budget.minor_units
    ):
        return [line + " That sits inside the figure you mentioned."]

    return [line]


def _reviewed_specifics(offer: Offer, vehicle: Vehicle | None) -> list[str]:
    """Only facts somebody has reviewed. Absent options stay absent, not denied."""
    if vehicle is None:
        return []
    parts: list[str] = []
    if vehicle.variant:
        parts.append(vehicle.variant)
    if vehicle.model_year:
        parts.append(f"model year {vehicle.model_year}")
    if vehicle.mileage_km is not None:
        parts.append(f"{vehicle.mileage_km:,} km")
    if vehicle.first_registration is not None:
        parts.append(f"first registered {vehicle.first_registration.date().isoformat()}")
    if vehicle.powertrain:
        parts.append(vehicle.powertrain)
    if vehicle.steering and vehicle.steering != "unknown":
        parts.append(vehicle.steering.upper())
    if vehicle.seats is not None:
        parts.append(f"{vehicle.seats} homologated seats")
    present = [key for key, value in (vehicle.specification or {}).items() if value is True]
    if present:
        parts.append("with " + ", ".join(sorted(key.replace("_", " ") for key in present)))
    if offer.location_country:
        parts.append(f"located in {offer.location_country}")
    return parts


def _car_label(offer: Offer, vehicle: Vehicle | None) -> str:
    if vehicle is None:
        return "the car in your listing"
    return display_name(vehicle.model_family, vehicle.variant)


def _recipient(company: Company, contact: Contact | None) -> str:
    if contact is None or not contact.full_name:
        return f"{company.display_name} (no identified contact recorded)"
    role = f", {contact.role_title}" if contact.role_title else ""
    return f"{contact.full_name}{role} — {company.display_name}"


def _sentence_list(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(cleaned[:-1]) + " and " + cleaned[-1]


def _spaced(paragraphs: list[str]) -> list[str]:
    out: list[str] = []
    for index, para in enumerate(paragraphs):
        if index:
            out.append("")
        out.append(para)
    return out


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


__all__ = [
    "BUY_SIDE",
    "SELL_SIDE",
    "HUMAN_IN_THE_LOOP",
    "Draft",
    "build_buy_side_draft",
    "build_sell_side_draft",
]
