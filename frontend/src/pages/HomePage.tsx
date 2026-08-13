import type { Health, Job, Template } from "../types";

export function HomePage({
  health,
  templates,
  jobs,
  onNavigate,
  onOpenJob,
}: {
  health: Health | null;
  templates: Template[];
  jobs: Job[];
  onNavigate: (page: "templates" | "ocr" | "records") => void;
  onOpenJob: (job: Job) => void;
}) {
  const active = templates.flatMap((template) => template.versions).filter((version) => version.status === "ACTIVE");
  const attention = jobs.filter((job) => NEEDS_ATTENTION.has(job.status));
  const recent = jobs.filter((job) => !NEEDS_ATTENTION.has(job.status));
  return (
    <div className="page-stack">
      <section className="hero">
        <div>
          <p className="eyebrow">LOCAL LEDGER WORKBENCH</p>
          <h1>紙の台帳を、<br /><em>確認できるデータ</em>へ。</h1>
          <p className="hero-copy">帳票の位置合わせ、手書き分離、文字読み取り、目視確認までをこの端末内で管理します。</p>
          <div className="hero-actions">
            <button className="primary" onClick={() => onNavigate("ocr")}>新しいOCRを開始</button>
            <button className="secondary" onClick={() => onNavigate("records")}>個体JSONを検索</button>
            <button className="secondary" onClick={() => onNavigate("templates")}>テンプレートを準備</button>
          </div>
        </div>
        <div className="health-panel">
          <span className="panel-kicker">稼働状況</span>
          <StatusRow label="バックエンド" ok={Boolean(health)} value={health ? "接続中" : "未接続"} />
          <StatusRow label="処理ワーカー" ok={health?.jobWorker === "running"} value={health?.jobWorker === "running" ? "稼働中" : "停止中"} />
          <StatusRow label="OCR" ok={health?.ocrProvider !== "disabled"} value={providerLabel(health?.ocrProvider)} />
          <StatusRow label="有効テンプレート" ok={active.length > 0} value={`${active.length} 件`} />
          <p className="local-note">データの保存先はこのローカル端末です。</p>
        </div>
      </section>

      {health?.ocrProvider === "mock" && (
        <aside className="notice"><strong>試運転モード</strong><span>OCRは固定のサンプル結果を返します。APIキーは不要です。</span></aside>
      )}
      {health?.ocrProvider === "openai" && (
        <aside className="notice external"><strong>外部OCRを使用</strong><span>読み取り対象の領域画像がOpenAI APIへ送信されます。</span></aside>
      )}
      {!health && (
        <aside className="notice danger"><strong>バックエンド未起動</strong><span>backendで開発サーバーを起動してください。自動的に再確認します。</span></aside>
      )}

      {attention.length > 0 && (
        <section>
          <div className="section-title"><div><span className="eyebrow">ACTION REQUIRED</span><h2>対応が必要な処理</h2></div></div>
          <p className="section-lead">確認待ち・中断・失敗した処理は、ここから内容確認や再試行へ進めます。</p>
          <div className="job-list">
            {attention.map((job) => (
              <JobRow job={job} key={job.id} onOpenJob={onOpenJob} />
            ))}
          </div>
        </section>
      )}

      <section>
        <div className="section-title"><div><span className="eyebrow">RECENT</span><h2>最近の処理</h2></div></div>
        {recent.length === 0 ? (
          <div className="empty-state">まだ処理履歴はありません。</div>
        ) : (
          <div className="job-list">
            {recent.map((job) => (
              <JobRow job={job} key={job.id} onOpenJob={onOpenJob} />
            ))}
          </div>
        )}
      </section>
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
      <span className="row-arrow">→</span>
    </button>
  );
}

function StatusRow({ label, ok, value }: { label: string; ok: boolean; value: string }) {
  return <div className="status-row"><span className={ok ? "status-dot ok" : "status-dot"} /><span>{label}</span><strong>{value}</strong></div>;
}

function providerLabel(provider?: Health["ocrProvider"]) {
  if (provider === "mock") return "試運転（mock）";
  if (provider === "openai") return "外部OCR";
  if (provider === "disabled") return "画像処理のみ";
  return "確認中";
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
