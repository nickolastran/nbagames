import { Suspense, lazy } from "react";

const Hoop = lazy(() => import("./Hoop")); // keeps three.js out of the signed-in bundle

export const REPO = "https://github.com/your-handle/nbagames"; // ← point this at your repo

/** The Game Tracker mark — same art as public/favicon.svg. */
export const Ball = ({ size = 26 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
    <rect x="1" y="1" width="30" height="30" rx="7" fill="#1f3527" stroke="#e3b347" strokeWidth="1.5" />
    <circle cx="16" cy="16" r="9.5" fill="#e3b347" />
    <path d="M6.5 16h19M16 6.5v19M9.3 9.3c3.6 3.4 3.6 10 0 13.4M22.7 9.3c-3.6 3.4-3.6 10 0 13.4" fill="none" stroke="#1f3527" strokeWidth="1.5" />
  </svg>
);

const STEPS = [
  ["Find the game", <>Type <code>warriors 3/14</code> and get that night in every recent season, or <code>12/25/25</code> for the whole slate. Team name, city or abbreviation — all match.</>],
  ["Hit +", <>That is it. The full box score comes with it — final score, overtimes, the arena and every player’s line.</>],
  ["Watch it add up", <>Every game you add feeds your records, your player leader boards and your badges.</>],
] as const;

const TRACKS = [
  ["Team records", "How every team has done in front of you — your good-luck and bad-luck teams."],
  ["Single-game records", "Longest game, highest and lowest scoring, biggest blowout, best individual lines you’ve seen."],
  ["Player leaders", "Most points, rebounds, assists and threes you’ve watched, totalled across every game."],
  ["Milestones", "Your 1st, 10th, 25th, 50th game, first overtime, first playoff game, first triple-double."],
] as const;

const ACCOLADES = ["Triple-Double", "40-Point Game", "50-Point Game", "20-20 Game", "10 Threes",
  "Overtime", "Double OT", "One-Point Game", "Christmas Game", "Playoff Game", "Seen All 30"];

export default function Landing() {
  return (
    <div className="landing">
      <nav className="nav">
        <a className="logo" href="#top"><Ball /><span>Game<b>Tracker</b></span></a>
        <div className="navlinks">
          <a href="#how">How it works</a>
          <a href="#tracks">What it tracks</a>
          <a className="btn btn-solid" href="#app">Open tracker</a>
        </div>
      </nav>

      <header className="hero" id="top">
        <div className="hero-copy">
          <p className="eyebrow">Every game you’ve seen live</p>
          <h1>Every game<br />you’ve seen,<br /><em>scored.</em></h1>
          <p className="lede">
            Find the NBA games you’ve been to and add them with one click. The real box scores come
            with them — records, leaders and badges build themselves from there.
          </p>
          <div className="hero-cta">
            <a className="btn btn-solid" href="#app">Start logging</a>
            <a className="btn" href="#how">See how it works</a>
          </div>
        </div>
        <Suspense fallback={<div className="court" />}><Hoop /></Suspense>
      </header>

      <section className="strip" id="how">
        <p className="eyebrow mb10">How it works</p>
        <div className="steps">
          {STEPS.map(([title, body], i) => (
            <div className="step" key={title}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>
              <h3>{title}</h3>
              <p>{body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="strip" id="tracks">
        <p className="eyebrow mb10">What it tracks</p>
        <p className="lede narrow">
          Every game you add is scanned for badges the moment it lands.
        </p>
        <div className="chips">
          {ACCOLADES.map((a) => <span className="chip" key={a}>{a}</span>)}
        </div>
        <div className="tracks">
          {TRACKS.map(([title, body]) => (
            <div className="track" key={title}><h3>{title}</h3><p>{body}</p></div>
          ))}
        </div>
        <p className="fineprint">
          No account needed — your games are saved in this browser. Export them to a file any time
          to back them up or move them to another device.
        </p>
      </section>

      <section className="strip cta">
        <h2>Clean slate, waiting.</h2>
        <p className="lede narrow">No sample data, no filler. The first game in there is yours.</p>
        <a className="btn btn-solid" href="#app">Start logging</a>
      </section>

      <footer className="foot">
        <a className="mark" href={REPO} target="_blank" rel="noreferrer noopener">
          <Ball size={16} /><span>© {new Date().getFullYear()} Game Tracker™</span>
        </a>
        <p className="fineprint">
          <a href={REPO} target="_blank" rel="noreferrer noopener">Source on GitHub</a>. Not
          affiliated with or endorsed by the NBA. Schedule and scores from ESPN’s public scoreboard.
        </p>
      </footer>
    </div>
  );
}
