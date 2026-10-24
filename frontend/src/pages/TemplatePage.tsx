import { userText } from "../userMessages";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { v2client as editingClient, EditingError } from "../editing/v2client";
import { templateEditingStore, type LoadedManualView } from "../editing/workbenchStore";
import { reconcileOwnSave } from "../editing/reconcile";
import { TemplateAnalysisStore } from "../editing/templateAnalysisStore";
import { TemplateAnalysisStatus } from "../editing/TemplateAnalysisStatus";
import { FiAlertCircle, FiArrowLeft, FiArrowRight, FiCheck, FiChevronDown, FiCopy, FiFileText, FiImage, FiGrid, FiPrinter, FiTrash2, FiX } from "react-icons/fi";
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
  type LayoutEditorNotice,
  type LayoutWorkflowStep,
} from "../components/LayoutEditor";
import { TemplateFieldSettingsEditor } from "../components/TemplateFieldSettingsEditor";
import { TemplateDeleteDialog, TemplateTrashPanel } from "../components/TemplateTrash";
import { ITEM_JOIN_EDITING_VISIBLE, openItemJoinEditor } from "../components/TemplateItemJoinEditor";
import { showToast } from "../components/Toast";
import { useJobPoller } from "../hooks/useJobPoller";
import { templatePreviewKey, useTemplatePreviews } from "../hooks/useTemplatePreviews";
import type { Artifact, DocumentIdentity, DocumentNameRule, FieldMappingsResponse, Job, LayoutDefinition, Template, TemplateVersion } from "../types";
import { isEditingDraft, suggestedCopyName } from "../templateEditing";
import { ImageSpinner, Spinner } from "../components/Spinner";

const MODES = [
  { id: "SINGLE_IMAGE_EXPERIMENTAL", number: "01", title: "記入済みの一枚から", copy: "記入済み帳票1枚を補正し、枠と読み取り欄を画面で設定します。", count: "記入済み・1枚" },
  { id: "REFERENCE_TEMPLATE", number: "02", title: "白紙帳票から", copy: "記入前の白紙帳票を基準画像として使用します。", count: "白紙・1枚" },
  { id: "SPREADSHEET_TEMPLATE", number: "03", title: "元のExcelから", copy: "XLSXのセル、罫線、結合から枠と区分の案を自動作成します。", count: "XLSX・1ファイル" },
  { id: "COPY", number: "04", title: "既存のテンプレートから", copy: "使用中のテンプレートを複製し、そのまま編集します。", count: "既存・1件" },
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
  { id: "identity", shortTitle: "確認する欄", title: "読み取り後に確認する欄を決める", description: "氏名や番号など、読み取り後に「この書類で合っているか」を確かめる欄を決めます。" },
];

type TemplateWorkspace = "create" | "manage";
type TemplateListFilter = "current" | "draft" | "active";

export function TemplatePage({
  templates,
  refresh,
  rememberJob,
  onNavigationGuardChange,
  initialWorkspace,
  initialEditVersionId,
  initialEditTarget,
  onClose,
  onTemplateActivated,
}: {
  templates: Template[];
  refresh: () => Promise<Template[] | void>;
  rememberJob: (id: string) => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
  initialWorkspace?: TemplateWorkspace;
  initialEditVersionId?: string;
  initialEditTarget?: "structure" | "fields";
  onClose?: () => void;
  onTemplateActivated?: (versionId: string) => void;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("SINGLE_IMAGE_EXPERIMENTAL");
  const [name, setName] = useState("");
  const nameEditedRef = useRef(false);
  const [fieldNames, setFieldNames] = useState<string[]>([]);
  const [fieldNameDraft, setFieldNameDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  // 「既存のテンプレートから」: the template series to copy.
  const [copySourceId, setCopySourceId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobPollRevision, setJobPollRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [openingReview, setOpeningReview] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<TemplateWorkspace | null>(initialWorkspace ?? null);
  const [templateFilter, setTemplateFilter] = useState<TemplateListFilter>("current");
  const [deletingTemplate, setDeletingTemplate] = useState<Template | null>(null);
  const [showingTrash, setShowingTrash] = useState(false);
  const [candidateEditor, setCandidateEditor] = useState<{
    template: Template;
    version: TemplateVersion;
    returnToReading?: boolean;
  } | null>(() => {
    if (initialEditTarget !== "fields" || !initialEditVersionId) return null;
    const owner = templates.find((template) => template.versions.some((candidate) => candidate.id === initialEditVersionId));
    const version = owner?.versions.find((candidate) => candidate.id === initialEditVersionId);
    return owner && version ? { template: owner, version, returnToReading: true } : null;
  });
  const [review, setReview] = useState<{
    templateId: string;
    version: TemplateVersion;
    jobId: string;
    origin: TemplateWorkspace;
    returnToReading?: boolean;
  } | null>(() => {
    if (!initialEditVersionId || initialEditTarget === "fields") return null;
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
  // When each template was last opened in this browser; the list shows the latest first.
  const [openedAt, setOpenedAt] = useState<Record<string, number>>(readOpenedAt);
  const openedTemplateId = review?.templateId ?? null;
  useEffect(() => {
    if (!openedTemplateId) return;
    setOpenedAt((current) => {
      const next = { ...current, [openedTemplateId]: Date.now() };
      try { window.localStorage.setItem(OPENED_AT_KEY, JSON.stringify(next)); } catch { /* per-browser order only */ }
      return next;
    });
  }, [openedTemplateId]);
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
        const hasDraft = versions.some(isEditingDraft);
        const hasActive = versions.some((version) => version.status === "ACTIVE");
        if (templateFilter === "draft") return hasDraft;
        if (templateFilter === "active") return hasActive;
        return hasDraft || hasActive;
      })
      .sort((first, second) => {
        const opened = (openedAt[second.template.id] ?? 0) - (openedAt[first.template.id] ?? 0);
        if (opened !== 0) return opened;
        const firstDraft = first.versions.some(isEditingDraft);
        const secondDraft = second.versions.some(isEditingDraft);
        if (firstDraft !== secondDraft) return firstDraft ? -1 : 1;
        const firstDate = Date.parse(first.versions[0]?.createdAt ?? first.template.createdAt);
        const secondDate = Date.parse(second.versions[0]?.createdAt ?? second.template.createdAt);
        return secondDate - firstDate;
      });
  }, [openedAt, templateFilter, templates]);
  const listedPreviewVersions = useMemo(() => listedTemplates.flatMap(({ versions }) => {
    const version = versions.find((item) => item.status === "DRAFT") ?? versions.find((item) => item.status === "ACTIVE") ?? versions[0];
    return version ? [version] : [];
  }), [listedTemplates]);
  const listedPreviewFor = useTemplatePreviews(listedPreviewVersions, selectedWorkspace === "manage" && !review);
  const [listedImageState, setListedImageState] = useState<Record<string, "loaded" | "failed">>({});
  const draftCount = templates.filter((template) => (
    template.versions.some(isEditingDraft)
  )).length;
  // Copies start from the template in use; one without its source image cannot be edited.
  const copySources = useMemo(() => templates.flatMap((template) => {
    const version = template.versions.find((item) => item.status === "ACTIVE");
    return version?.sourceJobId ? [{ template, version }] : [];
  }), [templates]);
  const copySource = copySources.find(({ template }) => template.id === copySourceId) ?? null;
  const copyPreviewVersions = useMemo(() => copySources.map(({ version }) => version), [copySources]);
  const copyPreviewFor = useTemplatePreviews(
    copyPreviewVersions, selectedWorkspace === "create" && mode === "COPY" && !review,
  );
  const chooseCopySource = (template: Template) => {
    idempotencyKeyRef.current = null;
    setCopySourceId(template.id);
    if (!nameEditedRef.current) setName(suggestedCopyName(template.name, templates.map((item) => item.name)));
  };
  const startCopy = (template: Template) => {
    setMode("COPY");
    setFiles([]);
    nameEditedRef.current = false;
    chooseCopySource(template);
    setWorkspace("create");
  };
  const requiredCountValid = mode === "COPY" ? copySource !== null : files.length === 1;
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
        // Optional, after activation: show scattered items (an address, a date) as one value.
        showToast(ITEM_JOIN_EDITING_VISIBLE ? {
          tone: "success", title: "テンプレートを使えるようになりました", duration: 12000,
          message: "住所や日付など、ばらばらの欄を1つにまとめて表示できます（任意）。",
          actionLabel: "まとめ方を設定", onAction: () => openItemJoinEditor(review.templateId, versionId),
        } : { tone: "success", title: "テンプレートを使えるようになりました" });
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
    return <TemplateFieldSettingsEditor
      template={candidateEditor.template}
      version={candidateEditor.version}
      closeLabel={candidateEditor.returnToReading ? "← 読み取りへ戻る" : undefined}
      onNavigationGuardChange={onNavigationGuardChange}
      onClose={() => {
        if (candidateEditor.returnToReading && onClose) { onClose(); return; }
        setWorkspace("manage");
        setCandidateEditor(null);
      }}
    />;
  }
  const copyTemplate = async (source: Template, version: TemplateVersion) => {
    creatingRef.current = true;
    setBusy(true); setMessage(null);
    try {
      const result = await api.duplicateTemplateVersion(version.id, version.revision, templateName, true);
      const details = (await refresh()) ?? [];
      const copied = details.find((item) => item.id === result.templateId) ?? await api.template(result.templateId);
      const copiedVersion = copied.versions.find((item) => item.id === result.templateVersionId);
      showToast({
        tone: "success", title: "テンプレートを複製しました",
        message: `「${source.name}」をもとに「${copied.name}」を作成しました。`,
      });
      setName(""); nameEditedRef.current = false; setCopySourceId(null);
      if (copiedVersion?.sourceJobId) {
        setReview({ templateId: copied.id, version: copiedVersion, jobId: copiedVersion.sourceJobId, origin: "manage" });
      } else {
        setWorkspace("manage");
      }
    } catch (error) {
      if (error instanceof ApiError) {
        setMessage(errorMessage(error, "テンプレートを複製できませんでした。"));
      } else {
        try { await refresh(); } catch { /* The outcome is still unknown. */ }
        setMessage("複製できたか確認できませんでした。「既存を確認・修正」の一覧を確認してから再操作してください。");
      }
    } finally {
      creatingRef.current = false;
      setBusy(false);
    }
  };
  return (
    <div className="page-stack narrow template-setup-workspace">
      <header className={`page-header split${onClose ? " has-back" : ""}`}>
        {onClose && <button type="button" className="ghost page-back" onClick={closeWorkspace}><FiArrowLeft aria-hidden="true" />読み取りへ戻る</button>}
        <div><h1>テンプレート作成・編集</h1><p>新しい帳票のテンプレートを作成するか、既存のテンプレートを編集します。</p></div>
      </header>
      {message && <div className="error-box action-message" role="alert"><span>{message}</span>{job?.status === "REVIEW_REQUIRED" && <button type="button" className="secondary compact" disabled={openingReview} onClick={() => void openPreparedTemplate(job)}>{openingReview ? <><Spinner />再取得中…</> : "確認画面を再取得"}</button>}</div>}
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
              if (item.id === "COPY" && copySource && !nameEditedRef.current) chooseCopySource(copySource.template);
            }}>
              <span className="template-mode-heading">{item.id === "COPY" ? <FiCopy aria-hidden="true" /> : item.id === "SPREADSHEET_TEMPLATE" ? <FiGrid aria-hidden="true" /> : item.id === "REFERENCE_TEMPLATE" ? <FiFileText aria-hidden="true" /> : <FiImage aria-hidden="true" />}<strong>{item.title}</strong><span className="template-mode-selected">{mode === item.id && <FiCheck aria-label="選択中" />}</span></span><p>{item.copy}</p><small>{item.count}</small>
            </button>
          ))}</div></fieldset>
          {mode === "COPY" ? <fieldset className="template-create-section"><legend><span className="template-section-number">2</span> もとにするテンプレートを選ぶ</legend>
            {copySources.length === 0 ? <p className="template-copy-empty">複製できる使用中のテンプレートはありません。</p> : (
              <div className="template-copy-grid">{copySources.map(({ template, version }) => {
                const preview = copyPreviewFor(version);
                const previewKey = templatePreviewKey(version);
                const selected = copySourceId === template.id;
                return (
                  <button type="button" key={template.id} className={`template-copy-card${selected ? " selected" : ""}`}
                    aria-pressed={selected} onClick={() => chooseCopySource(template)}>
                    <span className="ocr-template-card-visual">
                      {(preview?.loading || (Boolean(preview?.url) && !listedImageState[previewKey]))
                        && <ImageSpinner />}
                      {(!preview?.loading && (!preview?.url || listedImageState[previewKey] === "failed"))
                        && <ImageSpinner />}
                      {preview?.url && <img key={previewKey} src={preview.url} alt="" loading="lazy"
                        className={listedImageState[previewKey] === "loaded" ? "loaded" : ""}
                        onLoad={() => setListedImageState((current) => ({ ...current, [previewKey]: "loaded" }))}
                        onError={() => setListedImageState((current) => ({ ...current, [previewKey]: "failed" }))} />}
                    </span>
                    <span className="template-copy-card-name">
                      <strong title={template.name}>{template.name}</strong>
                      {selected && <FiCheck aria-label="選択中" />}
                    </span>
                  </button>
                );
              })}</div>
            )}
          </fieldset> : <fieldset className="template-create-section"><legend><span className="template-section-number">2</span> {mode === "SPREADSHEET_TEMPLATE" ? "Excelファイルを選ぶ" : "帳票画像を選ぶ"}</legend>
          <FilePicker
            key={mode}
            multiple={false}
            hideWhenChosen
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
          </fieldset>}
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
          <div className="template-create-submit"><p>{mode === "COPY" ? "複製したテンプレートの編集画面が開きます。元のテンプレートは変わりません。" : "準備後に、読み取る欄と内容を確認できます。"}</p>
          <button className="primary full" disabled={busy || submissionLocked || !templateName || !requiredCountValid || Boolean(duplicateTemplate)} onClick={async () => {
            if (creatingRef.current || submissionLocked) return;
            if (mode === "COPY") {
              if (copySource) void copyTemplate(copySource.template, copySource.version);
              return;
            }
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
          }}><FiArrowRight aria-hidden="true" />{mode === "COPY" ? (busy ? <><Spinner />複製中…</> : "複製して編集を開始") : busy ? <><Spinner />送信中…</> : submissionLocked ? "テンプレート準備を開始しました" : "テンプレート準備を開始"}</button>
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
        {showingTrash ? <TemplateTrashPanel onClose={() => setShowingTrash(false)} onRestored={async () => { await refresh(); }} /> : <>
        <div className="template-list-toolbar">
          <button type="button" className="ghost compact template-trash-open" onClick={() => setShowingTrash(true)}><FiTrash2 aria-hidden="true" />ごみ箱</button>
          <label className="field-label">表示する状態<select value={templateFilter} onChange={(event) => setTemplateFilter(event.target.value as TemplateListFilter)}>
            <option value="current">作業途中・使用中</option>
            <option value="draft">作業途中のみ</option>
            <option value="active">使用中のみ</option>
          </select></label>
        </div>
        <div className="template-management-list" aria-label="既存テンプレート一覧">
          {listedTemplates.map(({ template, versions }) => {
            const draft = versions.find((version) => version.status === "DRAFT") ?? null;
            const active = versions.find((version) => version.status === "ACTIVE") ?? null;
            const primaryVersion = draft ?? active;
            const printVersion = active ?? primaryVersion;
            const editing = Boolean(draft && isEditingDraft(draft));
            const status = editing ? "draft" : active ? "active" : "history";
            const statusLabel = editing ? "作業途中" : active ? "使用中" : "過去のテンプレート";
            const isDeletingCurrentTemplate = deletingTemplate?.id === template.id;
            const thumbnailVersion = primaryVersion ?? versions[0];
            const preview = thumbnailVersion ? listedPreviewFor(thumbnailVersion) : undefined;
            const previewKey = thumbnailVersion ? templatePreviewKey(thumbnailVersion) : "";
            return (
              <article className="template-management-card" key={template.id}>
                <button type="button" className="template-management-visual ocr-template-card-visual"
                  aria-label={primaryVersion ? `${template.name}を開く` : `${template.name}の書類見本`}
                  disabled={!primaryVersion?.sourceJobId || isDeletingCurrentTemplate || openingReview}
                  onClick={() => primaryVersion?.sourceJobId && setReview({
                    templateId: template.id, version: primaryVersion, jobId: primaryVersion.sourceJobId, origin: "manage",
                  })}>
                  {(preview?.loading || (Boolean(preview?.url) && !listedImageState[previewKey]))
                    && <ImageSpinner />}
                  {(!preview?.loading && (!preview?.url || listedImageState[previewKey] === "failed"))
                    && <ImageSpinner />}
                  {preview?.url && <img key={previewKey} src={preview.url} alt="" loading="lazy"
                    className={listedImageState[previewKey] === "loaded" ? "loaded" : ""}
                    onLoad={() => setListedImageState((current) => ({ ...current, [previewKey]: "loaded" }))}
                    onError={() => setListedImageState((current) => ({ ...current, [previewKey]: "failed" }))} />}
                </button>
                <div className="template-management-main">
                  <div className="template-management-title">
                    <span className={`template-version-status ${status}`}>{statusLabel}</span>
                    <h3 title={template.name}>{template.name}</h3>
                  </div>
                  {primaryVersion ? (
                    <p>{templateModeLabel(primaryVersion.mode)} · 更新 {formatTemplateDateTime(primaryVersion.createdAt)}</p>
                  ) : (
                    <p>現在使用できる設定はありません。</p>
                  )}
                </div>
                <div className="template-management-action">
                  {primaryVersion && (
                    <TemplateEditMenu
                      label={draft ? "作成を再開" : "編集"}
                      primary={Boolean(draft)}
                      disabled={!primaryVersion.sourceJobId || isDeletingCurrentTemplate || openingReview}
                      structureLabel={draft ? "作成を再開する" : "構造を編集する"}
                      structureAriaLabel={draft ? draftResumeLabel(template.name, primaryVersion) : `${template.name}のテンプレートを編集`}
                      onStructure={() => primaryVersion.sourceJobId && setReview({
                        templateId: template.id,
                        version: primaryVersion,
                        jobId: primaryVersion.sourceJobId,
                        origin: "manage",
                      })}
                      onFields={() => setCandidateEditor({ template, version: active ?? primaryVersion })}
                      onCopy={active?.sourceJobId ? () => startCopy(template) : undefined}
                    />
                  )}
                  {primaryVersion && (
                    <button
                      type="button"
                      className="secondary compact template-print-button"
                      aria-label={`${template.name}を印刷`}
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
                  {printVersion?.mode === "SPREADSHEET_TEMPLATE" && printVersion.quality.printLayout ? <div>
                    <small>印刷用帳票</small>
                    <TemplatePrintFiles key={printVersion.id}
                      versionId={printVersion.id} disabled={isDeletingCurrentTemplate || openingReview} />
                  </div> : null}

                  <button
                    type="button"
                    className="danger-button compact"
                    disabled={Boolean(deletingTemplate) || openingReview}
                    onClick={() => setDeletingTemplate(template)}
                  >
                    削除
                  </button>
                  {!primaryVersion?.sourceJobId && <small>元画像がないため編集できません</small>}
                  {!primaryVersion && <small>編集できる設定がありません</small>}
                </div>
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
        </>}
        {deletingTemplate && <TemplateDeleteDialog template={deletingTemplate} templates={templates}
          onClose={() => setDeletingTemplate(null)}
          onDeleted={async () => { setDeletingTemplate(null); try { await refresh(); } catch { /* The next load shows it. */ } }} />}
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
      : <p role="status"><Spinner />編集用の下書きを準備しています…</p>}
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
  const [printEditorOpen, setPrintEditorOpen] = useState(false);
  useEffect(() => {
    // The issue list is a dropdown: a click elsewhere or Esc closes it.
    if (!mappingWarningsExpanded) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape"
        : !(event.target instanceof Element && event.target.closest(".mapping-warning-panel"))) setMappingWarningsExpanded(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", close); };
  }, [mappingWarningsExpanded]);
  const [mappingWarningFocusRequest, setMappingWarningFocusRequest] = useState(0);
  const [mappingWarningNotices, setMappingWarningNotices] = useState<Record<string, string>>({});
  const [layoutCellFocusRequest, setLayoutCellFocusRequest] = useState<LayoutEditorFocusRequest | null>(null);
  const [layoutMeaningState, setLayoutMeaningState] = useState<LayoutEditorMeaningState | null>(null);
  const [editorNotices, setEditorNotices] = useState<LayoutEditorNotice[]>([]);
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
  const draftMappingWarnings = layoutConfig && !layoutMeaningState
    ? []
    : mappingWarningsForCurrentDraft(
      identityVisibleMappingWarnings,
      mappingConfig?.fieldErrors,
      layoutMeaningState,
    );
  // The editor reports the same conditions from the current draft; the saved ones would repeat them.
  const noticeKeys = new Set(editorNotices.map(notice => notice.key.split(":")[0]));
  const visibleMappingWarnings = draftMappingWarnings.filter(warning => !(
    (noticeKeys.has("incomplete") && isChoiceSetupWarning(warning))
    || (noticeKeys.has("no-meaning") && warning === "読み取りに使用できる意味づけ済みセルがありません。")
    || ((noticeKeys.has("identity") || noticeKeys.has("document-name")) && mappingWarningWorkflowStep(warning) === "identity")
  ));
  const pendingCount = visibleMappingWarnings.length + editorNotices.length;
  // A draft made from an existing version changes a template already in use.
  const isRevision = Boolean(version.quality.forkParentVersionId);
  const effectiveLayoutVerified = layoutVerified
    && !editorDirty
    && !identityDirty
    && !documentNameDirty
    && mappingFresh;
  const navigationBusy = navigationPending || editorBusy || maskBusy || printBusy
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
  const moveToEditorNotice = (notice: LayoutEditorNotice) => {
    if (navigationBusy || navigationLockRef.current || !notice.step) return;
    moveLayoutWorkflow(notice.step);
    const [cellId, ...others] = notice.cellIds ?? [];
    if (!cellId || notice.step !== "meaning" || layoutEditorDisabled || operationBusy) return;
    if (printSaved) {
      const pages = printState?.saved?.pages;
      const page = pages ? savedPrintPageForCell(pages, cellId) : null;
      if (!pages || !page) return;
      setAuthoringPage(pages.indexOf(page));
    }
    const requestId = layoutCellFocusRequestIdRef.current + 1;
    layoutCellFocusRequestIdRef.current = requestId;
    layoutCellFocusContextRef.current = null;
    setMappingWarningsExpanded(false);
    setLayoutCellFocusRequest({ requestId, cellId, cellIds: others, purpose: "meaning-cell" });
  };
  const moveToMappingWarning = (
    warning: string,
    warningKey: string,
    step: LayoutWorkflowStep,
  ) => {
    if (navigationBusy || navigationLockRef.current) return;
    const structuredCellTarget = isChoiceSetupWarning(warning)
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
    const target = isChoiceSetupWarning(warning)
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
      setFeedback({ kind: "success", text: isRevision ? "変更を反映しました。" : "テンプレートを作成しました。" });
    }
    setApprovalPendingConfirmation(false);
  };
  const recheckApproval = async () => {
    if (!beginDecision("approve")) return;
    try { await confirmApproval(); }
    catch (error) {
      setFeedback({ kind: "error", text: `${isRevision ? "反映" : "作成"}の要求は届きましたが、結果を確認できません。${errorMessage(error, "結果を再取得してください。")}` });
    }
    finally { endDecision(); }
  };
  const approveVersion = async () => {
    if (printDirty || printBusy || approvalPendingConfirmation) return;
    let revision = printSaved ? printState?.revision : undefined;
    if (printSaved) {
      if (!revision || !meaningSaved) return;
    } else if (!meaningSaved || maskRequiresReprocess) return;
    if (editorBusy || editorSaving || editorDirty || identityDirty || documentNameDirty
      || maskDirty || maskBusy || !beginDecision("approve")) return;
    let approvalPosted = false;
    try {
      if (!printSaved && !effectiveLayoutVerified) {
        // The reading areas are checked against the form as part of creating the template.
        setPreparationMessage("読み取る欄を確認しています…");
        const verified = await api.verifyLayout(version.id, revisionRef.current);
        acceptRevision(verified.resourceRevision);
        setLayoutConfig(verified);
        setArtifacts((await api.artifacts(jobId)).items);
        setLayoutVerified(true);
        setPreparationMessage("テンプレートを有効にしています…");
      }
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
        setPreparationMessage("テンプレートを有効にしています…");
      }
      acceptRevision((await api.approve(version.id, revisionRef.current, revision)).resourceRevision);
      approvalPosted = true;
      setApprovalPendingConfirmation(true);
      await confirmApproval();
    } catch (error) {
      if (approvalPosted) {
        setFeedback({ kind: "error", text: `${isRevision ? "反映" : "作成"}の要求は届きましたが、結果を確認できません。${errorMessage(error, "結果を再取得してください。")}` });
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
        setFeedback({ kind: "error", text: errorMessage(error, isRevision ? "変更を反映できませんでした。" : "テンプレートを作成できませんでした。") });
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
    recoveryBusy: editorBusy || editorSaving || maskBusy || printBusy || decisionBusy !== null,
    resourceBlocker: version.status !== "DRAFT" || activatedVersion ? "使用中のテンプレートです。"
      : loadFailed || !mappingFresh || maskDirty || printDirty || assistantSelection?.draftReviewRequired ? "既存の編集・保存状態を先に解決してください。" : null,
    busy: operationBusy || printBusy || maskBusy,
    blocker: version.status !== "DRAFT" || activatedVersion ? "使用中のテンプレートは直接操作できません。一覧の「編集」から開いてください。"
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
      <header className="page-header split authoring-header has-back">
        <button type="button" className="ghost compact page-back" disabled={navigationBusy} onClick={closeReview}><FiArrowLeft aria-hidden="true" />{closeLabel.replace(/^←\s*/, "")}</button>
        <div className="authoring-title"><span className="eyebrow">テンプレート設定</span><h1>{version.quality.forkParentVersionId ? "読み取る欄を設定" : "復元結果を確認"}</h1></div>
        <div className="authoring-header-actions">
          {pendingCount > 0 && (
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
                  {pendingCount}件
                </span>
                <FiChevronDown className="mapping-warning-chevron" aria-hidden="true" />
              </button>
              {mappingWarningsExpanded && (
                <div className="mapping-warning-list" id="mapping-warning-list">
                  <ul>
                    {editorNotices.map((notice) => {
                      const target = notice.step ? LAYOUT_WIZARD_STEPS.find((step) => step.id === notice.step) ?? null : null;
                      return (
                        <li key={notice.key}>
                          {target ? (
                            <button type="button" className="mapping-warning-action"
                              disabled={navigationBusy || navigationLockRef.current}
                              onClick={() => moveToEditorNotice(notice)}>
                              <span>{notice.text}</span>
                              <small>{navigationBusy || navigationLockRef.current
                                ? "現在の処理が完了するまで移動できません。"
                                : (notice.cellIds?.length ?? 0) > 1
                                  ? `Step ${LAYOUT_WIZARD_STEPS.indexOf(target) + 1}「${target.shortTitle}」で${notice.cellIds!.length}件を選択`
                                  : `Step ${LAYOUT_WIZARD_STEPS.indexOf(target) + 1}「${target.shortTitle}」へ移動`}</small>
                            </button>
                          ) : <span className="mapping-warning-message">{notice.text}</span>}
                        </li>
                      );
                    })}
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
                              <span>{userText(warning, "設定の注意")}</span>
                              <small>{navigationBusy || navigationLockRef.current
                                ? "現在の処理が完了するまで移動できません。"
                                : `Step ${LAYOUT_WIZARD_STEPS.indexOf(target) + 1}「${target.shortTitle}」へ移動`}</small>
                            </button>
                          ) : (
                            <span className="mapping-warning-message">{userText(warning, "設定の注意")}</span>
                          )}
                          {mappingWarningNotices[warningKey] && (
                            <small className="mapping-warning-notice" role="status">
                              <span>{mappingWarningNotices[warningKey]}</span>
                              <span>{isChoiceSetupWarning(warning)
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
          {isSpreadsheetTemplate && <button type="button" className={`secondary compact authoring-print-toggle${printEditorOpen ? " active" : ""}`} aria-expanded={printEditorOpen}
            onClick={() => setPrintEditorOpen((current) => !current)}><FiPrinter aria-hidden="true" />印刷ページを編集</button>}
        </div>
      </header>
      {loadFailed && (
        <div className="error-box action-message" role="alert">
          <span>テンプレート設定を最後まで読み込めませんでした。</span>
          <button type="button" className="secondary compact" onClick={() => setLoadAttempt((current) => current + 1)}>
            読み込みを再試行
          </button>
        </div>
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
        disabled={editorDirty || editorBusy || maskDirty || maskBusy || assistantApplying || decisionBusy !== null}
        onDirtyChange={setPrintDirty} onBusyChange={setPrintBusy} onSavedChange={setPrintSaved}
        initialSaved={Boolean(version.quality.printLayout)}
        onStateChange={setPrintState} externalState={printState}
        onLayoutSaved={setLayoutConfig}
        open={printEditorOpen || printDirty}
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
                disabled={navigationBusy}
                onClick={() => moveLayoutWorkflow(step.id)}
              >
                <span>{index + 1}</span>
                <strong>{step.shortTitle}</strong>
              </button>
            ))}
          </nav>
          <div className="assistant-editor-workspace">
          {editingEnabled ? <EditingControls store={editingStore} tableSelector={false} voiceOnly floatingAnchor=".layout-workbench .mapping-canvas-wrap"
            holdSelectionContext={analysisStatus.held}
            pageScope={currentPrintPage ? { mode: "current", pageId: `${currentPrintPage.page}:${currentPrintPage.range}` } : { mode: "all", pageId: null }}
            selectedEntities={assistantSelection?.selectedIds ?? []} selectionEpoch={assistantSelection?.selectionEpoch ?? 0}
            manualActive={analysisStatus.held || editorDirty || editorBusy || editorSaving || identityDirty || documentNameDirty || maskDirty || printDirty || maskBusy || printBusy || decisionBusy !== null || Boolean(assistantSelection?.draftReviewRequired)}
            disabled={version.status !== "DRAFT" || activatedVersion?.status === "ACTIVE" ? "一覧の「編集」から開いて音声入力を使用してください。" : loadFailed || !mappingFresh ? "編集状態を取得してから音声入力を使用してください。" : null}
            onSaved={async saved => {
              if (!saved.template) throw new Error("保存済みのテンプレートを取得できません。");
              if (editorDirty || identityDirty || documentNameDirty || maskDirty || printDirty || editorBusy || editorSaving) throw new Error("保存済み・表示更新待ちです。未保存の入力を保持しています。");
              const next = saved.template.authoring;
              if (!layoutEditorRef.current?.acceptAssistantSaved(next.layout, false)) throw new Error("保存済み・表示更新待ちです。");
              acceptRevision(next.revision); setLayoutConfig(next.layout); setMappingConfig(next.mapping);
              setIdentityFields(identityFieldsFromResponse(next.mapping)); setDocumentNameRule(documentNameFromResponse(next.mapping));
              setMappingFresh(true); setLayoutVerified(next.quality.layoutVerified === true);
              // The voice overlay shows the result as a bubble; a page banner would also scroll the page.
              setAssistantSaveError(null);
            }} /> : <AssistantPanel snapshot={assistantSnapshot} adapter={{
            setApplying: setAssistantApplying,
            saved: async (receipt, discardDraft = false) => {
              try {
                const [next, print] = await Promise.all([api.authoring(version.id), api.printLayout(version.id)]);
                if (next.revision < receipt.revision) throw new Error("保存した内容をまだ読み込めません。少し待ってから画面を再読み込みしてください。");
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
          {analysisEnabled && <TemplateAnalysisStatus store={analysisStore}
            blocked={layoutEditorDisabled || editorBusy || editingStatus.state !== "ready"}
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
            stepNavigation={{
              index: layoutWorkflowIndex, count: LAYOUT_WIZARD_STEPS.length,
              title: LAYOUT_WIZARD_STEPS[layoutWorkflowIndex]?.shortTitle ?? "",
              previous: layoutWorkflowIndex > 0 && !navigationBusy
                ? () => moveLayoutWorkflow(LAYOUT_WIZARD_STEPS[layoutWorkflowIndex - 1].id) : null,
              next: layoutWorkflowIndex < LAYOUT_WIZARD_STEPS.length - 1 && !navigationBusy
                && !(layoutWorkflowStep === "identity" && (!identityReady || !documentNameReady))
                ? () => moveLayoutWorkflow(LAYOUT_WIZARD_STEPS[layoutWorkflowIndex + 1].id) : null,
            }}
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
            onNoticesChange={setEditorNotices}
            onAssistantSelectionChange={setAssistantSelection}
            onManualFacets={analysisEnabled ? analysisStore.touch : undefined}
            draftStorageKey={`template-layout-draft-v2:${version.id}`}
            detectLabel={version.mode === "SPREADSHEET_TEMPLATE"
              ? "元のExcelセル構造に戻す"
              : undefined}
            onDetect={async () => api.detectLayout(version.id, revisionRef.current)}
            onRecognizeLabels={async (layout) => api.recognizeLayoutLabels(version.id, revisionRef.current, layout)}
            onSuggestMarkOptions={async (layout, cellIds, hint) => api.suggestMarkOptions(version.id, revisionRef.current, layout, cellIds, hint)}
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
                    ? `レイアウトを途中保存しました。${saved.mapping.warnings.map((warning) => userText(warning, "保存時の注意")).join(" ")}`
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
                ? "確認する欄を決めてください。"
                : null}
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
      {feedback && <p className={feedback.kind === "error" ? "error-box" : "success-box"} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.text}</p>}
      {approvalPendingConfirmation && <button type="button" className="secondary"
        disabled={assistantApplying || decisionBusy !== null} onClick={() => void recheckApproval()}>結果を再取得</button>}
      {activatedVersion && <div className="decision-bar">
        <div><strong>このテンプレートは有効です</strong></div>
      </div>}
      {version.status === "DRAFT" && !activatedVersion && <div className="decision-bar">
        <div><strong>{preparationMessage ? <><Spinner />{preparationMessage}</> : (isRevision ? "変更をテンプレートに反映します" : "この設定でテンプレートを作成します")}</strong>
          {editorDirty || maskDirty || identityDirty || documentNameDirty
            ? <span>未保存の変更があります。保存してから次へ進んでください。</span>
            : isRevision && <span>反映すると、次の読み取りからこの設定を使います。</span>}</div>
        <button className="danger-button" disabled={operationBusy} onClick={() => void rejectVersion()}>
          <FiX aria-hidden="true" />{decisionBusy === "reject" ? <><Spinner />却下中…</> : "却下"}</button>
        {!version.quality.forkParentVersionId && maskRequiresReprocess && <button className="secondary"
          disabled={operationBusy || printSaved || printDirty || maskDirty || !meaningSaved}
          onClick={() => void reprocessVersion()}>{decisionBusy === "reprocess" ? <><Spinner />再生成中…</> : "選別マスクから再生成"}</button>}
        <button className="primary"
          disabled={operationBusy || printDirty || editorDirty || identityDirty || documentNameDirty || maskDirty
            || approvalPendingConfirmation || (printSaved ? !meaningSaved || !printState?.revision
              : maskRequiresReprocess || !meaningSaved)}
          onClick={() => void approveVersion()}><FiCheck aria-hidden="true" />{decisionBusy === "approve" ? <><Spinner />{isRevision ? <><Spinner />反映中…</> : "作成中…"}</> : isRevision ? "変更を反映する" : "テンプレートを作成する"}</button>
      </div>}
    </div>
  );
}

/** 「編集」: edit the structure, the per-field settings or the item joins, or copy the template. */
function TemplateEditMenu({ label, primary, disabled, structureLabel, structureAriaLabel, onStructure, onFields, onCopy }: {
  label: string;
  primary: boolean;
  disabled: boolean;
  structureLabel: string;
  structureAriaLabel: string;
  onStructure: () => void;
  onFields: () => void;
  onCopy?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <div className="template-edit-menu" ref={root}>
      <button
        type="button"
        className={primary ? "primary compact" : "secondary compact"}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >{label}</button>
      {open && (
        <div className="template-edit-menu-list" role="menu">
          <button type="button" role="menuitem" aria-label={structureAriaLabel} onClick={() => { setOpen(false); onStructure(); }}>
            <strong>{structureLabel}</strong>
            <small>欄・表・項目名など、テンプレートの形を直します。</small>
          </button>
          <button type="button" role="menuitem" onClick={() => { setOpen(false); onFields(); }}>
            <strong>項目の読み取り設定</strong>
            <small>欄ごとに候補語や読み方の指示を付けます。</small>
          </button>
          {onCopy && <button type="button" role="menuitem" onClick={() => { setOpen(false); onCopy(); }}>
            <strong>複製する</strong>
            <small>このテンプレートをもとに、新しいテンプレートを作ります。</small>
          </button>}
        </div>
      )}
    </div>
  );
}

function layerLabel(name: string) {
  return ({ layoutVerificationOverlay: "枠の再検出", debugOverlay: "文字候補", handwritingOnly: "候補文字のみ", handwritingProbability: "候補確率" } as Record<string, string>)[name] ?? name;
}

function templateModeLabel(mode: string) {
  return MODES.find((item) => item.id === mode)?.title ?? mode;
}


function draftResumeLabel(templateName: string, version: TemplateVersion) {
  return `確認を再開: ${templateName} · ${templateModeLabel(version.mode)} · ${formatTemplateDateTime(version.createdAt)}`;
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
    if (!isChoiceSetupWarning(warning) || !markTarget) return true;
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
    || isChoiceSetupWarning(warning)
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

const OPENED_AT_KEY = "fuminosuke.templateOpenedAt";
function readOpenedAt(): Record<string, number> {
  try {
    const value = JSON.parse(window.localStorage.getItem(OPENED_AT_KEY) ?? "{}") as unknown;
    return value && typeof value === "object" ? value as Record<string, number> : {};
  } catch {
    return {};
  }
}

/** Warnings about an unfinished choice box (its options or storage path). */
export function isChoiceSetupWarning(warning: string) {
  return warning.startsWith("選択欄「") || warning.startsWith("[MARK_OPTIONS_REQUIRED]");
}
