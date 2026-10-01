# Putting the Opportunity Desk on a public link

Everything in the repository is ready for this. What remains is four manual steps
that need your GitHub account, so they are yours to run rather than mine.

**Read this first.** The hosted copy is safe to share because it *cannot hold real
data*, not because the app became secure. There is still no authentication and no
access control. `DESK_PUBLIC=1` makes that a guard rather than a promise: with it
set, the app refuses to start in live mode, with network access, or with runtime
AI enabled. Keep real stock, real buyers and real contact records on a local
machine.

---

## What is already done

| Item | State |
|---|---|
| Single entrypoint | `app.py` at the repository root |
| Pinned runtime dependencies | `requirements.txt` — runtime only, exact versions |
| Theme | `.streamlit/config.toml` — dark palette, CORS and XSRF protection on |
| First-run seeding | The app loads sample stock automatically into an empty database |
| Public-mode guard | `desk/services/deployment.py`, enforced before any screen renders |
| Scraping code | None. `grep -ri "beautifulsoup\|selenium" desk/` returns the disabled optional adapter only |

---

## Streamlit Community Cloud — the short route

1. **Create a GitHub repository** and push this project to it. A **private**
   repository is fine; Streamlit Community Cloud supports them.

   ```bash
   git init
   git add .
   git commit -m "Opportunity Desk"
   git branch -M main
   git remote add origin https://github.com/<you>/opportunity-desk.git
   git push -u origin main
   ```

2. **Go to https://share.streamlit.io** and sign in with GitHub. Choose
   **New app**, then pick the repository, the `main` branch, and `app.py` as the
   entrypoint.

3. **Set the secret.** Open **Advanced settings → Secrets** before deploying and
   paste:

   ```toml
   DESK_PUBLIC = "1"
   DESK_MODE = "demo"
   ```

   The first turns on the guard. The second is belt and braces — demo is already
   the default, and an unrecognised value resolves to demo rather than live.

4. **Deploy.** You get a link of the form
   `https://<something>.streamlit.app`. Open it on your phone to check it before
   you send it to anyone.

**Known behaviour worth mentioning if someone asks:** a free Community Cloud app
sleeps when nobody has used it for a while and takes roughly twenty seconds to
wake on the next visit. That is the platform, not the app. If you are sending the
link ahead of a meeting, open it yourself a minute beforehand so it is warm.

---

## Render.com — the alternative

Useful if you want it always-on, or if the repository cannot go to Streamlit
Cloud.

- **Service type:** Web Service
- **Build command:** `pip install -r requirements.txt`
- **Start command:**
  ```bash
  streamlit run app.py --server.port $PORT --server.address 0.0.0.0
  ```
- **Environment variables:** `DESK_PUBLIC=1` and `DESK_MODE=demo`

Render's free tier also sleeps when idle.

---

## Checking it before you share it

Once the link is live, confirm these four things yourself. They take a minute and
they are the ones somebody technical will look for.

1. **The sidebar says `Public demonstration · sample stock only`.** If it says
   *Local instance*, the secret did not take.
2. **The Overview screen names mobile.de as built and not connected.** If it ever
   says "connected", something has changed that should not have.
3. **The Opportunities screen loads with cards, not an empty state.** That
   confirms first-run seeding worked on the platform's filesystem.
4. **The top card shows `Incomplete` with a named missing fact.** That is the
   whole product in one screenshot.

---

## What to say when you send the link

> Works end to end on representative sample stock. The mobile.de connector is
> built and ready to switch on with official API access — I didn't scrape
> anything.

And if they ask which parts are real:

- **Real:** the comparability rules and their exclusion reasons, the exact-decimal
  money handling, the cost and contribution arithmetic, the matching rules, the
  freshness and staleness logic, the draft generation, the source register and its
  refusals.
- **Sample:** every car, company, contact, price and requirement on the screen.
- **Not connected:** every marketplace. No live request has been made from this
  project.

---

## A note on the database, if anyone technical asks

The hosted copy writes to SQLite on the platform's ephemeral filesystem. Anything
entered on the hosted link disappears when the instance restarts, and the sample
stock reseeds. For a demonstration that is the behaviour you want: every visitor
gets a clean, identical app. It is also one of several reasons the hosted copy is
not a system of record, and the Multi-user design note sets out what a real shared
version would need instead.
