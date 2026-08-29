import { Suspense, lazy } from "react";

const Hoop = lazy(() => import("./Hoop")); // keeps three.js out of the signed-in bundle

export const REPO = "https://github.com/your-handle/nbagames"; // ← point this at your repo

export const Ball = ({ size = 22 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
    <circle cx="12" cy="12" r="10" />
    <path d="M2 12h20M12 2v20M4.9 4.9c4 3.2 4 10.9 0 14.2M19.1 4.9c-4 3.2-4 10.9 0 14.2" />
  </svg>
);

const STEPS = [
  ["Find the game", <>Type <code>warriors 3/14</code> and get that night in every recent season, or <code>12/25/25</code> for the whole slate. Team name, city or abbreviation — all match.</>],
  ["Pick your side", <>Every result gives you both teams. Choose one and the date, opponent, venue and final score are already filled in.</>],
  ["Add your line", <>Points, boards, assists, minutes, threes. That is the only part you type — everything below computes itself.</>],
] as const;

const TRACKS = [
  ["Single-game records", "Career bests for points, assists, rebounds, blocks, steals and threes, each stamped with the night it happened."],
  ["Home & away splits", "Record, per-game averages and totals, split by venue."],
  ["Leader boards", "Your top five games in every category, ranked."],
  ["Milestones", "First triple-double, first 40, first perfect night from three — plus career point thresholds and how far off the next one is."],
] as const;

const ACCOLADES = ["Triple-Double", "40+ Points", "50+ Points", "20+ Rebounds", "10+ Assists",
  "5+ Blocks", "Perfect 3PT", "No Turnovers", "Career High"];

export default function Landing() {
  return (
    <div className="landing">
      <nav className="nav">
        <a className="logo" href="#top"><Ball /><span>Game<b>Tracker</b></span></a>
        <div className="navlinks">
          <a href="#how">How it works</a>
          <a href="#tracks">What it tracks</a>
          <a className="btn btn-solid" href="#login">Sign in</a>
        </div>
      </nav>

      <header className="hero" id="top">
        <div className="hero-copy">
          <p className="eyebrow">Personal box score, real schedule</p>
          <h1>Log your line<br />against the<br /><em>real thing.</em></h1>
          <p className="lede">
            Search any night in the NBA game logs, pick the side you played, and add your stat
            line. Accolades, records, splits and milestones build themselves from there.
          </p>
          <div className="hero-cta">
            <a className="btn btn-solid" href="#login">Start logging</a>
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
          Every game you save is scanned for accolades the moment it lands.
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
          Your games are stored against your account with Postgres row-level security — they
          follow you to any browser, and nobody else can read them.
        </p>
      </section>

      <section className="strip cta">
        <h2>Clean slate, waiting.</h2>
        <p className="lede narrow">No sample data, no filler. The first game in there is yours.</p>
        <a className="btn btn-solid" href="#login">Create an account</a>
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
