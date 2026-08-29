import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { SignIn, configured, handleOf, supabase, useSession } from "./auth";
import Landing from "./Landing";

/* ---------- api ---------- */

export type Game = {
  id: number; date: string; opp: string; home: boolean;
  team_score: number; opp_score: number; min: number;
  pts: number; reb: number; ast: number; stl: number; blk: number;
  fg3m: number; fg3a: number; tov: number;
  accolades: string[];
};
type GameRef = { date: string; opp: string; opp_name: string; home: boolean };
export type Summary = {
  handle: string;
  totals: { games: number; record: string; pts: number; reb: number; ast: number; ppg: number };
  accolades: { key: string; label: string; count: number }[];
  records: { key: string; label: string; value: number; game: GameRef | null }[];
  splits: { label: string; record: string; games: number; pts: number; ppg: number; rpg: number; apg: number }[];
  leaders: { key: string; label: string; top: (GameRef & { value: number })[] }[];
  teams: { abbr: string; name: string; color: string; w: number; l: number; txt: string; played: number }[];
  milestones: { title: string; done: boolean; mark: string | null; detail: string }[];
  games: Game[];
};
type Filter = { acc: string | null; team: string | null };

/** One team's view of a real NBA game found by search — exactly what the add form needs. */
type Side = {
  abbr: string; name: string; home: boolean;
  team_score: number; opp: string; opp_name: string; opp_score: number;
};
type Found = { date: string; label: string; final: boolean; sides: Side[] };
type Prefill = Side & { date: string };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    const body = await res.text();
    let detail = body;
    try { detail = (JSON.parse(body) as { detail?: string }).detail ?? body; } catch { /* not json */ }
    throw new Error(detail || res.statusText);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

/* ---------- helpers ---------- */

const PAGE = 25;
const fmtDate = (s: string) =>
  new Date(`${s}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" });
const at = (g: { home: boolean }) => (g.home ? "vs" : "@");
const label = (s: Summary, key: string) => s.accolades.find((a) => a.key === key)?.label ?? key;

/* ---------- app ---------- */

/** Routing is one hash: "#login" is the only route, everything else is the landing page. */
function useHash() {
  const [hash, setHash] = useState(() => location.hash.slice(1));
  useEffect(() => {
    const on = () => setHash(location.hash.slice(1));
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export default function App() {
  const { session, ready } = useSession();
  const route = useHash();

  if (!ready) return <div className="wrap"><p className="loading">Loading…</p></div>;
  if (session) return <Tracker session={session} key={session.user.id} />;
  if (route !== "login") return <Landing />;
  if (!configured)
    return (
      <div className="wrap">
        <p className="error">
          Supabase isn’t configured. Copy <code>web/.env.example</code> to <code>web/.env.local</code>,
          paste your project URL and anon key, and restart <code>npm run dev</code>.
        </p>
        <p><a href="#">&larr; Back</a></p>
      </div>
    );
  return <SignIn />;
}

function Tracker({ session }: { session: Session }) {
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>({ acc: null, team: null });
  const [page, setPage] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [adding, setAdding] = useState(false);
  const ledgerRef = useRef<HTMLElement>(null);

  const reload = useCallback(async () => {
    const { data: rows, error } = await supabase
      .from("games").select("*").order("date", { ascending: false });
    if (error) return setError(error.message);
    const summary = await call<Summary>("/summary", {
      method: "POST",
      body: JSON.stringify({ handle: handleOf(session), games: rows ?? [], ...filter }),
    }).catch((e: Error) => { setError(e.message); return null; });
    if (summary) { setData(summary); setError(null); }
  }, [filter, session]);
  useEffect(() => { void reload(); }, [reload]);

  const toggle = (key: keyof Filter, value: string) => {
    setFilter((f) => ({ ...f, [key]: f[key] === value ? null : value }));
    setPage(0);
    ledgerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const removeGame = async (id: number) => {
    const { error } = await supabase.from("games").delete().eq("id", id);
    if (error) return setError(error.message);
    void reload();
  };

  const rename = async (h: string) => {
    await supabase.auth.updateUser({ data: { handle: h.trim().slice(0, 24) || "Handy" } });
    void reload();
  };

  const startLogging = (p: Prefill) => {
    setPrefill(p);
    setAdding(true);
    ledgerRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  };

  if (error) return <div className="wrap"><p className="error">{error}</p></div>;
  if (!data) return <div className="wrap"><p className="loading">Loading your games…</p></div>;

  const empty = data.totals.games === 0;
  const list = data.games;
  const start = showAll ? 0 : page * PAGE;
  const view = showAll ? list : list.slice(start, start + PAGE);

  return (
    <div className="wrap">
      <Masthead handle={data.handle} email={session.user.email ?? ""} onRename={(h) => void rename(h)} />

      {empty ? (
        <section className="panel blank">
          <h2>Nothing logged yet <span className="eyebrow">Clean slate</span></h2>
          <div className="body">
            <p>
              Search the NBA game logs below, pick the side you played, and add your stat line.
              Every game you log is scanned for accolades — triple-doubles, 40-point nights,
              perfect shooting — and your records, splits and milestones build from there.
            </p>
          </div>
        </section>
      ) : (
        <>
          <section className="scoreboard">
            {([
              ["Games Logged", data.totals.games],
              ["Record", data.totals.record],
              ["Points", data.totals.pts.toLocaleString()],
              ["Rebounds", data.totals.reb],
              ["Assists", data.totals.ast],
              ["PPG", data.totals.ppg.toFixed(1)],
            ] as const).map(([l, v]) => (
              <div key={l}><span className="num">{v}</span><span className="lbl">{l}</span></div>
            ))}
          </section>

          <section>
            <p className="eyebrow mb10">Accolades — tap to filter</p>
            <div className="chips">
              {data.accolades.map((a) => (
                <button key={a.key} className="chip" data-empty={a.count ? 0 : 1}
                  aria-pressed={filter.acc === a.key} onClick={() => toggle("acc", a.key)}>
                  {a.label}<span className="ct">{a.count}</span>
                </button>
              ))}
            </div>
          </section>

          <div className="grid">
            <section className="panel">
              <h2>Single Game Records <span className="eyebrow">Career bests</span></h2>
              <div className="body"><div className="rows">
                {data.records.map((r) => (
                  <div className="row" key={r.key}>
                    <span className="k">{r.label}
                      {r.game && <><br /><span className="sub">{at(r.game)} {r.game.opp_name} · {fmtDate(r.game.date)}</span></>}
                    </span>
                    <span className="v">{r.game ? r.value : "—"}</span>
                  </div>
                ))}
              </div></div>
            </section>

            <section className="panel">
              <h2>Milestones <span className="eyebrow">First &amp; furthest</span></h2>
              <div className="body">
                {data.milestones.map((m, i) => (
                  <div className={`ms${m.done ? "" : " locked"}`} key={i}>
                    <span className="mk">{m.done && m.mark ? fmtDate(m.mark) : m.mark ?? "—"}</span>
                    <span><span className="mt">{m.title}</span><br /><span className="md">{m.detail}</span></span>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <section className="panel">
            <h2>Home &amp; Away <span className="eyebrow">Split by venue</span></h2>
            <div className="split">
              {data.splits.map((s) => (
                <div key={s.label}>
                  <h3>{s.label}</h3>
                  <div className="kv"><span>Record</span><b>{s.record}</b></div>
                  <div className="kv"><span>Games</span><b>{s.games}</b></div>
                  <div className="kv"><span>Points per game</span><b>{s.ppg.toFixed(1)}</b></div>
                  <div className="kv"><span>Rebounds per game</span><b>{s.rpg.toFixed(1)}</b></div>
                  <div className="kv"><span>Assists per game</span><b>{s.apg.toFixed(1)}</b></div>
                  <div className="kv"><span>Total points</span><b>{s.pts}</b></div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>Leader Boards <span className="eyebrow">Top 5 games</span></h2>
            <div className="grid grid-flush">
              {data.leaders.map((b) => (
                <div className="leader-col" key={b.key}>
                  <h3>Most {b.label}</h3>
                  <div className="rows">
                    {b.top.map((g, i) => (
                      <div className="row" key={`${g.date}-${i}`}>
                        <span className="rank">{i + 1}</span>
                        <span className="k">{at(g)} {g.opp_name}<br /><span className="sub">{fmtDate(g.date)}</span></span>
                        <span className="v">{g.value}</span>
                      </div>
                    ))}
                    {!b.top.length && <p className="sub">No games yet.</p>}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>Record vs. Every Team <span className="eyebrow">Tap a team to filter</span></h2>
            <div className="teams">
              {data.teams.map((t) => (
                <button key={t.abbr} className="team" data-none={t.played ? 0 : 1}
                  aria-pressed={filter.team === t.abbr} style={{ ["--tc" as string]: t.color }}
                  onClick={() => toggle("team", t.abbr)}>
                  <span className="dot" />
                  <span className="nm">{t.name}<small>{t.abbr}</small></span>
                  <span className="rec">{t.txt}</span>
                </button>
              ))}
            </div>
          </section>
        </>
      )}

      <section className="panel" ref={ledgerRef}>
        <h2>Your Games <span className="eyebrow">{data.totals.games} logged</span></h2>

        <GameSearch onPick={startLogging} />

        {(filter.acc || filter.team) && (
          <div className="filterbar">
            <span>
              Filtered by {[filter.acc && label(data, filter.acc),
                filter.team && `vs ${data.teams.find((t) => t.abbr === filter.team)?.name}`]
                .filter(Boolean).map((s, i) => <b key={i}>{i ? " + " : ""}{s}</b>)}
              {" "}— {list.length} game{list.length === 1 ? "" : "s"}
            </span>
            <button className="btn" onClick={() => { setFilter({ acc: null, team: null }); setPage(0); }}>Clear</button>
          </div>
        )}

        <div className="ledger">
          {view.map((g) => {
            const team = data.teams.find((t) => t.abbr === g.opp)!;
            const win = g.team_score > g.opp_score;
            return (
              <div className="game" key={g.id} style={{ ["--tc" as string]: team.color }}>
                <span className="dt">{fmtDate(g.date)}</span>
                <span className="opp">
                  <span className="bar" />
                  <span>
                    <b><span className="loc">{at(g)}</span>{team.name}</b>
                    <span className="line">
                      {g.pts} PTS · {g.reb} REB · {g.ast} AST · {g.stl} STL · {g.blk} BLK · {g.fg3m}/{g.fg3a} 3PT · {g.tov} TO
                    </span>
                  </span>
                </span>
                <span className="sc">
                  <span className="n">{g.team_score}–{g.opp_score}</span>
                  <span className={`res ${win ? "W" : "L"}`}>{win ? "W" : "L"}</span>
                </span>
                <span className="tags">
                  {g.accolades.map((k) => <span className="tag" key={k}>{label(data, k)}</span>)}
                  <button className="del" title="Remove game"
                    aria-label={`Remove game ${at(g)} ${team.name} on ${fmtDate(g.date)}`}
                    onClick={() => void removeGame(g.id)}>×</button>
                </span>
              </div>
            );
          })}
          {!view.length && (
            <p className="empty">{empty ? "Search above to log your first game." : "No games match this filter."}</p>
          )}
        </div>

        {!empty && (
          <div className="pager">
            <span className="count">
              {list.length ? start + 1 : 0}-{showAll ? list.length : Math.min(start + PAGE, list.length)} of {list.length} games
            </span>
            <div className="pgbtns">
              <button disabled={showAll || page === 0} onClick={() => setPage((p) => p - 1)}>Prev</button>
              <button disabled={showAll || start + PAGE >= list.length} onClick={() => setPage((p) => p + 1)}>Next</button>
              <button onClick={() => { setShowAll((v) => !v); setPage(0); }}>
                {showAll ? "Show 25 per page" : "See all games"}
              </button>
              <button className="btn-solid" onClick={() => { setPrefill(null); setAdding((v) => !v); }}>
                Add manually
              </button>
            </div>
          </div>
        )}

        {adding && (
          <AddGame teams={data.teams} prefill={prefill}
            onDone={() => { setAdding(false); setPrefill(null); }}
            onSaved={() => { setAdding(false); setPrefill(null); void reload(); }} />
        )}
      </section>
    </div>
  );
}

/* ---------- pieces ---------- */

function Masthead({ handle, email, onRename }: { handle: string; email: string; onRename: (h: string) => void }) {
  return (
    <header className="masthead">
      <h1>
        <span id="handle" contentEditable suppressContentEditableWarning spellCheck={false} title="Click to rename"
          onBlur={(e) => onRename(e.currentTarget.textContent ?? "")}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }}>
          {handle}
        </span>
        <em>&rsquo;s</em> Games
      </h1>
      <div className="who">
        <span className="sub">{email}</span>
        <button className="btn" onClick={() => { location.hash = ""; void supabase.auth.signOut(); }}>Sign out</button>
      </div>
    </header>
  );
}

function GameSearch({ onPick }: { onPick: (p: Prefill) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Found[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true); setErr(null);
    try {
      const { games } = await call<{ games: Found[] }>(`/search?q=${encodeURIComponent(q)}`);
      setHits(games);
    } catch (e) {
      setErr((e as Error).message);
      setHits(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="search">
      <form onSubmit={(e) => void run(e)}>
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search NBA game logs"
          placeholder="warriors 3/14  ·  lakers 3/15/26  ·  12/25/25" />
        <button className="btn btn-solid" type="submit" disabled={busy}>{busy ? "Searching…" : "Find games"}</button>
      </form>
      <p className="sub hint">
        Team + date finds that matchup. A bare <code>3/14</code> checks that day across the last five
        seasons; add a year to pin one. A date on its own lists every game that night.
      </p>

      {err && <p className="error">{err}</p>}
      {hits && !hits.length && !err && <p className="empty">No games found for “{q}”.</p>}

      {!!hits?.length && (
        <div className="results">
          {hits.map((g) => (
            <div className="result" key={`${g.date}-${g.label}`}>
              <span className="dt">{fmtDate(g.date)}</span>
              <span className="match">
                <b>{g.sides[1].name} @ {g.sides[0].name}</b>
                <span className="sub">
                  {g.final ? `Final ${g.sides[1].team_score}–${g.sides[0].team_score}` : "Not final"}
                </span>
              </span>
              <span className="picks">
                {g.sides.map((s) => (
                  <button className="btn" key={s.abbr} onClick={() => onPick({ ...s, date: g.date })}>
                    Log as {s.name}
                  </button>
                ))}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const FIELDS: [keyof Game & string, string][] = [
  ["team_score", "Our Score"], ["opp_score", "Their Score"], ["min", "Minutes"],
  ["pts", "PTS"], ["reb", "REB"], ["ast", "AST"], ["stl", "STL"], ["blk", "BLK"],
  ["fg3m", "3PM"], ["fg3a", "3PA"], ["tov", "TO"],
];

function AddGame({ teams, prefill, onSaved, onDone }:
  { teams: Summary["teams"]; prefill: Prefill | null; onSaved: () => void; onDone: () => void }) {
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const row: Record<string, unknown> = {
      date: form.get("date"), opp: form.get("opp"), home: form.get("home") === "1",
    };
    for (const [key] of FIELDS) row[key] = Number(form.get(key)) || 0;
    row.fg3a = Math.max(row.fg3a as number, row.fg3m as number);
    setSaving(true);
    const { error } = await supabase.from("games").insert(row);
    setSaving(false);
    if (error) return setErr(error.message);
    onSaved();
  };

  return (
    <form className="add" onSubmit={(e) => void submit(e)}
      key={prefill ? `${prefill.date}-${prefill.abbr}` : "blank"}>
      {prefill && (
        <p className="prefill">
          Logging <b>{prefill.name}</b> {at(prefill)} {prefill.opp_name} · {fmtDate(prefill.date)} —
          scores are filled in, add your own line.
        </p>
      )}
      <label>Date<input type="date" name="date" required
        defaultValue={prefill?.date ?? new Date().toISOString().slice(0, 10)} /></label>
      <label>Opponent
        <select name="opp" defaultValue={prefill?.opp ?? "BOS"}>
          {teams.map((t) => <option key={t.abbr} value={t.abbr}>{t.name}</option>)}
        </select>
      </label>
      <label>Venue
        <select name="home" defaultValue={prefill && !prefill.home ? "0" : "1"}>
          <option value="1">Home</option><option value="0">Away</option>
        </select>
      </label>
      {FIELDS.map(([key, l]) => (
        <label key={key}>{l}
          <input type="number" name={key} min={0} max={300} required
            defaultValue={key === "team_score" ? prefill?.team_score ?? 0
              : key === "opp_score" ? prefill?.opp_score ?? 0 : 0} />
        </label>
      ))}
      <div className="actions">
        <button className="btn btn-solid" type="submit" disabled={saving}>{saving ? "Saving…" : "Save game"}</button>
        <button className="btn" type="button" onClick={onDone}>Cancel</button>
        {err && <span className="sub" style={{ color: "var(--loss)" }}>{err}</span>}
      </div>
    </form>
  );
}
