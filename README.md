# NBA Game Tracker

A personal scoreboard for every NBA game you've been to. Search for a game, hit **+**, and its
real box score comes with it — final score, overtimes, arena, every player's line. Your stats
build from those: team records, single-game records, player leader boards across your games,
arenas, milestones, and badges for rare stuff you witnessed (each with roughly how rare it is).

No account: games are saved in your browser (localStorage). Use **Export** / **Import** in the
tracker to back them up or move devices.

`/` is a landing page — a three.js shot loop, the pitch, and a footer mark linking to the repo.
`#app` is the tracker. **Set `REPO` at the top of `web/src/Landing.tsx` to your GitHub URL** —
it ships as a placeholder.

FastAPI + React/TypeScript. The API is stateless — it computes every derived stat from the
games the browser posts, and proxies ESPN's public scoreboard (search) and box scores (adding).
No database, no keys.

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

A date is required. Data comes from ESPN's public API — no key, cached per day / per game.

## Check

```bash
.venv/bin/python api/main.py selftest          # stats, milestones, query parsing
.venv/bin/python api/main.py selftest --live   # also hits ESPN
cd web && npm run build                        # typechecks, then bundles
```

## API

| | |
|---|---|
| `GET /api/search?q=` | keyword search over NBA scoreboards |
| `GET /api/game/{id}` | one finished game's box score, in the shape the browser stores |
| `POST /api/summary` | `{games[], badge?, team?}` → everything the page renders |
