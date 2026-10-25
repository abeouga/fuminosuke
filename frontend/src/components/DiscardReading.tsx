import { useState } from "react";
import { FiTrash2 } from "react-icons/fi";
import { ApiError, api } from "../api";
import { readingQueue } from "../readingQueue";
import { Spinner } from "./Spinner";
import { showToast } from "./Toast";

/**
 * 「廃棄」 on the review screen of a reading not confirmed yet: deletes the reading (its result,
 * the uploaded image and the crops) after asking, and takes it off the reading queue.
 */
export function DiscardReading({ jobId, disabled = false, onDiscarded }: {
  jobId: string;
  disabled?: boolean;
  onDiscarded: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const discard = async () => {
    setBusy(true);
    setError(null);
    try {
      try {
        await api.deleteJob(jobId, true);
      } catch (reason) {
        // Already gone (another tab discarded it): the outcome is the same.
        if (!(reason instanceof ApiError) || reason.code !== "JOB_NOT_FOUND") throw reason;
      }
      readingQueue.remove(jobId);
      showToast({ tone: "success", quiet: true, title: "読み取りを廃棄しました" });
      setAsking(false);
      onDiscarded();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "廃棄できませんでした。");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" className="ghost review-discard" disabled={disabled || busy}
        onClick={() => { setError(null); setAsking(true); }}>
        <FiTrash2 aria-hidden="true" />廃棄
      </button>
      {asking && (
        <div className="modal-backdrop">
          <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="discard-title">
            <h2 id="discard-title">この読み取りを廃棄しますか？</h2>
            <p>読み取り結果と画像を削除します。直した内容も消え、元に戻せません。</p>
            {error && <p className="error-box" role="alert">{error}</p>}
            <div className="page-actions">
              <button type="button" className="secondary" disabled={busy} onClick={() => setAsking(false)}>確認を続ける</button>
              <button type="button" className="danger-button" disabled={busy} onClick={() => void discard()}>
                {busy ? <Spinner /> : <FiTrash2 aria-hidden="true" />}廃棄する
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
