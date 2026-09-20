import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { PageFieldEdit, PagedReviewResult } from "../pagedReview";

type Drafts = Record<string, Record<string, PageFieldEdit>>;

export function PagedReviewPage({ jobId, onClose, onNavigationGuardChange }: {
  jobId: string;
  onClose: () => void;
  onNavigationGuardChange?: (dirty: boolean) => void;
}) {
  const [result, setResult] = useState<PagedReviewResult | null>(null);
  const [pageId, setPageId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [busy, setBusy] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [lifecycleUncertain, setLifecycleUncertain] = useState(false);
  const [reviewed, setReviewed] = useState<Record<string, boolean>>({});
  const [documentName, setDocumentName] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const downloading = useRef(false);
  const dirty = Object.values(drafts).some((fields) => Object.keys(fields).length > 0);
  const unsaved = dirty || Boolean(result && !result.confirmed && documentName !== (result.documentName ?? ""));
  useEffect(() => {
    const current = ++generation.current;
    setResult(null); setDrafts({}); setPageId(null); setSelectedId(null);
    setError(null); setNeedsRefresh(false); setBusy(false); inFlight.current = false;
    setDownloadBusy(false); downloading.current = false;
    setLifecycleBusy(false); setLifecycleUncertain(false); setReviewed({}); setDocumentName("");
    void api.pageReview(jobId).then((next) => {
      if (generation.current === current) {
        setResult(next); setPageId(next.pages[0]?.pageId ?? null);
        setDocumentName(next.documentName ?? next.documentMetadata?.proposedName ?? "");
      }
    }).catch((reason) => {
      if (generation.current === current) setError(reason instanceof Error ? reason.message : "結果を取得できませんでした。");
    });
    return () => { generation.current += 1; };
  }, [jobId]);
  useEffect(() => {
    onNavigationGuardChange?.(unsaved || busy);
    const guard = (event: BeforeUnloadEvent) => {
      if (unsaved || busy) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", guard);
    return () => { onNavigationGuardChange?.(false); window.removeEventListener("beforeunload", guard); };
  }, [unsaved, busy, onNavigationGuardChange]);

  const page = result?.pages.find((p) => p.pageId === pageId);
  const selected = page?.fields.find((field) => field.mappingId === selectedId);
  function edit(mappingId: string, patch: Partial<PageFieldEdit>) {
    if (!page || result?.confirmed || lifecycleBusy || lifecycleUncertain) return;
    const field = page.fields.find((item) => item.mappingId === mappingId);
    if (!field) return;
    setReviewed((old) => ({ ...old, [page.pageId]: false }));
    setDrafts((old) => {
      const value = old[page.pageId]?.[mappingId] ?? { mappingId,
        raw: field.reading.raw, normalized: field.reading.normalized, status: field.reading.status,
        alternatives: field.reading.alternatives, reviewReason: field.reading.reviewReason };
      return { ...old, [page.pageId]: { ...old[page.pageId], [mappingId]: { ...value, ...patch } } };
    });
  }
  async function save() {
    if (!result || !page || result.confirmed || inFlight.current || needsRefresh) return;
    const sent = { ...drafts[page.pageId] };
    if (!Object.keys(sent).length) return;
    const current = generation.current;
    const savedPageId = page.pageId;
    inFlight.current = true; setBusy(true); setError(null);
    try {
      const next = await api.correctPage(jobId, savedPageId, result.revision, Object.values(sent));
      if (generation.current !== current) return;
      setResult(next);
      setDrafts((latest) => {
        const retained = { ...latest[savedPageId] };
        for (const [id, value] of Object.entries(sent)) {
          if (retained[id] === value) delete retained[id];
        }
        return { ...latest, [savedPageId]: retained };
      });
    } catch (reason) {
      if (generation.current !== current) return;
      setError(reason instanceof Error ? reason.message : "保存できませんでした。");
      // A lost response may already have committed. Retain all drafts and require
      // an explicit refresh before retrying against a new document revision.
      setNeedsRefresh(true);
    } finally {
      if (generation.current === current) { inFlight.current = false; setBusy(false); }
    }
  }
  async function refresh() {
    if (inFlight.current) return;
    const current = generation.current;
    inFlight.current = true; setBusy(true);
    try {
      const next = await api.pageReview(jobId);
      if (generation.current === current) {
        setResult(next); setNeedsRefresh(false); setLifecycleUncertain(false); setError(null);
        setReviewed({});
        if (next.confirmed) setDocumentName(next.documentName ?? "");
        setPageId((old) => next.pages.some((p) => p.pageId === old) ? old : next.pages[0]?.pageId ?? null);
      }
    } catch (reason) {
      if (generation.current === current) setError(reason instanceof Error ? reason.message : "再取得できませんでした。");
    } finally {
      if (generation.current === current) { inFlight.current = false; setBusy(false); }
    }
  }
  async function changeState(action: "confirm" | "reopen") {
    if (!result || inFlight.current || downloading.current || needsRefresh) return;
    if (action === "confirm" && (dirty || !documentName.trim() || !result.confirmAvailable ||
      !result.pages.every((p) => reviewed[p.pageId]))) return;
    const current = generation.current;
    inFlight.current = true; setBusy(true); setLifecycleBusy(true); setError(null);
    try {
      const next = action === "confirm"
        ? await api.confirmPages(jobId, result.revision, result.pages.map((p) => p.pageId), documentName)
        : await api.reopenPages(jobId, result.revision);
      if (generation.current !== current) return;
      setResult(next); setDocumentName(next.documentName ?? ""); setReviewed({});
    } catch (reason) {
      if (generation.current !== current) return;
      setError(reason instanceof Error ? reason.message : "状態を変更できませんでした。");
      setNeedsRefresh(true); setLifecycleUncertain(true);
    } finally {
      if (generation.current === current) {
        inFlight.current = false; setBusy(false); setLifecycleBusy(false);
      }
    }
  }
  async function download() {
    if (!result?.confirmed || !result.outputAvailable || downloading.current || inFlight.current || needsRefresh) return;
    const current = generation.current;
    downloading.current = true; setDownloadBusy(true); setError(null);
    try {
      const blob = await api.downloadPageOutput(jobId, result.revision);
      if (generation.current !== current) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `${result.documentName}.xlsx`;
      document.body.appendChild(link);
      try { link.click(); } finally {
        link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (reason) {
      if (generation.current !== current) return;
      setError(reason instanceof Error ? reason.message : "XLSXを取得できませんでした。");
      setNeedsRefresh(true);
    } finally {
      if (generation.current === current) { downloading.current = false; setDownloadBusy(false); }
    }
  }
  return <section className="page">
    <div className="page-header"><h2>複数ページの読み取り確認</h2><button className="secondary" onClick={onClose}>戻る</button></div>
    {error && <p role="alert">{error}</p>}
    {(needsRefresh || (!result && error)) && <button disabled={busy} onClick={() => void refresh()}>編集内容を保持して最新結果を取得</button>}
    {!result && !error && <p role="status">読み込み中…</p>}
    {result && <>
      <nav aria-label="確認するページ">{result.pages.map((p) => <button key={p.pageId}
        className={p.pageId === pageId ? "" : "secondary"} aria-pressed={p.pageId === pageId}
        onClick={() => { setPageId(p.pageId); setSelectedId(null); }}>ページ {p.pageNumber}</button>)}</nav>
      <p>全 {result.pages.length} ページ · 保存revision {result.revision}{unsaved ? " · 未保存の変更あり" : ""}</p>
      {result.confirmed && <p role="status">全ページ確定済み</p>}
      {result.confirmed && dirty && <p>未保存の補正を保持しています。保存するには再編集してください。</p>}
      {page && <>
        <h3>ページ {page.pageNumber}：{page.pageRange}</h3>
        {!result.confirmed && <button onClick={() => void save()} disabled={busy || needsRefresh || !Object.keys(drafts[page.pageId] ?? {}).length}>
          {busy ? "保存処理中…" : "このページの変更を保存"}</button>}
        {selected && <img src={selected.cropUrl} alt={`${selected.label}の読み取り領域`} style={{ maxWidth: "100%", maxHeight: 360, objectFit: "contain" }} />}
        <div>{page.fields.map((field) => {
          const value = result.confirmed ? field.reading : (drafts[page.pageId]?.[field.mappingId] ?? field.reading);
          return <div key={field.mappingId} className="card">
            <button className="secondary" onClick={() => setSelectedId(field.mappingId)}>{field.label}の領域を表示</button>
            {result.documentMetadata?.identityFields.some((identity) => identity.pageId === page.pageId && identity.mappingId === field.mappingId) && <span>書類の識別項目</span>}
            <label>{field.label}<textarea value={value.raw ?? ""} maxLength={500} disabled={result.confirmed || lifecycleBusy || lifecycleUncertain}
              onFocus={() => setSelectedId(field.mappingId)} onChange={(event) => edit(field.mappingId, {
                raw: event.target.value || null, normalized: event.target.value || null,
                status: event.target.value ? "readable" : "blank", alternatives: [], reviewReason: null,
              })} /></label>
            <label>{field.label}の判定<select value={value.status} disabled={result.confirmed || lifecycleBusy || lifecycleUncertain} onChange={(event) => {
              const status = event.target.value as PageFieldEdit["status"];
              edit(field.mappingId, { status, normalized: status === "readable" ? value.raw : null,
                ...(status === "blank" ? { raw: null } : {}) });
            }}><option value="readable">読取可</option><option value="uncertain">要確認</option><option value="blank">空欄</option></select></label>
            {value.reviewReason && <p>{value.reviewReason}</p>}
          </div>;
        })}</div>
        {page.validation.issues.map((issue, index) => <p key={index}>{issue.message}</p>)}
        {!result.confirmed && result.confirmAvailable && <label>
          <input type="checkbox" checked={Boolean(reviewed[page.pageId])}
            disabled={busy || needsRefresh || Boolean(Object.keys(drafts[page.pageId] ?? {}).length)}
            onChange={(event) => setReviewed((old) => ({ ...old, [page.pageId]: event.target.checked }))} />
          このページの内容を確認しました
        </label>}
      </>}
      <label>保存する書類名<input value={documentName} maxLength={180} disabled={result.confirmed || lifecycleBusy || lifecycleUncertain}
        onChange={(event) => setDocumentName(event.target.value)} /></label>
      {!result.confirmed && result.documentMetadata?.proposedName && <button className="secondary"
        disabled={busy || needsRefresh || lifecycleUncertain} onClick={() => setDocumentName(result.documentMetadata?.proposedName ?? "")}>
        設定した項目から書類名を反映</button>}
      {result.confirmed
        ? <button className="secondary" disabled={busy || downloadBusy || needsRefresh} onClick={() => void changeState("reopen")}>再編集する</button>
        : <button disabled={busy || needsRefresh || dirty || !documentName.trim() || !result.confirmAvailable ||
            !result.pages.every((p) => reviewed[p.pageId])} onClick={() => void changeState("confirm")}>全ページを確定</button>}
      {result.confirmed && result.outputAvailable && <button className="secondary" disabled={busy || downloadBusy || needsRefresh}
        onClick={() => void download()}>{downloadBusy ? "XLSXを取得中…" : "全ページの記入済みXLSXを取得"}</button>}
      <p>書類一覧への登録は準備中です。</p>
    </>}
  </section>;
}
