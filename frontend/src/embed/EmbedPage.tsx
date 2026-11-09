import { useEffect, useMemo, useRef, useState } from "react";
import { FiExternalLink, FiFileText } from "react-icons/fi";
import { Spinner } from "../components/Spinner";
import { choiceText } from "../components/OcrReviewResults";
import { EditingError } from "../editing/client";
import { PageViewer } from "../pages/ShelfPage";
import { structuredClient, type StructuredDocument, type StructuredReading } from "../structured/client";
import { allowedOrigin, initialParentOrigin, parentMessage, postToParent, type EmbedErrorCode } from "./embedChannel";

/**
 * A confirmed document's page images with one field outlined, to be shown inside another
 * application's page (`/embed?job=<document id>&field=<field id>`). Only for looking: nothing here
 * edits, confirms or deletes; 「文之助で開く」 opens the usual review screen in a new tab.
 */
export function EmbedPage() {
  const [params] = useState(() => new URLSearchParams(window.location.search));
  const jobId = params.get("job");
  const [fieldId, setFieldId] = useState<string | null>(params.get("field"));
  const [document, setDocument] = useState<StructuredDocument | null>(null);
  const [failure, setFailure] = useState<EmbedErrorCode | null>(jobId ? null : "DOCUMENT_NOT_FOUND");
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

  // The parent changes the outlined field without reloading the document.
  useEffect(() => {
    const listen = (event: MessageEvent) => {
      if (event.source !== window.parent || !allowedOrigin(event.origin)) return;
      const message = parentMessage(event.data);
      if (!message) return;
      parent.current = event.origin;
      setFieldId(message.fieldId);
    };
    window.addEventListener("message", listen);
    return () => window.removeEventListener("message", listen);
  }, []);

  const picked = useMemo(() => (document && fieldId ? document.fields.find((reading) => reading.fieldId === fieldId) ?? null : null), [document, fieldId]);
  const fieldMissing = Boolean(document && fieldId && !picked);
  useEffect(() => {
    if (fieldMissing) postToParent(parent.current, { type: "embed:error", code: "FIELD_NOT_FOUND" });
  }, [fieldMissing, fieldId]);

  const openHref = jobId ? `/?job=${encodeURIComponent(jobId)}${picked ? `&field=${encodeURIComponent(picked.fieldId)}` : ""}` : "/";
  const open = <a className="embed-open" href={openHref} target="_blank" rel="noopener noreferrer"><FiExternalLink aria-hidden="true" />文之助で開く</a>;

  if (failure) {
    return <main className="embed-page">
      <p className="embed-message" role="alert">
        {failure === "DOCUMENT_NOT_FOUND" ? "この書類は見つかりません。確定済みの書類だけを表示できます。" : "書類を読み込めませんでした。"}
      </p>
    </main>;
  }
  if (!document) {
    return <main className="embed-page"><p className="embed-message" role="status"><Spinner />書類を読み込んでいます…</p></main>;
  }
  const title = <h1 className="embed-title"><FiFileText aria-hidden="true" /><span>{document.documentName ?? "（名称なし）"}</span><small>{document.templateName}</small></h1>;
  return <main className="embed-page">
    <PageViewer pages={document.pages ?? []} readings={document.fields} picked={picked} headings={[]} pickedHeading={null}
      onPick={(reading) => setFieldId(reading.fieldId)} onPickHeading={() => undefined}
      title={title} actions={open} />
    {fieldMissing
      ? <p className="embed-note" role="alert">指定の欄はこの書類にありません。</p>
      : picked && <PickedLine reading={picked} />}
  </main>;
}

function PickedLine({ reading }: { reading: StructuredReading }) {
  const value = reading.value === null ? null : choiceText(reading.value, reading.choiceLabels ?? undefined);
  return <p className="embed-note" aria-live="polite">
    <span className="embed-note-label">{reading.label}</span>
    <span className={value === null ? "embed-note-blank" : undefined}>{value ?? "空欄"}</span>
  </p>;
}
