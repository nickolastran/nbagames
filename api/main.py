"""NBA game tracker API.

Stateless: games live in Supabase (Postgres + RLS), the client posts them here and
this module computes every derived stat, so the client never re-implements them.
Also proxies a keyword search over ESPN's public NBA scoreboard.
"""
import json, re, sys, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date as Date
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DIST = Path(__file__).parent.parent / "web" / "dist"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?limit=100&dates="
SEASONS_BACK = 5  # how far a bare month/day like "3/14" reaches

TEAMS = [
    ("ATL", "Hawks", "#E03A3E"), ("BOS", "Celtics", "#00A94F"), ("BKN", "Nets", "#C9C9C9"),
    ("CHA", "Hornets", "#00A6A6"), ("CHI", "Bulls", "#CE1141"), ("CLE", "Cavaliers", "#A8203F"),
    ("DAL", "Mavericks", "#3C82C4"), ("DEN", "Nuggets", "#FEC524"), ("DET", "Pistons", "#E4405A"),
    ("GSW", "Warriors", "#FFC72C"), ("HOU", "Rockets", "#E0403E"), ("IND", "Pacers", "#FDBB30"),
    ("LAC", "Clippers", "#E4405A"), ("LAL", "Lakers", "#8B5CC7"), ("MEM", "Grizzlies", "#7FA0D6"),
    ("MIA", "Heat", "#D4426B"), ("MIL", "Bucks", "#4E9E6B"), ("MIN", "Timberwolves", "#78BE20"),
    ("NOP", "Pelicans", "#B4975A"), ("NYK", "Knicks", "#F58426"), ("OKC", "Thunder", "#3FA9E0"),
    ("ORL", "Magic", "#3D8FD4"), ("PHI", "76ers", "#4B92DB"), ("PHX", "Suns", "#E56020"),
    ("POR", "Trail Blazers", "#E85C5C"), ("SAC", "Kings", "#9B6BD1"), ("SAS", "Spurs", "#C4CED4"),
    ("TOR", "Raptors", "#E0403E"), ("UTA", "Jazz", "#6BA84F"), ("WAS", "Wizards", "#E31837"),
]
TEAM_NAME = {a: n for a, n, _ in TEAMS}
BY_NICK = {n.lower(): a for a, n, _ in TEAMS}  # ESPN's team.name is the nickname

Game = dict[str, Any]
Test = Callable[[Game, int], bool]

ACC: list[tuple[str, str, Test]] = [
    ("td",  "Triple-Double", lambda g, mx: sum(g[k] >= 10 for k in ("pts", "reb", "ast", "stl", "blk")) >= 3),
    ("p40", "40+ Points",    lambda g, mx: g["pts"] >= 40),
    ("p50", "50+ Points",    lambda g, mx: g["pts"] >= 50),
    ("r20", "20+ Rebounds",  lambda g, mx: g["reb"] >= 20),
    ("a10", "10+ Assists",   lambda g, mx: g["ast"] >= 10),
    ("b5",  "5+ Blocks",     lambda g, mx: g["blk"] >= 5),
    ("p3",  "Perfect 3PT",   lambda g, mx: g["fg3a"] >= 2 and g["fg3m"] == g["fg3a"]),
    ("nto", "No Turnovers",  lambda g, mx: g["tov"] == 0 and g["min"] >= 20),
    ("ch",  "Career High",   lambda g, mx: mx > 0 and g["pts"] == mx),
]
ACC_LABEL = {k: label for k, label, _ in ACC}

STATS = [("pts", "Highest Points Scored", "Points"), ("ast", "Most Assists", "Assists"),
         ("reb", "Most Rebounds", "Rebounds"), ("blk", "Most Blocks", "Blocks"),
         ("stl", "Most Steals", "Steals"), ("fg3m", "Most 3-Pointers Made", "3-Pointers")]


class LoggedGame(BaseModel):
    """One row out of the Supabase `games` table."""
    id: int = 0
    date: str
    opp: str
    home: bool = True
    team_score: int = Field(0, ge=0, le=300)
    opp_score: int = Field(0, ge=0, le=300)
    min: int = Field(0, ge=0, le=70)
    pts: int = Field(0, ge=0, le=150)
    reb: int = Field(0, ge=0, le=60)
    ast: int = Field(0, ge=0, le=50)
    stl: int = Field(0, ge=0, le=20)
    blk: int = Field(0, ge=0, le=20)
    fg3m: int = Field(0, ge=0, le=30)
    fg3a: int = Field(0, ge=0, le=40)
    tov: int = Field(0, ge=0, le=20)


class SummaryIn(BaseModel):
    handle: str = "Handy"
    games: list[LoggedGame] = []
    acc: str | None = None
    team: str | None = None


# ---------- derived stats ----------

def accolades_of(g: Game, mx: int) -> list[str]:
    return [k for k, _, test in ACC if test(g, mx)]


def won(g: Game) -> bool:
    return g["team_score"] > g["opp_score"]


def record(games: list[Game]) -> dict[str, Any]:
    w = sum(won(g) for g in games)
    return {"w": w, "l": len(games) - w, "txt": f"{w}-{len(games) - w}"}


def avg(games: list[Game], k: str) -> float:
    return round(sum(g[k] for g in games) / len(games), 1) if games else 0.0


def ref(g: Game) -> dict[str, Any]:
    """Minimal game context shown next to a record or leaderboard entry."""
    return {"date": g["date"], "opp": g["opp"], "opp_name": TEAM_NAME[g["opp"]], "home": g["home"]}


def milestones(games: list[Game], mx: int) -> list[dict[str, Any]]:
    asc = sorted(games, key=lambda g: g["date"])
    out = []
    for title, key in [("First triple-double", "td"), ("First 40-point game", "p40"),
                       ("First 50-point game", "p50"), ("First 20-rebound game", "r20"),
                       ("First 10-assist game", "a10"), ("First perfect 3PT night", "p3")]:
        test = next(t for k, _, t in ACC if k == key)
        hit = next((g for g in asc if test(g, mx)), None)
        out.append({"title": title, "done": bool(hit), "mark": hit["date"] if hit else None,
                    "detail": (f"{'vs' if hit['home'] else '@'} {TEAM_NAME[hit['opp']]} · "
                               f"{hit['pts']} PTS / {hit['reb']} REB / {hit['ast']} AST")
                    if hit else "Not yet reached"})

    for n in (25, 50, 100):
        done = len(asc) >= n
        out.append({"title": f"{n} games logged", "done": done,
                    "mark": asc[n - 1]["date"] if done else f"{n - len(asc)} to go",
                    "detail": f"{record(asc[:n])['txt']} through {n} games" if done
                    else f"{len(asc)} logged so far"})

    run = 0
    for i, g in enumerate(asc):
        before, run = run, run + g["pts"]
        for m in (500, 1000, 2500, 5000):
            if before < m <= run:
                out.append({"title": f"{m:,} career points", "done": True, "mark": g["date"],
                            "detail": f"Crossed {'vs' if g['home'] else '@'} {TEAM_NAME[g['opp']]} in game {i + 1}"})
    nxt = next((m for m in (500, 1000, 2500, 5000, 10000) if run < m), None)
    if nxt:
        out.append({"title": f"{nxt:,} career points", "done": False, "mark": f"{nxt - run} to go",
                    "detail": f"Currently at {run:,}"})
    return out


def summary(games: list[Game], handle: str = "Handy",
            acc: str | None = None, team: str | None = None) -> dict[str, Any]:
    games = sorted(games, key=lambda g: g["date"], reverse=True)
    mx = max((g["pts"] for g in games), default=0)
    tagged = [dict(g, accolades=accolades_of(g, mx)) for g in games]

    shown = [g for g in tagged
             if (not acc or acc in g["accolades"]) and (not team or g["opp"] == team)]

    return {
        "handle": handle,
        "totals": {"games": len(games), "record": record(games)["txt"],
                   "pts": sum(g["pts"] for g in games), "reb": sum(g["reb"] for g in games),
                   "ast": sum(g["ast"] for g in games), "ppg": avg(games, "pts")},
        "accolades": [{"key": k, "label": label,
                       "count": sum(1 for g in games if test(g, mx))} for k, label, test in ACC],
        "records": [{"key": k, "label": label,
                     "value": max(g[k] for g in games) if games else 0,
                     "game": ref(max(games, key=lambda g: g[k])) if games else None}
                    for k, label, _ in STATS],
        "splits": [{"label": label,
                    "record": record(l)["txt"], "games": len(l), "pts": sum(g["pts"] for g in l),
                    "ppg": avg(l, "pts"), "rpg": avg(l, "reb"), "apg": avg(l, "ast")}
                   for label, l in (("Home", [g for g in games if g["home"]]),
                                    ("Away", [g for g in games if not g["home"]]))],
        "leaders": [{"key": k, "label": short,
                     "top": [{"value": g[k], **ref(g)}
                             for g in sorted(games, key=lambda g: (-g[k], g["date"]))[:5]]}
                    for k, _, short in STATS if k != "stl"],
        "teams": [{"abbr": a, "name": n, "color": col,
                   **record([g for g in games if g["opp"] == a]),
                   "played": sum(1 for g in games if g["opp"] == a)} for a, n, col in TEAMS],
        "milestones": milestones(games, mx),
        "games": shown,
    }


# ---------- keyword game-log search ----------

DATE_RE = re.compile(r"^(?:(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?)$")


def parse_query(q: str, today: Date | None = None) -> tuple[list[str], str]:
    """"warriors 3/15/26" -> one date; "warriors 3/14" -> that day across recent seasons.

    Returns (dates newest-first as YYYY-MM-DD, leftover team keywords).
    """
    today = today or Date.today()
    dates: list[Date] = []
    words: list[str] = []
    for tok in q.replace(",", " ").split():
        m = DATE_RE.match(tok)
        if not m:
            words.append(tok)
            continue
        iso_y, iso_m, iso_d, mm, dd, yy = m.groups()
        if iso_y:
            y, mon, day = int(iso_y), int(iso_m), int(iso_d)
        elif yy:
            y, mon, day = int(yy), int(mm), int(dd)
            y += 2000 if y < 100 else 0
        else:  # bare month/day: the last SEASONS_BACK occurrences, not-future
            mon, day, y = int(mm), int(dd), None
        try:
            if y is not None:
                dates.append(Date(y, mon, day))
            else:
                dates += [d for yr in range(today.year, today.year - SEASONS_BACK, -1)
                          if (d := safe_date(yr, mon, day)) and d <= today]
        except ValueError:
            raise HTTPException(400, f"{tok} isn’t a real date")
    if not dates:
        raise HTTPException(400, "Include a date — try “warriors 3/14” or “3/15/26”.")
    return [d.isoformat() for d in dates], " ".join(words).strip().lower()


def safe_date(y: int, m: int, d: int) -> Date | None:
    try:
        return Date(y, m, d)
    except ValueError:
        return None  # Feb 29 in a non-leap year


@lru_cache(maxsize=256)
def scoreboard(iso: str) -> tuple[dict[str, Any], ...]:
    """One day's NBA slate, normalised. Cached — a past date never changes."""
    # no custom User-Agent: ESPN's edge 403s anything but urllib's default
    try:
        with urllib.request.urlopen(SCOREBOARD + iso.replace("-", ""), timeout=12) as r:
            data = json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise HTTPException(502, f"ESPN scoreboard unavailable: {e}")

    out = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        sides = {}
        for c in comp["competitors"]:
            abbr = BY_NICK.get(c["team"]["name"].lower())
            if not abbr:
                break  # All-Star / international side we can't map to a franchise
            sides[c["homeAway"]] = {"abbr": abbr, "name": TEAM_NAME[abbr],
                                    "score": int(c.get("score") or 0),
                                    "search": f"{c['team']['displayName']} {c['team']['abbreviation']} {abbr}".lower()}
        if len(sides) != 2:
            continue
        h, a = sides["home"], sides["away"]
        out.append({
            "date": iso, "label": f"{a['abbr']} @ {h['abbr']}",
            "final": bool(comp.get("status", {}).get("type", {}).get("completed")),
            "search": f"{h['search']} {a['search']}",
            # one entry per side: exactly the payload the add-game form needs
            "sides": [{"abbr": t["abbr"], "name": t["name"], "home": t is h,
                       "team_score": t["score"], "opp": o["abbr"], "opp_name": o["name"],
                       "opp_score": o["score"]} for t, o in ((h, a), (a, h))],
        })
    return tuple(out)


def search_games(q: str, today: Date | None = None) -> list[dict[str, Any]]:
    dates, team = parse_query(q, today)
    with ThreadPoolExecutor(max_workers=SEASONS_BACK) as pool:
        days = pool.map(scoreboard, dates)
    return [g for day in days for g in day if not team or team in g["search"]]


# ---------- app ----------

app = FastAPI(title="NBA Game Tracker")


@app.post("/api/summary")
def post_summary(body: SummaryIn):
    if body.acc and body.acc not in ACC_LABEL:
        raise HTTPException(400, "unknown accolade")
    if body.team and body.team not in TEAM_NAME:
        raise HTTPException(400, "unknown team")
    games = [g.model_dump() for g in body.games]
    if bad := next((g["opp"] for g in games if g["opp"] not in TEAM_NAME), None):
        raise HTTPException(400, f"unknown team {bad}")
    return summary(games, body.handle, body.acc, body.team)


@app.get("/api/search")
def get_search(q: str):
    if not (q := q.strip()):
        raise HTTPException(400, "empty search")
    return {"games": search_games(q)}


if DIST.is_dir():  # serve the built frontend in production
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(DIST / "index.html")


def selfcheck(live: bool = False) -> None:
    g = lambda **kw: {**dict(pts=0, reb=0, ast=0, stl=0, blk=0, fg3m=0, fg3a=0, tov=0, min=36,
                             team_score=100, opp_score=99, home=True, opp="BOS", date="2026-01-01"), **kw}
    assert "td" in accolades_of(g(pts=30, reb=10, ast=11), 30)
    assert "td" not in accolades_of(g(pts=30, reb=9, ast=11), 30)
    assert accolades_of(g(pts=41), 99)[:1] == ["p40"]
    assert "p50" not in accolades_of(g(pts=41), 99)
    assert "p3" in accolades_of(g(fg3m=4, fg3a=4), 0)
    assert "p3" not in accolades_of(g(fg3m=0, fg3a=0), 0)
    assert "nto" in accolades_of(g(tov=0), 0) and "nto" not in accolades_of(g(tov=1), 0)
    assert "ch" in accolades_of(g(pts=50), 50) and "ch" not in accolades_of(g(pts=20), 50)
    assert record([g(), g(team_score=90)])["txt"] == "1-1"

    # empty tracker renders without blowing up
    s = summary([])
    assert s["totals"] == {"games": 0, "record": "0-0", "pts": 0, "reb": 0, "ast": 0, "ppg": 0.0}
    assert all(r["game"] is None for r in s["records"]) and not any(m["done"] for m in s["milestones"])
    assert summary([g(pts=52, id=1)])["records"][0]["value"] == 52

    # query parsing
    t = Date(2026, 8, 28)
    assert parse_query("warriors 3/15/26", t) == (["2026-03-15"], "warriors")
    assert parse_query("3/15/2026", t) == (["2026-03-15"], "")
    assert parse_query("golden state 2026-03-15", t) == (["2026-03-15"], "golden state")
    d, team = parse_query("warriors 3/14", t)          # bare month/day fans out over seasons
    assert d == ["2026-03-14", "2025-03-14", "2024-03-14", "2023-03-14", "2022-03-14"] and team == "warriors"
    assert parse_query("12/25", t)[0][0] == "2025-12-25"  # future dates dropped
    assert parse_query("2/29", t)[0] == ["2024-02-29"]  # leap years only
    for bad in ("warriors", "", "13/45/26"):
        try:
            parse_query(bad, t)
            raise AssertionError(f"{bad!r} should have been rejected")
        except HTTPException:
            pass

    if live:  # hits ESPN
        hits = search_games("celtics 3/14/25")
        assert len(hits) == 1 and hits[0]["label"] == "BOS @ MIA", hits
        assert [s["team_score"] for s in hits[0]["sides"]] == [91, 103]
        assert len(search_games("3/14/25")) == 10
        assert not search_games("warriors 3/14/25")
    print("selfcheck ok" + (" (incl. live search)" if live else ""))


if __name__ == "__main__":
    if "selftest" in sys.argv:
        selfcheck(live="--live" in sys.argv)
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8000)
