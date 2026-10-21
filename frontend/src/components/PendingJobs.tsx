import type { Health, Job } from "../types";
import { FiArrowRight } from "react-icons/fi";

// Shown under the reading page's template list: the trial-mode notice and the jobs that still
// need attention (waiting for review, failed or interrupted), so they stay reachable without a home page.
export function PendingJobs({
  health,
  jobs,
  onOpenJob,
}: {
  health: Health | null;
  jobs: Job[];
  onOpenJob: (job: Job) => void;
}) {
  const attention = jobs.filter((job) => NEEDS_ATTENTION.has(job.status));
  return (
    <>
      {health?.ocrProvider === "mock" && (
        <aside className="notice"><strong>試運転モード</strong><span>OCRは固定のサンプル結果を返します。APIキーは不要です。</span></aside>
      )}
      {attention.length > 0 && (
        <details className="home-history">
          <summary>処理履歴<span className="home-history-count">{attention.length}件</span></summary>
          <p className="section-lead">確認待ち・中断・失敗した処理は、ここから内容確認や再試行へ進めます。</p>
          <div className="job-list home-attention-list" role="region" aria-label="処理履歴一覧" tabIndex={0}>
            {attention.map((job) => (
              <JobRow job={job} key={job.id} onOpenJob={onOpenJob} />
            ))}
          </div>
        </details>
      )}
    </>
  );
}

const NEEDS_ATTENTION = new Set([
  "CREATED",
  "QUEUED",
  "PROCESSING",
  "REVIEW_REQUIRED",
  "FAILED",
  "INTERRUPTED",
]);

function JobRow({
  job,
  onOpenJob,
}: {
  job: Job;
  onOpenJob: (job: Job) => void;
}) {
  return (
    <button className="job-row" onClick={() => onOpenJob(job)}>
      <span className="job-icon">{job.jobType === "EXTRACT_DOCUMENT" ? "読" : "型"}</span>
      <span>
        <strong>{job.jobType === "EXTRACT_DOCUMENT" ? "帳票読み取り" : "テンプレート準備"}</strong>
        <small>{new Date(job.createdAt).toLocaleString("ja-JP")}</small>
      </span>
      <span className={`status-pill status-${job.status.toLowerCase()}`}>{statusLabel(job.status)}</span>
      <FiArrowRight className="row-arrow" aria-hidden="true" />
    </button>
  );
}

function statusLabel(status: string) {
  return ({
    CREATED: "準備中",
    QUEUED: "待機中",
    PROCESSING: "処理中",
    REVIEW_REQUIRED: "確認待ち",
    CONFIRMED: "確定済み",
    FAILED: "失敗",
    CANCELLED: "中止",
    INTERRUPTED: "再開可能",
  } as Record<string, string>)[status] ?? status;
}
