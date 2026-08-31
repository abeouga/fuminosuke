import { useState } from "react";
import { ApiError, api } from "../api";
import { JobProgress } from "../components/JobProgress";
import { useJobPoller } from "../hooks/useJobPoller";
import type { Job } from "../types";

const RETRYABLE = new Set(["FAILED", "INTERRUPTED"]);
const CANCELLABLE = new Set(["QUEUED", "PROCESSING"]);

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
      await api.retry(jobId);
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
      <header className="page-header split">
        <div>
          <span className="eyebrow">PROCESS RECOVERY</span>
          <h1>処理状況を確認</h1>
          <p>保存された処理状況を確認し、再試行または新しい登録など必要な対応を選べます。</p>
        </div>
        <button className="ghost" type="button" onClick={onClose}>← ホームへ</button>
      </header>

      <JobProgress job={job} connectionLost={connectionLost} />

      {!job && !connectionLost && (
        <div className="empty-state large">処理状況を読み込んでいます…</div>
      )}
      {connectionLost && !job && (
        <aside className="notice danger">
          <strong>バックエンドへ接続できません</strong>
          <span>接続が戻ると自動的に確認を再開します。ジョブはDBに保存されています。</span>
        </aside>
      )}
      {message && <p className={job?.status === "FAILED" ? "error-box" : "success-box"}>{message}</p>}

      {job && (
        <section className="form-card job-recovery-actions">
          <div>
            <span className="eyebrow">NEXT ACTION</span>
            <h2>{nextActionTitle(job)}</h2>
            <p>{nextActionDescription(job)}</p>
          </div>
          <div className="job-action-buttons">
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
                {busy ? "再開中…" : "処理を再試行"}
              </button>
            )}
            {CANCELLABLE.has(job.status) && (
              <button className="danger-button" type="button" disabled={busy} onClick={() => void cancel()}>
                処理を中止
              </button>
            )}
            <button className="secondary" type="button" onClick={onClose}>ホームへ戻る</button>
          </div>
        </section>
      )}
    </div>
  );
}

function nextActionTitle(job: Job) {
  if (hasIncompleteJobInputs(job)) return "元画像の保存が完了していません";
  if (hasInvalidTemplateSnapshot(job)) return "受付時のテンプレートを確認できません";
  if (job.status === "REVIEW_REQUIRED") {
    return job.jobType === "EXTRACT_DOCUMENT"
      ? "読み取り結果を確認できます"
      : "テンプレートの確認へ進めます";
  }
  if (job.status === "CONFIRMED") return "この結果は確定保存済みです";
  if (job.status === "FAILED") return "処理に失敗しました";
  if (job.status === "INTERRUPTED") return "再起動により処理が中断されました";
  if (job.status === "CANCELLED") return "処理は中止されています";
  return "処理が完了するまでお待ちください";
}

function nextActionDescription(job: Job) {
  if (hasIncompleteJobInputs(job)) {
    return job.jobType === "PREPARE_TEMPLATE"
      ? "受付中に停止したため、この処理には再試行に必要な元画像がありません。画像を選び直して、新しいテンプレートとして登録してください。"
      : "受付中に停止したため、この処理には再試行に必要な元画像がありません。画像を選び直して、新しい読み取りを開始してください。";
  }
  if (hasInvalidTemplateSnapshot(job)) {
    return "受付時に固定したテンプレート画像または設定を確認できないため、安全に再試行できません。画像を選び直して、新しい読み取りを開始してください。";
  }
  if (RETRYABLE.has(job.status)) return "元画像と設定は保存されています。同じ内容で安全に再試行できます。";
  if (job.status === "CANCELLED") return "新しく読み取りを開始する場合は、左側の「読み取り」を選んでください。";
  if (job.status === "REVIEW_REQUIRED") return "内容を目視確認してから確定保存してください。";
  return "画面を移動しても処理はバックエンドで継続します。";
}

function hasIncompleteJobInputs(job: Job) {
  return job.status === "FAILED" && job.error?.code === "INCOMPLETE_JOB_INPUTS";
}

function hasInvalidTemplateSnapshot(job: Job) {
  return job.status === "FAILED" && job.error?.code === "OCR_TEMPLATE_SNAPSHOT_INVALID";
}

function cannotRetrySafely(job: Job) {
  return hasIncompleteJobInputs(job) || hasInvalidTemplateSnapshot(job);
}
