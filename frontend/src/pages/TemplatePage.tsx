import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api";
import { ComparisonViewer } from "../components/ComparisonViewer";
import { FilePicker } from "../components/FilePicker";
import { FieldMappingEditor } from "../components/FieldMappingEditor";
import { JobProgress } from "../components/JobProgress";
import { MaskEditor } from "../components/MaskEditor";
import { useJobPoller } from "../hooks/useJobPoller";
import type { Artifact, FieldMappingsResponse, Job, Template, TemplateVersion } from "../types";

const MODES = [
  { id: "REFERENCE_TEMPLATE", number: "01", title: "白紙帳票から", copy: "記入前の帳票がある場合。最も安定した方法です。", count: "1枚・推奨" },
  { id: "MULTI_SCAN_CONSENSUS", number: "02", title: "複数スキャンから", copy: "同じ帳票の記入済み画像から共通の印刷部分を復元します。", count: "3枚以上" },
  { id: "SINGLE_IMAGE_EXPERIMENTAL", number: "03", title: "1枚だけで試す", copy: "黒い印刷と手書きを完全には分離できません。必ず目視確認します。", count: "1枚・実験的" },
] as const;

export function TemplatePage({ templates, refresh, rememberJob }: { templates: Template[]; refresh: () => Promise<void>; rememberJob: (id: string) => void }) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("REFERENCE_TEMPLATE");
  const [name, setName] = useState("牛繁殖台帳");
  const [files, setFiles] = useState<File[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [review, setReview] = useState<{ version: TemplateVersion; jobId: string } | null>(null);
  const { job, connectionLost } = useJobPoller(jobId, (terminal) => {
    if (terminal.status === "REVIEW_REQUIRED") void openPreparedTemplate(terminal);
  });

  const openPreparedTemplate = async (terminal: Job) => {
    await refresh();
    const list = await api.templates();
    for (const item of list) {
      const detail = await api.template(item.id);
      const version = detail.versions.find((candidate) => candidate.sourceJobId === terminal.id);
      if (version) return setReview({ version, jobId: terminal.id });
    }
  };
  const draftVersions = templates.flatMap((item) => item.versions).filter((version) => version.status === "DRAFT");
  const activeVersions = templates.flatMap((template) => template.versions
    .filter((version) => version.status === "ACTIVE")
    .map((version) => ({ templateName: template.name, version })));
  const requiredCountValid = mode === "MULTI_SCAN_CONSENSUS" ? files.length >= 3 : files.length === 1;

  if (review) {
    return <TemplateReview version={review.version} jobId={review.jobId} onClose={() => { setReview(null); void refresh(); }} onReprocess={(nextJobId) => { setReview(null); setJobId(nextJobId); rememberJob(nextJobId); }} />;
  }
  return (
    <div className="page-stack narrow">
      <header className="page-header"><span className="eyebrow">TEMPLATE PREPARATION</span><h1>帳票の「型」を準備する</h1><p>罫線や印刷文字だけの基準画像を作り、読み取り位置を安定させます。</p></header>
      {draftVersions.length > 0 && (
        <section className="resume-strip"><div><strong>確認待ちのテンプレートがあります</strong><span>{draftVersions.length}件の下書き</span></div>{draftVersions.map((version) => <button key={version.id} className="secondary compact" onClick={() => version.sourceJobId && setReview({ version, jobId: version.sourceJobId })}>確認を再開</button>)}</section>
      )}
      {activeVersions.length > 0 && (
        <section className="resume-strip active-template-strip"><div><strong>有効テンプレートの読取項目</strong><span>矩形とJSON項目の対応を編集できます</span></div>{activeVersions.map(({ templateName, version }) => <button key={version.id} className="secondary compact" disabled={!version.sourceJobId} onClick={() => version.sourceJobId && setReview({ version, jobId: version.sourceJobId })}>{templateName} v{version.versionNumber} を設定</button>)}</section>
      )}
      <section className="form-card">
        <label className="field-label">テンプレート名<input value={name} maxLength={200} onChange={(event) => setName(event.target.value)} /></label>
        <fieldset><legend>準備方法</legend><div className="mode-grid">{MODES.map((item) => (
          <button type="button" key={item.id} className={`mode-card ${mode === item.id ? "selected" : ""}`} onClick={() => { setMode(item.id); setFiles([]); }}>
            <span className="mode-number">{item.number}</span><strong>{item.title}</strong><p>{item.copy}</p><small>{item.count}</small>
          </button>
        ))}</div></fieldset>
        <FilePicker key={mode} multiple={mode === "MULTI_SCAN_CONSENSUS"} onChange={setFiles} />
        {files.length > 0 && !requiredCountValid && <p className="inline-warning">複数スキャン方式には3枚以上の画像が必要です。</p>}
        {message && <p className="error-box">{message}</p>}
        <button className="primary full" disabled={busy || !name.trim() || !requiredCountValid} onClick={async () => {
          setBusy(true); setMessage(null);
          try { const created = await api.createTemplate(name, mode, files); setJobId(created.jobId); rememberJob(created.jobId); }
          catch (error) { setMessage(error instanceof ApiError ? error.message : "開始できませんでした。"); }
          finally { setBusy(false); }
        }}>{busy ? "送信中…" : "テンプレート準備を開始"}</button>
      </section>
      <JobProgress job={job} connectionLost={connectionLost} />
    </div>
  );
}

function TemplateReview({ version, jobId, onClose, onReprocess }: { version: TemplateVersion; jobId: string; onClose: () => void; onReprocess: (jobId: string) => void }) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [mappingConfig, setMappingConfig] = useState<FieldMappingsResponse | null>(null);
  const [layer, setLayer] = useState("debugOverlay");
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    void Promise.all([api.artifacts(jobId), api.fieldMappings(version.id)])
      .then(([artifactResponse, mappings]) => { setArtifacts(artifactResponse.items); setMappingConfig(mappings); })
      .catch((error) => setMessage(error instanceof ApiError ? error.message : "設定を読み込めませんでした。"));
  }, [jobId, version.id]);
  const byName = useMemo(() => new Map(artifacts.map((item) => [item.name, item.url])), [artifacts]);
  const before = byName.get("alignedSource.1") ?? byName.get("normalized") ?? byName.get("cleanTemplate") ?? "";
  const after = byName.get("cleanTemplate") ?? byName.get("cleanPreview") ?? before;
  const mask = byName.get("handwritingMask") ?? byName.get("suspectedHandwritingMask");
  const layers = ["debugOverlay", "registrationDebug", "handwritingOnly", "handwritingProbability", "staticInkFrequency"].filter((name) => byName.has(name));
  return (
    <div className="page-stack">
      <header className="page-header split"><div><span className="eyebrow">TEMPLATE {version.status === "ACTIVE" ? "FIELD SETTINGS" : "REVIEW"} · V{version.versionNumber}</span><h1>{version.status === "ACTIVE" ? "読み取る欄を設定" : "復元結果を確認"}</h1><p>矩形とJSON項目の対応はGUIで固定します。LLMは矩形内の文字の転記だけを担当します。</p></div><button className="ghost" onClick={onClose}>← 一覧へ戻る</button></header>
      <div className="review-layout">
        <section className="viewer-card">
          {before && after ? <ComparisonViewer before={before} after={after} /> : <div className="empty-state">画像を読み込んでいます…</div>}
          <div className="layer-tabs">{layers.map((name) => <button key={name} className={layer === name ? "active" : ""} onClick={() => setLayer(name)}>{layerLabel(name)}</button>)}</div>
          {byName.get(layer) && <div className="single-image-view"><img src={byName.get(layer)} alt={layerLabel(layer)} /></div>}
        </section>
        <aside className="quality-card"><span className="panel-kicker">品質メモ</span><h3>自動点検の結果</h3>{Object.entries(version.quality).slice(0, 8).map(([key, value]) => <div className="metric" key={key}><span>{key}</span><strong>{typeof value === "number" ? value.toFixed(3) : String(value)}</strong></div>)}<p>数値は正解確率ではありません。比較画像を優先して確認してください。</p></aside>
      </div>
      {mappingConfig && after && <section className="form-card mapping-section"><div className="section-title"><div><span className="eyebrow">FIELD MAPPING</span><h2>矩形をJSON項目へ割り当て</h2><p>ラベルを選んで、記入欄だけを囲んでください。印刷済みの見出し文字は囲みません。</p></div></div><FieldMappingEditor source={after} canonicalWidth={mappingConfig.canonicalWidth} canonicalHeight={mappingConfig.canonicalHeight} initialMappings={mappingConfig.mappings} targets={mappingConfig.targets} onSave={async (mappings) => {
        try {
          const saved = await api.saveFieldMappings(version.id, mappings);
          setMappingConfig(saved);
          setMessage(`項目マッピングを${saved.mappings.length}件保存しました。次回OCRから反映されます。`);
        } catch (error) {
          setMessage(error instanceof ApiError ? error.message : "項目マッピングを保存できませんでした。");
        }
      }} /></section>}
      {mask && <section className="form-card"><div className="section-title"><div><span className="eyebrow">MASK EDITOR</span><h2>手書き候補を選別</h2></div></div><MaskEditor source={mask} background={before} candidatesSource={byName.get("handwritingCandidates")} onSave={async (blob) => { await api.saveMask(version.id, blob); setMessage("選別結果を保存しました。再生成すると結果へ反映されます。"); }} /></section>}
      {message && <p className="success-box">{message}</p>}
      {version.status === "DRAFT" ? <div className="decision-bar"><div><strong>この復元結果を採用しますか？</strong><span>矩形設定を保存してから承認すると、その設定でOCRできます。</span></div><button className="danger-button" onClick={async () => { await api.reject(version.id); onClose(); }}>却下</button><button className="secondary" onClick={async () => { const next = await api.reprocess(version.id); onReprocess(next.jobId); }}>マスクから再生成</button><button className="primary" onClick={async () => { await api.approve(version.id); onClose(); }}>承認して有効化</button></div> : <div className="decision-bar"><div><strong>有効テンプレートを編集中</strong><span>保存済みの矩形設定は次に開始するOCRから使われます。</span></div><button className="primary" onClick={onClose}>設定を閉じる</button></div>}
    </div>
  );
}

function layerLabel(name: string) {
  return ({ debugOverlay: "検出オーバーレイ", registrationDebug: "位置合わせ", handwritingOnly: "手書きのみ", handwritingProbability: "手書き候補", staticInkFrequency: "共通印刷" } as Record<string, string>)[name] ?? name;
}
