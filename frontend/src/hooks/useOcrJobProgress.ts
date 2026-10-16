import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { showToast } from "../components/Toast";
import { useJobPoller } from "./useJobPoller";

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

export function useOcrJobProgress(jobId: string | null, paged: boolean,
  openReview: (id: string) => void) {
  const [retryBusy, setRetryBusy] = useState(false);
  const [retryRequestedForAttempt, setRetryRequestedForAttempt] = useState<number | null>(null);
  const [jobPollRevision, setJobPollRevision] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    setRetryBusy(false);
    setRetryRequestedForAttempt(null);
    return () => { generation.current += 1; };
  }, [jobId]);
  const { job, connectionLost } = useJobPoller(jobId, (terminal) => {
    if (terminal.status !== "REVIEW_REQUIRED") return;
    const current = generation.current;
    if (paged) {
      void api.pageReview(terminal.id).then(() => {
        if (current === generation.current) openReview(terminal.id);
      }).catch((reason) => {
        if (current === generation.current) showToast({ tone: "error", title: "ページ結果を開けませんでした",
          message: reason instanceof Error ? reason.message : "バックエンドとの接続を確認してください。" });
      });
      return;
    }
    void api.result(terminal.id)
      .then(() => {
        if (current !== generation.current) return;
        openReview(terminal.id);
      })
      .catch(() => { if (current === generation.current) openReview(terminal.id); });
  }, jobPollRevision);
  const retryBlocked = job?.status === "FAILED"
    && NEW_READING_REQUIRED_ERRORS.has(job.error?.code ?? "");
  const canRetry = Boolean(job && ["FAILED", "INTERRUPTED"].includes(job.status) && !retryBlocked);
  const retryPending = retryRequestedForAttempt === job?.attemptNumber;

  const retryJob = async () => {
    if (!job || !canRetry || retryBusy || retryPending) return;
    const current = generation.current;
    setRetryBusy(true);
    try {
      await api.retry(job.id, job.resourceRevision);
      if (current !== generation.current) return;
      setRetryRequestedForAttempt(job.attemptNumber);
      setJobPollRevision((current) => current + 1);
    } catch (error) {
      if (current !== generation.current) return;
      showToast({
        tone: "error",
        title: "再試行を開始できませんでした",
        message: error instanceof ApiError ? error.message : "バックエンドとの接続を確認してください。",
      });
    } finally {
      if (current === generation.current) setRetryBusy(false);
    }
  };

  const resetRetry = () => { setRetryBusy(false); setRetryRequestedForAttempt(null); };
  return { job, connectionLost, canRetry, retryBusy, retryPending, retryBlocked, retryJob, resetRetry };
}
