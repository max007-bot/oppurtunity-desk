# Interview brief: what you built, and how to present it

Written for Max, to be read before the conversation. It has three jobs: make sure
you can explain the thing accurately without notes, give you the actual words,
and make sure nothing in the room surprises you.

The single most important line in this document is this one:

> **The credibility of this tool is its refusal to guess. Lead with that, not
> around it.**

Everyone who pitches a car tool says it finds profit. You are going to show them
one that tells you when it cannot, and explain why that is worth more.

---

## Part 1 — What it is

### The one-sentence version

> It connects the cars you can buy to the people who have actually said they want
> one, prices the gap between them from genuinely comparable cars only, and
> refuses to give you a number when something it needs is unknown.

### The thirty-second version

> A dealer's real problem is not a shortage of listings, it is that nobody can
> hold the whole picture at once: which car is still actually available, which
> buyer said what and when, whether the price is high or low against genuinely
> similar cars, and whether the deal still works after transport, VAT and
> registration.
>
> I built a desk that holds all four together. It watches for changes in the cars
> you are tracking, matches them against buying requirements somebody has
> recorded from a real conversation, works out the gap against comparable cars,
> and prepares the call.
>
> What it will not do is guess. If the VAT basis is unknown, it will not produce
> a contribution figure — it returns *Incomplete* and tells you which fact is
> missing. That is a deliberate design choice and it is the reason I would trust
> it.

### The two-minute version, walking through the shape

There are four things in the system and one rule connecting them.

**One — observations.** A car is not a record, it is a stream of observations. It
was seen at this price on this date by this source. Nothing ever overwrites an
observation: new ones get appended. So you can always answer "when did we last
actually confirm this car exists?" rather than "what does our database currently
say?"

**Two — reviewed facts.** Separately, there are the facts a human has checked:
the VIN, the mileage, the homologated seat count, the generation. A source can
*disagree* with a reviewed fact, and when that happens the system shows you the
contradiction and asks you to resolve it. It never silently overwrites a person's
judgement with a scraper's output.

**Three — buyer requirements.** What a named person at a named business actually
said they wanted, on a date, with who confirmed it. Hard requirements are
enforced strictly: if they need five homologated seats and the car has four, that
is a rejection, not a lower score.

**Four — money.** Comparable prices, costs, and a scenario that tells you what
the deal contributes after everything.

The rule connecting them: **unknown is a real value and it fails closed.** An
unknown does not become a zero, an average, or an optimistic assumption. It
blocks the thing that depends on it and says so.

---

## Part 2 — The seven decisions that are the actual work

If they are technical, this is the part that will land. Each of these is a
decision someone had to make, and each one has a failure mode behind it.

### 1. Unknown blocks, it never becomes zero

The commonest way a tool like this lies to you is by treating a missing cost as
no cost. Leave the registration-tax box empty here and the scenario returns
**Incomplete** with the reason. It will not show you a contribution figure built
on a gap.

The demo proves this against itself: the seeded cost template has a deliberately
unpriced registration-tax line, so loading it produces a worksheet that correctly
refuses to complete.

### 2. Money is never a floating-point number

Every price is stored as an integer number of minor units — cents — with an ISO
currency code, and all arithmetic is exact decimal. No price in this system can
drift by a cent through rounding.

There is a related trap this handles: `119.000` means a hundred and nineteen
thousand in Prague and a hundred and nineteen in London. Parsing a price without
knowing the locale is refused rather than guessed.

### 3. Three kinds of price are never blended

A supply asking price, a retail asking price, a dealer-to-dealer bid, a buyer's
stated budget and a completed transaction are five different kinds of evidence
about five different things. Averaging them produces a number that describes no
market at all.

The tool holds them apart and makes you pick which one you are comparing against.

### 4. "Same badge" is not "comparable"

Every excluded comparable carries its reason, visible on screen. Different
generation. Different powertrain. Different steering side. Mileage outside the
tolerance you set. A margin-scheme car being compared against a VAT-qualifying
one. A repeat of the same VIN, which is one car and must not count twice.

And — added after looking at their actual adverts — a different variant. More on
that in Part 4, because it is your best story.

### 5. Absence is not a sale

If a check fails, or a car is not seen in a scan, that is **not** evidence it
sold. It is recorded as a failed check, the last successful observation survives
untouched, and the call brief says "do not describe this car as currently
available."

This is a small thing that matters enormously in practice, because the
embarrassing phone call is the one where you tell a buyer a car is available and
it went last week.

### 6. Permission to contact is a recorded human decision

The tool finds businesses. It does not decide that you may ring them. A contact
policy is recorded by a named person, on a named channel, for a stated purpose,
with a date, and it expires. A suppression outranks every score in the system.

And there is no Send button. There is no dialler, no scheduler, no mail
credentials anywhere in the codebase. It prepares a brief; a person makes the
call. If they ask whether it can do outreach automatically: **no, and that is on
purpose.**

### 7. Freshness has a clock and the clock is injectable

Supply confirmations go stale after 24 hours, comparables after 14 days, buyer
briefs after 30 days, contact details after 90. Nothing in the system calls
`datetime.now()` directly — the clock is passed in, which is why the whole thing
is testable and why every "stale" badge is reproducible rather than dependent on
when you happen to look.

---

## Part 3 — How this maps to the idea you gave me

You described it as: *a client finder and potential car finder, that notifies us
when a car under value appears, and finds the best people to sell it to.*

Here is the honest mapping. Read this carefully, because the gap between your
description and the artefact is the one place you could get caught out.

| What you asked for | What exists | The honest sentence |
|---|---|---|
| Notifies us when an under-value car appears | **Real.** Change events fire on price drops and availability changes, each with an explanation, deduplicated so one event alerts once. | "It alerts on price and availability changes against what it is tracking." |
| Under value | **Real, but named precisely.** It computes an asking-price gap against the comparable median and labels it an *investigation signal, not profit.* | "It shows the gap and tells you exactly what the gap excludes." |
| Finds the best people to sell it to | **Real for recorded demand.** Matching against buyer briefs is the strongest part of the system. | "It ranks against requirements somebody actually recorded." |
| Finds people generally | **Partly.** It can hold discovered business candidates, but a discovered business is a *candidate*, never a buyer, and a found phone number is never permission. | "It finds candidates. It does not invent demand." |
| Live market coverage | **Does not exist.** | "It is not connected to a live feed, and that is the next thing I would need from you." |

### The one you must not fumble

**It is not connected to mobile.de, AutoScout24 or Sauto.** Those sit in the
source register as `blocked` — recorded, described, with no adapter behind them,
because scraping a marketplace against its terms is not something I would build
into a tool I was asking a business to rely on. No live request has ever been
made from this project.

If you say "it's connected to mobile.de" and someone asks "under what licence?",
you have a problem. If you say the sentence below, you have a credential.

> It is not connected to a marketplace, and not because I could not write the
> scraper. Every source is in a register with its legal basis and an expiry, and
> the fetch path physically refuses to run against a source nobody has approved.
> I would rather show you a tool that asks permission than one that has already
> taken it.

That sentence is worth more in a job interview than a working scraper.

---

## Part 4 — Your strongest single moment

This is the story to tell if you only get to tell one. It is true, it is
checkable, and it demonstrates judgement rather than typing.

**What happened:** you had 38 of their real adverts saved locally. Instead of
loading them into the tool as fake stock — which would have made the demo look
better and proved nothing — they were read as research, and the tool was checked
against them.

**What it found, immediately:**

1. **The business is not what the tool assumed.** 33 of the 38 cars are new, with
   0 to 30 km. That is not a used-car trader, it is a new and nearly-new export
   operation. Which means almost every car meets the EU definition of a *new
   means of transport* — under six months since first use, or under 6,000 km — so
   the cross-border VAT treatment is not a footnote, it is the mechanism of the
   whole trade. That is now a headline line on the vehicle record and on every
   call brief.

2. **The tool's S-Class matcher did not match a single real advert.** It looked
   for "S-Class", "S Class", "S-Klasse". Real listings are titled **"S 450"** and
   **"S 580"** and never once say "S-Class". Seven cars — the largest cluster in
   their stock — would have silently gone to manual review.

   The reason the test suite never caught it is the good part: the synthetic
   fixtures were written around the internal key `mercedes_s_class`, which
   contains "s class" once you replace the underscores. **The tests were checking
   my naming convention, not the world.**

3. **Nothing separated an S 450 from an S 580.** They share the W223 body, so the
   generation filter let both into one comparable set. The real spread across
   their seven S-Class cars is **€131,000 to €192,000.** A median across that
   range describes no car anyone can buy.

4. **And then the fix was wrong too.** The first version compared variant strings
   exactly, which splits "G 63" from "G 63 4MATIC" — one car written at two levels
   of detail. The demo data carries both spellings, so the rule was one record
   away from quietly shrinking a comparable set over a suffix somebody did or did
   not type. It now compares the model designation, which holds an S 450 apart
   from an S 580 and leaves a G 63 alone.

   If you tell this story, tell it this way. The rule was wrong on its own terms.
   It did not move any number on the demo screen — the median is €233,500 from
   six vehicles either way — and claiming it did would be exactly the kind of
   unearned claim the rest of this tool exists to prevent.

**The line that ties it together:**

> Four hours with your real adverts found three defects that the specification,
> the tests and the demo all missed. That is the argument for a pilot: the tool
> is only as good as its contact with your actual data, and it has now had four
> hours of it.

**The judgement line, if they ask why you did not just import the adverts:**

> Because a demo built on data I scraped from your website is not a demo, it is a
> liability. I used them to calibrate what the tool should cover, and I would
> want an authorised export before anything real goes in.

---

## Part 5 — The demo, in eight minutes

Run it on your laptop. Reasons for that decision are in Part 7.

Before you start: `desk reset-demo` then `desk seed-demo`, so you begin clean.
The banner says **DEMO DATA** on every screen — leave it there, and point at it.

**Minute 1 — Today.** "This is what changed since I last looked." Point at the
price-change alert: €200,000 down to €193,000, with the reason attached. "Every
alert explains itself and fires once."

**Minute 2 — Vehicles.** Open a car. Show the split: what the *source* says on the
left, what a *human has reviewed* in the middle. Then the green box: **EU new
means of transport: yes**, with both limbs of the test spelled out. "For a
business exporting nearly-new cars, this decides how the invoice is written."

**Minute 3 — the contradiction.** Find a car where the source disagrees with the
reviewed record. "It does not silently take the newer number. It asks a person."

**Minute 4 — Matches.** Open the specification fit. Every rule that matched,
every rule that failed, every rule still unverified, in three columns. "A
specification fit means every mandatory fact matches. It is not evidence anyone
will buy."

**Minute 5 — the exclusions.** This is the moment. Open **Excluded (13)** next to
**Included (6)**. Read two or three reasons aloud. "Thirteen cars wearing the same
badge are not comparable, and it tells you why for each one."

**Minute 6 — the gap.** €40,500 between the asking price and the comparable
median — and then read the tool's own caveat off the screen: *"This is an
investigation signal, not profit: it excludes every cost and assumes a sale that
has not happened."* Let that sit.

**Minute 7 — the scenario.** Fill the cost lines. Get the complete answer:
**€8,500 contribution.** Then *delete one cost line* and press calculate again.
**Incomplete**, with the missing fact named. That contrast is the whole product.

**Minute 8 — Call preparation.** Show the brief. Point at the missing-facts
section — "it leaves these out rather than filling them in" — and at the bottom
of the screen: **"There is no Send button and no Auto-dial. A person makes the
call."**

Then stop talking.

### If something breaks

Say so plainly and move on. "That is a prototype on a laptop" costs you nothing.
Pretending it did not happen costs you everything.

---

## Part 6 — The questions they will ask

**"Is it connected to mobile.de?"**
> No. It is in the source register as blocked, with no adapter. Every source has
> a recorded legal basis and an expiry, and the fetch path refuses to run against
> one nobody has approved. Getting authorised access is the first thing I would
> ask you for.

**"So where does the data come from?"**
> Imports and replayed snapshots today. The import path validates everything
> before it writes, and refuses the file as a whole if any row is wrong. An
> authorised stock export from you is the single highest-value change available.

**"Could you not just scrape it?"**
> Technically yes. I did not, because the first thing you would ask a supplier is
> whether they had permission, and I would rather have the answer ready.

**"Does it use AI?"**
> Not at runtime, deliberately. Every number on screen comes from a rule you can
> read, and every exclusion carries its reason. A model that produced a
> confident-looking price with no traceable basis would be the opposite of what
> this is for. Where a model helps is in the messy part — parsing a free-text
> advert into fields — and that output would go into the review queue like any
> other source, never straight into a price.

**"How do we know the numbers are right?"**
> Four hundred and eighteen tests, including the two worked examples from the
> specification calculated to the cent. But the better answer is that the tool
> tells you when it does not know, which is the failure mode you actually care
> about.

**"What can't it do?"**
Answer plainly. It is the credible part:
> No live market coverage. It cannot send anything. It does not compute anyone's
> tax liability — it records reviewed assumptions with a date and a source. It
> does not tell you a deal is profitable, it shows a conditional contribution and
> what it depends on. And it is one person on one machine; making it multi-user
> is a rebuild, not a login screen, and I have written down why.

**"How long did this take?"**
Be straightforward. And add:
> The build was the fast part. Deciding what it should refuse to do took longer.

**"Why should we hire you for this?"**
> Because the hard part of this problem is not the software, it is knowing which
> number you are not allowed to make up. I have spent this build finding those
> places and closing them, and then I checked it against your actual adverts and
> found three more.

---

## Part 7 — Should you host it so they can look on a phone?

**Short answer: no for the app, yes for a page about it.**

### Why not the app

1. **It has no authentication.** None. Anyone with the link sees everything.
   Putting a tool that holds contact records and buyer requirements on a public
   URL is exactly the judgement failure you are trying to demonstrate you do not
   have. They will notice.
2. **It would look worse, not better.** It is a dense analyst tool: wide tables,
   five-column filter rows, expanders. On a phone it becomes a column of
   scrolling. The thing that makes it impressive — thirteen exclusion reasons
   beside six inclusions — is unreadable at 390 pixels.
3. **A live link is a thing that can fail in the room.** A laptop you have
   already tested is not.
4. **The demo needs narration.** Left alone with it, someone sees a form with
   empty cost boxes. Walked through it, they see a tool that refuses to lie. You
   want to be there.

### What to do instead

**Two artefacts with two different jobs.**

- **The laptop** runs the real thing, driven by you. That is the demo.
- **A single web page** explains what it is, shows the reasoning, and is
  comfortable on a phone. That is the leave-behind — the thing they open on the
  train afterwards, or forward to whoever was not in the room.

That page has been built and published. It holds no data, talks to nothing, and
cannot break. Send the link *after* the meeting, not before: you want them
watching you, not reading ahead.

### On making it look like their website

The page is built in the same register — dark, restrained, photography-led
typography, the way premium dealer sites present themselves — but it is
presented as **your** work, with your name on it, and it does not use their
branding, logo or name as though it came from them. A page that looked like it
was published *by* them would be a problem for you rather than a compliment,
especially in a hiring conversation. If you want it moved closer to their house
style, that is easy to do and worth doing with their actual site open in front of
us.

---

## Part 8 — What to ask them for

Ask for these in this order. The full version is in `docs/PILOT_QUESTIONS.md`.

1. **An authorised stock export.** Any format. This is worth more than every
   other item combined, because it turns replayed snapshots into real supply
   monitoring.
2. **Five to ten real buyer requirements**, with permission to record them. The
   matching engine is the best part of the tool and it is currently being fed
   entirely synthetic demand.
3. **Registration tax and recoverability for one destination country**, with a
   source and a date. That is what turns *Incomplete* into a complete scenario.
4. **A decision on contact routes** for one market, so the outreach gates have a
   real policy to enforce.
5. **Approval for one bounded live read.** The machinery is built and tested and
   refuses to run without a recorded review. What is missing is a signature, not
   code.

And ask them the question that decides what to build next, which the data cannot
answer:

> Do you buy mostly against confirmed orders, or mostly into stock? If it is
> orders, the buyer side matters most. If it is stock, supply monitoring does.
> The adverts cannot tell me which, and it changes what I would build first.

---

## Part 9 — Things not to say

Every one of these is checkable, and false.

- ❌ "It's connected to mobile.de / AutoScout24 / Sauto."
- ❌ "It monitors the European market."
- ❌ "It found a profitable car."
- ❌ "It found you a buyer."
- ❌ "It tells you the profit on a deal." *(It shows a conditional contribution
  and what it depends on.)*
- ❌ "It handles the VAT." *(It records reviewed assumptions. It is not a tax
  engine and it says so.)*
- ❌ "It's ready to use." *(It is a working prototype, single-user, on
  synthetic data, waiting on an authorised source.)*
- ❌ Anything about the 38 adverts that implies you have their stock list. You
  looked at their public adverts, which anybody can do, and you were careful with
  what you did next. Say it that way.

---

## The closing line

If you need one sentence to end on:

> I built the version that tells you when it does not know, because in a business
> where one wrong car costs more than the software ever will, that is the only
> version worth having.
