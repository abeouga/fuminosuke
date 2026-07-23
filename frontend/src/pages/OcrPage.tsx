import { useMemo, useState } from "react";
import { ApiError, api } from "../api";
import { FilePicker } from "../components/FilePicker";
import { JobProgress } from "../components/JobProgress";
import { useJobPoller } from "../hooks/useJobPoller";
import type { Template } from "../types";

export function OcrPage({
  templates,
  rememberJob,
  openReview,
}: {
  templates: Template[];
  rememberJob: (id: string) => void;
  openReview: (id: string) => void;
}) {
  const versions = useMemo(
    () => templates.flatMap((template) => template.versions.filter((version) => version.status === "ACTIVE").map((version) => ({ template, version }))),
    [templates],
  );
  const [versionId, setVersionId] = useState(versions[0]?.version.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { job, connectionLost } = useJobPoller(jobId, (terminal) => {
    if (terminal.status === "REVIEW_REQUIRED") openReview(terminal.id);
  });
  return (
    <div className="page-stack narrow">
      <header className="page-header"><span className="eyebrow">NEW READING</span><h1>台帳を読み取る</h1><p>有効なテンプレートに画像を合わせ、手書き部分を確認しやすい形にします。</p></header>
      {versions.length === 0 ? (
        <div className="empty-state large"><span>型</span><h2>有効なテンプレートがありません</h2><p>先にテンプレートを準備して承認してください。</p></div>
      ) : (
        <section className="form-card">
          <label className="field-label">使用するテンプレート<select value={versionId} onChange={(event) => setVersionId(event.target.value)}>{versions.map(({ template, version }) => <option key={version.id} value={version.id}>{template.name} — v{version.versionNumber}</option>)}</select></label>
          <FilePicker multiple={false} onChange={(files) => setFile(files[0] ?? null)} label="台帳画像を選択 / 撮影" />
          <label className="switch-row"><span><strong>文字読み取りも実行する</strong><small>オフにすると画像処理と領域切り出しだけを行います。</small></span><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><i /></label>
          {!enabled && <aside className="notice compact"><strong>画像処理のみ</strong><span>結果欄は空欄として作成され、手入力できます。</span></aside>}
          {message && <p className="error-box">{message}</p>}
          <button className="primary full" disabled={!file || !versionId || busy} onClick={async () => {
            if (!file) return;
            setBusy(true); setMessage(null);
            try { const created = await api.createOcr(versionId, file, enabled); setJobId(created.jobId); rememberJob(created.jobId); }
            catch (error) { setMessage(error instanceof ApiError ? error.message : "開始できませんでした。"); }
            finally { setBusy(false); }
          }}>{busy ? "送信中…" : enabled ? "読み取りを開始" : "画像処理を開始"}</button>
        </section>
      )}
      <JobProgress job={job} connectionLost={connectionLost} />
    </div>
  );
}
