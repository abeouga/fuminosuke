import type { Health, Job } from "../types";
import { FiArrowRight, FiCheckSquare, FiDownload, FiFileText, FiGrid } from "react-icons/fi";
import { usePhoneDevice } from "../hooks/usePhoneDevice";

export function HomePage({
  health,
  healthChecked = true,
  jobs,
  onNavigate,
  onOpenJob,
}: {
  health: Health | null;
  healthChecked?: boolean;
  jobs: Job[];
  onNavigate: (page: "templates" | "ocr") => void;
  onOpenJob: (job: Job) => void;
}) {
  const phoneDevice = usePhoneDevice();
  const attention = jobs.filter((job) => NEEDS_ATTENTION.has(job.status));
  const lastOutput = jobs.find((job) => job.jobType === "EXTRACT_DOCUMENT" && job.status === "CONFIRMED");
  return (
    <div className="page-stack home-workspace">
      <section className="hero home-hero">
        <div>
          <span className="eyebrow">帳票の読み取り</span>
          <h1>紙の書類を、<em>確認できるデータ</em>へ。</h1>
          <p className="hero-copy">帳票を読み取り、内容を確認・修正して、記入済みのXLSXを取得します。</p>
          <ol className="home-workflow" aria-label="読み取りの流れ">
            <li><FiFileText aria-hidden="true" /><span>1. 帳票を選ぶ</span></li>
            <li><FiCheckSquare aria-hidden="true" /><span>2. 結果を確認・修正</span></li>
            <li><FiDownload aria-hidden="true" /><span>3. 確定してXLSX取得</span></li>
          </ol>
          <div className="hero-actions">
            <button className="primary" onClick={() => onNavigate("ocr")}><FiArrowRight aria-hidden="true" />新しいOCRを開始</button>
          </div>
        </div>
      </section>

      {!phoneDevice && <section className="home-shortcuts" aria-label="テンプレートの準備">
        <div>
          <FiFileText aria-hidden="true" />
          <div><h2>初めて使う帳票</h2><p>空の帳票から、読み取る欄を設定します。</p></div>
          <button className="secondary" onClick={() => onNavigate("templates")}><FiGrid aria-hidden="true" />テンプレートを準備</button>
        </div>
      </section>}

      {health?.ocrProvider === "mock" && (
        <aside className="notice"><strong>試運転モード</strong><span>OCRは固定のサンプル結果を返します。APIキーは不要です。</span></aside>
      )}
      {!health && healthChecked && (
        <aside className="notice danger"><strong>接続を確認できません</strong><span>接続は自動で再確認します。復帰後に保存済みの処理状況を取得します。</span></aside>
      )}

      {lastOutput && <section className="home-last-output" aria-label="直前の確定結果">
        <FiDownload aria-hidden="true" />
        <div><h2>直前の確定結果</h2><p>{new Date(lastOutput.completedAt ?? lastOutput.createdAt).toLocaleString("ja-JP")} · 結果を開いてXLSXを取得できます。</p></div>
        <button className="secondary" onClick={() => onOpenJob(lastOutput)}><FiDownload aria-hidden="true" />結果・XLSXを開く</button>
      </section>}

      {attention.length > 0 && (
        <section>
          <div className="section-title"><h2>対応が必要な処理</h2></div>
          <p className="section-lead">確認待ち・中断・失敗した処理は、ここから内容確認や再試行へ進めます。</p>
          <div className="job-list home-attention-list" role="region" aria-label="対応が必要な処理一覧" tabIndex={0}>
            {attention.map((job) => (
              <JobRow job={job} key={job.id} onOpenJob={onOpenJob} />
            ))}
          </div>
        </section>
      )}

    </div>
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
