import { googleLoginUrl } from "../api";
import type { AuthSession } from "../types";

export function AuthGate({
  session,
  loading,
  error,
  onRetry,
  reconnectRequired,
  onReconnect,
}: {
  session: AuthSession | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  reconnectRequired: boolean;
  onReconnect: () => void;
}) {
  const configured = session?.configured ?? false;
  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-mark" aria-hidden="true">牛</div>
        <p className="eyebrow">GOOGLE SHEETS CONNECTION</p>
        <h1 id="auth-title">
          {reconnectRequired ? "Google連携の更新が必要です" : "Googleアカウントで接続"}
        </h1>
        <p className="auth-description">
          {reconnectRequired
            ? "保存先をGoogle Pickerで選択する権限方式へ切り替えます。旧連携を解除して再認証してください。"
            : "読み取り結果をあなたのGoogleスプレッドシートへ直接出力するため、Googleアカウントの認証が必要です。"}
        </p>
        {loading && <p className="auth-status" role="status">認証状態を確認しています…</p>}
        {!loading && error && (
          <div className="error-box auth-message" role="alert">
            <span>{error}</span>
            <button type="button" className="secondary compact" onClick={onRetry}>再試行</button>
          </div>
        )}
        {!loading && !error && !configured && (
          <div className="notice auth-message" role="status">
            <strong>管理者のOAuth設定が必要です</strong>
            <span>
              バックエンドの GOOGLE_OAUTH_CLIENT_ID、GOOGLE_OAUTH_CLIENT_SECRET、
              AUTH_JWT_SECRET を設定して再起動してください。
            </span>
          </div>
        )}
        {!loading && !error && configured && reconnectRequired && (
          <button
            type="button"
            className="google-login-button"
            onClick={onReconnect}
          >
            <span aria-hidden="true">G</span>
            Google連携を更新
          </button>
        )}
        {!loading && !error && configured && !reconnectRequired && (
          <button
            type="button"
            className="google-login-button"
            onClick={() => window.location.assign(googleLoginUrl(window.location.href))}
          >
            <span aria-hidden="true">G</span>
            Googleでログイン
          </button>
        )}
        <p className="auth-security-note">
          ログイン状態はHttpOnly Cookieで保持し、Googleの更新トークンはローカルDBへ暗号化して保存します。
        </p>
      </section>
    </main>
  );
}
