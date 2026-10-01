# Source access register

Every source the prototype knows about, what it may be used for, and what is still required
before it could be used live. The authoritative copy is the `sources` table; this document
explains the reasoning behind each entry.

**No live request has been made from this project.** Every network-capable adapter is
`review_required` or `blocked`, so `SourcePolicy.may_fetch` refuses it.

## What a status means

| Status | Meaning |
|---|---|
| `approved` | A reviewer has recorded a decision, a scope and a review date. Fetching is permitted within that scope, in live mode, with the network flag on. |
| `review_required` | Nobody has reviewed the route and its conditions yet. Live fetching is disabled. It does not silently fall back to reading the website. |
| `expired` | The review-due date has passed. Derived automatically, without anyone editing the row. Live access and export are blocked; stored data stays under its retention rule. |
| `blocked` | There is no authorised route. The adapter is an interface only. |

Two different kinds of evidence sit behind `approved`, and the register distinguishes them:

- **For an open or public source,** approval means the project owner has read the published
  route and its conditions and recorded that decision. It does **not** mean a government or a
  provider has individually approved this project. Nothing in this repository should imply that.
- **For a commercial source,** approval additionally requires the relevant contract or account
  scope.

## What is recorded per source

Name, category, base URL and allowed hosts, allowed path prefixes, documentation URL, access
mode, approved use, retention rule and retention days, whether raw content may be kept, whether
data may be exported, attribution, rate and concurrency limits, approval evidence, reviewer,
review date, review-due date, and the last fetch result.

**Credentials are never stored here.** They live in environment variables. Logs never contain a
token or a full contact list.

## Stage A — enabled

| Source | Category | Route | Notes |
|---|---|---|---|
| `fixture_demo` | mixed | Local JSON, no network | Synthetic supply and accounts. Marked `synthetic_demo`; the loader refuses a file without that marker. |
| `fixture_demo_market` | comparables | Local JSON, no network | Synthetic comparable observations. |
| `human_entry_demo` | human demand | Typed in by the user | Buying briefs, contact decisions and recorded outcomes. Each carries a conversation date and an evidence note. |

A company-authorised CSV or JSON export would also sit in Stage A. The import contracts and
templates exist (`desk.cli template <kind>`); what is missing is a real export and a record of
who owns the data and what the project may do with it.

**The 38-advert audit in the parent folder is background research.** It is not a feed licence
and not proof of current stock, and it is deliberately not imported. A genuine demonstration
uses the synthetic fixtures or an authorised stock export.

## Stage B — implemented, awaiting review

### OpenStreetMap via Overpass — `osm_overpass`

- **Contributes:** discovery of car sellers and rentals in one small selected area
  (`shop=car`, `amenity=car_rental`).
- **Implemented:** bounded bounding-box queries capped at 1.5° per side and 200 results;
  captures object type and id, name, coordinates, address and contact tags where the object
  supplies them; preserves OSM attribution and licence metadata on every record.
- **Limitations recorded in the adapter:** coverage is incomplete, and a car seller is not a
  luxury buyer. Discovery never sets a buying route, a buying authority or a contact policy.
- **Still required:** read the current usage policy, record the intended area and query
  frequency, and set a rate limit. A large job belongs in an extraction service, not in
  repeated continent-wide queries here.
- Documentation: <https://wiki.openstreetmap.org/wiki/Overpass_API> ·
  Licence: <https://www.openstreetmap.org/copyright>

### Czech ARES — `cz_ares`

- **Contributes:** verification of the legal identity of an already-known Czech company by IČO.
- **Implemented:** IČO validation before any request; defensive parsing of the legal name,
  address, legal form, registration dates and primary activity; a not-found response verifies
  nothing and says so.
- **Deliberately unset:** the base URL. The adapter refuses to run without one rather than
  guessing an endpoint or a field name. Load the current published specification linked from the
  Ministry of Finance and record the route.
- **Still required:** the ministry's operating conditions on request rate, concurrency and
  repeated or random queries must be recorded as limits on this entry.
- **What it is not:** a source of proven buyers, and not an executive-email database. It
  confirms who a company is, not that it buys vehicles and not who may be contacted.
- Documentation: <https://mf.gov.cz/cs/ministerstvo/informacni-systemy/ares>

### French API Recherche d'entreprises — `fr_recherche_entreprises`

- **Contributes:** French legal identity and location, after the Czech workflow works.
- **Implemented:** parsing of SIREN, legal name, primary activity and registered address;
  records carrying a diffusion restriction are skipped and reported, not ingested; the
  pagination cursor is returned.
- **Do not confuse it with API Entreprise,** which is separately restricted. The attribution
  string says so.
- **Still required:** check the current response schema, the diffusion rules, pagination and
  rate limits against the live documentation.
- Explanation: <https://annuaire-entreprises.data.gouv.fr/donnees/api-entreprises> ·
  Documentation: <https://recherche-entreprises.api.gouv.fr/docs/>

### Approved company websites — `approved_website`

- **Contributes:** published business facts from explicitly selected contact, team, stock or
  fleet pages.
- **Implemented:** at most five reviewed URLs per business; no crawling and no link following;
  only reviewed hosts and path prefixes; scripts are stripped and never executed; bounded
  response size; field-level evidence with a short permitted excerpt.
- **Refusals are refusals:** a login, a CAPTCHA or a block stops the adapter. Successful HTTP
  access is never treated as permission.
- **What is not evidence:** a fleet photograph, a stock image or a follower count is not
  ownership or demand.
- **Still required:** a per-domain review recording which pages may be read and what may be
  retained.

## Stage C — blocked, interface only

| Source | Why it is blocked | What would be needed |
|---|---|---|
| `mobile_de` | Public terms prohibit unauthorised extraction. The official API family (Search API, Ad Stream) needs an expressly authorised scope. | Account activation, the permitted data scope, storage and onward-use terms — all confirmed in writing. An API for publishing your own stock is not access to the whole market. |
| `autoscout24` | The verified listing-creation API concerns authorised customer listings, not all-market search. | The relevant operator granting the required access first. |
| `kompass_easybusiness` | No licence held. | An approved sample, provider provenance retained, permitted-use conditions recorded, and the exact fields validated. A data licence does not establish consent to market to the people listed. |

There is no scraper and no speculative endpoint for any of these. The adapter interface exists
so that a future authorised integration has somewhere to go.

**Dealer-supplied feeds** are a better route than a broad marketplace: a CSV, XML or JSON feed
from a dealer who authorises monitoring. Record which fields may be retained and whether the
prices may be shared. A feed provided privately for one deal is not automatically reusable for
every customer.

## Sources that stay manual

Sauto, TipCars, La Centrale, Coches.net, OTOMOTO, Classic Driver, manufacturer retailer
locators, NLA/NLARide, EAIVT, AutoProff and Fleet Europe. Their roles and limitations are in
`OUTBOUND_PLAYBOOK.md`. **No general scraping permission was established for any of them.**
Keep links and permitted research notes; do not build a connector.

## Never implemented

| Not built | Why |
|---|---|
| Google Maps scraping | The Places API exists, but its storage and reuse restrictions make it unsuitable as an assumed permanent CRM export. <https://developers.google.com/maps/documentation/places/web-service/policies> |
| LinkedIn connector | Prohibited by the User Agreement. <https://www.linkedin.com/legal/user-agreement> |
| Private WhatsApp monitoring | Out of scope and not appropriate. |
| Email open and click tracking | Out of scope for the first version. |
| Wealth profiling | Out of scope. |
| Make.com or an equivalent automation service | Not needed to prove the workflow. |

## How the gate is enforced

`desk/services/source_policy.py` is the only path to the network. Before any request it checks,
in order: the application mode (demo never fetches), the network flag, the existence of a
register entry, whether that entry is a demo fixture, the effective status including automatic
expiry, whether the access mode has a fetch route at all, the host against the allowed list,
the path against the allowed prefixes, and the resolved IP address.

The address check refuses loopback, private, link-local, reserved, multicast and metadata
destinations, by name and by resolved address, and it is re-applied to every redirect target,
so a redirect cannot turn an approved fetch into a request against a local service. The address
is only resolved — nothing is contacted.

Throttling is honoured: a 429 or 503 backs off and reports `Retry-After`. Identities are never
rotated to defeat a limit. A 401 or 403 stops the adapter.

`tests/test_source_policy.py` and `tests/test_connectors.py` assert all of this, including that
no source in the seeded register can be fetched.

## Recording a review

Approval is no longer a hand edit to a database row. **Sources and imports, Review a source**
records a decision with a named reviewer, and keeps every review as history rather than
overwriting the last one. `desk review-source --source <id>` shows a source's current status and
its review history from the command line.

A review captures: the new status, the reviewer, what the source may be used for, what the
permission rests on, the allowed hosts and path prefixes, retention and export rules, rate and
concurrency limits, and a date the approval lapses.

### What the form will refuse

These are enforced, not advisory:

| Attempted | Refused because |
|---|---|
| An approval with no stated use, or no stated evidence | An approval that cannot say what it permits or what it rests on is not an approval. |
| An approval with an evidence kind of `unknown` | The kind of evidence is the whole point of the distinction below. |
| A **licensed** source approved on `published_terms_reviewed` | Reading a provider's published terms is not the same as holding the account scope those terms describe. Record the contract or account scope, or leave it blocked. |
| A public API or website approved with no host list | An approval with no host scope would permit any address. |
| A review-due date on or before the review date | An approval that has already lapsed is not an approval. |

And these are warnings rather than refusals, because they may be deliberate: no review-due date
given (one is set 180 days out), no path prefixes on an approved website, export permitted with
no retention rule recorded, and approving while in demo mode.

### The two evidence kinds, again

The register records which of these an approval rests on, because conflating them is how a
project ends up claiming permission it does not have:

- **`published_terms_reviewed`** - the project owner read the published route and its conditions
  and recorded that. **This is not an individual approval by the provider or by any government**,
  and nothing in the interface or the documents may imply that it is.
- **`contract_or_account_scope`** - a contract, licence or activated account defines the scope.
- **`owner_authorisation`** - the data owner authorised this specific use in writing.

## Enabling a source, when the time comes

1. Read the current official documentation. It, not this repository, is authoritative.
2. Record the review on the Review a source screen, with the scope, the limits, the retention and
   export rules, the evidence kind, the reviewer and the review-due date.
3. Put any credential in the environment, never in the database.
4. Set `DESK_MODE=live` and `DESK_ALLOW_NETWORK=true`, and use the live database. Approving a
   source while in demo mode is a rehearsal: demo sources still cannot fetch, and the form says
   so.
5. Run `desk refresh-source --source <id>` and check that what came back is correctly labelled as
   company *candidates*, not buyers.
6. Re-review before the review-due date. Approval lapses on its own, and the Today screen counts
   approvals that are close to lapsing.
