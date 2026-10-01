"""French company identity via API Recherche d'entreprises.

Deliberately a stub with a real parser and no live route enabled. Two points that
are easy to get wrong are recorded here:

* this is the public business-search API, which is *not* the separately restricted
  API Entreprise; the two must not be confused;
* diffusion restrictions apply to some records, so a restricted record is skipped
  rather than ingested.

Only the fields listed in ``supported_fields`` are taken. The current response
schema, pagination and rate limits must be checked against the live documentation
before this is enabled.

Explanation: https://annuaire-entreprises.data.gouv.fr/donnees/api-entreprises
Documentation: https://recherche-entreprises.api.gouv.fr/docs/
"""

from __future__ import annotations

from typing import Any

from ..config import Confidence
from ..models import AdapterRecord, AdapterResult
from .base import Connector, ConnectorUnavailable

ATTRIBUTION = (
    "Data from API Recherche d'entreprises (data.gouv.fr). Public business search only; "
    "not API Entreprise."
)


class FranceConnector(Connector):
    source_id = "fr_recherche_entreprises"
    version = "0.1"
    category = "identity_verification"
    documentation_url = "https://recherche-entreprises.api.gouv.fr/docs/"

    def fetch(self, *, query: str, page: int = 1, **kwargs: Any) -> AdapterResult:
        self.require_available()
        source = self.policy.get(self.source_id)
        if source is None or not source.base_url:
            raise ConnectorUnavailable(
                "no base URL recorded for the French search API; check the current documentation "
                "and record the route and its limits in the source register first"
            )
        try:
            response = self.fetcher.get(
                self.source_id,
                f"{source.base_url.rstrip('/')}/search",
                params={"q": query, "page": page},
            )
        except ConnectorUnavailable as exc:
            return self.empty(errors=[str(exc)], status="unavailable")
        return self.parse(response.json(), raw_hash=response.content_hash)

    def parse(self, payload: Any, *, raw_hash: str | None = None, **kwargs: Any) -> AdapterResult:
        records: list[AdapterRecord] = []
        errors: list[str] = []
        if not isinstance(payload, dict):
            return self.empty(errors=["unexpected payload shape"], status="parse_error")

        for result in payload.get("results", []) or []:
            siren = _text(result.get("siren"))
            if not siren:
                continue

            # Some records carry diffusion restrictions; those are not ingested.
            if str(result.get("statut_diffusion", "")).upper() in {"P", "N"}:
                errors.append(
                    f"{siren}: record carries a diffusion restriction and was not ingested"
                )
                continue

            siege = result.get("siege") or {}
            fields: dict[str, Any] = {
                "external_id": f"siren/{siren}",
                "legal_name": _text(result.get("nom_raison_sociale"))
                or _text(result.get("nom_complet")),
                "registry_id": siren,
                "registry_verified": True,
                "country": "FR",
                "city": _text(siege.get("libelle_commune")),
                "address": _text(siege.get("adresse")),
                "primary_activity": _text(result.get("activite_principale")),
                "notes": (
                    "French public business-search identity. Registry identity only; it implies "
                    "no buying intent and no contact permission."
                ),
            }
            records.append(
                self.record(
                    external_record_id=f"siren/{siren}",
                    entity_type="company",
                    observed_at=self.clock.now(),
                    fields=fields,
                    evidence=[
                        {
                            "field_name": key,
                            "value_text": str(value),
                            "confidence": Confidence.OBSERVED_PUBLIC.value,
                        }
                        for key, value in fields.items()
                        if value not in (None, "", False) and key != "notes"
                    ],
                    raw_hash=raw_hash,
                    confidence=Confidence.OBSERVED_PUBLIC,
                    attribution=ATTRIBUTION,
                )
            )

        return AdapterResult(
            source_id=self.source_id,
            connector_version=self.version,
            records=records,
            errors=errors,
            next_cursor=_next_page(payload),
            attribution=ATTRIBUTION,
        )

    def supported_fields(self) -> list[str]:
        return [
            "siren",
            "nom_raison_sociale",
            "nom_complet",
            "activite_principale",
            "statut_diffusion (used to skip restricted records)",
            "siege.adresse",
            "siege.libelle_commune",
        ]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _next_page(payload: dict[str, Any]) -> str | None:
    page = payload.get("page")
    total = payload.get("total_pages")
    if isinstance(page, int) and isinstance(total, int) and page < total:
        return str(page + 1)
    return None
