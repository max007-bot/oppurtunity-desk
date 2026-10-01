"""The common connector contract and the only HTTP path in the project.

Every connector returns an ``AdapterResult``. None of them decides buying intent,
sets a contact policy, sends a message or overwrites a reviewed fact. All network
access goes through ``HttpFetcher``, which refuses to move until
``SourcePolicy.may_fetch`` has approved the source, the scope and the URL.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

from ..clock import Clock
from ..config import Config
from ..models import AdapterRecord, AdapterResult
from ..services.source_policy import PolicyDenied, SourcePolicy, check_address


class ConnectorUnavailable(RuntimeError):
    """Raised when a connector has no permitted route.

    Reported to the user as-is. It is never a reason to fall back to scraping, to
    guess an endpoint or to invent credentials.
    """


@dataclass
class FetchResponse:
    status_code: int
    text: str
    headers: Mapping[str, str]
    url: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:32]

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class HttpFetcher:
    """Bounded, policy-gated HTTP access."""

    def __init__(self, policy: SourcePolicy, config: Config, clock: Clock) -> None:
        self.policy = policy
        self.config = config
        self.clock = clock

    def get(
        self,
        source_id: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        accept: str = "application/json",
    ) -> FetchResponse:
        return self._request(source_id, "GET", url, params=params, accept=accept)

    def post(
        self,
        source_id: str,
        url: str,
        *,
        data: Any = None,
        accept: str = "application/json",
        content_type: str = "text/plain; charset=utf-8",
    ) -> FetchResponse:
        return self._request(
            source_id, "POST", url, data=data, accept=accept, content_type=content_type
        )

    def _request(
        self,
        source_id: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Any = None,
        accept: str = "application/json",
        content_type: str | None = None,
    ) -> FetchResponse:
        decision = self.policy.may_fetch(source_id, url)
        if not decision.allowed:
            raise PolicyDenied(decision.reason)

        import httpx

        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": accept,
            "Accept-Encoding": "gzip",
        }
        if content_type and data is not None:
            headers["Content-Type"] = content_type

        source = self.policy.get(source_id)
        timeout = httpx.Timeout(
            self.config.request_timeout_seconds,
            connect=min(10.0, self.config.request_timeout_seconds),
        )

        # Redirects are followed manually so every hop is re-checked: a redirect
        # must not be able to turn an approved fetch into a local request.
        current = url
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            for hop in range(4):
                response = client.request(
                    method, current, params=params, content=data, headers=headers
                )

                if response.status_code in (429, 503):
                    retry_after = response.headers.get("Retry-After")
                    self.policy.record_fetch(source_id, f"throttled:{response.status_code}")
                    raise ConnectorUnavailable(
                        f"{source.name if source else source_id} is throttling requests "
                        f"(HTTP {response.status_code}"
                        + (f", Retry-After {retry_after}" if retry_after else "")
                        + "). Backing off; identities are never rotated to defeat a limit."
                    )

                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise ConnectorUnavailable("redirect without a Location header")
                    current = str(httpx.URL(current).join(location))
                    hop_decision = self.policy.check_url(source, current) if source else None
                    if hop_decision is not None and not hop_decision.allowed:
                        raise PolicyDenied(
                            f"redirect to {current} blocked: {hop_decision.reason}"
                        )
                    continue

                if response.status_code in (401, 403):
                    self.policy.record_fetch(source_id, f"denied:{response.status_code}")
                    raise ConnectorUnavailable(
                        f"access denied (HTTP {response.status_code}). Stopping: the prototype "
                        f"does not work around a login, a CAPTCHA or a block."
                    )

                body = response.text
                if len(body.encode("utf-8", errors="ignore")) > self.config.max_response_bytes:
                    raise ConnectorUnavailable(
                        f"response exceeded the {self.config.max_response_bytes} byte limit"
                    )

                response.raise_for_status()
                self.policy.record_fetch(source_id, "ok")
                return FetchResponse(
                    status_code=response.status_code,
                    text=body,
                    headers=dict(response.headers),
                    url=str(response.url),
                )

        raise ConnectorUnavailable("too many redirects")


class Connector(ABC):
    """Base class for every adapter."""

    source_id: str = ""
    version: str = "0.1"
    category: str = "mixed"
    # Documentation must be read and the scope recorded before live use.
    documentation_url: str = ""

    def __init__(
        self,
        *,
        policy: SourcePolicy,
        config: Config,
        clock: Clock,
        fetcher: HttpFetcher | None = None,
    ) -> None:
        self.policy = policy
        self.config = config
        self.clock = clock
        self.fetcher = fetcher or HttpFetcher(policy, config, clock)

    @abstractmethod
    def fetch(self, **kwargs: Any) -> AdapterResult:  # pragma: no cover - interface
        ...

    @abstractmethod
    def parse(self, payload: Any, **kwargs: Any) -> AdapterResult:  # pragma: no cover
        """Parse a provider payload into the internal contract.

        Kept separate from ``fetch`` so adapters can be tested against saved
        fixture responses with no internet.
        """

    # -- helpers --------------------------------------------------------
    def availability(self) -> str:
        """Human-readable statement of whether this connector can run right now."""
        decision = self.policy.may_fetch(self.source_id)
        if decision.allowed:
            return f"available: {decision.reason}"
        return f"unavailable: {decision.reason}"

    def require_available(self) -> None:
        decision = self.policy.may_fetch(self.source_id)
        if not decision.allowed:
            raise ConnectorUnavailable(decision.reason)

    def record(self, **kwargs: Any) -> AdapterRecord:
        kwargs.setdefault("source_id", self.source_id)
        kwargs.setdefault("fetched_at", self.clock.now())
        return AdapterRecord(**kwargs)

    def empty(self, *, errors: list[str] | None = None, status: str = "ok") -> AdapterResult:
        return AdapterResult(
            source_id=self.source_id,
            connector_version=self.version,
            records=[],
            errors=errors or [],
            fetch_status=status,
        )


__all__ = [
    "Connector",
    "ConnectorUnavailable",
    "FetchResponse",
    "HttpFetcher",
    "check_address",
]
