import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, createIdempotencyKey } from "../api";
import { ComparisonViewer } from "../components/ComparisonViewer";
import { DocumentBoundaryEditor } from "../components/DocumentBoundaryEditor";
import { FilePicker } from "../components/FilePicker";
import { JobProgress } from "../components/JobProgress";
import { LayoutEditor, type LayoutEditorHandle, type LayoutWorkflowStep } from "../components/LayoutEditor";
import { MaskEditor } from "../components/MaskEditor";
import { TemplateCandidateSettingsEditor } from "../components/TemplateCandidateSettingsEditor";
import { showToast } from "../components/Toast";
import { pickGoogleDriveFolder } from "../googlePicker";
import { useJobPoller } from "../hooks/useJobPoller";
import type { Artifact, DocumentIdentity, DocumentNameRule, FieldMappingsResponse, Job, LayoutDefinition, Template, TemplateVersion } from "../types";

const MODES = [
  { id: "SINGLE_IMAGE_EXPERIMENTAL", number: "01", title: "記入済みの一枚から", copy: "記入済み台帳1枚を補正し、矩形と読み取り欄を画面で設定します。", count: "記入済み・1枚" },
  { id: "REFERENCE_TEMPLATE", number: "02", title: "白紙帳票から", copy: "記入前の白紙帳票を基準画像として使用します。", count: "白紙・1枚" },
  { id: "SPREADSHEET_TEMPLATE", number: "03", title: "元のExcelから", copy: "XLSXのセル、罫線、結合から矩形と区分の案を自動作成します。", count: "XLSX・1ファイル" },
] as const;

const LAYOUT_WIZARD_STEPS: Array<{
  id: LayoutWorkflowStep;
  title: string;
  shortTitle: string;
  description: string;
}> = [
  { id: "classify", shortTitle: "欄の種類", title: "検出された枠の種類を確認する", description: "書類から検出された枠を、項目名、記入欄、選択欄、見出し、読み取らない欄に分けます。項目名の文字候補もここで確認できます。" },
  { id: "meaning", shortTitle: "読み取る内容", title: "各欄で読み取る内容を決める", description: "記入欄と保存する情報を対応づけます。項目名のない記入欄には、保存する情報を直接指定できます。" },
  { id: "records", shortTitle: "繰り返し部分", title: "繰り返し部分をまとめる", description: "明細表など、同じ構造を繰り返す欄をひとまとまりとして設定します。" },
  { id: "identity", shortTitle: "照合と書類名", title: "同じ書類を探す条件と書類名を決める", description: "複数の欄を氏名・住所などの照合グループへまとめ、ライブラリと出力で共用する書類名を設定します。" },
  { id: "review", shortTitle: "確認・保存", title: "設定を確認して保存する", description: "未割当とレコード構造を確認し、テンプレート設定を保存します。" },
];

type TemplateWorkspace = "create" | "manage";
type TemplateListFilter = "current" | "draft" | "active" | "history";

export function TemplatePage({
  templates,
  refresh,
  rememberJob,
  onNavigationGuardChange,
  initialWorkspace,
  initialEditVersionId,
  onClose,
  onTemplateActivated,
}: {
  templates: Template[];
  refresh: () => Promise<void>;
  rememberJob: (id: string) => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
  initialWorkspace?: TemplateWorkspace;
  initialEditVersionId?: string;
  onClose?: () => void;
  onTemplateActivated?: (versionId: string) => void;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("SINGLE_IMAGE_EXPERIMENTAL");
  const [name, setName] = useState("");
  const [fieldNames, setFieldNames] = useState<string[]>([]);
  const [fieldNameDraft, setFieldNameDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobPollRevision, setJobPollRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [openingReview, setOpeningReview] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<TemplateWorkspace | null>(initialWorkspace ?? null);
  const [templateQuery, setTemplateQuery] = useState("");
  const [templateFilter, setTemplateFilter] = useState<TemplateListFilter>("current");
  const [deletingTemplateId, setDeletingTemplateId] = useState<string | null>(null);
  const [changingOutputTemplateId, setChangingOutputTemplateId] = useState<string | null>(null);
  const [candidateEditor, setCandidateEditor] = useState<{
    template: Template;
    version: TemplateVersion;
  } | null>(null);
  const [review, setReview] = useState<{
    version: TemplateVersion;
    jobId: string;
    origin: TemplateWorkspace;
    returnToReading?: boolean;
  } | null>(() => {
    if (!initialEditVersionId) return null;
    const version = templates
      .flatMap((template) => template.versions)
      .find((candidate) => candidate.id === initialEditVersionId);
    if (!version?.sourceJobId) return null;
    return {
      version,
      jobId: version.sourceJobId,
      origin: "manage",
      returnToReading: true,
    };
  });
  const creatingRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);
  const openingReviewRef = useRef(false);
  const { job, connectionLost } = useJobPoller(jobId, (terminal) => {
    if (terminal.status === "REVIEW_REQUIRED") void openPreparedTemplate(terminal);
  }, jobPollRevision);

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
      setReview({ version, jobId: terminal.id, origin: "create" });
    } catch (error) {
      setMessage(errorMessage(error, "確認画面を開けませんでした。"));
    } finally {
      openingReviewRef.current = false;
      setOpeningReview(false);
    }
  };
  const selectedWorkspace = workspace ?? (templates.length > 0 ? "manage" : "create");
  const listedTemplates = useMemo(() => {
    const query = normalizeTemplateName(templateQuery);
    return templates
      .map((template) => ({
        template,
        versions: [...template.versions].sort((first, second) => (
          second.versionNumber - first.versionNumber
          || Date.parse(second.createdAt) - Date.parse(first.createdAt)
        )),
      }))
      .filter(({ template, versions }) => {
        if (query && !normalizeTemplateName(template.name).includes(query)) return false;
        const hasDraft = versions.some((version) => version.status === "DRAFT");
        const hasActive = versions.some((version) => version.status === "ACTIVE");
        if (templateFilter === "draft") return hasDraft;
        if (templateFilter === "active") return hasActive;
        if (templateFilter === "history") return true;
        return hasDraft || hasActive;
      })
      .sort((first, second) => {
        const firstDraft = first.versions.some((version) => version.status === "DRAFT");
        const secondDraft = second.versions.some((version) => version.status === "DRAFT");
        if (firstDraft !== secondDraft) return firstDraft ? -1 : 1;
        const firstDate = Date.parse(first.versions[0]?.createdAt ?? first.template.createdAt);
        const secondDate = Date.parse(second.versions[0]?.createdAt ?? second.template.createdAt);
        return secondDate - firstDate;
      });
  }, [templateFilter, templateQuery, templates]);
  const draftCount = templates.filter((template) => (
    template.versions.some((version) => version.status === "DRAFT")
  )).length;
  const requiredCountValid = files.length === 1;
  const templateName = name.trim();
  const normalizedFieldNameDraft = normalizeTemplateName(fieldNameDraft);
  const duplicateFieldName = normalizedFieldNameDraft
    ? fieldNames.find((item) => normalizeTemplateName(item) === normalizedFieldNameDraft) ?? null
    : null;
  const duplicateTemplate = templateName
    ? templates.find((template) => normalizeTemplateName(template.name) === normalizeTemplateName(templateName))
    : null;
  const submissionLocked = Boolean(jobId)
    && (job?.id !== jobId || !["FAILED", "CANCELLED", "INTERRUPTED"].includes(job.status));
  const creationNavigationRisk = !review && !candidateEditor && (
    busy
    || submissionLocked
    || name.trim().length > 0
    || fieldNameDraft.trim().length > 0
    || fieldNames.length > 0
    || files.length > 0
  );
  useEffect(() => {
    if (review || candidateEditor) return;
    onNavigationGuardChange?.(creationNavigationRisk);
  }, [candidateEditor, creationNavigationRisk, onNavigationGuardChange, review]);
  useEffect(
    () => () => onNavigationGuardChange?.(false),
    [onNavigationGuardChange],
  );
  const closeWorkspace = () => {
    if (
      creationNavigationRisk
      && !window.confirm("入力途中のテンプレートがあります。内容を破棄して読み取りへ戻りますか？")
    ) return;
    onClose?.();
  };
  const addFieldName = () => {
    const nextName = fieldNameDraft.normalize("NFKC").trim().replace(/\s+/g, " ");
    if (!nextName || duplicateFieldName || fieldNames.length >= 200) return;
    setFieldNames((current) => [...current, nextName]);
    setFieldNameDraft("");
    idempotencyKeyRef.current = null;
  };
  if (review) {
    return <TemplateReview
      version={review.version}
      jobId={review.jobId}
      closeLabel={review.returnToReading ? "← 読み取りへ戻る" : undefined}
      onNavigationGuardChange={onNavigationGuardChange}
      onClose={() => {
        const returnToReading = review.returnToReading;
        setWorkspace(review.origin);
        setReview(null);
        setJobId(null);
        setMessage(null);
        void refresh().catch(() => undefined).finally(() => {
          if (returnToReading) onClose?.();
        });
      }}
      onApproved={async (versionId) => {
        if (!onTemplateActivated) return false;
        await refresh();
        onTemplateActivated(versionId);
        return true;
      }}
      onReprocess={(nextJobId) => {
        setWorkspace(review.origin);
        setReview(null);
        setJobId(nextJobId);
        setMessage(null);
        rememberJob(nextJobId);
      }}
    />;
  }
  if (candidateEditor) {
    return <TemplateCandidateSettingsEditor
      template={candidateEditor.template}
      version={candidateEditor.version}
      onNavigationGuardChange={onNavigationGuardChange}
      onClose={() => {
        setWorkspace("manage");
        setCandidateEditor(null);
      }}
    />;
  }
  const deleteTemplate = async (template: Template) => {
    if (deletingTemplateId || !window.confirm(
      `「${template.name}」を読み取り一覧から削除しますか？\n\n未使用の場合は完全に削除され、既存書類で使用中の場合は履歴を保つためアーカイブされます。`,
    )) {
      return;
    }
    const deletingId = template.id;
    const templateName = template.name;
    setDeletingTemplateId(deletingId);
    setMessage(null);
    try {
      const result = await api.deleteTemplate(deletingId);
      await refresh();
      showToast({
        tone: "success",
        title: result.outcome === "archived"
          ? "テンプレートを読み取り一覧から削除しました"
          : "テンプレートを削除しました",
        message: result.outcome === "archived"
          ? "既存書類の履歴を保つため、テンプレート本体はアーカイブされています。"
          : `「${templateName}」と保存済み設定を削除しました。`,
      });
    } catch (error) {
      setMessage(errorMessage(error, "テンプレートを削除できませんでした。"));
    } finally {
      setDeletingTemplateId((current) => (current === deletingId ? null : current));
    }
  };
  const changeTemplateOutputFolder = async (template: Template) => {
    if (changingOutputTemplateId) return;
    setChangingOutputTemplateId(template.id);
    try {
      const [current, pickerSession] = await Promise.all([
        api.templateOutputFolder(template.id),
        api.drivePickerSession(),
      ]);
      const picked = await pickGoogleDriveFolder(
        pickerSession,
        current.effectiveFolderId,
      );
      if (!picked) return;
      const selected = await api.selectTemplateOutputFolder(template.id, picked.id);
      showToast({
        tone: "success",
        title: "テンプレートの出力先を更新しました",
        message: `${template.name}: ${selected.effectiveFolderName ?? picked.name}`,
      });
    } catch (error) {
      showToast({
        tone: "error",
        title: "出力先フォルダを変更できませんでした",
        message: errorMessage(error, "Google Driveとの接続を確認してください。"),
      });
    } finally {
      setChangingOutputTemplateId(null);
    }
  };
  return (
    <div className="page-stack narrow">
      <header className="page-header split">
        <div><span className="eyebrow">READING / TEMPLATE MANAGEMENT</span><h1>テンプレート作成・編集</h1><p>新しい帳票の型を作成するか、既存の設定を確認・修正します。</p></div>
        {onClose && <button type="button" className="ghost" onClick={closeWorkspace}>← 読み取りへ戻る</button>}
      </header>
      {message && <div className="error-box action-message" role="alert"><span>{message}</span>{job?.status === "REVIEW_REQUIRED" && <button type="button" className="secondary compact" disabled={openingReview} onClick={() => void openPreparedTemplate(job)}>{openingReview ? "再取得中…" : "確認画面を再取得"}</button>}</div>}
      <div
        className="template-workspace-tabs"
        role="tablist"
        aria-label="テンプレートの作業を選択"
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const nextWorkspace: TemplateWorkspace = event.key === "ArrowLeft" || event.key === "Home"
            ? "create"
            : "manage";
          setWorkspace(nextWorkspace);
          requestAnimationFrame(() => document.getElementById(
            `template-workspace-${nextWorkspace}-tab`,
          )?.focus());
        }}
      >
        <button
          type="button"
          id="template-workspace-create-tab"
          role="tab"
          aria-selected={selectedWorkspace === "create"}
          aria-controls="template-workspace-create-panel"
          tabIndex={selectedWorkspace === "create" ? 0 : -1}
          disabled={busy || submissionLocked || openingReview}
          onClick={() => setWorkspace("create")}
        >
          新規作成
        </button>
        <button
          type="button"
          id="template-workspace-manage-tab"
          role="tab"
          aria-selected={selectedWorkspace === "manage"}
          aria-controls="template-workspace-manage-panel"
          tabIndex={selectedWorkspace === "manage" ? 0 : -1}
          disabled={busy || submissionLocked || openingReview}
          onClick={() => setWorkspace("manage")}
        >
          <span>既存を確認・修正</span>
          <small>{templates.length}件</small>
        </button>
      </div>

      <section
        id="template-workspace-create-panel"
        className="template-workspace-panel"
        role="tabpanel"
        aria-labelledby="template-workspace-create-tab"
        hidden={selectedWorkspace !== "create"}
      >
        <div className="template-panel-heading">
          <div><span className="eyebrow">NEW TEMPLATE</span><h2>新しいテンプレートを作成</h2></div>
          <p>帳票画像から、読み取り位置と保存する情報を設定します。</p>
        </div>
        <section className="form-card">
          <label className="field-label">テンプレート名<input value={name} required maxLength={200} placeholder="テンプレート名を入力してください" onChange={(event) => {
            idempotencyKeyRef.current = null;
            setName(event.target.value);
          }} /></label>
          {duplicateTemplate && (
            <div className="inline-warning duplicate-template-warning">
              <span>「{duplicateTemplate.name}」は既に存在します。別の名前を指定するか、既存の設定を確認してください。</span>
              <button type="button" className="secondary compact" onClick={() => {
                setTemplateQuery(duplicateTemplate.name);
                setTemplateFilter("current");
                setWorkspace("manage");
              }}>既存テンプレートを確認</button>
            </div>
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
          <FilePicker
            key={mode}
            multiple={false}
            label={mode === "SPREADSHEET_TEMPLATE" ? "XLSXを選択" : "画像を選択"}
            accept={mode === "SPREADSHEET_TEMPLATE"
              ? ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              : "image/png,image/jpeg,image/webp,image/tiff"}
            help={mode === "SPREADSHEET_TEMPLATE"
              ? "XLSX・最初の表示シートからテンプレート案を作成"
              : "PNG / JPEG / WebP / TIFF・最大20 MB"}
            onChange={(nextFiles) => {
            idempotencyKeyRef.current = null;
            setFiles(nextFiles);
          }} />
          <button className="primary full" disabled={busy || submissionLocked || !templateName || !requiredCountValid || Boolean(duplicateTemplate)} onClick={async () => {
            if (creatingRef.current || submissionLocked) return;
            creatingRef.current = true;
            const idempotencyKey = idempotencyKeyRef.current
              ?? createIdempotencyKey("template");
            idempotencyKeyRef.current = idempotencyKey;
            setBusy(true); setMessage(null);
            try {
              const created = mode === "SPREADSHEET_TEMPLATE"
                ? await api.createSpreadsheetTemplate(templateName, files[0], idempotencyKey)
                : await api.createTemplate(
                  templateName,
                  mode,
                  files,
                  idempotencyKey,
                  fieldNames,
                );
              idempotencyKeyRef.current = null;
              setJobId(created.jobId);
              rememberJob(created.jobId);
            }
            catch (error) { setMessage(errorMessage(error, "開始できませんでした。")); }
            finally { creatingRef.current = false; setBusy(false); }
          }}>{busy ? "送信中…" : submissionLocked ? "テンプレート準備を開始しました" : "テンプレート準備を開始"}</button>
        </section>
        <JobProgress job={job} connectionLost={connectionLost} />
        {job?.status === "FAILED" && job.error?.code === "DOCUMENT_BOUNDARY_NOT_FOUND" && (
          <DocumentBoundaryEditor
            jobId={job.id}
            onRetry={() => {
              setMessage(null);
              setJobPollRevision((current) => current + 1);
            }}
          />
        )}
      </section>

      <section
        id="template-workspace-manage-panel"
        className="template-workspace-panel"
        role="tabpanel"
        aria-labelledby="template-workspace-manage-tab"
        hidden={selectedWorkspace !== "manage"}
      >
        <div className="template-panel-heading template-manage-heading">
          <div><span className="eyebrow">EXISTING TEMPLATES</span><h2>確認・修正するテンプレートを選択</h2></div>
          <p>{draftCount > 0 ? `${draftCount}件の作業途中テンプレートがあります。` : "作業途中のテンプレートはありません。"}</p>
        </div>
        <div className="template-list-toolbar">
          <label className="field-label">テンプレートを検索<input type="search" value={templateQuery} placeholder="名前で検索" onChange={(event) => setTemplateQuery(event.target.value)} /></label>
          <label className="field-label">表示する状態<select value={templateFilter} onChange={(event) => setTemplateFilter(event.target.value as TemplateListFilter)}>
            <option value="current">作業途中・使用中</option>
            <option value="draft">作業途中のみ</option>
            <option value="active">使用中のみ</option>
            <option value="history">過去を含むすべて</option>
          </select></label>
        </div>
        <div className="template-management-list" aria-label="既存テンプレート一覧">
          {listedTemplates.map(({ template, versions }) => {
            const draft = versions.find((version) => version.status === "DRAFT") ?? null;
            const active = versions.find((version) => version.status === "ACTIVE") ?? null;
            const primaryVersion = draft ?? active;
            const status = draft ? "draft" : active ? "active" : "history";
            const statusLabel = draft ? "作業途中" : active ? "使用中" : "過去のテンプレート";
            const isDeletingCurrentTemplate = deletingTemplateId === template.id;
            return (
              <article className="template-management-card" key={template.id}>
                <div className="template-management-main">
                  <div className="template-management-title">
                    <span className={`template-version-status ${status}`}>{statusLabel}</span>
                    <h3>{template.name}</h3>
                  </div>
                  {primaryVersion ? (
                    <p>v{primaryVersion.versionNumber} · {templateModeLabel(primaryVersion.mode)} · 更新 {formatTemplateDateTime(primaryVersion.createdAt)}</p>
                  ) : (
                    <p>現在利用できるバージョンはありません。</p>
                  )}
                </div>
                <div className="template-management-action">
                  {primaryVersion && (
                    <button
                      type="button"
                      className={draft ? "primary compact" : "secondary compact"}
                      aria-label={draft ? draftResumeLabel(template.name, primaryVersion) : `${template.name} v${primaryVersion.versionNumber} の設定を確認・修正`}
                      disabled={!primaryVersion.sourceJobId || isDeletingCurrentTemplate || openingReview}
                      onClick={() => primaryVersion.sourceJobId && setReview({
                        version: primaryVersion,
                        jobId: primaryVersion.sourceJobId,
                        origin: "manage",
                      })}
                    >{draft ? "作成を再開" : "設定を確認・修正"}</button>
                  )}
                  {primaryVersion && (
                    <button
                      type="button"
                      className="secondary compact template-print-button"
                      aria-label={`${template.name} v${primaryVersion.versionNumber}を印刷`}
                      disabled={isDeletingCurrentTemplate || openingReview}
                      onClick={() => {
                        const opened = window.open(
                          api.templatePrintUrl(primaryVersion.id),
                          "_blank",
                          "noopener,noreferrer",
                        );
                        if (!opened) {
                          showToast({
                            tone: "error",
                            title: "印刷画面を開けませんでした",
                            message: "ブラウザーのポップアップ許可を確認してください。",
                          });
                        }
                      }}
                    >
                      <svg aria-hidden="true" viewBox="0 0 24 24">
                        <path d="M7 3h10v5H7zm-2 6h14a3 3 0 0 1 3 3v5h-4v4H6v-4H2v-5a3 3 0 0 1 3-3zm3 6v4h8v-4zm10-3a1 1 0 1 0 0 .01z" />
                      </svg>
                      印刷
                    </button>
                  )}
                  {primaryVersion && (
                    <button
                      type="button"
                      className="secondary compact template-candidate-settings-button"
                      aria-label={`${template.name}の読み取り候補を設定`}
                      disabled={!primaryVersion.sourceJobId || isDeletingCurrentTemplate || openingReview}
                      onClick={() => setCandidateEditor({ template, version: primaryVersion })}
                    >
                      <svg aria-hidden="true" viewBox="0 0 24 24">
                        <path d="M5 5h14v3H5zm0 6h9v3H5zm0 6h6v2H5zm13-6v8m-4-4h8" />
                      </svg>
                      読み取り候補を設定
                    </button>
                  )}
                  <button
                    type="button"
                    className="secondary compact template-output-folder-button"
                    aria-label={`${template.name} の出力先フォルダを変更`}
                    disabled={Boolean(changingOutputTemplateId) || isDeletingCurrentTemplate || openingReview}
                    onClick={() => void changeTemplateOutputFolder(template)}
                  >
                    <svg aria-hidden="true" viewBox="0 0 24 24">
                      <path d="M3 6.5h7l2 2h9v9.5H3z" />
                    </svg>
                    {changingOutputTemplateId === template.id ? "Google Driveを開いています…" : "出力先フォルダを変更"}
                  </button>
                  <button
                    type="button"
                    className="danger-button compact"
                    disabled={Boolean(deletingTemplateId) || openingReview}
                    onClick={() => void deleteTemplate(template)}
                  >
                    {isDeletingCurrentTemplate ? "削除中…" : "削除"}
                  </button>
                  {isDeletingCurrentTemplate && <small>削除処理中…</small>}
                  {!primaryVersion?.sourceJobId && <small>元画像がないため編集できません</small>}
                  {!primaryVersion && <small>編集対象の版がありません</small>}
                </div>
                <details className="template-version-history">
                  <summary>バージョン履歴（{versions.length}件）</summary>
                  <div>{versions.map((version) => (
                    <div className="template-version-row" key={version.id}>
                      <span>v{version.versionNumber}</span>
                      <span>{templateVersionStatusLabel(version.status)}</span>
                      <span>{formatTemplateDateTime(version.createdAt)}</span>
                      {(version.status === "DRAFT" || version.status === "ACTIVE") && version.id !== primaryVersion?.id && (
                        <button
                          type="button"
                          className="ghost compact"
                          aria-label={version.status === "DRAFT"
                            ? draftResumeLabel(template.name, version)
                            : `${template.name} v${version.versionNumber} の設定を確認・修正`}
                          disabled={!version.sourceJobId || isDeletingCurrentTemplate || openingReview}
                          onClick={() => version.sourceJobId && setReview({
                            version,
                            jobId: version.sourceJobId,
                            origin: "manage",
                        })}
                        >開く</button>
                      )}
                    </div>
                  ))}</div>
                </details>
              </article>
            );
          })}
          {listedTemplates.length === 0 && (
            <div className="empty-state template-list-empty">
              <h2>{templates.length === 0 ? "既存テンプレートはありません" : "条件に一致するテンプレートはありません"}</h2>
              <p>{templates.length === 0 ? "新規作成へ切り替えて、最初のテンプレートを作成してください。" : "検索語または表示する状態を変更してください。"}</p>
              {templates.length === 0 && <button type="button" className="primary compact" onClick={() => setWorkspace("create")}>新規作成へ</button>}
            </div>
          )}
        </div>
      </section>
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
  onApproved,
  closeLabel = "← 一覧へ戻る",
  onNavigationGuardChange,
}: {
  version: TemplateVersion;
  jobId: string;
  onClose: () => void;
  onReprocess: (jobId: string) => void;
  onApproved?: (versionId: string) => boolean | Promise<boolean>;
  closeLabel?: string;
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
  const [identityFields, setIdentityFields] = useState<DocumentIdentity[]>([]);
  const [documentNameRule, setDocumentNameRule] = useState<DocumentNameRule | null>(null);
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
  const [layoutWorkflowStep, setLayoutWorkflowStep] = useState<LayoutWorkflowStep>("classify");
  const layoutEditorRef = useRef<LayoutEditorHandle>(null);
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
        setIdentityFields(identityFieldsFromResponse(mappings));
        setDocumentNameRule(documentNameFromResponse(mappings));
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
    || identityFieldsSignature(identityFields) !== identityFieldsSignature(
      mappingConfig ? identityFieldsFromResponse(mappingConfig) : [],
    )
    || documentNameSignature(documentNameRule) !== documentNameSignature(
      mappingConfig ? documentNameFromResponse(mappingConfig) : null,
    )
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
  const isSpreadsheetTemplate = version.mode === "SPREADSHEET_TEMPLATE";
  const layers = ["layoutVerificationOverlay", "debugOverlay", "handwritingOnly", "handwritingProbability"].filter((name) => byName.has(name));
  const persistedIdentityFields = mappingConfig ? identityFieldsFromResponse(mappingConfig) : [];
  const identityReady = identityConfigurationReady(identityFields);
  const documentNameReady = Boolean(documentNameRule?.fieldPaths.length);
  const identityDirty = identityFieldsSignature(identityFields) !== identityFieldsSignature(
    persistedIdentityFields,
  );
  const documentNameDirty = documentNameSignature(documentNameRule) !== documentNameSignature(
    mappingConfig ? documentNameFromResponse(mappingConfig) : null,
  );
  const meaningSaved = mappingFresh
    && Boolean(mappingConfig?.validForOcr)
    && identityConfigurationReady(persistedIdentityFields)
    && Boolean(mappingConfig && documentNameFromResponse(mappingConfig)?.fieldPaths.length);
  const visibleMappingWarnings = mappingWarningsForIdentity(
    mappingConfig?.warnings ?? [],
    identityFields,
    documentNameRule,
  );
  const effectiveLayoutVerified = layoutVerified
    && !editorDirty
    && !identityDirty
    && !documentNameDirty
    && mappingFresh;
  const operationBusy = editorBusy || maskBusy || verifying || decisionBusy !== null;
  const layoutWorkflowIndex = LAYOUT_WIZARD_STEPS.findIndex(
    (step) => step.id === layoutWorkflowStep,
  );
  const activeLayoutStep = LAYOUT_WIZARD_STEPS[layoutWorkflowIndex] ?? LAYOUT_WIZARD_STEPS[0];
  const saveEditorBeforeLeaving = async (): Promise<boolean> => {
    if (!editorDirty) return true;
    const saved = await layoutEditorRef.current?.saveIfNeeded();
    if (saved) return true;
    setFeedback({
      kind: "error",
      text: "編集中の内容を保存できなかったため、画面移動を中止しました。接続状態を確認して再度操作してください。",
    });
    return false;
  };
  const moveLayoutWorkflow = (step: LayoutWorkflowStep) => {
    if (operationBusy || step === layoutWorkflowStep) return;
    if (!editorDirty) {
      setLayoutWorkflowStep(step);
      return;
    }
    void saveEditorBeforeLeaving().then((saved) => {
      if (saved) setLayoutWorkflowStep(step);
    });
  };
  const closeAfterEditorSave = () => {
    if (
      (maskDirty || identityDirty || documentNameDirty)
      && !window.confirm(
        "保存していないテンプレート変更があります。変更を破棄して一覧へ戻りますか？",
      )
    ) return;
    onClose();
  };
  const closeReview = () => {
    if (operationBusy) return;
    if (!editorDirty) {
      closeAfterEditorSave();
      return;
    }
    void saveEditorBeforeLeaving().then((saved) => {
      if (saved) closeAfterEditorSave();
    });
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
      const handled = await onApproved?.(version.id);
      if (!handled) onClose();
    } catch (error) {
      setFeedback({ kind: "error", text: errorMessage(error, "テンプレートを承認できませんでした。") });
    } finally {
      endDecision();
    }
  };
  return (
    <div className="page-stack">
      <header className="page-header split"><div><span className="eyebrow">TEMPLATE {version.status === "ACTIVE" ? "FIELD SETTINGS" : "REVIEW"} · V{version.versionNumber}</span><h1>{version.status === "ACTIVE" ? "読み取る欄を設定" : "復元結果を確認"}</h1><p>検出された枠の役割と、保存する情報の対応を画面上で確定します。文字認識は枠内の文字の転記だけを担当します。</p></div><button className="ghost" disabled={operationBusy} onClick={closeReview}>{closeLabel}</button></header>
      <div className="template-lifecycle-status" aria-label="テンプレート設定の状態">
        <div className="done"><span>前処理</span><strong>画像準備済み</strong></div>
        <div className={meaningSaved ? "done" : "current"}><span>現在</span><strong>{meaningSaved ? "テンプレート設定保存済み" : `テンプレート設定中 · ${activeLayoutStep.shortTitle}`}</strong></div>
        <div className={effectiveLayoutVerified ? "done" : "next"}><span>{effectiveLayoutVerified ? "完了" : "次"}</span><strong>{effectiveLayoutVerified ? "読み取り確認済み" : "保存後に読み取り確認"}</strong></div>
      </div>
      {loadFailed && (
        <div className="error-box action-message" role="alert">
          <span>テンプレート設定を最後まで読み込めませんでした。</span>
          <button type="button" className="secondary compact" onClick={() => setLoadAttempt((current) => current + 1)}>
            読み込みを再試行
          </button>
        </div>
      )}
      {visibleMappingWarnings.map((warning) => (
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
      {mappingConfig && layoutConfig && after && (
        <section className="form-card mapping-section">
          <div className="section-title">
            <div>
              <span className="eyebrow">
                STEP {layoutWorkflowIndex + 1}/{LAYOUT_WIZARD_STEPS.length}
              </span>
              <h2>{activeLayoutStep.title}</h2>
              <p>{activeLayoutStep.description}</p>
            </div>
          </div>
          <nav className="layout-wizard-steps" aria-label="テンプレート設定の工程">
            {LAYOUT_WIZARD_STEPS.map((step, index) => (
              <button
                type="button"
                key={step.id}
                className={step.id === layoutWorkflowStep ? "current" : index < layoutWorkflowIndex ? "done" : ""}
                aria-current={step.id === layoutWorkflowStep ? "step" : undefined}
                disabled={operationBusy || (step.id === "review" && (!identityReady || !documentNameReady))}
                onClick={() => moveLayoutWorkflow(step.id)}
              >
                <span>{index + 1}</span>
                <strong>{step.shortTitle}</strong>
              </button>
            ))}
          </nav>
          {maskDirty && <p className="inline-warning">先に文字候補の選別結果を保存してください。</p>}
          <LayoutEditor
            ref={layoutEditorRef}
            source={after}
            initialLayout={layoutConfig}
            targets={mappingConfig.targets}
            workflowStep={layoutWorkflowStep}
            documentIdentityFields={identityFields}
            onDocumentIdentityFieldsChange={setIdentityFields}
            documentNameRule={documentNameRule}
            onDocumentNameRuleChange={setDocumentNameRule}
            allowUnchangedSave={!mappingConfig.validForOcr || identityDirty || documentNameDirty}
            disabled={maskDirty || maskBusy}
            onDirtyChange={setEditorDirty}
            onBusyChange={setEditorBusy}
            draftStorageKey={`template-layout-draft:${version.id}`}
            detectLabel={version.mode === "SPREADSHEET_TEMPLATE"
              ? "元のExcelセル構造に戻す"
              : undefined}
            onDetect={async () => api.detectLayout(version.id)}
            onRecognizeLabels={async (layout) => api.recognizeLayoutLabels(version.id, layout)}
            onSave={async (layout) => {
              const saved = await api.saveLayout(version.id, layout);
              setLayoutConfig(saved);
              setLayoutVerified(false);
              setMappingFresh(false);
              try {
                const refreshed = await api.fieldMappings(version.id);
                const persistedIdentityFields = identityFields.map((field) => {
                  const selectedMapping = refreshed.mappings.find((mapping) => (
                    mapping.enabled
                    && mapping.mappingType !== "mark"
                    && mapping.targetPath === field.targetPath
                  )) ?? null;
                  if (!selectedMapping) {
                    throw new Error(`選択した「${field.label}」の意味づけが保存結果にありません。意味づけ工程へ戻って確認してください。`);
                  }
                  return {
                    mappingId: selectedMapping.mappingId,
                    targetPath: selectedMapping.targetPath,
                    label: selectedMapping.label,
                    valueType: selectedMapping.targetPath === "document.unifiedEarTag" ? "digits" as const : "text" as const,
                    exactLength: selectedMapping.targetPath === "document.unifiedEarTag" ? 10 : null,
                    matchRole: field.matchRole,
                    displayRole: field.displayRole,
                    matchGroupId: field.matchGroupId,
                    matchGroupLabel: field.matchGroupLabel,
                    componentOrder: field.componentOrder,
                  };
                });
                const primaryIdentity = persistedIdentityFields.find((field) => field.matchRole === "primary") ?? null;
                const persisted = await api.saveFieldMappings(
                  version.id,
                  refreshed.mappings,
                  primaryIdentity,
                  persistedIdentityFields,
                  documentNameRule,
                );
                setMappingConfig(persisted);
                setIdentityFields(identityFieldsFromResponse(persisted));
                setDocumentNameRule(documentNameFromResponse(persisted));
                setMappingFresh(true);
                setFeedback({
                  kind: "success",
                  text: persisted.validForOcr
                    ? `レイアウト${saved.cells.length}セル、意味づけ${saved.relations.length}件、書類の見分け方を保存しました。次に記入欄を再検出してください。`
                    : `レイアウト${saved.cells.length}セルを途中保存しました。バックエンドの再起動後も、この続きから編集できます。`,
                });
              } catch (error) {
                setFeedback({
                  kind: "error",
                  text: `レイアウトは保存されましたが、書類の見分け方を保存できませんでした。${errorMessage(error, "")}`,
                });
                throw error;
              }
              return saved;
            }}
          />
          <div className="layout-wizard-navigation">
            <button
              type="button"
              className="secondary"
              disabled={layoutWorkflowIndex === 0 || operationBusy}
              onClick={() => moveLayoutWorkflow(
                LAYOUT_WIZARD_STEPS[Math.max(0, layoutWorkflowIndex - 1)].id,
              )}
            >
              ← 前の工程
            </button>
            <span>
              {layoutWorkflowStep === "identity" && (!identityReady || !documentNameReady)
                ? "照合グループと、ライブラリ・出力で共用する書類名を指定してください。"
                : layoutWorkflowIndex < LAYOUT_WIZARD_STEPS.length - 1
                  ? "設定内容は工程を移動しても保持されます。"
                  : "未割当と、書類を見分ける項目を確認して保存してください。"}
            </span>
            {layoutWorkflowIndex < LAYOUT_WIZARD_STEPS.length - 1 && (
              <button
                type="button"
                className="primary"
                disabled={operationBusy || (layoutWorkflowStep === "identity" && (!identityReady || !documentNameReady))}
                onClick={() => moveLayoutWorkflow(LAYOUT_WIZARD_STEPS[layoutWorkflowIndex + 1].id)}
              >
                次の工程 →
              </button>
            )}
          </div>
        </section>
      )}
      {meaningSaved && !effectiveLayoutVerified && <section className="form-card verification-gate"><div><span className="eyebrow">LAYOUT VERIFICATION</span><h2>{isSpreadsheetTemplate ? "XLSXのセル構造を確定" : "記入欄を実データで再検出"}</h2><p>{editorDirty ? "先に未保存のレイアウト変更を保存してください。" : isSpreadsheetTemplate ? "XLSXテンプレートでは実画像による再検出を行わず、確認済みのセル構造と印刷プレビューを読み取り基準として確定します。" : "保存した矩形だけを対象にOpenCVでセル候補を再検出します。任意の文字認識機能がある場合は候補を補助します。"}</p></div><button className="primary" type="button" disabled={verifying || editorBusy || editorDirty} onClick={async () => {
        if (verificationLockRef.current || editorDirty || editorBusy) return;
        verificationLockRef.current = true;
        setVerifying(true);
        setFeedback(null);
        try {
          const verified = await api.verifyLayout(version.id);
          setLayoutConfig(verified);
          const refreshedArtifacts = await api.artifacts(jobId);
          setArtifacts(refreshedArtifacts.items);
          if (!isSpreadsheetTemplate) setLayer("layoutVerificationOverlay");
          setLayoutVerified(true);
          setFeedback({ kind: "success", text: isSpreadsheetTemplate
            ? `${verified.relations.length}件の意味づけとXLSXセル構造を確定しました。`
            : `${verified.relations.length}件の意味づけ対象を再検出しました。` });
        } catch (error) {
          setFeedback({ kind: "error", text: errorMessage(error, isSpreadsheetTemplate ? "XLSXのセル構造を確定できませんでした。" : "記入欄を再検出できませんでした。") });
        } finally {
          setVerifying(false);
          verificationLockRef.current = false;
        }
      }}>{verifying ? (isSpreadsheetTemplate ? "確定中…" : "再検出中…") : (isSpreadsheetTemplate ? "XLSX構造を確定" : "記入欄を再検出")}</button></section>}
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
      {version.status === "DRAFT" ? <div className="decision-bar"><div><strong>このテンプレート設定を採用しますか？</strong><span>{editorDirty || maskDirty || identityDirty || documentNameDirty ? "未保存の変更があります。保存してから次へ進んでください。" : maskRequiresReprocess ? "保存した選別マスクから新しい下書きを再生成してください。" : "意味づけを保存し、記入欄を再検出してから承認してください。"}</span></div><button className="danger-button" disabled={operationBusy} onClick={() => void rejectVersion()}>{decisionBusy === "reject" ? "却下中…" : "却下"}</button><button className="secondary" disabled={operationBusy || maskDirty || (!effectiveLayoutVerified && !maskRequiresReprocess) || !meaningSaved} onClick={() => void reprocessVersion()}>{decisionBusy === "reprocess" ? "再生成中…" : "選別マスクから再生成"}</button><button className="primary" disabled={operationBusy || maskDirty || maskRequiresReprocess || !effectiveLayoutVerified || !meaningSaved} onClick={() => void approveVersion()}>{decisionBusy === "approve" ? "承認中…" : "承認して有効化"}</button></div> : <div className="decision-bar"><div><strong>有効テンプレートを編集中</strong><span>{editorDirty || maskDirty || identityDirty || documentNameDirty ? "未保存の変更があります。" : "保存済みの矩形設定は次に開始するOCRから使われます。"}</span></div><button className="primary" disabled={operationBusy} onClick={closeReview}>設定を閉じる</button></div>}
    </div>
  );
}

function layerLabel(name: string) {
  return ({ layoutVerificationOverlay: "矩形再検出", debugOverlay: "文字候補", handwritingOnly: "候補文字のみ", handwritingProbability: "候補確率" } as Record<string, string>)[name] ?? name;
}

function templateModeLabel(mode: string) {
  return MODES.find((item) => item.id === mode)?.title ?? mode;
}

function templateVersionStatusLabel(status: TemplateVersion["status"]) {
  return {
    DRAFT: "作業途中",
    ACTIVE: "使用中",
    REJECTED: "却下済み",
    ARCHIVED: "過去の版",
  }[status];
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

function identityFieldsFromResponse(config: FieldMappingsResponse): DocumentIdentity[] {
  if (config.documentIdentityFields?.length) return config.documentIdentityFields;
  return config.documentIdentity
    ? [{
        ...config.documentIdentity,
        matchRole: config.documentIdentity.matchRole ?? "primary",
        displayRole: config.documentIdentity.displayRole ?? "title",
      }]
    : [];
}

function identityFieldsSignature(fields: DocumentIdentity[]) {
  return JSON.stringify(fields.map((field) => ({
    targetPath: field.targetPath,
    matchRole: field.matchRole,
    displayRole: field.displayRole,
    matchGroupId: field.matchGroupId ?? null,
    matchGroupLabel: field.matchGroupLabel ?? null,
    componentOrder: field.componentOrder ?? 0,
  })).sort((left, right) => left.targetPath.localeCompare(right.targetPath)));
}

function identityConfigurationReady(fields: DocumentIdentity[]) {
  if (fields.length > 0 && fields.every((field) => Boolean(field.matchGroupId))) {
    return fields.every((field) => Boolean(field.matchGroupLabel));
  }
  const primaryCount = fields.filter((field) => field.matchRole === "primary").length;
  return fields.length > 0
    && (primaryCount === 1 || (primaryCount === 0 && fields.length >= 2))
    && fields.filter((field) => field.displayRole === "title").length === 1
    && fields.filter((field) => field.displayRole === "subtitle").length <= 1;
}

export function mappingWarningsForIdentity(
  warnings: string[],
  fields: DocumentIdentity[],
  documentName: DocumentNameRule | null = null,
) {
  if (!identityConfigurationReady(fields)) return warnings;
  return warnings.filter((warning) => (
    warning !== "書類を見分ける項目が未設定です。"
    && !warning.startsWith("書類を見分ける基準")
    && !warning.startsWith("一覧で大きく表示する項目")
    && (documentName === null || !warning.startsWith("ライブラリと出力で使う書類名"))
  ));
}

function documentNameFromResponse(config: FieldMappingsResponse): DocumentNameRule | null {
  if (config.documentName?.fieldPaths.length) return config.documentName;
  const legacyTitle = identityFieldsFromResponse(config).find(
    (field) => field.displayRole === "title",
  );
  return legacyTitle
    ? { fieldPaths: [legacyTitle.targetPath], separator: "", prefix: "", suffix: "" }
    : null;
}

function documentNameSignature(rule: DocumentNameRule | null) {
  return JSON.stringify(rule);
}
