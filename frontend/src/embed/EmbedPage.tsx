import { useEffect, useMemo, useRef, useState } from "react";
import { FiExternalLink, FiFileText, FiX } from "react-icons/fi";
import { ImageLightbox, type LightboxImage } from "../components/ImageLightbox";
import { Spinner } from "../components/Spinner";
import { EditingError } from "../editing/client";
import { PageViewer } from "../pages/ShelfPage";
import { structuredClient, type StructuredDocument } from "../structured/client";
import { allowedOrigin, initialParentOrigin, parentMessage, postToParent, type EmbedErrorCode } from "./embedChannel";

/**
 * A confirmed document's page images with its cells outlined, to be shown inside another
 * application's page (`/embed?job=<document id>&field=<field id>`). It looks like the inside of the
 * chat's document panel; the embedding page draws the frame around it. Only for looking: nothing here
 * edits, confirms or deletes; 「文之助で開く」 opens the usual review screen in a new tab.
 */
export function EmbedPage() {
  const [params] = useState(() => new URLSearchParams(window.location.search));
  const jobId = params.get("job");
  const [fieldId, setFieldId] = useState<string | null>(params.get("field"));
  // The answer's cells on this document, sent by the parent; null shows every cell with only `fieldId` picked.
  const [fieldIds, setFieldIds] = useState<string[] | null>(null);
  const [document, setDocument] = useState<StructuredDocument | null>(null);
  const [failure, setFailure] = useState<EmbedErrorCode | null>(jobId ? null : "DOCUMENT_NOT_FOUND");
  const [expanded, setExpanded] = useState<LightboxImage | null>(null);
  const parent = useRef<string | null>(initialParentOrigin());

  useEffect(() => {
    if (!jobId) { postToParent(parent.current, { type: "embed:error", code: "DOCUMENT_NOT_FOUND" }); return; }
    let stopped = false;
    void structuredClient.document(jobId)
      .then((value) => {
        if (stopped) return;
        setDocument(value);
        postToParent(parent.current, { type: "embed:ready" });
      })
      .catch((error: unknown) => {
        if (stopped) return;
        const code: EmbedErrorCode = error instanceof EditingError && (error.status === 404 || error.status === 409) ? "DOCUMENT_NOT_FOUND" : "LOAD_FAILED";
        setFailure(code);
        postToParent(parent.current, { type: "embed:error", code });
      });
    return () => { stopped = true; };
  }, [jobId]);

  // The parent changes the outlined cells without reloading the document.
  useEffect(() => {
    const listen = (event: MessageEvent) => {
      if (event.source !== window.parent || !allowedOrigin(event.origin)) return;
      const message = parentMessage(event.data);
      if (!message) return;
      parent.current = event.origin;
      setFieldId(message.fieldId);
      setFieldIds(message.fieldIds);
    };
    window.addEventListener("message", listen);
    return () => window.removeEventListener("message", listen);
  }, []);

  // × and Escape ask the parent to close the panel (the lightbox closes first).
  const close = () => postToParent(parent.current, { type: "embed:close" });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !expanded) postToParent(parent.current, { type: "embed:close" }); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const readings = useMemo(() => {
    if (!document) return [];
    if (!fieldIds) return document.fields;
    const ids = new Set(fieldIds);
    if (fieldId) ids.add(fieldId);
    return document.fields.filter((reading) => ids.has(reading.fieldId));
  }, [document, fieldIds, fieldId]);
  const picked = useMemo(() => (fieldId ? readings.find((reading) => reading.fieldId === fieldId) ?? null : null), [readings, fieldId]);
  const fieldMissing = Boolean(document && fieldId && !document.fields.some((reading) => reading.fieldId === fieldId));
  useEffect(() => {
    if (fieldMissing) postToParent(parent.current, { type: "embed:error", code: "FIELD_NOT_FOUND" });
  }, [fieldMissing, fieldId]);

  const openHref = jobId ? `/?job=${encodeURIComponent(jobId)}${picked ? `&field=${encodeURIComponent(picked.fieldId)}` : ""}` : "/";
  const title = <h2 className="ask-document-title"><FiFileText aria-hidden="true" /><span>{document?.documentName ?? "（名称なし）"}</span></h2>;
  const actions = <>
    <a className="ask-icon-button ask-document-close embed-open" href={openHref} target="_blank" rel="noopener noreferrer"
      aria-label="文之助で開く" title="文之助で開く"><FiExternalLink aria-hidden="true" /></a>
    <button type="button" className="ask-icon-button ask-document-close" onClick={close} aria-label="書類を閉じる" title="閉じる"><FiX aria-hidden="true" /></button>
  </>;

  if (failure || !document) {
    return <main className="embed-page">
      <div className="ask-panel-head">{title}{actions}</div>
      {failure
        ? <p className="ask-panel-lead" role="alert">{failure === "DOCUMENT_NOT_FOUND" ? "この書類は見つかりません。確定済みの書類だけを表示できます。" : "書類を読み込めませんでした。"}</p>
        : <p className="ask-panel-lead" role="status"><Spinner />書類を読み込んでいます…</p>}
    </main>;
  }
  return <main className="embed-page">
    <PageViewer pages={document.pages ?? []} readings={readings} picked={picked} headings={[]} pickedHeading={null} marked={fieldIds !== null}
      onPick={(reading) => setFieldId(reading.fieldId)} onPickHeading={() => undefined}
      title={title} actions={actions} onExpand={setExpanded} />
    {fieldMissing && <p className="ask-panel-lead" role="alert">指定の欄はこの書類にありません。</p>}
    {expanded && <ImageLightbox image={expanded} onClose={() => setExpanded(null)} />}
  </main>;
}
