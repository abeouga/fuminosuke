import { privateLocalStorage } from "./auth/transport";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { ToastViewport, showToast } from "./components/Toast";
import { PendingJobs } from "./components/PendingJobs";
import { JobPage } from "./pages/JobPage";
const AskPage = lazy(() => import("./pages/AskPage").then(module => ({ default: module.AskPage })));
const ShelfPage = lazy(() => import("./pages/ShelfPage").then(module => ({ default: module.ShelfPage })));
import type { ShelfSelection } from "./pages/ShelfPage";
import { ItemJoinEditorHost } from "./components/TemplateItemJoinEditor";
import { DocumentNameEditorHost } from "./components/TemplateDocumentNameEditor";
import { TemplateMemoryHost } from "./components/TemplateMemoryDialog";
import type { TemplateWorkspaceRequest } from "./pages/OcrPage";
import type { Health, Job, Template } from "./types";
import { Spinner } from "./components/Spinner";
import { ReadingQueueMenu } from "./components/ReadingQueueMenu";
import { readingQueue, useReadingQueuePolling, type QueuedReading } from "./readingQueue";

const OcrPage = lazy(() => import("./pages/OcrPage").then(module => ({ default: module.OcrPage })));
const ReviewPage = lazy(() => import("./pages/ReviewPage").then(module => ({ default: module.ReviewPage })));

type Page = "ocr" | "review" | "job" | "ask" | "shelf";
type ReviewReturn = "ocr" | "ask" | "shelf";
const REVIEW_CLOSE_LABELS: Record<ReviewReturn, string> = { ocr: "読み取りへ戻る", ask: "チャットへ戻る", shelf: "書類棚へ戻る" };
type NavigationGuard = "review" | "ocr" | "template";
type PendingNavigation = { page: Page; guard: NavigationGuard; resetOcrWorkspace?: boolean; action?: () => void };
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
  const initialView = initialSearchParams.get("view");
  const [page, setPage] = useState<Page>(
    initialReviewJobId ? "review" : initialView === "ocr" ? "ocr" : initialView === "shelf" ? "shelf" : "ask",
  );
  const [health, setHealth] = useState<Health | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templateLoadFailed, setTemplateLoadFailed] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [reviewJobId, setReviewJobId] = useState<string | null>(initialReviewJobId);
  const [reviewField, setReviewField] = useState<string | null>(initialReviewField);
  // The page a review opened from (the question page and the shelf return there instead of the reading page).
  const [reviewReturn, setReviewReturn] = useState<ReviewReturn>("ocr");
  // The shelf's folder, document and search survive opening a review and a reload (`?view=shelf&doc=`).
  const [shelfSelection, setShelfSelection] = useState<ShelfSelection>(() => ({
    templateId: null,
    jobId: initialView === "shelf" ? initialSearchParams.get("doc") : null,
    query: "",
  }));
  const updateShelfSelection = useCallback((next: ShelfSelection) => {
    setShelfSelection(next);
    const url = new URL(window.location.href);
    if (next.jobId) url.searchParams.set("doc", next.jobId);
    else url.searchParams.delete("doc");
    window.history.replaceState({}, "", url);
  }, []);
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
  const openReview = (
    id: string,
    requireIdentityConfirmation = true,
    options: { returnTo?: ReviewReturn; field?: string | null } = {},
  ) => {
    const url = new URL(window.location.href);
    url.searchParams.set("job", id);
    if (options.field) url.searchParams.set("field", options.field);
    else url.searchParams.delete("field");
    url.searchParams.delete("edit");
    url.searchParams.delete("view");
    url.searchParams.delete("doc");
    window.history.replaceState({}, "", url);
    setReviewJobId(id);
    setReviewField(options.field ?? null);
    setReviewReturn(options.returnTo ?? "ocr");
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
  const openQueuedReading = (item: QueuedReading) => {
    if (page === "review" && reviewJobId === item.jobId) return;
    const open = () => {
      if (item.status === "REVIEW_REQUIRED" || item.status === "CONFIRMED") {
        openReview(item.jobId);
        return;
      }
      setNavigationGuard(null);
      setMonitoredJobId(item.jobId);
      setPage("job");
    };
    // The menu is reachable from every page: leaving unsaved work asks first, like the side menu.
    if (navigationGuard) {
      setPendingNavigation({ page: item.status === "REVIEW_REQUIRED" ? "review" : "job", guard: navigationGuard, action: open });
      return;
    }
    open();
  };
  useReadingQueuePolling();
  const applyNavigation = (nextPage: Page) => {
    if (nextPage === page) return;
    if (nextPage !== "review") {
      setReviewAutoReopen(false);
      const url = new URL(window.location.href);
      url.searchParams.delete("job");
      url.searchParams.delete("field");
      url.searchParams.delete("edit");
      // The chat is the landing page (no `view`); the reading page and the shelf are kept in the address so a reload comes back to them.
      if (nextPage === "ocr" || nextPage === "shelf") url.searchParams.set("view", nextPage);
      else url.searchParams.delete("view");
      if (nextPage === "shelf" && shelfSelection.jobId) url.searchParams.set("doc", shelfSelection.jobId);
      else url.searchParams.delete("doc");
      window.history.replaceState({}, "", url);
    }
    setNavigationGuard(null);
    if (nextPage === "ocr") {
      void refreshJobs();
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
        <button className="brand" onClick={() => navigateTo("ask")} aria-label="チャット">
          <span aria-hidden="true"><img src="/favicon.svg" alt="" /></span><div><strong>文之助</strong></div>
        </button>
        <span className="beta-badge">ベータ</span>
        <nav id="main-menu" aria-label="メインメニュー">
          <NavButton active={page === "ask" || (page === "review" && reviewReturn === "ask")} icon="ask" label="チャット" onClick={() => navigateTo("ask")} />
          <NavButton active={page === "ocr" || (page === "review" && reviewReturn === "ocr")} icon="ocr" label="読み取り" onClick={openReadingRoot} />
          <NavButton active={page === "shelf" || (page === "review" && reviewReturn === "shelf")} icon="shelf" label="書類棚" onClick={() => navigateTo("shelf")} />
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
              {templatesLoading ? <><Spinner />再取得中…</> : "テンプレートを再取得"}
            </button>
          </aside>
        )}
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
          templateSelectionFooter={<PendingJobs health={health} jobs={jobs} onOpenJob={openJob} />}
        />}
        {page === "review" && reviewJobId && <ReviewPage key={`${reviewJobId}:${reviewField ?? ""}`} jobId={reviewJobId} templateNames={templateNames} initialSelectedPath={reviewField} autoReopenConfirmed={reviewAutoReopen} requireIdentityConfirmation={reviewRequiresIdentityConfirmation} onNavigationGuardChange={updateReviewGuard} closeLabel={REVIEW_CLOSE_LABELS[reviewReturn]} onClose={() => { void refreshJobs(); navigateTo(reviewReturn); }}
          onConfirmed={({ templateId, documentName }) => {
            void refreshJobs();
            // A document from the reading queue: go on to the next one waiting for review.
            const queued = readingQueue.snapshot().some((item) => item.jobId === reviewJobId);
            if (queued) {
              readingQueue.setStatus(reviewJobId, "CONFIRMED");
              const next = readingQueue.nextReview(reviewJobId);
              const waiting = readingQueue.snapshot().filter((item) => item.status === "REVIEW_REQUIRED").length;
              const running = readingQueue.running().length;
              if (next) {
                openReview(next.jobId);
                showToast({ tone: "success", quiet: true, title: `「${documentName}」を確定しました`,
                  message: `次の書類「${next.fileName}」を開きました（確認待ち あと${waiting}件${running > 0 ? `・読み取り中 ${running}件` : ""}）。` });
                return;
              }
              if (running > 0) {
                applyNavigation("ocr");
                showToast({ tone: "success", quiet: true, title: `「${documentName}」を確定しました`,
                  message: `残り${running}件は読み取り中です。終わったものから確認できます。` });
                return;
              }
            }
            // A confirmed document goes on the shelf: show its template's documents (newest first).
            updateShelfSelection({ templateId: templateId || null, jobId: null, query: "" });
            applyNavigation("shelf");
            showToast({ tone: "success", quiet: true, title: `「${documentName}」を確定しました` });
          }} />}
        {page === "shelf" && <ShelfPage templateNames={templateNames} selection={shelfSelection} onSelectionChange={updateShelfSelection} onOpenReview={(id, field) => openReview(id, true, { returnTo: "shelf", field })} />}
        {page === "ask" && <AskPage onOpenReview={(id, field) => openReview(id, true, { returnTo: "ask", field })} />}
        {page === "job" && monitoredJobId && <JobPage
          jobId={monitoredJobId}
          onClose={() => { void refreshJobs(); navigateTo("ocr"); }}
          onOpenReview={openReview}
          onOpenTemplates={(workspace) => { retryTemplates(); openTemplateWorkspace(workspace); }}
          onStartNewReading={() => navigateTo("ocr")}
        />}
        </Suspense>
      </main>
      <ReadingQueueMenu onOpen={openQueuedReading} />
      <ToastViewport />
      <ItemJoinEditorHost />
      <DocumentNameEditorHost />
      <TemplateMemoryHost />
      {pendingNavigation && (
        <NavigationConfirmDialog
          guard={pendingNavigation.guard}
          onCancel={() => setPendingNavigation(null)}
          onConfirm={() => {
            if (pendingNavigation.action) {
              const action = pendingNavigation.action;
              setPendingNavigation(null);
              setNavigationGuard(null);
              action();
              return;
            }
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

type SidebarIconName = "ocr" | "ask" | "shelf";

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: SidebarIconName; label: string; onClick: () => void }) {
  return <button type="button" className={active ? "active" : ""} aria-current={active ? "page" : undefined} title={label} onClick={onClick}><span><SidebarIcon name={icon} /></span><span className="nav-label">{label}</span></button>;
}

function SidebarIcon({ name }: { name: SidebarIconName }) {
  const props = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "ocr") return <svg {...props}><path d="M7 3.75h7l4 4v12.5H7z" /><path d="M14 3.75v4h4M10 12h5M10 15.5h5" /></svg>;
  if (name === "shelf") return <svg {...props}><path d="M3.5 6.5a1 1 0 0 1 1-1h4.75l2 2h8.25a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z" /><path d="M3.5 10h17" /></svg>;
  if (name === "ask") return <svg {...props}><path d="M4 5.5h16v10H9l-4 3.5v-3.5H4z" /><path d="M9.5 9.25a2.5 2.5 0 1 1 3.25 2.4c-.5.2-.75.6-.75 1.1M12 14.6h.01" /></svg>;
  return null;
}
