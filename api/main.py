"""NBA games-attended tracker API.

Stateless: the browser keeps the games you've been to (each one a box-score snapshot taken
when you added it) and posts them here; this module computes every derived stat, so the
client never re-implements them. Also proxies ESPN's public NBA scoreboard (search) and
box scores (adding a game).
"""
import json, re, sys, unicodedata, urllib.error, urllib.request
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
ATHLETES = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes"
RECENT_HITS = 10  # a bare month/day like "10/29" walks back year by year until this many games
FIRST_YEAR = 1993  # ...but no further back than this: ESPN's scoreboard starts here
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
ESPN_CODE = {"GSW": "gs", "NOP": "no", "NYK": "ny", "SAS": "sa", "UTA": "utah", "WAS": "wsh"}  # the rest: lowercase abbr
TEAM_LOGO = {a: f"https://a.espncdn.com/i/teamlogos/nba/500/{ESPN_CODE.get(a, a.lower())}.png" for a, _, _ in TEAMS}
# ESPN's team id follows the franchise through renames (Sonics -> Thunder, NO Hornets -> Pelicans, Bobcats -> Hornets)
BY_ESPN_ID = {"1": "ATL", "2": "BOS", "17": "BKN", "30": "CHA", "4": "CHI", "5": "CLE", "6": "DAL", "7": "DEN",
              "8": "DET", "9": "GSW", "10": "HOU", "11": "IND", "12": "LAC", "13": "LAL", "29": "MEM", "14": "MIA",
              "15": "MIL", "16": "MIN", "3": "NOP", "18": "NYK", "25": "OKC", "19": "ORL", "20": "PHI", "21": "PHX",
              "22": "POR", "23": "SAC", "24": "SAS", "28": "TOR", "26": "UTA", "27": "WAS"}
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


class CareerMark(BaseModel):
    """A player crossing a round career number (regular season) in a game you saw."""
    id: str
    name: str
    team: str
    stat: str
    mark: int


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
    milestones: list[CareerMark] | None = None  # None: not computed yet, the browser re-fetches the game


class SummaryIn(BaseModel):
    games: list[SeenGame] = []
    badge: str | None = None
    team: str | None = None


Game = dict[str, Any]

# ---------- arenas ----------

# ESPN files every game under its building's latest name (a 2015 Lakers game says "crypto.com Arena").
# Keyed by ESPN's name: (first day of each name, the name), oldest first. Moved-out-of buildings
# (Oracle Arena, KeyArena, The Palace) are their own ESPN venues, so they only need their renames too.
# ponytail: rename days are to the month or so; fix a date if a game near a rename shows the wrong name
ARENA_NAMES: dict[str, list[tuple[str, str]]] = {
    "crypto.com Arena": [("1999-10-01", "Staples Center"), ("2021-12-25", "Crypto.com Arena")],
    "Capital One Arena": [("1997-12-01", "MCI Center"), ("2006-07-01", "Verizon Center"), ("2017-08-09", "Capital One Arena")],
    "Gainbridge Fieldhouse": [("1999-11-01", "Conseco Fieldhouse"), ("2011-12-22", "Bankers Life Fieldhouse"),
                              ("2021-08-24", "Gainbridge Fieldhouse")],
    "Kaseya Center": [("1999-12-01", "American Airlines Arena"), ("2021-06-04", "FTX Arena"),
                      ("2023-01-11", "Miami-Dade Arena"), ("2023-04-03", "Kaseya Center")],
    "Xfinity Mobile Arena": [("1996-08-01", "CoreStates Center"), ("1998-07-01", "First Union Center"),
                             ("2003-08-01", "Wachovia Center"), ("2010-07-01", "Wells Fargo Center"),
                             ("2025-09-01", "Xfinity Mobile Arena")],
    "Delta Center": [("1991-10-01", "Delta Center"), ("2006-11-20", "EnergySolutions Arena"),
                     ("2015-10-26", "Vivint Smart Home Arena"), ("2020-07-01", "Vivint Arena"), ("2023-07-01", "Delta Center")],
    "Mortgage Matchup Center": [("1992-06-01", "America West Arena"), ("2006-01-01", "US Airways Center"),
                                ("2015-07-01", "Talking Stick Resort Arena"), ("2020-07-01", "Phoenix Suns Arena"),
                                ("2021-07-01", "Footprint Center"), ("2025-10-01", "Mortgage Matchup Center")],
    "Paycom Center": [("2002-06-01", "Ford Center"), ("2010-03-01", "Oklahoma City Arena"),
                      ("2011-06-01", "Chesapeake Energy Arena"), ("2021-07-01", "Paycom Center")],
    "Rocket Arena": [("1994-10-01", "Gund Arena"), ("2005-04-01", "Quicken Loans Arena"),
                     ("2019-08-01", "Rocket Mortgage FieldHouse"), ("2025-05-01", "Rocket Arena")],
    "Frost Bank Center": [("2002-10-01", "SBC Center"), ("2006-01-01", "AT&T Center"), ("2023-07-01", "Frost Bank Center")],
    "Spectrum Center": [("2005-10-01", "Charlotte Bobcats Arena"), ("2008-04-01", "Time Warner Cable Arena"),
                        ("2016-08-01", "Spectrum Center")],
    "State Farm Arena": [("1999-09-01", "Philips Arena"), ("2018-06-01", "State Farm Arena")],
    "TD Garden": [("1995-09-01", "FleetCenter"), ("2005-03-01", "TD Banknorth Garden"), ("2009-07-01", "TD Garden")],
    "Moda Center": [("1995-10-01", "Rose Garden"), ("2013-08-01", "Moda Center")],
    "Ball Arena": [("1999-10-01", "Pepsi Center"), ("2020-10-01", "Ball Arena")],
    "Scotiabank Arena": [("1999-02-01", "Air Canada Centre"), ("2018-07-01", "Scotiabank Arena")],
    "Kia Center": [("2010-10-01", "Amway Center"), ("2023-08-01", "Kia Center")],
    "Smoothie King Center": [("1999-10-01", "New Orleans Arena"), ("2014-02-01", "Smoothie King Center")],
    "Toyota Center (Houston)": [("2003-10-01", "Toyota Center")],
    "Oracle Arena": [("1966-11-01", "Oakland-Alameda County Coliseum Arena"), ("1997-11-01", "The Arena in Oakland"),
                     ("2006-08-01", "Oracle Arena")],
    "Sleep Train Arena": [("1988-11-01", "ARCO Arena"), ("2011-03-01", "Power Balance Pavilion"), ("2012-02-01", "Sleep Train Arena")],
    "Amway Arena": [("1989-01-01", "Orlando Arena"), ("2000-01-01", "TD Waterhouse Centre"), ("2006-09-01", "Amway Arena")],
    "IZOD Center": [("1981-07-01", "Brendan Byrne Arena"), ("1996-01-01", "Continental Airlines Arena"), ("2007-10-01", "IZOD Center")],
    "BMO Harris Bradley Center": [("1988-10-01", "Bradley Center"), ("2012-03-01", "BMO Harris Bradley Center")],
}


def arena_at(venue: str, day: str) -> str:
    """The building's name on that day: arena_at("crypto.com Arena", "2015-01-07") -> "Staples Center"."""
    venue = venue.strip()
    return next((name for since, name in reversed(ARENA_NAMES.get(venue, [])) if since <= day), venue)


# ---------- badges ----------

def top(g: Game, k: str) -> int:
    return max((p[k] for p in g["players"]), default=0)


def margin(g: Game) -> int:
    return abs(g["home"]["score"] - g["away"]["score"])


def points(g: Game) -> int:
    return g["home"]["score"] + g["away"]["score"]


def triple_double(p: dict) -> bool:
    return sum(p[k] >= 10 for k in ("pts", "reb", "ast", "stl", "blk")) >= 3


EAST = {"ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DET", "IND", "MIA", "MIL", "NYK", "ORL", "PHI", "TOR", "WAS"}


def finals(g: Game) -> bool:
    """The Finals are the only playoff round between the conferences. (New Orleans' franchise was East until 2004.)"""
    east = lambda a: a in EAST or (a == "NOP" and g["date"] < "2004-07-01")
    return g["playoff"] and east(g["home"]["abbr"]) != east(g["away"]["abbr"])


def plain(name: str) -> str:
    """"Dražen Petrović" / "Shaquille O’Neal" -> "drazen petrovic" / "shaquille oneal"."""
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())


# Inducted players who played in a game ESPN covers (1993 on), through the 2025 class.
# ponytail: by name, add each new class; switch to ESPN athlete ids if two players ever share a name
HALL_OF_FAMERS = {plain(n) for n in (
    "Michael Jordan, Scottie Pippen, Dennis Rodman, Hakeem Olajuwon, Patrick Ewing, David Robinson, Karl Malone, "
    "John Stockton, Charles Barkley, Clyde Drexler, Dominique Wilkins, Robert Parish, Kevin McHale, Magic Johnson, "
    "James Worthy, Isiah Thomas, Joe Dumars, Bernard King, Moses Malone, Drazen Petrovic, Shaquille O'Neal, "
    "Kobe Bryant, Tim Duncan, Kevin Garnett, Allen Iverson, Yao Ming, Dirk Nowitzki, Steve Nash, Jason Kidd, "
    "Gary Payton, Reggie Miller, Alonzo Mourning, Dikembe Mutombo, Grant Hill, Ray Allen, Paul Pierce, Chris Webber, "
    "Ben Wallace, Tracy McGrady, Vince Carter, Pau Gasol, Tony Parker, Manu Ginobili, Dwyane Wade, Chris Bosh, "
    "Tim Hardaway, Chauncey Billups, Mitch Richmond, Chris Mullin, Arvydas Sabonis, Toni Kukoc, Dino Radja, "
    "Sarunas Marciulionis, Vlade Divac, Maurice Cheeks, Carmelo Anthony, Dwight Howard").split(", ")}


def hall_of_famers(g: Game) -> list[str]:
    return [p["name"] for p in g["players"] if plain(p["name"]) in HALL_OF_FAMERS]


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
    ("fin",  "NBA Finals",       260, finals),
    ("hof",  "Hall of Famer",    3,   lambda g: bool(hall_of_famers(g))),  # common in the '90s, rare since 2020
]

# You were there when an NBA record fell. Box scores can't tell (no per-quarter or career stats), so: ESPN
# event ids, each checked against its box score (except Curry's 13, which ESPN has as all zeros).
# ponytail: hand-kept list, add new records' event ids as they happen
RECORD_GAMES = [
    ("r3",  "3-Point Record", {"230107013",    # Kobe Bryant, 12 threes (2003)
                               "400899549",    # Stephen Curry, 13 (2016)
                               "401070772"}),  # Klay Thompson, 14 (2018)
    ("rq",  "Quarter Record", {"400578943"}),  # Klay Thompson, 37 points in a quarter (2015)
    ("rpts", "Scoring King",  {"401468976"}),  # LeBron James passes Kareem for the all-time points record (2023)
    ("r3c", "Splash King",    {"401360238"}),  # Stephen Curry passes Ray Allen for the all-time threes record (2021)
    ("rtd", "Triple-Double King", {"401307835"}),  # Russell Westbrook's 182nd, passing Oscar Robertson (2021)
]
GAMES_SINCE_1993 = 40_000  # roughly every game ESPN covers
BADGES += [(k, label, GAMES_SINCE_1993 // len(ids), lambda g, ids=ids: g["id"] in ids) for k, label, ids in RECORD_GAMES]
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


def player_totals(games: list[Game]) -> list[dict[str, Any]]:
    """One row per player you've seen: games, minutes and every counting stat, summed."""
    tot: dict[str, dict[str, Any]] = {}
    for g in sorted(games, key=lambda g: g["date"]):
        for p in g["players"]:
            t = tot.setdefault(p["id"], {"id": p["id"], "games": 0, "min": 0, **dict.fromkeys(STAT_KEYS, 0)})
            t.update(name=p["name"], team=p["team"], games=t["games"] + 1, min=t["min"] + p["min"],
                     **{k: t[k] + p[k] for k in STAT_KEYS})  # latest team wins
    return list(tot.values())


def game_log(games: list[Game]) -> list[dict[str, Any]]:
    """Every player line from every game you saw, newest first, with the game it came from."""
    out = []
    for g in sorted(games, key=lambda g: g["date"], reverse=True):
        for p in g["players"]:
            mine, opp = (g["home"], g["away"]) if p["team"] == g["home"]["abbr"] else (g["away"], g["home"])
            out.append({**p, "game": g["id"], "date": g["date"], "away": g["away"]["abbr"], "home": g["home"]["abbr"],
                        "result": f"{'W' if mine['score'] > opp['score'] else 'L'} {mine['score']}–{opp['score']}"})
    return out


def leaders(games: list[Game], n: int = 25) -> list[dict[str, Any]]:
    totals = player_totals(games)
    boards = [("games", "Most Seen", "GP"), ("pts", "Most Points", "PTS"), ("reb", "Most Rebounds", "REB"),
              ("ast", "Most Assists", "AST"), ("fg3m", "Most Threes", "3PM"), ("blk", "Most Blocks", "BLK"),
              ("stl", "Most Steals", "STL")]
    return [{"key": k, "label": label, "col": col,
             "top": [{"name": t["name"], "team": t["team"], "value": t[k]}
                     for t in sorted(totals, key=lambda t: (-t[k], t["name"]))[:n] if t[k]]}
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
                    "detail": f"{p['name']} · {p['pts']} PTS, {p['reb']} REB, {p['ast']} AST"})
    if hit := next(((n, g) for g in asc for n in hall_of_famers(g)), None):
        out.append({"title": "First Hall of Famer", "done": True, "mark": hit[1]["date"], "detail": hit[0]})
    out += [{"title": f"{m['mark']:,}th career {CAREER_WORD.get(m['stat'], m['stat'])}", "done": True,
             "mark": g["date"], "detail": m["name"]}
            for g in asc for m in g.get("milestones") or []]
    return out


def badge_years(tagged: list[Game]) -> list[dict[str, Any]]:
    """Newest year first: each game badge earned that year, how often, and whether it was the first time ever."""
    first: dict[str, str] = {}
    years: dict[str, Counter] = defaultdict(Counter)
    for g in sorted(tagged, key=lambda g: g["date"]):
        for k in g["badges"]:
            first.setdefault(k, g["date"][:4])
        years[g["date"][:4]].update(g["badges"])
    order = {k: i for i, (k, *_) in enumerate(BADGES)}
    return [{"year": y, "badges": [{"key": k, "count": n, "new": first[k] == y} for k, n in sorted(c.items(), key=lambda kv: order[kv[0]])]}
            for y, c in sorted(years.items(), reverse=True) if c]


def summary(games: list[Game], badge: str | None = None, team: str | None = None) -> dict[str, Any]:
    games = sorted({g["id"]: g for g in games}.values(), key=lambda g: g["date"], reverse=True)  # one entry per game
    teams_seen = {g[s]["abbr"] for g in games for s in ("home", "away")}
    arenas = Counter((arena_at(g["venue"], g["date"]), g["city"]) for g in games if g["venue"].strip())
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
        "badge_years": badge_years(tagged),
        "records": records(games),
        "teams": sorted(({"abbr": a, "name": TEAM_NAME[a], "color": TEAM_COLOR[a], "logo": TEAM_LOGO[a], "w": c["w"], "l": c["l"],
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
        abbr = BY_ESPN_ID.get(c["team"]["id"])
        if not abbr:
            raise HTTPException(400, "Only games between NBA franchises can be added.")
        sides[c["homeAway"]] = {"abbr": abbr, "score": int(c.get("score") or 0),
                                "periods": len(c.get("linescores", []))}
    players = []
    for team in d.get("boxscore", {}).get("players", []):
        abbr = BY_ESPN_ID.get(team["team"]["id"], "")
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
        "regular": d["header"].get("season", {}).get("type") == 2,  # not preseason, play-in or playoffs
        "periods": max(4, sides["home"]["periods"], sides["away"]["periods"]),
        "venue": venue.get("fullName", ""), "city": venue.get("address", {}).get("city", ""),
        "home": {"abbr": sides["home"]["abbr"], "name": TEAM_NAME[sides["home"]["abbr"]], "score": sides["home"]["score"]},
        "away": {"abbr": sides["away"]["abbr"], "name": TEAM_NAME[sides["away"]["abbr"]], "score": sides["away"]["score"]},
        "players": players,
    }


# ---------- career milestones ----------

# our stat key -> ESPN's name for it, in both the athlete stats and game log endpoints
CAREER = {"pts": "points", "reb": "totalRebounds", "ast": "assists", "stl": "steals", "blk": "blocks",
          "fg3m": "threePointFieldGoalsMade-threePointFieldGoalsAttempted"}
CAREER_WORD = {"pts": "point", "reb": "rebound", "ast": "assist", "stl": "steal", "blk": "block", "fg3m": "three"}
MILESTONE_EVERY = 1000


def season_of(day: str) -> int:
    """ESPN's season year (the year it ends) for a game date. The 2020 bubble ran July to October."""
    y = int(day[:4])
    return y if day[5:] < "08-01" or "2020-07" <= day[:7] <= "2020-10" else y + 1


@lru_cache(maxsize=4096)
def athlete_frozen(path: str) -> dict[str, Any]:
    return fetch(f"{ATHLETES}/{path}")


def career_before(pid: str, day: str) -> Counter:
    """Regular-season career totals going into that day's game."""
    season = season_of(day)
    # past seasons never change, so cache them; the current season's game log grows
    stats = athlete_frozen(f"{pid}/stats")  # only rows before `season` are read, and those are final
    log = (athlete_frozen if season < season_of(Date.today().isoformat()) else
           lambda path: fetch(f"{ATHLETES}/{path}"))(f"{pid}/gamelog?season={season}")
    tot: Counter = Counter()
    cat = next(c for c in stats.get("categories", []) if c["name"] == "totals")
    col = {n: i for i, n in enumerate(cat["names"])}
    for row in cat["statistics"]:  # one row per team stint, plus a "Totals" row when traded: skip that
        if row["season"]["year"] < season and "Totals" not in (row.get("teamSlug") or ""):
            tot.update({k: stat_int(row["stats"][col[n]]) for k, n in CAREER.items()})
    col = {n: i for i, n in enumerate(log.get("names", []))}
    for st in log.get("seasonTypes", []):
        if st["displayName"].endswith("Regular Season") and "Play In" not in st["displayName"]:
            for cat in st["categories"]:
                for e in cat.get("events", []):
                    ev = log["events"][e["eventId"]]
                    # ESPN's regular-season log also holds the All-Star Game and the Cup final; neither counts
                    if eastern_date(ev["gameDate"]) < day and not re.search(r"All-Star|Championship", ev.get("eventNote", "")):
                        tot.update({k: stat_int(e["stats"][col[n]]) for k, n in CAREER.items()})
    return tot


def career_marks(g: Game) -> list[dict[str, Any]]:
    """Every player who crossed a multiple of MILESTONE_EVERY in a stat during this (regular-season) game."""
    if not g.get("regular"):
        return []  # career milestones count the regular season
    playing = [p for p in g["players"] if any(p[k] for k in CAREER)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        befores = pool.map(lambda p: career_before(p["id"], g["date"]), playing)
    return [{"id": p["id"], "name": p["name"], "team": p["team"], "stat": k, "mark": after // MILESTONE_EVERY * MILESTONE_EVERY}
            for p, before in zip(playing, befores) for k in CAREER
            if (after := before[k] + p[k]) // MILESTONE_EVERY > before[k] // MILESTONE_EVERY]


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
            if y < 100:  # "10/29/98" is 1998, "3/15/26" is 2026
                y += 2000 if y <= today.year % 100 else 1900
        else:  # bare month/day: every occurrence back to FIRST_YEAR, not-future
            mon, day, y = int(mm), int(dd), None
        try:
            if y is not None:
                dates.append(Date(y, mon, day))
            else:
                dates += [d for yr in range(today.year, FIRST_YEAR - 1, -1)
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
            abbr = BY_ESPN_ID.get(c["team"]["id"])
            if not abbr:
                break  # All-Star / international side we can't map to a franchise
            sides[c["homeAway"]] = {"abbr": abbr, "score": int(c.get("score") or 0),
                                    "search": f"{c['team']['displayName']} {c['team']['abbreviation']} {abbr} {TEAM_NAME[abbr]}".lower()}
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
    """Newest first. A single date returns its whole slate; a yearless date stops at RECENT_HITS."""
    dates, team = parse_query(q, today)
    hits: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for i in range(0, len(dates), 5):  # five years at a time, stop once we have enough
            hits += [{k: v for k, v in g.items() if k != "search"}
                     for day in pool.map(scoreboard, dates[i:i + 5])
                     for g in day if not team or team in g["search"]]
            if len(hits) >= RECENT_HITS:
                break
    return hits if len(dates) == 1 else hits[:RECENT_HITS]


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


@app.post("/api/players")
def post_players(body: SummaryIn):
    """The "See all" page: every player's totals and every player line, for the browser to sort."""
    games = list({g.id: g.model_dump() for g in body.games}.values())  # one entry per game
    return {"games": len(games), "totals": player_totals(games), "log": game_log(games)}


@app.get("/api/search")
def get_search(q: str):
    if not (q := q.strip()):
        raise HTTPException(400, "empty search")
    return {"games": search_games(q)}


@app.get("/api/game/{event_id}")
def get_game(event_id: str):
    if not event_id.isdigit():
        raise HTTPException(400, "bad game id")
    g = boxscore(event_id)
    try:
        return {**g, "milestones": career_marks(g)}
    except (HTTPException, KeyError, StopIteration, IndexError):
        return g  # no career data right now: the browser asks again later


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
    tor_gsw = dict(home={"abbr": "GSW", "score": 110}, away={"abbr": "TOR", "score": 114})
    assert "fin" in badges_of(g("1", playoff=True, **tor_gsw)) and "fin" not in badges_of(g("1", **tor_gsw))
    assert "fin" not in badges_of(g("1", playoff=True))  # BOS-MIA: an East series
    assert "fin" not in badges_of(g("1", "2003-05-01", playoff=True, home={"abbr": "NOP", "score": 90}, away={"abbr": "PHI", "score": 80}))
    assert "hof" in badges_of(g("1", players=[pl("x", name="Dražen Petrović")])) and plain("Shaquille O’Neal") == "shaquille oneal"
    assert "hof" not in badges_of(g("1", players=[pl("x", name="Tim Hardaway Jr.")]))
    assert season_of("2019-11-11") == 2020 and season_of("2020-03-01") == 2020 and season_of("2020-08-10") == 2020
    assert season_of("2020-12-23") == 2021
    assert "r3" in badges_of(g("401070772")) and "rq" in badges_of(g("400578943")) and not {"r3", "rq"} & set(badges_of(g("1")))
    assert ordinal(1) == "1st" and ordinal(12) == "12th" and ordinal(22) == "22nd"
    assert stat_int("5-12") == 5 and stat_int("--") == 0 and stat_int("37") == 37
    assert eastern_date("2025-03-15T00:30Z") == "2025-03-14"  # 8:30pm ET tip
    assert arena_at("crypto.com Arena", "2015-01-07") == "Staples Center"
    assert arena_at("crypto.com Arena", "2023-02-07") == "Crypto.com Arena"
    assert arena_at("Delta Center", "2008-01-01") == "EnergySolutions Arena" and arena_at("Delta Center", "1999-01-01") == "Delta Center"
    assert arena_at("Chase Center", "2019-11-11") == "Chase Center" and arena_at("Charlotte Coliseum   ", "1999-01-01") == "Charlotte Coliseum"
    assert arena_at("Staples Center", "2015-01-07") == "Staples Center"  # already-fixed names pass through
    assert [a["venue"] for a in summary([g("1", "2015-01-07", venue="crypto.com Arena", city="Los Angeles"),
                                         g("2", "2023-02-07", venue="crypto.com Arena", city="Los Angeles")])["arenas"]] \
        == ["Crypto.com Arena", "Staples Center"]  # same building, two names, newest game first

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
    by = summary(games + [g("3", "2027-01-01", periods=6)])["badge_years"]
    assert [y["year"] for y in by] == ["2027", "2026"]
    assert {"key": "2ot", "count": 1, "new": False} in by[0]["badges"] and {"key": "2ot", "count": 1, "new": True} in by[1]["badges"]
    assert {"key": "p40", "count": 1, "new": True} in by[1]["badges"]
    assert summary(games + games[:1])["totals"]["games"] == 2  # duplicate ids count once
    a = next(t for t in player_totals(games) if t["id"] == "a")
    assert (a["games"], a["pts"], a["min"]) == (2, 61, 60)
    log = game_log(games)
    assert log[0]["game"] == "2" and len(log) == 4
    assert next(r for r in log if r["id"] == "c")["result"] == "W 110–90"  # MIA, the away side, won game 2

    # query parsing
    t = Date(2026, 8, 28)
    assert parse_query("warriors 3/15/26", t) == (["2026-03-15"], "warriors")
    assert parse_query("golden state 2026-03-15", t) == (["2026-03-15"], "golden state")
    d, team = parse_query("warriors 3/14", t)  # bare month/day fans out over seasons
    assert d[:3] == ["2026-03-14", "2025-03-14", "2024-03-14"] and d[-1] == "1993-03-14" and team == "warriors"
    assert parse_query("12/25", t)[0][0] == "2025-12-25"  # future dates dropped
    assert parse_query("10/29/98", t)[0] == ["1998-10-29"]
    assert parse_query("2/29", t)[0] == ["2024-02-29", "2020-02-29", "2016-02-29", "2012-02-29", "2008-02-29", "2004-02-29", "2000-02-29", "1996-02-29"]  # leap years only
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
        lebron = get_game(search_games("lakers 2024-03-02")[0]["id"])["milestones"]  # LeBron's 40,000th point
        assert {"name": "LeBron James", "stat": "pts", "mark": 40000} in [{k: m[k] for k in ("name", "stat", "mark")} for m in lebron], lebron
        old = search_games("lakers 12/25")  # yearless: walks back year by year, capped
        assert len(old) == RECENT_HITS and old[-1]["date"] < "2020", old
        assert search_games("pelicans 11/5/10")[0]["home"]["abbr"] == "NOP"  # the New Orleans Hornets, by franchise
    print("selfcheck ok" + (" (incl. live ESPN)" if live else ""))


if __name__ == "__main__":
    if "selftest" in sys.argv:
        selfcheck(live="--live" in sys.argv)
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8000)
