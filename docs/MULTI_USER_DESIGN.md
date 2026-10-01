# Multi-user access: a design, not an implementation

**Status: deliberately not built.**

The prototype is single-user and bound to localhost. This document exists because
"add logins" is the kind of change that looks small and is not, and because the
next person to pick this up should be able to see what was considered rather than
rediscover it.

The honest summary: **multi-user is a different application.** Not a bigger one,
a different one. Almost every invariant this prototype enforces is currently
enforced against one trusted person on one machine, and several of them stop
meaning anything the moment there are two people and a network.

## Why it is not a bolt-on

### 1. The accountability model is currently a text field

Everywhere this application records a decision — a contact policy, an accepted
correction, an analyst adjustment, a source review, a cost template — it stores
`reviewer` or `decided_by` as **text the user typed about themselves**.

That is defensible with one user on their own machine. It is worthless with two:
a suppression that says `recorded_by: "Max"` proves nothing once anyone can type
"Max". And these are exactly the fields the application relies on to say a thing
was permitted.

So the first real change is not a login screen. It is replacing every
self-asserted actor with an authenticated identity, and migrating the existing
rows to say honestly that their actor was self-asserted under the single-user
model rather than silently implying otherwise.

Affected: `contact_policies.reviewer`, `suppressions.recorded_by`,
`interactions.recorded_by`, `fact_reviews.decided_by`,
`comparable_adjustments.created_by`, `source_reviews.reviewer`,
`cost_templates.reviewer`, `change_log.actor`, `buyer_briefs.confirmed_by`.

`buyer_briefs.confirmed_by` is the interesting one: it names the **buyer** who
confirmed, not the user who typed it. Both need recording, and conflating them
would be a real loss of meaning.

### 2. Permission to contact is not the same as permission to use the app

The most consequential state in the system is whether an account may be contacted
on a channel. Today one person sets that and one person relies on it.

With several users, at least three separate questions appear, none of which the
current schema answers:

- **who may record** a contact permission (not everyone should);
- **who may act** on one (possibly a wider group);
- **who may override or retire** one (narrower still, and arguably nobody, since
  an objection should be close to irreversible).

A role model that only distinguishes read from write gets this wrong. The
distinction that matters is between recording an accountable decision and
consuming it.

### 3. Concurrency is currently avoided rather than handled

SQLite with short-lived connections and one writer is entirely adequate for one
person. It stops being adequate when two people work the same account:

- two users resolving the **same contradiction** differently;
- one accepting a correction while another generates a draft from the
  pre-correction facts;
- one suppressing an account while another exports it.

The draft fingerprint already guards the third case by accident, which is
encouraging, but the others are unguarded. Either the store moves to something
with real concurrent write semantics (PostgreSQL), or every such decision gets an
optimistic-concurrency token and a genuine conflict path. Not a retry loop: a
screen that says who else changed this and what they said.

### 4. Demo and live separation assumes one person's intent

`DESK_MODE` is process-wide, and the demo and live databases are separate files
stamped with their mode. With several users this has to become per-session, and
the guarantee changes from "this process cannot touch live data" to "this
*request* cannot" — a much weaker property that needs enforcing on every path
rather than once at startup.

### 5. Source approval becomes an organisational act

`source_review` currently records that a named person read some terms. Shared,
it becomes a statement binding on everyone who then fetches. That probably wants
a second reviewer for anything network-facing, and certainly wants the
distinction between *proposing* and *approving* a source.

## What a first real version would need

In dependency order. Steps 1 and 2 are prerequisites for everything else.

| # | Change | Why it comes here |
|---|---|---|
| 1 | Authenticated identity, and an `actors` table | Every other guarantee depends on knowing who acted. |
| 2 | Replace self-asserted actor fields with actor references, migrating existing rows as `self_asserted` | Without this, existing records silently gain authority they never had. |
| 3 | Move to a store with real concurrent writes | Needed before two people can safely hold the same record open. |
| 4 | Capability model: record-decision, act-on-decision, administer-sources | Read and write is the wrong axis. |
| 5 | Per-session mode, enforced per request | The process-wide flag stops being a guarantee. |
| 6 | Conflict surfaces on contradictions, policies and suppressions | Concurrency made visible rather than last-write-wins. |
| 7 | Transport security, session management, and an access log distinct from `change_log` | `change_log` records what changed; an access log records who looked. |
| 8 | A data-sharing review before anyone outside the team sees a screen | Contact data and buyer requirements are the sensitive parts. |

## What must not change

Whatever else multi-user brings, these should survive intact, because they are
the reason the tool is worth using:

- unknown stays unknown, and unknown blocks a pass;
- a source observation is never overwritten by review;
- absence and failure are never a sale;
- a discovered contact detail never becomes permission;
- a suppression outranks every score;
- only three things move a figure, and each needs a person and a reason.

A multi-user version that keeps the logins and loses these would be worse than
the single-user one.

## A smaller step, if the pilot needs one first

If the actual requirement is "two people at the same dealership want to see the
same pipeline", there is a cheaper honest option than full multi-user:

**one shared machine, one database, named sessions.** Add an actor chosen at
startup from a short configured list, record it on every decision, and keep
everything else as it is. That gets real attribution — the single biggest gap —
without pretending to offer access control the prototype cannot enforce.

It is not multi-user. It should not be described as multi-user. But it would make
the audit trail mean something, and it is perhaps a fortnight rather than a
quarter.
