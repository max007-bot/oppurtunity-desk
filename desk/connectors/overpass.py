"""OpenStreetMap company discovery via the Overpass API.

Scope and limitations, stated in the interface rather than hidden:

* it discovers car sellers and rentals in a small selected area only;
* OSM coverage is incomplete, and "car seller" does not mean "luxury buyer";
* attribution and licence metadata are preserved on every record;
* queries are bounded by a bounding box and a result cap. Repeated
  continent-wide queries are not supported here - a large job belongs in an
  extraction service, not in this adapter.

Documentation: https://wiki.openstreetmap.org/wiki/Overpass_API
Licence: https://www.openstreetmap.org/copyright
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Confidence
from ..models import AdapterRecord, AdapterResult
from .base import Connector, ConnectorUnavailable

ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)"

# Tags the adapter looks for. Anything else is out of scope.
SUPPORTED_TAGS = (("shop", "car"), ("amenity", "car_rental"))

# A bounding box larger than this is refused: it is an application limit that
# keeps queries bounded, not a statement about what the provider would allow.
MAX_BBOX_DEGREES = 1.5
MAX_RESULTS = 200


@dataclass(frozen=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float

    def validate(self) -> None:
        if self.south >= self.north or self.west >= self.east:
            raise ValueError("bounding box corners are in the wrong order")
        if (self.north - self.south) > MAX_BBOX_DEGREES or (self.east - self.west) > MAX_BBOX_DEGREES:
            raise ValueError(
                f"bounding box exceeds the {MAX_BBOX_DEGREES} degree limit; select a smaller area "
                f"or use an extraction service for a large job"
            )

    def as_overpass(self) -> str:
        return f"{self.south},{self.west},{self.north},{self.east}"


class OverpassConnector(Connector):
    source_id = "osm_overpass"
    version = "0.1"
    category = "company_discovery"
    documentation_url = "https://wiki.openstreetmap.org/wiki/Overpass_API"

    def build_query(self, bbox: BoundingBox, *, timeout_seconds: int = 25) -> str:
        """Build a bounded Overpass QL query for car sellers and rentals."""
        bbox.validate()
        area = bbox.as_overpass()
        clauses = "\n".join(
            f'  nwr["{key}"="{value}"]({area});' for key, value in SUPPORTED_TAGS
        )
        return f"[out:json][timeout:{timeout_seconds}];\n(\n{clauses}\n);\nout tags center {MAX_RESULTS};"

    def fetch(self, *, bbox: BoundingBox, **kwargs: Any) -> AdapterResult:
        """Run a live bounded query. Refuses unless the source is approved."""
        self.require_available()
        query = self.build_query(bbox)
        try:
            response = self.fetcher.post(
                self.source_id,
                self.config.overpass_endpoint,
                data=query.encode("utf-8"),
            )
        except ConnectorUnavailable as exc:
            return self.empty(errors=[str(exc)], status="unavailable")
        return self.parse(response.json(), raw_hash=response.content_hash)

    def parse(self, payload: Any, *, raw_hash: str | None = None, **kwargs: Any) -> AdapterResult:
        """Map an Overpass JSON response onto company candidate records."""
        records: list[AdapterRecord] = []
        errors: list[str] = []
        elements = payload.get("elements", []) if isinstance(payload, dict) else []
        generated = (payload.get("osm3s", {}) or {}).get("timestamp_osm_base")
        observed_at = generated or self.clock.now()

        for element in elements:
            tags = element.get("tags") or {}
            name = tags.get("name")
            if not name:
                # An unnamed object is not a usable company candidate.
                continue

            osm_type = element.get("type")
            osm_id = element.get("id")
            if osm_type is None or osm_id is None:
                errors.append("element without a type or id was skipped")
                continue

            category = "rental" if tags.get("amenity") == "car_rental" else "dealer"
            address = _address(tags)

            fields: dict[str, Any] = {
                "external_id": f"{osm_type}/{osm_id}",
                "legal_name": name,
                "country": (tags.get("addr:country") or "").upper() or None,
                "city": tags.get("addr:city"),
                "address": address,
                "website": tags.get("website") or tags.get("contact:website"),
                "contact_phone": tags.get("phone") or tags.get("contact:phone"),
                "contact_email": tags.get("email") or tags.get("contact:email"),
                "category": category,
                # Discovery never sets a buying route or a contact policy.
                "buying_route": "unknown",
                "buying_authority": "unknown",
                "notes": (
                    "OpenStreetMap discovery candidate. Coverage is incomplete and a car seller "
                    "is not necessarily a luxury buyer; this is a research lead, not a buyer."
                ),
                "osm_type": osm_type,
                "osm_id": osm_id,
                "latitude": element.get("lat") or (element.get("center") or {}).get("lat"),
                "longitude": element.get("lon") or (element.get("center") or {}).get("lon"),
            }

            evidence = [
                {
                    "field_name": key,
                    "value_text": str(value),
                    "url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                    "confidence": Confidence.OBSERVED_PUBLIC.value,
                }
                for key, value in fields.items()
                if value is not None and key not in {"notes", "buying_route", "buying_authority"}
            ]

            records.append(
                self.record(
                    external_record_id=f"{osm_type}/{osm_id}",
                    entity_type="company",
                    observed_at=observed_at,
                    fields=fields,
                    evidence=evidence,
                    raw_reference=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
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
            attribution=ATTRIBUTION,
        )


def _address(tags: dict[str, Any]) -> str | None:
    parts = [
        " ".join(
            part for part in [tags.get("addr:street"), tags.get("addr:housenumber")] if part
        ),
        tags.get("addr:postcode"),
        tags.get("addr:city"),
    ]
    joined = ", ".join(part for part in parts if part)
    return joined or None
