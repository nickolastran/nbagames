"""NBA games-attended tracker API.

Stateless: the browser keeps the games you've been to (each one a box-score snapshot taken
when you added it) and posts them here; this module computes every derived stat, so the
client never re-implements them. Also proxies ESPN's public NBA scoreboard (search) and
box scores (adding a game).
"""
import json, re, sys, urllib.error, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date as Date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DIST = Path(__file__).parent.parent / "web" / "dist"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
SEASONS_BACK = 5  # how far a bare month/day like "3/14" reaches
EASTERN = ZoneInfo("America/New_York")  # ESPN dates are UTC; the NBA's calendar is Eastern

TEAMS = [
    ("ATL", "Hawks", "#E03A3E"), ("BOS", "Celtics", "#00A94F"), ("BKN", "Nets", "#777777"),
    ("CHA", "Hornets", "#00A6A6"), ("CHI", "Bulls", "#CE1141"), ("CLE", "Cavaliers", "#A8203F"),
    ("DAL", "Mavericks", "#3C82C4"), ("DEN", "Nuggets", "#FEC524"), ("DET", "Pistons", "#E4405A"),
    ("GSW", "Warriors", "#FFC72C"), ("HOU", "Rockets", "#E0403E"), ("IND", "Pacers", "#FDBB30"),
    ("LAC", "Clippers", "#E4405A"), ("LAL", "Lakers", "#8B5CC7"), ("MEM", "Grizzlies", "#7FA0D6"),
    ("MIA", "Heat", "#D4426B"), ("MIL", "Bucks", "#4E9E6B"), ("MIN", "Timberwolves", "#78BE20"),
    ("NOP", "Pelicans", "#B4975A"), ("NYK", "Knicks", "#F58426"), ("OKC", "Thunder", "#3FA9E0"),
    ("ORL", "Magic", "#3D8FD4"), ("PHI", "76ers", "#4B92DB"), ("PHX", "Suns", "#E56020"),
    ("POR", "Trail Blazers", "#E85C5C"), ("SAC", "Kings", "#9B6BD1"), ("SAS", "Spurs", "#9AA5AD"),
    ("TOR", "Raptors", "#E0403E"), ("UTA", "Jazz", "#6BA84F"), ("WAS", "Wizards", "#E31837"),
]
TEAM_NAME = {a: n for a, n, _ in TEAMS}
TEAM_COLOR = {a: c for a, _, c in TEAMS}
BY_NICK = {n.lower(): a for a, n, _ in TEAMS}  # ESPN's team.name is the nickname
STAT_KEYS = ("pts", "reb", "ast", "stl", "blk", "fg3m")


class Player(BaseModel):
    id: str
    name: str
    team: str
    min: int = 0
    pts: int = 0
    reb: int = 0
    ast: int = 0
    stl: int = 0
    blk: int = 0
    fg3m: int = 0


class Side(BaseModel):
    abbr: str
    name: str = ""
    score: int


class SeenGame(BaseModel):
    """One game you were at, as the browser stores it (the output of /api/game/{id})."""
    id: str
    date: str
    playoff: bool = False
    periods: int = 4
    venue: str = ""
    city: str = ""
    home: Side
    away: Side
    players: list[Player] = []


class SummaryIn(BaseModel):
    games: list[SeenGame] = []
    badge: str | None = None
    team: str | None = None


Game = dict[str, Any]

# ---------- badges ----------

def top(g: Game, k: str) -> int:
    return max((p[k] for p in g["players"]), default=0)


def margin(g: Game) -> int:
    return abs(g["home"]["score"] - g["away"]["score"])


def points(g: Game) -> int:
    return g["home"]["score"] + g["away"]["score"]


def triple_double(p: dict) -> bool:
    return sum(p[k] >= 10 for k in ("pts", "reb", "ast", "stl", "blk")) >= 3


# (key, label, ~1 in N games league-wide, test)
# ponytail: rarities are hand-estimated from recent seasons, recompute from real data if they need to be exact
BADGES: list[tuple[str, str, int, Callable[[Game], bool]]] = [
    ("td",   "Triple-Double",    9,   lambda g: any(triple_double(p) for p in g["players"])),
    ("p40",  "40-Point Game",    10,  lambda g: top(g, "pts") >= 40),
    ("p50",  "50-Point Game",    100, lambda g: top(g, "pts") >= 50),
    ("2020", "20-20 Game",       80,  lambda g: any(p["pts"] >= 20 and p["reb"] >= 20 for p in g["players"])),
    ("a20",  "20-Assist Game",   400, lambda g: top(g, "ast") >= 20),
    ("t10",  "10 Threes",        150, lambda g: top(g, "fg3m") >= 10),
    ("b7",   "Block Party",      120, lambda g: top(g, "blk") >= 7),
    ("ot",   "Overtime",         16,  lambda g: g["periods"] > 4),
    ("2ot",  "Double OT",        150, lambda g: g["periods"] > 5),
    ("one",  "One-Point Game",   30,  lambda g: margin(g) == 1),
    ("blow", "40-Point Blowout", 100, lambda g: margin(g) >= 40),
    ("t150", "150-Point Team",   80,  lambda g: max(g["home"]["score"], g["away"]["score"]) >= 150),
    ("xmas", "Christmas Game",   250, lambda g: g["date"][5:] == "12-25"),
    ("po",   "Playoff Game",     15,  lambda g: g["playoff"]),
]
BADGE_TEST = {k: t for k, _, _, t in BADGES}


def badges_of(g: Game) -> list[str]:
    return [k for k, _, _, t in BADGES if t(g)]


# ---------- derived stats ----------

def winner(g: Game) -> str:
    return g["home"]["abbr"] if g["home"]["score"] > g["away"]["score"] else g["away"]["abbr"]


def matchup(g: Game) -> str:
    a, h = g["away"], g["home"]
    return f"{TEAM_NAME[a['abbr']]} {a['score']} @ {TEAM_NAME[h['abbr']]} {h['score']}"


def length(periods: int) -> str:
    return "Regulation" if periods <= 4 else "OT" if periods == 5 else f"{periods - 4}OT"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def records(games: list[Game]) -> list[dict[str, Any]]:
    if not games:
        return []
    out = []
    g = max(games, key=lambda g: g["periods"])
    out.append({"label": "Longest Game", "value": length(g["periods"]), "sub": matchup(g)})
    g = max(games, key=points)
    out.append({"label": "Highest Scoring Game", "value": f"{points(g)} pts", "sub": matchup(g)})
    g = min(games, key=points)
    out.append({"label": "Lowest Scoring Game", "value": f"{points(g)} pts", "sub": matchup(g)})
    g = max(games, key=margin)
    out.append({"label": "Biggest Blowout", "value": f"+{margin(g)}", "sub": matchup(g)})
    lines = [(p, g) for g in games for p in g["players"]]
    for k, label in (("pts", "Most Points"), ("reb", "Most Rebounds"), ("ast", "Most Assists"),
                     ("fg3m", "Most Threes"), ("blk", "Most Blocks")):
        if lines:
            p, g = max(lines, key=lambda pg: pg[0][k])
            out.append({"label": label, "value": str(p[k]), "sub": f"{p['name']} · {g['date']}"})
    return out


def leaders(games: list[Game], n: int = 25) -> list[dict[str, Any]]:
    tot: dict[str, Counter] = defaultdict(Counter)
    who: dict[str, dict] = {}
    for g in sorted(games, key=lambda g: g["date"]):
        for p in g["players"]:
            tot[p["id"]].update({k: p[k] for k in STAT_KEYS} | {"games": 1})
            who[p["id"]] = {"name": p["name"], "team": p["team"]}  # latest team wins
    boards = [("games", "Most Seen", "GP"), ("pts", "Most Points", "PTS"), ("reb", "Most Rebounds", "REB"),
              ("ast", "Most Assists", "AST"), ("fg3m", "Most Threes", "3PM"), ("blk", "Most Blocks", "BLK"),
              ("stl", "Most Steals", "STL")]
    return [{"key": k, "label": label, "col": col,
             "top": [{**who[pid], "value": c[k]}
                     for pid, c in sorted(tot.items(), key=lambda kv: (-kv[1][k], who[kv[0]]["name"]))[:n]
                     if c[k]]}
            for k, label, col in boards]


def milestones(games: list[Game]) -> list[dict[str, Any]]:
    asc = sorted(games, key=lambda g: g["date"])
    out = []
    for n in (1, 10, 25, 50, 100, 250):
        if len(asc) >= n:
            out.append({"title": f"{ordinal(n)} game", "done": True, "mark": asc[n - 1]["date"], "detail": matchup(asc[n - 1])})
        else:
            out.append({"title": f"{ordinal(n)} game", "done": False, "mark": f"{n - len(asc)} to go", "detail": ""})
            break
    for title, key in (("First overtime", "ot"), ("First playoff game", "po"), ("First 50-point game", "p50")):
        if hit := next((g for g in asc if BADGE_TEST[key](g)), None):
            out.append({"title": title, "done": True, "mark": hit["date"], "detail": matchup(hit)})
    if hit := next(((p, g) for g in asc for p in g["players"] if triple_double(p)), None):
        p, g = hit
        out.append({"title": "First triple-double", "done": True, "mark": g["date"],
                    "detail": f"{p['name']} · {p['pts']}/{p['reb']}/{p['ast']}"})
    return out


def summary(games: list[Game], badge: str | None = None, team: str | None = None) -> dict[str, Any]:
    games = sorted({g["id"]: g for g in games}.values(), key=lambda g: g["date"], reverse=True)  # one entry per game
    teams_seen = {g[s]["abbr"] for g in games for s in ("home", "away")}
    arenas = Counter((g["venue"], g["city"]) for g in games if g["venue"])
    tagged = [dict(g, badges=badges_of(g)) for g in games]

    wl: dict[str, Counter] = defaultdict(Counter)
    for g in games:
        w = winner(g)
        for s in ("home", "away"):
            wl[g[s]["abbr"]]["w" if g[s]["abbr"] == w else "l"] += 1

    collect = [("first", "First Game", len(games), 1), ("t5", "Seen 5+ Teams", len(teams_seen), 5),
               ("t30", "Seen All 30", len(teams_seen), 30), ("a10", "10 Arenas", len(arenas), 10)]

    return {
        "totals": {"games": len(games), "points": sum(points(g) for g in games),
                   "players": len({p["id"] for g in games for p in g["players"]}),
                   "teams": len(teams_seen), "arenas": len(arenas)},
        "badges": [{"key": k, "label": label, "rarity": n, "count": sum(k in g["badges"] for g in tagged)}
                   for k, label, n, _ in BADGES]
                  + [{"key": k, "label": label, "rarity": None, "count": int(have >= need),
                      "progress": f"{min(have, need)}/{need}" if need > 1 else None} for k, label, have, need in collect],
        "records": records(games),
        "teams": sorted(({"abbr": a, "name": TEAM_NAME[a], "color": TEAM_COLOR[a], "w": c["w"], "l": c["l"],
                          "games": c["w"] + c["l"]} for a, c in wl.items()),
                        key=lambda t: (-t["games"], -t["w"], t["name"])),
        "arenas": [{"venue": v, "city": c, "games": n} for (v, c), n in arenas.most_common()],
        "leaders": leaders(games),
        "milestones": milestones(games),
        "games": [g for g in tagged if (not badge or badge in g["badges"])
                  and (not team or team in (g["home"]["abbr"], g["away"]["abbr"]))],
    }


# ---------- ESPN ----------

def fetch(url: str) -> dict[str, Any]:
    # no custom User-Agent: ESPN's edge 403s anything but urllib's default
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise HTTPException(502, f"ESPN unavailable: {e}")


def eastern_date(utc: str) -> str:
    return datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(EASTERN).date().isoformat()


def stat_int(v: str) -> int:
    """'28' -> 28, '5-12' (made-attempted) -> 5, '--' -> 0."""
    m = re.match(r"\d+", v or "")
    return int(m.group()) if m else 0


@lru_cache(maxsize=512)
def boxscore(event_id: str) -> dict[str, Any]:
    """A finished game, normalised to exactly what the browser stores. Cached — it never changes."""
    d = fetch(f"{ESPN}/summary?event={event_id}")
    comp = d["header"]["competitions"][0]
    if not comp.get("status", {}).get("type", {}).get("completed"):
        raise HTTPException(400, "That game hasn’t finished yet.")
    sides = {}
    for c in comp["competitors"]:
        abbr = BY_NICK.get(c["team"]["name"].lower())
        if not abbr:
            raise HTTPException(400, "Only games between NBA franchises can be added.")
        sides[c["homeAway"]] = {"abbr": abbr, "score": int(c.get("score") or 0),
                                "periods": len(c.get("linescores", []))}
    players = []
    for team in d.get("boxscore", {}).get("players", []):
        abbr = BY_NICK.get(team["team"]["name"].lower(), "")
        st = team["statistics"][0]
        col = {label: i for i, label in enumerate(st["labels"])}
        for a in st["athletes"]:
            if a.get("didNotPlay") or not a.get("stats"):
                continue
            s = a["stats"]
            players.append({"id": a["athlete"]["id"], "name": a["athlete"]["displayName"], "team": abbr,
                            **{k: stat_int(s[col[label]]) for k, label in
                               (("min", "MIN"), ("pts", "PTS"), ("reb", "REB"), ("ast", "AST"),
                                ("stl", "STL"), ("blk", "BLK"), ("fg3m", "3PT")) if label in col}})
    venue = d.get("gameInfo", {}).get("venue", {})
    return {
        "id": event_id, "date": eastern_date(comp["date"]),
        "playoff": d["header"].get("season", {}).get("type") == 3,
        "periods": max(4, sides["home"]["periods"], sides["away"]["periods"]),
        "venue": venue.get("fullName", ""), "city": venue.get("address", {}).get("city", ""),
        "home": {"abbr": sides["home"]["abbr"], "name": TEAM_NAME[sides["home"]["abbr"]], "score": sides["home"]["score"]},
        "away": {"abbr": sides["away"]["abbr"], "name": TEAM_NAME[sides["away"]["abbr"]], "score": sides["away"]["score"]},
        "players": players,
    }


# ---------- keyword game search ----------

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
    data = fetch(f"{ESPN}/scoreboard?limit=100&dates={iso.replace('-', '')}")
    out = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        sides = {}
        for c in comp["competitors"]:
            abbr = BY_NICK.get(c["team"]["name"].lower())
            if not abbr:
                break  # All-Star / international side we can't map to a franchise
            sides[c["homeAway"]] = {"abbr": abbr, "score": int(c.get("score") or 0),
                                    "search": f"{c['team']['displayName']} {c['team']['abbreviation']} {abbr}".lower()}
        if len(sides) != 2:
            continue
        h, a = sides["home"], sides["away"]
        out.append({
            "id": ev["id"], "date": iso,
            "final": bool(comp.get("status", {}).get("type", {}).get("completed")),
            "periods": comp.get("status", {}).get("period", 4),
            "search": f"{h['search']} {a['search']}",
            "home": {"abbr": h["abbr"], "name": TEAM_NAME[h["abbr"]], "score": h["score"]},
            "away": {"abbr": a["abbr"], "name": TEAM_NAME[a["abbr"]], "score": a["score"]},
        })
    return tuple(out)


def search_games(q: str, today: Date | None = None) -> list[dict[str, Any]]:
    dates, team = parse_query(q, today)
    with ThreadPoolExecutor(max_workers=SEASONS_BACK) as pool:
        days = pool.map(scoreboard, dates)
    return [{k: v for k, v in g.items() if k != "search"}
            for day in days for g in day if not team or team in g["search"]]


# ---------- app ----------

app = FastAPI(title="NBA Games Attended")


@app.post("/api/summary")
def post_summary(body: SummaryIn):
    if body.badge and body.badge not in BADGE_TEST:
        raise HTTPException(400, "unknown badge")
    if body.team and body.team not in TEAM_NAME:
        raise HTTPException(400, "unknown team")
    games = [g.model_dump() for g in body.games]
    if bad := next((s["abbr"] for g in games for s in (g["home"], g["away"]) if s["abbr"] not in TEAM_NAME), None):
        raise HTTPException(400, f"unknown team {bad}")
    return summary(games, body.badge, body.team)


@app.get("/api/search")
def get_search(q: str):
    if not (q := q.strip()):
        raise HTTPException(400, "empty search")
    return {"games": search_games(q)}


@app.get("/api/game/{event_id}")
def get_game(event_id: str):
    if not event_id.isdigit():
        raise HTTPException(400, "bad game id")
    return boxscore(event_id)


if DIST.is_dir():  # serve the built frontend in production
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(DIST / "index.html")


def selfcheck(live: bool = False) -> None:
    pl = lambda pid, team="BOS", **kw: {"id": pid, "name": f"P{pid}", "team": team, "min": 30,
                                        **dict.fromkeys(STAT_KEYS, 0), **kw}
    g = lambda gid, date="2026-01-01", hs=100, as_=99, **kw: {
        "id": gid, "date": date, "playoff": False, "periods": 4, "venue": "TD Garden", "city": "Boston",
        "home": {"abbr": "BOS", "score": hs}, "away": {"abbr": "MIA", "score": as_}, "players": [], **kw}

    assert "td" in badges_of(g("1", players=[pl("a", pts=30, reb=10, ast=11)]))
    assert "td" not in badges_of(g("1", players=[pl("a", pts=30, reb=9, ast=11)]))
    assert {"ot", "one"} <= set(badges_of(g("1", periods=5))) and "2ot" not in badges_of(g("1", periods=5))
    assert "blow" in badges_of(g("1", hs=140, as_=100)) and "t150" not in badges_of(g("1", hs=140, as_=100))
    assert "xmas" in badges_of(g("1", date="2025-12-25")) and "po" in badges_of(g("1", playoff=True))
    assert ordinal(1) == "1st" and ordinal(12) == "12th" and ordinal(22) == "22nd"
    assert stat_int("5-12") == 5 and stat_int("--") == 0 and stat_int("37") == 37
    assert eastern_date("2025-03-15T00:30Z") == "2025-03-14"  # 8:30pm ET tip

    # empty tracker renders without blowing up
    s = summary([])
    assert s["totals"] == {"games": 0, "points": 0, "players": 0, "teams": 0, "arenas": 0}
    assert s["records"] == [] and all(not b["count"] for b in s["badges"])

    games = [g("1", "2026-01-01", 100, 99, players=[pl("a", pts=41), pl("b", "MIA", pts=12)]),
             g("2", "2026-02-01", 90, 110, periods=6, players=[pl("a", pts=20), pl("c", "MIA", reb=21, pts=20)])]
    s = summary(games)
    assert s["totals"]["games"] == 2 and s["totals"]["players"] == 3 and s["totals"]["teams"] == 2
    assert {t["abbr"]: (t["w"], t["l"]) for t in s["teams"]} == {"BOS": (1, 1), "MIA": (1, 1)}
    assert s["records"][0]["value"] == "2OT" and s["records"][4]["value"] == "41"
    pts = next(b for b in s["leaders"] if b["key"] == "pts")["top"]
    assert pts[0] == {"name": "Pa", "team": "BOS", "value": 61}
    assert next(b for b in s["badges"] if b["key"] == "p40")["count"] == 1
    assert next(b for b in s["badges"] if b["key"] == "2020")["count"] == 1
    assert [x["id"] for x in summary(games, badge="2ot")["games"]] == ["2"]
    assert summary(games + games[:1])["totals"]["games"] == 2  # duplicate ids count once

    # query parsing
    t = Date(2026, 8, 28)
    assert parse_query("warriors 3/15/26", t) == (["2026-03-15"], "warriors")
    assert parse_query("golden state 2026-03-15", t) == (["2026-03-15"], "golden state")
    d, team = parse_query("warriors 3/14", t)  # bare month/day fans out over seasons
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
        assert len(hits) == 1 and hits[0]["away"] == {"abbr": "BOS", "name": "Celtics", "score": 103}, hits
        b = boxscore(hits[0]["id"])
        assert b["date"] == "2025-03-14" and b["venue"] == "Kaseya Center" and b["home"]["abbr"] == "MIA"
        tatum = next(p for p in b["players"] if p["name"] == "Jayson Tatum")
        assert (tatum["pts"], tatum["fg3m"], tatum["team"]) == (28, 5, "BOS"), tatum
        SeenGame(**b)  # what we hand the browser round-trips through the summary model
    print("selfcheck ok" + (" (incl. live ESPN)" if live else ""))


if __name__ == "__main__":
    if "selftest" in sys.argv:
        selfcheck(live="--live" in sys.argv)
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8000)
