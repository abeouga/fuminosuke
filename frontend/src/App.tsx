import { privateLocalStorage } from "./auth/transport";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { ToastViewport } from "./components/Toast";
import { HomePage } from "./pages/HomePage";
import { JobPage } from "./pages/JobPage";
import type { TemplateWorkspaceRequest } from "./pages/OcrPage";
import type { Health, Job, Template } from "./types";

const OcrPage = lazy(() => import("./pages/OcrPage").then(module => ({ default: module.OcrPage })));
const ReviewPage = lazy(() => import("./pages/ReviewPage").then(module => ({ default: module.ReviewPage })));

type Page = "home" | "ocr" | "review" | "job";
type NavigationGuard = "review" | "ocr" | "template";
type PendingNavigation = { page: Page; guard: NavigationGuard; resetOcrWorkspace?: boolean };
const RECENT_KEY = "document-ocr-recent-jobs";
const ATTENTION_STATUSES = new Set([
  "CREATED",
  "QUEUED",
  "PROCESSING",
  "REVIEW_REQUIRED",
  "FAILED",
  "INTERRUPTED",
]);

export default function App() {
  const [initialSearchParams] = useState(() => new URLSearchParams(window.location.search));
  const initialReviewJobId = initialSearchParams.get("job");
  const initialReviewField = initialSearchParams.get("field");
  const initialReviewEdit = initialSearchParams.get("edit") === "1";
  const [page, setPage] = useState<Page>(initialReviewJobId ? "review" : "home");
  const [health, setHealth] = useState<Health | null>(null);
  const [healthChecked, setHealthChecked] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templateLoadFailed, setTemplateLoadFailed] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [reviewJobId, setReviewJobId] = useState<string | null>(initialReviewJobId);
  const [reviewAutoReopen, setReviewAutoReopen] = useState(initialReviewEdit);
  const [reviewRequiresIdentityConfirmation, setReviewRequiresIdentityConfirmation] = useState(
    !initialReviewEdit,
  );
  const [monitoredJobId, setMonitoredJobId] = useState<string | null>(null);
  const [templateWorkspaceRequest, setTemplateWorkspaceRequest] = useState<TemplateWorkspaceRequest | null>(null);
  const [readingRootRequestId, setReadingRootRequestId] = useState(0);
  const [navigationGuard, setNavigationGuard] = useState<NavigationGuard | null>(
    initialReviewJobId ? "review" : null,
  );
  const [pendingNavigation, setPendingNavigation] = useState<PendingNavigation | null>(null);
  const templateRefreshRef = useRef<Promise<Template[]> | null>(null);
  const templateAutoRetryAttemptedRef = useRef(false);
  const templateWorkspaceRequestIdRef = useRef(0);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("edit")) {
      url.searchParams.delete("edit");
      window.history.replaceState({}, "", url);
    }
  }, []);
  const updateReviewGuard = useCallback((shouldWarn: boolean) => {
    setNavigationGuard((current) => shouldWarn ? "review" : current === "review" ? null : current);
  }, []);
  const updateOcrGuard = useCallback((shouldWarn: boolean) => {
    setNavigationGuard((current) => shouldWarn ? "ocr" : current === "ocr" ? null : current);
  }, []);
  const updateTemplateGuard = useCallback((shouldWarn: boolean) => {
    setNavigationGuard((current) => shouldWarn ? "template" : current === "template" ? null : current);
  }, []);
  const refreshTemplates = useCallback(() => {
    if (templateRefreshRef.current) return templateRefreshRef.current;
    setTemplatesLoading(true);
    const request = (async () => {
      const summaries = await api.templates();
      const details = await Promise.all(summaries.map((item) => api.template(item.id)));
      setTemplates(details);
      setTemplateLoadFailed(false);
      return details;
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
  const openTemplateWorkspace = useCallback((
    workspace: TemplateWorkspaceRequest["workspace"],
    editVersionId?: string,
  ) => {
    templateWorkspaceRequestIdRef.current += 1;
    setTemplateWorkspaceRequest({
      id: templateWorkspaceRequestIdRef.current,
      workspace,
      editVersionId,
    });
    setPage("ocr");
  }, []);
  const openReview = (id: string, requireIdentityConfirmation = true) => {
    const url = new URL(window.location.href);
    url.searchParams.set("job", id);
    url.searchParams.delete("field");
    url.searchParams.delete("edit");
    window.history.replaceState({}, "", url);
    setReviewJobId(id);
    setReviewAutoReopen(false);
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
      void refreshTemplates().catch(() => undefined).finally(() => openTemplateWorkspace("manage"));
      return;
    }
    setMonitoredJobId(job.id);
    setPage("job");
  };
  const applyNavigation = (nextPage: Page) => {
    if (nextPage === page) return;
    if (nextPage !== "review") {
      setReviewAutoReopen(false);
      const url = new URL(window.location.href);
      url.searchParams.delete("job");
      url.searchParams.delete("field");
      url.searchParams.delete("edit");
      window.history.replaceState({}, "", url);
    }
    setNavigationGuard(null);
    if (nextPage === "home") void refreshJobs();
    if (nextPage === "ocr") {
      setTemplateWorkspaceRequest(null);
    }
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
  const openReadingRoot = () => {
    if (page === "ocr") {
      if (navigationGuard) {
        setPendingNavigation({ page: "ocr", guard: navigationGuard, resetOcrWorkspace: true });
        return false;
      }
      setReadingRootRequestId((current) => current + 1);
      return true;
    }
    return navigateTo("ocr");
  };

  useEffect(() => {
    const beforeLogout = (event: Event) => { if (navigationGuard) event.preventDefault(); };
    window.addEventListener("fuminosuke-before-logout", beforeLogout);
    return () => window.removeEventListener("fuminosuke-before-logout", beforeLogout);
  }, [navigationGuard]);

  useEffect(() => {
    let stopped = false;
    const check = async () => {
      try {
        const next = await api.health();
        if (!stopped) setHealth((current) => sameHealth(current, next) ? current : next);
      }
      catch { if (!stopped) setHealth(null); }
      finally { if (!stopped) setHealthChecked(true); }
    };
    const poll = () => { if (!document.hidden) void check(); };
    const onVisibilityChange = () => { if (!document.hidden) void check(); };
    void check();
    const timer = window.setInterval(poll, 5000);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);
  const templateNames = useMemo(
    () => Object.fromEntries(templates.map(template => [template.id, template.name])),
    [templates],
  );
  useEffect(() => {
    retryTemplates();
    void refreshJobs();
  }, [refreshJobs, retryTemplates]);
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
  return (
    <div className="app-shell ux-shell deploy-shell">
      <aside className="side-nav">
        <button className="brand" onClick={() => navigateTo("home")} aria-label="ホーム">
          <span aria-hidden="true"><img src="/favicon.svg" alt="" /></span><div><strong>文之助</strong></div>
        </button>
        <span className="beta-badge">ベータ</span>
        <nav id="main-menu" aria-label="メインメニュー">
          <NavButton active={page === "home"} icon="home" label="ホーム" onClick={() => navigateTo("home")} />
          <NavButton active={page === "ocr" || page === "review"} icon="ocr" label="読み取り" onClick={openReadingRoot} />
        </nav>
        <div className="side-footer" role="status">
          <span className={health ? "status-dot ok" : "status-dot"} />
          <span>{health ? "接続中" : "接続待ち"}</span>
        </div>
      </aside>
      <main className="main-content">
        <Suspense fallback={<section className="panel" aria-busy="true"><p role="status">画面を準備しています。</p></section>}>
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
        {page === "home" && <HomePage health={health} healthChecked={healthChecked} jobs={jobs} onNavigate={(destination) => {
          if (destination === "templates") {
            openTemplateWorkspace("create");
            return;
          }
          navigateTo(destination);
        }} onOpenJob={openJob} />}
        {page === "ocr" && <OcrPage
          templates={templates}
          textRecognitionToggleEnabled={health?.ocrTextRecognitionToggleEnabled === true}
          rememberJob={rememberJob}
          openReview={openReview}
          refreshTemplates={refreshTemplates}
          templateWorkspaceRequest={templateWorkspaceRequest}
          onTemplateWorkspaceRequestHandled={() => setTemplateWorkspaceRequest(null)}
          readingRootRequestId={readingRootRequestId}
          onNavigationGuardChange={updateOcrGuard}
          onTemplateNavigationGuardChange={updateTemplateGuard}
        />}
        {page === "review" && reviewJobId && <ReviewPage key={reviewJobId} jobId={reviewJobId} templateNames={templateNames} initialSelectedPath={initialReviewJobId === reviewJobId ? initialReviewField : null} autoReopenConfirmed={reviewAutoReopen} requireIdentityConfirmation={reviewRequiresIdentityConfirmation} onNavigationGuardChange={updateReviewGuard} onClose={() => { void refreshJobs(); navigateTo("home"); }} />}
        {page === "job" && monitoredJobId && <JobPage
          jobId={monitoredJobId}
          onClose={() => { void refreshJobs(); navigateTo("home"); }}
          onOpenReview={openReview}
          onOpenTemplates={(workspace) => { retryTemplates(); openTemplateWorkspace(workspace); }}
          onStartNewReading={() => navigateTo("ocr")}
        />}
        </Suspense>
      </main>
      <ToastViewport />
      {pendingNavigation && (
        <NavigationConfirmDialog
          guard={pendingNavigation.guard}
          onCancel={() => setPendingNavigation(null)}
          onConfirm={() => {
            if (pendingNavigation.resetOcrWorkspace) {
              setPendingNavigation(null);
              setNavigationGuard(null);
              setReadingRootRequestId((current) => current + 1);
              return;
            }
            const destination = pendingNavigation.page;
            setPendingNavigation(null);
            applyNavigation(destination);
          }}
        />
      )}
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

function sameHealth(current: Health | null, next: Health): boolean {
  if (current === null) return false;
  const keys = Object.keys(next) as (keyof Health)[];
  return keys.length === Object.keys(current).length && keys.every((key) => current[key] === next[key]);
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
    const stored: unknown = JSON.parse(privateLocalStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(stored)
      ? stored.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    try {
      privateLocalStorage.removeItem(RECENT_KEY);
    } catch {
      // Browser storage can be disabled; the backend list still restores jobs.
    }
    return [];
  }
}

function writeRecentJobIds(ids: string[]) {
  try {
    privateLocalStorage.setItem(RECENT_KEY, JSON.stringify(ids));
  } catch {
    // The DB-backed job list remains authoritative if browser storage is unavailable.
  }
}

type SidebarIconName = "home" | "ocr";

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: SidebarIconName; label: string; onClick: () => void }) {
  return <button type="button" className={active ? "active" : ""} aria-current={active ? "page" : undefined} title={label} onClick={onClick}><span><SidebarIcon name={icon} /></span><span className="nav-label">{label}</span></button>;
}

function SidebarIcon({ name }: { name: SidebarIconName }) {
  const props = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "home") return <svg {...props}><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1Z" /></svg>;
  if (name === "ocr") return <svg {...props}><path d="M7 3.75h7l4 4v12.5H7z" /><path d="M14 3.75v4h4M10 12h5M10 15.5h5" /></svg>;
  return null;
}
