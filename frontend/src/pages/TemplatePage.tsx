import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, createIdempotencyKey } from "../api";
import { ComparisonViewer } from "../components/ComparisonViewer";
import { FilePicker } from "../components/FilePicker";
import { JobProgress } from "../components/JobProgress";
import { LayoutEditor } from "../components/LayoutEditor";
import { MaskEditor } from "../components/MaskEditor";
import { useJobPoller } from "../hooks/useJobPoller";
import type { Artifact, FieldMappingsResponse, Job, LayoutDefinition, Template, TemplateVersion } from "../types";

const MODES = [
  { id: "REFERENCE_TEMPLATE", number: "01", title: "白紙帳票から", copy: "記入前の帳票がある場合。最も安定した方法です。", count: "1枚・推奨" },
  { id: "MULTI_SCAN_CONSENSUS", number: "02", title: "複数スキャンから", copy: "同じ帳票の記入済み画像から共通の印刷部分を復元します。", count: "3枚以上" },
  { id: "SINGLE_IMAGE_EXPERIMENTAL", number: "03", title: "1枚だけで試す", copy: "黒い印刷と手書きを完全には分離できません。必ず目視確認します。", count: "1枚・実験的" },
] as const;

export function TemplatePage({
  templates,
  refresh,
  rememberJob,
  onNavigationGuardChange,
}: {
  templates: Template[];
  refresh: () => Promise<void>;
  rememberJob: (id: string) => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("REFERENCE_TEMPLATE");
  const [name, setName] = useState("牛繁殖台帳");
  const [files, setFiles] = useState<File[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openingReview, setOpeningReview] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [review, setReview] = useState<{ version: TemplateVersion; jobId: string } | null>(null);
  const creatingRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);
  const openingReviewRef = useRef(false);
  const { job, connectionLost } = useJobPoller(jobId, (terminal) => {
    if (terminal.status === "REVIEW_REQUIRED") void openPreparedTemplate(terminal);
  });

  const openPreparedTemplate = async (terminal: Job) => {
    if (openingReviewRef.current) return;
    openingReviewRef.current = true;
    setOpeningReview(true);
    setMessage(null);
    try {
      await refresh();
      const list = await api.templates();
      const details = await Promise.all(list.map((item) => api.template(item.id)));
      const version = details
        .flatMap((item) => item.versions)
        .find((candidate) => candidate.sourceJobId === terminal.id);
      if (!version) {
        throw new Error("確認対象のテンプレートを見つけられませんでした。少し待ってから再取得してください。");
      }
      setReview({ version, jobId: terminal.id });
    } catch (error) {
      setMessage(errorMessage(error, "確認画面を開けませんでした。"));
    } finally {
      openingReviewRef.current = false;
      setOpeningReview(false);
    }
  };
  const draftVersions = templates.flatMap((template) => template.versions
    .filter((version) => version.status === "DRAFT")
    .map((version) => ({ templateName: template.name, version })));
  const activeVersions = templates.flatMap((template) => template.versions
    .filter((version) => version.status === "ACTIVE")
    .map((version) => ({ templateName: template.name, version })));
  const requiredCountValid = mode === "MULTI_SCAN_CONSENSUS" ? files.length >= 3 : files.length === 1;
  const duplicateTemplate = templates.find(
    (template) => normalizeTemplateName(template.name) === normalizeTemplateName(name),
  );
  const submissionLocked = Boolean(jobId)
    && (job?.id !== jobId || !["FAILED", "CANCELLED", "INTERRUPTED"].includes(job.status));

  if (review) {
    return <TemplateReview version={review.version} jobId={review.jobId} onNavigationGuardChange={onNavigationGuardChange} onClose={() => { setReview(null); setJobId(null); setMessage(null); void refresh().catch(() => undefined); }} onReprocess={(nextJobId) => { setReview(null); setJobId(nextJobId); setMessage(null); rememberJob(nextJobId); }} />;
  }
  return (
    <div className="page-stack narrow">
      <header className="page-header"><span className="eyebrow">TEMPLATE PREPARATION</span><h1>帳票の「型」を準備する</h1><p>罫線や印刷文字だけの基準画像を作り、読み取り位置を安定させます。</p></header>
      {draftVersions.length > 0 && (
        <section className="resume-strip draft-resume-strip"><div><strong>確認待ちのテンプレートがあります</strong><span>{draftVersions.length}件の下書き</span></div><div className="draft-resume-actions">{draftVersions.map(({ templateName, version }) => {
          const label = draftResumeLabel(templateName, version);
          return <button key={version.id} className="secondary compact" aria-label={label} disabled={!version.sourceJobId} onClick={() => version.sourceJobId && setReview({ version, jobId: version.sourceJobId })}>{label}</button>;
        })}</div></section>
      )}
      {activeVersions.length > 0 && (
        <section className="resume-strip active-template-strip"><div><strong>有効テンプレートの読取項目</strong><span>矩形とJSON項目の対応を編集できます</span></div>{activeVersions.map(({ templateName, version }) => <button key={version.id} className="secondary compact" disabled={!version.sourceJobId} onClick={() => version.sourceJobId && setReview({ version, jobId: version.sourceJobId })}>{templateName} v{version.versionNumber} · {version.id.slice(0, 8)} を設定</button>)}</section>
      )}
      <section className="form-card">
        <label className="field-label">テンプレート名<input value={name} maxLength={200} onChange={(event) => {
          idempotencyKeyRef.current = null;
          setName(event.target.value);
        }} /></label>
        {duplicateTemplate && (
          <p className="inline-warning">
            「{duplicateTemplate.name}」は既に存在します。読み取り時に区別できる別の名前を指定してください。
          </p>
        )}
        <fieldset><legend>準備方法</legend><div className="mode-grid">{MODES.map((item) => (
          <button type="button" key={item.id} className={`mode-card ${mode === item.id ? "selected" : ""}`} onClick={() => {
            idempotencyKeyRef.current = null;
            setMode(item.id);
            setFiles([]);
          }}>
            <span className="mode-number">{item.number}</span><strong>{item.title}</strong><p>{item.copy}</p><small>{item.count}</small>
          </button>
        ))}</div></fieldset>
        <FilePicker key={mode} multiple={mode === "MULTI_SCAN_CONSENSUS"} onChange={(nextFiles) => {
          idempotencyKeyRef.current = null;
          setFiles(nextFiles);
        }} />
        {files.length > 0 && !requiredCountValid && <p className="inline-warning">複数スキャン方式には3枚以上の画像が必要です。</p>}
        {message && <div className="error-box action-message" role="alert"><span>{message}</span>{job?.status === "REVIEW_REQUIRED" && <button type="button" className="secondary compact" disabled={openingReview} onClick={() => void openPreparedTemplate(job)}>{openingReview ? "再取得中…" : "確認画面を再取得"}</button>}</div>}
        <button className="primary full" disabled={busy || submissionLocked || !name.trim() || !requiredCountValid || Boolean(duplicateTemplate)} onClick={async () => {
          if (creatingRef.current || submissionLocked) return;
          creatingRef.current = true;
          const idempotencyKey = idempotencyKeyRef.current
            ?? createIdempotencyKey("template");
          idempotencyKeyRef.current = idempotencyKey;
          setBusy(true); setMessage(null);
          try {
            const created = await api.createTemplate(name, mode, files, idempotencyKey);
            idempotencyKeyRef.current = null;
            setJobId(created.jobId);
            rememberJob(created.jobId);
          }
          catch (error) { setMessage(errorMessage(error, "開始できませんでした。")); }
          finally { creatingRef.current = false; setBusy(false); }
        }}>{busy ? "送信中…" : submissionLocked ? "テンプレート準備を開始しました" : "テンプレート準備を開始"}</button>
      </section>
      <JobProgress job={job} connectionLost={connectionLost} />
    </div>
  );
}

type ReviewFeedback = { kind: "success" | "error"; text: string };
type DecisionAction = "reject" | "reprocess" | "approve";

function TemplateReview({
  version,
  jobId,
  onClose,
  onReprocess,
  onNavigationGuardChange,
}: {
  version: TemplateVersion;
  jobId: string;
  onClose: () => void;
  onReprocess: (jobId: string) => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [mappingConfig, setMappingConfig] = useState<FieldMappingsResponse | null>(null);
  const [layoutConfig, setLayoutConfig] = useState<LayoutDefinition | null>(null);
  const [layer, setLayer] = useState("layoutVerificationOverlay");
  const [layoutVerified, setLayoutVerified] = useState(version.quality.layoutVerified === true);
  const [verifying, setVerifying] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [editorBusy, setEditorBusy] = useState(false);
  const [maskDirty, setMaskDirty] = useState(false);
  const [maskBusy, setMaskBusy] = useState(false);
  const [maskRequiresReprocess, setMaskRequiresReprocess] = useState(
    version.status === "DRAFT"
      && Boolean(version.quality.editedMaskSha256)
      && version.quality.layoutVerified !== true,
  );
  const [mappingFresh, setMappingFresh] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [feedback, setFeedback] = useState<ReviewFeedback | null>(null);
  const [decisionBusy, setDecisionBusy] = useState<DecisionAction | null>(null);
  const decisionLockRef = useRef(false);
  const verificationLockRef = useRef(false);
  useEffect(() => {
    let cancelled = false;
    setLoadFailed(false);
    setFeedback(null);
    void Promise.all([api.artifacts(jobId), api.fieldMappings(version.id), api.layout(version.id)])
      .then(([artifactResponse, mappings, layout]) => {
        if (cancelled) return;
        setArtifacts(artifactResponse.items);
        setMappingConfig(mappings);
        setLayoutConfig(layout);
        setMappingFresh(true);
      })
      .catch((error) => {
        if (cancelled) return;
        setLoadFailed(true);
        setFeedback({ kind: "error", text: errorMessage(error, "設定を読み込めませんでした。") });
      });
    return () => { cancelled = true; };
  }, [jobId, loadAttempt, version.id]);
  const navigationRisk = (
    editorDirty
    || editorBusy
    || maskDirty
    || maskBusy
    || verifying
    || decisionBusy !== null
  );
  useEffect(() => {
    onNavigationGuardChange?.(navigationRisk);
  }, [navigationRisk, onNavigationGuardChange]);
  useEffect(
    () => () => onNavigationGuardChange?.(false),
    [onNavigationGuardChange],
  );
  const byName = useMemo(() => new Map(artifacts.map((item) => [item.name, item.url])), [artifacts]);
  const before = byName.get("alignedSource.1") ?? byName.get("normalized") ?? byName.get("cleanTemplate") ?? "";
  const after = byName.get("cleanPreview") ?? byName.get("cleanTemplate") ?? before;
  const mask = layoutVerified ? byName.get("handwritingMask") : undefined;
  const layers = ["layoutVerificationOverlay", "debugOverlay", "handwritingOnly", "handwritingProbability"].filter((name) => byName.has(name));
  const meaningSaved = mappingFresh && Boolean(mappingConfig?.validForOcr);
  const effectiveLayoutVerified = layoutVerified && !editorDirty && mappingFresh;
  const operationBusy = editorBusy || maskBusy || verifying || decisionBusy !== null;
  const closeReview = () => {
    if (operationBusy) return;
    if (
      (editorDirty || maskDirty)
      && !window.confirm(
        "保存していないテンプレート変更があります。変更を破棄して一覧へ戻りますか？",
      )
    ) return;
    onClose();
  };
  const beginDecision = (action: DecisionAction) => {
    if (decisionLockRef.current) return false;
    decisionLockRef.current = true;
    setDecisionBusy(action);
    setFeedback(null);
    return true;
  };
  const endDecision = () => {
    decisionLockRef.current = false;
    setDecisionBusy(null);
  };
  const rejectVersion = async () => {
    if (!window.confirm("この下書きを却下しますか？却下後は確認を再開できません。") || !beginDecision("reject")) return;
    try {
      await api.reject(version.id);
      onClose();
    } catch (error) {
      setFeedback({ kind: "error", text: errorMessage(error, "テンプレートを却下できませんでした。") });
    } finally {
      endDecision();
    }
  };
  const reprocessVersion = async () => {
    if ((!effectiveLayoutVerified && !maskRequiresReprocess) || !meaningSaved || editorBusy || maskDirty || maskBusy || !beginDecision("reprocess")) return;
    try {
      const next = await api.reprocess(version.id);
      onReprocess(next.jobId);
    } catch (error) {
      setFeedback({ kind: "error", text: errorMessage(error, "テンプレートを再生成できませんでした。") });
    } finally {
      endDecision();
    }
  };
  const approveVersion = async () => {
    if (!effectiveLayoutVerified || !meaningSaved || maskRequiresReprocess || editorBusy || maskDirty || maskBusy || !beginDecision("approve")) return;
    try {
      await api.approve(version.id);
      onClose();
    } catch (error) {
      setFeedback({ kind: "error", text: errorMessage(error, "テンプレートを承認できませんでした。") });
    } finally {
      endDecision();
    }
  };
  return (
    <div className="page-stack">
      <header className="page-header split"><div><span className="eyebrow">TEMPLATE {version.status === "ACTIVE" ? "FIELD SETTINGS" : "REVIEW"} · V{version.versionNumber}</span><h1>{version.status === "ACTIVE" ? "読み取る欄を設定" : "復元結果を確認"}</h1><p>矩形とJSON項目の対応はGUIで固定します。LLMは矩形内の文字の転記だけを担当します。</p></div><button className="ghost" disabled={operationBusy} onClick={closeReview}>← 一覧へ戻る</button></header>
      <ol className="processing-order" aria-label="帳票処理の順番">
        <li className="done"><strong>1. 歪み補正</strong><span>表示前に透視変換</span></li>
        <li className={meaningSaved ? "done" : "current"}><strong>2. 矩形の分類と意味づけ</strong><span>手書き文字欄・ラベル・保存先を確定</span></li>
        <li className={effectiveLayoutVerified ? "done" : meaningSaved ? "current" : ""}><strong>3. 記入欄の再検出</strong><span>OpenCV中心で候補を実検証</span></li>
      </ol>
      {loadFailed && (
        <div className="error-box action-message" role="alert">
          <span>テンプレート設定を最後まで読み込めませんでした。</span>
          <button type="button" className="secondary compact" onClick={() => setLoadAttempt((current) => current + 1)}>
            読み込みを再試行
          </button>
        </div>
      )}
      {mappingConfig?.warnings.map((warning) => (
        <p className="error-box" key={warning}>{warning}</p>
      ))}
      {effectiveLayoutVerified && <div className="review-layout">
        <section className="viewer-card">
          {before && after ? <ComparisonViewer before={before} after={after} /> : <div className="empty-state">画像を読み込んでいます…</div>}
          <div className="layer-tabs">{layers.map((name) => <button key={name} className={layer === name ? "active" : ""} onClick={() => setLayer(name)}>{layerLabel(name)}</button>)}</div>
          {byName.get(layer) && <div className="single-image-view"><img src={byName.get(layer)} alt={layerLabel(layer)} /></div>}
        </section>
        <aside className="quality-card"><span className="panel-kicker">品質メモ</span><h3>自動点検の結果</h3>{Object.entries(version.quality).slice(0, 8).map(([key, value]) => <div className="metric" key={key}><span>{key}</span><strong>{typeof value === "number" ? value.toFixed(3) : String(value)}</strong></div>)}<p>数値は正解確率ではありません。比較画像を優先して確認してください。</p></aside>
      </div>}
      {mappingConfig && layoutConfig && before && <section className="form-card mapping-section"><div className="section-title"><div><span className="eyebrow">FORM LAYOUT V2</span><h2>帳票セルの検出と意味づけ</h2><p>橙はラベル、青緑は手書き文字欄です。歪み補正済みの元画像上で分類し、保存先までここで確定します。</p></div></div>{maskDirty && <p className="inline-warning">先に文字候補の選別結果を保存してください。</p>}<LayoutEditor source={before} initialLayout={layoutConfig} targets={mappingConfig.targets} allowUnchangedSave={!mappingConfig.validForOcr} disabled={maskDirty || maskBusy} onDirtyChange={setEditorDirty} onBusyChange={setEditorBusy} onDetect={async () => {
        return api.detectLayout(version.id);
      }} onSave={async (layout) => {
        const saved = await api.saveLayout(version.id, layout);
        setLayoutConfig(saved);
        setLayoutVerified(false);
        setMappingFresh(false);
        try {
          const mappings = await api.fieldMappings(version.id);
          setMappingConfig(mappings);
          setMappingFresh(true);
          setFeedback({ kind: "success", text: `レイアウト${saved.cells.length}セル、意味づけ${saved.relations.length}件を保存しました。次に記入欄を再検出してください。` });
        } catch (error) {
          setFeedback({
            kind: "error",
            text: `レイアウトは保存されましたが、保存状態を再取得できませんでした。画面を閉じて開き直してください。${errorMessage(error, "")}`,
          });
        }
        return saved;
      }} /></section>}
      {meaningSaved && !effectiveLayoutVerified && <section className="form-card verification-gate"><div><span className="eyebrow">LAYOUT VERIFICATION</span><h2>記入欄を実データで再検出</h2><p>{editorDirty ? "先に未保存のレイアウト変更を保存してください。" : "保存した矩形だけを対象にOpenCVでセル候補を再検出します。任意の文字認識機能がある場合は候補を補助します。"}</p></div><button className="primary" type="button" disabled={verifying || editorBusy || editorDirty} onClick={async () => {
        if (verificationLockRef.current || editorDirty || editorBusy) return;
        verificationLockRef.current = true;
        setVerifying(true);
        setFeedback(null);
        try {
          const verified = await api.verifyLayout(version.id);
          setLayoutConfig(verified);
          const refreshedArtifacts = await api.artifacts(jobId);
          setArtifacts(refreshedArtifacts.items);
          setLayer("layoutVerificationOverlay");
          setLayoutVerified(true);
          setFeedback({ kind: "success", text: `${verified.relations.length}件の意味づけ対象を再検出しました。` });
        } catch (error) {
          setFeedback({ kind: "error", text: errorMessage(error, "記入欄を再検出できませんでした。") });
        } finally {
          setVerifying(false);
          verificationLockRef.current = false;
        }
      }}>{verifying ? "再検出中…" : "記入欄を再検出"}</button></section>}
      {mask && layoutVerified && mappingFresh && <section className="form-card"><div className="section-title"><div><span className="eyebrow">DETECTION REVIEW</span><h2>再検出した文字候補の選別</h2><p>保存済みの記入欄内をOpenCVで再検証した候補です。任意の文字認識機能がある場合は候補を補助します。</p></div></div>{editorDirty && <p className="inline-warning">先にレイアウト変更を保存し、記入欄を再検出してください。</p>}<MaskEditor source={mask} background={before} candidatesSource={byName.get("handwritingCandidates")} disabled={editorDirty || editorBusy} onDirtyChange={setMaskDirty} onBusyChange={setMaskBusy} onSave={async (blob) => {
        await api.saveMask(version.id, blob);
        if (version.status === "DRAFT") {
          setMaskRequiresReprocess(true);
          setFeedback({ kind: "success", text: "選別結果を保存しました。「選別マスクから再生成」で新しい下書きへ反映してください。" });
        } else {
          setLayoutVerified(false);
          setFeedback({ kind: "success", text: "選別結果を保存しました。記入欄を再検出すると、次のOCRから反映されます。" });
        }
      }} /></section>}
      {feedback && <p className={feedback.kind === "error" ? "error-box" : "success-box"} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.text}</p>}
      {version.status === "DRAFT" ? <div className="decision-bar"><div><strong>このテンプレート設定を採用しますか？</strong><span>{editorDirty || maskDirty ? "未保存の変更があります。保存してから次へ進んでください。" : maskRequiresReprocess ? "保存した選別マスクから新しい下書きを再生成してください。" : "意味づけを保存し、記入欄の再検出を完了してから承認してください。"}</span></div><button className="danger-button" disabled={operationBusy} onClick={() => void rejectVersion()}>{decisionBusy === "reject" ? "却下中…" : "却下"}</button><button className="secondary" disabled={operationBusy || maskDirty || (!effectiveLayoutVerified && !maskRequiresReprocess) || !meaningSaved} onClick={() => void reprocessVersion()}>{decisionBusy === "reprocess" ? "再生成中…" : "選別マスクから再生成"}</button><button className="primary" disabled={operationBusy || maskDirty || maskRequiresReprocess || !effectiveLayoutVerified || !meaningSaved} onClick={() => void approveVersion()}>{decisionBusy === "approve" ? "承認中…" : "承認して有効化"}</button></div> : <div className="decision-bar"><div><strong>有効テンプレートを編集中</strong><span>{editorDirty || maskDirty ? "未保存の変更があります。" : "保存済みの矩形設定は次に開始するOCRから使われます。"}</span></div><button className="primary" disabled={operationBusy} onClick={closeReview}>設定を閉じる</button></div>}
    </div>
  );
}

function layerLabel(name: string) {
  return ({ layoutVerificationOverlay: "矩形再検出", debugOverlay: "文字候補", handwritingOnly: "候補文字のみ", handwritingProbability: "候補確率" } as Record<string, string>)[name] ?? name;
}

function templateModeLabel(mode: string) {
  return MODES.find((item) => item.id === mode)?.title ?? mode;
}

function draftResumeLabel(templateName: string, version: TemplateVersion) {
  return `確認を再開: ${templateName} v${version.versionNumber} · ${templateModeLabel(version.mode)} · ${formatTemplateDateTime(version.createdAt)} · ID ${version.id.slice(0, 8)}`;
}

function formatTemplateDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("ja-JP", {
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
}

function errorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return fallback;
}

function normalizeTemplateName(name: string) {
  return name.normalize("NFKC").trim().toLocaleLowerCase();
}
