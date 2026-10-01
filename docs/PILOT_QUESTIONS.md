# Questions to put to the company before a pilot

From manual section 13. These are the questions whose answers decide what the
next version should be; guessing at them is how a prototype becomes a product
nobody asked for.

Take this into the conversation. It is deliberately a list of questions rather
than a proposal: the prototype should adapt to the answers, not require the
business to replace what it already has.

## How to open it

The honest framing, which is also the strongest one:

> I have built a working prototype that connects vehicle observations to
> documented buyer requirements and shows what is uncertain. It runs on synthetic
> data. Before it touches anything real I need to understand what data exists,
> who owns it, and what is already in place — because the useful version of this
> complements your systems rather than duplicating them.

What **not** to claim, because none of it is true and all of it is checkable:
that it has found a real buyer, that it has identified a profitable trade, that
it covers the European market, or that it works without a data source.

## 1. Data: what exists, and who owns it

- Is there a **stock export** — CSV, XML, JSON, anything — and who inside the
  business owns the decision to share it?
- Which fields may be retained, and for how long?
- May the prices in it be shown to a third party, or only used internally?
- Is an export for one deal reusable for others, or does each one need its own
  permission?
- Who owns customer and prospect records: the company, the individual
  salesperson, or a platform the company licenses?
- Are there existing data-licence terms — Kompass, a marketplace account, a CRM
  vendor — that restrict what can be done with records already held?

*Why it matters:* without an authorised supply feed the tool analyses imported
observations, and it says so on every screen. The single highest-value change
available is a real stock export, not another connector.

## 2. Markets and models

- Which markets actually matter: which countries do cars come from, and which do
  they go to?
- Which model families are worth the work? The prototype starts with G-Class,
  S-Class, 7 Series and X5/X7, and anything else goes to review.
- How often does a cross-border deal actually happen, versus a domestic one?
- Which destination countries need registration tax and recoverability modelled?
  Each becomes a reviewed cost template.

## 3. Contact routes and permission

- Who decides whether a business may be contacted, and on which channel?
- What is the current practice for recording that decision, if any?
- Is there an existing suppression or do-not-contact list, and where does it
  live?
- Which markets are in scope? Czech practice in particular treats published
  electronic contact details differently from a general permission, and the
  approach needs to be the company's, not an assumption in software.

*Why it matters:* the application records an accountable human decision. It does
not determine what any jurisdiction permits, and it should not be presented as
though it does.

## 4. Existing systems

- What CRM is in use, and what does it already do well?
- Where do buyer requirements live today — a CRM field, a spreadsheet, a
  salesperson's memory?
- What does the stock process look like from advert to sale?
- Which licensed data does the company already pay for? Duplicating it would be
  waste; feeding this tool from it might be cheap.
- What would this need to hand back to be useful rather than another place to
  type things?

## 5. People and process

- Who would actually use it, and how many of them? (If more than one, read
  `MULTI_USER_DESIGN.md` first: it is a different application.)
- What does a good day look like for them now, and where does the time go?
- Which of these is the real bottleneck: finding cars, finding buyers, matching
  the two, or working out whether a deal is worth doing?
- Who signs off a purchase, and what do they need to see?

## 6. What a small pilot would look like

- One market pair, one or two model families, one or two users — what would that
  be?
- What would count as success after a month? Suggested measures, from manual
  section 13: verified facts per imported record, duplicate rate, time to prepare
  a useful call, qualified requirements captured, meetings held, offers, and
  eventually delivered contribution. **Also track false alerts and time wasted**,
  because a tool that reduces bad matches is valuable even if it does not
  increase message volume.
- What would make the company stop the pilot? Worth knowing in advance.

## 7. The specific unlocks, in order

If the conversation goes well, these are the things to ask for, in the order that
adds most value:

1. **An authorised stock export.** Turns replayed snapshots into real supply
   monitoring. Nothing else comes close.
2. **Five to ten real buyer requirements**, with permission to record them. The
   matching engine is only as useful as the demand side, and that side is
   currently entirely synthetic.
3. **Registration tax and recoverability for one destination country**, with a
   source and a date. Becomes a reviewed cost template and makes scenarios
   complete rather than incomplete.
4. **A decision on contact routes** for one market, so the outreach gates have
   something real to enforce.
5. **Approval for one bounded live read**, if discovery is genuinely wanted. The
   machinery is built and refuses to run without a recorded review; what is
   missing is the authorisation.

## What to say if asked what it cannot do

Answer plainly; the boundaries are the credible part:

- It has no live market coverage and does not pretend to.
- It cannot send anything. There is no sending capability in the codebase.
- It does not compute anyone's tax liability. It records reviewed assumptions.
- It does not tell you a deal is profitable. It shows a conditional contribution
  and what it depends on, and returns *Incomplete* when something is unknown.
- It is one user on one machine, and making it more than that is a rebuild.
