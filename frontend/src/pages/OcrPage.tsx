import { OcrTemplateSelector, type ReadingTemplateCard } from "../components/OcrTemplateSelector";
import { OcrJobProgressPanel } from "../components/OcrJobProgressPanel";
import { useOcrMappingPreview } from "../hooks/useOcrMappingPreview";
import { lazy, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  FiArrowLeft,
  FiArrowRight,
  FiPlay,
  FiRefreshCw,
  FiSend,
  FiX,
} from "react-icons/fi";
import { ReadingSendPanel, type SentReading } from "../components/ReadingSendPanel";
import { ApiError, api, createIdempotencyKey } from "../api";
import { TemplateDeleteDialog } from "../components/TemplateTrash";
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
import { isEditingDraft } from "../templateEditing";
import { Spinner } from "../components/Spinner";
import { readingQueue } from "../readingQueue";

type OcrStep = 1 | 2;
const TemplatePage = lazy(() => import("./TemplatePage").then(module => ({ default: module.TemplatePage })));
type TemplateWorkspace = "create" | "manage";

export type TemplateWorkspaceRequest = {
  id: number;
  workspace: TemplateWorkspace;
  editVersionId?: string;
  /** "fields": open 「項目の読み取り設定」 of `editVersionId` instead of its structure. */
  editTarget?: "structure" | "fields";
};

type TemplateWorkspaceView = TemplateWorkspaceRequest;

const EMPTY_TEMPLATE_REFRESH = async () => undefined;

const OCR_STEPS = [
  { number: 1 as const, title: "型を選択", caption: "テンプレート" },
  { number: 2 as const, title: "画像を準備", caption: "撮影・アップロード" },
  // Reading runs by itself once started: the third step only shows its progress.
  { number: 3 as const, title: "読み取り", caption: "自動で進みます" },
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
  templateSelectionFooter,
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
  // Shown under the template list: pending jobs and system notices (there is no separate home page).
  templateSelectionFooter?: ReactNode;
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
      .filter(isEditingDraft)
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
  // Image templates: each file is read as its own document, queued on the server one after another.
  const [files, setFiles] = useState<File[]>([]);
  const [enabled, setEnabled] = useState(true);
  // Optional: read the upload as the next version of a confirmed document the user picks.
  const [seriesOpen, setSeriesOpen] = useState(false);
  const [predecessor, setPredecessor] = useState<SeriesSelection | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Images just handed to the reading queue: step 3 plays the send animation, then returns to step 2.
  const [sent, setSent] = useState<SentReading | null>(null);
  useEffect(() => () => {
    sent?.images.forEach((url) => { if (url) URL.revokeObjectURL(url); });
  }, [sent]);
  const { mappingPreview, mappingValid, mappingError, mappingResolvedVersionId, reloadMappings } = useOcrMappingPreview(versionId);
  const [previewImageState, setPreviewImageState] = useState<Record<string, "loaded" | "failed">>({});
  const [templatePreviewOpen, setTemplatePreviewOpen] = useState(false);
  const previewTriggerButtonRef = useRef<HTMLButtonElement | null>(null);
  const previewDialogRef = useRef<HTMLElement | null>(null);
  const [pageInputRisk, setPageInputRisk] = useState(false);
  const [templateWorkspace, setTemplateWorkspace] = useState<TemplateWorkspaceView | null>(null);
  const phoneDevice = usePhoneDevice();
  const [deletingTemplate, setDeletingTemplate] = useState<Template | null>(null);
  const [selectionInvalidated, setSelectionInvalidated] = useState(false);
  const creatingRef = useRef(false);
  const submissionGenerationRef = useRef(0);
  const idempotencyKeyRef = useRef<string | null>(null);
  // One key per file of a batch, so sending again after a failure does not read a file twice.
  const fileKeysRef = useRef(new Map<File, string>());
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
  const ocrEnabledForSubmission = !textRecognitionToggleEnabled || enabled;
  const mappedReadingReady = !ocrEnabledForSubmission || (
    Boolean(mappingPreview?.length)
    && mappingValid
  );
  useEffect(() => {
    if (!textRecognitionToggleEnabled) setEnabled(true);
  }, [textRecognitionToggleEnabled]);
  useEffect(() => {
    if (!templateWorkspace) onNavigationGuardChange?.(files.length > 0 || busy || Boolean(jobId) || pageInputRisk);
  }, [busy, files, jobId, onNavigationGuardChange, pageInputRisk, templateWorkspace]);
  useEffect(() => () => onNavigationGuardChange?.(false), [onNavigationGuardChange]);
  useEffect(() => () => { submissionGenerationRef.current += 1; }, []);
  usePreviewDialog(templatePreviewOpen, () => setTemplatePreviewOpen(false), previewDialogRef, previewTriggerButtonRef);
  const openReadingSetup = (nextVersionId: string) => {
    if (nextVersionId !== versionId && files.length > 0
      && !window.confirm("選択した画像を破棄して別のテンプレートへ移動しますか？")) return;
    idempotencyKeyRef.current = null;
    setSelectionInvalidated(false);
    if (nextVersionId !== versionId) setFiles([]);
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
    editTarget?: "structure" | "fields",
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
      editTarget,
    });
  };
  const deleteTemplate = async (template: Template) => { setDeletingTemplate(template); };
  const templateDeleted = async (template: Template) => {
    setDeletingTemplate(null);
    try { await refreshTemplates(); } catch { /* The next load shows it. */ }
    if (template.versions.some((version) => version.id === versionId)) {
      setSelectionInvalidated(true);
      setVersionId("");
      setStep(1);
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
    setSent(null);
    setTemplateWorkspace(null);
    setFiles([]);
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
  }, [selectionInvalidated, versionId, versions]);
  const { job, connectionLost, canRetry, retryBusy, retryPending, retryBlocked, retryJob, resetRetry } = useOcrJobProgress(
    jobId, activeVersion?.quality.printLayout != null, openReview,
  );
  const currentStep = jobId || sent ? 3 : step;
  const startReading = async () => {
    if (files.length === 0 || creatingRef.current || jobId || sent) return;
    creatingRef.current = true;
    const generation = submissionGenerationRef.current;
    const templateName = activeSelection?.template.name ?? "テンプレート";
    const keys = fileKeysRef.current;
    setBusy(true);
    let accepted = 0;
    let remaining = files;
    const acceptedFiles: File[] = [];
    try {
      for (const item of files) {
        let key = keys.get(item);
        if (!key) { key = createIdempotencyKey("ocr"); keys.set(item, key); }
        const created = await api.createOcr(
          versionId,
          item,
          ocrEnabledForSubmission,
          key,
          files.length === 1 ? predecessor?.jobId : undefined,
        );
        rememberJob(created.jobId);
        readingQueue.add(created.jobId, item.name, templateName, {
          templateId: activeSelection?.template.id,
          predecessorJobId: files.length === 1 ? predecessor?.jobId : undefined,
        });
        keys.delete(item);
        accepted += 1;
        acceptedFiles.push(item);
        remaining = remaining.filter((kept) => kept !== item);
        if (generation === submissionGenerationRef.current) setFiles(remaining);
      }
    } catch (error) {
      if (generation !== submissionGenerationRef.current) return;
      showToast({
        tone: "error",
        title: accepted > 0 ? `${accepted}件を受け付けましたが、残り${remaining.length}件を送れませんでした` : "読み取りを開始できませんでした",
        message: error instanceof ApiError ? error.message : "バックエンドとの接続を確認してください。",
      });
    } finally {
      if (generation === submissionGenerationRef.current) {
        creatingRef.current = false;
        setBusy(false);
        if (accepted > 0) {
          setSeriesOpen(false);
          setPredecessor(null);
          setSent({
            id: Date.now(),
            count: accepted,
            images: acceptedFiles.slice(0, 3).map((item) => (
              /\.xlsx$/i.test(item.name) ? null : URL.createObjectURL(item))),
          });
        }
      }
    }
  };
  const startNewReading = () => {
    idempotencyKeyRef.current = null;
    setFiles([]);
    setJobId(null);
    resetRetry();
    setStep(2);
  };

  if (templateWorkspace && phoneDevice) {
    return (
      <div className="page-stack narrow">
        <section className="form-card phone-template-notice" role="status">
          <h1>テンプレートの作成・編集はパソコンで行ってください</h1>
          <p>スマートフォンでは、作成済みのテンプレートを使った読み取り・確認・修正・確定・Excel取得・チャットができます。</p>
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
        initialEditTarget={templateWorkspace.editTarget}
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
          <strong>{sent ? "読み取りに送信" : jobId ? "読み取り中" : step === 1 ? "テンプレート選択" : "画像を準備"}</strong>
        </nav>
      </header>
      <ol className="ocr-step-progress" aria-label="読み取り手順">
        {OCR_STEPS.map((item) => (
          <li
            key={item.number}
            className={`${item.number < currentStep ? "completed" : item.number === currentStep ? "current" : ""}${sent && item.number === 3 ? " is-sending" : ""}`}
            aria-current={item.number === currentStep ? "step" : undefined}
          >
            <span>{item.number < currentStep ? "✓" : sent && item.number === 3 ? <FiSend aria-hidden="true" /> : item.number}</span>
            <div><strong>{item.title}</strong><small>{item.caption}</small></div>
          </li>
        ))}
      </ol>
      {sent ? (
        <ReadingSendPanel key={sent.id} sent={sent} onDone={() => setSent(null)} />
      ) : jobId ? (
        <OcrJobProgressPanel job={job} connectionLost={connectionLost} canRetry={canRetry} retryBusy={retryBusy} retryPending={retryPending} retryBlocked={retryBlocked} retryJob={retryJob} startNewReading={startNewReading} />
      ) : (
        <>
          <section className={`form-card ocr-step-card${step === 1 ? " ocr-template-select-card" : ""}`}>
            {step === 1 && (
              <OcrTemplateSelector templateCards={templateCards}
                previewFor={previewFor}
                previewImageState={previewImageState}
                setPreviewImageState={setPreviewImageState}
                deletingTemplateId={null}
                openReadingSetup={openReadingSetup}
                deleteTemplate={deleteTemplate}
                openTemplateWorkspace={openTemplateWorkspace} />
            )}
            {deletingTemplate && <TemplateDeleteDialog template={deletingTemplate} templates={templates}
              onClose={() => setDeletingTemplate(null)}
              onDeleted={() => templateDeleted(deletingTemplate)} />}

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
                      <OcrImagePicker key={versionId} files={files} multiple={!seriesOpen}
                        allowXlsx={activeVersion?.mode === "SPREADSHEET_TEMPLATE" && !activeVersion.quality.printLayout}
                        onChange={setFiles} />
                    </div>
                    {versionId && files.length <= 1 && (
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
                    disabled={files.length === 0 || !versionId || busy || Boolean(jobId) || mappingPreview === null || !mappedReadingReady || Boolean(mappingError) || (seriesOpen && !predecessor)}
                    onClick={() => void startReading()}>
                    <FiPlay aria-hidden="true" />{busy ? <><Spinner />送信中…</> : `${ocrEnabledForSubmission ? "読み取りを開始" : "画像処理を開始"}${files.length > 1 ? `（${files.length}件）` : ""}`}</button>
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

          </section>
          {step === 1 && templateSelectionFooter}
        </>
      )}
    </div>
  );
}
