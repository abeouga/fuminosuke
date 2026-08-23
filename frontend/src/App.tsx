import { useCallback, useEffect, useRef, useState } from "react";
import { api, googleLoginUrl } from "./api";
import { AuthGate } from "./components/AuthGate";
import { ToastViewport } from "./components/Toast";
import { DevelopmentPage } from "./pages/DevelopmentPage";
import { HomePage } from "./pages/HomePage";
import { IndividualRecordsPage } from "./pages/IndividualRecordsPage";
import { DocumentLibraryPage } from "./pages/DocumentLibraryPage";
import { DriveSettingsPage } from "./pages/DriveSettingsPage";
import { JobPage } from "./pages/JobPage";
import { OcrPage } from "./pages/OcrPage";
import { ReviewPage } from "./pages/ReviewPage";
import { TemplatePage } from "./pages/TemplatePage";
import type { AuthSession, Health, Job, OcrLibraryContext, Template } from "./types";

type Page = "home" | "templates" | "ocr" | "review" | "job" | "records" | "library" | "drive" | "development";
type NavigationGuard = "review" | "ocr" | "template";
type PendingNavigation = { page: Page; guard: NavigationGuard };
const RECENT_KEY = "ledger-ocr-recent-jobs";
const ATTENTION_STATUSES = new Set([
  "CREATED",
  "QUEUED",
  "PROCESSING",
  "REVIEW_REQUIRED",
  "FAILED",
  "INTERRUPTED",
]);

export default function App() {
  const initialSearchParams = new URLSearchParams(window.location.search);
  const initialReviewJobId = initialSearchParams.get("job");
  const initialReviewField = initialSearchParams.get("field");
  const initialReviewEdit = initialSearchParams.get("edit") === "1";
  const initialOAuthError = oauthErrorMessage(initialSearchParams.get("authError"));
  const [page, setPage] = useState<Page>(initialReviewJobId ? "review" : "home");
  const [health, setHealth] = useState<Health | null>(null);
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [oauthCallbackError, setOAuthCallbackError] = useState<string | null>(initialOAuthError);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templateLoadFailed, setTemplateLoadFailed] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [reviewJobId, setReviewJobId] = useState<string | null>(initialReviewJobId);
  const [reviewRequiresIdentityConfirmation, setReviewRequiresIdentityConfirmation] = useState(
    !initialReviewEdit,
  );
  const [monitoredJobId, setMonitoredJobId] = useState<string | null>(null);
  const [ocrLibraryContext, setOcrLibraryContext] = useState<OcrLibraryContext | null>(null);
  const [libraryDocumentId, setLibraryDocumentId] = useState<string | null>(null);
  const [navigationGuard, setNavigationGuard] = useState<NavigationGuard | null>(
    initialReviewJobId ? "review" : null,
  );
  const [pendingNavigation, setPendingNavigation] = useState<PendingNavigation | null>(null);
  const [disconnectPending, setDisconnectPending] = useState(false);
  const templateRefreshRef = useRef<Promise<void> | null>(null);
  const templateAutoRetryAttemptedRef = useRef(false);
  const updateReviewGuard = useCallback((shouldWarn: boolean) => {
    setNavigationGuard((current) => shouldWarn ? "review" : current === "review" ? null : current);
  }, []);
  const updateOcrGuard = useCallback((shouldWarn: boolean) => {
    setNavigationGuard((current) => shouldWarn ? "ocr" : current === "ocr" ? null : current);
  }, []);
  const updateTemplateGuard = useCallback((shouldWarn: boolean) => {
    setNavigationGuard((current) => shouldWarn ? "template" : current === "template" ? null : current);
  }, []);
  const refreshAuth = useCallback(async () => {
    setAuthLoading(true);
    try {
      const session = await api.authSession();
      setAuth(session);
      setAuthError(null);
    } catch (error) {
      setAuth(null);
      setAuthError(error instanceof Error ? error.message : "認証状態を確認できませんでした。");
    } finally {
      setAuthLoading(false);
    }
  }, []);

  const refreshTemplates = useCallback(() => {
    if (templateRefreshRef.current) return templateRefreshRef.current;
    setTemplatesLoading(true);
    const request = (async () => {
      const summaries = await api.templates();
      const details = await Promise.all(summaries.map((item) => api.template(item.id)));
      setTemplates(details);
      setTemplateLoadFailed(false);
    })()
      .catch((error) => {
        setTemplateLoadFailed(true);
        throw error;
      })
      .finally(() => {
        templateRefreshRef.current = null;
        setTemplatesLoading(false);
      });
    templateRefreshRef.current = request;
    return request;
  }, []);
  const retryTemplates = useCallback(() => {
    void refreshTemplates().catch(() => undefined);
  }, [refreshTemplates]);
  const refreshJobs = useCallback(async () => {
    try {
      const [storedResponse, attentionResponse] = await Promise.allSettled([
        api.jobs(100),
        api.jobs(1000, { attentionOnly: true }),
      ]);
      if (storedResponse.status === "rejected") throw storedResponse.reason;
      const stored = storedResponse.value;
      const attention = attentionResponse.status === "fulfilled"
        ? attentionResponse.value
        : stored.filter((job) => ATTENTION_STATUSES.has(job.status));
      setJobs(visibleJobs(stored, attention));
      writeRecentJobIds(stored.map((job) => job.id));
    } catch {
      const ids = readRecentJobIds();
      const loaded = await Promise.all(ids.slice(0, 12).map((id) => api.job(id).catch(() => null)));
      setJobs(visibleJobs(loaded.filter((item): item is Job => item !== null)));
    }
  }, []);
  const rememberJob = useCallback((id: string) => {
    const current = readRecentJobIds();
    writeRecentJobIds([id, ...current.filter((item) => item !== id)].slice(0, 20));
    void refreshJobs();
  }, [refreshJobs]);
  const openReview = (id: string, requireIdentityConfirmation = true) => {
    const url = new URL(window.location.href);
    url.searchParams.set("job", id);
    url.searchParams.delete("field");
    window.history.replaceState({}, "", url);
    setReviewJobId(id);
    setReviewRequiresIdentityConfirmation(requireIdentityConfirmation);
    setNavigationGuard("review");
    setPage("review");
  };
  const openJob = (job: Job) => {
    if (
      job.jobType === "EXTRACT_DOCUMENT"
      && ["REVIEW_REQUIRED", "CONFIRMED"].includes(job.status)
    ) {
      openReview(job.id);
      return;
    }
    if (job.jobType === "PREPARE_TEMPLATE" && job.status === "REVIEW_REQUIRED") {
      void refreshTemplates().catch(() => undefined).finally(() => setPage("templates"));
      return;
    }
    setMonitoredJobId(job.id);
    setPage("job");
  };
  const applyNavigation = (nextPage: Page) => {
    if (nextPage === page) return;
    if (nextPage !== "review") {
      const url = new URL(window.location.href);
      url.searchParams.delete("job");
      url.searchParams.delete("field");
      window.history.replaceState({}, "", url);
    }
    setNavigationGuard(null);
    if (nextPage === "ocr") setOcrLibraryContext(null);
    setPage(nextPage);
  };
  const navigateTo = (nextPage: Page) => {
    if (nextPage === page) return true;
    if (navigationGuard) {
      setPendingNavigation({ page: nextPage, guard: navigationGuard });
      return false;
    }
    applyNavigation(nextPage);
    return true;
  };

  useEffect(() => {
    let stopped = false;
    const check = async () => {
      try { const next = await api.health(); if (!stopped) setHealth(next); }
      catch { if (!stopped) setHealth(null); }
    };
    void check();
    const timer = window.setInterval(check, 5000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    void refreshAuth();
    const requireAuth = () => { void refreshAuth(); };
    window.addEventListener("ledger-auth-required", requireAuth);
    return () => window.removeEventListener("ledger-auth-required", requireAuth);
  }, [refreshAuth]);
  useEffect(() => {
    if (!initialSearchParams.has("authError")) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("authError");
    window.history.replaceState({}, "", url);
  }, []);
  const authorizationCurrent = auth?.authorizationCurrent !== false;
  const authReady = auth !== null && (
    !auth.required || (auth.authenticated && authorizationCurrent)
  );
  useEffect(() => {
    if (!authReady) return;
    retryTemplates();
    void refreshJobs();
  }, [authReady, refreshJobs, retryTemplates]);
  const backendHealthy = health !== null;
  useEffect(() => {
    if (!templateLoadFailed) {
      templateAutoRetryAttemptedRef.current = false;
      return;
    }
    if (!backendHealthy) {
      templateAutoRetryAttemptedRef.current = false;
      return;
    }
    if (templatesLoading || templateAutoRetryAttemptedRef.current) return;
    templateAutoRetryAttemptedRef.current = true;
    retryTemplates();
  }, [backendHealthy, retryTemplates, templateLoadFailed, templatesLoading]);
  const displayedAuthError = oauthCallbackError ?? authError;
  const reconnectRequired = Boolean(
    auth?.required && auth.authenticated && auth.authorizationCurrent === false,
  );
  if (
    authLoading
    || displayedAuthError
    || (auth?.required && (!auth.authenticated || !authorizationCurrent))
  ) {
    return (
      <AuthGate
        session={auth}
        loading={authLoading}
        error={displayedAuthError}
        reconnectRequired={reconnectRequired}
        onRetry={() => {
          setOAuthCallbackError(null);
          void refreshAuth();
        }}
        onReconnect={() => {
          setAuthLoading(true);
          void api.disconnectGoogle()
            .then(() => window.location.assign(googleLoginUrl(window.location.href)))
            .catch((error: unknown) => {
              setAuthError(
                error instanceof Error ? error.message : "Google連携を更新できませんでした。",
              );
              setAuthLoading(false);
            });
        }}
      />
    );
  }
  return (
    <div className="app-shell">
      <aside className="side-nav">
        <button className="brand" onClick={() => navigateTo("home")} aria-label="ホーム">
          <span>V2</span><div><strong>帳票 OCR V2</strong><small>FORM DEFINITION TOOL</small></div>
        </button>
        <nav aria-label="メインメニュー">
          <NavButton active={page === "home"} icon="home" label="ホーム" onClick={() => navigateTo("home")} />
          <NavButton active={page === "templates"} icon="templates" label="テンプレート" onClick={() => navigateTo("templates")} />
          <NavButton active={page === "ocr" || page === "review"} icon="ocr" label="読み取り" onClick={() => navigateTo("ocr")} />
          <NavButton active={page === "library"} icon="library" label="書類ライブラリ" onClick={() => { setLibraryDocumentId(null); navigateTo("library"); }} />
          <NavButton active={page === "records"} icon="search" label="データ検索" onClick={() => navigateTo("records")} />
          <NavButton active={page === "drive"} icon="drive" label="保存先設定" onClick={() => navigateTo("drive")} />
          <NavButton active={page === "development"} icon="development" label="開発情報" onClick={() => navigateTo("development")} />
        </nav>
        <div className="side-footer">
          {auth?.user?.pictureUrl
            ? <img className="auth-avatar" src={auth.user.pictureUrl} alt="" referrerPolicy="no-referrer" />
            : <span className={health ? "status-dot ok" : "status-dot"} />}
          <div>
            <strong>{auth?.user?.displayName ?? (health ? "ローカル接続中" : "接続待ち")}</strong>
            <small>{auth?.user?.email ?? "127.0.0.1"}</small>
          </div>
          {auth?.required && (
            <div className="auth-session-actions">
              <button
                type="button"
                className="auth-logout"
                onClick={() => {
                  void api.logout().finally(() => { void refreshAuth(); });
                }}
              >
                ログアウト
              </button>
              <button
                type="button"
                className="auth-disconnect"
                onClick={() => setDisconnectPending(true)}
              >
                連携解除
              </button>
            </div>
          )}
        </div>
      </aside>
      <main className="main-content">
        {templateLoadFailed && (
          <aside className="notice danger persistent-system-notice" role="alert">
            <div>
              <strong>テンプレートを取得できませんでした。</strong>
              <span>接続が戻ると自動で再取得します。必要な場合は手動で再取得できます。</span>
            </div>
            <button
              type="button"
              className="secondary compact"
              disabled={templatesLoading}
              onClick={retryTemplates}
            >
              {templatesLoading ? "再取得中…" : "テンプレートを再取得"}
            </button>
          </aside>
        )}
        {page === "home" && <HomePage health={health} templates={templates} jobs={jobs} onNavigate={navigateTo} onOpenJob={openJob} />}
        {page === "templates" && <TemplatePage templates={templates} refresh={refreshTemplates} rememberJob={rememberJob} onNavigationGuardChange={updateTemplateGuard} />}
        {page === "ocr" && <OcrPage templates={templates} libraryContext={ocrLibraryContext} rememberJob={rememberJob} openReview={openReview} openTemplates={() => navigateTo("templates")} onNavigationGuardChange={updateOcrGuard} />}
        {page === "review" && reviewJobId && <ReviewPage jobId={reviewJobId} initialSelectedPath={initialReviewField} autoReopenConfirmed={initialReviewEdit} requireIdentityConfirmation={reviewRequiresIdentityConfirmation} onNavigationGuardChange={updateReviewGuard} onClose={() => { void refreshJobs(); navigateTo("home"); }} />}
        {page === "job" && monitoredJobId && <JobPage
          jobId={monitoredJobId}
          onClose={() => { void refreshJobs(); navigateTo("home"); }}
          onOpenReview={openReview}
          onOpenTemplates={() => { retryTemplates(); navigateTo("templates"); }}
          onStartNewReading={() => navigateTo("ocr")}
        />}
        {page === "records" && <IndividualRecordsPage
          onEditResult={(id) => openReview(id, false)}
          onOpenDocument={(documentId) => {
            setLibraryDocumentId(documentId);
            setPage("library");
          }}
        />}
        {page === "library" && <DocumentLibraryPage initialDocumentId={libraryDocumentId} openReview={(id) => openReview(id, false)} startReading={(context) => { setOcrLibraryContext(context); setPage("ocr"); }} />}
        {page === "drive" && <DriveSettingsPage />}
        {page === "development" && <DevelopmentPage />}
      </main>
      <ToastViewport />
      {pendingNavigation && (
        <NavigationConfirmDialog
          guard={pendingNavigation.guard}
          onCancel={() => setPendingNavigation(null)}
          onConfirm={() => {
            const destination = pendingNavigation.page;
            setPendingNavigation(null);
            applyNavigation(destination);
          }}
        />
      )}
      {disconnectPending && (
        <AuthDisconnectDialog
          onCancel={() => setDisconnectPending(false)}
          onConfirm={() => {
            setDisconnectPending(false);
            void api.disconnectGoogle().finally(() => { void refreshAuth(); });
          }}
        />
      )}
    </div>
  );
}

function AuthDisconnectDialog({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="navigation-confirm-backdrop" role="presentation">
      <section
        className="navigation-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-disconnect-title"
      >
        <span className="navigation-confirm-icon" aria-hidden="true">G</span>
        <div>
          <p className="eyebrow">GOOGLE CONNECTION</p>
          <h2 id="auth-disconnect-title">Google連携を解除しますか？</h2>
          <p>保存済みのGoogleトークンを削除します。再利用にはGoogle認証が必要です。</p>
        </div>
        <div className="navigation-confirm-actions">
          <button type="button" className="secondary" onClick={onCancel}>キャンセル</button>
          <button type="button" className="danger-button" onClick={onConfirm}>連携を解除</button>
        </div>
      </section>
    </div>
  );
}

function NavigationConfirmDialog({
  guard,
  onCancel,
  onConfirm,
}: {
  guard: NavigationGuard;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const cancelOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", cancelOnEscape);
    return () => window.removeEventListener("keydown", cancelOnEscape);
  }, [onCancel]);
  const message = guard === "review"
    ? "この読み取り結果はまだ確定保存されていません。"
    : guard === "ocr"
      ? "読み取りの入力途中です。入力内容は保存されていません。"
      : "テンプレートの変更が未保存、または処理中です。";
  return (
    <div
      className="navigation-confirm-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <section
        className="navigation-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="navigation-confirm-title"
        aria-describedby="navigation-confirm-description"
      >
        <span className="navigation-confirm-icon" aria-hidden="true">!</span>
        <div>
          <span className="eyebrow">UNSAVED CHANGES</span>
          <h2 id="navigation-confirm-title">未保存の変更があります</h2>
          <p id="navigation-confirm-description">{message}<br />変更を破棄して別の画面へ移動しますか？</p>
        </div>
        <div className="navigation-confirm-actions">
          <button type="button" className="secondary" autoFocus onClick={onCancel}>この画面に残る</button>
          <button type="button" className="danger-button" onClick={onConfirm}>変更を破棄して移動</button>
        </div>
      </section>
    </div>
  );
}

function visibleJobs(allJobs: Job[], allAttention?: Job[]): Job[] {
  const recent = allJobs.slice(0, 20);
  const attention = (allAttention ?? allJobs)
    .filter((job) => ATTENTION_STATUSES.has(job.status));
  const seen = new Set<string>();
  return [...attention, ...recent].filter((job) => {
    if (seen.has(job.id)) return false;
    seen.add(job.id);
    return true;
  });
}

function readRecentJobIds(): string[] {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(stored)
      ? stored.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    try {
      localStorage.removeItem(RECENT_KEY);
    } catch {
      // Browser storage can be disabled; the backend list still restores jobs.
    }
    return [];
  }
}

function writeRecentJobIds(ids: string[]) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(ids));
  } catch {
    // The DB-backed job list remains authoritative if browser storage is unavailable.
  }
}

function oauthErrorMessage(code: string | null): string | null {
  if (!code) return null;
  if (code === "GOOGLE_OAUTH_INVALID_CLIENT") {
    return "Google OAuthのクライアント設定が一致しません。管理者が設定を確認してください。";
  }
  if (code === "GOOGLE_OAUTH_INVALID_GRANT") {
    return "Google認証コードが期限切れ、再使用済み、またはリダイレクトURIと一致しません。認証を最初からやり直してください。";
  }
  if (code === "GOOGLE_OAUTH_CANCELLED") {
    return "Googleアカウントの認証は完了していません。";
  }
  return "Googleアカウントの認証に失敗しました。認証を最初からやり直してください。";
}

type SidebarIconName = "home" | "templates" | "ocr" | "library" | "search" | "drive" | "development";

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: SidebarIconName; label: string; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick}><span><SidebarIcon name={icon} /></span>{label}</button>;
}

function SidebarIcon({ name }: { name: SidebarIconName }) {
  const props = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "home") return <svg {...props}><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1Z" /></svg>;
  if (name === "templates") return <svg {...props}><path d="m12 3 8 4.5-8 4.5-8-4.5Z" /><path d="m4 12 8 4.5 8-4.5" /><path d="m4 16.5 8 4.5 8-4.5" /></svg>;
  if (name === "ocr") return <svg {...props}><rect x="3" y="7" width="18" height="12" rx="2" /><path d="M8 7 9.5 4h5L16 7M12 10v6m-3-3h6" /></svg>;
  if (name === "library") return <svg {...props}><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17.5H6.5A2.5 2.5 0 0 0 4 22Z" /><path d="M4 4.5V22m4-15h8m-8 4h8" /></svg>;
  if (name === "search") return <svg {...props}><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></svg>;
  if (name === "drive") return <svg {...props}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M7 15h.01M11 15h6" /></svg>;
  return <svg {...props}><path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 4l-4 16" /></svg>;
}
