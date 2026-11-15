import { useEffect, useRef, useState } from "react";
import Landing, { Ball } from "./Landing";

/* ---------- api ---------- */

type Side = { abbr: string; name: string; score: number };
/** A game you were at: the box-score snapshot /api/game/{id} returns, stored as-is. */
type Stored = {
  id: string; date: string; playoff: boolean; periods: number; venue: string; city: string;
  home: Side; away: Side; players: unknown[];
};
type Seen = Stored & { badges: string[] };
type Badge = { key: string; label: string; rarity: number | null; count: number; progress?: string | null };
type Board = { key: string; label: string; col: string; top: { name: string; team: string; value: number }[] };
type Summary = {
  totals: { games: number; points: number; players: number; teams: number; arenas: number };
  badges: Badge[];
  records: { label: string; value: string; sub: string }[];
  teams: { abbr: string; name: string; color: string; w: number; l: number; games: number }[];
  arenas: { venue: string; city: string; games: number }[];
  leaders: Board[];
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

/** "#app" is the tracker; everything else (including in-page anchors) is the landing page. */
export default function App() {
  return useHash() === "app" ? <Tracker /> : <Landing />;
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
              <span><i style={{ background: t.color }} />{t.name}</span><u>{t.w}–{t.l}</u>
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
          <Ranked key={b.key} title={b.label} head="Player" col={b.col}
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

/* ---------- pieces ---------- */

type RankRow = { key: string; label: React.ReactNode; value: number };

/** A top-5 table with a "See all" toggle: arenas and every player leader board. */
function Ranked({ title, head, col, rows }: { title: string; head: string; col: string; rows: RankRow[] }) {
  const [all, setAll] = useState(false);
  return (
    <section className="box">
      <h2>{title}</h2>
      <div className="thead"><span>{head}</span><span>{col}</span></div>
      {(all ? rows : rows.slice(0, 5)).map((r) => (
        <div className="tr" key={r.key}><span>{r.label}</span><u>{r.value}</u></div>
      ))}
      {rows.length > 5 && (
        <button className="more" onClick={() => setAll((v) => !v)}>{all ? "Top 5" : "See all"} →</button>
      )}
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
