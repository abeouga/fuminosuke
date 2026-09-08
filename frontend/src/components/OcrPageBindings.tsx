import { useEffect, useRef, useState } from "react";
import { api, createIdempotencyKey } from "../api";
import { FilePicker } from "./FilePicker";

export type PageCatalog = {
  templateVersionId: string;
  templateRevision: string;
  available: boolean;
  reason: string;
  pages: { pageId: string; pageNumber: number; range: string; referenceUrl: string }[];
};

export function OcrPageBindings({ versionId, onAccepted }: {
  versionId: string; onAccepted: (jobId: string) => void;
}) {
  const [catalog, setCatalog] = useState<PageCatalog | null>(null);
  const [files, setFiles] = useState<Record<string, { file: File; id: string; revision: number }>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const key = useRef(createIdempotencyKey("ocr"));
  useEffect(() => {
    const current = ++generation.current;
    setCatalog(null); setFiles({}); setError(""); setBusy(false);
    api.getOcrPages(versionId).then((value) => {
      if (current === generation.current) setCatalog(value);
    }).catch((reason: Error) => {
      if (current === generation.current) setError(reason.message);
    });
    return () => { generation.current++; };
  }, [versionId]);

  async function submit() {
    if (!catalog?.available || busy || !catalog.pages.every((p) => files[p.pageId])) return;
    const current = generation.current;
    setBusy(true); setError("");
    try {
      const selected = catalog.pages.map((page) => ({ page, ...files[page.pageId] }));
      const bindings = await Promise.all(selected.map(async (item) => ({
        templatePageId: item.page.pageId, inputId: item.id, inputRevision: item.revision,
        inputSha256: Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await item.file.arrayBuffer())))
          .map((b) => b.toString(16).padStart(2, "0")).join(""),
      })));
      const form = new FormData();
      form.append("manifest", JSON.stringify({ templateVersionId: versionId,
        templateRevision: catalog.templateRevision, bindings, uploadIds: selected.map((i) => i.id) }));
      selected.forEach((item) => form.append("files", item.file));
      const result = await api.createOcrPageJob(form, key.current);
      if (current === generation.current) onAccepted(result.jobId);
    } catch (reason) {
      if (current === generation.current) setError((reason as Error).message);
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  return <section aria-label="ページ別の入力画像">
    {error && <p role="alert">{error}</p>}
    {!catalog && !error && <p>ページ構成を取得しています。</p>}
    {catalog && <>
      {!catalog.available && <p role="status">基準ページ画像の位置精度が未検証のため、実OCRは開始できません。</p>}
      {catalog.pages.map((page) => <fieldset key={page.pageId} disabled={busy}>
        <legend>Page {page.pageNumber} · {page.range}</legend>
        <img src={page.referenceUrl} alt={`Page ${page.pageNumber} 基準画像`} style={{ maxWidth: 160, maxHeight: 220 }} />
        <FilePicker multiple={false} label={`Page ${page.pageNumber} の画像を選択`} onChange={(items) => {
          key.current = createIdempotencyKey("ocr");
          setFiles((old) => {
            const next = { ...old };
            if (items[0]) next[page.pageId] = { file: items[0], id: crypto.randomUUID(),
              revision: (old[page.pageId]?.revision ?? 0) + 1 };
            else delete next[page.pageId];
            return next;
          });
        }} />
        <p>{files[page.pageId] ? "入力画像を指定済み" : "入力画像が未指定です"}</p>
      </fieldset>)}
      <button type="button" disabled={busy || !catalog.available || !catalog.pages.length || !catalog.pages.every((p) => files[p.pageId])}
        onClick={() => void submit()}>全ページを読み取る</button>
      <p>ページ別結果のレビュー・確定・出力は未対応です。</p>
    </>}
  </section>;
}
