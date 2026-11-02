import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { FiLogOut } from "react-icons/fi";
import { authenticatedFetch, endBrowserSession, loadSession, signIn } from "./transport";
import type { AuthSession } from "./transport";
import { Spinner } from "../components/Spinner";
import { useServiceHoursNotice } from "../serviceHours";
import type { ServiceHoursNotice } from "../serviceHours";

type Account = { logout: () => void; loggingOut: boolean };
const AccountContext = createContext<Account | null>(null);

/** Logout for the signed-in Cognito user; renders nothing in the local (no login) mode. */
export function LogoutButton({ className = "" }: { className?: string }) {
  const account = useContext(AccountContext);
  if (!account) return null;
  return <button type="button" className={`auth-logout-button ${className}`} disabled={account.loggingOut}
    onClick={account.logout} aria-label="ログアウト" title="ログアウト">
    {account.loggingOut ? <Spinner /> : <FiLogOut aria-hidden="true" />}
    <span>ログアウト</span>
  </button>;
}

/** Shown instead of the login when the server is stopped for the night (or still starting in the morning). */
function ServiceHoursPage({ notice }: { notice: ServiceHoursNotice }) {
  return <main className="login-page">
    <section className="login-card service-hours-card" role="status" aria-labelledby="service-hours-title">
      <img className="login-mark" src="/favicon.svg" alt="" />
      <h1>文之助</h1>
      <strong id="service-hours-title">{notice.title}</strong>
      <p>{notice.detail}</p>
    </section>
  </main>;
}

function LoginPage({ expired, notice, onSignedIn }: {
  expired: boolean; notice: string; onSignedIn: (session: AuthSession) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !username.trim() || !password) return;
    setBusy(true); setError("");
    try {
      onSignedIn(await signIn(username.trim(), password));
      setPassword("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "ログインできませんでした。");
    } finally { setBusy(false); }
  }
  return <main className="login-page">
    <form className="login-card" onSubmit={event => void submit(event)} aria-labelledby="login-title">
      <img className="login-mark" src="/favicon.svg" alt="" />
      <h1 id="login-title">文之助</h1>
      {(expired || notice) && <p className="login-note" role="status">
        {expired ? "ログインの期限が切れました。もう一度ログインすると、この画面の続きから使えます。" : notice}
      </p>}
      <label className="login-field">
        <span>ユーザー名</span>
        <input autoComplete="username" autoCapitalize="none" spellCheck={false} value={username}
          onChange={event => setUsername(event.target.value)} autoFocus disabled={busy} />
      </label>
      <label className="login-field">
        <span>パスワード</span>
        <input type="password" autoComplete="current-password" value={password}
          onChange={event => setPassword(event.target.value)} disabled={busy} />
      </label>
      {error && <p className="login-error" role="alert">{error}</p>}
      <button type="submit" className="primary login-submit" disabled={busy || !username.trim() || !password}>
        {busy ? <><Spinner />ログイン中…</> : "ログイン"}
      </button>
    </form>
  </main>;
}

const MODE_KEY = "fuminosuke.authMode";
const UNREACHABLE = "サーバーに接続できませんでした。少し待ってから、もう一度ログインしてください。";

/** Whether this address uses sign-in, from the last answered check (the server may not answer now). */
function signInExpected(): boolean {
  try {
    const known = window.localStorage.getItem(MODE_KEY);
    if (known) return known === "cognito";
  } catch { /* storage may be off */ }
  return !/^(localhost|127\.0\.0\.1|\[::1\]|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(window.location.hostname);
}

export function AuthBoundary({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  // The session an API call just refused: while the check still calls it signed in, ask for a new login
  // instead of showing the workspace again (that only flips back and forth between the two screens).
  const refused = useRef<string | null>(null);
  const sessionRef = useRef<AuthSession | null>(null);
  sessionRef.current = session;
  const [message, setMessage] = useState("");
  // Why the last session check failed (the server is unreachable, e.g. stopped for the night); cleared once it answers.
  const [checkError, setCheckError] = useState("");
  const [ready, setReady] = useState(false);
  const [confirmLogout, setConfirmLogout] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true); setMessage("");
    try {
      const response = await authenticatedFetch("/api/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("ログアウトできませんでした。");
      setSession(endBrowserSession()); setReady(false); setConfirmLogout(false);
      setMessage("ログアウトしました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "ログアウトできませんでした。");
    } finally { setLoggingOut(false); }
  }
  function requestLogout() {
    const event = new Event("fuminosuke-before-logout", { cancelable: true });
    if (!window.dispatchEvent(event)) setConfirmLogout(true);
    else void logout();
  }
  useEffect(() => {
    let active = true;
    const refresh = () => { void loadSession().then(value => {
      if (!active) return;
      try { window.localStorage.setItem(MODE_KEY, value.mode); } catch { /* storage may be off */ }
      setCheckError("");
      if (value.authenticated && refused.current !== null && value.csrfToken === refused.current) {
        setSession({ ...value, authenticated: false });
        return;
      }
      refused.current = null;
      setSession(value);
      if (value.authenticated) { setReady(true); setMessage(""); }
    }).catch(() => { if (active) setCheckError(UNREACHABLE); }); };
    const expired = () => {
      const previous = sessionRef.current;
      if (previous?.authenticated) refused.current = previous.csrfToken ?? "";
      setSession(value => value ? { ...value, authenticated: false } : null);
    };
    refresh();
    const timer = window.setInterval(refresh, 15_000);
    window.addEventListener("focus", refresh);
    window.addEventListener("fuminosuke-auth-required", expired);
    return () => {
      active = false; window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("fuminosuke-auth-required", expired);
    };
  }, []);
  const signedIn = session?.authenticated === true;
  // Only when the server does not answer: at night it may still be up (e.g. kept running for work), and then login works.
  const scheduleNotice = useServiceHoursNotice(!checkError);
  const hoursNotice = checkError ? scheduleNotice : null;
  const account = signedIn && session.mode === "cognito" ? { logout: requestLogout, loggingOut } : null;
  return <AccountContext.Provider value={account}>
    {!session && !checkError && <main className="auth-screen auth-checking" aria-busy="true">
      <p><Spinner />ログイン中</p>
    </main>}
    {!signedIn && hoursNotice && <ServiceHoursPage notice={hoursNotice} />}
    {!session && checkError && !hoursNotice && !signInExpected() && <main className="auth-screen">
      <p role="alert">サーバーに接続できません。起動しているか確かめてください。</p>
    </main>}
    {!signedIn && !hoursNotice && (session ? session.mode === "cognito" : Boolean(checkError) && signInExpected())
      && <LoginPage expired={ready && !checkError} notice={checkError || message}
        onSignedIn={value => { refused.current = null; setCheckError(""); setSession(value); setReady(true); setMessage(""); }} />}
    {/* Kept mounted while signed out after expiry, so unsaved input survives the re-login. */}
    {ready && <div hidden={!signedIn}>{children}</div>}
    {confirmLogout && signedIn && <div className="navigation-confirm-backdrop">
      <section className="navigation-confirm-dialog auth-logout-dialog" role="dialog" aria-modal="true" aria-labelledby="logout-title">
        <h2 id="logout-title">編集内容を確認してください</h2>
        <p>未保存の入力、または確認が必要な処理があります。ログアウトすると、このタブの編集画面を閉じます。</p>
        {message && <p role="alert">{message}</p>}
        <div className="navigation-confirm-actions">
          <button className="secondary" disabled={loggingOut} onClick={() => setConfirmLogout(false)} autoFocus>戻る</button>
          <button className="secondary" disabled={loggingOut} onClick={() => void logout()}>ログアウトする</button>
        </div>
      </section>
    </div>}
  </AccountContext.Provider>;
}
