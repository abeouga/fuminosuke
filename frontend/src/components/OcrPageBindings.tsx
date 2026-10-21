import { useEffect, useRef, useState, type DragEvent, type Ref } from "react";
import { FiArrowLeft, FiPlay, FiX } from "react-icons/fi";
import { api, createIdempotencyKey } from "../api";
import { TemplateNameEditor } from "./TemplateNameEditor";
import { TemplateLoading } from "./TemplateLoading";
import { usePreviewDialog } from "../hooks/usePreviewDialog";
import { Spinner } from "./Spinner";

const PAGE_IMAGE_ACCEPT = "image/png,image/jpeg,image/webp,image/tiff";
const PAGE_IMAGE_MAX_BYTES = 20 * 1024 * 1024;
const PAGE_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"];

export type PageCatalog = {
  templateVersionId: string;
  templateRevision: string;
  available: boolean;
  reason: string;
  pages: { pageId: string; pageNumber: number; range: string; referenceUrl: string }[];
};

function pageImageError(file: File) {
  const lowerName = file.name.toLocaleLowerCase();
  const supported = PAGE_IMAGE_ACCEPT.split(",").includes(file.type)
    || PAGE_IMAGE_EXTENSIONS.some((extension) => lowerName.endsWith(extension));
  if (!supported) return "PNG / JPEG / WebP / TIFF の画像を指定してください。";
  if (file.size > PAGE_IMAGE_MAX_BYTES) return "画像は20 MB以下にしてください。";
  return "";
}

function PageImagePicker({ pageNumber, file, disabled, onSelect, pickerRef }: {
  pickerRef?: Ref<HTMLDivElement>;
  pageNumber: number;
  file?: File;
  disabled: boolean;
  onSelect: (file: File) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [validationError, setValidationError] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");

  useEffect(() => {
    if (!file) { setPreviewUrl(""); return; }
    const next = URL.createObjectURL(file);
    setPreviewUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);

  function acceptFile(next: File) {
    const message = pageImageError(next);
    setValidationError(message);
    if (!message) onSelect(next);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    const dropped = Array.from(event.dataTransfer.files);
    if (dropped.length !== 1) {
      setValidationError("1ページにつき画像を1ファイルだけ指定してください。");
      return;
    }
    acceptFile(dropped[0]);
  }

  return <div className="ocr-page-image-picker" ref={pickerRef}>
    <label
      className={`ocr-page-drop-zone${dragging ? " dragging" : ""}${file ? " has-file" : ""}`}
      onDragEnter={(event) => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={handleDrop}
    >
      <input
        type="file"
        accept={PAGE_IMAGE_ACCEPT}
        disabled={disabled}
        aria-label={`Page ${pageNumber} の画像を選択`}
        onChange={(event) => {
          const selected = event.target.files?.[0];
          event.target.value = "";
          if (selected) acceptFile(selected);
        }}
      />
      {previewUrl ? <img src={previewUrl} alt={`Page ${pageNumber} 選択画像`} />
        : <span className="ocr-page-drop-icon" aria-hidden="true">＋</span>}
      <span className="ocr-page-drop-copy">
        <strong>{file ? "画像を置換" : `Page ${pageNumber} の画像を選択またはドロップ`}</strong>
        <small>{file ? file.name : "PNG / JPEG / WebP / TIFF・最大20 MB"}</small>
      </span>
    </label>
    {validationError && <p className="ocr-page-file-error" role="alert">{validationError}</p>}
  </div>;
}

export function OcrPageBindings({ versionId, templateId, templateName, onRenameTemplate, onAccepted, onJobCreated, onBack, onNavigationGuardChange }: {
  versionId: string; onAccepted: (jobId: string) => void; onBack?: () => void;
  templateId?: string; onRenameTemplate?: (templateId: string, name: string) => Promise<string>;
  templateName?: string; versionNumber?: number;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
  onJobCreated?: (jobId: string) => void;
}) {
  const [catalog, setCatalog] = useState<PageCatalog | null>(null);
  const [files, setFiles] = useState<Record<string, { file: File; id: string; revision: number }>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [retry, setRetry] = useState(0);
  const [referencePreview, setReferencePreview] = useState<{ src: string; label: string } | null>(null);
  const generation = useRef(0);
  const submitting = useRef(false);
  const key = useRef(createIdempotencyKey("ocr"));
  const previewDialogRef = useRef<HTMLElement | null>(null);
  const previewReturnFocusRef = useRef<HTMLElement | null>(null);
  const pickerRefs = useRef(new Map<string, HTMLDivElement>());
  const submitBarRef = useRef<HTMLElement | null>(null);
  const pendingAdvance = useRef<string | null>(null);
  const navigationRisk = busy || accepted || Object.keys(files).length > 0;
  useEffect(() => { onNavigationGuardChange?.(navigationRisk); }, [navigationRisk, onNavigationGuardChange]);
  useEffect(() => () => onNavigationGuardChange?.(false), [onNavigationGuardChange]);
  usePreviewDialog(Boolean(referencePreview), () => setReferencePreview(null), previewDialogRef, previewReturnFocusRef);
  useEffect(() => {
    const current = ++generation.current;
    pendingAdvance.current = null;
    setCatalog(null); setFiles({}); setError(""); setBusy(false); setAccepted(false);
    submitting.current = false; key.current = createIdempotencyKey("ocr");
    api.getOcrPages(versionId).then((value) => {
      if (value.templateVersionId !== versionId) throw new Error("ページ構成が変わりました。再取得してください。");
      if (current === generation.current) setCatalog(value);
    }).catch((reason: Error) => {
      if (current === generation.current) setError(reason.message);
    });
    return () => { generation.current++; };
  }, [versionId, retry]);

  const currentCatalog = catalog?.templateVersionId === versionId ? catalog : null;
  const selectedCount = currentCatalog?.pages.filter((page) => files[page.pageId]).length ?? 0;
  const pageCount = currentCatalog?.pages.length ?? 0;
  const canSubmit = currentCatalog?.available === true
    && currentCatalog.pages.length > 0 && currentCatalog.pages.every((p) => files[p.pageId]);

  useEffect(() => {
    const addedPage = pendingAdvance.current;
    pendingAdvance.current = null;
    if (!addedPage || !files[addedPage] || !currentCatalog || referencePreview || busy || accepted) return;
    const nextPage = [...currentCatalog.pages]
      .sort((a, b) => a.pageNumber - b.pageNumber)
      .find((page) => !files[page.pageId]);
    const target = nextPage ? pickerRefs.current.get(nextPage.pageId) : submitBarRef.current;
    if (!target) return;
    const scroll = (behavior: ScrollBehavior) => target.scrollIntoView?.({ behavior, block: "center", inline: "nearest" });
    const frame = requestAnimationFrame(() => {
      scroll(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth");
    });
    // Recalculate against the live layout if the window changes during the transition.
    const onResize = () => scroll("instant");
    const stopFollowing = () => window.removeEventListener("resize", onResize);
    window.addEventListener("resize", onResize);
    window.addEventListener("wheel", stopFollowing, { once: true, passive: true });
    window.addEventListener("pointerdown", stopFollowing, { once: true });
    const timer = window.setTimeout(stopFollowing, 800);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      stopFollowing();
      window.removeEventListener("wheel", stopFollowing);
      window.removeEventListener("pointerdown", stopFollowing);
    };
  }, [files, currentCatalog, referencePreview, busy, accepted]);

  function selectPageFile(pageId: string, file: File) {
    pendingAdvance.current = files[pageId] ? null : pageId;
    key.current = createIdempotencyKey("ocr");
    setFiles((old) => ({
      ...old,
      [pageId]: { file, id: crypto.randomUUID(), revision: (old[pageId]?.revision ?? 0) + 1 },
    }));
  }

  async function submit() {
    if (!currentCatalog || !canSubmit || submitting.current || accepted) return;
    submitting.current = true;
    const current = generation.current;
    setBusy(true); setError("");
    try {
      const selected = currentCatalog.pages.map((page) => ({ page, ...files[page.pageId] }));
      const bindings = await Promise.all(selected.map(async (item) => ({
        templatePageId: item.page.pageId, inputId: item.id, inputRevision: item.revision,
        inputSha256: Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await item.file.arrayBuffer())))
          .map((b) => b.toString(16).padStart(2, "0")).join(""),
      })));
      if (current !== generation.current) return;
      const form = new FormData();
      form.append("manifest", JSON.stringify({ templateVersionId: versionId,
        templateRevision: currentCatalog.templateRevision, bindings, uploadIds: selected.map((i) => i.id) }));
      selected.forEach((item) => form.append("files", item.file));
      const result = await api.createOcrPageJob(form, key.current);
      onJobCreated?.(result.jobId);
      if (current === generation.current) { setAccepted(true); onAccepted(result.jobId); }
    } catch (reason) {
      if (current === generation.current) setError((reason as Error).message);
    } finally {
      if (current === generation.current) { submitting.current = false; setBusy(false); }
    }
  }
  return <section className="ocr-page-bindings ocr-step-panel" aria-labelledby="ocr-page-bindings-title">
    <header className="ocr-reading-step-header ocr-page-bindings-header">
      {onBack && <button type="button" className="secondary compact ocr-reading-back"
        aria-label="テンプレート選択画面に戻る" title="テンプレート選択画面に戻る"
        disabled={busy || accepted} onClick={() => {
          if (navigationRisk && !window.confirm("選択したページ画像を破棄してテンプレート選択へ戻りますか？")) return;
          onBack();
        }}>
        <FiArrowLeft aria-hidden="true" focusable="false" />
      </button>}
      {templateId && onRenameTemplate
        ? <TemplateNameEditor headingId="ocr-page-bindings-title"
            name={templateName ?? "選択したテンプレート"}
            onSave={(name) => onRenameTemplate(templateId, name)} />
        : <h2 id="ocr-page-bindings-title">{templateName ?? "選択したテンプレート"}</h2>}
      {currentCatalog && <div className="ocr-page-progress" aria-label={`画像選択 ${selectedCount} / ${pageCount} ページ`}>
        <strong>{selectedCount}<small> / {pageCount} ページ</small></strong>
        <span><i style={{ width: `${pageCount ? (selectedCount / pageCount) * 100 : 0}%` }} /></span>
      </div>}
    </header>
    {error && <p role="alert">{error}</p>}
    {!currentCatalog && !error && <TemplateLoading message="ページ構成を確認しています" />}
    {!currentCatalog && error && <button type="button" onClick={() => setRetry((value) => value + 1)}>ページ構成を再取得</button>}
    {currentCatalog && <>
      {!currentCatalog.available && <p className="ocr-page-admission-block" role="status">このテンプレートでは現在読み取りを開始できません。{currentCatalog.reason}</p>}
      <div className="ocr-page-card-grid">
        {currentCatalog.pages.map((page) => {
          const selected = files[page.pageId];
          return <fieldset className={`ocr-page-card${selected ? " selected" : ""}`} key={page.pageId} disabled={busy || accepted}>
            <legend><strong>Page {page.pageNumber}</strong></legend>
            <div className="ocr-page-card-body">
              <figure className="ocr-page-reference">
                <button type="button" className="ocr-page-reference-trigger"
                  aria-label={`Page ${page.pageNumber} 基準画像を拡大表示`}
                  onClick={(event) => {
                    const label = `Page ${page.pageNumber} 基準画像`;
                    previewReturnFocusRef.current = event.currentTarget;
                    setReferencePreview({ src: page.referenceUrl, label });
                  }}>
                  <img src={page.referenceUrl} alt={`Page ${page.pageNumber} 基準画像`} />
                </button>
              </figure>
              <PageImagePicker pageNumber={page.pageNumber} file={selected?.file} disabled={busy || accepted}
                pickerRef={(element) => {
                  if (element) pickerRefs.current.set(page.pageId, element);
                  else pickerRefs.current.delete(page.pageId);
                }}
                onSelect={(file) => selectPageFile(page.pageId, file)} />
            </div>
          </fieldset>;
        })}
      </div>
      <footer className="ocr-page-submit-bar" ref={submitBarRef}>
        <div><strong>{selectedCount === pageCount ? "全ページの画像を確認してください" : `残り ${pageCount - selectedCount} ページです`}</strong></div>
        <button type="button" className="primary" disabled={busy || accepted || !canSubmit}
          onClick={() => void submit()}><FiPlay aria-hidden="true" />{accepted ? "全ページの読み取りを受け付けました" : busy ? <><Spinner />全ページを送信中…</> : "全ページを読み取る"}</button>
      </footer>
    </>}
    {referencePreview && (
      <div className="ocr-template-preview-backdrop" onMouseDown={(event) => {
        if (event.target === event.currentTarget) setReferencePreview(null);
      }}>
        <section ref={previewDialogRef} tabIndex={-1} className="ocr-template-preview-dialog" role="dialog" aria-modal="true"
          aria-labelledby="ocr-page-reference-preview-title">
          <header>
            <h2 id="ocr-page-reference-preview-title">{referencePreview.label}</h2>
            <button type="button" className="secondary compact"
              aria-label="プレビューを閉じる" title="閉じる"
              onClick={() => setReferencePreview(null)}>
              <FiX aria-hidden="true" focusable="false" />
            </button>
          </header>
          <img src={referencePreview.src} alt={referencePreview.label} />
        </section>
      </div>
    )}
  </section>;
}
