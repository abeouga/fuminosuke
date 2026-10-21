import { useEffect } from "react";
import { logDetail, userText } from "../userMessages";
import type { Job } from "../types";

const STAGE_LABELS: Record<string, string> = {
  VALIDATING_INPUT: "画像を確認中",
  NORMALIZING_DOCUMENT: "帳票の向きを調整中",
  DETECTING_LAYOUT: "帳票の枠とラベルを検出中",
  REGISTERING_TEMPLATE: "帳票位置を合わせています",
  ISOLATING_HANDWRITING: "手書き部分を分離中",
  DETECTING_MARKS: "選択マークを確認中",
  CROPPING_FIELDS: "読み取り領域を準備中",
  CALLING_OCR: "文字を読み取り中",
  VALIDATING_RESULT: "読み取り結果を点検中",
  WRITING_ARTIFACTS: "結果を保存中",
  READY_FOR_REVIEW: "確認の準備ができました",
};

const STATUS_LABELS: Record<string, string> = {
  CREATED: "受付済み", QUEUED: "待機中", RUNNING: "処理中", PROCESSING: "処理中", REVIEW_REQUIRED: "確認待ち", CONFIRMED: "確定済み",
  COMPLETED: "完了", FAILED: "失敗", INTERRUPTED: "中断", CANCELLED: "中止", CANCELLING: "中止中",
};

export function JobProgress({ job, connectionLost }: { job: Job | null; connectionLost: boolean }) {
  const failure = job?.error ? JSON.stringify(job.error) : null;
  useEffect(() => {
    // The code and stack trace are for diagnosis in the browser console, not for the screen.
    if (failure) logDetail("処理エラー", JSON.parse(failure));
  }, [failure]);
  if (!job) return null;
  return (
    <section className="progress-card" aria-live="polite">
      <div className="progress-head">
        <div>
          <span className={`status-pill status-${job.status.toLowerCase()}`}>{STATUS_LABELS[job.status] ?? "処理中"}</span>
          <strong>{STAGE_LABELS[job.progressStage ?? ""] ?? "処理を準備しています"}</strong>
        </div>
        <span className="progress-number">{job.progressPercent}%</span>
      </div>
      <div className="progress-track">
        <span style={{ width: `${job.progressPercent}%` }} />
      </div>
      {connectionLost && <p className="inline-warning">接続を再試行しています。処理はバックエンドで継続します。</p>}
      {job.status === "INTERRUPTED" && (
        <p className="inline-warning">バックエンドの再起動で中断されました。再試行できます。</p>
      )}
      {job.error && <p className="error-box">{userText(job.error.message, "処理エラー")}</p>}
    </section>
  );
}
