import { OcrTemplateSelector, type ReadingTemplateCard } from "../components/OcrTemplateSelector";
import { OcrJobProgressPanel } from "../components/OcrJobProgressPanel";
import { useOcrMappingPreview } from "../hooks/useOcrMappingPreview";
import { lazy, useEffect, useMemo, useRef, useState } from "react";
import {
  FiArrowLeft,
  FiArrowRight,
  FiPlay,
  FiRefreshCw,
  FiX,
} from "react-icons/fi";
import { ApiError, api, createIdempotencyKey } from "../api";
import { DocumentSeriesPicker, type SeriesSelection } from "../components/DocumentSeriesPicker";
import { OcrPageBindings } from "../components/OcrPageBindings";
import { TemplateNameEditor } from "../components/TemplateNameEditor";
import { OcrImagePicker } from "../components/OcrImagePicker";
import { TemplateLoading } from "../components/TemplateLoading";
import { showToast } from "../components/Toast";


import { useOcrJobProgress } from "../hooks/useOcrJobProgress";
import { usePhoneDevice } from "../hooks/usePhoneDevice";
import { usePreviewDialog } from "../hooks/usePreviewDialog";
import { templatePreviewKey, useTemplatePreviews } from "../hooks/useTemplatePreviews";
import type { Template } from "../types";

type OcrStep = 1 | 2 | 3;
const TemplatePage = lazy(() => import("./TemplatePage").then(module => ({ default: module.TemplatePage })));
type TemplateWorkspace = "create" | "manage";

export type TemplateWorkspaceRequest = {
  id: number;
  workspace: TemplateWorkspace;
  editVersionId?: string;
};

type TemplateWorkspaceView = TemplateWorkspaceRequest;

const EMPTY_TEMPLATE_REFRESH = async () => undefined;

function suggestedDuplicateName(template: Template, templates: Template[]): string {
  const base = `${template.name} のコピー`;
  const existing = new Set(templates.map((item) => item.name.normalize("NFKC").trim().toLocaleLowerCase()));
  for (let number = 1; ; number += 1) {
    const suffix = number === 1 ? "" : ` ${number}`;
    const name = `${base.slice(0, 200 - suffix.length)}${suffix}`;
    if (!existing.has(name.normalize("NFKC").trim().toLocaleLowerCase())) return name;
  }
}

const OCR_STEPS = [
  { number: 1 as const, title: "型を選択", caption: "テンプレート" },
  { number: 2 as const, title: "画像を準備", caption: "撮影・アップロード" },
  { number: 3 as const, title: "確認・実行", caption: "読み取り開始" },
];


export function OcrPage({
  templates,
  textRecognitionToggleEnabled = true,
  rememberJob,
  openReview,
  openTemplates,
  refreshTemplates = EMPTY_TEMPLATE_REFRESH,
  templateWorkspaceRequest,
  onTemplateWorkspaceRequestHandled,
  readingRootRequestId = 0,
  onTemplateWorkspaceVisibilityChange,
  onNavigationGuardChange,
  onTemplateNavigationGuardChange,
}: {
  templates: Template[];
  textRecognitionToggleEnabled?: boolean;
  rememberJob: (id: string) => void;
  openReview: (id: string) => void;
  openTemplates?: () => void;
  refreshTemplates?: () => Promise<Template[] | void>;
  templateWorkspaceRequest?: TemplateWorkspaceRequest | null;
  onTemplateWorkspaceRequestHandled?: () => void;
  readingRootRequestId?: number;
  onTemplateWorkspaceVisibilityChange?: (visible: boolean) => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
  onTemplateNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const templateCards = useMemo<ReadingTemplateCard[]>(() => templates.flatMap((template) => {
    const pointedVersion = template.versions.find(
      (version) => version.id === template.activeVersionId,
    );
    const activeVersion = pointedVersion?.status === "ACTIVE"
      ? pointedVersion
      : template.versions.find((version) => version.status === "ACTIVE");
    if (!activeVersion) return [];
    const draftVersion = [...template.versions]
      .filter((version) => version.status === "DRAFT")
      .sort((first, second) => second.versionNumber - first.versionNumber)[0];
    return [{
      template,
      version: activeVersion,
      editVersion: draftVersion ?? activeVersion,
      // Opening page bindings is not OCR admission; the server catalog gates submission.
      inputAvailable: activeVersion.status === "ACTIVE"
        && (activeVersion.quality.layoutVerified === true
          || (activeVersion.mode === "SPREADSHEET_TEMPLATE" && activeVersion.quality.printLayout != null))
        && !draftVersion,
    }];
  }), [templates]);
  const versions = useMemo(
    () => templateCards.filter((card) => card.inputAvailable),
    [templateCards],
  );
  const [versionId, setVersionId] = useState(versions[0]?.version.id ?? "");
  const [step, setStep] = useState<OcrStep>(1);
  const [file, setFile] = useState<File | null>(null);
  const [enabled, setEnabled] = useState(true);
  // Optional: read the upload as the next version of a confirmed document the user picks.
  const [seriesOpen, setSeriesOpen] = useState(false);
  const [predecessor, setPredecessor] = useState<SeriesSelection | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { mappingPreview, mappingValid, mappingError, mappingResolvedVersionId, reloadMappings } = useOcrMappingPreview(versionId);
  const [duplicateError, setDuplicateError] = useState<string | null>(null);
  const [previewImageState, setPreviewImageState] = useState<Record<string, "loaded" | "failed">>({});
  const [templatePreviewOpen, setTemplatePreviewOpen] = useState(false);
  const previewTriggerButtonRef = useRef<HTMLButtonElement | null>(null);
  const previewDialogRef = useRef<HTMLElement | null>(null);
  const [pageInputRisk, setPageInputRisk] = useState(false);
  const [templateWorkspace, setTemplateWorkspace] = useState<TemplateWorkspaceView | null>(null);
  const phoneDevice = usePhoneDevice();
  const [deletingTemplateId, setDeletingTemplateId] = useState<string | null>(null);
  const [duplicateBusyTemplateId, setDuplicateBusyTemplateId] = useState<string | null>(null);
  const [recentCopies, setRecentCopies] = useState<Array<{
    sourceTemplateId: string; copyTemplateId: string;
  }>>([]);
  const [pendingCopyVersionId, setPendingCopyVersionId] = useState<string | null>(null);
  const [selectionInvalidated, setSelectionInvalidated] = useState(false);
  const creatingRef = useRef(false);
  const submissionGenerationRef = useRef(0);
  const idempotencyKeyRef = useRef<string | null>(null);
  const templateWorkspaceIdRef = useRef(0);
  const activeSelection = versions.find((item) => item.version.id === versionId);
  const activeVersion = activeSelection?.version;
  useEffect(() => {
    // A predecessor is chosen per template version; another version starts without one.
    setSeriesOpen(false);
    setPredecessor(null);
  }, [versionId]);
  // The template grid (step 1) shows every card's thumbnail; the selected version loads first.
  const previewVersions = useMemo(
    () => [...templateCards.map((card) => card.version)]
      .sort((first, second) => Number(second.id === versionId) - Number(first.id === versionId)),
    [templateCards, versionId],
  );
  const previewFor = useTemplatePreviews(previewVersions, !templateWorkspace && !jobId);
  const activePreviewState = activeVersion ? previewFor(activeVersion) : undefined;
  const activePreview = activePreviewState?.url;
  const detailLoading = activeVersion !== undefined && (
    mappingResolvedVersionId !== activeVersion.id
    || (Boolean(activeVersion.sourceJobId) && (!activePreviewState || activePreviewState.loading))
  );
  const activePreviewLabel = `${activeSelection?.template.name ?? "選択したテンプレート"}の書類見本`;
  const visibleVersions = useMemo(() => {
    const ordered = [...templateCards];
    for (const { sourceTemplateId, copyTemplateId } of recentCopies) {
      const sourceIndex = ordered.findIndex(({ template }) => template.id === sourceTemplateId);
      const copyIndex = ordered.findIndex(({ template }) => template.id === copyTemplateId);
      if (sourceIndex < 0 || copyIndex < 0) continue;
      const [copy] = ordered.splice(copyIndex, 1);
      const nextSourceIndex = ordered.findIndex(({ template }) => template.id === sourceTemplateId);
      ordered.splice(nextSourceIndex + 1, 0, copy);
    }
    return ordered;
  }, [recentCopies, templateCards]);
  const ocrEnabledForSubmission = !textRecognitionToggleEnabled || enabled;
  const mappedReadingReady = !ocrEnabledForSubmission || (
    Boolean(mappingPreview?.length)
    && mappingValid
  );
  useEffect(() => {
    if (!textRecognitionToggleEnabled) setEnabled(true);
  }, [textRecognitionToggleEnabled]);
  useEffect(() => {
    if (!templateWorkspace) onNavigationGuardChange?.(Boolean(file) || busy || Boolean(jobId) || pageInputRisk);
  }, [busy, file, jobId, onNavigationGuardChange, pageInputRisk, templateWorkspace]);
  useEffect(() => () => onNavigationGuardChange?.(false), [onNavigationGuardChange]);
  useEffect(() => () => { submissionGenerationRef.current += 1; }, []);
  usePreviewDialog(templatePreviewOpen, () => setTemplatePreviewOpen(false), previewDialogRef, previewTriggerButtonRef);
  const openReadingSetup = (nextVersionId: string) => {
    if (nextVersionId !== versionId && file
      && !window.confirm("選択した画像を破棄して別のテンプレートへ移動しますか？")) return;
    idempotencyKeyRef.current = null;
    setSelectionInvalidated(false);
    if (nextVersionId !== versionId) setFile(null);
    setVersionId(nextVersionId);
    setStep(2);
  };
  const renameTemplate = async (templateId: string, name: string): Promise<string> => {
    const renamed = await api.renameTemplate(templateId, name);
    try {
      await refreshTemplates();
    } catch {
      showToast({ tone: "warning", title: "テンプレート名は保存されました",
        message: "一覧を更新できませんでした。再読み込み後に最新の名前が反映されます。" });
    }
    return renamed.name;
  };
  const openTemplateWorkspace = (
    workspace: TemplateWorkspace,
    editVersionId?: string,
  ) => {
    if (openTemplates) {
      openTemplates();
      return;
    }
    templateWorkspaceIdRef.current += 1;
    setTemplateWorkspace({
      id: templateWorkspaceIdRef.current,
      workspace,
      editVersionId,
    });
  };
  const deleteTemplate = async (template: Template) => {
    if (
      deletingTemplateId
      || !window.confirm(
        `「${template.name}」を読み取り一覧から削除しますか？\n\n未使用の場合は完全に削除され、既存書類で使用中の場合は履歴を保つためアーカイブされます。`,
      )
    ) return;
    setDeletingTemplateId(template.id);
    try {
      const result = await api.deleteTemplate(template.id);
      await refreshTemplates();
      if (template.versions.some((version) => version.id === versionId)) {
        setSelectionInvalidated(true);
        setVersionId("");
        setStep(1);
      }
      showToast({
        tone: "success",
        title: result.outcome === "archived"
          ? "テンプレートを読み取り一覧から削除しました"
          : "テンプレートを削除しました",
        message: result.outcome === "archived"
          ? "既存書類の履歴を保つため、テンプレート本体はアーカイブされています。"
          : `「${template.name}」と保存済み設定を削除しました。`,
      });
    } catch (error) {
      showToast({
        tone: "error",
        title: "テンプレートを削除できませんでした",
        message: error instanceof ApiError ? error.message : "バックエンドとの接続を確認してください。",
      });
    } finally {
      setDeletingTemplateId((current) => current === template.id ? null : current);
    }
  };
  const duplicateTemplate = async (template: Template, version: Template["versions"][number]) => {
    if (duplicateBusyTemplateId) return;
    setDuplicateError(null);
    const name = suggestedDuplicateName(template, templates);
    setDuplicateBusyTemplateId(template.id);
    try {
      const result = await api.duplicateTemplateVersion(version.id, version.revision, name, true);
      let copied: Template;
      try {
        copied = await api.template(result.templateId);
      } catch {
        try { await refreshTemplates(); } catch { /* The operation outcome is still unknown. */ }
        showToast({
          tone: "warning", title: "複製結果を確認できませんでした",
          message: "コピーが作成された可能性があります。一覧を確認してから再操作してください。",
        });
        return;
      }
      const copiedVersion = copied.versions.find((item) => item.id === result.templateVersionId);
      if (copied.activeVersionId !== result.templateVersionId || copiedVersion?.status !== "ACTIVE") {
        try { await refreshTemplates(); } catch { /* The draft is still not reading-ready. */ }
        showToast({
          tone: "error", title: "コピーは読み取り用に有効化されていません",
          message: `「${name}」は下書きとして作成されました。バックエンドを更新してから再操作してください。`,
        });
        return;
      }
      try {
        await refreshTemplates();
      } catch {
        showToast({
          tone: "error", title: "複製は完了しました",
          message: "一覧を更新できませんでした。画面を再読み込みしてコピーを確認してください。",
        });
        return;
      }
      setRecentCopies((current) => [...current, {
        sourceTemplateId: template.id, copyTemplateId: result.templateId,
      }]);
      setPendingCopyVersionId(result.templateVersionId);
      setVersionId(result.templateVersionId);
      setSelectionInvalidated(false);
      showToast({
        tone: "success", title: "テンプレートを複製しました",
        message: `「${name}」を読み取り用テンプレートに追加しました。`,
      });
    } catch (error) {
      if (error instanceof ApiError) {
        setDuplicateError(error.message);
      } else {
        try { await refreshTemplates(); } catch { /* The operation outcome is still unknown. */ }
        showToast({
          tone: "warning", title: "複製結果を確認できませんでした",
          message: "通信が切れた可能性があります。一覧を確認してから再操作してください。",
        });
      }
    } finally {
      setDuplicateBusyTemplateId(null);
    }
  };
  useEffect(() => {
    if (!templateWorkspaceRequest) return;
    setTemplateWorkspace(templateWorkspaceRequest);
    onTemplateWorkspaceRequestHandled?.();
  }, [onTemplateWorkspaceRequestHandled, templateWorkspaceRequest]);
  useEffect(() => {
    if (readingRootRequestId === 0) return;
    submissionGenerationRef.current += 1;
    creatingRef.current = false;
    setBusy(false);
    setTemplateWorkspace(null);
    setFile(null);
    setJobId(null);
    setPageInputRisk(false);
    setTemplatePreviewOpen(false);
    idempotencyKeyRef.current = null;
    setStep(1);
  }, [readingRootRequestId]);
  useEffect(() => {
    onTemplateWorkspaceVisibilityChange?.(templateWorkspace !== null);
    return () => onTemplateWorkspaceVisibilityChange?.(false);
  }, [onTemplateWorkspaceVisibilityChange, templateWorkspace]);
  useEffect(() => {
    if (versionId === pendingCopyVersionId) return;
    if (versions.some((item) => item.version.id === versionId)) return;
    idempotencyKeyRef.current = null;
    if (versionId) {
      setSelectionInvalidated(true);
      setVersionId("");
      return;
    }
    if (selectionInvalidated) return;
    setVersionId(
      versions[0]?.version.id ?? "",
    );
  }, [pendingCopyVersionId, selectionInvalidated, versionId, versions]);
  useEffect(() => {
    if (pendingCopyVersionId && versions.some((item) => item.version.id === pendingCopyVersionId)) {
      setPendingCopyVersionId(null);
    }
  }, [pendingCopyVersionId, versions]);
  const { job, connectionLost, canRetry, retryBusy, retryPending, retryBlocked, retryJob, resetRetry } = useOcrJobProgress(
    jobId, activeVersion?.quality.printLayout != null, openReview,
  );
  const startNewReading = () => {
    idempotencyKeyRef.current = null;
    setFile(null);
    setJobId(null);
    resetRetry();
    setStep(2);
  };

  if (templateWorkspace && phoneDevice) {
    return (
      <div className="page-stack narrow">
        <section className="form-card phone-template-notice" role="status">
          <h1>テンプレートの作成・編集はパソコンで行ってください</h1>
          <p>スマートフォンでは、作成済みのテンプレートを使った読み取り・確認・修正・確定・Excel取得・質問ができます。</p>
          <button type="button" className="primary" onClick={() => setTemplateWorkspace(null)}>読み取りに戻る</button>
        </section>
      </div>
    );
  }
  if (templateWorkspace) {
    return (
      <TemplatePage
        key={templateWorkspace.id}
        templates={templates}
        refresh={refreshTemplates}
        rememberJob={rememberJob}
        initialWorkspace={templateWorkspace.workspace}
        initialEditVersionId={templateWorkspace.editVersionId}
        onNavigationGuardChange={onTemplateNavigationGuardChange}
        onClose={() => setTemplateWorkspace(null)}
        onTemplateActivated={(activatedVersionId) => {
          setSelectionInvalidated(false);
          setVersionId(activatedVersionId);
          setStep(1);
          setTemplateWorkspace(null);
        }}
      />
    );
  }
  return (
    <div className="page-stack narrow">
      <header className="page-header split ocr-page-header">
        <nav className="ocr-breadcrumb" aria-label="パンくずリスト">
          <span>読み取り</span>
          <span aria-hidden="true">›</span>
          <strong>{step === 1 ? "テンプレート選択" : step === 2 ? "画像を準備" : "内容確認・実行"}</strong>
        </nav>
      </header>
      {jobId ? (
        <OcrJobProgressPanel job={job} connectionLost={connectionLost} canRetry={canRetry} retryBusy={retryBusy} retryPending={retryPending} retryBlocked={retryBlocked} retryJob={retryJob} startNewReading={startNewReading} />
      ) : (
        <>
          <ol className="ocr-step-progress" aria-label="読み取り手順">
            {OCR_STEPS.map((item) => (
              <li
                key={item.number}
                className={item.number < step ? "completed" : item.number === step ? "current" : ""}
                aria-current={item.number === step ? "step" : undefined}
              >
                <span>{item.number < step ? "✓" : item.number}</span>
                <div><strong>{item.title}</strong><small>{item.caption}</small></div>
              </li>
            ))}
          </ol>

          <section className={`form-card ocr-step-card${step === 1 ? " ocr-template-select-card" : ""}`}>
            {step === 1 && duplicateError && <aside className="error-box" role="alert">
              <strong>テンプレートを複製できませんでした</strong>
              <p>{duplicateError}</p>
              <button type="button" className="secondary compact" aria-label="複製エラーを閉じる"
                onClick={() => setDuplicateError(null)}><FiX aria-hidden="true" />閉じる</button>
            </aside>}
            {step === 1 && (
              <OcrTemplateSelector templateCards={templateCards}
                visibleVersions={visibleVersions}
                previewFor={previewFor}
                previewImageState={previewImageState}
                setPreviewImageState={setPreviewImageState}
                deletingTemplateId={deletingTemplateId}
                duplicateBusyTemplateId={duplicateBusyTemplateId}
                openReadingSetup={openReadingSetup}
                deleteTemplate={deleteTemplate}
                duplicateTemplate={duplicateTemplate}
                openTemplateWorkspace={openTemplateWorkspace} />
            )}

            {step === 2 && activeVersion?.quality.printLayout != null && (
              <OcrPageBindings key={versionId} versionId={versionId} templateId={activeSelection!.template.id}
                onJobCreated={rememberJob}
                onNavigationGuardChange={setPageInputRisk}
                onRenameTemplate={renameTemplate}
                templateName={activeSelection?.template.name ?? "選択したテンプレート"}
                versionNumber={activeVersion.versionNumber}
                onBack={() => setStep(1)} onAccepted={(id) => {
                setJobId(id);
              }} />
            )}
            {step === 2 && activeVersion && activeVersion.quality.printLayout == null && detailLoading && (
              <section className="ocr-step-panel">
                <button type="button" className="secondary compact ocr-reading-back"
                  aria-label="テンプレート選択画面に戻る" onClick={() => setStep(1)}>
                  <FiArrowLeft aria-hidden="true" focusable="false" />
                </button>
                <TemplateLoading message="書類見本と読み取り設定を確認しています" />
              </section>
            )}
            {step === 2 && activeVersion && activeVersion.quality.printLayout == null && !detailLoading && (
              <section className="ocr-step-panel" aria-labelledby="ocr-step-image">
                <header className="ocr-reading-step-header">
                  <button type="button" className="secondary compact ocr-reading-back"
                    aria-label="テンプレート選択画面に戻る" title="テンプレート選択画面に戻る"
                    onClick={() => setStep(1)}>
                    <FiArrowLeft aria-hidden="true" focusable="false" />
                  </button>
                  <TemplateNameEditor headingId="ocr-step-image"
                    name={activeSelection.template.name}
                    onSave={(name) => renameTemplate(activeSelection.template.id, name)} />
                </header>
                <div className="ocr-reading-setup">
                  <figure className="ocr-reading-reference">
                    <div>
                      {activePreview
                        ? <button ref={previewTriggerButtonRef} type="button" className="ocr-reading-preview-trigger"
                            aria-label={`${activePreviewLabel}を拡大表示`}
                            onClick={() => setTemplatePreviewOpen(true)}>
                            <img src={activePreview} alt={activePreviewLabel} />
                          </button>
                        : <span>書類見本はありません</span>}
                    </div>
                  </figure>
                  <div className="ocr-reading-input-panel">
                    <div className="ocr-reading-file-picker">
                      <OcrImagePicker key={versionId} file={file} allowXlsx={activeVersion?.mode === "SPREADSHEET_TEMPLATE" && !activeVersion.quality.printLayout} onChange={(nextFile) => {
                        idempotencyKeyRef.current = null;
                        setFile(nextFile);
                      }} />
                    </div>
                    {versionId && (
                      <DocumentSeriesPicker
                        templateVersionId={versionId}
                        open={seriesOpen}
                        onOpenChange={(open) => {
                          idempotencyKeyRef.current = null;
                          setSeriesOpen(open);
                        }}
                        value={predecessor}
                        onChange={(next) => {
                          idempotencyKeyRef.current = null;
                          setPredecessor(next);
                        }}
                      />
                    )}
                    {textRecognitionToggleEnabled && <>
                      <label className="switch-row">
                        <span><strong>文字読み取りも実行する</strong><small>オフにすると画像処理と領域切り出しだけを行います。</small></span>
                        <input type="checkbox" checked={enabled} onChange={(event) => {
                          idempotencyKeyRef.current = null;
                          setEnabled(event.target.checked);
                        }} /><i />
                      </label>
                      {!enabled && <aside className="notice compact"><strong>画像処理のみ</strong><span>結果欄は空欄として作成され、手入力できます。</span></aside>}
                    </>}
                    {mappingError && (
                      <div className="mapping-load-error" role="alert">
                        <p className="inline-warning">テンプレート設定を確認できませんでした。</p>
                        <button type="button" className="secondary compact"
                          onClick={() => {
                            reloadMappings();
                          }}><FiRefreshCw aria-hidden="true" />もう一度読み込む</button>
                      </div>
                    )}
                    {mappingPreview !== null && !mappedReadingReady && !mappingError && (
                      <p className="inline-warning" role="status">このテンプレートは現在読み取りに使用できません。テンプレートを編集してください。</p>
                    )}
                  </div>
                </div>
                <div className="ocr-step-actions ocr-step-actions-single">
                  <button type="button" className="primary"
                    disabled={!file || mappingPreview === null || !mappedReadingReady || Boolean(mappingError) || (seriesOpen && !predecessor)}
                    onClick={() => setStep(3)}>内容確認へ<FiArrowRight aria-hidden="true" /></button>
                </div>
              </section>
            )}

            {templatePreviewOpen && activePreview && (
              <div className="ocr-template-preview-backdrop" onMouseDown={(event) => {
                if (event.target === event.currentTarget) setTemplatePreviewOpen(false);
              }}>
                <section ref={previewDialogRef} tabIndex={-1} className="ocr-template-preview-dialog" role="dialog" aria-modal="true"
                  aria-labelledby="ocr-template-preview-title">
                  <header>
                    <h2 id="ocr-template-preview-title">{activePreviewLabel}</h2>
                    <button type="button" className="secondary compact"
                      aria-label="プレビューを閉じる" title="閉じる"
                      onClick={() => setTemplatePreviewOpen(false)}>
                      <FiX aria-hidden="true" focusable="false" />
                    </button>
                  </header>
                  <img src={activePreview} alt={activePreviewLabel} />
                </section>
              </div>
            )}

            {step === 3 && (
              <section className="ocr-step-panel" aria-labelledby="ocr-step-confirm">
                <div className="ocr-step-heading">
                  <span>STEP 3</span>
                  <h2 id="ocr-step-confirm">読み取り内容を確認</h2>
                  <p>使用するテンプレートと帳票画像を確認してから開始してください。</p>
                </div>
                <dl className="ocr-reading-summary">
                  <div><dt>テンプレート</dt><dd>{activeSelection ? `${activeSelection.template.name} · v${activeSelection.version.versionNumber}` : "未選択"}</dd></div>
                  <div><dt>帳票画像</dt><dd>{file?.name ?? "未選択"}</dd></div>
                  <div><dt>読み込み方</dt><dd>{predecessor ? `「${predecessor.label}」の更新（版${predecessor.seriesVersion + 1}として保存し、前の版は履歴に残します）` : "新しい書類として読み込む"}</dd></div>
                  <div><dt>処理内容</dt><dd>{ocrEnabledForSubmission ? "手書き文字を読み取る" : "画像処理のみ"}</dd></div>
                </dl>
                <div className="ocr-step-actions">
                  <button type="button" className="secondary" disabled={busy || Boolean(jobId)} onClick={() => setStep(2)}><FiArrowLeft aria-hidden="true" />戻る</button>
                  <button
                    className="primary"
                    disabled={!file || !versionId || busy || !mappedReadingReady || Boolean(jobId)}
                    onClick={async () => {
                      if (!file || creatingRef.current || jobId) return;
                      creatingRef.current = true;
                      const generation = submissionGenerationRef.current;
                      const idempotencyKey = idempotencyKeyRef.current
                        ?? createIdempotencyKey("ocr");
                      idempotencyKeyRef.current = idempotencyKey;
                      setBusy(true);
                      try {
                        const created = await api.createOcr(
                          versionId,
                          file,
                          ocrEnabledForSubmission,
                          idempotencyKey,
                          predecessor?.jobId,
                        );
                        rememberJob(created.jobId);
                        if (generation !== submissionGenerationRef.current) return;
                        idempotencyKeyRef.current = null;
                        setJobId(created.jobId);
                      } catch (error) {
                        if (generation !== submissionGenerationRef.current) return;
                        showToast({
                          tone: "error",
                          title: "読み取りを開始できませんでした",
                          message: error instanceof ApiError ? error.message : "バックエンドとの接続を確認してください。",
                        });
                      } finally {
                        if (generation === submissionGenerationRef.current) {
                          creatingRef.current = false;
                          setBusy(false);
                        }
                      }
                    }}
                  >
                    <FiPlay aria-hidden="true" />{busy ? "送信中…" : jobId ? "読み取りを開始しました" : ocrEnabledForSubmission ? "読み取りを開始" : "画像処理を開始"}
                  </button>
                </div>
              </section>
            )}
          </section>
        </>
      )}
    </div>
  );
}
