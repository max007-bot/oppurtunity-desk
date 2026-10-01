"""mobile.de, through its official API — built, and not connected.

**This module contains no scraping and never will.** It targets mobile.de's
official partner interfaces (the Search API for listings and the Seller API for a
seller's own stock), both of which require a partner agreement and issued
credentials. There is no HTML parsing here, no browser automation, and no request
to any mobile.de URL that is not the documented API host recorded in the source
register.

Three separate things have to be true before this adapter moves:

1. **A partner agreement exists**, and the source register entry for ``mobile_de``
   has been reviewed and approved by a named person. Until then the policy gate
   refuses the fetch, exactly as it does for every other source.
2. **Credentials are present** in the environment. They are never committed, never
   defaulted and never guessed.
3. **A base URL is recorded** in the source register, taken from the published
   specification rather than from this file. If it is missing the adapter raises
   instead of improvising an endpoint.

Today none of the three holds, so :meth:`fetch` raises ``ConnectorUnavailable``.
It does **not** quietly return sample data in place of the market: a caller that
wanted mobile.de and got the sample feed without being told would be the exact
failure this project exists to avoid. The Opportunities screen stamps the source
on every row, so a sample car is visibly a sample car.

:meth:`parse` is deliberately separate and fully testable offline against a saved
fixture response, which is how the mapping stays verifiable before anyone has
access to the real thing.

Documentation: https://services.mobile.de/manual/
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from ..config import Confidence
from ..models import AdapterResult
from .base import Connector, ConnectorUnavailable

ATTRIBUTION = (
    "Listing data from mobile.de via its official API, under a partner agreement. "
    "An advert is evidence that a car is offered for sale; it is not evidence of a "
    "completed transaction, of current availability, or of what the seller will accept."
)

#: Environment variables the adapter reads. Absent by design in this build.
CREDENTIAL_KEYS = ("MOBILE_DE_CLIENT_ID", "MOBILE_DE_CLIENT_SECRET")

#: What the UI is allowed to say about this source. "Connected" is not on the list,
#: because the adapter has never connected to anything.
STATUS_BUILT_NO_CREDENTIALS = "built_awaiting_credentials"
STATUS_CREDENTIALS_PRESENT = "credentials_present_pending_approval"
STATUS_READY = "approved_and_credentialled"


def credential_status() -> str:
    """Which of the three preconditions are currently met.

    Reads the environment only. It never reports more than it can see, and it
    never treats an empty string as a credential.
    """
    missing = [key for key in CREDENTIAL_KEYS if not (os.environ.get(key) or "").strip()]
    if missing:
        return STATUS_BUILT_NO_CREDENTIALS
    return STATUS_CREDENTIALS_PRESENT


def status_label() -> str:
    """The sentence the UI shows. Deliberately not the word 'connected'."""
    if credential_status() == STATUS_BUILT_NO_CREDENTIALS:
        return "Built · not connected · awaiting official API credentials"
    return "Credentials present · awaiting a recorded source approval"


class MobileDeConnector(Connector):
    """The official-API adapter for mobile.de listings."""

    source_id = "mobile_de"
    version = "0.1"
    category = "vehicle_listings"
    documentation_url = "https://services.mobile.de/manual/"

    # Held in the source register so it can be corrected from the published
    # specification without editing this module. Never defaulted to a guess.
    default_search_path = "/search-api/search"

    # ------------------------------------------------------------------ fetch

    def fetch(self, **kwargs: Any) -> AdapterResult:
        """Refuses, for whichever precondition is missing, and says which.

        The order matters. The policy gate runs first, because an unapproved
        source must be refused whether or not somebody has put credentials in the
        environment. Having a key is not the same as being allowed to use it.
        """
        self.require_available()

        if credential_status() == STATUS_BUILT_NO_CREDENTIALS:
            raise ConnectorUnavailable(
                "mobile.de requires official API credentials under a partner agreement. "
                f"Set {' and '.join(CREDENTIAL_KEYS)} in the environment. This adapter does "
                "not scrape the public site as an alternative, and it does not substitute "
                "sample data for the market."
            )

        source = self.policy.get(self.source_id)
        if source is None or not source.base_url:
            raise ConnectorUnavailable(
                "no base URL recorded for mobile.de; take it from the published API "
                "specification and record it in the source register rather than guessing "
                "an endpoint"
            )

        raise ConnectorUnavailable(
            "mobile.de access has not been exercised. Before the first live call, confirm "
            "the agreed request scope, rate limits and retention terms, and record them "
            "against the source register entry."
        )

    # ------------------------------------------------------------------ parse

    def parse(self, payload: Any, **kwargs: Any) -> AdapterResult:
        """Map a published mobile.de search payload into the internal contract.

        Written defensively on purpose. The published specification is the
        authority on field names, not this file, so every field is read through
        :func:`_first` with a short list of documented aliases, and anything the
        payload does not carry stays absent rather than being defaulted. A price
        with no currency, or a listing with no identifier, is reported as an error
        rather than repaired.
        """
        if not isinstance(payload, dict):
            return self.empty(errors=["payload is not a JSON object"], status="unparseable")

        ads = _first(payload, "ads", "searchResult", "items") or []
        if isinstance(ads, dict):
            ads = _first(ads, "ads", "items") or []
        if not isinstance(ads, list):
            return self.empty(errors=["no list of adverts in the payload"], status="unparseable")

        records = []
        errors: list[str] = []
        now = self.clock.now()

        for index, ad in enumerate(ads):
            if not isinstance(ad, dict):
                errors.append(f"advert {index} is not an object")
                continue

            external_id = _text(_first(ad, "mobileAdId", "@key", "adId", "id"))
            if not external_id:
                errors.append(f"advert {index} has no identifier; skipped rather than invented")
                continue

            price_block = _first(ad, "price") or {}
            amount = _number(_first(price_block, "consumerPriceGross", "grossAmount", "amount"))
            currency = _text(_first(price_block, "currency", "currencyCode"))
            net_amount = _number(_first(price_block, "consumerPriceNet", "netAmount"))
            vat_rate = _number(_first(price_block, "vatRate"))
            vat_deductible = _first(price_block, "vatDeductible", "vatReclaimable")

            fields: dict[str, Any] = {
                "external_id": external_id,
                "seller_name": _text(_first(ad, "sellerName", "dealerName")),
                "listing_url": _text(_first(ad, "detailPageUrl", "url", "adUrl")),
                "make": _text(_first(ad, "make", "makeName")),
                "model": _text(_first(ad, "model", "modelName")),
                "model_description": _text(_first(ad, "modelDescription", "title")),
                "mileage_km": _number(_first(ad, "mileage", "mileageInKm")),
                "first_registration": _registration(_first(ad, "firstRegistration")),
                "location_country": _text(_first(ad, "country", "countryCode")),
            }

            # Price basis is derived from stated VAT fields, never assumed. An advert
            # that does not say leaves the basis unknown, which blocks a comparison
            # downstream rather than quietly entering one.
            if amount is not None and currency:
                fields["price_amount"] = amount
                fields["price_currency"] = currency
                if vat_deductible is True and net_amount is not None:
                    fields["price_basis"] = "net"
                    fields["price_amount"] = net_amount
                    fields["vat_regime"] = "standard"
                elif vat_deductible is False:
                    fields["price_basis"] = "gross"
                    fields["vat_regime"] = "margin"
                else:
                    fields["price_basis"] = "unknown"
                    fields["vat_regime"] = "unknown"
                if vat_rate is not None:
                    fields["vat_rate"] = vat_rate
            elif amount is not None and not currency:
                errors.append(
                    f"advert {external_id} states a price with no currency; "
                    f"recorded without a price rather than assuming euros"
                )

            # An advert is a supply asking price. It is never a completed sale, and
            # it is never blended with retail evidence downstream.
            fields["price_evidence_type"] = "supply_asking"

            fields = {key: value for key, value in fields.items() if value not in (None, "")}

            records.append(
                self.record(
                    external_record_id=external_id,
                    entity_type="offer",
                    observed_at=_timestamp(_first(ad, "creationDate", "modificationDate")) or now,
                    fields=fields,
                    confidence=Confidence.OBSERVED_PUBLIC,
                    attribution=ATTRIBUTION,
                    evidence=[
                        {
                            "field": key,
                            "value": str(value),
                            "source_id": self.source_id,
                            "external_record_id": external_id,
                        }
                        for key, value in fields.items()
                    ],
                )
            )

        return AdapterResult(
            source_id=self.source_id,
            connector_version=self.version,
            records=records,
            next_cursor=_text(_first(payload, "nextPageCursor", "cursor")),
            errors=errors,
            attribution=ATTRIBUTION,
            fetch_status="ok",
        )


# -- small readers, all of which prefer absence to a guess ------------------


def _first(block: Any, *names: str) -> Any:
    if not isinstance(block, dict):
        return None
    for name in names:
        if name in block and block[name] is not None:
            return block[name]
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _registration(value: Any) -> str | None:
    """mobile.de states first registration as ``YYYY-MM``; a day is not invented."""
    text = _text(value)
    if not text:
        return None
    parts = text.split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}-{parts[1].zfill(2)}-01"
    return text


def _timestamp(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


__all__ = [
    "ATTRIBUTION",
    "CREDENTIAL_KEYS",
    "MobileDeConnector",
    "credential_status",
    "status_label",
]
