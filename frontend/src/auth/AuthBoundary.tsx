import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { authenticatedFetch, endBrowserSession, loadSession } from "./transport";
import type { AuthSession } from "./transport";
import { Spinner } from "../components/Spinner";

export function AuthBoundary({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [message, setMessage] = useState("");
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
  useEffect(() => {
    let active = true;
    const refresh = () => { void loadSession().then(value => {
      if (active) { setSession(value); setMessage(""); if (value.authenticated) setReady(true); }
    }).catch(error => { if (active) setMessage(String(error.message)); }); };
    const expired = () => { setSession(previous => previous ? { ...previous, authenticated: false } : null); };
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
  return <>
    {!session?.authenticated && <main className="auth-screen">
    <h1>文之助 ベータ</h1>
    <p>{message || (ready
      ? "ログイン期限が切れました。未保存の入力はこのタブに保持しています。別タブで再ログインし、このタブに戻ってください。"
      : session ? "招待されたアカウントでログインしてください。" : "ログイン状態を確認しています。")}</p>
    {session?.mode === "cognito" && <a className="button" href="/api/auth/login"
      target={ready ? "_blank" : undefined} rel="noopener">
      {ready ? "別タブで再ログイン" : "ログイン"}
    </a>}
    </main>}
    {session?.authenticated && session.mode === "cognito" && <div className="auth-account-bar">
      <span>文之助 ベータ</span>
      <small>バックアップの復元対象は7日間です。削除の反映に時間がかかる場合があります。</small>
      <button type="button" disabled={loggingOut} onClick={() => {
        const event = new Event("fuminosuke-before-logout", { cancelable: true });
        if (!window.dispatchEvent(event)) setConfirmLogout(true);
        else void logout();
      }}>{loggingOut ? <><Spinner />ログアウト中…</> : "ログアウト"}</button>
      {message && <span role="alert">{message}</span>}
    </div>}
    {ready && <div hidden={!session?.authenticated}>{children}</div>}
    {confirmLogout && session?.authenticated && <div className="navigation-confirm-backdrop">
      <section className="navigation-confirm-dialog auth-logout-dialog" role="dialog" aria-modal="true" aria-labelledby="logout-title">
        <h2 id="logout-title">編集内容を確認してください</h2>
        <p>未保存の入力、または確認が必要な処理があります。ログアウトすると、このタブの編集画面を閉じます。</p>
        {message && <p role="alert">{message}</p>}
        <div className="navigation-confirm-actions">
          <button className="secondary" disabled={loggingOut} onClick={() => setConfirmLogout(false)} autoFocus>編集画面に戻る</button>
          <button className="secondary" disabled={loggingOut} onClick={() => void logout()}>ログアウトする</button>
        </div>
      </section>
    </div>}
  </>;
}
