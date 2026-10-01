# Luxury Car Opportunity Desk — self-contained overview

**Paste this whole document into a fresh chat.** It assumes no prior context.

> **Instruction for whoever receives this:** Below is a factual briefing on a software
> prototype and the situation it will be presented in. Turn it into a spoken interview
> script. Keep every factual claim exactly as written — the numbers, the limitations and
> the refusals are load-bearing, and several of them are checkable by the people in the
> room. Do not add capabilities, do not soften the limitations, and do not invent
> results. Where the briefing says something is *not* true, the script must say so too.

---

## 1. The situation

Max Watkinson built a working software prototype and will present it in a job interview
with a luxury car export business based near Prague. The business buys and sells
premium and performance cars — mostly new and nearly-new German-specification stock —
and exports them across Europe and beyond.

The prototype is a portfolio piece, not a delivered product. It runs on one laptop, on
synthetic data, and it is not connected to anything.

The goal of the conversation is to demonstrate judgement, not to sell software.

---

## 2. What the tool is

**One sentence:**

> It connects the cars you can buy to the people who have actually said they want one,
> prices the gap between them from genuinely comparable cars only, and refuses to give
> you a number when something it needs is unknown.

**The problem it addresses:** a dealer's difficulty is not a shortage of listings.
Anyone can see the cars. What nobody can hold in their head at once is: which car is
still genuinely available rather than still advertised; which buyer said what, when, and
whether they meant it; whether a price is high or low against cars that are actually
similar rather than cars wearing the same badge; and whether the deal survives transport,
VAT, registration tax and the money tied up while it happened.

All four are knowable. They live in four different places and go stale at four different
speeds.

---

## 3. The shape of it: four things and one rule

**Observations.** A car is a stream of sightings, not a database row. Seen at this price,
on this date, by this source. Nothing is ever overwritten — new observations are
appended. So the question "when did we last actually confirm this car exists?" always has
an answer.

**Reviewed facts.** Separately, the facts a human has checked: the VIN, the mileage, the
homologated seat count, the generation. When a source disagrees with a reviewed fact, the
contradiction is surfaced for a person to resolve. A scraper never silently overwrites a
human's judgement.

**Buyer requirements.** What a named person at a named business said they wanted, on a
date, and who confirmed it. Hard requirements are enforced as rejections, not as lower
scores: if a buyer needs five homologated seats and the car has four, that is a no.

**Money.** Comparable prices, costs, and a scenario that states what the deal contributes
after everything.

**The rule connecting them: unknown is a real value, and it fails closed.** An unknown
never becomes a zero, an average, or a convenient assumption. It blocks whatever depends
on it and names itself while doing so.

---

## 4. The seven design decisions

These are the intellectual content. Each exists because of a specific failure mode.

1. **Unknown blocks; it never becomes zero.** The commonest way a tool like this lies to
   you is by treating a missing cost as no cost. Leave a cost box empty and the scenario
   returns *Incomplete* with the reason named. It will not show a contribution figure
   built on a gap. The demo proves this against itself: the seeded cost template has a
   deliberately unpriced registration-tax line, so loading it produces a worksheet that
   correctly refuses to complete.

2. **Money is never a floating-point number.** Every price is an integer number of minor
   units — cents — with an ISO currency code, and all arithmetic is exact decimal. No
   price can drift by a cent through rounding. Related: `119.000` means a hundred and
   nineteen thousand in Prague and a hundred and nineteen in London, so parsing a price
   without knowing the locale is refused rather than guessed.

3. **Five kinds of price are never blended.** A supply asking price, a retail asking
   price, a dealer-to-dealer bid, a buyer's stated budget and a completed transaction are
   five different kinds of evidence about five different things. Averaging them produces
   a number that describes no market at all. The tool holds them apart and makes you pick
   which one you are comparing against.

4. **"Same badge" is not "comparable".** Every excluded comparable carries its reason,
   visible on screen: different generation, different powertrain, different steering side,
   mileage outside tolerance, a margin-scheme car against a VAT-qualifying one, a repeat
   of the same VIN counted once, or a different variant.

5. **Absence is not a sale.** If a check fails, or a car is not seen in a scan, that is
   not evidence it sold. It is recorded as a failed check, the last successful observation
   survives untouched, and the call brief says "do not describe this car as currently
   available." The embarrassing call is the one where you promise a car that went last
   week.

6. **Permission to contact is a recorded human decision.** The tool finds businesses; it
   does not decide you may ring them. A contact policy is recorded by a named person, on a
   named channel, for a stated purpose, with a date, and it expires. A suppression
   outranks every score in the system. A discovered phone number is never consent.

7. **Freshness has a clock, and the clock is injectable.** Supply confirmations go stale
   after 24 hours, comparables after 14 days, buyer requirements after 30 days, contact
   details after 90. Nothing in the system calls the system clock directly, which is why
   every "stale" badge is reproducible rather than dependent on when you happen to look.

Eighth, worth mentioning if asked about safety: **demo and live are separate database
files**, each stamped with its mode, every row flagged, with a guard that refuses to open
one as the other. Demo data cannot leak into a real decision by accident.

---

## 5. Verifiable facts and numbers

- **418 automated tests, all passing.** Includes two worked examples from the
  specification calculated to the cent.
- **Seven working screens.** Today, Vehicles, Businesses, Capture a requirement, Matches,
  Call preparation, Sources and imports.
- **Zero live network requests** have ever been made from the project.
- **Zero ways to send a message.** No send button, no dialler, no scheduler, no message
  credentials anywhere in the codebase.
- Built in Python with Streamlit, SQLite, Pydantic. Versioned SQL migrations applied
  incrementally, with a backup taken first.

**A real worked example from the running demo** (synthetic data):

- Asking price on the car: **€193,000**
- Nineteen cars carried the same badge; **six** passed the comparability filters
- Comparable median: **€233,500**, range €228,000 to €243,000
- Observed asking-price gap: **€40,500**
- The tool's own caption on that number, verbatim: *"This is an investigation signal, not
  profit: it excludes every cost and assumes a sale that has not happened."*
- With all costs entered: **contribution €8,500**; if the retail outcome falls 5%,
  **−€1,500**
- With registration tax left blank: **Incomplete**, with the missing fact named

**Sample exclusion reasons, verbatim from the application:**

- *"price evidence type is dealer_bid; supply quotes, dealer-to-dealer bids, retail asking
  prices and completed transactions are not blended into one market price"*
- *"same vehicle identity as an offer carrying the newer observation; counted once so it is
  not treated as independent inventory"*
- *"tax basis is not comparable: price basis differs (net versus gross) and is not
  converted"*
- *"mileage differs by 83,500 km, beyond the selected 20,000 km tolerance"*
- *"different steering side (rhd versus lhd)"*
- *"observed 2026-08-19, older than the 14-day freshness window"*

---

## 6. The strongest story: four hours with real adverts

This is the single best moment available. It is true, checkable, and shows judgement
rather than typing.

Thirty-eight of the company's **public** adverts were saved locally. Rather than loading
them into the tool as fake stock — which would have made the demo look better and proved
nothing — they were read as research and the tool was checked against them.

Four findings:

1. **The business is not what the tool assumed.** 33 of the 38 cars are new, with 0 to
   30 km. That is a new and nearly-new export operation, not a used-car trader. Which
   means almost every car meets the EU definition of a *new means of transport* — under
   six months since first use, **or** under 6,000 km — so the cross-border VAT treatment
   is the mechanism of the whole trade, not a footnote. That test is now a headline fact
   on the vehicle record and a line on every call brief.

2. **The S-Class matcher did not match a single real advert.** It looked for "S-Class",
   "S Class", "S-Klasse". Real listings are titled **"S 450"** and **"S 580"** and never
   once say "S-Class". Seven cars — the largest cluster in the stock — would have silently
   gone to manual review. The test suite never caught it because the synthetic fixtures
   were written around the internal key `mercedes_s_class`, which contains "s class" once
   the underscores are replaced. **The tests were checking a naming convention, not the
   world.**

3. **Nothing separated an S 450 from an S 580.** They share the same generation, so the
   generation filter let both into one comparable set. The real spread across the seven
   S-Class cars is **€131,000 to €192,000**. A median across that range describes no car
   anyone can buy.

4. **The first fix was also wrong.** It compared variant text exactly, which splits
   "G 63" from "G 63 4MATIC" — one car written at two levels of detail. It now compares
   the model designation instead, so a suffix somebody did or did not type changes
   nothing while an S 450 stays apart from an S 580.

**Accuracy note for the script:** that fourth fix did *not* change any number on the demo
screen — the median is €233,500 from six vehicles either way. The rule was wrong on its
own terms. Do not claim it moved a figure.

Other outcomes of the audit: GLE, GLC and GLS were added to the supported model families,
taking coverage of the observed stock from **61% to 82%**; and build slots, which are 13%
of the real stock, are now called out in a sentence on the match screen rather than being
one badge among badges.

**The line that ties it together:**

> Four hours with your real adverts found three defects that the specification, the tests
> and the demo all missed. That is the argument for a pilot: the tool is only as good as
> its contact with your actual data.

**If asked why the adverts weren't just imported:**

> Because a demo built on data scraped from your website is not a demo, it's a liability.
> I used them to calibrate what the tool should cover. I'd want an authorised export
> before anything real goes in.

---

## 7. What it is NOT — say these plainly

The boundaries are the credible part. Every one of these is checkable.

- **Not connected to mobile.de, AutoScout24 or Sauto.** They sit in a source register as
  `blocked` — recorded, described, with no adapter behind them. Three other sources are
  implemented and tested offline, and all three are `review required`, so the fetch path
  refuses to run them.
- **No live market coverage.** It analyses imported observations and replayed snapshots,
  and says so on every screen.
- **Cannot send anything.** There is no sending capability in the codebase at all.
- **Does not compute anyone's tax liability.** It records reviewed assumptions with a
  source and a date. It is not a tax engine.
- **Does not tell you a deal is profitable.** It shows a conditional contribution and
  everything that contribution depends on.
- **No AI at runtime.** Every figure comes from a rule you can read. A model that produced
  a confident-looking price with no traceable basis would be the opposite of the point.
  Where a model would genuinely help is parsing messy free-text adverts into fields — and
  that output would go into the review queue like any other source, never straight into a
  price.
- **Single user, one machine, bound to localhost.** Making it multi-user is a rebuild, not
  a login screen, and the reasons are written down.

**The key line on connectivity** — this one is worth delivering carefully:

> It is not connected to a marketplace, and not because I couldn't write the scraper.
> Every source is in a register with its legal basis and an expiry, and the fetch path
> physically refuses to run against a source nobody has approved. I'd rather show you a
> tool that asks permission than one that has already taken it.

---

## 8. How the demo runs — eight minutes on a laptop

Run it locally and narrate it. A "DEMO DATA" banner appears on every screen; leave it
there and point at it.

1. **Today** — what changed since last time. A price-change alert, €200,000 down to
   €193,000, with its reason attached. Every alert explains itself and fires once.
2. **Vehicles** — open a car. Show what the source says beside what a human has reviewed.
   Then the EU new-means-of-transport verdict, with both limbs of the test spelled out.
3. **A contradiction** — a car where the source disagrees with the reviewed record. It
   does not take the newer number. It asks a person.
4. **Matches** — every rule that matched, failed, or is still unverified, in three
   columns.
5. **The exclusions** — open "Excluded (13)" beside "Included (6)" and read two or three
   reasons aloud. This is the moment that lands.
6. **The gap** — €40,500, then read the tool's own caveat off the screen.
7. **The scenario** — fill the costs, get €8,500. Then delete one cost line and
   recalculate: *Incomplete*, with the missing fact named. That contrast is the whole
   product.
8. **Call preparation** — the brief, the missing-facts section, and the line at the
   bottom: *"There is no Send button and no Auto-dial. A person makes the call."*

If something breaks, say so plainly and move on.

---

## 9. Likely questions

**"Is it connected to mobile.de?"** — No. See section 7.

**"So where does the data come from?"** — Imports and replayed snapshots. The import path
validates everything before it writes and rejects the whole file if any row is wrong. An
authorised stock export is the highest-value change available.

**"Could you not just scrape it?"** — Technically yes. I didn't, because the first thing
you'd ask a supplier is whether they had permission, and I'd rather have the answer ready.

**"How do we know the numbers are right?"** — 418 tests, including the specification's
worked examples to the cent. The better answer is that it tells you when it doesn't know,
which is the failure mode that actually costs money.

**"How long did this take?"** — Be straightforward, then: *the build was the fast part;
deciding what it should refuse to do took longer.*

**"Why should we hire you?"** — *Because the hard part isn't the software, it's knowing
which number you're not allowed to make up. I spent this build finding those places and
closing them, then checked it against your real adverts and found three more.*

---

## 10. What to ask for

In this order. The first is worth more than the rest combined.

1. **An authorised stock export.** Any format. Turns replayed snapshots into real supply
   monitoring.
2. **Five to ten real buyer requirements**, with permission to record them. The matching
   engine is the strongest part of the tool and is currently fed entirely synthetic demand.
3. **Registration tax and recoverability for one destination country**, with a source and
   a date. That is what turns *Incomplete* into a complete scenario.
4. **A decision on contact routes** for one market, so the outreach gates have a real
   policy to enforce.
5. **Approval for one bounded live read.** The machinery is built and tested and refuses
   to run without a recorded review. What's missing is a signature, not code.

And one question to put to them, which the data cannot answer:

> Do you buy mostly against confirmed orders, or mostly into stock? If it's orders, the
> buyer side matters most. If it's stock, supply monitoring does. Your adverts can't tell
> me which, and it changes what I'd build first.

---

## 11. Claims the script must never make

Every one is false and checkable.

- "It's connected to mobile.de / AutoScout24 / Sauto."
- "It monitors the European market."
- "It found a profitable car."
- "It found you a buyer."
- "It tells you the profit on a deal." *(It shows a conditional contribution.)*
- "It handles the VAT." *(It records reviewed assumptions.)*
- "It's ready to use." *(Working prototype, single user, synthetic data, awaiting an
  authorised source.)*
- Anything implying possession of the company's stock list. The adverts were public, and
  what mattered was being careful about what happened next.

---

## 12. Tone

Plain, specific, unhurried. The pitch is competence and restraint, not enthusiasm. Avoid
salesmanship — the tool's whole argument is that it doesn't oversell, so a script that
oversells it contradicts the product.

A closing line, if one is wanted:

> I built the version that tells you when it doesn't know, because in a business where one
> wrong car costs more than the software ever will, that's the only version worth having.
