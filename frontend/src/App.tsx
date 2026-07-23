import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { HomePage } from "./pages/HomePage";
import { IndividualRecordsPage } from "./pages/IndividualRecordsPage";
import { OcrPage } from "./pages/OcrPage";
import { ReviewPage } from "./pages/ReviewPage";
import { TemplatePage } from "./pages/TemplatePage";
import type { Health, Job, Template } from "./types";

type Page = "home" | "templates" | "ocr" | "review" | "records";
const RECENT_KEY = "ledger-ocr-recent-jobs";

export default function App() {
  const initialReviewJobId = new URLSearchParams(window.location.search).get("job");
  const [page, setPage] = useState<Page>(initialReviewJobId ? "review" : "home");
  const [health, setHealth] = useState<Health | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [reviewJobId, setReviewJobId] = useState<string | null>(initialReviewJobId);

  const refreshTemplates = useCallback(async () => {
    const summaries = await api.templates();
    const details = await Promise.all(summaries.map((item) => api.template(item.id)));
    setTemplates(details);
  }, []);
  const refreshJobs = useCallback(async () => {
    const ids = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as string[];
    const loaded = await Promise.all(ids.slice(0, 12).map((id) => api.job(id).catch(() => null)));
    setJobs(loaded.filter((item): item is Job => item !== null));
  }, []);
  const rememberJob = useCallback((id: string) => {
    const current = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as string[];
    localStorage.setItem(RECENT_KEY, JSON.stringify([id, ...current.filter((item) => item !== id)].slice(0, 20)));
    void refreshJobs();
  }, [refreshJobs]);
  const openReview = (id: string) => { setReviewJobId(id); setPage("review"); };

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
  useEffect(() => { void refreshTemplates().catch(() => setTemplates([])); void refreshJobs(); }, [refreshJobs, refreshTemplates]);

  return (
    <div className="app-shell">
      <aside className="side-nav">
        <button className="brand" onClick={() => setPage("home")} aria-label="ホーム">
          <span>帳</span><div><strong>台帳 OCR</strong><small>LOCAL WORKBENCH</small></div>
        </button>
        <nav aria-label="メインメニュー">
          <NavButton active={page === "home"} icon="⌂" label="ホーム" onClick={() => setPage("home")} />
          <NavButton active={page === "templates"} icon="型" label="テンプレート" onClick={() => setPage("templates")} />
          <NavButton active={page === "ocr" || page === "review"} icon="読" label="読み取り" onClick={() => setPage("ocr")} />
          <NavButton active={page === "records"} icon="個" label="個体検索" onClick={() => setPage("records")} />
        </nav>
        <div className="side-footer"><span className={health ? "status-dot ok" : "status-dot"} /><div><strong>{health ? "ローカル接続中" : "接続待ち"}</strong><small>127.0.0.1</small></div></div>
      </aside>
      <main className="main-content">
        {page === "home" && <HomePage health={health} templates={templates} jobs={jobs} onNavigate={setPage} onOpenJob={(job) => { if (job.jobType === "EXTRACT_DOCUMENT" && ["REVIEW_REQUIRED", "CONFIRMED"].includes(job.status)) openReview(job.id); else setPage(job.jobType === "PREPARE_TEMPLATE" ? "templates" : "ocr"); }} />}
        {page === "templates" && <TemplatePage templates={templates} refresh={refreshTemplates} rememberJob={rememberJob} />}
        {page === "ocr" && <OcrPage templates={templates} rememberJob={rememberJob} openReview={openReview} />}
        {page === "review" && reviewJobId && <ReviewPage jobId={reviewJobId} onClose={() => { void refreshJobs(); setPage("home"); }} />}
        {page === "records" && <IndividualRecordsPage />}
      </main>
    </div>
  );
}

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: string; label: string; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick}><span>{icon}</span>{label}</button>;
}
