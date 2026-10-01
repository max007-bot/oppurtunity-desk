"""Czech ARES registry verification.

Scope: verify a company that is already known, by its ICO. It returns the legal
identity, address and registry fields the response carries. It is a registry
connector - it does not prove that a company buys cars, and it is not an executive
email database.

The Ministry of Finance publishes operating conditions on request rate,
concurrency and repeated or random queries. Those conditions live in the source
register entry; this adapter refuses to run until that entry is approved, and the
field names below are parsed defensively because the published specification is
the authority, not this code.

Documentation: https://mf.gov.cz/cs/ministerstvo/informacni-systemy/ares
"""

from __future__ import annotations

import re
from typing import Any

from ..config import Confidence
from ..models import AdapterResult
from .base import Connector, ConnectorUnavailable

ATTRIBUTION = (
    "Data from ARES, Ministry of Finance of the Czech Republic. Registry identity only; "
    "no buying intent is implied."
)

ICO_PATTERN = re.compile(r"^\d{8}$")


class AresConnector(Connector):
    source_id = "cz_ares"
    version = "0.1"
    category = "identity_verification"
    documentation_url = "https://mf.gov.cz/cs/ministerstvo/informacni-systemy/ares"

    # The base path is held in the source register so it can be corrected from the
    # published specification without touching this module.
    default_path = "/ekonomicke-subjekty/"

    def fetch(self, *, ico: str, **kwargs: Any) -> AdapterResult:
        """Verify one known company by its ICO."""
        self.require_available()
        normalised = str(ico).strip()
        if not ICO_PATTERN.match(normalised):
            return self.empty(
                errors=[f"{ico!r} is not an eight-digit ICO; name search is a separate route"],
                status="invalid_request",
            )

        source = self.policy.get(self.source_id)
        if source is None or not source.base_url:
            raise ConnectorUnavailable(
                "no base URL recorded for ARES; load the current published API specification "
                "and record it in the source register rather than guessing an endpoint"
            )

        url = f"{source.base_url.rstrip('/')}{self.default_path}{normalised}"
        try:
            response = self.fetcher.get(self.source_id, url)
        except ConnectorUnavailable as exc:
            return self.empty(errors=[str(exc)], status="unavailable")
        return self.parse(response.json(), raw_hash=response.content_hash, url=url)

    def parse(
        self,
        payload: Any,
        *,
        raw_hash: str | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> AdapterResult:
        """Map an ARES economic-subject payload onto a company record.

        Field names are read defensively: an absent field stays absent rather than
        being filled from a similarly named one.
        """
        if not isinstance(payload, dict):
            return self.empty(errors=["unexpected ARES payload shape"], status="parse_error")

        subjects = payload.get("ekonomickeSubjekty")
        subject = subjects[0] if isinstance(subjects, list) and subjects else payload

        ico = _text(subject.get("ico"))
        if not ico:
            return self.empty(
                errors=["no ICO in the response; nothing is verified"], status="not_found"
            )

        legal_name = _text(subject.get("obchodniJmeno"))
        address_block = subject.get("sidlo") or {}
        address = _text(address_block.get("textovaAdresa"))
        city = _text(address_block.get("nazevObce"))
        country = (_text(address_block.get("kodStatu")) or "CZ").upper()[:2]

        fields: dict[str, Any] = {
            "external_id": f"ico/{ico}",
            "legal_name": legal_name,
            "registry_id": ico,
            # Verified means the registry returned this identity for this ICO.
            "registry_verified": bool(legal_name),
            "country": country,
            "city": city,
            "address": address,
            "legal_form": _text(subject.get("pravniForma")),
            "registered_from": _text(subject.get("datumVzniku")),
            "registered_until": _text(subject.get("datumZaniku")),
            "primary_activity": _activity(subject),
            "notes": (
                "ARES registry identity. This confirms who the company is, not that it buys "
                "vehicles and not who may be contacted."
            ),
        }

        evidence = [
            {
                "field_name": key,
                "value_text": str(value),
                "url": url,
                "confidence": Confidence.OBSERVED_PUBLIC.value,
            }
            for key, value in fields.items()
            if value not in (None, "", False) and key != "notes"
        ]

        record = self.record(
            external_record_id=f"ico/{ico}",
            entity_type="company",
            observed_at=self.clock.now(),
            fields=fields,
            evidence=evidence,
            raw_reference=url,
            raw_hash=raw_hash,
            confidence=Confidence.OBSERVED_PUBLIC,
            attribution=ATTRIBUTION,
        )
        return AdapterResult(
            source_id=self.source_id,
            connector_version=self.version,
            records=[record],
            attribution=ATTRIBUTION,
        )

    def supported_fields(self) -> list[str]:
        """What this adapter reads. Anything else is deliberately not ingested."""
        return [
            "ico",
            "obchodniJmeno",
            "pravniForma",
            "datumVzniku",
            "datumZaniku",
            "sidlo.textovaAdresa",
            "sidlo.nazevObce",
            "sidlo.kodStatu",
            "czNace (primary activity, first entry)",
        ]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _activity(subject: dict[str, Any]) -> str | None:
    nace = subject.get("czNace")
    if isinstance(nace, list) and nace:
        return _text(nace[0])
    return _text(nace)
