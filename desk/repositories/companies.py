"""Companies, contacts, contact policies, suppressions and buyer briefs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import from_iso
from ..config import ContactPolicyStatus
from ..db import new_id
from ..money import Money
from .base import dumps, exists, loads, money_from


# -- companies -----------------------------------------------------------


@dataclass
class Company:
    id: str
    legal_name: str
    country: str
    category: str
    buying_route: str
    trading_name: str | None = None
    city: str | None = None
    region: str | None = None
    address: str | None = None
    registry_id: str | None = None
    registry_verified: bool = False
    website: str | None = None
    parent_company_id: str | None = None
    is_branch: bool = False
    is_referral_partner: bool = False
    buying_authority: str = "unknown"
    network_claim: str | None = None
    review_status: str = "review_required"
    introduction_notes: str | None = None
    notes: str | None = None
    source_id: str | None = None
    external_record_id: str | None = None
    is_demo: bool = True

    @property
    def display_name(self) -> str:
        return self.trading_name or self.legal_name

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Company":
        return cls(
            id=row["id"],
            legal_name=row["legal_name"],
            country=row["country"],
            category=row["category"],
            buying_route=row["buying_route"],
            trading_name=row["trading_name"],
            city=row["city"],
            region=row["region"],
            address=row["address"],
            registry_id=row["registry_id"],
            registry_verified=bool(row["registry_verified"]),
            website=row["website"],
            parent_company_id=row["parent_company_id"],
            is_branch=bool(row["is_branch"]),
            is_referral_partner=bool(row["is_referral_partner"]),
            buying_authority=row["buying_authority"],
            network_claim=row["network_claim"],
            review_status=row["review_status"],
            introduction_notes=row["introduction_notes"],
            notes=row["notes"],
            source_id=row["source_id"],
            external_record_id=row["external_record_id"],
            is_demo=bool(row["is_demo"]),
        )


def find_company_by_external(
    conn: sqlite3.Connection, source_id: str, external_id: str
) -> Company | None:
    row = conn.execute(
        "SELECT * FROM companies WHERE source_id = ? AND external_record_id = ?",
        (source_id, external_id),
    ).fetchone()
    return None if row is None else Company.from_row(row)


def find_company_by_registry(
    conn: sqlite3.Connection, country: str, registry_id: str
) -> Company | None:
    """Country plus verified registration id is the strong company identity."""
    row = conn.execute(
        "SELECT * FROM companies WHERE country = ? AND registry_id = ? AND registry_verified = 1",
        (country.upper(), registry_id),
    ).fetchone()
    return None if row is None else Company.from_row(row)


def get_company(conn: sqlite3.Connection, company_id: str) -> Company | None:
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return None if row is None else Company.from_row(row)


def list_companies(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
    country: str | None = None,
    referral_only: bool | None = None,
) -> list[Company]:
    clauses: list[str] = []
    params: list[Any] = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if country:
        clauses.append("country = ?")
        params.append(country.upper())
    if referral_only is not None:
        clauses.append("is_referral_partner = ?")
        params.append(1 if referral_only else 0)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM companies {where} ORDER BY legal_name", params).fetchall()
    return [Company.from_row(row) for row in rows]


def upsert_company(
    conn: sqlite3.Connection,
    *,
    source_id: str | None,
    external_record_id: str | None,
    now: str,
    is_demo: bool,
    **fields: Any,
) -> tuple[str, bool]:
    """Insert or update a company keyed on (source_id, external_record_id).

    Returns the id and whether it was newly created, so an import can report
    created versus updated counts honestly.
    """
    existing = None
    if source_id and external_record_id:
        existing = find_company_by_external(conn, source_id, external_record_id)
    if existing is None and fields.get("registry_verified") and fields.get("registry_id"):
        existing = find_company_by_registry(conn, fields["country"], fields["registry_id"])

    payload = {
        "legal_name": fields["legal_name"],
        "trading_name": fields.get("trading_name"),
        "country": fields["country"].upper(),
        "region": fields.get("region"),
        "city": fields.get("city"),
        "address": fields.get("address"),
        "registry_id": fields.get("registry_id"),
        "registry_verified": 1 if fields.get("registry_verified") else 0,
        "website": fields.get("website"),
        "parent_company_id": fields.get("parent_company_id"),
        "is_branch": 1 if fields.get("is_branch") else 0,
        "category": fields.get("category", "unknown"),
        "is_referral_partner": 1 if fields.get("is_referral_partner") else 0,
        "buying_route": fields.get("buying_route", "unknown"),
        "buying_authority": fields.get("buying_authority", "unknown"),
        "network_claim": fields.get("network_claim"),
        "review_status": fields.get("review_status", "review_required"),
        "introduction_notes": fields.get("introduction_notes"),
        "notes": fields.get("notes"),
        "updated_at": now,
    }

    if existing is not None:
        assignments = ", ".join(f"{key} = :{key}" for key in payload)
        conn.execute(
            f"UPDATE companies SET {assignments} WHERE id = :id",
            {**payload, "id": existing.id},
        )
        return existing.id, False

    company_id = new_id("cmp")
    conn.execute(
        """INSERT INTO companies
           (id, source_id, external_record_id, legal_name, trading_name, country, region, city,
            address, registry_id, registry_verified, website, parent_company_id, is_branch,
            category, is_referral_partner, buying_route, buying_authority, network_claim,
            review_status, introduction_notes, notes, is_demo, created_at, updated_at)
           VALUES (:id, :source_id, :external_record_id, :legal_name, :trading_name, :country,
            :region, :city, :address, :registry_id, :registry_verified, :website,
            :parent_company_id, :is_branch, :category, :is_referral_partner, :buying_route,
            :buying_authority, :network_claim, :review_status, :introduction_notes, :notes,
            :is_demo, :created_at, :updated_at)""",
        {
            **payload,
            "id": company_id,
            "source_id": source_id,
            "external_record_id": external_record_id,
            "is_demo": 1 if is_demo else 0,
            "created_at": now,
        },
    )
    return company_id, True


# -- contacts ------------------------------------------------------------


@dataclass
class Contact:
    id: str
    company_id: str
    full_name: str | None
    role_title: str | None
    business_email: str | None
    business_phone: str | None
    contact_type: str
    verified_at: datetime | None
    notes: str | None = None

    @property
    def is_identified(self) -> bool:
        """False when the person is unknown - which is a valid, stored state."""
        return bool(self.full_name)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Contact":
        return cls(
            id=row["id"],
            company_id=row["company_id"],
            full_name=row["full_name"],
            role_title=row["role_title"],
            business_email=row["business_email"],
            business_phone=row["business_phone"],
            contact_type=row["contact_type"],
            verified_at=from_iso(row["verified_at"]),
            notes=row["notes"],
        )


def upsert_contact(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    now: str,
    is_demo: bool,
    full_name: str | None = None,
    role_title: str | None = None,
    business_email: str | None = None,
    business_phone: str | None = None,
    contact_type: str = "unknown",
    verified_at: str | None = None,
    notes: str | None = None,
) -> str:
    """Store a published business contact.

    Matching is on company plus email or phone. Two people are never merged just
    because their names are similar.
    """
    row = None
    if business_email:
        row = conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? AND business_email = ?",
            (company_id, business_email),
        ).fetchone()
    if row is None and business_phone:
        row = conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? AND business_phone = ?",
            (company_id, business_phone),
        ).fetchone()
    if row is None and full_name:
        row = conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? AND full_name = ? "
            "AND business_email IS NULL AND business_phone IS NULL",
            (company_id, full_name),
        ).fetchone()

    payload = {
        "full_name": full_name,
        "role_title": role_title,
        "business_email": business_email,
        "business_phone": business_phone,
        "contact_type": contact_type,
        "verified_at": verified_at,
        "notes": notes,
        "updated_at": now,
    }
    if row is not None:
        assignments = ", ".join(f"{key} = :{key}" for key in payload)
        conn.execute(
            f"UPDATE contacts SET {assignments} WHERE id = :id", {**payload, "id": row["id"]}
        )
        return row["id"]

    contact_id = new_id("con")
    conn.execute(
        """INSERT INTO contacts
           (id, company_id, full_name, role_title, business_email, business_phone,
            contact_type, verified_at, notes, is_demo, created_at, updated_at)
           VALUES (:id, :company_id, :full_name, :role_title, :business_email, :business_phone,
            :contact_type, :verified_at, :notes, :is_demo, :created_at, :updated_at)""",
        {
            **payload,
            "id": contact_id,
            "company_id": company_id,
            "is_demo": 1 if is_demo else 0,
            "created_at": now,
        },
    )
    return contact_id


def list_contacts(conn: sqlite3.Connection, company_id: str) -> list[Contact]:
    rows = conn.execute(
        "SELECT * FROM contacts WHERE company_id = ? ORDER BY full_name IS NULL, full_name",
        (company_id,),
    ).fetchall()
    return [Contact.from_row(row) for row in rows]


def get_contact(conn: sqlite3.Connection, contact_id: str) -> Contact | None:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return None if row is None else Contact.from_row(row)


# -- contact policy and suppression -------------------------------------


@dataclass
class ContactPolicy:
    id: str
    company_id: str
    contact_id: str | None
    channel: str
    purpose: str
    status: str
    market: str | None
    basis: str | None
    restrictions: str | None
    reviewer: str | None
    reviewed_at: datetime | None
    expires_at: datetime | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ContactPolicy":
        return cls(
            id=row["id"],
            company_id=row["company_id"],
            contact_id=row["contact_id"],
            channel=row["channel"],
            purpose=row["purpose"],
            status=row["status"],
            market=row["market"],
            basis=row["basis"],
            restrictions=row["restrictions"],
            reviewer=row["reviewer"],
            reviewed_at=from_iso(row["reviewed_at"]),
            expires_at=from_iso(row["expires_at"]),
        )

    def effective_status(self, now: datetime) -> str:
        """An expiry date lapses on its own, without anyone editing the row."""
        if self.status == ContactPolicyStatus.PERMITTED_FOR_SCOPE.value:
            if self.expires_at is not None and self.expires_at <= now:
                return ContactPolicyStatus.EXPIRED.value
        return self.status


def set_contact_policy(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    channel: str,
    purpose: str,
    status: str,
    now: str,
    is_demo: bool,
    contact_id: str | None = None,
    market: str | None = None,
    basis: str | None = None,
    restrictions: str | None = None,
    reviewer: str | None = None,
    reviewed_at: str | None = None,
    expires_at: str | None = None,
) -> tuple[str, bool]:
    """Store a channel policy decision, returning its id and whether it is new."""
    existing = conn.execute(
        "SELECT id FROM contact_policies WHERE company_id = ? AND channel = ? AND purpose = ? "
        "AND IFNULL(contact_id,'') = IFNULL(?,'')",
        (company_id, channel, purpose, contact_id),
    ).fetchone()
    payload = {
        "status": status,
        "market": market,
        "basis": basis,
        "restrictions": restrictions,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "expires_at": expires_at,
        "updated_at": now,
    }
    if existing is not None:
        assignments = ", ".join(f"{key} = :{key}" for key in payload)
        conn.execute(
            f"UPDATE contact_policies SET {assignments} WHERE id = :id",
            {**payload, "id": existing["id"]},
        )
        return existing["id"], False

    policy_id = new_id("pol")
    conn.execute(
        """INSERT INTO contact_policies
           (id, company_id, contact_id, channel, purpose, market, status, basis, restrictions,
            reviewer, reviewed_at, expires_at, is_demo, created_at, updated_at)
           VALUES (:id, :company_id, :contact_id, :channel, :purpose, :market, :status, :basis,
            :restrictions, :reviewer, :reviewed_at, :expires_at, :is_demo, :created_at,
            :updated_at)""",
        {
            **payload,
            "id": policy_id,
            "company_id": company_id,
            "contact_id": contact_id,
            "channel": channel,
            "purpose": purpose,
            "is_demo": 1 if is_demo else 0,
            "created_at": now,
        },
    )
    return policy_id, True


def list_policies(conn: sqlite3.Connection, company_id: str) -> list[ContactPolicy]:
    rows = conn.execute(
        "SELECT * FROM contact_policies WHERE company_id = ? ORDER BY channel", (company_id,)
    ).fetchall()
    return [ContactPolicy.from_row(row) for row in rows]


def policy_for(
    conn: sqlite3.Connection, company_id: str, channel: str, *, contact_id: str | None = None
) -> ContactPolicy | None:
    row = conn.execute(
        "SELECT * FROM contact_policies WHERE company_id = ? AND channel = ? "
        "AND (contact_id IS NULL OR contact_id = ?) "
        "ORDER BY contact_id IS NULL LIMIT 1",
        (company_id, channel, contact_id),
    ).fetchone()
    return None if row is None else ContactPolicy.from_row(row)


def add_suppression(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    reason: str,
    now: str,
    is_demo: bool,
    contact_id: str | None = None,
    scope: str = "all",
    recorded_by: str | None = None,
) -> str:
    already = conn.execute(
        "SELECT id FROM suppressions WHERE company_id = ? AND IFNULL(contact_id,'') = IFNULL(?,'') "
        "AND scope = ? AND reason = ?",
        (company_id, contact_id, scope, reason),
    ).fetchone()
    if already is not None:
        return already["id"]

    suppression_id = new_id("sup")
    conn.execute(
        """INSERT INTO suppressions
           (id, company_id, contact_id, scope, reason, recorded_by, created_at, is_demo)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            suppression_id,
            company_id,
            contact_id,
            scope,
            reason,
            recorded_by,
            now,
            1 if is_demo else 0,
        ),
    )
    return suppression_id


def suppressions_for(
    conn: sqlite3.Connection, company_id: str, *, channel: str | None = None
) -> list[sqlite3.Row]:
    if channel is None:
        return conn.execute(
            "SELECT * FROM suppressions WHERE company_id = ?", (company_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM suppressions WHERE company_id = ? AND scope IN ('all', ?)",
        (company_id, channel),
    ).fetchall()


def is_suppressed(conn: sqlite3.Connection, company_id: str, *, channel: str | None = None) -> bool:
    return bool(suppressions_for(conn, company_id, channel=channel))


# -- buyer briefs --------------------------------------------------------


@dataclass
class BuyerBrief:
    id: str
    company_id: str
    model_family: str
    variant: str | None
    required_specs: dict[str, Any]
    preferred_specs: dict[str, Any]
    budget: Money | None
    budget_basis: str
    budget_vat_regime: str
    quantity: int
    destination_country: str | None
    route: str
    required_by: datetime | None
    conversation_date: datetime
    confirmed_at: datetime | None
    confirmed_by: str | None
    expires_at: datetime | None
    evidence_note: str | None
    contact_id: str | None = None
    source_id: str | None = None
    external_record_id: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "BuyerBrief":
        return cls(
            id=row["id"],
            company_id=row["company_id"],
            model_family=row["model_family"],
            variant=row["variant"],
            required_specs=loads(row["required_specs"], {}),
            preferred_specs=loads(row["preferred_specs"], {}),
            budget=money_from(row["budget_minor"], row["budget_currency"]),
            budget_basis=row["budget_basis"],
            budget_vat_regime=row["budget_vat_regime"],
            quantity=row["quantity"],
            destination_country=row["destination_country"],
            route=row["route"],
            required_by=from_iso(row["required_by"]),
            conversation_date=from_iso(row["conversation_date"]),
            confirmed_at=from_iso(row["confirmed_at"]),
            confirmed_by=row["confirmed_by"],
            expires_at=from_iso(row["expires_at"]),
            evidence_note=row["evidence_note"],
            contact_id=row["contact_id"],
            source_id=row["source_id"],
            external_record_id=row["external_record_id"],
        )


def upsert_brief(
    conn: sqlite3.Connection,
    *,
    source_id: str | None,
    external_record_id: str | None,
    company_id: str,
    now: str,
    is_demo: bool,
    **fields: Any,
) -> tuple[str, bool]:
    existing = None
    if source_id and external_record_id:
        row = conn.execute(
            "SELECT id FROM buyer_briefs WHERE source_id = ? AND external_record_id = ?",
            (source_id, external_record_id),
        ).fetchone()
        existing = None if row is None else row["id"]

    budget: Money | None = fields.get("budget")
    payload = {
        "company_id": company_id,
        "contact_id": fields.get("contact_id"),
        "model_family": fields["model_family"],
        "variant": fields.get("variant"),
        "required_specs": dumps(fields.get("required_specs") or {}),
        "preferred_specs": dumps(fields.get("preferred_specs") or {}),
        "budget_minor": None if budget is None else budget.minor_units,
        "budget_currency": None if budget is None else budget.currency,
        "budget_basis": fields.get("budget_basis", "unknown"),
        "budget_vat_regime": fields.get("budget_vat_regime", "unknown"),
        "quantity": fields.get("quantity", 1),
        "destination_country": fields.get("destination_country"),
        "route": fields.get("route", "unknown"),
        "required_by": fields.get("required_by"),
        "conversation_date": fields["conversation_date"],
        "confirmed_at": fields.get("confirmed_at"),
        "confirmed_by": fields.get("confirmed_by"),
        "expires_at": fields.get("expires_at"),
        "evidence_note": fields.get("evidence_note"),
        "updated_at": now,
    }
    if existing is not None:
        assignments = ", ".join(f"{key} = :{key}" for key in payload)
        conn.execute(
            f"UPDATE buyer_briefs SET {assignments} WHERE id = :id", {**payload, "id": existing}
        )
        return existing, False

    brief_id = new_id("brf")
    columns = ", ".join(payload.keys())
    placeholders = ", ".join(f":{key}" for key in payload)
    conn.execute(
        f"""INSERT INTO buyer_briefs
            (id, source_id, external_record_id, is_demo, created_at, {columns})
            VALUES (:id, :source_id, :external_record_id, :is_demo, :created_at, {placeholders})""",
        {
            **payload,
            "id": brief_id,
            "source_id": source_id,
            "external_record_id": external_record_id,
            "is_demo": 1 if is_demo else 0,
            "created_at": now,
        },
    )
    return brief_id, True


def list_briefs(
    conn: sqlite3.Connection,
    *,
    company_id: str | None = None,
    model_family: str | None = None,
    confirmed_only: bool = False,
) -> list[BuyerBrief]:
    clauses: list[str] = []
    params: list[Any] = []
    if company_id:
        clauses.append("company_id = ?")
        params.append(company_id)
    if model_family:
        clauses.append("model_family = ?")
        params.append(model_family)
    if confirmed_only:
        clauses.append("confirmed_at IS NOT NULL")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM buyer_briefs {where} ORDER BY conversation_date DESC", params
    ).fetchall()
    return [BuyerBrief.from_row(row) for row in rows]


def get_brief(conn: sqlite3.Connection, brief_id: str) -> BuyerBrief | None:
    row = conn.execute("SELECT * FROM buyer_briefs WHERE id = ?", (brief_id,)).fetchone()
    return None if row is None else BuyerBrief.from_row(row)


def record_duplicate_review(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    left_id: str,
    right_id: str,
    reason: str,
    now: str,
    is_demo: bool,
    signals: dict[str, Any] | None = None,
) -> str | None:
    """Suggest a review. A fuzzy signal never merges two records on its own."""
    left, right = sorted([left_id, right_id])
    if exists(
        conn,
        "duplicate_reviews",
        "entity_type = ? AND left_id = ? AND right_id = ?",
        (entity_type, left, right),
    ):
        return None
    review_id = new_id("dup")
    conn.execute(
        """INSERT INTO duplicate_reviews
           (id, entity_type, left_id, right_id, reason, signals, decision, created_at, is_demo)
           VALUES (?,?,?,?,?,?, 'pending', ?, ?)""",
        (
            review_id,
            entity_type,
            left,
            right,
            reason,
            dumps(signals or {}),
            now,
            1 if is_demo else 0,
        ),
    )
    return review_id


def pending_duplicate_reviews(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM duplicate_reviews WHERE decision = 'pending' ORDER BY created_at"
    ).fetchall()
