import { useState } from "react";
import { InfoTip } from "../components/InfoTip";
import { ApiError, api } from "../api";
import { JobProgress } from "../components/JobProgress";
import { useJobPoller } from "../hooks/useJobPoller";
import type { Job } from "../types";
import { Spinner } from "../components/Spinner";
import { DiscardReading } from "../components/DiscardReading";

const RETRYABLE = new Set(["FAILED", "INTERRUPTED"]);
const CANCELLABLE = new Set(["QUEUED", "PROCESSING"]);
const NEW_READING_REQUIRED_ERRORS = new Set([
  "INCOMPLETE_JOB_INPUTS",
  "OCR_TEMPLATE_SNAPSHOT_INVALID",
  "OCR_CALL_BUDGET_TOO_SMALL",
  "OCR_CALL_BUDGET_EXHAUSTED",
  "OCR_CALL_BUDGET_RETRY_RESERVE_REQUIRED",
  "PAGE_OCR_BUDGET_REQUIRED",
  "PAGE_OCR_BUDGET_INVALID",
  "PAGE_EXECUTION_CONFIG_CHANGED",
]);

export function JobPage({
  jobId,
  onClose,
  onOpenReview,
  onOpenTemplates,
  onStartNewReading,
}: {
  jobId: string;
  onClose: () => void;
  onOpenReview: (jobId: string) => void;
  onOpenTemplates: (workspace: "create" | "manage") => void;
  onStartNewReading: () => void;
}) {
  const [refreshToken, setRefreshToken] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { job, connectionLost } = useJobPoller(jobId, undefined, refreshToken);
  const requiresNewRegistration = job !== null && cannotRetrySafely(job);

  const retry = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.retry(jobId, job?.resourceRevision);
      setRefreshToken((value) => value + 1);
      setMessage("処理を再開しました。完了までこの画面で確認できます。");
    } catch (reason) {
      setMessage(reason instanceof ApiError ? reason.message : "再試行を開始できませんでした。");
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm("この処理を中止しますか？保存済みの元画像は削除されません。")) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.cancel(jobId);
      setRefreshToken((value) => value + 1);
      setMessage("中止を受け付けました。処理中の場合は安全な区切りで停止します。");
    } catch (reason) {
      setMessage(reason instanceof ApiError ? reason.message : "処理を中止できませんでした。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-stack narrow">
      <header className="page-header split has-back title-end">
        <button className="ghost page-back" type="button" onClick={onClose}>← 戻る</button>
        <div>
          <span className="eyebrow">PROCESS RECOVERY</span>
          <h1>処理状況を確認<InfoTip align="end" text="やり直すか、新しく読み取るか選べます。" /></h1>
        </div>
      </header>

      <JobProgress job={job} connectionLost={connectionLost} actions={job && (
        <>
        {RETRYABLE.has(job.status) && job.jobType === "EXTRACT_DOCUMENT" && (
          <DiscardReading jobId={job.id} disabled={busy} onDiscarded={onClose} />
        )}
        {requiresNewRegistration && (
          <button
            className="primary"
            type="button"
            onClick={job.jobType === "PREPARE_TEMPLATE"
              ? () => onOpenTemplates("create")
              : onStartNewReading}
          >
            {job.jobType === "PREPARE_TEMPLATE"
              ? "新しいテンプレートを登録"
              : "新しい読み取りを開始"}
          </button>
        )}
        {job.status === "REVIEW_REQUIRED" && job.jobType === "EXTRACT_DOCUMENT" && (
          <button className="primary" type="button" onClick={() => onOpenReview(job.id)}>
            読み取り結果を確認
          </button>
        )}
        {job.status === "CONFIRMED" && job.jobType === "EXTRACT_DOCUMENT" && (
          <button className="primary" type="button" onClick={() => onOpenReview(job.id)}>
            確定済み結果を表示
          </button>
        )}
        {job.status === "REVIEW_REQUIRED" && job.jobType === "PREPARE_TEMPLATE" && (
          <button className="primary" type="button" onClick={() => onOpenTemplates("manage")}>
            テンプレートを確認
          </button>
        )}
        {RETRYABLE.has(job.status) && !requiresNewRegistration && (
          <button className="primary" type="button" disabled={busy} onClick={() => void retry()}>
            {busy ? <><Spinner />再開中…</> : "処理を再試行"}
          </button>
        )}
        {CANCELLABLE.has(job.status) && (
          <button className="danger-button" type="button" disabled={busy} onClick={() => void cancel()}>
            処理を中止
          </button>
        )}
        </>
      )} />

      {!job && !connectionLost && (
        <div className="empty-state large">処理状況を読み込んでいます…</div>
      )}
      {connectionLost && !job && (
        <aside className="notice danger">
          <strong>バックエンドへ接続できません</strong>
          <span>接続が戻ると自動的に確認を再開します。処理状況はサーバーに保存されています。</span>
        </aside>
      )}
      {message && <p className={job?.status === "FAILED" ? "error-box" : "success-box"}>{message}</p>}

          </div>
  );
}

function cannotRetrySafely(job: Job) {
  return job.status === "FAILED" && NEW_READING_REQUIRED_ERRORS.has(job.error?.code ?? "");
}
