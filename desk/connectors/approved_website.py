"""Optional approved-domain website adapter.

Hard limits, all enforced here:

* only explicitly reviewed hosts and path prefixes, from the source register;
* at most five approved URLs per business - an application limit, not a statement
  that the provider permits anything;
* no crawling: it reads only the URLs it is given, and follows no links;
* no script execution, and a bounded response size;
* a login, CAPTCHA or block stops the adapter rather than being worked around.

Successful HTTP access is never treated as permission. A fleet photograph, a stock
image or a follower count is not evidence of ownership or demand.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from ..config import Confidence
from ..models import AdapterRecord, AdapterResult
from .base import Connector, ConnectorUnavailable

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"\+?\d[\d\s().-]{7,}\d")

# Kept short so nothing resembling a full page copy is retained.
MAX_EXCERPT_CHARS = 200


class ApprovedWebsiteConnector(Connector):
    source_id = "approved_website"
    version = "0.1"
    category = "company_discovery"
    documentation_url = ""

    def fetch(
        self, *, company_external_id: str, urls: Sequence[str], **kwargs: Any
    ) -> AdapterResult:
        """Read up to five explicitly approved pages for one business."""
        # Argument validation first: a malformed request is refused whatever the
        # mode, and without consulting the policy.
        if len(urls) > self.config.max_approved_urls_per_business:
            return self.empty(
                errors=[
                    f"{len(urls)} URLs requested; this adapter reads at most "
                    f"{self.config.max_approved_urls_per_business} approved pages per business"
                ],
                status="invalid_request",
            )

        self.require_available()

        records: list[AdapterRecord] = []
        errors: list[str] = []
        may_retain = self.policy.may_retain_raw(self.source_id).allowed

        for url in urls:
            try:
                response = self.fetcher.get(self.source_id, url, accept="text/html")
            except ConnectorUnavailable as exc:
                errors.append(f"{url}: {exc}")
                continue
            except PermissionError as exc:
                errors.append(f"{url}: {exc}")
                continue

            parsed = self.parse(
                response.text,
                url=url,
                company_external_id=company_external_id,
                raw_hash=response.content_hash if may_retain else None,
            )
            records.extend(parsed.records)
            errors.extend(parsed.errors)

        return AdapterResult(
            source_id=self.source_id,
            connector_version=self.version,
            records=records,
            errors=errors,
        )

    def parse(
        self,
        payload: Any,
        *,
        url: str | None = None,
        company_external_id: str | None = None,
        raw_hash: str | None = None,
        **kwargs: Any,
    ) -> AdapterResult:
        """Extract published business facts, with a field-level evidence trail."""
        text = _visible_text(str(payload))

        emails = sorted(set(EMAIL_PATTERN.findall(text)))[:3]
        phones = sorted({_clean_phone(match) for match in PHONE_PATTERN.findall(text)})[:3]

        fields: dict[str, Any] = {
            "external_id": company_external_id,
            "contact_email": emails[0] if emails else None,
            "contact_phone": phones[0] if phones else None,
            "contact_type": "published_business" if (emails or phones) else "unknown",
            # A discovered address never becomes permitted use.
            "contact_policy_status": "review_required",
            "notes": (
                "extracted from an explicitly approved page; a published address is not consent "
                "to contact, and the page was not crawled beyond this URL"
            ),
        }

        evidence = []
        for key, value in fields.items():
            if value is None or key in {"notes", "contact_policy_status", "external_id"}:
                continue
            evidence.append(
                {
                    "field_name": key,
                    "value_text": str(value),
                    "url": url,
                    "excerpt": _excerpt(text, str(value)),
                    "confidence": Confidence.OBSERVED_PUBLIC.value,
                }
            )

        record = self.record(
            external_record_id=f"{company_external_id}@{url}",
            entity_type="contact",
            observed_at=self.clock.now(),
            fields=fields,
            evidence=evidence,
            raw_reference=url,
            raw_hash=raw_hash,
            confidence=Confidence.OBSERVED_PUBLIC,
        )
        return AdapterResult(
            source_id=self.source_id, connector_version=self.version, records=[record]
        )


def _visible_text(html: str) -> str:
    """Strip markup, preferring a real parser when the optional extra is installed."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-untyped]
    except ImportError:
        cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def _clean_phone(raw: str) -> str:
    return re.sub(r"[^\d+]", "", raw)


def _excerpt(text: str, needle: str) -> str:
    index = text.find(needle)
    if index < 0:
        return needle[:MAX_EXCERPT_CHARS]
    start = max(0, index - 60)
    return text[start : start + MAX_EXCERPT_CHARS]
