# Status

**Last updated:** 1 October 2026 (fifth session)
**Editor this session:** Claude Code (Opus 5)
**Read this before resuming work.** It is the handover record; do not rely on a chat history.

## Where the build has got to

| Milestone | State |
|---|---|
| 1 — Offline foundation | **Complete.** Project files, schemas, migrations, fixtures, six screens, imports, seeding, replay, persistent edits. No live requests, no runtime AI. |
| 2 — Commercial logic | **Complete.** Identity review, comparable selection, hard buyer constraints, scenario calculations, freshness, explainable alerts. |
| 3 — Useful call preparation | **Complete.** Call briefs, human-reviewed drafts, tasks, contact policies, suppression, scoped exports. |
| 4 — First approved live discovery | **Complete, and awaiting authorisation rather than code.** The refresh path now runs a connector and ingests what it returns, with evidence and a run record. Live access is still not enabled: Overpass, ARES and the French API are implemented and tested against saved synthetic provider responses. All three are `review_required` in the source register, so `may_fetch` refuses them. No live request has been made. |
| 5 — Optional expansion | **Partly done.** An approved-website adapter exists but is disabled, and no AI adapter is wired up or needed. The four capabilities listed as next steps after the first session are now built: accepting a source correction, dated FX conversion, analyst adjustments, and a recorded source review. |
| 6 — Demo and independent review | **Done for this session.** Reviewed against every case in manual section 11, and the running app was driven in a browser as a user would. Sixteen defects were found and fixed; they are listed below. |

## Exact commands, as run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m desk.cli init-db --mode demo
.\.venv\Scripts\python.exe -m desk.cli seed-demo
.\.venv\Scripts\python.exe -m desk.cli replay-demo
.\.venv\Scripts\python.exe -m desk.cli recompute
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
.\.venv\Scripts\python.exe -m pytest
```

Tested on Python 3.12.10, Windows 10 Pro (19045). Pinned versions are in
`requirements.lock.txt`, generated from the environment the tests actually ran in
(Streamlit 1.64.0, Pydantic 2.13.5, HTTPX 0.28.1, pytest 8.4.2).

## Test results

```
418 passed in 137.86s
```

| File | Tests | Covers |
|---|---|---|
| `tests/test_money.py` | 19 | Integer minor units, exact medians, EU/UK number formats, the ambiguous-separator refusal. |
| `tests/test_pricing.py` | 39 | The mandatory €8,500 and −€1,500 cases, incompleteness, cash timing, comparable selection and exclusions, VAT compatibility, the EU new-vehicle indicator. |
| `tests/test_matching.py` | 45 | Every hard requirement, fail-closed behaviour, scoring, the AMG-Line trap, formula-cell neutralisation. |
| `tests/test_imports.py` | 25 | Whole-batch validation, transactional apply, idempotent re-import, impossible values, relationship handling. |
| `tests/test_identity_and_observations.py` | 16 | VIN linking, duplicate review, append-only history, not-seen and fetch-failed. |
| `tests/test_source_policy.py` | 28 | Approval and expiry, host and path scope, the SSRF guard, demo/live separation. |
| `tests/test_outreach.py` | 25 | Call briefs, the contact-readiness gates, draft invalidation, scoped exports, and that a brief contains no internal slug, raw dict or ISO timestamp. |
| `tests/test_connectors.py` | 24 | Overpass, ARES, France and website adapters against fixtures; every live route refused. |
| `tests/test_workflow.py` | 31 | End-to-end over the real demo fixtures, replay, idempotent recompute, crash rollback. |
| `tests/test_fx.py` | 17 | Dated conversion: recording, direction, staleness, hindsight, and what a rate does and does not unlock. |
| `tests/test_corrections.py` | 15 | Accepting or rejecting a source correction, its audit trail, and its effect on a match. |
| `tests/test_adjustments_and_review.py` | 29 | Analyst adjustments as labelled assumptions, and the recorded source review. |
| `tests/test_refresh.py` | 14 | The live refresh path, offline: refusals, a successful run, evidence, and what a failure must not do. |
| `tests/test_briefs_and_templates.py` | 30 | Capturing a requirement on a call, and reviewed reusable cost templates. |
| `tests/test_app.py` | 35 | The real Streamlit app through `AppTest`: every screen renders, the demo label persists, no Send button exists, a rerun does no work, and the worked example produces the manual's figures on screen. |

Reproducibility was checked separately: five independent fresh seeds produce an identical
comparable set and an identical €235,000 median.

## Which screens work

All six, verified through `AppTest` and by hand.

| Screen | State |
|---|---|
| Today | Alerts with old and new values, tasks with stated reasons, stale confirmations, briefs due, duplicate reviews, an explicit Recompute button. |
| Vehicles | Filters on family, location, basis, evidence type and freshness. Per-offer observation history, sibling offers on one VIN, and contradictions between the source and the reviewed record. |
| Businesses | Identity, branch links, network claims labelled as claims, contacts, channel permissions, buying briefs, recorded outcomes, and an outcome form where an objection immediately suppresses the account. |
| Matches | Every matched, failed and unverified rule. Comparable set with included and excluded reasons. Cost worksheet with the manual's worked example, the downside, and the cash schedule. Category-fit prospects in a separate tab. |
| Call preparation | Deterministic brief, gate status, missing facts, Copy and Download only. Saved drafts re-checked on every open. Research and contact-ready exports with per-row exclusion reasons. |
| Sources and imports | The register with the effective status and the reason each source may or may not be fetched. Import preview → validate → confirm. Demo seed, replay and reset. |

## Which data is synthetic

**All of it.** `fixtures/demo_seed.json` and `fixtures/demo_updates.json` both carry
`"data_class": "synthetic_demo"`, and the loader refuses a file without that marker.

- 12 companies, 10 supply offers, 6 buying briefs, 18 comparable observations, 4 recorded
  outcomes, 8 contact-policy decisions.
- Every domain is under the reserved `.example` TLD; every phone number uses the unassigned
  `+999` country code, so nothing is routable. A test asserts both.
- Timestamps use relative tokens (`@now-3h`, `@now+21d`) resolved against an anchor stored in
  `app_meta` on the first seed. That keeps the demo fresh whenever it is first run *and* makes
  re-seeding a genuine no-op.
- The saved marketplace HTML in the parent folder is deliberately **not** imported.

## Which connectors remain disabled

| Source | Register status | Why |
|---|---|---|
| `fixture_demo`, `fixture_demo_market`, `human_entry_demo` | approved | Synthetic and human-entered. The only enabled routes. |
| `osm_overpass` | review_required | Adapter and fixtures done. Needs the published usage policy reviewed and the intended area recorded. |
| `cz_ares` | review_required | Adapter and fixtures done. Base URL deliberately unset: the published specification must be loaded rather than an endpoint guessed. |
| `fr_recherche_entreprises` | review_required | Adapter and fixtures done. Current schema, diffusion restrictions, pagination and limits still to be checked. |
| `approved_website` | review_required | Adapter done, capped at five reviewed URLs per business, no crawling. Needs a per-domain review. |
| `mobile_de`, `autoscout24`, `kompass_easybusiness` | blocked | No authorised scope. Interface only — no scraper, no invented endpoint. |
| `fixture_expired_demo` | approved but past its review date | Present to demonstrate that an approval lapses on its own date and that export is then blocked. |

Runtime AI: **not implemented.** `DESK_AI_ENABLED` exists and defaults to false; there is no
provider call anywhere in the codebase. The deterministic call-brief template is the only
generator, which is also the documented fallback.

## Defects found and fixed during this session

Recorded because they were real bugs, not cosmetic:

1. **Migrations were not atomic.** `executescript` commits implicitly, so the schema and its
   version stamp were not one unit of work. The BEGIN/COMMIT now lives inside the script.
2. **Re-seeding was not idempotent.** Relative time tokens were re-resolved against "now" on
   every run, so every row got a new timestamp and replay appended duplicate observations and
   alerts. Fixed with the stored time anchor.
3. **Category-fit prospects overwrote each other.** The `matches` uniqueness key was
   `(offer_id, brief_id)`, and `brief_id` is NULL for a prospect, so one company's prospect row
   replaced another's. The key is now `(offer_id, brief_id, company_id)`.
4. **Comparable observations were treated as sellable supply**, producing 261 mostly meaningless
   matches. Only `supply_asking` offers are matched now.
5. **Comparable deduplication was non-deterministic.** Two same-VIN observations sharing a
   timestamp meant the median depended on row order. Ordering is now fully determined and
   tie-broken on the source's own record id, which is stable across re-seedings.
6. **Future timestamps were accepted.** The manual requires rejecting them; the check needs a
   clock, so it lives in the importer rather than the row models.
7. **Handler-level import problems were discarded** by being overwritten with the validation
   problems.
8. **Drafts could not be revalidated** when they had no match row, because the supporting offer
   was only reachable through the match. Drafts now record `offer_id` directly.
9. **Duplicate-review thresholds were far too loose**, flagging 12 genuinely different cars.
   Tightened to 1,000 km, 14 days and all three signals agreeing.
10. **Streamlit widget keys collided** when one offer appeared under two briefs. Keys are now
    scoped per match.
11. **Evidence-type exclusions were invisible**, filtered out in SQL before the user could see
    why a dealer bid was not in the median. The pricing service now decides, so every exclusion
    has a displayed reason.
12. **A rejected match still scored points** for having a confirmed requirement, letting a known
    conflict out-rank an open one. A rejection and an expired brief now score zero.

Found later, by driving the running app in a browser rather than only through tests:

13. **The worked-example toggle silently did nothing.** Streamlit ignores a changed `value` on a
    keyed widget once session state exists, so the screen calculated a scenario from zeroed cost
    lines and showed a €15,000 contribution where the manual says €8,500. The worksheet's widget
    keys now vary with the prefill state. Four `AppTest` cases pin the on-screen figures, because
    the service-level tests had been passing throughout.
14. **Call briefs leaked internals.** A brief meant to be read aloud contained the family key
    `mercedes_g63`, a raw Python dict of requirements, and ISO timestamps with `+00:00`. Briefs
    now say "Mercedes-AMG G 63", "no more than 25,000 km, ... physical stock, not an allocation",
    and "2026-09-27 19:51 UTC". A test asserts none of the internals can come back.
15. **Tasks were indistinguishable.** A company with five candidate offers showed five identical
    "Check availability" rows. Task labels now name the offer and its seller.
16. **Confirmation alerts read "unknown → value"**, implying a previous value had been checked and
    found missing. An event with no previous value now shows only the new one.

## Known limitations

These are properties of the prototype, not defects:

- **No live market coverage.** Stated on every screen.
- **Freshness thresholds are product assumptions** (24 h supply, 14 d comparables, 30 d briefs,
  90 d contacts), configurable in `.env`, not market or legal standards.
- **Match weights are untested product choices** (40/25/15/10/10), not predictors of closing.
- **The minimum of three comparables is a conservative prototype setting**, not a valuation
  standard.
- **Non-EUR amounts are excluded from comparison until a dated rate is entered.** That is now
  possible on the Exchange rates screen. A conversion is arithmetic only: it does not explain why
  a car is priced differently in another market, and the interface says so.
- **No automatic monetary adjustment** for options or mileage. An analyst may enter one; it is
  labelled as an assumption and its effect on the median is reported separately.
- **Scenarios remain single-currency.** Conversion applies to comparable sets and to a
  cross-currency budget check, not to a cost worksheet.
- **A cost template is not a tax engine.** It records reviewed assumptions for a route. It does
  not compute anyone's liability, and it cannot tell whether a figure is still correct.
- **Only three vehicle fields are resolvable as contradictions**: seats, mileage and powertrain.
  Anything else needs a deliberate edit.
- **Contact-policy recording is a decision log, not a legal engine.** It records an accountable
  human decision. It does not determine what any jurisdiction permits.
- **Source review is likewise a decision log.** It records what a reviewer asserted, and refuses
  some obviously unsupported combinations. It cannot tell whether the assertion is true.
- **A specification correction still does not update the reviewed vehicle by itself.** By
  design. It is now resolvable in the app, but only by a named person leaving a dated note, and
  only for seats, mileage and powertrain.
- **Single user, localhost.** No authentication, no access control, no backup schedule. Every
  accountability field is text the user typed about themselves, which is why multi-user is a
  rebuild rather than an addition. See `docs/MULTI_USER_DESIGN.md`.
- **Windows console encoding** can mangle the euro sign in CLI output. The stored data is
  correct; use `PYTHONIOENCODING=utf-8` if it matters.

## Built in the second session

The four next steps from the first session are done. Each is a service with its own tests, a
screen, and a CLI route where one made sense. Migration `0002_adjustments_and_review.sql`
applies incrementally to an existing database.

### 1. Accepting a source correction (`desk/services/corrections.py`)

A contradiction between the newest observation and the reviewed vehicle record now has two
recorded outcomes rather than one unactionable warning:

- **Accept the source** updates the reviewed record, writes a `change_log` entry naming who
  decided, stores a `fact_reviews` row with a mandatory dated note and the deciding observation,
  and acknowledges the alert.
- **Keep the reviewed value** is equally a decision and equally recorded, and stops that same
  claim being raised again. A *different* later value is still raised.

Both require a named person and a note; neither touches the observation history. A test follows
the whole path: a corrected seat count turns a `specification_fit` into a `no_match` after a
recompute, which is the point of the feature.

`desk contradictions` lists what is outstanding, and the Today screen counts it under "Facts to
confirm". Only seats, mileage and powertrain are resolvable this way.

### 2. Dated FX conversion (`desk/services/fx.py`)

A non-EUR price is still excluded from comparison, until someone enters a rate with a direction,
a date and a stated origin. Then it is converted, and the rate and its date travel with the
figure everywhere it is shown.

The refusals matter as much as the conversion. An unattributed rate, a non-positive rate, a rate
dated *after* the moment being analysed (that would be hindsight) and a rate older than the
seven-day window are all rejected rather than used. The inverse direction is derived, so only one
direction is stored and the two cannot drift apart.

It also resolves a cross-currency *budget* in the matching rules, which is where the GBP 7 Series
actually blocked. The demonstration now shows both halves: the 7 Series analysis reports
**insufficient comparables** until a rate exists, then reaches a median with the converted
comparable clearly labelled.

Screen: Sources and imports, Exchange rates tab. CLI: `desk fx`.

### 3. Analyst adjustments (`desk/services/adjustments.py`)

Nothing is adjusted automatically, as before. An analyst may now enter a reasoned adjustment on
one comparable. It is stored with an author and a reason, and the **median before adjustments**
is shown next to the median, so the effect of an assumption is never mistaken for evidence.

Guards: a reason and an author are mandatory, a zero adjustment is refused, an adjustment in the
wrong currency is not applied, and an adjustment worth more than 25 per cent of the price is
refused on the grounds that the two cars are probably not comparable in the first place.
Retiring an adjustment stops it applying but keeps the record.

Screen: Matches, inside a comparable set, under Analyst adjustments.

### 4. Recorded source review (`desk/services/source_review.py`)

Enabling a source is no longer a hand edit. A review records the reviewer, the scope, the
retention and export rules, the limits, what the permission rests on, and a date it lapses.
Reviews are kept as history rather than overwritten.

The validation enforces the distinction the manual insists on: reading a licensed provider's
published terms is not the same as holding the account scope those terms describe, so that
combination is refused outright. An approval also needs a stated use, stated evidence, and a host
scope for anything network-facing. Approving in demo mode warns that it is a rehearsal, and a
test confirms a demo source still cannot fetch afterwards.

Screen: Sources and imports, Review a source tab. CLI: `desk review-source --source <id>`.

## Defects found and fixed in the second session

17. **A confirmation was wiped before it could be read.** Calling `st.rerun()` straight after
    `st.success()` discards the message. Confirmations are now carried across the rerun in
    session state and rendered at the top of the next pass. Two `AppTest` cases pin it.
18. **A nineteenth comparable crept into the fixtures.** The manual specifies eighteen. Rather
    than add one for the FX demonstration, an existing GBP comparable was repurposed as a
    euro-priced Irish car, which makes the demonstration stronger: the set now falls below the
    three-comparable threshold without a rate.

## Built in the third session

Steps 2 to 5 from the second session are done, as far as they can be without an authorisation
that is not the builder's to give. Migration `0003_templates_and_runs.sql` applies incrementally.

### The live refresh path is real (`desk/services/refresh.py`)

`refresh-source` previously checked the policy and then printed that no connector was wired up.
That was a button that did nothing, which `AGENTS.md` forbids describing as done. It now:

1. asks `SourcePolicy` whether the fetch may happen — a refusal opens no run and writes nothing;
2. opens a run record, so a failure is accounted for rather than invisible;
3. calls the connector, which remains the only thing that touches the network;
4. maps the records into the ordinary import contract and applies them in one transaction;
5. writes field-level evidence linked to the rows it created;
6. closes the run with its counts, its errors and its attribution.

A failure is handled deliberately: offers from that source are marked as a failed check, which is
neither a sale nor a price change, and the last successful observation is untouched. An adapter
raising an unexpected exception is caught, so a bug cannot corrupt stored records.

The CLI needs `--yes` to actually fetch; without it the command reports what it would do.
`desk runs` shows what the refreshes did, including the failures.

Fourteen tests cover this **entirely offline**, using a stub fetcher over the saved synthetic
provider responses. Nothing in the suite contacts anything.

### Capturing a requirement on a call (`desk/services/briefs.py`, Capture a requirement screen)

A screen for typing in what a buyer actually said, while it is fresh. It reuses the ordinary
import validation, so a hand-typed requirement is held to the same standard as an imported one: a
confirmed brief must name who confirmed it, a budget must state its basis and regime.

It also says what is missing without refusing to save: no budget, a budget with no basis, no hard
requirement at all ("almost any car of this family will match"), no date, not confirmed. A field
the salesperson left alone is dropped rather than stored as a rule.

### Reviewed cost templates (`desk/services/cost_templates.py`, Cost templates tab)

Registration tax and the rest were being retyped into every worksheet. A template is a reviewed
set of figures for one route, with a named reviewer, a date, and a stated basis, and it lapses.

The important property is what it refuses to do: **a template line with no amount arrives as
unknown and still blocks a complete scenario.** The seeded demo template demonstrates exactly
this — its registration tax line is deliberately unpriced, so loading it produces a worksheet
that correctly reports Incomplete. A template supplies defaults; it does not supply facts, and
every line stays editable because the car in front of you may not match the assumption.

A lapsed template is still offered, with a warning, rather than hidden: hiding it would silently
produce an empty cost sheet instead of a visible caution.

### Multi-user: designed, deliberately not built (`docs/MULTI_USER_DESIGN.md`)

The honest finding is that multi-user is a different application, and the document says why at
length. The short version: every accountability field in this system is currently **text the user
typed about themselves**, which is defensible for one person on one machine and worthless for
two. A login screen on top of that would make the audit trail look trustworthy without making it
so.

The document sets out what a real version needs, in dependency order, and what must survive the
change. It also offers a cheaper honest middle step — one shared machine with named sessions —
which would fix attribution without pretending to offer access control the prototype cannot
enforce.

### The pilot conversation (`docs/PILOT_QUESTIONS.md`)

The section 13 questions, written out to take into the meeting, with the five specific unlocks to
ask for in the order that adds most value. An authorised stock export is first by a distance;
everything else in the roadmap is downstream of it.

## Defects found and fixed in the third session

19. **`refresh-source` was a mock button.** It reported the policy decision and then printed that
    nothing was implemented. Now built and tested.
20. **Two runs could share a timestamp**, making `ORDER BY started_at` ambiguous in the runs
    listing — the same class of determinism bug as the comparable deduplication. Tie-broken on id.
21. **A dataframe column mixed integers and strings**, which could not be serialised for display.
    Made consistently text, with a regression test.
22. **The migration test seeded data the newest migration provides**, so it broke as soon as the
    demo snapshot grew. Restructured to use only what the older schema offers.

## Built in the fourth session: calibrating against real adverts

The 38 saved mobile.de adverts in the parent folder were read as **background research**, and
`docs/STOCK_PROFILE.md` records what they show. Nothing from them was imported: the manual
directs that a genuine demonstration uses synthetic fixtures or a company-authorised export, and
mobile.de stays `blocked` in the source register with no adapter.

The audit changed the picture in one important way. **33 of the 38 cars are new, with 0-30 km.**
This is not a used-car trading business but a new and nearly-new export operation, which moves
the EU new-means-of-transport test from a footnote to the mechanism of the trade.

### Model coverage now matches the stock

`SUPPORTED_FAMILIES` gained `mercedes_gle`, `mercedes_glc` and `mercedes_gls`. Those three are
eight of the 38 cars, a larger cluster than the G 63, and they take coverage of the observed
stock from 61% to 82%. The long tail — one Rolls-Royce, one Ferrari, one ALPINA, one Range Rover
— is still left to go to review rather than guessed at.

### The new-means-of-transport test is now on the record and on the brief

It was computed and tested but shown nowhere. It is now a line on the vehicle record and a
**Cross-border basis** line on every call brief. When the registration date or the mileage is
missing it says it cannot be determined and adds a missing fact, rather than assuming.

### An allocation says so in a sentence

Thirteen per cent of the real stock is a build slot rather than a car. That was a badge in a row
of badges, which is easy to read past, so the match screen now states it in words alongside the
date the buyer said they needed a car by.

## Defects found and fixed in the fourth session

23. **The S-Class matcher did not match a single real advert.** It was built from family-name
    tokens — `s class`, `s-class`, `sklasse`, `s klasse` — and real listings are titled "S 450"
    and "S 580", which contain none of them. All seven S-Class cars in the audit, the largest
    cluster in the stock, would have gone to review. The trim designations are now needles in
    their own right. The synthetic fixtures had hidden this completely, because they were written
    around the internal key `mercedes_s_class`, which does contain "s class" once the underscores
    become spaces: the test was checking the naming convention rather than the world.
24. **Family matching on bare substrings produced false matches in both directions.** `gle`
    matches the word "single", and `s 63` matches "gls 63", so a GLS 63 matched two families at
    once and went to review as ambiguous. Needles are now matched at a word *start*; a trailing
    boundary would be wrong, because `740` has to keep matching "740d".
25. **Nothing separated two variants of the same generation.** An S 450 and an S 580 share the
    W223 body, so `require_same_generation` let both into one comparable set, and the real spread
    between them is €131,000 to €192,000. `require_same_variant` is now on by default.
26. **The first version of that rule compared the variant strings exactly**, which splits "G 63"
    from "G 63 4MATIC" — one car written at two levels of detail. The demo snapshot contains
    both spellings (one `G 63`, eighteen `G 63 4MATIC`), so the rule was one retail comparable
    away from silently shrinking a comparable set over a typed suffix. It now compares the
    numeric designation instead: a suffix somebody did or did not type changes nothing, while an
    S 450 is still held apart from an S 580.

    Measured, so the record is accurate: on the current demo database the G 63 median is
    **€233,500 from six vehicles under all three rules** — designation, exact-string, and the
    filter switched off. The single `G 63` car is supply-side rather than a retail comparable,
    so it never entered this particular set. The exact-string rule was wrong on its own terms,
    not because it moved this number.

### Deliberately not done

`docs/STOCK_PROFILE.md` also recommends recalibrating the demo G 63 figures towards the real
€211,000-220,000 band. That has **not** been done. The manual fixes the worked example at
€200,000 against a €235,000 median and several acceptance cases assert those figures, so moving
the seed would break the mandatory arithmetic cases to make the demo prettier. The real band is
recorded in `STOCK_PROFILE.md` instead, which is the useful part.

## Built in the fifth session: the hosted build

The prototype became a hostable web app without becoming a less honest one. The whole
argument for hosting it is narrow and worth stating plainly: **it is safe to put on a public
link because the hosted instance cannot hold real data**, not because the app became secure.
There is still no authentication and no access control.

### The public-mode guard (`desk/services/deployment.py`)

`DESK_PUBLIC=1` is set by the hosting configuration. With it set, the app refuses at startup
to run in live mode, with network access, or with runtime AI enabled, and says which of those
it objected to. It fails rather than degrading: a hosted instance that quietly dropped to demo
mode after being pointed at a live database would be worse, because somebody would believe the
live data was being served.

A fresh deployment also seeds itself once, so the first visitor to a new link sees the sample
stock rather than an empty screen. It never overwrites a database that already holds offers.

### The mobile.de adapter (`desk/connectors/mobile_de.py`)

A real adapter against the official partner API, with the payload mapping written and tested
offline against a published-shape fixture. It contains **no scraping and no HTML parsing.**

It refuses on whichever of three preconditions is missing — an approved source register entry,
credentials in the environment, a recorded base URL — and the policy gate runs first, because
holding a key is not the same as being allowed to use it. Critically, it **does not fall back
to the sample feed.** Returning sample data to a caller who asked for the market would make
every downstream figure a fabrication wearing a real source's name.

The UI says `Built · not connected · awaiting official API credentials`. It never says
"connected", and a test asserts that.

### The Opportunities screen (`desk/services/opportunities.py`, `desk/ui/opportunities.py`)

One ranked list that answers, per card: what the car is and where the row came from; whether
it is cheap against genuinely comparable cars and which ones were excluded and why; what is
left after costs or what is blocking that; and who has actually asked for one. A missing cost
can be typed in and the contribution recomputed on the spot.

**Ranking is by tier, not by a blended score.** A weighted total would let a large unverified
gap outrank a small verified contribution, and no weighting makes those commensurable — one
excludes every cost, the other survives them. The seeded demonstration shows the ordering
doing its job: a **€9,650 verified contribution sits above a €49,750 gap**, and the card says
why.

### Drafts (`desk/services/drafting.py`)

Buy-side and sell-side messages, filled from stored fields by templates — no model writes the
words, so the output is identical offline, in a test, and in front of a customer. Each draft
carries what it **asked rather than assumed** and what it **deliberately did not claim**: our
own comparable median is never shown to a seller, a waiting buyer is never mentioned to one,
and a stale confirmation becomes a question instead of an assurance.

### A realistic stock feed

The snapshot grew from 10 supply offers to 23, with 51 comparables across G 63, S-Class,
7 Series, X5, X7, GLE, GLC and GLS, plus an allocation, a car with an unstated VAT basis, a
sterling-priced car, and two families the matcher deliberately does not support. A second cost
template covers a fully-priced route, so the demonstration shows a scenario that completes
beside one that correctly refuses to.

## Defects found and fixed in the fifth session

27. **The Opportunities card listed category-fit prospects under "Who has asked for one".**
    Eleven companies that had asked for nothing appeared as buyer matches on several cards.
    This was new code of my own and it is exactly the overclaim the project exists to prevent:
    "might buy this sort of car" is not "asked for this car". Prospects are now a separate
    list, separately labelled, contributing nothing to the ranking.
28. **`enableCORS = false` in the Streamlit config.** Copied from a common deployment snippet
    without thinking. It sends `Access-Control-Allow-Origin: *` and lets any origin open a
    WebSocket to the app, which is the opposite of what a publicly reachable app wants, and
    XSRF protection does not cover it. Both protections are now on.
29. **A family and a variant that overlapped were concatenated**, producing
    "Mercedes-AMG G 63 G 63 4MATIC". `display_name` now writes the shared run of words once.
30. **`test_the_resolved_contradiction_stops_being_raised` asserted that no contradiction
    remained**, which passed for the wrong reason the moment the snapshot grew a second one.
    It now asserts that the specific one resolved is gone and the rest survive.

### Known fragility, not fixed

`test_a_later_migration_lands_on_an_existing_database_without_losing_data` moves the newest
migration file aside and restores it. Two pytest processes running concurrently will therefore
race, and one will fail with a missing migration. It is correct in a normal single run; it is
simply not safe to run the suite twice at once.

## Sensible next steps

Everything that can be built without an authorisation or an answer has been. What remains needs a
conversation first.

1. **The company conversation.** `docs/PILOT_QUESTIONS.md` is written for it. Everything below
   depends on the answers, and guessing at them is how a prototype becomes a product nobody asked
   for.
2. **An authorised stock export**, which is the single highest-value change available. It turns
   replayed snapshots into real supply monitoring.
3. **Five to ten real buyer requirements.** The matching engine is only as good as the demand
   side, and that side is currently entirely synthetic.
4. **One bounded live read**, once a source has a real review recorded against it. The machinery
   is built, tested and refuses to run without that review.
5. **Multi-user, if the pilot needs it** — starting from `docs/MULTI_USER_DESIGN.md` rather than
   from a login screen.
