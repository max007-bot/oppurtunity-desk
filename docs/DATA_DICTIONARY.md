# Data dictionary

The schema is in `desk/migrations/0001_initial.sql`. This document explains what each table is
for and why the enumerated values are drawn the way they are.

## The principle behind the whole schema

**Unknown is a stored value, not an absent one.** A missing option, mileage, VAT regime or
buying authority is recorded as unknown, and unknown blocks a pass, a comparison or a completed
calculation. The alternative — defaulting to a convenient value — is how a research tool starts
quietly inventing facts.

Three consequences run through everything below:

1. **Source observations are separate from reviewed canonical facts.** An import appends an
   observation and may raise an alert. It never rewrites a reviewed fact. Where they disagree,
   the contradiction is shown to a human.
2. **Money is an integer count of minor units plus an ISO currency.** Results are computed with
   `Decimal`. No binary float touches a price, a cost or a result.
3. **Every displayed fact has a provenance route** through `evidence`, or through an observation,
   or through a recorded human confirmation.

## Money, price basis and tax regime

Three separate columns, because conflating them is the most expensive mistake available here.

| Column | Values | Meaning |
|---|---|---|
| `*_minor` + `*_currency` | integer + ISO code | The amount. `200000.00 EUR` is stored as `20000000` and `EUR`. |
| `price_basis` | `net`, `gross`, `unknown` | Whether tax is included in the figure. |
| `vat_regime` | `standard`, `margin`, `other`, `unknown` | Which regime applies. **A price basis is not a tax regime.** |

Only net-to-net or gross-to-gross under the same known regime may be compared or combined.
Margin-scheme, `other` and `unknown` are refused. The prototype never divides a used-car gross
price by a country VAT rate.

`price_evidence_type` is a separate axis again, because an asking price, a budget and a firm bid
are different kinds of evidence:

| Value | Meaning |
|---|---|
| `supply_asking` | What a seller is asking. The only type treated as sellable supply. |
| `retail_asking` | A retail advert. Comparable evidence. |
| `dealer_bid` | A dealer-to-dealer bid. Real evidence of what a trade buyer will pay. |
| `buyer_budget` | A stated ceiling. **Not a commitment.** |
| `completed_transaction` | Evidence of a sale that happened. |
| `analyst_assumption` | A figure a person assumed. Labelled as such wherever it appears. |

These are never blended into a single market price. Every exclusion on that ground is shown with
its reason.

## Confidence

`evidence.confidence` records *how a fact came to be believed*. It is not a probability, and no
number is attached to it.

| Value | Meaning |
|---|---|
| `observed_public` | Seen in a permitted public source. |
| `seller_claimed` | The seller says so. |
| `buyer_confirmed` | The buyer confirmed it in a recorded conversation. |
| `company_confirmed` | The company confirmed it. |
| `derived` | Computed from other stored facts. |
| `unknown` | Not established. |
| `conflicting` | Sources disagree; needs a human. |

`confirmed_by` stores *who* confirmed a fact. A model-generated confidence number is never
verification.

## Tables

### Sources and evidence

| Table | Purpose |
|---|---|
| `sources` | The register. Category, access mode, allowed hosts and paths, approved use, retention and export rules, limits, approval trail, review-due date, last fetch result. No credentials — those live in the environment. |
| `evidence` | Field-level provenance: which source, which URL or file row, when the source stated it, when this app stored it, the confidence, a permitted short excerpt, and an optional expiry. |

An approval lapses automatically on `review_due_at`; the stored status is not edited. When a
retention expiry removes evidence, affected matches and drafts become stale rather than silently
keeping a conclusion whose support must be deleted.

### Companies and people

| Table | Purpose |
|---|---|
| `companies` | Legal and trading name, country, registry id and whether it is *verified*, website, parent link and branch flag, category, referral-partner flag, confirmed buying route, buying authority, and any `network_claim`. |
| `contacts` | A published business contact. `full_name` may be NULL: unknown is a valid state and no decision-maker is invented. |
| `contact_policies` | One accountable human decision per company, channel and purpose, with the basis, restrictions, reviewer, review date and expiry. |
| `suppressions` | An explicit do-not-contact entry with its reason and scope. Outranks everything. |

Company identity is country plus a *verified* registration id. Domain and address are supporting
signals only. A branch is not automatically the purchasing entity. Two people are never merged
because their names match.

`network_claim` exists so a statement like "advertises a 700-car partner network" can be stored
as a claim, with nothing in the system treating it as owned stock or central purchasing.

#### Contact-policy statuses

| Value | Meaning |
|---|---|
| `unknown` | Nothing recorded. |
| `review_required` | Needs a human decision. **What a discovered address becomes.** |
| `permitted_for_scope` | A named channel, purpose, basis and reviewer. Validation refuses this status without all four. |
| `denied` | Recorded as not to be contacted on this channel. |
| `expired` | Derived automatically once the expiry passes. |

This is a decision log, not a legal rule engine. It records an accountable human decision; it
does not determine what any jurisdiction permits.

### Demand

`buyer_briefs` holds a human-recorded requirement: model and variant, `required_specs` (hard) and
`preferred_specs` (ranking only), budget with its basis and regime, quantity, destination, route,
required-by date, the conversation date, who confirmed it and when, an expiry, and an evidence
note.

`required_specs` is a JSON object read by the matching rules:

```json
{
  "steering": "lhd",
  "seats": 5,
  "min_seats": 5,
  "powertrain": "petrol",
  "max_mileage_km": 25000,
  "max_registration_age_months": 24,
  "must_have_options": ["rear_entertainment"],
  "stock_requirement": "physical_stock",
  "variant": "G 63 4MATIC",
  "destination_country": "DE"
}
```

An option **absent** from a vehicle's specification is unknown and yields *needs verification*.
An option recorded as `false` is a conflict and yields *no match*. A confirmed brief must name
who confirmed it.

### Supply

| Table | Purpose |
|---|---|
| `vehicles` | The reviewed identity: VIN and whether *verified*, identity-review status, canonical model, generation, registration, mileage, powertrain, steering, homologated seats, and a reviewed specification object. |
| `offers` | One seller's offer of one vehicle: price with its basis, regime and evidence type, the preserved raw price text, seller status, stock kind, location, authority to sell, availability dates, and the latest observation state. |
| `observations` | **Append-only.** Every check: what the source stated and when, when this app looked, the state, the parsed fields, a response hash, and a version number. |
| `change_events` | Derived alerts, deduplicated by source record, field and observation version. |
| `duplicate_reviews` | A suggestion for a human. Never an automatic merge. |

`identity_review_status`: `review_required`, `reviewed`, `possible_duplicate`,
`allocation_no_vin`. A build slot with no VIN cannot be a confirmed single identity.

#### Three separate facts about availability

Conflating these is how a tool starts claiming a car is available when nobody has checked.

| Column | Question it answers |
|---|---|
| `status` | What does the **seller** say? `unknown`, `advertised_available`, `availability_confirmed`, `reserved`, `unavailable`, `allocation`. |
| `last_observation_state` | What did the **latest check** see? `seen`, `not_seen`, `fetch_failed`. |
| `stock_kind` | Does the car **physically exist**? `physical_stock`, `allocation`, `unknown`. |

`not_seen` means "not observed in the latest successful check". `fetch_failed` means the check
itself failed. **Neither means sold**, and no code path maps them to a sale. Seller authority
(`authority_to_sell`) and physical location are separate facts again.

### Analysis

| Table | Purpose |
|---|---|
| `comparable_sets` | One analysis: the filters used, the tax basis, the count of distinct vehicles, the median and range, the result status, the rule version and when it ran. |
| `comparable_members` | Every candidate considered, whether it was included, and **why**. Exclusions are first-class. Where a conversion or an adjustment changed the figure used, both the observed price and the figure used are stored, with the rate or the reason. |
| `matches` | One row per offer, brief and company. Fit status, score with its breakdown, and the passed, failed and unknown rules as displayable text. |
| `scenarios` | Acquisition and sale with their bases and evidence types, the downstream dealer inputs, the computed outlay, trade price, contribution, asking gap and cash figures, the completeness status, the missing inputs and the assumptions. |
| `scenario_costs` | One row per cost line. |
| `fx_rates` | Dated conversion rates, one direction each. Nothing converts without one; the inverse is derived. |
| `comparable_adjustments` | An analyst adjustment on one comparable, with its amount, reason and author. Retired rather than deleted. |
| `source_reviews` | Every recorded review of a source, kept as history so the next reviewer can see what the last one relied on. |
| `fact_reviews` | A decision about a contradiction: accepting the source's value or keeping the reviewed one, with who decided and a dated note. |

`comparable_sets.result_status` is `median_available` or `insufficient_comparables`. Fewer than
three sufficiently comparable distinct vehicles gives the latter, and the individual observations
are shown instead of a fabricated median. Three is a conservative prototype setting, not a
valuation standard.

`matches.fit_status`:

| Value | Meaning |
|---|---|
| `specification_fit` | Every mandatory fact matches. **Not** proof the buyer will buy or that the economics work. |
| `needs_verification` | A required fact is missing. Not a pass. |
| `no_match` | A known conflict, with the reason shown. |
| `category_fit_prospect` | Right category, no recorded requirement. Research only. |
| `brief_expired` | The requirement needs reconfirmation before it supports anything. |

Only a clean `specification_fit` with no failed and no unknown rules is eligible for the
contact-ready queue. A rejection and an expired brief score zero.

#### Cost lines

| Column | Why it exists |
|---|---|
| `amount_minor` NULL | The cost is **unknown**, and that blocks completion. |
| `is_confirmed_zero` | Someone checked and it really is nothing. Different from unknown. |
| `tax_treatment` | `non_recoverable` counts as cost; `recoverable` is cash out but not final cost; `outside_scope` is neither. |
| `already_in_purchase_price` | Excluded from the total to prevent double counting. |
| `paid_at`, `refunded_at` | Without these the cash schedule is incomplete, even when the totals compute. |

### Follow-up

| Table | Purpose |
|---|---|
| `tasks` | A dated next action with a stated reason, deduplicated so a rerun invents no work. Actions: `check_availability`, `ask_buyer`, `review_costs`, `ready_for_call_preparation`, `reconfirm_brief`, `verify_contact`, `review_duplicate`. |
| `interactions` | What a human recorded after a real conversation. Unique per source record, so re-importing does not double-count. |
| `drafts` | A prepared message with the facts it used, the unresolved fields, its status, the block reasons, when the policy was checked, and a fingerprint of the supporting facts. **No sending credentials.** |

Interaction outcomes: `no_answer`, `wrong_role`, `no_fit`, `requirement_captured`,
`requested_dossier`, `meeting_booked`, `meeting_held`, `offer`, `deposit`, `delivered`, `lost`,
`objection`. Nothing is inferred from silence, and an `objection` creates a suppression.

Draft statuses: `internal_research` (always available), `contact_ready` (every gate passed at
that moment), `blocked`. `facts_fingerprint` covers the price, status, stock kind, confirmation
and expiry of the offer, the brief's budget and confirmation, and the contact's details — so any
change makes the stored draft stale rather than quietly reusable.

### Audit

| Table | Purpose |
|---|---|
| `runs` | Each import or refresh: connector, version, mode, timings, counts, errors. |
| `change_log` | Field-level transitions with the actor and the time. |
| `app_meta` | The database's mode stamp and the demo time anchor. |

## Demo and live separation

Two mechanisms, deliberately overlapping:

1. **Separate files**, `data/demo.sqlite` and `data/live.sqlite`, each stamped with its mode in
   `app_meta`. Opening one as the other raises `ModeViolation`.
2. **`is_demo` on every row.** A demo reset refuses a file not stamped demo and never deletes an
   arbitrary path.

## Import contracts

Documented templates for `companies`, `offers`, `buyer_briefs`, `comparables`, `interactions` and
`contact_policies` (`desk.cli template <kind>`). Every row needs a stable external id and a
source. Identity is `(source_id, external_record_id)`.

The whole batch is validated first. **The default on any error is to reject all of it.** Applying
only the valid rows requires an explicit choice.

Rejected: negative prices, impossible seat counts, mileage outside bounds, unknown currencies,
observation timestamps in the future, a VIN that is not 17 alphanumeric characters, a confirmed
brief with no named confirmer, a `permitted_for_scope` policy missing its basis or reviewer, and
an allocation claiming confirmed availability.

Raw strings such as `"€119.000"` are preserved beside the parsed value. An ambiguous separator
requires the source locale: `119.000` is 119,000 in the EU and 119.0 in the UK, and guessing
would be wrong by three orders of magnitude.

CSV export neutralises any cell starting `=`, `+`, `-` or `@` so a spreadsheet cannot execute it.
A research export and a contact-ready export are distinct outputs, and contact fields are never
exported for a suppressed account or where the source licence forbids it.

## How a stored figure can differ from what a source said

Three, and only three, things can move a figure between the observation and the result. Each one
requires a person, and each is recorded beside the original rather than replacing it.

| Mechanism | Table | What is required |
|---|---|---|
| Currency conversion | `fx_rates`, recorded on `comparable_members` | A rate with a direction, a date at or before the moment analysed, within the freshness window, and a stated origin. |
| Analyst adjustment | `comparable_adjustments` | An amount, a reason, and an author. Refused if zero, in the wrong currency, or worth more than a quarter of the price. |
| Accepted correction | `fact_reviews` plus `change_log` | A named person, a dated note, and a specific contradiction. Only seats, mileage and powertrain are resolvable this way. |

A connector can do none of these. It appends an observation and may raise an alert; that is all.
