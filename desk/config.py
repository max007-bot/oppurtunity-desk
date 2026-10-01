"""Application configuration and the controlled vocabularies the domain uses.

Defaults are deliberately the safe ones: demo mode, no network, no runtime AI.
Every threshold here is a product assumption to be tested, not a market or legal
standard, and each is configurable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Mode(str, Enum):
    DEMO = "demo"
    LIVE = "live"


# -- controlled vocabularies --------------------------------------------


class Confidence(str, Enum):
    """How a stored fact came to be believed. Not a probability."""

    OBSERVED_PUBLIC = "observed_public"
    SELLER_CLAIMED = "seller_claimed"
    BUYER_CONFIRMED = "buyer_confirmed"
    COMPANY_CONFIRMED = "company_confirmed"
    DERIVED = "derived"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"


class OfferStatus(str, Enum):
    UNKNOWN = "unknown"
    ADVERTISED_AVAILABLE = "advertised_available"
    AVAILABILITY_CONFIRMED = "availability_confirmed"
    RESERVED = "reserved"
    UNAVAILABLE = "unavailable"
    ALLOCATION = "allocation"


class ObservationState(str, Enum):
    """What the latest check saw. Separate from the seller's own status claim."""

    SEEN = "seen"
    NOT_SEEN = "not_seen"
    FETCH_FAILED = "fetch_failed"


class StockKind(str, Enum):
    PHYSICAL_STOCK = "physical_stock"
    ALLOCATION = "allocation"
    UNKNOWN = "unknown"


class PriceBasis(str, Enum):
    NET = "net"
    GROSS = "gross"
    UNKNOWN = "unknown"


class VatRegime(str, Enum):
    STANDARD = "standard"
    MARGIN = "margin"
    OTHER = "other"
    UNKNOWN = "unknown"


class PriceEvidenceType(str, Enum):
    """An asking price, a budget and a firm bid are different kinds of evidence."""

    RETAIL_ASKING = "retail_asking"
    SUPPLY_ASKING = "supply_asking"
    DEALER_BID = "dealer_bid"
    BUYER_BUDGET = "buyer_budget"
    COMPLETED_TRANSACTION = "completed_transaction"
    ANALYST_ASSUMPTION = "analyst_assumption"


class ContactPolicyStatus(str, Enum):
    UNKNOWN = "unknown"
    REVIEW_REQUIRED = "review_required"
    PERMITTED_FOR_SCOPE = "permitted_for_scope"
    DENIED = "denied"
    EXPIRED = "expired"


class FitStatus(str, Enum):
    NO_MATCH = "no_match"
    NEEDS_VERIFICATION = "needs_verification"
    SPECIFICATION_FIT = "specification_fit"
    CATEGORY_FIT_PROSPECT = "category_fit_prospect"
    BRIEF_EXPIRED = "brief_expired"


class CompanyCategory(str, Enum):
    DEALER = "dealer"
    FLEET = "fleet"
    RENTAL = "rental"
    CHAUFFEUR = "chauffeur"
    BROKER = "broker"
    REFERRAL_PARTNER = "referral_partner"
    UNKNOWN = "unknown"


class BuyingRoute(str, Enum):
    BUY_FOR_STOCK = "buy_for_stock"
    BUY_AGAINST_ORDER = "buy_against_order"
    LEASE = "lease"
    BROKER_ONLY = "broker_only"
    REFERRAL_ONLY = "referral_only"
    UNKNOWN = "unknown"


class ScenarioStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class DraftStatus(str, Enum):
    INTERNAL_RESEARCH = "internal_research"
    CONTACT_READY = "contact_ready"
    BLOCKED = "blocked"


class SourceAccessMode(str, Enum):
    FIXTURE = "fixture"
    FILE_IMPORT = "file_import"
    HUMAN_ENTRY = "human_entry"
    PUBLIC_API = "public_api"
    LICENSED_API = "licensed_api"
    APPROVED_WEBSITE = "approved_website"


class SourceStatus(str, Enum):
    APPROVED = "approved"
    REVIEW_REQUIRED = "review_required"
    EXPIRED = "expired"
    BLOCKED = "blocked"


# Outcomes a human records after an actual conversation. The application never
# infers these from opens, clicks or silence.
INTERACTION_OUTCOMES = (
    "no_answer",
    "wrong_role",
    "no_fit",
    "requirement_captured",
    "requested_dossier",
    "meeting_booked",
    "meeting_held",
    "offer",
    "deposit",
    "delivered",
    "lost",
    "objection",
)


@dataclass(frozen=True)
class Freshness:
    """Prototype staleness windows. Product assumptions, not standards."""

    supply_hours: float = 24
    comparable_days: float = 14
    brief_days: float = 30
    contact_days: float = 90


@dataclass(frozen=True)
class Config:
    mode: Mode = Mode.DEMO
    demo_db: Path = PROJECT_ROOT / "data" / "demo.sqlite"
    live_db: Path = PROJECT_ROOT / "data" / "live.sqlite"
    timezone: str = "Europe/Prague"
    allow_network: bool = False
    ai_enabled: bool = False
    ai_provider: str = ""
    ai_model: str = ""
    user_agent: str = "luxury-car-opportunity-desk/0.1 (local research prototype)"
    overpass_endpoint: str = "https://overpass-api.de/api/interpreter"
    freshness: Freshness = field(default_factory=Freshness)
    base_currency: str = "EUR"

    # Conservative prototype guards, all configurable.
    min_comparables_for_median: int = 3
    max_approved_urls_per_business: int = 5
    max_price_eur: int = 5_000_000
    max_seats: int = 9
    max_mileage_km: int = 1_000_000
    request_timeout_seconds: float = 20.0
    max_response_bytes: int = 5_000_000

    @property
    def db_path(self) -> Path:
        return self.demo_db if self.mode is Mode.DEMO else self.live_db

    @property
    def is_demo(self) -> bool:
        return self.mode is Mode.DEMO

    def for_mode(self, mode: Mode) -> "Config":
        return replace(self, mode=mode)


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if not raw:
        return default
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _number(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_config(*, mode: str | None = None) -> Config:
    """Build configuration from the environment, falling back to safe defaults.

    A missing or unrecognised mode resolves to demo, never live.
    """
    raw_mode = (mode or os.environ.get("DESK_MODE") or Mode.DEMO.value).strip().lower()
    resolved = Mode.LIVE if raw_mode == Mode.LIVE.value else Mode.DEMO

    freshness = Freshness(
        supply_hours=_number("DESK_SUPPLY_STALE_HOURS", 24),
        comparable_days=_number("DESK_COMPARABLE_STALE_DAYS", 14),
        brief_days=_number("DESK_BRIEF_STALE_DAYS", 30),
        contact_days=_number("DESK_CONTACT_STALE_DAYS", 90),
    )

    return Config(
        mode=resolved,
        demo_db=_path("DESK_DEMO_DB", PROJECT_ROOT / "data" / "demo.sqlite"),
        live_db=_path("DESK_LIVE_DB", PROJECT_ROOT / "data" / "live.sqlite"),
        timezone=os.environ.get("DESK_TIMEZONE", "Europe/Prague"),
        # Demo mode can never reach the network regardless of the flag.
        allow_network=_flag("DESK_ALLOW_NETWORK", False) and resolved is Mode.LIVE,
        ai_enabled=_flag("DESK_AI_ENABLED", False),
        ai_provider=os.environ.get("DESK_AI_PROVIDER", ""),
        ai_model=os.environ.get("DESK_AI_MODEL", ""),
        user_agent=os.environ.get(
            "DESK_USER_AGENT", "luxury-car-opportunity-desk/0.1 (local research prototype)"
        ),
        overpass_endpoint=os.environ.get(
            "DESK_OVERPASS_ENDPOINT", "https://overpass-api.de/api/interpreter"
        ),
        freshness=freshness,
    )
