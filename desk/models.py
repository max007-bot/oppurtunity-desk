"""Pydantic validation for imported rows and connector results.

These models are the boundary: anything arriving from a file, a fixture or an API
is validated here before it reaches a repository. Validation failures carry the
row reference so the import preview can show them per row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .config import (
    BuyingRoute,
    CompanyCategory,
    Confidence,
    ContactPolicyStatus,
    OfferStatus,
    PriceBasis,
    PriceEvidenceType,
    StockKind,
    VatRegime,
)
from .money import Money, MoneyError, parse_money

# Sensible upper bounds. Configurable product guards, not market limits.
MAX_PRICE_MINOR = 5_000_000_00
MAX_MILEAGE_KM = 1_000_000
MAX_SEATS = 9


class DeskModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# -- shared field helpers -----------------------------------------------


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, "", "unknown"):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class MoneyIn(DeskModel):
    """A money amount supplied by a source, keeping the raw text beside it."""

    raw_text: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    locale: Literal["eu", "uk"] | None = None

    @model_validator(mode="after")
    def _require_something(self) -> "MoneyIn":
        if self.amount is None and not self.raw_text:
            raise ValueError("supply either a numeric amount or the raw price text")
        return self

    def to_money(self, *, default_currency: str = "EUR") -> Money:
        if self.amount is not None:
            code = (self.currency or default_currency).upper()
            return Money.from_decimal(self.amount, code)
        return parse_money(self.raw_text or "", currency=self.currency, locale=self.locale)


# -- import row models --------------------------------------------------


class CompanyRow(DeskModel):
    """One row of a ``companies`` import."""

    external_id: str = Field(min_length=1, max_length=120)
    legal_name: str = Field(min_length=1, max_length=300)
    trading_name: str | None = None
    country: str = Field(min_length=2, max_length=2)
    region: str | None = None
    city: str | None = None
    address: str | None = None
    registry_id: str | None = None
    registry_verified: bool = False
    website: str | None = None
    category: CompanyCategory = CompanyCategory.UNKNOWN
    is_referral_partner: bool = False
    buying_route: BuyingRoute = BuyingRoute.UNKNOWN
    buying_authority: str = "unknown"
    network_claim: str | None = None
    introduction_notes: str | None = None
    parent_external_id: str | None = None
    is_branch: bool = False
    notes: str | None = None
    # Published business contact, if any. Absent stays absent.
    contact_name: str | None = None
    contact_role: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_type: Literal["published_business", "switchboard", "web_form", "unknown"] = "unknown"

    @field_validator("country")
    @classmethod
    def _upper_country(cls, value: str) -> str:
        return value.upper()

    @field_validator("registry_verified", mode="before")
    @classmethod
    def _bool_like(cls, value: Any) -> Any:
        return _coerce_bool(value)

    @model_validator(mode="after")
    def _registry_claim(self) -> "CompanyRow":
        if self.registry_verified and not self.registry_id:
            raise ValueError("registry_verified requires a registry_id")
        return self

    @property
    def has_contact(self) -> bool:
        return any([self.contact_name, self.contact_email, self.contact_phone, self.contact_role])


class OfferRow(DeskModel):
    """One row of an ``offers`` import: a seller's offer of one vehicle."""

    external_id: str = Field(min_length=1, max_length=120)
    model_family: str = Field(min_length=1, max_length=120)
    variant: str | None = None
    generation: str | None = None
    vin: str | None = None
    vin_verified: bool = False
    model_year: int | None = Field(default=None, ge=1900, le=2100)
    first_registration: datetime | None = None
    mileage_km: int | None = Field(default=None, ge=0, le=MAX_MILEAGE_KM)
    powertrain: str | None = None
    steering: Literal["lhd", "rhd", "unknown"] = "unknown"
    seats: int | None = Field(default=None, ge=1, le=MAX_SEATS)
    specification: dict[str, Any] = Field(default_factory=dict)

    seller_name: str | None = None
    seller_external_id: str | None = None
    listing_url: str | None = None

    price: MoneyIn | None = None
    price_basis: PriceBasis = PriceBasis.UNKNOWN
    vat_regime: VatRegime = VatRegime.UNKNOWN
    price_evidence_type: PriceEvidenceType = PriceEvidenceType.SUPPLY_ASKING

    status: OfferStatus = OfferStatus.UNKNOWN
    stock_kind: StockKind = StockKind.UNKNOWN
    location_country: str | None = None
    location_city: str | None = None
    authority_to_sell: Literal["unknown", "claimed", "confirmed", "denied"] = "unknown"
    available_from: datetime | None = None
    valid_until: datetime | None = None
    availability_confirmed_at: datetime | None = None
    observed_at: datetime
    notes: str | None = None

    @field_validator(
        "first_registration",
        "available_from",
        "valid_until",
        "availability_confirmed_at",
        "observed_at",
        mode="before",
    )
    @classmethod
    def _timestamps(cls, value: Any) -> Any:
        return _parse_timestamp(value)

    @field_validator("vin_verified", mode="before")
    @classmethod
    def _bool_like(cls, value: Any) -> Any:
        return _coerce_bool(value)

    @field_validator("vin")
    @classmethod
    def _vin_shape(cls, value: str | None) -> str | None:
        if value in (None, "", "unknown"):
            return None
        text = str(value).strip().upper()
        if len(text) != 17 or not text.isalnum():
            raise ValueError(
                "a VIN must be 17 alphanumeric characters; do not derive one from an advert id"
            )
        return text

    @model_validator(mode="after")
    def _consistency(self) -> "OfferRow":
        if self.vin_verified and not self.vin:
            raise ValueError("vin_verified requires a vin")
        if self.price is not None:
            money = self.price.to_money()
            if money.minor_units < 0:
                raise ValueError("a negative price is not a valid offer")
            if money.minor_units > MAX_PRICE_MINOR:
                raise ValueError("price exceeds the configured upper bound; review the source")
        if self.stock_kind is StockKind.ALLOCATION and self.status is OfferStatus.AVAILABILITY_CONFIRMED:
            raise ValueError(
                "an allocation cannot be recorded as confirmed physical availability"
            )
        return self


class ObservationRow(DeskModel):
    """A comparable or repeat observation of an offer."""

    external_id: str = Field(min_length=1, max_length=120)
    offer_external_id: str | None = None
    model_family: str | None = None
    variant: str | None = None
    vin: str | None = None
    price: MoneyIn | None = None
    price_basis: PriceBasis = PriceBasis.UNKNOWN
    vat_regime: VatRegime = VatRegime.UNKNOWN
    price_evidence_type: PriceEvidenceType = PriceEvidenceType.RETAIL_ASKING
    mileage_km: int | None = Field(default=None, ge=0, le=MAX_MILEAGE_KM)
    seats: int | None = Field(default=None, ge=1, le=MAX_SEATS)
    powertrain: str | None = None
    steering: Literal["lhd", "rhd", "unknown"] = "unknown"
    first_registration: datetime | None = None
    location_country: str | None = None
    status: OfferStatus = OfferStatus.UNKNOWN
    stock_kind: StockKind = StockKind.UNKNOWN
    state: Literal["seen", "not_seen", "fetch_failed"] = "seen"
    observed_at: datetime
    seller_name: str | None = None
    listing_url: str | None = None
    notes: str | None = None

    @field_validator("first_registration", "observed_at", mode="before")
    @classmethod
    def _timestamps(cls, value: Any) -> Any:
        return _parse_timestamp(value)


class BuyerBriefRow(DeskModel):
    """A human-recorded buying requirement."""

    external_id: str = Field(min_length=1, max_length=120)
    company_external_id: str = Field(min_length=1)
    contact_name: str | None = None
    model_family: str = Field(min_length=1)
    variant: str | None = None
    required_specs: dict[str, Any] = Field(default_factory=dict)
    preferred_specs: dict[str, Any] = Field(default_factory=dict)
    budget: MoneyIn | None = None
    budget_basis: PriceBasis = PriceBasis.UNKNOWN
    budget_vat_regime: VatRegime = VatRegime.UNKNOWN
    quantity: int = Field(default=1, ge=1, le=500)
    destination_country: str | None = None
    route: BuyingRoute = BuyingRoute.UNKNOWN
    required_by: datetime | None = None
    conversation_date: datetime
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    expires_at: datetime | None = None
    evidence_note: str | None = None

    @field_validator("required_by", "conversation_date", "confirmed_at", "expires_at", mode="before")
    @classmethod
    def _timestamps(cls, value: Any) -> Any:
        return _parse_timestamp(value)

    @model_validator(mode="after")
    def _confirmation_needs_person(self) -> "BuyerBriefRow":
        if self.confirmed_at and not self.confirmed_by:
            raise ValueError("a confirmed brief must record who confirmed it")
        return self


class InteractionRow(DeskModel):
    """A human-recorded conversation outcome."""

    external_id: str = Field(min_length=1, max_length=120)
    company_external_id: str = Field(min_length=1)
    channel: Literal["phone", "email", "post", "web_form", "in_person"]
    occurred_at: datetime
    outcome: str
    meeting_status: str | None = None
    notes: str | None = None
    recorded_by: str | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _timestamps(cls, value: Any) -> Any:
        return _parse_timestamp(value)

    @field_validator("outcome")
    @classmethod
    def _known_outcome(cls, value: str) -> str:
        from .config import INTERACTION_OUTCOMES

        text = value.strip().lower()
        if text not in INTERACTION_OUTCOMES:
            raise ValueError(f"outcome must be one of {', '.join(INTERACTION_OUTCOMES)}")
        return text


class ContactPolicyRow(DeskModel):
    """An explicit human decision about contacting a company on a channel."""

    company_external_id: str
    channel: Literal["phone", "email", "post", "web_form", "in_person"]
    purpose: str
    status: ContactPolicyStatus
    market: str | None = None
    basis: str | None = None
    restrictions: str | None = None
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("reviewed_at", "expires_at", mode="before")
    @classmethod
    def _timestamps(cls, value: Any) -> Any:
        return _parse_timestamp(value)

    @model_validator(mode="after")
    def _permission_needs_accountability(self) -> "ContactPolicyRow":
        if self.status is ContactPolicyStatus.PERMITTED_FOR_SCOPE:
            missing = [
                name
                for name, value in (
                    ("purpose", self.purpose),
                    ("basis", self.basis),
                    ("reviewer", self.reviewer),
                    ("reviewed_at", self.reviewed_at),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "permitted_for_scope requires a named channel, purpose, basis and "
                    f"reviewer; missing: {', '.join(missing)}"
                )
        return self


class SourceRow(DeskModel):
    """A source register entry. Credentials live in the environment, not here."""

    id: str
    name: str
    category: Literal[
        "company_discovery",
        "identity_verification",
        "vehicle_supply",
        "comparables",
        "human_demand",
        "mixed",
    ]
    access_mode: Literal[
        "fixture", "file_import", "human_entry", "public_api", "licensed_api", "approved_website"
    ]
    status: Literal["approved", "review_required", "expired", "blocked"]
    base_url: str | None = None
    documentation_url: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    approved_use: str | None = None
    attribution: str | None = None
    retention_rule: str | None = None
    retention_days: int | None = None
    raw_retention_allowed: bool = True
    export_allowed: bool = False
    rate_limit_per_minute: int | None = None
    max_concurrency: int = 1
    approval_evidence: str | None = None
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    review_due_at: datetime | None = None
    notes: str | None = None

    @field_validator("reviewed_at", "review_due_at", mode="before")
    @classmethod
    def _timestamps(cls, value: Any) -> Any:
        return _parse_timestamp(value)

    @model_validator(mode="after")
    def _approval_needs_reviewer(self) -> "SourceRow":
        if self.status == "approved" and not (self.reviewer and self.reviewed_at):
            raise ValueError("an approved source must record a reviewer and review date")
        return self


# -- connector contract -------------------------------------------------


class AdapterRecord(DeskModel):
    """One validated record returned by any connector.

    Different provider schemas map into this shape. A connector never decides
    buying intent, never sets a contact policy and never overwrites a reviewed
    fact directly.
    """

    source_id: str
    external_record_id: str
    entity_type: Literal["company", "offer", "observation", "contact", "brief"]
    observed_at: datetime
    fetched_at: datetime
    fields: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    raw_reference: str | None = None
    raw_hash: str | None = None
    confidence: Confidence = Confidence.OBSERVED_PUBLIC
    attribution: str | None = None

    @field_validator("observed_at", "fetched_at", mode="before")
    @classmethod
    def _timestamps(cls, value: Any) -> Any:
        return _parse_timestamp(value)


class AdapterResult(DeskModel):
    """The whole outcome of one connector call."""

    source_id: str
    connector_version: str
    records: list[AdapterRecord] = Field(default_factory=list)
    next_cursor: str | None = None
    errors: list[str] = Field(default_factory=list)
    attribution: str | None = None
    fetch_status: str = "ok"

    @property
    def ok(self) -> bool:
        return self.fetch_status == "ok" and not self.errors


# -- validation error reporting ----------------------------------------


class RowProblem(DeskModel):
    """A single row-level validation failure, shown in the import preview."""

    row_number: int
    external_id: str | None = None
    field: str | None = None
    message: str

    def render(self) -> str:
        where = f"row {self.row_number}"
        if self.external_id:
            where += f" ({self.external_id})"
        if self.field:
            where += f" field {self.field}"
        return f"{where}: {self.message}"


def _coerce_bool(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "y"}:
            return True
        if text in {"false", "no", "0", "n", ""}:
            return False
    return value


def validate_rows(model: type[DeskModel], rows: list[dict[str, Any]]) -> tuple[list[Any], list[RowProblem]]:
    """Validate a whole batch, collecting every row-level problem.

    Nothing is applied here: the caller decides what to do with a batch that has
    problems, and the default is to reject all of it.
    """
    valid: list[Any] = []
    problems: list[RowProblem] = []
    for index, row in enumerate(rows, start=1):
        external = row.get("external_id") or row.get("id")
        try:
            valid.append(model.model_validate(row))
        except MoneyError as exc:
            problems.append(
                RowProblem(row_number=index, external_id=_as_text(external), message=str(exc))
            )
        except Exception as exc:  # pydantic ValidationError and anything it wraps
            for problem in _explode(exc, index, _as_text(external)):
                problems.append(problem)
    return valid, problems


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _explode(exc: Exception, row_number: int, external_id: str | None) -> list[RowProblem]:
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return [RowProblem(row_number=row_number, external_id=external_id, message=str(exc))]
    out: list[RowProblem] = []
    for item in errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        out.append(
            RowProblem(
                row_number=row_number,
                external_id=external_id,
                field=location or None,
                message=item.get("msg", "invalid value"),
            )
        )
    return out
