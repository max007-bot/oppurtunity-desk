# Brief for drafting an email to Alexej

**Paste this whole document into a fresh chat.** It assumes no prior context.

> **Instruction for whoever receives this:** Below is a factual briefing on a
> piece of software and the person it is being sent to. Draft a short email.
>
> Keep every factual claim exactly as written. The limitations are not modesty,
> they are the product's main argument, and several are checkable by the
> recipient in about thirty seconds. Do not add capabilities. Do not describe
> anything as connected, live, or finding real deals. Where this briefing says
> something is **not** true, the email must not imply otherwise. Section 7 lists
> claims that are specifically forbidden.
>
> Aim for roughly 150–200 words in the body. The recipient is a sales lead, not
> an engineer: lead with what it does for him, not how it is built.

---

## 1. Who it is going to

**Alexej, Head of Sales** at a luxury car export business near Prague. They buy
and sell premium and performance cars — mostly new and nearly-new
German-specification stock — and export them across Europe and beyond.

Max Watkinson built the software and is sending the email himself. The context is
a job conversation, not a software sale. Nothing is being charged for and nothing
is being proposed as a purchase.

Alexej is commercial, not technical. He will care about: does this save time,
does it find money, and is it real or a mock-up.

---

## 2. What has actually been built

A working web application called the **Opportunity Desk**. It runs on
representative sample stock — not anyone's real inventory.

**The one-sentence version:**

> It finds cars priced below genuinely comparable ones, works out what is left
> after all the costs, matches each car to a buyer who asked for that exact
> specification, and drafts the message — then stops and lets a person send it.

**What a person sees when they open it.** One ranked list. Each card carries four
things in the order you would actually ask them:

1. **The car** — specification, price, seller, where the row came from, and
   whether its availability was confirmed recently enough to be relied on.
2. **Is it cheap?** — the median of genuinely comparable cars, the gap, and a
   list of every car that was thrown *out* of the comparison with the reason for
   each. On the lead example, six cars qualified and thirteen were excluded.
3. **What is left after costs** — transport, VAT, destination registration tax,
   funding. Either a contribution figure, or the word **Incomplete** with the
   missing fact named. A missing cost can be typed in and the figure recalculates
   on the spot.
4. **Who has asked for one** — buyers with a recorded requirement that this car
   satisfies, with every rule that matched, failed, or is still unverified.

Then a **draft message**, in two directions: an enquiry to the seller to buy the
car, or an offer to the buyer to sell it. Both are filled in with the real
details. Nothing is ever sent automatically.

---

## 3. The thing that makes it different

Most tools in this space show you a big number. This one shows you what the
number depends on, and refuses to produce it when something is missing.

**The ranking proves the point by itself.** It is ordered by how much is actually
*known*, not by headline size:

| Position | Car | Gap vs comparables | After costs |
|---|---|---|---|
| 1st | S 450 4MATIC | €22,750 | **€9,650 contribution** |
| 2nd | G 63 4MATIC | €35,000 | blocked — registration tax unknown |
| 3rd | S 580 4MATIC | **€49,750** | costs unknown |

The biggest number on the screen is third. A gap excludes every cost and assumes
a sale that has not happened; a contribution is what survives them. The screen
says so, in those words, next to the figure.

**A concrete example of the comparison being careful.** An S 450 and an S 580 are
the same generation of car. Pooling them would give a median describing nothing
anyone can buy. The tool prices them separately: **€153,750** against
**€187,750** — thirty-four thousand euros apart.

---

## 4. Numbers that can be quoted

- **452 automated tests**, all passing.
- **23 cars** on file with **51 comparable listings** behind them, across G-Class,
  S-Class, 7 Series, X5, X7, GLE, GLC and GLS.
- **Zero** network requests ever made by the software.
- **Zero** ability to send a message — no dialler, no mail, no credentials in the
  code at all.
- Example card: car asking **€131,000**, comparable median **€153,750** from four
  vehicles, contribution after all costs **€9,650**.

---

## 5. What it is NOT

These are the important ones. Say them plainly if the email touches on them at
all; do not quietly imply the opposite.

- **Not connected to mobile.de, AutoScout24 or Sauto.** The mobile.de connector
  is built against their official partner API and is waiting on credentials. It
  has never connected to anything. Nothing was scraped.
- **Not running on real stock.** Every car, company, price and buyer on the
  screen is sample data, labelled as such on the page.
- **Not a tax calculator.** It records reviewed assumptions with a date and a
  source; it does not compute anyone's liability.
- **Not able to tell you a deal is profitable.** It shows a conditional figure
  and everything it depends on.
- **Not finished.** It is a working prototype built to show the approach.

---

## 6. The link

Max will include a link to the live application. Whoever drafts the email should
leave a clear placeholder — `[LINK]` — for him to paste it in.

What Alexej will see on it: the application, working, on sample stock, with a
label on the page saying so. It opens fine on a phone.

One honest caveat worth a half-sentence: it is hosted on a free tier that sleeps
when idle, so the first visit can take about twenty seconds to wake up.

---

## 7. Claims the email must not make

Every one of these is false and checkable.

- "It's connected to mobile.de" / "it pulls live listings"
- "It monitors the market"
- "It found these deals" / "it found you a buyer"
- "It shows you the profit on each car"
- "It handles the VAT"
- "It's ready to use" / "ready to roll out"
- Anything implying Max has their stock list, their customer data, or any
  non-public information about the business
- Any price, margin or buyer name presented as real

---

## 8. What the email should do

In order:

1. Say what it is in one sentence a busy person understands.
2. Give the link, with the sample-data caveat attached to it rather than buried.
3. Name one or two concrete things he can look at when he opens it — the
   exclusion reasons, and the Incomplete-versus-contribution contrast are the two
   that land.
4. Say plainly that nothing is connected and nothing was scraped, framed as a
   deliberate choice rather than a limitation.
5. Invite a short conversation. Do not ask for a decision.

**Do not** include a feature list, an architecture description, or anything about
tests, databases or programming languages. If he wants that, he will ask.

---

## 9. A starting draft

Use this as a reference for tone and length rather than copying it verbatim.

> **Subject:** The buyer-matching tool — working version
>
> Hi Alexej,
>
> Here is the prototype I mentioned: [LINK]
>
> It takes a list of cars, finds the ones priced below genuinely comparable
> ones, works out what is actually left after transport, VAT and registration
> tax, and matches each car to a buyer who has asked for that specification — then
> drafts the message for you to send.
>
> Two things worth clicking when you open it. On any car, open the excluded
> comparables: it lists every car it refused to compare against and why. And look
> at a card marked *Incomplete* — where a cost is unknown it says so and names
> the missing figure rather than guessing one. That restraint is the whole idea.
>
> It runs on representative sample stock, not real inventory, and it is labelled
> that way on the page. Nothing is connected to mobile.de or anywhere else — the
> connector is built and ready for official API access, but I did not scrape
> anything, deliberately.
>
> Worth fifteen minutes to talk through?
>
> Max

---

## 10. Tone

Plain and unhurried. No superlatives, no "revolutionise", no "leverage". The
product's argument is that it does not oversell, so an email that oversells it
contradicts the thing it is describing.

Short sentences. One idea per paragraph. It should read like a competent person
describing something they made, not like marketing.
