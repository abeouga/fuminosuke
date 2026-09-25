import { useEffect, useRef, useState, type DragEvent } from "react";
import { api, createIdempotencyKey } from "../api";

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

function PageImagePicker({ pageNumber, file, disabled, onSelect }: {
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

  return <div className="ocr-page-image-picker">
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
        : <span className="ocr-page-drop-icon" aria-hidden="true">↓</span>}
      <span className="ocr-page-drop-copy">
        <strong>{file ? "画像を置換" : "ここに画像をドロップ"}</strong>
        <small>{file ? file.name : "またはクリックして選択"}</small>
      </span>
      <span className="ocr-page-file-rule">PNG / JPEG / WebP / TIFF · 最大20 MB</span>
    </label>
    {validationError && <p className="ocr-page-file-error" role="alert">{validationError}</p>}
  </div>;
}

export function OcrPageBindings({ versionId, templateName, versionNumber, onAccepted, onBack, libraryContextUnsupported = false }: {
  versionId: string; onAccepted: (jobId: string) => void; onBack?: () => void;
  templateName?: string; versionNumber?: number;
  libraryContextUnsupported?: boolean;
}) {
  const [catalog, setCatalog] = useState<PageCatalog | null>(null);
  const [files, setFiles] = useState<Record<string, { file: File; id: string; revision: number }>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const submitting = useRef(false);
  const key = useRef(createIdempotencyKey("ocr"));
  useEffect(() => {
    const current = ++generation.current;
    setCatalog(null); setFiles({}); setError(""); setBusy(false); setAccepted(false);
    submitting.current = false; key.current = createIdempotencyKey("ocr");
    api.getOcrPages(versionId).then((value) => {
      if (value.templateVersionId !== versionId) throw new Error("ページ構成の版が一致しません。再取得してください。");
      if (current === generation.current) setCatalog(value);
    }).catch((reason: Error) => {
      if (current === generation.current) setError(reason.message);
    });
    return () => { generation.current++; };
  }, [versionId, retry]);

  const currentCatalog = catalog?.templateVersionId === versionId ? catalog : null;
  const selectedCount = currentCatalog?.pages.filter((page) => files[page.pageId]).length ?? 0;
  const pageCount = currentCatalog?.pages.length ?? 0;
  const canSubmit = currentCatalog?.available === true && !libraryContextUnsupported
    && currentCatalog.pages.length > 0 && currentCatalog.pages.every((p) => files[p.pageId]);

  function selectPageFile(pageId: string, file: File) {
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
      if (current === generation.current) { setAccepted(true); onAccepted(result.jobId); }
    } catch (reason) {
      if (current === generation.current) setError((reason as Error).message);
    } finally {
      if (current === generation.current) { submitting.current = false; setBusy(false); }
    }
  }
  return <section className="ocr-page-bindings" aria-label="ページ別の入力画像">
    <header className="ocr-page-bindings-header">
      <div>
        <span>STEP 2 · PAGE INPUT</span>
        <h2>ページごとに記入済み画像を指定</h2>
        <p>{templateName}{versionNumber != null ? ` · v${versionNumber}` : ""} の基準画像と同じページを、右側へ直接ドロップしてください。</p>
      </div>
      {currentCatalog && <div className="ocr-page-progress" aria-label={`画像選択 ${selectedCount} / ${pageCount} ページ`}>
        <strong>{selectedCount}<small> / {pageCount} ページ</small></strong>
        <span><i style={{ width: `${pageCount ? (selectedCount / pageCount) * 100 : 0}%` }} /></span>
      </div>}
    </header>
    <p className="ocr-page-review-notice">読み取り結果は確認前の候補です。位置合わせと切り出しの対応を検査しますが、筆画の欠落や隣欄の混入を完全には保証しません。全ページの画像・読み取り欄・文字を確認し、必要な修正後に確定してください。</p>
    {libraryContextUnsupported && <p role="alert">ページ別OCRは書類ライブラリへの追加・再読取に未対応です。通常の読み取り画面から開始してください。</p>}
    {error && <p role="alert">{error}</p>}
    {!currentCatalog && !error && <p>ページ構成を取得しています。</p>}
    {!currentCatalog && error && <button type="button" onClick={() => setRetry((value) => value + 1)}>ページ構成を再取得</button>}
    {currentCatalog && <>
      {!currentCatalog.available && <p className="ocr-page-admission-block" role="status">このテンプレートでは現在読み取りを開始できません。{currentCatalog.reason}</p>}
      <div className="ocr-page-card-grid">
        {currentCatalog.pages.map((page) => {
          const selected = files[page.pageId];
          return <fieldset className={`ocr-page-card${selected ? " selected" : ""}`} key={page.pageId} disabled={busy || accepted}>
            <legend><strong>Page {page.pageNumber} · {page.range}</strong></legend>
            <div className="ocr-page-card-body">
              <figure className="ocr-page-reference">
                <img src={page.referenceUrl} alt={`Page ${page.pageNumber} 基準画像`} />
                <figcaption>基準画像</figcaption>
              </figure>
              <PageImagePicker pageNumber={page.pageNumber} file={selected?.file} disabled={busy || accepted}
                onSelect={(file) => selectPageFile(page.pageId, file)} />
            </div>
            <p className="ocr-page-card-status"><i aria-hidden="true" />{selected ? "入力画像を指定済み" : "入力画像が未指定です"}</p>
          </fieldset>;
        })}
      </div>
      <footer className="ocr-page-submit-bar">
        <div><strong>{selectedCount === pageCount ? "全ページの画像を確認してください" : `残り ${pageCount - selectedCount} ページです`}</strong><span>読み取り後に、ページ別の確認・修正と全ページの確定へ進みます。</span></div>
        <button type="button" className="primary" disabled={busy || accepted || !canSubmit}
          onClick={() => void submit()}>{accepted ? "全ページの読み取りを受け付けました" : busy ? "全ページを送信中…" : "全ページを読み取る"}</button>
      </footer>
    </>}
    {onBack && <button type="button" className="secondary ocr-page-back" disabled={busy || accepted} onClick={onBack}>別のテンプレートを選ぶ</button>}
  </section>;
}
