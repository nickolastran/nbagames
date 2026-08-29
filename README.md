# NBA Game Tracker

Log your own stat line against real NBA games. Sign in with Supabase, search the league's
game logs by keyword, pick the side you played, and add your box score.

`/` is a landing page — a three.js shot loop, the pitch, and a footer mark linking to the
repo. `#login` is the sign-in route; once you have a session you land in the tracker.
**Set `REPO` at the top of `web/src/Landing.tsx` to your GitHub URL** — it ships as a
placeholder.

FastAPI + React/TypeScript. Games live in Supabase (Postgres + row-level security); the API
is stateless — it computes every derived stat (accolades, records, splits, leaders,
milestones) so the client never re-implements them, and proxies the game-log search.

## Setup

1. **Supabase** — create a project, run `supabase/schema.sql` in the SQL editor. It creates
   the `games` table and an RLS policy so each account only ever sees its own rows.
   To skip the confirmation email, turn off *Confirm email* under Authentication → Providers.
2. **Keys** — `cp web/.env.example web/.env.local` and paste the project URL + anon key
   from Project Settings → API.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt
.venv/bin/uvicorn api.main:app --reload --port 8000   # API on :8000
cd web && npm install && npm run dev                  # UI on :5173, proxies /api to :8000
```

Production: `cd web && npm run build`, then `uvicorn api.main:app` serves `web/dist` at `/`.

## Searching game logs

| Query | Finds |
|---|---|
| `warriors 3/14` | every Warriors game on March 14, last five seasons |
| `lakers 3/15/26` | that one game |
| `12/25/25` | every game that night |
| `golden state 12/25/24` | city, nickname or abbreviation all match |

A date is required. Data comes from ESPN's public scoreboard — no API key, cached per day.
Picking a side prefills the date, opponent, venue and final score; you fill in your own line.

## Check

```bash
.venv/bin/python api/main.py selftest          # stats, milestones, query parsing
.venv/bin/python api/main.py selftest --live   # also hits ESPN
cd web && npm run build                        # typechecks, then bundles
```

## API

| | |
|---|---|
| `POST /api/summary` | `{handle, games[], acc?, team?}` → everything the page renders |
| `GET /api/search?q=` | keyword search over NBA game logs |
