import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { HomePage } from "./pages/HomePage";
import { IndividualRecordsPage } from "./pages/IndividualRecordsPage";
import { JobPage } from "./pages/JobPage";
import { OcrPage } from "./pages/OcrPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SireNamesPage } from "./pages/SireNamesPage";
import { TemplatePage } from "./pages/TemplatePage";
import type { Health, Job, Template } from "./types";

type Page = "home" | "templates" | "ocr" | "review" | "job" | "records" | "sireNames";
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
  const initialReviewJobId = new URLSearchParams(window.location.search).get("job");
  const [page, setPage] = useState<Page>(initialReviewJobId ? "review" : "home");
  const [health, setHealth] = useState<Health | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templateLoadFailed, setTemplateLoadFailed] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [reviewJobId, setReviewJobId] = useState<string | null>(initialReviewJobId);
  const [monitoredJobId, setMonitoredJobId] = useState<string | null>(null);
  const [navigationGuard, setNavigationGuard] = useState<NavigationGuard | null>(
    initialReviewJobId ? "review" : null,
  );
  const [pendingNavigation, setPendingNavigation] = useState<PendingNavigation | null>(null);
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
  const openReview = (id: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("job", id);
    window.history.replaceState({}, "", url);
    setReviewJobId(id);
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
      window.history.replaceState({}, "", url);
    }
    setNavigationGuard(null);
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
  useEffect(() => { retryTemplates(); void refreshJobs(); }, [refreshJobs, retryTemplates]);
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
    <div className="app-shell">
      <aside className="side-nav">
        <button className="brand" onClick={() => navigateTo("home")} aria-label="ホーム">
          <span>V2</span><div><strong>帳票 OCR V2</strong><small>FORM DEFINITION TOOL</small></div>
        </button>
        <nav aria-label="メインメニュー">
          <NavButton active={page === "home"} icon="⌂" label="ホーム" onClick={() => navigateTo("home")} />
          <NavButton active={page === "templates"} icon="型" label="テンプレート" onClick={() => navigateTo("templates")} />
          <NavButton active={page === "ocr" || page === "review"} icon="読" label="読み取り" onClick={() => navigateTo("ocr")} />
          <NavButton active={page === "sireNames"} icon="種" label="種雄牛名を追加" onClick={() => navigateTo("sireNames")} />
          <NavButton active={page === "records"} icon="個" label="個体検索" onClick={() => navigateTo("records")} />
        </nav>
        <div className="side-footer"><span className={health ? "status-dot ok" : "status-dot"} /><div><strong>{health ? "ローカル接続中" : "接続待ち"}</strong><small>127.0.0.1</small></div></div>
      </aside>
      <main className="main-content">
        {templateLoadFailed && (
          <aside className="error-box action-message" role="alert">
            <span>テンプレートを取得できませんでした。接続が戻ると自動で再取得します。</span>
            <button type="button" className="secondary compact" disabled={templatesLoading} onClick={retryTemplates}>
              {templatesLoading ? "再取得中…" : "テンプレートを再取得"}
            </button>
          </aside>
        )}
        {page === "home" && <HomePage health={health} templates={templates} jobs={jobs} onNavigate={navigateTo} onOpenJob={openJob} />}
        {page === "templates" && <TemplatePage templates={templates} refresh={refreshTemplates} rememberJob={rememberJob} onNavigationGuardChange={updateTemplateGuard} />}
        {page === "ocr" && <OcrPage templates={templates} rememberJob={rememberJob} openReview={openReview} openTemplates={() => navigateTo("templates")} onNavigationGuardChange={updateOcrGuard} />}
        {page === "review" && reviewJobId && <ReviewPage jobId={reviewJobId} requireIdentityConfirmation onNavigationGuardChange={updateReviewGuard} onClose={() => { void refreshJobs(); navigateTo("home"); }} />}
        {page === "job" && monitoredJobId && <JobPage
          jobId={monitoredJobId}
          onClose={() => { void refreshJobs(); navigateTo("home"); }}
          onOpenReview={openReview}
          onOpenTemplates={() => { retryTemplates(); navigateTo("templates"); }}
          onStartNewReading={() => navigateTo("ocr")}
        />}
        {page === "sireNames" && <SireNamesPage />}
        {page === "records" && <IndividualRecordsPage />}
      </main>
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

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: string; label: string; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick}><span>{icon}</span>{label}</button>;
}
