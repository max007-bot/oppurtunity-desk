# Luxury Car Opportunity Desk

A local, single-user research prototype that connects permitted vehicle observations,
comparable prices, business prospects and buyer requirements, and tells you **what changed,
which buyer might fit, what remains uncertain and what to do next**.

It runs entirely offline against clearly marked synthetic data. It has no paid data
subscription, no runtime AI key, and no way to contact anyone.

Built from `docs/BUILD_MANUAL.md`, which remains the implementation authority.

## What it is not

Stating this plainly, because the value of the tool depends on it:

- **Not live market coverage.** No authorised vehicle-price feed is connected. It analyses
  imported observations and replayed snapshots. The interface says so on every screen.
- **Not an arbitrage guarantee.** It produces an *observed asking-price gap* (an investigation
  signal), a *conditional scenario contribution*, and a *cash requirement*. These are three
  different numbers and it never merges them into "profit".
- **Not an outreach tool.** There is no Send button, no dialler, no scheduler and no message
  credentials. It prepares a brief; a person makes the call.
- **Not connected to anything yet.** The live refresh path is built and tested, but every
  network-facing source is `review_required` or `blocked`, so it refuses to run. No live request
  has ever been made from this project.
- **Not a production system.** One user, one machine, bound to localhost. A shared deployment
  would first need authentication, access control, backups and a data-sharing review.

## Setup

Requires Python 3.11+. Tested on Python 3.12.10, Windows 10 Pro (19045), 28 September 2026.
On Windows, calling the virtual environment's Python directly avoids activation-policy problems.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m desk.cli init-db --mode demo
.\.venv\Scripts\python.exe -m desk.cli seed-demo
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

On macOS or Linux use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`.

Then open <http://127.0.0.1:8501>. Optionally copy `.env.example` to `.env` to change the time
zone or the freshness thresholds; the defaults need no configuration.

## Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

452 tests, all passing. They cover the acceptance cases in manual section 11: the mandatory
arithmetic (€8,500 and −€1,500), false-match rejection, VAT incompatibility, insufficient
comparables, idempotent replay, draft invalidation, suppression, the SSRF guard and demo/live
separation. See `docs/ACCEPTANCE.md` for the case-by-case mapping.

## The five-minute demonstration

Full script in `docs/ACCEPTANCE.md`. In short:

```powershell
.\.venv\Scripts\python.exe -m desk.cli reset-demo --yes
.\.venv\Scripts\python.exe -m desk.cli seed-demo
.\.venv\Scripts\python.exe -m desk.cli recompute
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

1. **Today** — say out loud that the data is synthetic. Show the confirmed requirement count
   next to the category-fit prospect count: a business carrying similar stock is not a buyer.
2. **Matches** — open `OFR-001 × BRF-001`. Every matched, failed and unverified rule is listed.
   Open the excluded comparables to show why the same badge is not enough. Tick *Start the cost
   lines from the reviewed template* to load the seeded route assumptions — note that its
   registration tax line is deliberately unpriced, so the scenario still reports Incomplete.
3. **Matches → cost worksheet** — turn on *Load the build manual's worked example*. The €35,000
   asking gap becomes a conditional €8,500 contribution, and −€1,500 in the downside. Under
   *Analyst adjustments*, add one and watch the median move, with the pre-adjustment median kept
   beside it.
4. **Call preparation** — generate a brief from verified facts, with its missing-fact warning.
   Record an objection on `Demo Prestige Dealer F` and watch the account leave every actionable
   list and export.
5. **Sources and imports** — show which connectors are disabled and why. Open *Exchange rates*:
   the 7 Series analysis reports insufficient comparables until a dated rate is entered, then
   reaches a median with the converted comparable labelled. Open *Review a source* and try to
   approve a licensed marketplace on the strength of its published terms; it is refused, because
   reading terms is not the same as holding the account scope they describe.

Then run `replay-demo` to show a price drop, a reservation and a specification correction
against the preserved history.

## Commands

| Command | What it does |
|---|---|
| `init-db --mode demo\|live` | Create or migrate a database and stamp its mode. |
| `seed-demo` | Load the synthetic snapshot. Demo mode only. Idempotent. |
| `replay-demo` | Replay the second snapshot: a price drop, a reservation, a specification correction. Idempotent. |
| `reset-demo --yes` | Delete and recreate the demo database only, after checking its mode stamp. |
| `validate-import <path> [--kind K]` | Validate a file and report row-level problems. Writes nothing. |
| `import-file <path> --source ID [--kind K] [--allow-partial]` | Validate then apply transactionally. Rejects the whole batch on error by default. |
| `template <kind>` | Print the documented CSV column template. |
| `recompute` | Recompute matches, comparable sets and tasks. Idempotent. |
| `fx [--from X --to Y --rate R --source-note "..."]` | List dated exchange rates, or record one. Without `--rate` it lists. |
| `contradictions` | List offers where a source disagrees with the reviewed vehicle record. |
| `review-source --source ID` | Show a source's status and its review history. |
| `refresh-source --source ID --yes [...]` | Run an approved live source. Without `--yes` it reports what it would do and fetches nothing. **Unavailable in demo mode.** |
| `runs` | What the live refreshes did, including the ones that failed. |
| `status` | Record counts and the effective status of every source. |
| `sources` | The source register as JSON. |

Nothing here starts a background job or a scheduler. A scheduled refresh should call
`refresh-source` from the operating system's own scheduler, with a single-run lock.

## Which live sources were tested

**None.** No live request has been made from this project. Every live adapter
(OpenStreetMap/Overpass, Czech ARES, the French business-search API, approved company websites)
is implemented against saved synthetic provider responses and is `review_required` in the source
register, so `may_fetch` refuses it. mobile.de, AutoScout24 and Kompass are recorded as `blocked`
with no implementation beyond the interface: there is no scraper and no invented endpoint.

Enabling any of them means reading the current official documentation, recording the approved
scope and its conditions in the source register, and setting `DESK_MODE=live` with
`DESK_ALLOW_NETWORK=true`. See `docs/SOURCE_ACCESS.md`.

## The three financial outputs

| Output | What it means | What it does not mean |
|---|---|---|
| **Observed asking-price gap** | Compatible comparable asking median minus the observed supply asking price. | Not profit. It excludes every cost and assumes a sale that has not happened. |
| **Scenario contribution** | Sale proceeds on a compatible tax basis, minus the acquisition cost, minus every included non-recoverable direct cost. | Before unmodelled overhead, referral fees and anything not listed. Conditional on its inputs. |
| **Cash requirement** | Money that must leave before collections and refunds, including recoverable VAT temporarily tied up. | Not the final cost. Recoverable tax returns. |

A missing acquisition price, price basis, VAT regime or required cost returns **Incomplete** —
never a zero-cost profit. Expected commission stays blank: no approved compensation rule exists.

## Three places a figure can change, and how you see it

Every number on screen is either something a source said, or something a person decided. The
three ways a figure can move are each labelled at the point of use:

| Change | What makes it happen | How it is shown |
|---|---|---|
| **Currency conversion** | A rate someone entered, with a direction, a date and a stated origin. | The rate and its date travel with the figure. The unconverted price stays beside it. |
| **Analyst adjustment** | A person entering an amount with a reason and their name. | The median before adjustments is shown next to the median, so the effect of the assumption is separate from the evidence. |
| **Accepted correction** | A person accepting a source's value over the reviewed one, with a dated note. | An audit entry and a stored decision. The original observation is untouched. |

Nothing else changes a figure. A connector never does.

## Layout

```text
app.py                  Streamlit entry point
desk/
  config.py             Configuration and the controlled vocabularies
  clock.py money.py     Injectable clock; exact integer-minor-unit money
  models.py             Pydantic validation for imports and connector results
  db.py migrations/     SQLite access, transactions, versioned schema
  repositories/         Row mapping and SQL
  services/             Every business rule (testable without Streamlit)
    fx.py               Dated currency conversion; nothing converts without a rate
    corrections.py      Resolving a source-versus-reviewed-record contradiction
    adjustments.py      Analyst adjustments as labelled assumptions
    source_review.py    Recording an approval, its scope and its expiry
    refresh.py          Running an approved live source and storing what came back
    briefs.py           Capturing a buying requirement during a call
    cost_templates.py   Reviewed, reusable cost assumptions for a route
  connectors/           Adapters; only fixtures and file imports are enabled
  ui/                   The six screens
  cli.py                Command line
fixtures/               Synthetic demo data and saved provider responses
tests/                  452 tests, including the acceptance invariants
docs/                   Manual, playbook, status, source access, data dictionary, acceptance
data/ exports/          Local, git-ignored
```

## Documentation

| File | Contents |
|---|---|
| `docs/BUILD_MANUAL.md` | The specification. The implementation authority. |
| `docs/STATUS.md` | What is built, what was tested, what is deliberately out of scope. Read this first when resuming. |
| `docs/SOURCE_ACCESS.md` | The source register: what each source may be used for and what is still required. |
| `docs/DATA_DICTIONARY.md` | Every table and enumerated value, and why unknown is a value. |
| `docs/ACCEPTANCE.md` | Each acceptance case mapped to the test that pins it, plus the demo script. |
| `docs/DEPLOY.md` | The four manual steps to put it on a public link, and what to check before sharing it. |
| `docs/INTERVIEW_BRIEF.md` | What this is, how to explain it, how to demonstrate it, and what not to claim. |
| `docs/STOCK_PROFILE.md` | What 38 real public adverts showed, and what changed in the code because of them. Research, not a feed. |
| `docs/PILOT_QUESTIONS.md` | The questions to put to the company before a pilot, and what to ask for, in order. |
| `docs/MULTI_USER_DESIGN.md` | Why multi-user is a different application, and what it would actually need. Deliberately not built. |
| `docs/OUTBOUND_PLAYBOOK.md` | Commercial background. Not a licence to ingest its sources. |

## Data handling

Demo and live data live in separate database files, each stamped with its mode; opening one as
the other is refused. A demo reset deletes only a file that is itself stamped demo. Every row
carries an `is_demo` flag. Credentials live in the environment and are never stored in the
database or logged. The app binds to localhost.
