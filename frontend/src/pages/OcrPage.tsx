import { useEffect, useMemo, useRef, useState } from "react";
import {
  FiDownload,
  FiFilePlus,
  FiPlus,
  FiSettings,
  FiTrash2,
  FiUpload,
  FiX,
} from "react-icons/fi";
import { ApiError, api, createIdempotencyKey } from "../api";
import { CameraCapture } from "../components/CameraCapture";
import { FilePicker } from "../components/FilePicker";
import { showToast } from "../components/Toast";
import { JobProgress } from "../components/JobProgress";
import { TemplatePackageDialog } from "../components/TemplatePackageDialog";
import { useJobPoller } from "../hooks/useJobPoller";
import {
  navigateToSpreadsheet,
  type SpreadsheetNavigator,
} from "../spreadsheetNavigation";
import type { FieldMapping, OcrLibraryContext, Template } from "../types";
import { TemplatePage } from "./TemplatePage";

type OcrStep = 1 | 2 | 3;
type TemplateWorkspace = "create" | "manage";

export type TemplateWorkspaceRequest = {
  id: number;
  workspace: TemplateWorkspace;
  editVersionId?: string;
};

type TemplateWorkspaceView = TemplateWorkspaceRequest;

type ReadingTemplateCard = {
  template: Template;
  version: Template["versions"][number];
  editVersion: Template["versions"][number];
  ocrAvailable: boolean;
};

const EMPTY_TEMPLATE_REFRESH = async () => undefined;

const OCR_STEPS = [
  { number: 1 as const, title: "型を選択", caption: "テンプレート" },
  { number: 2 as const, title: "画像を準備", caption: "撮影・アップロード" },
  { number: 3 as const, title: "確認・実行", caption: "読み取り開始" },
];

const TEMPLATE_PREVIEW_PRIORITY = [
  "cleanPreview",
  "cleanTemplate",
  "normalized",
  "alignedSource.1",
] as const;

export function OcrPage({
  templates,
  libraryContext,
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
  onSpreadsheetNavigate = navigateToSpreadsheet,
}: {
  templates: Template[];
  libraryContext?: OcrLibraryContext | null;
  rememberJob: (id: string) => void;
  openReview: (id: string) => void;
  openTemplates?: () => void;
  refreshTemplates?: () => Promise<void>;
  templateWorkspaceRequest?: TemplateWorkspaceRequest | null;
  onTemplateWorkspaceRequestHandled?: () => void;
  readingRootRequestId?: number;
  onTemplateWorkspaceVisibilityChange?: (visible: boolean) => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
  onTemplateNavigationGuardChange?: (shouldWarn: boolean) => void;
  onSpreadsheetNavigate?: SpreadsheetNavigator;
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
      ocrAvailable: activeVersion.status === "ACTIVE"
        && activeVersion.quality.layoutVerified === true
        && !draftVersion,
    }];
  }), [templates]);
  const versions = useMemo(
    () => templateCards.filter((card) => card.ocrAvailable),
    [templateCards],
  );
  const unverifiedVersions = useMemo(
    () => templateCards.filter((card) => !card.ocrAvailable),
    [templateCards],
  );
  const [versionId, setVersionId] = useState(libraryContext?.templateVersionId ?? versions[0]?.version.id ?? "");
  const [step, setStep] = useState<OcrStep>(libraryContext ? 2 : 1);
  const [file, setFile] = useState<File | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [mappingPreview, setMappingPreview] = useState<FieldMapping[] | null>(null);
  const [mappingValid, setMappingValid] = useState(false);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [mappingRetryToken, setMappingRetryToken] = useState(0);
  const [templateQuery, setTemplateQuery] = useState("");
  const [templateAddMenuOpen, setTemplateAddMenuOpen] = useState(false);
  const [templatePreviews, setTemplatePreviews] = useState<Record<string, string>>({});
  const [templateWorkspace, setTemplateWorkspace] = useState<TemplateWorkspaceView | null>(null);
  const [deletingTemplateId, setDeletingTemplateId] = useState<string | null>(null);
  const [selectionInvalidated, setSelectionInvalidated] = useState(false);
  const [packageDialog, setPackageDialog] = useState<
    | { mode: "import" }
    | { mode: "export"; template: Template; version: Template["versions"][number] }
    | null
  >(null);
  const creatingRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);
  const templateWorkspaceIdRef = useRef(0);
  const activeSelection = versions.find((item) => item.version.id === versionId);
  const activeVersion = activeSelection?.version;
  const visibleVersions = useMemo(() => {
    const query = templateQuery.trim().toLocaleLowerCase("ja-JP");
    if (!query) return templateCards;
    return templateCards.filter(({ template }) => (
      template.name.toLocaleLowerCase("ja-JP").includes(query)
    ));
  }, [templateCards, templateQuery]);
  const mappedReadingReady = !enabled || (
    Boolean(mappingPreview?.length)
    && mappingValid
  );
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
  useEffect(() => {
    if (!templateWorkspaceRequest) return;
    setTemplateWorkspace(templateWorkspaceRequest);
    onTemplateWorkspaceRequestHandled?.();
  }, [onTemplateWorkspaceRequestHandled, templateWorkspaceRequest]);
  useEffect(() => {
    if (readingRootRequestId === 0) return;
    setTemplateWorkspace(null);
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
      versions.some((item) => item.version.id === libraryContext?.templateVersionId)
        ? libraryContext?.templateVersionId ?? ""
        : versions[0]?.version.id ?? "",
    );
  }, [libraryContext?.templateVersionId, selectionInvalidated, versionId, versions]);
  useEffect(() => {
    let cancelled = false;
    const previewSources = templateCards.filter(({ version }) => version.sourceJobId);
    void Promise.all(previewSources.map(async ({ version }) => {
      try {
        const response = await api.artifacts(version.sourceJobId!);
        const preview = TEMPLATE_PREVIEW_PRIORITY
          .map((name) => response.items.find((artifact) => artifact.name === name)?.url)
          .find(Boolean);
        return preview ? [version.id, preview] as const : null;
      } catch {
        return null;
      }
    })).then((entries) => {
      if (cancelled) return;
      setTemplatePreviews(Object.fromEntries(entries.filter((entry) => entry !== null)));
    });
    return () => {
      cancelled = true;
    };
  }, [templateCards]);
  useEffect(() => {
    const shouldWarn = (
      !templateWorkspace
      && !jobId
      && (busy || step > 1 || file !== null)
    );
    onNavigationGuardChange?.(shouldWarn);
    return () => onNavigationGuardChange?.(false);
  }, [
    busy,
    file,
    jobId,
    onNavigationGuardChange,
    step,
    templateWorkspace,
  ]);
  useEffect(() => {
    let cancelled = false;
    setMappingPreview(null);
    setMappingValid(false);
    setMappingError(null);
    if (!versionId) return;
    void api.fieldMappings(versionId)
      .then((response) => {
        if (!cancelled) {
          setMappingPreview(response.mappings.filter((mapping) => mapping.enabled));
          setMappingValid(response.validForOcr);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setMappingError(
            error instanceof ApiError ? error.message : "意味づけを確認できませんでした。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [mappingRetryToken, versionId]);
  const { job, connectionLost } = useJobPoller(jobId, (terminal) => {
    if (terminal.status !== "REVIEW_REQUIRED") return;
    void api.result(terminal.id)
      .then((result) => {
        const sync = result.spreadsheetSync;
        if (sync?.status === "synced" && sync.spreadsheetUrl) {
          onNavigationGuardChange?.(false);
          onSpreadsheetNavigate(sync.spreadsheetUrl);
          return;
        }
        openReview(terminal.id);
      })
      .catch(() => openReview(terminal.id));
  });
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
      <header className="page-header split">
        <div>
          <span className="eyebrow">NEW READING</span>
          <h1>{libraryContext?.action === "RESCAN_PAGE" ? `${libraryContext.pageNumber ?? "選択した"}ページ目を読み直す` : libraryContext?.action === "ADD_PAGE" ? "続きのページを追加" : libraryContext?.action === "NEW_DOCUMENT" ? "同じ分類の別書類を読み取る" : "書類を読み取る"}</h1>
          <p>{libraryContext ? "書類ライブラリで選択した書類に紐付けて読み取ります。" : "テンプレートと画像を選ぶと、設定済みの項目を自動で読み取ります。"}</p>
        </div>
      </header>
      {libraryContext && <aside className="notice compact"><strong>書類ライブラリから開始</strong><span>{libraryContext.action === "RESCAN_PAGE" ? "新しい画像を以前のページと比較し、承認後に差分を反映します。" : libraryContext.action === "ADD_PAGE" ? "この書類の続きとして新しいページ番号を付けます。" : "現在の書類とは別の書類・別のスプレッドシートとして保存します。"}</span></aside>}
      {unverifiedVersions.length > 0 && (
        <aside className="notice compact action-message" role="status">
          <span>
            <strong>編集中のテンプレートが {unverifiedVersions.length} 件あります</strong>
            <small>
              一覧には表示されますが、設定確認が完了するまでOCRには使用できません。
            </small>
          </span>
          <button type="button" className="secondary compact" onClick={() => openTemplateWorkspace("manage")}>
            テンプレート設定を開く
          </button>
        </aside>
      )}
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

          <section className="form-card ocr-step-card">
            {step === 1 && (
              <section className="ocr-step-panel" aria-labelledby="ocr-step-template">
                <div className="ocr-step-heading">
                  <span>STEP 1</span>
                  <h2 id="ocr-step-template">読み取る書類の種類を選ぶ</h2>
                  <p>読み取る書類と同じ見た目のテンプレートを選択してください。</p>
                </div>
                {templateCards.length > 6 && (
                  <label className="ocr-template-search">
                    <span>テンプレートを検索</span>
                    <input
                      type="search"
                      value={templateQuery}
                      placeholder="名前で絞り込む"
                      onChange={(event) => setTemplateQuery(event.target.value)}
                    />
                  </label>
                )}
                <div className="ocr-template-grid" aria-label="使用するテンプレート">
                  {visibleVersions.map(({ template, version, editVersion, ocrAvailable }) => {
                    const selected = version.id === versionId;
                    const deleting = deletingTemplateId === template.id;
                    const editing = !ocrAvailable;
                    return (
                      <article
                        key={version.id}
                        className={`ocr-template-card${selected ? " selected" : ""}${editing ? " editing" : ""}`}
                      >
                        <button
                          type="button"
                          className="ocr-template-card-select"
                          aria-pressed={selected}
                          aria-label={editing
                            ? `${template.name} v${version.versionNumber}は編集中のためOCRに使用できません`
                            : `${template.name} v${version.versionNumber}を選択`}
                          disabled={editing || deleting}
                          onClick={() => {
                            idempotencyKeyRef.current = null;
                            setSelectionInvalidated(false);
                            setVersionId(version.id);
                          }}
                        >
                          <span className="ocr-template-card-visual">
                            <span className="ocr-template-card-placeholder" aria-hidden="true">型</span>
                            {templatePreviews[version.id] && (
                              <img
                                src={templatePreviews[version.id]}
                                alt=""
                                loading="lazy"
                                onLoad={(event) => {
                                  event.currentTarget.hidden = false;
                                }}
                                onError={(event) => {
                                  event.currentTarget.hidden = true;
                                }}
                              />
                            )}
                            {selected && <span className="ocr-template-card-check" aria-hidden="true">✓</span>}
                            {editing && (
                              <span className="ocr-template-editing-badge">編集中</span>
                            )}
                          </span>
                          <span className="ocr-template-card-copy">
                            <strong>{template.name}</strong>
                            <small>
                              バージョン {version.versionNumber}
                              {editing && " · OCR利用不可"}
                            </small>
                          </span>
                        </button>
                        <span className="ocr-template-card-actions">
                          <button
                            type="button"
                            className="secondary compact"
                            aria-label={`${template.name}を配布用に書き出す`}
                            disabled={deleting}
                            onClick={() => setPackageDialog({ mode: "export", template, version })}
                          >
                            <FiDownload aria-hidden="true" focusable="false" />
                            配布用に書き出す
                          </button>
                          <button
                            type="button"
                            className="secondary compact"
                            aria-label={`${template.name}の設定を確認・修正`}
                            disabled={!editVersion.sourceJobId || deleting}
                            onClick={() => openTemplateWorkspace("manage", editVersion.id)}
                          >
                            <FiSettings aria-hidden="true" focusable="false" />
                            設定を確認・修正
                          </button>
                          <button
                            type="button"
                            className="danger-button compact"
                            aria-label={`${template.name}を削除`}
                            disabled={Boolean(deletingTemplateId)}
                            onClick={() => void deleteTemplate(template)}
                          >
                            {!deleting && <FiTrash2 aria-hidden="true" focusable="false" />}
                            {deleting ? "削除中…" : "削除"}
                          </button>
                        </span>
                      </article>
                    );
                  })}
                  <article className={`ocr-template-card ocr-template-add-card${templateAddMenuOpen ? " open" : ""}`}>
                    <button
                      type="button"
                      className="ocr-template-add-trigger"
                      aria-expanded={templateAddMenuOpen}
                      aria-controls="ocr-template-add-actions"
                      aria-label={templateAddMenuOpen ? "追加メニューを閉じる" : "テンプレートを追加"}
                      onClick={() => setTemplateAddMenuOpen((current) => !current)}
                    >
                      {templateAddMenuOpen
                        ? <FiX aria-hidden="true" focusable="false" />
                        : <FiPlus aria-hidden="true" focusable="false" />}
                      <span className="sr-only">
                        {templateAddMenuOpen ? "追加メニューを閉じる" : "テンプレートを追加"}
                      </span>
                    </button>
                    {templateAddMenuOpen && (
                      <div
                        id="ocr-template-add-actions"
                        className="ocr-template-add-actions"
                        role="group"
                        aria-label="テンプレートの追加方法"
                      >
                        <header>
                          <strong>テンプレートを追加</strong>
                        </header>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => {
                            setTemplateAddMenuOpen(false);
                            openTemplateWorkspace("create");
                          }}
                        >
                          <FiFilePlus aria-hidden="true" focusable="false" />
                          新しく作成
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => {
                            setTemplateAddMenuOpen(false);
                            setPackageDialog({ mode: "import" });
                          }}
                        >
                          <FiUpload aria-hidden="true" focusable="false" />
                          テンプレートを取り込む
                        </button>
                      </div>
                    )}
                  </article>
                </div>
                {templateCards.length === 0 && (
                  <p className="ocr-template-empty">有効化済みのテンプレートはありません。追加カードから作成または取り込みを行ってください。</p>
                )}
                {templateCards.length > 0 && visibleVersions.length === 0 && (
                  <p className="ocr-template-empty">検索条件に一致するテンプレートがありません。</p>
                )}
                {mappingError && (
                  <div className="mapping-load-error" role="alert">
                    <p className="inline-warning">テンプレート設定を確認できませんでした。</p>
                    <button
                      type="button"
                      className="secondary compact"
                      onClick={() => setMappingRetryToken((value) => value + 1)}
                    >
                      もう一度読み込む
                    </button>
                  </div>
                )}
                {mappingPreview !== null && !mappedReadingReady && !mappingError && (
                  <p className="inline-warning" role="status">
                    このテンプレートは現在読み取りに使用できません。テンプレート設定を確認してください。
                  </p>
                )}
                <div className="ocr-step-actions">
                  <span />
                  <button
                    type="button"
                    className="primary"
                    disabled={!versionId || mappingPreview === null || !mappedReadingReady}
                    onClick={() => setStep(2)}
                  >
                    画像の準備へ
                  </button>
                </div>
              </section>
            )}

            {step === 2 && (
              <section className="ocr-step-panel" aria-labelledby="ocr-step-image">
                <div className="ocr-step-heading">
                  <span>STEP 2</span>
                  <h2 id="ocr-step-image">読み取る帳票画像を準備</h2>
                  <p>画像ファイルを選択するか、カメラで帳票を撮影してください。</p>
                </div>
                <div className="input-source-tabs">
                  <div><strong>帳票画像</strong><small>テスト段階では画像ファイルの利用を推奨します。</small></div>
                  <button type="button" className="secondary" onClick={() => setCameraOpen(true)}>カメラを起動</button>
                </div>
                <FilePicker multiple={false} onChange={(files) => {
                  idempotencyKeyRef.current = null;
                  setFile(files[0] ?? null);
                }} label="PNGなどの帳票画像を選択" />
                {file && <p className="selected-file">選択中: <strong>{file.name}</strong></p>}
                <label className="switch-row">
                  <span><strong>文字読み取りも実行する</strong><small>オフにすると画像処理と領域切り出しだけを行います。</small></span>
                  <input type="checkbox" checked={enabled} onChange={(event) => {
                    idempotencyKeyRef.current = null;
                    setEnabled(event.target.checked);
                  }} /><i />
                </label>
                {!enabled && <aside className="notice compact"><strong>画像処理のみ</strong><span>結果欄は空欄として作成され、手入力できます。</span></aside>}
                <div className="ocr-step-actions">
                  <button type="button" className="secondary" onClick={() => setStep(1)}>戻る</button>
                  <button type="button" className="primary" disabled={!file} onClick={() => setStep(3)}>内容確認へ</button>
                </div>
              </section>
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
                  <div><dt>処理内容</dt><dd>{enabled ? "手書き文字を読み取る" : "画像処理のみ"}</dd></div>
                </dl>
                <div className="ocr-step-actions">
                  <button type="button" className="secondary" disabled={busy || Boolean(jobId)} onClick={() => setStep(2)}>戻る</button>
                  <button
                    className="primary"
                    disabled={!file || !versionId || busy || !mappedReadingReady || Boolean(jobId)}
                    onClick={async () => {
                      if (!file || creatingRef.current || jobId) return;
                      creatingRef.current = true;
                      const idempotencyKey = idempotencyKeyRef.current
                        ?? createIdempotencyKey("ocr");
                      idempotencyKeyRef.current = idempotencyKey;
                      setBusy(true);
                      try {
                        const created = await api.createOcr(
                          versionId,
                          file,
                          enabled,
                          idempotencyKey,
                          libraryContext,
                        );
                        idempotencyKeyRef.current = null;
                        setJobId(created.jobId);
                        rememberJob(created.jobId);
                      } catch (error) {
                        showToast({
                          tone: "error",
                          title: "読み取りを開始できませんでした",
                          message: error instanceof ApiError ? error.message : "バックエンドとの接続を確認してください。",
                        });
                      } finally {
                        creatingRef.current = false;
                        setBusy(false);
                      }
                    }}
                  >
                    {busy ? "送信中…" : jobId ? "読み取りを開始しました" : enabled ? "読み取りを開始" : "画像処理を開始"}
                  </button>
                </div>
              </section>
            )}
          </section>
      <JobProgress job={job} connectionLost={connectionLost} />
      {cameraOpen && <CameraCapture
        documentAspect={(activeVersion?.canonicalWidth ?? 1400) / (activeVersion?.canonicalHeight ?? 990)}
        onCapture={(captured) => {
          idempotencyKeyRef.current = null;
          setFile(captured);
          setCameraOpen(false);
        }}
        onClose={() => setCameraOpen(false)}
      />}
      {packageDialog && (
        <TemplatePackageDialog
          mode={packageDialog.mode}
          template={packageDialog.mode === "export" ? packageDialog.template : undefined}
          version={packageDialog.mode === "export" ? packageDialog.version : undefined}
          refreshTemplates={refreshTemplates}
          onClose={() => setPackageDialog(null)}
          onReviewImported={(importedVersionId) => {
            setPackageDialog(null);
            openTemplateWorkspace("manage", importedVersionId);
          }}
          onActivated={(activatedVersionId) => {
            setSelectionInvalidated(false);
            setVersionId(activatedVersionId);
            setStep(1);
            setPackageDialog(null);
          }}
        />
      )}
    </div>
  );
}
