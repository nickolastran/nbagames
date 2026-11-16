import { useEffect, useRef, useState } from "react";
import Landing, { Ball } from "./Landing";

/* ---------- api ---------- */

type Side = { abbr: string; name: string; score: number };
/** A game you were at: the box-score snapshot /api/game/{id} returns, stored as-is. */
type Stored = {
  id: string; date: string; playoff: boolean; periods: number; venue: string; city: string;
  home: Side; away: Side; players: unknown[];
  milestones?: unknown[] | null; // career marks; missing on games stored before they existed
};
type Seen = Stored & { badges: string[] };
type Badge = { key: string; label: string; rarity: number | null; count: number; progress?: string | null };
type Board = { key: string; label: string; col: string; top: { name: string; team: string; value: number }[] };
type Summary = {
  totals: { games: number; points: number; players: number; teams: number; arenas: number };
  badges: Badge[];
  records: { label: string; value: string; sub: string }[];
  teams: { abbr: string; name: string; color: string; logo: string; w: number; l: number; games: number }[];
  arenas: { venue: string; city: string; games: number }[];
  leaders: Board[];
  badge_years: { year: string; badges: { key: string; count: number; new: boolean }[] }[];
  milestones: { title: string; done: boolean; mark: string; detail: string }[];
  games: Seen[];
};
type Filter = { badge: string | null; team: string | null };
type Found = { id: string; date: string; final: boolean; periods: number; home: Side; away: Side };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    const body = await res.text();
    let detail = body;
    try { detail = (JSON.parse(body) as { detail?: string }).detail ?? body; } catch { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : "Some games in there aren't valid");
  }
  return res.json();
}
const summarize = (games: Stored[], filter?: Filter) =>
  call<Summary>("/summary", { method: "POST", body: JSON.stringify({ games, ...filter }) });

/* ---------- local storage: no account, games live in this browser ---------- */

type Store = { handle: string; games: Stored[] };
const KEY = "nbagames.v2";

function loadStore(): Store {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (s && Array.isArray(s.games)) return { handle: String(s.handle ?? ""), games: s.games };
  } catch { /* blocked or corrupt storage: start clean */ }
  return { handle: "", games: [] };
}

/* ---------- helpers ---------- */

const PAGE = 10;
const day = (s: string, opts: Intl.DateTimeFormatOptions) => new Date(`${s}T00:00:00`).toLocaleDateString("en-US", opts);
const fmtDate = (s: string) => day(s, { month: "short", day: "numeric", year: "numeric" });
const fmtDay = (s: string) => day(s, { weekday: "short", month: "short", day: "numeric" });
const isDate = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s);
const otLabel = (p: number) => (p === 5 ? "OT" : `${p - 4}OT`);

function useHash() {
  const [hash, setHash] = useState(() => location.hash.slice(1));
  useEffect(() => {
    const on = () => setHash(location.hash.slice(1));
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return hash;
}

/** "#app" is the tracker, "#players/…" the full player tables; everything else (including in-page anchors) is the landing page. */
export default function App() {
  const hash = useHash();
  return hash === "app" ? <Tracker /> : hash.startsWith("players/") ? <PlayerStats route={hash} /> : <Landing />;
}

const Title = ({ handle, onClick }: { handle: string; onClick: () => void }) => (
  <h1><button className="plain" onClick={onClick} title="Click to rename">
    {handle ? <><em>{handle}</em>&rsquo;s Games</> : <>My <em>Games</em></>}
  </button></h1>
);

/** "Celtics 103 @ Heat 91 (OT)", winner in bold — the reference's box-score line. */
function Matchup({ g }: { g: { home: Side; away: Side; periods: number } }) {
  const homeWon = g.home.score > g.away.score;
  return (
    <span className="mu">
      <span className={homeWon ? "" : "win"}>{g.away.name} {g.away.score}</span>
      {" @ "}
      <span className={homeWon ? "win" : ""}>{g.home.name} {g.home.score}</span>
      {g.periods > 4 && <span className="ot"> ({otLabel(g.periods)})</span>}
    </span>
  );
}

function BadgePill({ b, pressed, onClick }: { b: Badge; pressed?: boolean; onClick?: () => void }) {
  const note = b.rarity ? `1 in ${b.rarity} games` : b.count ? null : b.progress;
  return (
    <button className="badge" data-earned={b.count ? 1 : 0} disabled={!onClick || !b.count || !b.rarity}
      aria-pressed={pressed} onClick={onClick}>
      ★ {b.label}
      {b.count > 1 && <span className="ct">×{b.count}</span>}
      {note && <span className="rar">{note}</span>}
    </button>
  );
}

/* ---------- tracker ---------- */

function Tracker() {
  const [store, setStore] = useState(loadStore);
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [storageOk, setStorageOk] = useState(true);
  const [filter, setFilter] = useState<Filter>({ badge: null, team: null });
  const [showAll, setShowAll] = useState(false);
  const [allTeams, setAllTeams] = useState(false);
  const [allBadges, setAllBadges] = useState(false);
  const [entering, setEntering] = useState(false);
  const gamesRef = useRef<HTMLElement>(null);

  useEffect(() => { scrollTo(0, 0); }, []);
  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(store)); setStorageOk(true); }
    catch { setStorageOk(false); }
  }, [store]);
  useEffect(() => {
    let live = true;
    summarize(store.games, filter)
      .then((s) => { if (live) { setData(s); setError(null); } })
      .catch((e: Error) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [store.games, filter]);

  // games saved before career milestones existed: fetch them again, one at a time, in the background
  useEffect(() => {
    let live = true;
    void (async () => {
      for (const id of store.games.filter((g) => !g.milestones).map((g) => g.id)) {
        if (!live) return;
        try {
          const g = await call<Stored>(`/game/${id}`);
          if (live && g.milestones) setStore((s) => ({ ...s, games: s.games.map((x) => (x.id === id ? g : x)) }));
        } catch { /* ESPN unreachable: try again next visit */ }
      }
    })();
    return () => { live = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- once per visit

  const have = new Set(store.games.map((g) => g.id));
  const add = async (id: string) => {
    const g = await call<Stored>(`/game/${id}`);
    setStore((s) => (s.games.some((x) => x.id === g.id) ? s : { ...s, games: [...s.games, g] }));
  };
  const toggle = (key: keyof Filter, value: string) => {
    setFilter((f) => ({ ...f, [key]: f[key] === value ? null : value }));
    gamesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const remove = (id: string) => {
    if (confirm("Remove this game?")) setStore((s) => ({ ...s, games: s.games.filter((g) => g.id !== id) }));
  };
  const rename = () => {
    const h = prompt("Your name (leave blank for “My Games”)", store.handle);
    if (h !== null) setStore((s) => ({ ...s, handle: h.trim().slice(0, 24) }));
  };
  const exportGames = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(store)], { type: "application/json" }));
    a.download = "my-games.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const importGames = async (file: File) => {
    try {
      const s = JSON.parse(await file.text());
      const games: Stored[] = Array.isArray(s) ? s : s?.games;
      if (!Array.isArray(games)) throw new Error("no games in that file");
      await summarize(games); // the API validates every game before we keep any of them
      if (store.games.length && !confirm(`Replace your ${store.games.length} games with ${games.length} from the file?`)) return;
      setStore({ handle: String(s.handle ?? store.handle), games });
    } catch (e) {
      setError(`Import failed: ${(e as Error).message}`);
    }
  };

  const top = (
    <nav className="topbar">
      <a href="#" className="logo"><Ball /><span>Game<b>Tracker</b></span></a>
      <span className="tools">
        <label className="ghost">Import<input type="file" accept="application/json,.json" hidden
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void importGames(f); }} /></label>
        {!!store.games.length && <button className="ghost" onClick={exportGames}>Export</button>}
      </span>
    </nav>
  );
  const notes = (
    <>
      {error && <p className="err">{error}</p>}
      {!storageOk && <p className="err">This browser is blocking storage — games won’t be kept after you leave. Export before closing.</p>}
    </>
  );

  if (!data) {
    return <div className="app"><div className="page">{top}{notes}{!error && <p className="muted center">Loading…</p>}</div></div>;
  }

  /* ----- empty: the "My Games" start screen ----- */
  if (!store.games.length) {
    const starters = data.badges.filter((b) => !b.rarity || ["td", "ot", "po"].includes(b.key));
    return (
      <div className="app"><div className="page">
        {top}{notes}
        <section className="green intro">
          <span className="kicker">★ Every game you’ve seen live</span>
          <Title handle={store.handle} onClick={rename} />
          <p className="lede">
            Build your personal scoreboard for all the NBA games you’ve been to. Get your own
            unique stats, records and highlights.
          </p>
          <GameSearch have={have} onAdd={add} />
          <p className="hint">Add games you’ve been to to earn your first badges</p>
          <div className="badges">{starters.map((b) => <BadgePill key={b.key} b={b} />)}</div>
        </section>
        <section className="box">
          <h2>Your Games</h2>
          <p className="muted">No games yet — find your first one above.</p>
        </section>
      </div></div>
    );
  }

  /* ----- populated: the stat page ----- */
  const list = data.games;
  const earned = data.badges.filter((b) => b.count);
  const teams = allTeams ? data.teams : data.teams.slice(0, 15);
  const years = [...new Set(list.map((g) => g.date.slice(0, 4)))];
  const row = (g: Seen, long: boolean) => (
    <div className="gm" key={g.id}>
      <span className="dt">{long ? fmtDate(g.date) : fmtDay(g.date)}</span>
      <span className="mid">
        <Matchup g={g} />
        {!!g.badges.length && (
          <span className="tags">
            {g.badges.map((k) => <span key={k}>★ {data.badges.find((b) => b.key === k)?.label}</span>)}
          </span>
        )}
      </span>
      <button className="del" aria-label={`Remove ${g.away.name} at ${g.home.name} on ${fmtDate(g.date)}`}
        title="Remove game" onClick={() => remove(g.id)}>×</button>
    </div>
  );

  return (
    <div className="app"><div className="page">
      {top}{notes}

      <header className="green">
        <div className="green-top">
          <div className="hero-copy">
            <span className="kicker">★ Every game you’ve seen live</span>
            <Title handle={store.handle} onClick={rename} />
          </div>
          <div className="totals">
            {([
              ["Games", data.totals.games], ["Points", data.totals.points.toLocaleString()],
              ["Players", data.totals.players], ["Teams", data.totals.teams], ["Arenas", data.totals.arenas],
            ] as const).map(([l, v]) => <div key={l}><b>{v}</b><span>{l}</span></div>)}
          </div>
        </div>
        <div className="badges">
          {(allBadges ? data.badges : earned).map((b) => (
            <BadgePill key={b.key} b={b} pressed={filter.badge === b.key} onClick={() => toggle("badge", b.key)} />
          ))}
          <button className="badge" data-earned={0} onClick={() => setAllBadges((v) => !v)}>
            ☆ {allBadges ? "Earned only" : "All badges"}
          </button>
        </div>
        <p className="hint">Tap a badge or team to filter your games</p>
      </header>

      <div className="cols2">
        <section className="box">
          <h2>Single Game Records</h2>
          {data.records.map((r) => (
            <div className="rec" key={r.label}>
              <span className="k">{r.label}</span>
              <span className="v"><b>{r.value}</b><small>{r.sub.replace(/\d{4}-\d{2}-\d{2}/, fmtDate)}</small></span>
            </div>
          ))}
        </section>

        <section className="box">
          <h2>Team Records</h2>
          <div className="thead"><span>Team</span><span>W–L</span></div>
          {teams.map((t) => (
            <button className="tr" key={t.abbr} aria-pressed={filter.team === t.abbr} onClick={() => toggle("team", t.abbr)}>
              <span><img src={t.logo} alt="" loading="lazy" />{t.name}</span><u>{t.w}–{t.l}</u>
            </button>
          ))}
          {data.teams.length > 15 && (
            <button className="more" onClick={() => setAllTeams((v) => !v)}>
              {allTeams ? "Show fewer" : `See all ${data.teams.length}`} →
            </button>
          )}
        </section>
      </div>

      <div className="cols3">
        <Ranked title="Arenas" head="Arena" col="Games"
          rows={data.arenas.map((a) => ({ key: a.venue, label: <>{a.venue}<small>{a.city}</small></>, value: a.games }))} />

        {data.leaders.map((b) => (
          <Ranked key={b.key} title={b.label} head="Player" col={b.col} href={`#players/${b.key}`}
            rows={b.top.map((p) => ({ key: p.name, label: p.name, value: p.value }))} />
        ))}

        <section className="box">
          <h2>Milestones</h2>
          {data.milestones.map((m) => (
            <div className={`tr${m.done ? "" : " locked"}`} key={m.title} title={m.detail}>
              <span>{m.title}{m.detail && <small>{m.detail}</small>}</span>
              <u>{isDate(m.mark) ? fmtDate(m.mark) : m.mark}</u>
            </div>
          ))}
        </section>
      </div>

      {!!data.badge_years.length && (
        <section className="box byyear">
          <h2>Badges by Year</h2>
          <p className="muted">Gold badges were earned for the first time that year.</p>
          <div className="years">
            {data.badge_years.map(({ year }) => (
              <button key={year} className="pill" onClick={() => document.getElementById(`b${year}`)?.scrollIntoView({ behavior: "smooth" })}>{year}</button>
            ))}
          </div>
          {data.badge_years.map(({ year, badges }) => {
            const fresh = badges.filter((b) => b.new), again = badges.filter((b) => !b.new);
            const pills = (list: typeof badges) => (
              <div className="badges">{list.map((x) => {
                const b = data.badges.find((d) => d.key === x.key)!;
                return <BadgePill key={x.key} b={{ ...b, count: x.count }} pressed={filter.badge === x.key}
                  onClick={() => toggle("badge", x.key)} />;
              })}</div>
            );
            return (
              <div key={year} id={`b${year}`} className="yrbadges">
                <h3 className="yr">{year} <small>{[fresh.length && `${fresh.length} new`, again.length && `${again.length} seen again`].filter(Boolean).join(" · ")}</small></h3>
                {!!fresh.length && <><p className="lbl">First earned</p><div data-new="1">{pills(fresh)}</div></>}
                {!!again.length && <><p className="lbl">Seen again</p><div data-new="0">{pills(again)}</div></>}
              </div>
            );
          })}
        </section>
      )}

      <section className="band">
        <h2>Been to a game recently?</h2>
        <p>Find it and add it — the box score comes with it.</p>
        {entering ? <GameSearch have={have} onAdd={add} /> : (
          <button className="navy" onClick={() => setEntering(true)}>Enter Games</button>
        )}
      </section>

      <section className="box" ref={gamesRef}>
        <h2>Games ({list.length})</h2>
        {(filter.badge || filter.team) && (
          <div className="filterbar">
            <span>
              Showing {[filter.badge && data.badges.find((b) => b.key === filter.badge)?.label,
                filter.team && data.teams.find((t) => t.abbr === filter.team)?.name].filter(Boolean).join(" + ")} games
            </span>
            <button className="link" onClick={() => setFilter({ badge: null, team: null })}>Clear</button>
          </div>
        )}
        {showAll ? (
          <>
            <div className="years">
              {years.map((y) => (
                <button key={y} className="pill" onClick={() => document.getElementById(`y${y}`)?.scrollIntoView({ behavior: "smooth" })}>{y}</button>
              ))}
            </div>
            {years.map((y) => {
              const yearGames = list.filter((g) => g.date.startsWith(y));
              return (
                <div key={y} id={`y${y}`}>
                  <h3 className="yr">{y} <small>{yearGames.length} game{yearGames.length === 1 ? "" : "s"}</small></h3>
                  {yearGames.map((g) => row(g, false))}
                </div>
              );
            })}
          </>
        ) : list.slice(0, PAGE).map((g) => row(g, true))}
        {!list.length && <p className="muted center">No games match this filter.</p>}
        {list.length > PAGE && (
          <button className="more boxed" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Show recent" : `See all ${list.length} games by year`} →
          </button>
        )}
      </section>
    </div></div>
  );
}

/* ---------- player tables: the leader boards' "See all" ---------- */

type Line = { id: string; name: string; team: string; min: number; pts: number; reb: number; ast: number; stl: number; blk: number; fg3m: number };
type Total = Line & { games: number };
type LogRow = Line & { game: string; date: string; away: string; home: string; result: string };
type PlayersOut = { games: number; totals: Total[]; log: LogRow[] };
type Col = { key: string; label: string; text?: boolean; cell?: (r: Total & LogRow) => React.ReactNode };

const STATS: Col[] = [["min", "MIN"], ["pts", "PTS"], ["reb", "REB"], ["ast", "AST"], ["stl", "STL"], ["blk", "BLK"], ["fg3m", "3PM"]]
  .map(([key, label]) => ({ key, label }));

/** "#players/fg3m" totals sorted by 3PM · "#players/fg3m/log" every player line · "#players/fg3m/<player id>" one player's games. */
function PlayerStats({ route }: { route: string }) {
  const [, stat = "pts", view = ""] = route.split("/");
  const [data, setData] = useState<PlayersOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState({ key: stat, desc: true });

  useEffect(() => { scrollTo(0, 0); }, [view]);
  useEffect(() => {
    call<PlayersOut>("/players", { method: "POST", body: JSON.stringify({ games: loadStore().games }) })
      .then(setData).catch((e: Error) => setError(e.message));
  }, []);

  const player = view && view !== "log" ? data?.totals.find((t) => t.id === view) : undefined;
  const isLog = !!view;
  const nameCol: Col = { key: "name", label: "Player", text: true, cell: (r) => <a href={`#players/${sort.key}/${r.id}`}>{r.name}</a> };
  const cols: Col[] = isLog
    ? [...(player ? [] : [nameCol]),
       { key: "date", label: "Game", text: true, cell: (r) => <>{fmtDate(r.date)}<small>
         {r.away === r.team ? <b>{r.away}</b> : r.away}@{r.home === r.team ? <b>{r.home}</b> : r.home}</small></> },
       { key: "result", label: "Result", text: true }, ...STATS]
    : [nameCol, { key: "team", label: "Team", text: true }, { key: "games", label: "G" }, ...STATS];
  const rows = ((isLog ? data?.log.filter((r) => !player || r.id === player.id) : data?.totals) ?? []) as (Total & LogRow)[];
  const val = (r: Total & LogRow, k: string) => (r as unknown as Record<string, string | number>)[k];
  const sorted = [...rows].sort((a, b) => {
    const x = val(a, sort.key), y = val(b, sort.key);
    return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1);
  });
  const by = (key: string) => setSort((s) => ({ key, desc: s.key === key ? !s.desc : !["name", "team"].includes(key) }));
  const sortLabel = cols.find((c) => c.key === sort.key)?.label ?? sort.key;

  return (
    <div className="app"><div className="page">
      <nav className="topbar">
        <a href="#app" className="logo"><Ball /><span>Game<b>Tracker</b></span></a>
        <span className="tools"><a className="ghost" href="#app">← My games</a></span>
      </nav>
      {error && <p className="err">{error}</p>}
      {data && (
        <section className="box">
          <h2>{player ? player.name : "Player Stats"}</h2>
          <div className="statbar">
            <span>
              {player ? <>{player.team} · {player.games} game{player.games === 1 ? "" : "s"} you were at · </>
                : <>From your {data.games} game{data.games === 1 ? "" : "s"} · </>}
              sorted by {sortLabel} · {sorted.length} result{sorted.length === 1 ? "" : "s"}
            </span>
            <span className="views">
              {player ? <a href={`#players/${sort.key}`}>← All players</a> : <>
                <a href={`#players/${sort.key}`} aria-current={!isLog}>Totals</a>
                <a href={`#players/${sort.key}/log`} aria-current={isLog}>Game log</a>
              </>}
            </span>
          </div>
          {sorted.length ? (
            <div className="scroll">
              <table className="stats">
                <thead><tr>{cols.map((c) => (
                  <th key={c.key} className={c.text ? "l" : undefined} aria-sort={sort.key === c.key ? (sort.desc ? "descending" : "ascending") : undefined}>
                    <button onClick={() => by(c.key)}>{c.label}{sort.key === c.key && (sort.desc ? " ▾" : " ▴")}</button>
                  </th>
                ))}</tr></thead>
                <tbody>{sorted.map((r) => (
                  <tr key={isLog ? `${r.game}-${r.id}` : r.id}>
                    {cols.map((c) => <td key={c.key} className={c.text ? "l" : undefined} data-on={sort.key === c.key ? 1 : 0}>{c.cell ? c.cell(r) : val(r, c.key)}</td>)}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : <p className="muted center">No player stats yet — <a href="#app">add a game</a> first.</p>}
        </section>
      )}
      {!data && !error && <p className="muted center">Loading…</p>}
    </div></div>
  );
}

/* ---------- pieces ---------- */

type RankRow = { key: string; label: React.ReactNode; value: number };

/** A top-5 table. "See all" expands it in place, or with `href` goes to the full player tables. */
function Ranked({ title, head, col, rows, href }: { title: string; head: string; col: string; rows: RankRow[]; href?: string }) {
  const [all, setAll] = useState(false);
  return (
    <section className="box">
      <h2>{title}</h2>
      <div className="thead"><span>{head}</span><span>{col}</span></div>
      {(all ? rows : rows.slice(0, 5)).map((r) => (
        <div className="tr" key={r.key}><span>{r.label}</span><u>{r.value}</u></div>
      ))}
      {href ? <a className="more" href={href}>See all →</a>
        : rows.length > 5 && <button className="more" onClick={() => setAll((v) => !v)}>{all ? "Top 5" : "See all"} →</button>}
    </section>
  );
}

const EXAMPLES = ["warriors 3/14", "lakers 12/25/25"];

function GameSearch({ have, onAdd }: { have: Set<string>; onAdd: (id: string) => Promise<void> }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Found[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async (query: string) => {
    if (!query.trim()) return;
    setQ(query); setBusy(true); setErr(null);
    try {
      setHits((await call<{ games: Found[] }>(`/search?q=${encodeURIComponent(query)}`)).games);
    } catch (e) {
      setErr((e as Error).message); setHits(null);
    } finally {
      setBusy(false);
    }
  };
  const add = async (id: string) => {
    setAdding(id); setErr(null);
    try { await onAdd(id); } catch (e) { setErr((e as Error).message); } finally { setAdding(null); }
  };

  return (
    <div className="search">
      <form onSubmit={(e) => { e.preventDefault(); void run(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find a game you went to"
          placeholder="Team and date, e.g. warriors 3/14" />
        <button className="navy" type="submit" disabled={busy}>{busy ? "Searching…" : "Find Games"}</button>
      </form>
      <p className="try">
        Try:{EXAMPLES.map((x) => <button key={x} type="button" className="pill" onClick={() => void run(x)}>{x}</button>)}
      </p>

      {err && <p className="err">{err}</p>}
      {hits && !hits.length && !err && <p className="muted center">No games found for “{q}”.</p>}
      {!!hits?.length && (
        <div className="results">
          <p className="found">Found {hits.length === 1 ? "it" : `${hits.length} games`} — click + to add:</p>
          {hits.map((g) => (
            <div className="result" key={g.id}>
              <span className="match">
                <small>{fmtDay(g.date)}, {g.date.slice(0, 4)}</small>
                <Matchup g={g} />
              </span>
              {have.has(g.id) ? <span className="added" title="Added">✓</span>
                : g.final ? (
                  <button className="plus" aria-label={`Add ${g.away.name} at ${g.home.name}`} disabled={adding === g.id}
                    onClick={() => void add(g.id)}>{adding === g.id ? "…" : "+"}</button>
                ) : <small>Not final</small>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
