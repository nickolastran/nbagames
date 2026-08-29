import { createClient, type Session } from "@supabase/supabase-js";
import { useEffect, useState } from "react";
import { Ball } from "./Landing";

const URL_ = import.meta.env.VITE_SUPABASE_URL;
const KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;

/** False until web/.env.local has the project URL + anon key. */
export const configured = Boolean(URL_ && KEY);
export const supabase = createClient(URL_ || "https://unconfigured.supabase.co", KEY || "anon");

export function useSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!configured) return setReady(true);
    void supabase.auth.getSession().then(({ data }) => { setSession(data.session); setReady(true); });
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);
  return { session, ready };
}

export const handleOf = (s: Session) =>
  (s.user.user_metadata?.handle as string | undefined) || s.user.email?.split("@")[0] || "Handy";

export function SignIn() {
  const [mode, setMode] = useState<"in" | "up">("in");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const email = String(f.get("email")), password = String(f.get("password"));
    setBusy(true); setErr(null); setMsg(null);
    const { data, error } =
      mode === "in"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({
            email, password,
            options: { data: { handle: String(f.get("handle") || "").trim().slice(0, 24) || email.split("@")[0] } },
          });
    setBusy(false);
    if (error) return setErr(error.message);
    if (mode === "up" && !data.session) setMsg("Check your email to confirm the account, then sign in.");
  };

  return (
    <div className="wrap gate">
      <header className="masthead">
        <a className="logo" href="#"><Ball /><span>Game<b>Tracker</b></span></a>
        <a className="btn" href="#">&larr; Back</a>
      </header>
      <form className="signin" onSubmit={(e) => void submit(e)}>
        <h2>{mode === "in" ? "Sign in" : "Create an account"}</h2>
        <p className="sub">Your games are stored against your account, so they follow you to any browser.</p>
        {mode === "up" && (
          <label>Display name<input name="handle" autoComplete="nickname" maxLength={24} placeholder="Handy" /></label>
        )}
        <label>Email<input name="email" type="email" autoComplete="email" required /></label>
        <label>Password
          <input name="password" type="password" minLength={6} required
            autoComplete={mode === "in" ? "current-password" : "new-password"} />
        </label>
        <button className="btn btn-solid" type="submit" disabled={busy}>
          {busy ? "…" : mode === "in" ? "Sign in" : "Sign up"}
        </button>
        {err && <p className="error">{err}</p>}
        {msg && <p className="notice">{msg}</p>}
        <button className="linkish" type="button"
          onClick={() => { setMode(mode === "in" ? "up" : "in"); setErr(null); setMsg(null); }}>
          {mode === "in" ? "No account yet? Sign up" : "Already have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
