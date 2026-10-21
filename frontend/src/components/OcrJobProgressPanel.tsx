import { FiPlus, FiRefreshCw } from "react-icons/fi";
import type { Job } from "../types";
import { JobProgress } from "./JobProgress";
import { Spinner } from "./Spinner";

const PHOTO_ERRORS = new Set(["LOW_REGISTRATION_CONFIDENCE", "DOCUMENT_BOUNDARY_NOT_FOUND"]);

export function OcrJobProgressPanel({ job, connectionLost, canRetry, retryBusy, retryPending, retryBlocked, retryJob, startNewReading }: {
  job: Job | null; connectionLost: boolean; canRetry: boolean; retryBusy: boolean;
  retryPending: boolean; retryBlocked: boolean; retryJob: () => Promise<void>;
  startNewReading: () => void;
}) { return (<section className="ocr-progress-stage" aria-label="OCRの進捗">
          {job ? (
            <>
              <JobProgress job={job} connectionLost={connectionLost} />
              {job.status === "FAILED" && PHOTO_ERRORS.has(job.error?.code ?? "") && (
                <div className="ocr-progress-actions ocr-retake-guide" role="status">
                  <p>写真から書類の位置を合わせられませんでした。次の点を確かめて撮り直してください。</p>
                  <ul>
                    <li>書類の四隅がすべて写っている</li>
                    <li>選んだテンプレートと同じ書類である</li>
                    <li>手ぶれ・強い反射や影が少ない</li>
                  </ul>
                  <button className="primary" type="button" onClick={startNewReading}>
                    <FiPlus aria-hidden="true" />画像を選び直す
                  </button>
                </div>
              )}
              {canRetry && (
                <div className="ocr-progress-actions">
                  <p role="status">
                    {job.error?.code === "MULTI_PAGE_PARTIAL_FAILURE"
                      ? "成功したページは保持したまま、失敗したページだけを再試行します。"
                      : "受付時の画像と設定を使って、処理を再試行します。"}
                  </p>
                  <button className="primary" type="button" disabled={retryBusy || retryPending} onClick={() => void retryJob()}>
                    <FiRefreshCw aria-hidden="true" />{retryBusy || retryPending ? <><Spinner />再試行を開始中…</> : job.error?.code === "MULTI_PAGE_PARTIAL_FAILURE"
                      ? "失敗したページを再試行"
                      : "処理を再試行"}
                  </button>
                </div>
              )}
              {retryBlocked && (
                <div className="ocr-progress-actions">
                  <p role="status">
                    {job.error?.code.startsWith("OCR_CALL_BUDGET")
                      || job.error?.code.startsWith("PAGE_OCR_BUDGET")
                      || job.error?.code === "PAGE_EXECUTION_CONFIG_CHANGED"
                      ? "この受付の呼び出し予算または実行設定は変更できません。現在の設定で新しい読み取りを受け付け直してください。"
                      : "この受付は安全に再試行できません。画像を選び直して、新しい読み取りを開始してください。"}
                  </p>
                  <button className="primary" type="button" onClick={startNewReading}>
                    <FiPlus aria-hidden="true" />新しい読み取りを開始
                  </button>
                </div>
              )}
            </>
          ) : (
            <section className="progress-card" aria-live="polite">
              <div className="progress-head">
                <div>
                  <span className="status-pill status-queued">受付済み</span>
                  <strong>処理状況を確認しています</strong>
                </div>
                <span className="progress-number">--%</span>
              </div>
              <div className="progress-track ocr-progress-pending" aria-hidden="true">
                <span />
              </div>
              {connectionLost && <p className="inline-warning">接続を再試行しています。処理はバックエンドで継続します。</p>}
            </section>
          )}
        </section>); }
