import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { v2client as editingClient, EditingError } from "../editing/v2client";
import { templateEditingStore, type LoadedManualView } from "../editing/workbenchStore";
import { reconcileOwnSave } from "../editing/reconcile";
import { TemplateAnalysisStore } from "../editing/templateAnalysisStore";
import { TemplateAnalysisPanel } from "../editing/TemplateAnalysisPanel";
import { FiAlertCircle, FiArrowLeft, FiArrowRight, FiCheck, FiFileText, FiImage, FiGrid, FiX } from "react-icons/fi";
import { ApiError, api, createIdempotencyKey } from "../api";
import { AssistantPanel } from "../assistant/AssistantPanel";
import { EditingControls } from "../editing/EditingControls";
import { CATEGORY_LABEL, type AssistantOperation, type AssistantSnapshot } from "../assistant/contracts";
import type { LayoutAssistantSelection } from "../assistant/templateSelection";
import { useInteractionContext } from "../assistant/useInteractionContext";
import { ComparisonViewer } from "../components/ComparisonViewer";
import { DocumentBoundaryEditor } from "../components/DocumentBoundaryEditor";
import { FilePicker } from "../components/FilePicker";
import { PrintLayoutEditor } from "../components/PrintLayoutEditor";
import {
  printReferencePreparationReady,
  type PrintPage,
  type PrintState,
} from "../printLayout";
import { TemplatePrintFiles } from "../components/TemplatePrintFiles";
import { JobProgress } from "../components/JobProgress";
import {
  LayoutEditor,
  type LayoutEditorFocusRequest,
  type LayoutEditorFocusResult,
  type LayoutEditorHandle,
  type LayoutEditorMeaningState,
  type LayoutWorkflowStep,
} from "../components/LayoutEditor";
import { MaskEditor } from "../components/MaskEditor";
import { TemplateCandidateSettingsEditor } from "../components/TemplateCandidateSettingsEditor";
import { showToast } from "../components/Toast";
import { useJobPoller } from "../hooks/useJobPoller";
import type { Artifact, DocumentIdentity, DocumentNameRule, FieldMappingsResponse, Job, LayoutDefinition, Template, TemplateVersion } from "../types";

const MODES = [
  { id: "SINGLE_IMAGE_EXPERIMENTAL", number: "01", title: "記入済みの一枚から", copy: "記入済み帳票1枚を補正し、矩形と読み取り欄を画面で設定します。", count: "記入済み・1枚" },
  { id: "REFERENCE_TEMPLATE", number: "02", title: "白紙帳票から", copy: "記入前の白紙帳票を基準画像として使用します。", count: "白紙・1枚" },
  { id: "SPREADSHEET_TEMPLATE", number: "03", title: "元のExcelから", copy: "XLSXのセル、罫線、結合から矩形と区分の案を自動作成します。", count: "XLSX・1ファイル" },
] as const;

const LAYOUT_WIZARD_STEPS: Array<{
  id: LayoutWorkflowStep;
  title: string;
  shortTitle: string;
  description: string;
}> = [
  { id: "classify", shortTitle: "欄の種類", title: "検出された枠の種類を確認する", description: "書類から検出された枠を、項目名、見出し、記入欄、読み取らない欄に分けます。記入欄では文字・番号・記号・印刷された選択肢の読み取り方法を指定できます。" },
  { id: "meaning", shortTitle: "読み取る内容", title: "各欄で読み取る内容を決める", description: "記入欄と保存する情報を対応づけます。項目名のない記入欄には、保存する情報を直接指定できます。" },
  { id: "records", shortTitle: "繰り返し部分", title: "繰り返し部分をまとめる", description: "明細表など、同じ構造を繰り返す欄をひとまとまりとして設定します。" },
  { id: "identity", shortTitle: "確認する欄とファイル名", title: "読み取り後に確認する欄とファイル名を決める", description: "氏名や番号など、読み取り後に「この書類で合っているか」を確かめる欄と、保存するファイル名を決めます。" },
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
  refresh: () => Promise<Template[] | void>;
  rememberJob: (id: string) => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
  initialWorkspace?: TemplateWorkspace;
  initialEditVersionId?: string;
  onClose?: () => void;
  onTemplateActivated?: (versionId: string) => void;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("SINGLE_IMAGE_EXPERIMENTAL");
  const [name, setName] = useState("");
  const nameEditedRef = useRef(false);
  const [fieldNames, setFieldNames] = useState<string[]>([]);
  const [fieldNameDraft, setFieldNameDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobPollRevision, setJobPollRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [openingReview, setOpeningReview] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<TemplateWorkspace | null>(initialWorkspace ?? null);
  const [templateFilter, setTemplateFilter] = useState<TemplateListFilter>("current");
  const [deletingTemplateId, setDeletingTemplateId] = useState<string | null>(null);
  const [candidateEditor, setCandidateEditor] = useState<{
    template: Template;
    version: TemplateVersion;
  } | null>(null);
  const [review, setReview] = useState<{
    templateId: string;
    version: TemplateVersion;
    jobId: string;
    origin: TemplateWorkspace;
    returnToReading?: boolean;
  } | null>(() => {
    if (!initialEditVersionId) return null;
    const owner = templates.find((template) => template.versions.some((candidate) => candidate.id === initialEditVersionId));
    const version = owner?.versions.find((candidate) => candidate.id === initialEditVersionId);
    if (!version?.sourceJobId) return null;
    return {
      templateId: owner!.id,
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
      const hasSourceVersion = (items: Template[]) =>
        items.some((item) => item.versions.some((candidate) => candidate.sourceJobId === terminal.id));
      // A refresh already in flight may predate this job, so fetch again only when the version is missing.
      let details = (await refresh()) ?? [];
      if (!hasSourceVersion(details)) {
        const list = await api.templates();
        details = await Promise.all(list.map((item) => api.template(item.id)));
      }
      const owner = details.find((item) => item.versions.some((candidate) => candidate.sourceJobId === terminal.id));
      const version = owner?.versions.find((candidate) => candidate.sourceJobId === terminal.id);
      if (!version) {
        throw new Error("確認対象のテンプレートを見つけられませんでした。少し待ってから再取得してください。");
      }
      setReview({ templateId: owner!.id, version, jobId: terminal.id, origin: "create" });
    } catch (error) {
      setMessage(errorMessage(error, "確認画面を開けませんでした。"));
    } finally {
      openingReviewRef.current = false;
      setOpeningReview(false);
    }
  };
  const selectedWorkspace = workspace ?? (templates.length > 0 ? "manage" : "create");
  const listedTemplates = useMemo(() => {
    return templates
      .map((template) => ({
        template,
        versions: [...template.versions].sort((first, second) => (
          second.versionNumber - first.versionNumber
          || Date.parse(second.createdAt) - Date.parse(first.createdAt)
        )),
      }))
      .filter(({ versions }) => {
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
  }, [templateFilter, templates]);
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
    return <TemplateReviewGate
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
        const authoritative = await api.template(review.templateId);
        const active = authoritative.versions.find((version) => version.id === versionId);
        if (
          authoritative.activeVersionId !== versionId
          || active?.status !== "ACTIVE"
          || authoritative.versions.some((version) => (
            version.id !== versionId && version.status === "ACTIVE"
          ))
        ) {
          throw new Error("承認結果の再取得でACTIVE切替を確認できませんでした。");
        }
        try { await refresh(); }
        catch {
          showToast({ tone: "warning", title: "このテンプレートは有効です",
            message: "一覧を更新できませんでした。再読み込み後に最新の状態が反映されます。" });
        }
        if (onTemplateActivated) onTemplateActivated(versionId);
        return { handled: Boolean(onTemplateActivated), version: active };
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
  return (
    <div className="page-stack narrow template-setup-workspace">
      <header className="page-header split">
        <div><h1>テンプレート作成・編集</h1><p>新しい帳票のテンプレートを作成するか、既存のテンプレートを編集します。</p></div>
        <div className="template-page-header-actions">
          {onClose && <button type="button" className="ghost" onClick={closeWorkspace}><FiArrowLeft aria-hidden="true" />読み取りへ戻る</button>}
        </div>
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
          <div><h2>新しいテンプレートを作成</h2></div>
          <p>原稿を選び、次の画面で読み取る欄を設定します。</p>
        </div>
        <section className="form-card template-create-form">
          <fieldset className="template-create-section"><legend><span className="template-section-number">1</span> 原稿の種類を選ぶ</legend><div className="mode-grid">{MODES.map((item) => (
            <button type="button" key={item.id} className={`mode-card ${mode === item.id ? "selected" : ""}`} aria-pressed={mode === item.id} onClick={() => {
              idempotencyKeyRef.current = null;
              setMode(item.id);
              setFiles([]);
            }}>
              <span className="template-mode-heading">{item.id === "SPREADSHEET_TEMPLATE" ? <FiGrid aria-hidden="true" /> : item.id === "REFERENCE_TEMPLATE" ? <FiFileText aria-hidden="true" /> : <FiImage aria-hidden="true" />}<strong>{item.title}</strong><span className="template-mode-selected">{mode === item.id && <FiCheck aria-label="選択中" />}</span></span><p>{item.copy}</p><small>{item.count}</small>
            </button>
          ))}</div></fieldset>
          <fieldset className="template-create-section"><legend><span className="template-section-number">2</span> {mode === "SPREADSHEET_TEMPLATE" ? "Excelファイルを選ぶ" : "帳票画像を選ぶ"}</legend>
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
              if (mode === "SPREADSHEET_TEMPLATE" && nextFiles[0] && !nameEditedRef.current) {
                setName(nextFiles[0].name.replace(/\.xlsx$/i, ""));
              }
              setFiles(nextFiles);
            }} />
          </fieldset>
          <fieldset className="template-create-section"><legend><span className="template-section-number">3</span> 名前を付ける</legend>
          <label className="field-label">テンプレート名<input value={name} required maxLength={200} placeholder="テンプレート名を入力してください" onChange={(event) => {
            idempotencyKeyRef.current = null;
            nameEditedRef.current = true;
            setName(event.target.value);
          }} /></label>
          {duplicateTemplate && (
            <div className="inline-warning duplicate-template-warning">
              <span>「{duplicateTemplate.name}」は既に存在します。別の名前を指定するか、既存の設定を確認してください。</span>
              <button type="button" className="secondary compact" onClick={() => {
                setTemplateFilter("current");
                setWorkspace("manage");
              }}>既存テンプレートを確認</button>
            </div>
          )}
          </fieldset>
          <div className="template-create-submit"><p>準備後に、読み取る欄と内容を確認できます。</p>
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
          }}><FiArrowRight aria-hidden="true" />{busy ? "送信中…" : submissionLocked ? "テンプレート準備を開始しました" : "テンプレート準備を開始"}</button>
          </div>
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
          <div><h2>確認・修正するテンプレートを選択</h2></div>
          <p>{draftCount > 0 ? `${draftCount}件の作業途中テンプレートがあります。` : "作業途中のテンプレートはありません。"}</p>
        </div>
        <div className="template-list-toolbar">
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
            const printVersion = active ?? primaryVersion;
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
                  {printVersion?.mode === "SPREADSHEET_TEMPLATE" && printVersion.quality.printLayout ? <div>
                    <small>{printVersion.status === "ACTIVE" ? "使用中" : "下書き"} v{printVersion.versionNumber} の印刷用帳票</small>
                    <TemplatePrintFiles key={printVersion.id}
                      versionId={printVersion.id} disabled={isDeletingCurrentTemplate || openingReview} />
                  </div> : null}

                  {primaryVersion && (
                    <button
                      type="button"
                      className={draft ? "primary compact" : "secondary compact"}
                      aria-label={draft ? draftResumeLabel(template.name, primaryVersion) : `${template.name} v${primaryVersion.versionNumber} のテンプレートを編集`}
                      disabled={!primaryVersion.sourceJobId || isDeletingCurrentTemplate || openingReview}
                      onClick={() => primaryVersion.sourceJobId && setReview({
                        templateId: template.id,
                        version: primaryVersion,
                        jobId: primaryVersion.sourceJobId,
                        origin: "manage",
                      })}
                    >{draft ? "作成を再開" : "テンプレートを編集"}</button>
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
                            : `${template.name} v${version.versionNumber} のテンプレートを編集`}
                          disabled={!version.sourceJobId || isDeletingCurrentTemplate || openingReview}
                          onClick={() => version.sourceJobId && setReview({
                            templateId: template.id,
                            version,
                            jobId: version.sourceJobId,
                            origin: "manage",
                        })}
                        >テンプレートを編集</button>
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
type ApprovalConfirmation = { handled: boolean; version: TemplateVersion };

function TemplateReviewGate(props: Parameters<typeof TemplateReview>[0]) {
  const [draft, setDraft] = useState<TemplateVersion | null>(
    props.version.status === "DRAFT" ? props.version : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (props.version.status === "DRAFT") return;
    let cancelled = false;
    setError(null);
    void (async () => {
      const baseRevision = props.version.quality.printLayout
        ? (await api.printLayout(props.version.id)).revision
        : undefined;
      return api.editTemplateVersion(props.version.id, props.version.revision, baseRevision);
    })().then((version) => {
      if (!cancelled) setDraft(version);
    }).catch((reason) => {
      if (!cancelled) setError(errorMessage(reason, "編集用の下書きを開けませんでした。"));
    });
    return () => { cancelled = true; };
  }, [props.version.id, props.version.status, attempt]);
  if (!draft) return <section className="form-card">
    {error ? <><p className="error-box" role="alert">{error}</p>
      <button onClick={() => setAttempt((value) => value + 1)}>再試行</button></>
      : <p role="status">編集用の下書きを準備しています…</p>}
    <button className="ghost" onClick={props.onClose}>戻る</button>
  </section>;
  return <TemplateReview {...props} version={draft} jobId={draft.sourceJobId ?? props.jobId} />;
}

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
  onApproved?: (versionId: string) => Promise<ApprovalConfirmation>;
  closeLabel?: string;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const revisionRef = useRef(version.revision);
  const [authoringRevision, setAuthoringRevision] = useState(version.revision);
  const acceptRevision = (revision: number) => {
    if (revision < revisionRef.current) return;
    revisionRef.current = revision;
    setAuthoringRevision(revision);
  };
  const [mappingConfig, setMappingConfig] = useState<FieldMappingsResponse | null>(null);
  const [layoutConfig, setLayoutConfig] = useState<LayoutDefinition | null>(null);
  const [layer, setLayer] = useState("layoutVerificationOverlay");
  const [layoutVerified, setLayoutVerified] = useState(version.quality.layoutVerified === true);
  const [verifying, setVerifying] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [printDirty, setPrintDirty] = useState(false);
  const [printBusy, setPrintBusy] = useState(false);
  const [printSaved, setPrintSaved] = useState(Boolean(version.quality.printLayout));
  const [printState, setPrintState] = useState<PrintState | null>(null);
  const [preparationMessage, setPreparationMessage] = useState("");
  const [activatedVersion, setActivatedVersion] = useState<TemplateVersion | null>(null);
  const [approvalPendingConfirmation, setApprovalPendingConfirmation] = useState(false);
  const [authoringPage, setAuthoringPage] = useState(0);
  const currentPrintPage = printState?.saved?.pages[
    Math.min(authoringPage, printState.saved.pages.length - 1)
  ];
  const [editorBusy, setEditorBusy] = useState(false);
  const [editorSaving, setEditorSaving] = useState(false);
  const [navigationPending, setNavigationPending] = useState(false);
  const navigationLockRef = useRef(false);
  const [maskDirty, setMaskDirty] = useState(false);
  const [maskBusy, setMaskBusy] = useState(false);
  const [identityFields, setIdentityFields] = useState<DocumentIdentity[]>([]);
  const [documentNameRule, setDocumentNameRule] = useState<DocumentNameRule | null>(null);
  const [maskRequiresReprocess, setMaskRequiresReprocess] = useState(
    version.status === "DRAFT"
      && !version.quality.forkParentVersionId
      && Boolean(version.quality.editedMaskSha256)
      && version.quality.layoutVerified !== true,
  );
  const [mappingFresh, setMappingFresh] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [feedback, setFeedback] = useState<ReviewFeedback | null>(null);
  const editingStore = useMemo(() => templateEditingStore(version.id), [version.id]);
  const editingStatus = useSyncExternalStore(editingStore.subscribe, editingStore.getSnapshot);
  const editingInteractionChange = useCallback((active: boolean) => editingStore.setInteraction(active), [editingStore]);
  const [editingEnabled, setEditingEnabled] = useState<boolean | null>(null);
  const [analysisEnabled, setAnalysisEnabled] = useState(false);
  const [editingReview, setEditingReview] = useState<LoadedManualView | null>(null);
  const [editingRecoveryBusy, setEditingRecoveryBusy] = useState(false);
  const ownSubstitutionsRef = useRef<Record<string, string>>({});
  useEffect(() => {
    let active = true;
    void editingClient.status().then(async status => {
      if (status.enabled) await editingStore.initialize();
      if (active) setEditingEnabled(status.enabled);
      if (active) setAnalysisEnabled(status.enabled && status.templateAnalysisEnabled === true);
    }).catch(error => {
      // Before the phase19 server cutover, a running legacy server has no
      // status endpoint. This discovery is not a fallback after a failed write.
      if (error instanceof EditingError && error.status === 404) {
        if (active) setEditingEnabled(false);
        return;
      }
      if (active) setFeedback({ kind: "error", text: errorMessage(error, "編集機能の状態を取得できませんでした。") });
    });
    return () => { active = false; };
  }, [editingStore]);
  const [decisionBusy, setDecisionBusy] = useState<DecisionAction | null>(null);
  const [layoutWorkflowStep, setLayoutWorkflowStep] = useState<LayoutWorkflowStep>("classify");
  const [mappingWarningsExpanded, setMappingWarningsExpanded] = useState(false);
  const [mappingWarningFocusRequest, setMappingWarningFocusRequest] = useState(0);
  const [mappingWarningNotices, setMappingWarningNotices] = useState<Record<string, string>>({});
  const [layoutCellFocusRequest, setLayoutCellFocusRequest] = useState<LayoutEditorFocusRequest | null>(null);
  const [layoutMeaningState, setLayoutMeaningState] = useState<LayoutEditorMeaningState | null>(null);
  const [assistantSelection, setAssistantSelection] = useState<LayoutAssistantSelection | null>(null);
  const [assistantApplying, setAssistantApplying] = useState(false);
  const [recoveryDiscardEpoch, setRecoveryDiscardEpoch] = useState(0);
  const [assistantSaveError, setAssistantSaveError] = useState<string | null>(null);
  const layoutCellFocusRequestIdRef = useRef(0);
  const layoutCellFocusContextRef = useRef<{ requestId: number; warningKey: string } | null>(null);
  const layoutEditorRef = useRef<LayoutEditorHandle>(null);
  const analysisInputs = useRef({ identityFields, documentNameRule });
  analysisInputs.current = { identityFields, documentNameRule };
  const analysisStore = useMemo(() => new TemplateAnalysisStore(editingStore, {
    matches: input => JSON.stringify(input.documentIdentityFields) === JSON.stringify(analysisInputs.current.identityFields)
      && JSON.stringify(input.documentName) === JSON.stringify(analysisInputs.current.documentNameRule)
      && (layoutEditorRef.current?.matchesAnalysisDraft(input.layout as LayoutDefinition) ?? false),
    priorityBounds: () => layoutEditorRef.current?.readAnalysisPriority() ?? null,
    read: () => {
      const layout = layoutEditorRef.current?.readAnalysisDraft();
      return layout ? structuredClone({ layout, documentIdentityFields: analysisInputs.current.identityFields, documentName: analysisInputs.current.documentNameRule }) : null;
    },
    apply: (input, merged, action) => {
      if (JSON.stringify(input.documentIdentityFields) !== JSON.stringify(analysisInputs.current.identityFields)
        || JSON.stringify(input.documentName) !== JSON.stringify(analysisInputs.current.documentNameRule)) return false;
      return layoutEditorRef.current?.applyAnalysisDraft(input.layout as LayoutDefinition, merged.layout as LayoutDefinition, action) ?? false;
    },
    pause: () => layoutEditorRef.current?.beginAssistant(),
    resume: () => layoutEditorRef.current?.endAssistant(),
    saveDraft: async () => (await layoutEditorRef.current?.saveIfNeeded()) ?? false,
  }), [editingStore]);
  const analysisStatus = useSyncExternalStore(analysisStore.subscribe, analysisStore.getSnapshot);
  useEffect(() => {
    if (!analysisEnabled || !layoutConfig) return;
    return analysisStore.reconnect();
  }, [analysisEnabled, Boolean(layoutConfig), analysisStore]);
  const mappingSectionHeadingRef = useRef<HTMLHeadingElement>(null);
  const decisionLockRef = useRef(false);
  const verificationLockRef = useRef(false);
  useEffect(() => {
    let cancelled = false;
    setLoadFailed(false);
    setFeedback(null);
    setLayoutMeaningState(null);
    void Promise.all([api.artifacts(jobId), api.authoring(version.id)])
      .then(([artifactResponse, authoring]) => {
        const { mapping: mappings, layout } = authoring;
        if (cancelled) return;
        acceptRevision(authoring.revision);
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
    assistantApplying || printDirty || printBusy ||
    editorDirty
    || editorBusy
    || editorSaving
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
  // A freshly prepared template (no fork parent, nothing assigned yet) gets one AI draft
  // automatically: run, apply the validated units, save. Edited or forked versions never
  // start on their own; the panel's "下書きを一度で作る" covers those on request.
  const autoDraftRef = useRef(false);
  useEffect(() => {
    if (autoDraftRef.current || !analysisEnabled || !layoutConfig || !mappingConfig || !after) return;
    if (editingStatus.state !== "ready" || editorBusy || editorSaving || loadFailed || !mappingFresh) return;
    if (version.status !== "DRAFT" || version.quality.forkParentVersionId || activatedVersion) return;
    const untouched = layoutConfig.cells.length > 0 && layoutConfig.relations.length === 0
      && (layoutConfig.recordSections ?? []).length === 0;
    if (!untouched || analysisStatus.run || analysisStatus.recovery || analysisStatus.held || analysisStatus.busy) return;
    autoDraftRef.current = true;
    void analysisStore.startDraft(currentPrintPage
      ? { mode: "current", pageId: `${currentPrintPage.page}:${currentPrintPage.range}` }
      : { mode: "all", pageId: null });
  }, [analysisEnabled, layoutConfig, mappingConfig, after, editingStatus.state, editorBusy, editorSaving, loadFailed, mappingFresh,
    version.status, version.quality.forkParentVersionId, activatedVersion, analysisStatus.run, analysisStatus.recovery,
    analysisStatus.held, analysisStatus.busy, analysisStore, currentPrintPage]);
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
  const identityVisibleMappingWarnings = mappingWarningsForIdentity(
    mappingConfig?.warnings ?? [],
    identityFields,
    documentNameRule,
  );
  const visibleMappingWarnings = layoutConfig && !layoutMeaningState
    ? []
    : mappingWarningsForCurrentDraft(
      identityVisibleMappingWarnings,
      mappingConfig?.fieldErrors,
      layoutMeaningState,
    );
  const effectiveLayoutVerified = layoutVerified
    && !editorDirty
    && !identityDirty
    && !documentNameDirty
    && mappingFresh;
  const navigationBusy = navigationPending || editorBusy || maskBusy || printBusy || verifying
    || assistantApplying || decisionBusy !== null;
  const operationBusy = navigationBusy || editorSaving || assistantApplying;
  const layoutEditorDisabled = maskDirty || maskBusy || printDirty || printBusy
    || assistantApplying || (printSaved && !currentPrintPage);
  const layoutWorkflowIndex = LAYOUT_WIZARD_STEPS.findIndex(
    (step) => step.id === layoutWorkflowStep,
  );
  const activeLayoutStep = LAYOUT_WIZARD_STEPS[layoutWorkflowIndex] ?? LAYOUT_WIZARD_STEPS[0];
  const saveEditorBeforeLeaving = async (): Promise<boolean> => {
    if (analysisStatus.held) {
      setFeedback({ kind: "error", text: "AI案を確認して保存するか、反映を戻して解析を終了してから移動してください。" });
      return false;
    }
    if (printDirty || printBusy) {
      setFeedback({ kind: "error", text: "印刷ページの変更を保存または取消してから移動してください。" });
      return false;
    }
    if (!editorDirty && !editorSaving) return true;
    const saved = await layoutEditorRef.current?.saveIfNeeded();
    if (saved) return true;
    setFeedback({
      kind: "error",
      text: "編集中の内容を保存できなかったため、画面移動を中止しました。編集欄に表示されたエラー内容を確認して再度操作してください。",
    });
    return false;
  };
  const moveLayoutWorkflow = (step: LayoutWorkflowStep) => {
    if (navigationBusy || navigationLockRef.current || step === layoutWorkflowStep) return;
    // These are views of the same mounted editor, not a persistence boundary.
    // Keep its draft/save queue alive and switch without waiting for the network.
    setLayoutWorkflowStep(step);
  };
  const moveToMappingWarning = (
    warning: string,
    warningKey: string,
    step: LayoutWorkflowStep,
  ) => {
    if (navigationBusy || navigationLockRef.current) return;
    const structuredCellTarget = warning.startsWith("[MARK_OPTIONS_REQUIRED]")
      || warning === "読み取りに使用できる意味づけ済みセルがありません。";
    if (!structuredCellTarget) {
      moveLayoutWorkflow(step);
      setMappingWarningFocusRequest((request) => request + 1);
      return;
    }
    moveLayoutWorkflow(step);
    const fallback = () => {
      layoutCellFocusContextRef.current = null;
      setLayoutCellFocusRequest(null);
      setMappingWarningNotices((current) => ({
        ...current,
        [warningKey]: "対象セルを特定できないため工程2を開いた",
      }));
      setMappingWarningFocusRequest((request) => request + 1);
    };
    const target = warning.startsWith("[MARK_OPTIONS_REQUIRED]")
      ? markOptionsTargetFromFieldErrors(mappingConfig?.fieldErrors)
      : firstMeaningFocusTarget(layoutMeaningState);
    const targetCell = target
      ? layoutMeaningState?.cells.find((cell) => cell.id === target.cellId) ?? null
      : null;
    const targetRoleValid = Boolean(targetCell && (target?.purpose === "meaning-cell"
      ? targetCell.role === "input" || targetCell.role === "mark"
      : targetCell.role === "mark"));
    if (
      !target
      || !targetRoleValid
      || (target.purpose === "mark-options"
        && !targetCell?.markSetIds.includes(target.markSetId))
      || layoutEditorDisabled
      || operationBusy
    ) {
      fallback();
      return;
    }
    let targetPageIndex: number | null = null;
    if (printSaved) {
      const pages = printState?.saved?.pages;
      const targetPage = pages ? savedPrintPageForCell(pages, target.cellId) : null;
      if (!pages || !targetPage) {
        fallback();
        return;
      }
      // The field binding above establishes page identity. The index is only
      // the existing page viewer's UI position, not an identity inference.
      targetPageIndex = pages.indexOf(targetPage);
      if (targetPageIndex < 0) {
        fallback();
        return;
      }
    }
    const requestId = layoutCellFocusRequestIdRef.current + 1;
    layoutCellFocusRequestIdRef.current = requestId;
    layoutCellFocusContextRef.current = { requestId, warningKey };
    setMappingWarningNotices((current) => {
      if (!(warningKey in current)) return current;
      const next = { ...current };
      delete next[warningKey];
      return next;
    });
    if (targetPageIndex !== null) setAuthoringPage(targetPageIndex);
    setLayoutCellFocusRequest({
      requestId,
      ...target,
    });
  };
  const handleLayoutFocusResult = useCallback((result: LayoutEditorFocusResult) => {
    const context = layoutCellFocusContextRef.current;
    if (!context || context.requestId !== result.requestId) return;
    layoutCellFocusContextRef.current = null;
    setLayoutCellFocusRequest(null);
    if (result.focused) return;
    setMappingWarningNotices((current) => ({
      ...current,
      [context.warningKey]: "対象セルを特定できないため工程2を開いた",
    }));
    setMappingWarningFocusRequest((request) => request + 1);
  }, []);
  useEffect(() => {
    if (mappingWarningFocusRequest === 0) return;
    const heading = mappingSectionHeadingRef.current;
    heading?.focus({ preventScroll: true });
    heading?.scrollIntoView?.({
      behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "center",
    });
  }, [mappingWarningFocusRequest]);
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
    if (navigationBusy || navigationLockRef.current) return;
    if (!editorDirty && !editorSaving) {
      closeAfterEditorSave();
      return;
    }
    navigationLockRef.current = true;
    setNavigationPending(true);
    void saveEditorBeforeLeaving().then((saved) => {
      if (saved) closeAfterEditorSave();
    }).finally(() => {
      navigationLockRef.current = false;
      setNavigationPending(false);
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
      await api.reject(version.id, revisionRef.current);
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
      const next = await api.reprocess(version.id, revisionRef.current);
      onReprocess(next.jobId);
    } catch (error) {
      setFeedback({ kind: "error", text: errorMessage(error, "テンプレートを再生成できませんでした。") });
    } finally {
      endDecision();
    }
  };
  const confirmApproval = async () => {
    const confirmation = await onApproved?.(version.id);
    if (!confirmation) onClose();
    else if (!confirmation.handled) {
      setActivatedVersion(confirmation.version);
      setFeedback({ kind: "success", text: "承認結果を再取得し、この版が有効になったことを確認しました。" });
    }
    setApprovalPendingConfirmation(false);
  };
  const recheckApproval = async () => {
    if (!beginDecision("approve")) return;
    try { await confirmApproval(); }
    catch (error) {
      setFeedback({ kind: "error", text: `承認要求は成功しましたが、状態を確認できません。${errorMessage(error, "承認結果を再取得してください。")}` });
    }
    finally { endDecision(); }
  };
  const approveVersion = async () => {
    if (printDirty || printBusy || approvalPendingConfirmation) return;
    let revision = printSaved ? printState?.revision : undefined;
    if (printSaved) {
      if (!revision || !meaningSaved) return;
    } else if (!effectiveLayoutVerified || !meaningSaved || maskRequiresReprocess) return;
    if (editorBusy || editorSaving || editorDirty || identityDirty || documentNameDirty
      || maskDirty || maskBusy || !beginDecision("approve")) return;
    let approvalPosted = false;
    try {
      if (printSaved && revision) {
        if (!printReferencePreparationReady(printState?.saved)) {
          setPreparationMessage("印刷見本を作成しています…");
          const prepared = await api.preparePrintFiles(version.id, revisionRef.current, revision);
          acceptRevision(prepared.resourceRevision);
          setPrintState(prepared);
          revision = prepared.revision;
        }
        setPreparationMessage("読み取る欄を準備しています…");
        acceptRevision((await api.prepareReviewedOcr(version.id, revisionRef.current, revision)).resourceRevision);
        setPreparationMessage("テンプレートを有効化しています…");
      }
      acceptRevision((await api.approve(version.id, revisionRef.current, revision)).resourceRevision);
      approvalPosted = true;
      setApprovalPendingConfirmation(true);
      await confirmApproval();
    } catch (error) {
      if (approvalPosted) {
        setFeedback({ kind: "error", text: `承認要求は成功しましたが、状態を確認できません。${errorMessage(error, "承認結果を再取得してください。")}` });
      } else if (printSaved && error instanceof ApiError && [
        "PRINT_LAYOUT_CONFLICT",
        "TEMPLATE_VERSION_CONFLICT",
      ].includes(error.code)) {
        try {
          setPrintState(await api.printLayout(version.id));
        } catch {
          // Preserve the original conflict as the user-visible failure.
        }
        setFeedback({ kind: "error", text: `${error.message} 最新のページ設定を取得しました。内容を確認してもう一度実行してください。` });
      } else {
        setFeedback({ kind: "error", text: errorMessage(error, "テンプレートを承認できませんでした。") });
      }
    } finally {
      setPreparationMessage("");
      endDecision();
    }
  };
  const assistantInteraction = useInteractionContext(
    JSON.stringify([version.id, currentPrintPage?.page, layoutWorkflowStep, assistantSelection?.selectionEpoch]),
    JSON.stringify([assistantSelection?.localEditEpoch, identityFields, documentNameRule, maskDirty, printDirty, activatedVersion?.status, mappingFresh]),
  );
  const assistantCell = assistantSelection?.cell;
  const assistantChoice = assistantSelection?.choice;
  const assistantMapping = assistantCell ? mappingConfig?.mappings.find(mapping => mapping.mappingId === `layout_${assistantCell.id}`) : null;
  const assistantOperations: AssistantOperation[] = assistantChoice ? ["template.rename_choice"] : assistantCell ? [
    ...(["input", "label", "parent_label"].includes(assistantCell.role) ? ["template.rename_cell" as const] : []),
    ...(!assistantSelection?.categoryChangeBlocked && ["input", "label", "parent_label", "ignored"].includes(assistantCell.role) ? ["template.change_category" as const] : []),
  ] : [];
  const assistantSnapshot: AssistantSnapshot = {
    context: { ...assistantInteraction, resourceKind: "template", versionId: version.id, resourceRevision: authoringRevision,
      snapshotKey: JSON.stringify([assistantSelection?.snapshotKey, printState?.revision, version.status, activatedVersion?.status]),
      pageId: assistantSelection?.pageId ?? null, cellIds: assistantSelection?.selectedIds ?? [],
      markSetId: assistantChoice?.markSetId ?? null, choiceId: assistantChoice?.choiceId ?? null },
    target: assistantCell ? { label: assistantChoice ? `${assistantCell.text || "選択欄"} · ${assistantChoice.label}` : assistantMapping?.label || assistantCell.text || `欄 ${assistantCell.id}`,
      currentValue: assistantChoice?.label ?? (["label", "parent_label"].includes(assistantCell.role) ? assistantCell.text : assistantCell.fieldLabel ?? assistantMapping?.label ?? null),
      category: CATEGORY_LABEL[assistantCell.role as keyof typeof CATEGORY_LABEL] ?? assistantCell.role,
      ...(assistantChoice ? { choiceValue: assistantChoice.value, choiceTracksLabel: assistantChoice.tracksLabel } : {}),
      ...(assistantSelection?.image ? { image: assistantSelection.image } : {}) } : null,
    operations: assistantOperations,
    dirty: editorDirty || identityDirty || documentNameDirty,
    recoveryDirty: editorDirty || identityDirty || documentNameDirty || maskDirty || printDirty || Boolean(assistantSelection?.draftReviewRequired),
    recoveryBusy: editorBusy || editorSaving || maskBusy || printBusy || verifying || decisionBusy !== null,
    resourceBlocker: version.status !== "DRAFT" || activatedVersion ? "公開済みの版です。"
      : loadFailed || !mappingFresh || maskDirty || printDirty || assistantSelection?.draftReviewRequired ? "既存の編集・保存状態を先に解決してください。" : null,
    busy: operationBusy || printBusy || maskBusy,
    blocker: version.status !== "DRAFT" || activatedVersion ? "公開済みの版は操作できません。下書き版を開いてください。"
      : loadFailed || !mappingFresh ? "保存状態を確認できません。既存の編集画面で再取得または保存を完了してください。"
      : maskDirty || printDirty ? "マスク・印刷ページの未保存変更を、既存の編集画面で保存または取り消してください。"
      : assistantSelection?.draftReviewRequired ? "復元した下書きの確認を既存の編集画面で完了してください。"
      : !assistantCell ? ((assistantSelection?.selectedIds.length ?? 0) > 1 ? "複数のセルが選択されています。操作対象を1つにしてください。" : "帳票上のセルを1つ選択してください。")
      : assistantCell.role === "mark" && !assistantChoice ? "選択肢エディタで、変更する選択肢を操作対象に選択してください。"
      : assistantOperations.length === 0 ? "このセルには対応する操作がありません。接続や繰り返し構造は既存の編集画面で変更してください。" : null,
    saveError: assistantSaveError ?? assistantSelection?.saveError ?? null,
  };
  return (
    <div className="page-stack template-authoring-workspace">
      <header className="page-header split"><div><span className="eyebrow">テンプレート設定 · V{version.versionNumber}</span><h1>{version.quality.forkParentVersionId ? "読み取る欄を設定" : "復元結果を確認"}</h1></div><button className="ghost" disabled={navigationBusy} onClick={closeReview}><FiArrowLeft aria-hidden="true" />{closeLabel.replace(/^←\s*/, "")}</button></header>
      {loadFailed && (
        <div className="error-box action-message" role="alert">
          <span>テンプレート設定を最後まで読み込めませんでした。</span>
          <button type="button" className="secondary compact" onClick={() => setLoadAttempt((current) => current + 1)}>
            読み込みを再試行
          </button>
        </div>
      )}
      {visibleMappingWarnings.length > 0 && (
        <aside className="mapping-warning-panel" aria-label="テンプレート設定の要対応事項">
          <button
            type="button"
            className="mapping-warning-toggle"
            aria-expanded={mappingWarningsExpanded}
            aria-controls="mapping-warning-list"
            onClick={() => setMappingWarningsExpanded((expanded) => !expanded)}
          >
            <FiAlertCircle aria-hidden="true" />
            <strong>要対応</strong>
            <span className="mapping-warning-count" aria-live="polite" aria-atomic="true">
              {visibleMappingWarnings.length}件
            </span>
            <span className="mapping-warning-toggle-label">
              {mappingWarningsExpanded ? "折りたたむ" : "展開する"}
            </span>
          </button>
          {mappingWarningsExpanded && (
            <div className="mapping-warning-list" id="mapping-warning-list">
              <ul>
                {visibleMappingWarnings.map((warning, index) => {
                  const warningKey = `${index}-${warning}`;
                  const targetStep = mappingWarningWorkflowStep(warning);
                  const target = targetStep === null
                    ? null
                    : LAYOUT_WIZARD_STEPS.find((step) => step.id === targetStep) ?? null;
                  return (
                    <li key={warningKey}>
                      {target ? (
                        <button
                          type="button"
                          className="mapping-warning-action"
                          disabled={navigationBusy || navigationLockRef.current}
                          onClick={() => moveToMappingWarning(warning, warningKey, target.id)}
                        >
                          <span>{warning}</span>
                          <small>{navigationBusy || navigationLockRef.current
                            ? "現在の処理が完了するまで移動できません。"
                            : `Step ${LAYOUT_WIZARD_STEPS.indexOf(target) + 1}「${target.shortTitle}」へ移動`}</small>
                        </button>
                      ) : (
                        <span className="mapping-warning-message">{warning}</span>
                      )}
                      {mappingWarningNotices[warningKey] && (
                        <small className="mapping-warning-notice" role="status">
                          <span>{mappingWarningNotices[warningKey]}</span>
                          <span>{warning.startsWith("[MARK_OPTIONS_REQUIRED]")
                            ? "帳票プレビューから対象の選択欄を選んでください。"
                            : "帳票プレビューから対象の記入欄または選択欄を選んでください。"}</span>
                        </small>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </aside>
      )}
      {effectiveLayoutVerified && <div className="template-verification-view">
        <section className="viewer-card">
          {before && after ? <ComparisonViewer before={before} after={after} /> : <div className="empty-state">画像を読み込んでいます…</div>}
          <div className="layer-tabs">{layers.map((name) => <button key={name} className={layer === name ? "active" : ""} onClick={() => setLayer(name)}>{layerLabel(name)}</button>)}</div>
          {byName.get(layer) && <div className="single-image-view"><img src={byName.get(layer)} alt={layerLabel(layer)} /></div>}
        </section>
      </div>}
      {isSpreadsheetTemplate && <PrintLayoutEditor
        key={`${version.id}:${recoveryDiscardEpoch}`} versionId={version.id}
        resourceRevision={authoringRevision} onMutationRevision={revision => {
          acceptRevision(revision);
          if (editingEnabled) void editingStore.acceptOwnRevision(revision).catch(error => {
            setFeedback({ kind: "error", text: errorMessage(error, "印刷操作後の編集状態を確認してください。") });
          });
        }}
        disabled={editorDirty || editorBusy || maskDirty || maskBusy || verifying || assistantApplying || decisionBusy !== null}
        onDirtyChange={setPrintDirty} onBusyChange={setPrintBusy} onSavedChange={setPrintSaved}
        initialSaved={Boolean(version.quality.printLayout)}
        onStateChange={setPrintState} externalState={printState}
        onLayoutSaved={setLayoutConfig}
      />}
      {mappingConfig && layoutConfig && after && (
        <section className="form-card mapping-section">
          <div className="section-title">
            <div>
              <h2 ref={mappingSectionHeadingRef} tabIndex={-1}>{activeLayoutStep.title}</h2>
              <p>{activeLayoutStep.description}</p>
            </div>
          </div>
          <nav className="layout-wizard-steps" aria-label="テンプレート設定の工程">
            {LAYOUT_WIZARD_STEPS.map((step, index) => (
              <button
                type="button"
                key={step.id}
                className={step.id === layoutWorkflowStep ? "current" : ""}
                aria-current={step.id === layoutWorkflowStep ? "step" : undefined}
                disabled={navigationBusy || (step.id === "review" && (!identityReady || !documentNameReady))}
                onClick={() => moveLayoutWorkflow(step.id)}
              >
                <span>{index + 1}</span>
                <strong>{step.shortTitle}</strong>
              </button>
            ))}
          </nav>
          {maskDirty && <p className="inline-warning">先に文字候補の選別結果を保存してください。</p>}
          <div className="assistant-editor-workspace">
          {editingEnabled ? <EditingControls store={editingStore}
            holdSelectionContext={analysisStatus.held}
            pageScope={currentPrintPage ? { mode: "current", pageId: `${currentPrintPage.page}:${currentPrintPage.range}` } : { mode: "all", pageId: null }}
            selectedEntities={assistantSelection?.selectedIds ?? []} selectionEpoch={assistantSelection?.selectionEpoch ?? 0}
            manualActive={analysisStatus.held || editorDirty || editorBusy || editorSaving || identityDirty || documentNameDirty || maskDirty || printDirty || maskBusy || printBusy || decisionBusy !== null || Boolean(assistantSelection?.draftReviewRequired)}
            disabled={version.status !== "DRAFT" || activatedVersion?.status === "ACTIVE" ? "編集中の版で音声入力を使用してください。" : loadFailed || !mappingFresh ? "編集状態を取得してから音声入力を使用してください。" : null}
            onSaved={async saved => {
              if (!saved.template) throw new Error("保存済みのテンプレートを取得できません。");
              if (editorDirty || identityDirty || documentNameDirty || maskDirty || printDirty || editorBusy || editorSaving) throw new Error("保存済み・表示更新待ちです。未保存の入力を保持しています。");
              const next = saved.template.authoring;
              if (!layoutEditorRef.current?.acceptAssistantSaved(next.layout, false)) throw new Error("保存済み・表示更新待ちです。");
              acceptRevision(next.revision); setLayoutConfig(next.layout); setMappingConfig(next.mapping);
              setIdentityFields(identityFieldsFromResponse(next.mapping)); setDocumentNameRule(documentNameFromResponse(next.mapping));
              setMappingFresh(true); setLayoutVerified(next.quality.layoutVerified === true);
              setAssistantSaveError(null); setFeedback({ kind: "success", text: saved.result.notification });
            }} /> : <AssistantPanel snapshot={assistantSnapshot} adapter={{
            setApplying: setAssistantApplying,
            saved: async (receipt, discardDraft = false) => {
              try {
                const [next, print] = await Promise.all([api.authoring(version.id), api.printLayout(version.id)]);
                if (next.revision < receipt.revision) throw new Error("保存済みの世代をまだ取得できません。");
                if ((editorDirty || identityDirty || documentNameDirty || maskDirty || printDirty) && !discardDraft) throw new Error("未保存の入力を保持しています。");
                if (editorBusy || editorSaving || maskBusy || printBusy || decisionBusy !== null) throw new Error("既存の保存を先に完了してください。");
                if (!layoutEditorRef.current?.acceptAssistantSaved(next.layout, discardDraft)) throw new Error("編集中の入力を保持しています。保存結果を再確認してください。");
                if (discardDraft && (maskDirty || printDirty)) { setRecoveryDiscardEpoch(current => current + 1); setMaskDirty(false); setPrintDirty(false); }
                acceptRevision(next.revision);
                setLayoutConfig(next.layout);
                setMappingConfig(next.mapping);
                setIdentityFields(identityFieldsFromResponse(next.mapping));
                setDocumentNameRule(documentNameFromResponse(next.mapping));
                setMappingFresh(true);
                setLayoutVerified(next.quality.layoutVerified === true);
                setPrintState(print);
                setAssistantSaveError(null);
                setFeedback({ kind: "success", text: `「${receipt.target.label}」${receipt.undoOfId ? "の変更を取り消しました。" : "の変更を保存しました。"}` });
              } catch (reason) {
                setAssistantSaveError("保存は完了しています。表示の再取得に失敗しました。");
                throw reason;
              }
            },
            begin: () => layoutEditorRef.current?.beginAssistant(),
            end: () => layoutEditorRef.current?.endAssistant(),
            save: async () => {
              if (maskDirty || printDirty || operationBusy || loadFailed) return false;
              return await layoutEditorRef.current?.saveIfNeeded() ?? false;
            },
            discard: () => {
              if (maskDirty || printDirty || operationBusy || !mappingFresh || loadFailed) return false;
              if (!layoutEditorRef.current?.discardUnsaved()) return false;
              setIdentityFields(mappingConfig ? identityFieldsFromResponse(mappingConfig) : []);
              setDocumentNameRule(mappingConfig ? documentNameFromResponse(mappingConfig) : null);
              return true;
            },
          }} />}
          {analysisEnabled && <TemplateAnalysisPanel store={analysisStore}
            structureAvailable={version.mode === "SINGLE_IMAGE_EXPERIMENTAL" && !printSaved && !editingStatus.context?.scopeContext.selectedTableId}
            pageScope={currentPrintPage ? { mode: "current", pageId: `${currentPrintPage.page}:${currentPrintPage.range}` } : { mode: "all", pageId: null }}
            multiplePages={(printState?.saved?.pages.length ?? 0) > 1}
            blocked={layoutEditorDisabled || editorBusy || editorSaving || editingStatus.state !== "ready" || version.status !== "DRAFT" || activatedVersion?.status === "ACTIVE" || Boolean(assistantSelection?.draftReviewRequired)}
            saving={editorSaving} onSave={() => { void layoutEditorRef.current?.saveIfNeeded(); }} />}
          <LayoutEditor
            aiFocus={editingEnabled ? analysisStatus.held ? analysisStatus.focus : analysisStatus.focus ?? editingStatus.focus : null}
            aiContext={editingStatus.context}
            aiViewRevision={authoringRevision}
            ref={layoutEditorRef}
            source={after}
            pageView={printSaved && currentPrintPage ? {
              ...currentPrintPage,
              imageUrl: `/api/v1/template-versions/${version.id}/print-layout/pages/${currentPrintPage.page}/image?sha=${currentPrintPage.referenceImage?.sha256 ?? ""}`,
            } : undefined}
            pageNavigation={printSaved && currentPrintPage && printState?.saved ? {
              currentPage: currentPrintPage.page,
              totalPages: printState.saved.pages.length,
              hasPrevious: authoringPage > 0,
              hasNext: authoringPage < printState.saved.pages.length - 1,
              disabled: operationBusy || printDirty,
              onPrevious: () => setAuthoringPage(current => Math.max(0, current - 1)),
              onNext: () => setAuthoringPage(current => Math.min(printState.saved!.pages.length - 1, current + 1)),
            } : undefined}
            initialLayout={layoutConfig}
            operationEndSave={editingEnabled === true}
            autoSaveUnchanged={editingEnabled === true && (identityDirty || documentNameDirty || (analysisEnabled && analysisStatus.pendingFacets > 0))}
            saveSignal={editingEnabled ? identityFieldsSignature(identityFields) + documentNameSignature(documentNameRule) + (analysisEnabled ? `|${analysisStatus.manualSerial}` : "") : ""}
            onInteractionChange={editingEnabled ? editingInteractionChange : undefined}
            reconcileSaved={editingEnabled ? (current, submitted, saved) => reconcileOwnSave(current, submitted, saved, ownSubstitutionsRef.current) : undefined}
            canUndoSaved={editingStatus.canUndo}
            onUndoSaved={editingEnabled ? async () => {
              const view = await editingStore.undo();
              acceptRevision(view.authoring.revision); setLayoutConfig(view.authoring.layout);
              setMappingConfig(view.authoring.mapping); setLayoutVerified(false);
              setIdentityFields(identityFieldsFromResponse(view.authoring.mapping));
              setDocumentNameRule(documentNameFromResponse(view.authoring.mapping));
              return view.authoring.layout;
            } : undefined}
            targets={mappingConfig.targets}
            workflowStep={layoutWorkflowStep}
            focusRequest={layoutCellFocusRequest}
            onFocusRequestResult={handleLayoutFocusResult}
            documentIdentityFields={identityFields}
            onDocumentIdentityFieldsChange={setIdentityFields}
            documentNameRule={documentNameRule}
            onDocumentNameRuleChange={setDocumentNameRule}
            allowUnchangedSave={!mappingConfig.validForOcr || identityDirty || documentNameDirty || (analysisEnabled && analysisStatus.pendingFacets > 0)}
            disabled={layoutEditorDisabled}
            onDirtyChange={setEditorDirty}
            onBusyChange={setEditorBusy}
            onSavingChange={setEditorSaving}
            onMeaningStateChange={setLayoutMeaningState}
            onAssistantSelectionChange={setAssistantSelection}
            onManualFacets={analysisEnabled ? analysisStore.touch : undefined}
            draftStorageKey={`template-layout-draft-v2:${version.id}`}
            detectLabel={version.mode === "SPREADSHEET_TEMPLATE"
              ? "元のExcelセル構造に戻す"
              : undefined}
            onDetect={async () => api.detectLayout(version.id, revisionRef.current)}
            onRecognizeLabels={async (layout) => api.recognizeLayoutLabels(version.id, revisionRef.current, layout)}
            onSave={async (layout) => {
              setMappingFresh(false);
              try {
                if (editingEnabled === null) throw new Error("編集機能の状態を取得してから保存してください。");
                const draft = { layout, documentIdentityFields: identityFields, documentName: documentNameRule };
                const provenance = analysisEnabled ? analysisStore.captureSave(draft) : undefined;
                const manualSaved = editingEnabled ? await editingStore.save(draft,
                  currentPrintPage ? { mode: "current", pageId: `${currentPrintPage.page}:${currentPrintPage.range}` }
                    : { mode: "all", pageId: null }, provenance ? { analysisMergeIds: provenance.analysisMergeIds, manualTouchedFacets: provenance.manualTouchedFacets } : {}) : null;
                const saved = manualSaved ? manualSaved.view.authoring : await api.saveAuthoring(version.id, revisionRef.current, {
                  layout, documentIdentityFields: identityFields, documentName: documentNameRule,
                });
                ownSubstitutionsRef.current = manualSaved?.result.substitutions ?? {};
                acceptRevision(saved.revision);
                if (provenance) analysisStore.saved(provenance, manualSaved?.result.substitutions ?? {});
                setLayoutConfig(saved.layout);
                setLayoutVerified(false);
                setMappingConfig(saved.mapping);
                // A late response must not replace input made after this save started.
                setIdentityFields(current => manualSaved ? reconcileOwnSave(current, identityFields,
                  identityFieldsFromResponse(saved.mapping), ownSubstitutionsRef.current)
                  : identityFieldsSignature(current) === identityFieldsSignature(identityFields)
                  ? identityFieldsFromResponse(saved.mapping) : current);
                setDocumentNameRule(current => manualSaved ? reconcileOwnSave(current, documentNameRule,
                  documentNameFromResponse(saved.mapping), ownSubstitutionsRef.current)
                  : documentNameSignature(current) === documentNameSignature(documentNameRule)
                  ? documentNameFromResponse(saved.mapping) : current);
                let printDisplayPending = false;
                if (printSaved) {
                  try { setPrintState(await api.printLayout(version.id)); }
                  catch (error) {
                    setFeedback({ kind: "error", text: `設定は保存済みですが、印刷表示の更新に失敗しました。${errorMessage(error, "")}` });
                    if (!manualSaved) throw error;
                    printDisplayPending = true;
                  }
                }
                setMappingFresh(true);
                if (!printDisplayPending) setFeedback({ kind: saved.mapping.fieldErrors?.length ? "error" : "success",
                  text: saved.mapping.fieldErrors?.length
                    ? `レイアウトを途中保存しました。${saved.mapping.warnings.join(" ")}`
                    : manualSaved ? manualSaved.result.notification
                    : `レイアウト${saved.layout.cells.length}セル、意味づけ${saved.layout.relations.length}件と確認する欄・ファイル名を保存しました。`,
                });
                return saved.layout;
              } catch (error) {
                setFeedback({ kind: "error", text: errorMessage(error, "保存状態を確認してください。入力は保持しています。") });
                throw error;
              }
            }}
          />
          {editingEnabled && editingStatus.state !== "ready" && editingStatus.state !== "saving" && (
            <section className="inline-warning" aria-label="編集保存の復旧">
              <p role="status">{editingStatus.message} 未保存の入力は画面に保持しています。</p>
              <button type="button" disabled={editingRecoveryBusy} onClick={async () => {
                setEditingRecoveryBusy(true);
                try {
                  const retained = editingStore.retainedDraft();
                  const { view, result } = await editingStore.recover();
                  ownSubstitutionsRef.current = result.substitutions ?? {};
                  acceptRevision(view.authoring.revision); setLayoutConfig(view.authoring.layout);
                  setMappingConfig(view.authoring.mapping); setMappingFresh(true); setLayoutVerified(false);
                  // A recovered response must not erase input typed after the lost
                  // response. The editor accepts only an unchanged retained draft.
                  if (retained) {
                    layoutEditorRef.current?.acceptOwnSaved(retained.layout, view.authoring.layout);
                    setIdentityFields(current => reconcileOwnSave(current, retained.documentIdentityFields,
                      identityFieldsFromResponse(view.authoring.mapping), result.substitutions ?? {}));
                    setDocumentNameRule(current => reconcileOwnSave(current, retained.documentName,
                      documentNameFromResponse(view.authoring.mapping), result.substitutions ?? {}));
                  }
                  if (analysisEnabled) analysisStore.saved(undefined, result.substitutions ?? {});
                  setFeedback({ kind: "success", text: result.notification });
                } catch (error) { setFeedback({ kind: "error", text: errorMessage(error, "保存結果をまだ確認できません。") }); }
                finally { setEditingRecoveryBusy(false); }
              }}>保存結果を再取得</button>
              {(editingStatus.state === "conflict" || editingStatus.state === "failed") && <button type="button" disabled={editingRecoveryBusy} onClick={async () => {
                setEditingRecoveryBusy(true);
                try { setEditingReview(await editingStore.inspectLatest()); }
                catch (error) { setFeedback({ kind: "error", text: errorMessage(error, "最新の保存版を取得できません。") }); }
                finally { setEditingRecoveryBusy(false); }
              }}>最新の保存版を確認</button>}
              {editingReview && <div>
                <p>保存版の変更点を確認してください。編集再開時は未編集の箇所に保存版を反映し、未保存の入力を保持します。次の操作までは再送しません。</p>
                <table><thead><tr><th>保存版の項目</th><th>名称</th></tr></thead><tbody>
                  {editingReview.authoring.layout.cells.flatMap((cell, index) => {
                    const previous = layoutConfig.cells.find(item => item.id === cell.id);
                    return !previous || JSON.stringify(previous) !== JSON.stringify(cell)
                      ? [<tr key={cell.id}><td>{cell.printedLabelText || cell.text || `欄 ${index + 1}`}</td><td>{cell.fieldLabel || cell.text || "名称未設定"}</td></tr>] : [];
                  })}
                </tbody></table>
                <p>変更された意味：{editingReview.authoring.layout.labelPool?.filter(entry => JSON.stringify(layoutConfig.labelPool?.find(previous => previous.id === entry.id)) !== JSON.stringify(entry)).map(entry => entry.label).join("、") || "変更なし"}。変更された表：{editingReview.authoring.layout.recordSections?.filter(section => JSON.stringify(layoutConfig.recordSections?.find(previous => previous.id === section.id)) !== JSON.stringify(section)).map(section => section.displayName || section.title).join("、") || "変更なし"}。</p>
                <button type="button" onClick={async () => {
                  try {
                  await editingStore.resumeAfterReview(editingReview);
                  layoutEditorRef.current?.acceptReviewedSaved(layoutConfig, editingReview.authoring.layout);
                  setIdentityFields(current => reconcileOwnSave(current, identityFieldsFromResponse(mappingConfig), identityFieldsFromResponse(editingReview.authoring.mapping), {}));
                  setDocumentNameRule(current => reconcileOwnSave(current, documentNameFromResponse(mappingConfig), documentNameFromResponse(editingReview.authoring.mapping), {}));
                  setLayoutConfig(editingReview.authoring.layout); setMappingConfig(editingReview.authoring.mapping); setMappingFresh(true);
                  acceptRevision(editingReview.authoring.revision); setEditingReview(null);
                  setFeedback({ kind: "success", text: "入力を保持して編集を再開しました。次の手動操作で保存します。" });
                  } catch (error) { setFeedback({ kind: "error", text: errorMessage(error, "編集を再開できません。入力は保持しています。") }); }
                }}>保存版を確認して編集を再開</button>
              </div>}
            </section>
          )}
          </div>
          <div className="layout-wizard-navigation">
            <button
              type="button"
              className="secondary"
              disabled={layoutWorkflowIndex === 0 || navigationBusy}
              onClick={() => moveLayoutWorkflow(
                LAYOUT_WIZARD_STEPS[Math.max(0, layoutWorkflowIndex - 1)].id,
              )}
            >
              <FiArrowLeft aria-hidden="true" />前の工程
            </button>
            <span>
              {navigationPending
                ? "変更を保存してから移動します。"
                : layoutWorkflowStep === "identity" && (!identityReady || !documentNameReady)
                ? "確認する欄と、保存するファイル名を決めてください。"
                : layoutWorkflowIndex < LAYOUT_WIZARD_STEPS.length - 1
                  ? null
                  : "未割当と、書類を見分ける項目を確認して保存してください。"}
            </span>
            {layoutWorkflowIndex < LAYOUT_WIZARD_STEPS.length - 1 && (
              <button
                type="button"
                className="primary"
                disabled={navigationBusy || (layoutWorkflowStep === "identity" && (!identityReady || !documentNameReady))}
                onClick={() => moveLayoutWorkflow(LAYOUT_WIZARD_STEPS[layoutWorkflowIndex + 1].id)}
              >
                次の工程<FiArrowRight aria-hidden="true" />
              </button>
            )}
          </div>
        </section>
      )}
      {meaningSaved && !printSaved && !effectiveLayoutVerified && <section className="form-card verification-gate"><div><h2>{isSpreadsheetTemplate ? "XLSXのセル構造を確定" : "記入欄を実データで再検出"}</h2><p>{editorDirty ? "先に未保存のレイアウト変更を保存してください。" : isSpreadsheetTemplate ? "XLSXテンプレートでは実画像による再検出を行わず、確認済みのセル構造と印刷プレビューを読み取り基準として確定します。" : "保存した読み取り範囲と帳票画像が対応しているか確認します。"}</p></div><button className="primary" type="button" disabled={verifying || editorBusy || editorDirty || assistantApplying} onClick={async () => {
        if (verificationLockRef.current || editorDirty || editorBusy) return;
        verificationLockRef.current = true;
        setVerifying(true);
        setFeedback(null);
        try {
          const verified = await api.verifyLayout(version.id, revisionRef.current);
          acceptRevision(verified.resourceRevision);
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
      {mask && layoutVerified && mappingFresh && <section className="form-card"><div className="section-title"><div><h2>再検出した文字候補の選別</h2><p>保存済みの記入欄内をOpenCVで再検証した候補です。任意の文字認識機能がある場合は候補を補助します。</p></div></div>{editorDirty && <p className="inline-warning">先にレイアウト変更を保存し、記入欄を再検出してください。</p>}<MaskEditor key={recoveryDiscardEpoch} source={mask} background={before} candidatesSource={byName.get("handwritingCandidates")} disabled={editorDirty || editorBusy || assistantApplying} onDirtyChange={setMaskDirty} onBusyChange={setMaskBusy} onSave={async (blob) => {
        acceptRevision((await api.saveMask(version.id, revisionRef.current, blob)).resourceRevision);
        if (version.status === "DRAFT" && !version.quality.forkParentVersionId) {
          setMaskRequiresReprocess(true);
          setFeedback({ kind: "success", text: "選別結果を保存しました。「選別マスクから再生成」で新しい下書きへ反映してください。" });
        } else {
          setLayoutVerified(false);
          setFeedback({ kind: "success", text: "選別結果を保存しました。記入欄を再検出すると、次のOCRから反映されます。" });
        }
      }} /></section>}
      {feedback && <p className={feedback.kind === "error" ? "error-box" : "success-box"} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.text}</p>}
      {approvalPendingConfirmation && <button type="button" className="secondary"
        disabled={assistantApplying || decisionBusy !== null} onClick={() => void recheckApproval()}>承認結果を再取得</button>}
      {activatedVersion && <div className="decision-bar">
        <div><strong>このテンプレートは有効です</strong></div>
      </div>}
      {version.status === "DRAFT" && !activatedVersion && <div className="decision-bar">
        <div><strong>{printSaved
          ? preparationMessage || "設定したページと欄でOCRを始めます"
          : "このテンプレート設定を採用しますか？"}</strong>
          <span>{editorDirty || maskDirty || identityDirty || documentNameDirty
            ? "未保存の変更があります。保存してから次へ進んでください。"
            : printSaved
              ? "必要な印刷見本と読み取り準備は自動で行います。読み取り後に結果を確認・修正してください。"
              : isSpreadsheetTemplate
                ? "設定を保存し、XLSX構造を確定してから承認してください。"
                : "設定を保存し、読み取り確認を完了してから承認してください。"}</span></div>
        <button className="danger-button" disabled={operationBusy} onClick={() => void rejectVersion()}>
          <FiX aria-hidden="true" />{decisionBusy === "reject" ? "却下中…" : "却下"}</button>
        {!version.quality.forkParentVersionId && <button className="secondary"
          disabled={operationBusy || printSaved || printDirty || maskDirty || (!effectiveLayoutVerified && !maskRequiresReprocess) || !meaningSaved}
          onClick={() => void reprocessVersion()}>{decisionBusy === "reprocess" ? "再生成中…" : "選別マスクから再生成"}</button>}
        <button className="primary"
          disabled={operationBusy || printDirty || editorDirty || identityDirty || documentNameDirty || maskDirty
            || approvalPendingConfirmation || (printSaved ? !meaningSaved || !printState?.revision
              : maskRequiresReprocess || !effectiveLayoutVerified || !meaningSaved)}
          onClick={() => void approveVersion()}><FiCheck aria-hidden="true" />{decisionBusy === "approve" ? (printSaved ? "準備中…" : "承認中…") : printSaved ? "このテンプレートでOCRを使う" : "承認して有効化"}</button>
      </div>}
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
  return config.documentIdentityFields;
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

export function mappingWarningsForCurrentDraft(
  warnings: string[],
  fieldErrors: FieldMappingsResponse["fieldErrors"],
  state: LayoutEditorMeaningState | null,
) {
  if (!state) return warnings;
  const markTarget = markOptionsTargetFromFieldErrors(fieldErrors);
  const incompleteMarkIds = new Set(state.incompleteMarkCellIds);
  return warnings.filter((warning) => {
    if (warning === "読み取りに使用できる意味づけ済みセルがありません。") {
      return state.usableMeaningCellCount === 0;
    }
    if (!warning.startsWith("[MARK_OPTIONS_REQUIRED]") || !markTarget) return true;
    const currentCell = state.cells.find((cell) => cell.id === markTarget.cellId);
    if (currentCell?.role !== "mark") return false;
    if (markTarget.purpose === "mark-cell") {
      return currentCell.markSetIds.length === 0 || incompleteMarkIds.has(currentCell.id);
    }
    return currentCell.markSetIds.includes(markTarget.markSetId)
      && incompleteMarkIds.has(currentCell.id);
  });
}

export function mappingWarningWorkflowStep(warning: string): LayoutWorkflowStep | null {
  if (
    warning === "読み取りに使用できる意味づけ済みセルがありません。"
    || warning.startsWith("[MARK_OPTIONS_REQUIRED]")
  ) return "meaning";
  if (
    warning.startsWith("書類を見分ける項目")
    || warning.startsWith("書類を見分ける基準")
    || warning.startsWith("一覧で大きく表示する項目")
    || warning.startsWith("従来の照合条件")
    || warning.startsWith("照合グループ名")
    || warning.startsWith("ライブラリと出力で使う書類名")
  ) return "identity";
  return null;
}

export function markOptionsTargetFromFieldErrors(
  fieldErrors: FieldMappingsResponse["fieldErrors"],
): ({ cellId: string } & (
  { purpose: "mark-cell" } | { purpose: "mark-options"; markSetId: string }
)) | null {
  type Target = { cellId: string } & (
    { purpose: "mark-cell" } | { purpose: "mark-options"; markSetId: string }
  );
  const targets = new Map<string, Target>();
  (fieldErrors ?? []).forEach(({ field }) => {
    const match = /^cells\.([^.]+)\.markSets(?:\.([^.]+)\.options)?$/.exec(field);
    if (!match) return;
    const target: Target = match[2]
      ? { cellId: match[1], markSetId: match[2], purpose: "mark-options" }
      : { cellId: match[1], purpose: "mark-cell" };
    targets.set(`${target.cellId}\u0000${match[2] ?? ""}`, target);
  });
  const uniqueTargets = [...targets.values()];
  if (new Set(uniqueTargets.map((target) => target.cellId)).size !== 1) return null;
  return uniqueTargets.length === 1
    ? uniqueTargets[0]
    : { cellId: uniqueTargets[0].cellId, purpose: "mark-cell" };
}

export function firstMeaningFocusTarget(
  state: LayoutEditorMeaningState | null,
): { cellId: string; purpose: "meaning-cell" } | null {
  if (!state) return null;
  const cellId = state.unassignedCellIds[0] ?? state.incompleteMarkCellIds[0];
  return cellId ? { cellId, purpose: "meaning-cell" } : null;
}

export function savedPrintPageForCell(
  pages: readonly PrintPage[],
  cellId: string,
): PrintPage | null {
  const matches = pages.filter((page) => page.fields.some((field) => (
    field.cellId === cellId
    || field.sourceParts?.some((part) => part.cellId === cellId)
  )));
  return matches.length === 1 ? matches[0] : null;
}

function documentNameFromResponse(config: FieldMappingsResponse): DocumentNameRule | null {
  return config.documentName;
}

function documentNameSignature(rule: DocumentNameRule | null) {
  return JSON.stringify(rule);
}
