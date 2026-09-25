import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { api } from "../api";
import {
  AnchoredReviewPopover,
  buildReviewDisplayModel,
  CellStatusPopover,
  GenericRecordReviewTable,
  IndependentReviewGroups,
  KeyValueReviewTable,
  ReviewResultsToolbar,
  ReviewTableSection,
  type ReadingEntry,
  type ReviewDisplayModel,
  type ReviewDisplayRecordSection,
  type ReviewInteractionMode,
} from "../components/OcrReviewResults";
import type { PageFieldEdit, PagedReviewResult } from "../pagedReview";
import type { FieldReading, OpenAIUsageRecord, OcrFieldPreview } from "../types";

type Drafts = Record<string, Record<string, PageFieldEdit>>;

export function PagedReviewPage({ jobId, onClose, onNavigationGuardChange }: {
  jobId: string;
  onClose: () => void;
  onNavigationGuardChange?: (dirty: boolean) => void;
}) {
  const [result, setResult] = useState<PagedReviewResult | null>(null);
  const [pageId, setPageId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null);
  const [previewRatio, setPreviewRatio] = useState(38);
  const [interactionMode, setInteractionMode] = useState<ReviewInteractionMode>("edit");
  const [drafts, setDrafts] = useState<Drafts>({});
  const [busy, setBusy] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [lifecycleUncertain, setLifecycleUncertain] = useState(false);
  const [reviewed, setReviewed] = useState<Record<string, boolean>>({});
  const [documentName, setDocumentName] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewImageFailures, setPreviewImageFailures] = useState<Record<string, boolean>>({});
  const [usageRecords, setUsageRecords] = useState<OpenAIUsageRecord[]>([]);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const downloading = useRef(false);
  const dirty = Object.values(drafts).some((fields) => Object.keys(fields).length > 0);
  const unsaved = dirty || Boolean(result && !result.confirmed && documentName !== (result.documentName ?? ""));
  useEffect(() => {
    const current = ++generation.current;
    setResult(null); setDrafts({}); setPageId(null); setSelectedId(null); setLastSelectedId(null);
    setError(null); setNeedsRefresh(false); setBusy(false); inFlight.current = false;
    setDownloadBusy(false); downloading.current = false;
    setLifecycleBusy(false); setLifecycleUncertain(false); setReviewed({}); setDocumentName("");
    setPreviewImageFailures({}); setUsageRecords([]); setInteractionMode("edit");
    void api.pageReview(jobId).then((next) => {
      if (generation.current === current) {
        setResult(next); setPageId(next.pages[0]?.pageId ?? null);
        setDocumentName(next.documentName ?? next.documentMetadata?.proposedName ?? "");
      }
    }).catch((reason) => {
      if (generation.current === current) setError(reason instanceof Error ? reason.message : "結果を取得できませんでした。");
    });
    void api.openaiUsage().then((report) => {
      if (generation.current === current) {
        setUsageRecords(report.records.filter((record) => record.jobId === jobId));
      }
    }).catch(() => undefined);
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
  const pageIndex = result?.pages.findIndex((p) => p.pageId === pageId) ?? -1;
  const selected = page?.fields.find((field) => field.mappingId === selectedId);
  const previewImageUrl = selected?.cropUrl ?? page?.pageImageUrl ?? "";
  const handwritingImageUrl = page?.pageImageUrl.replace(/alignedSource$/, "handwritingOnly") ?? "";
  const pageWarningCount = page?.fields.reduce((count, field) => {
    const value = result?.confirmed ? field.reading : (drafts[page.pageId]?.[field.mappingId] ?? field.reading);
    return count + (value.status === "uncertain" || Boolean(value.reviewReason) ? 1 : 0);
  }, 0) ?? 0;
  const totalWarningCount = result?.pages.reduce((total, item) => total + item.fields.reduce((count, field) => {
    const value = result.confirmed ? field.reading : (drafts[item.pageId]?.[field.mappingId] ?? field.reading);
    return count + (value.status === "uncertain" || Boolean(value.reviewReason) ? 1 : 0);
  }, 0), 0) ?? 0;
  const usage = useMemo(() => summarizeUsage(usageRecords), [usageRecords]);
  const pageEntries = useMemo<ReadingEntry[]>(() => page?.fields.map((field) => ({
    path: field.targetPath,
    label: field.label,
    value: result?.confirmed
      ? field.reading
      : mergePagedReading(field.reading, drafts[page.pageId]?.[field.mappingId]),
  })) ?? [], [drafts, page, result?.confirmed]);
  const pagePreviews = useMemo<OcrFieldPreview[]>(() => page?.fields.map((field) => ({
    targetPath: field.targetPath,
    mappingId: field.mappingId,
    artifactUrl: field.cropUrl,
    boundingBox: field.reading.boundingBox,
  })) ?? [], [page]);
  const displayModel = useMemo<ReviewDisplayModel>(() => {
    const model = buildReviewDisplayModel(pageEntries, pagePreviews, result?.reviewStructure ?? undefined);
    return {
      ...model,
      recordSections: model.recordSections
        .map((section) => ({
          ...section,
          rows: section.rows.filter((row) => row.cells.some((cell) => cell.entry)),
        }))
        .filter((section) => section.rows.length > 0),
      parentSections: model.parentSections.filter((section) => section.entries.length > 0),
      independentGroups: model.independentGroups.filter((group) => group.entries.length > 0),
    };
  }, [pageEntries, pagePreviews, result?.reviewStructure]);
  const warningPaths = useMemo(() => new Set(pageEntries
    .filter((entry) => entry.value.status === "uncertain" || Boolean(entry.value.reviewReason))
    .map((entry) => entry.path)), [pageEntries]);
  const choicePaths = useMemo(() => new Set<string>(), []);
  const selectedEntry = selected
    ? pageEntries.find((entry) => entry.value.sourceRegionId === selected.mappingId)
      ?? pageEntries.find((entry) => entry.path === selected.targetPath)
      ?? null
    : null;
  function showPage(index: number) {
    const next = result?.pages[index];
    if (!next) return;
    setPageId(next.pageId);
    setSelectedId(null); setLastSelectedId(null);
  }
  function selectField(mappingId: string) {
    setSelectedId(mappingId);
    setLastSelectedId(mappingId);
  }
  function fieldForEntry(entry: ReadingEntry) {
    return page?.fields.find((field) => field.mappingId === entry.value.sourceRegionId)
      ?? page?.fields.find((field) => field.targetPath === entry.path);
  }
  function selectEntry(entry: ReadingEntry) {
    const field = fieldForEntry(entry);
    if (field) selectField(field.mappingId);
  }
  function selectNextWarning() {
    if (!result || result.confirmed) return;
    const warnings = result.pages.flatMap((item) => item.fields.filter((field) => {
      const value = drafts[item.pageId]?.[field.mappingId] ?? field.reading;
      return value.status === "uncertain" || Boolean(value.reviewReason);
    }).map((field) => ({ pageId: item.pageId, field })));
    if (!warnings.length) return;
    const currentIndex = warnings.findIndex((item) => item.pageId === pageId && item.field.mappingId === selectedId);
    const next = currentIndex >= 0
      ? warnings[(currentIndex + 1) % warnings.length]
      : warnings.find((item) => item.pageId === pageId) ?? warnings[0];
    setPageId(next.pageId);
    selectField(next.field.mappingId);
    window.setTimeout(() => {
      Array.from(document.querySelectorAll<HTMLElement>("[data-review-path]"))
        .find((candidate) => candidate.dataset.reviewPath === next.field.targetPath)
        ?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    }, 0);
  }
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
  function editEntry(entry: ReadingEntry, text: string) {
    const field = fieldForEntry(entry);
    if (field) edit(field.mappingId, fieldEditPatch(text));
  }
  function moveRecordRow(section: ReviewDisplayRecordSection, fromIndex: number, toIndex: number) {
    if (!page || result?.confirmed || lifecycleBusy || lifecycleUncertain || interactionMode !== "reorder") return;
    const fromRow = section.rows[fromIndex];
    const toRow = section.rows[toIndex];
    if (!fromRow || !toRow || Math.abs(fromIndex - toIndex) !== 1) return;
    const pairs = section.columns.map((column) => {
      const firstEntry = fromRow.cells.find((cell) => cell.columnId === column.id)?.entry;
      const secondEntry = toRow.cells.find((cell) => cell.columnId === column.id)?.entry;
      const firstField = firstEntry ? fieldForEntry(firstEntry) : undefined;
      const secondField = secondEntry ? fieldForEntry(secondEntry) : undefined;
      return firstEntry && secondEntry && firstField && secondField
        ? { firstEntry, secondEntry, firstField, secondField }
        : null;
    });
    if (pairs.some((pair) => pair === null)) return;
    for (const pair of pairs) {
      if (!pair) return;
      edit(pair.firstField.mappingId, editableReadingPatch(pair.secondEntry.value));
      edit(pair.secondField.mappingId, editableReadingPatch(pair.firstEntry.value));
    }
    setSelectedId(null);
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
        setPreviewImageFailures({});
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
  return <div className="page-stack review-page">
    <header className="page-header split">
      <div><span className="eyebrow">OCR REVIEW · REVISION {result?.revision ?? "-"}</span><h1>読み取り結果を確認</h1>
        <p>画像と項目を見比べ、原文と整形値を必要に応じて修正してください。</p></div>
      <button className="ghost" onClick={onClose}>← ホームへ</button>
    </header>
    {error && <p className="error-box" role="alert">{error}</p>}
    {(needsRefresh || (!result && error)) && <button className="secondary" disabled={busy}
      onClick={() => void refresh()}>編集内容を保持して最新結果を取得</button>}
    {!result && !error && <p role="status">読み込み中…</p>}
    {result && <>
      <aside className="notice compact"><strong>使用テンプレート</strong><span>
        {result.pages[0]?.result.templateId ?? "-"} · v{result.pages[0]?.result.templateVersion ?? "-"} · 全{result.pages.length}ページ
      </span></aside>
      {!result.confirmed && <section className="form-card document-name-confirmation">
        <div><span className="eyebrow">DOCUMENT NAME</span><h2>書類名を確認</h2>
          <p>この名前を、書類ライブラリと保存するXLSXで共通使用します。</p></div>
        <div className="paged-document-name-field"><label>確定する書類名<input value={documentName} maxLength={180}
            aria-label="保存する書類名" disabled={lifecycleBusy || lifecycleUncertain}
            onChange={(event) => setDocumentName(event.target.value)} placeholder="書類名を入力してください" /></label>
          {result.documentMetadata?.proposedName && <button className="secondary compact"
            disabled={busy || needsRefresh || lifecycleUncertain}
            onClick={() => setDocumentName(result.documentMetadata?.proposedName ?? "")}>設定した項目から書類名を反映</button>}
        </div>
      </section>}
      {result.confirmed && <aside className="notice compact"><strong>確定した書類名</strong><span>{result.documentName}</span></aside>}
      {usage && <aside className="notice compact"><strong>{usage.models || "OpenAI OCR"}</strong><span>
        API {usage.apiCallCount.toLocaleString()}回 · 入力 {usage.inputTokens?.toLocaleString() ?? "-"} / 出力 {usage.outputTokens?.toLocaleString() ?? "-"} tokens
        {` · 合計 ${usage.totalTokens?.toLocaleString() ?? "-"} · API推定 ${usage.estimatedCost == null ? "計算不可" : `$${usage.estimatedCost.toFixed(6)}`}`}
      </span></aside>}
      <aside className="notice compact"><strong>全ページを確認してください</strong><span>
        OCR結果は候補です。各ページの画像と読み取りセルを確認してください。筆画の欠落・隣欄の混入を完全に自動検証した結果ではありません。
      </span></aside>
      {result.confirmed && <p role="status" className="success-box">全ページ確定済み</p>}
      {result.confirmed && dirty && <p className="notice compact">未保存の補正を保持しています。保存するには再編集してください。</p>}
      {page && <div className="ocr-review-layout paged-review-layout"
        style={{ "--preview-ratio": `${previewRatio}%` } as CSSProperties}>
        <aside className="source-pane">
          <div className="pane-title"><span>対応する画像</span><div className="pane-title-controls">
            <small>{`ページ ${page.pageNumber} / ${result.pages.length}`}{selected ? ` · ${selected.label}` : ""}</small>
            <label>プレビュー幅<input type="range" min="30" max="55" value={previewRatio}
              aria-label="プレビュー表示幅" onChange={(event) => setPreviewRatio(Number(event.target.value))} /></label>
          </div></div>
          <div className="source-image paged-review-source-image">
            <button type="button" className="paged-review-page-arrow previous" disabled={pageIndex <= 0}
              aria-label={`前のページ（現在 ${page.pageNumber} / ${result.pages.length}）`}
              onClick={() => showPage(pageIndex - 1)}>‹</button>
            {previewImageFailures[previewImageUrl]
              ? <div className="preview-missing">この画像を表示できません。</div>
              : <img key={previewImageUrl} src={previewImageUrl}
                  alt={selected ? `${selected.label}の読み取り領域` : `ページ ${page.pageNumber}の位置合わせ済み帳票`}
                  onLoad={() => setPreviewImageFailures((old) => ({ ...old, [previewImageUrl]: false }))}
                  onError={() => setPreviewImageFailures((old) => ({ ...old, [previewImageUrl]: true }))} />}
            <button type="button" className="paged-review-page-arrow next"
              disabled={pageIndex < 0 || pageIndex >= result.pages.length - 1}
              aria-label={`次のページ（現在 ${page.pageNumber} / ${result.pages.length}）`}
              onClick={() => showPage(pageIndex + 1)}>›</button>
          </div>
          <div className="source-switch">
            <button type="button" className={!selected ? "active" : ""} onClick={() => setSelectedId(null)}>帳票全体</button>
            <button type="button" className={selected ? "active" : ""} disabled={!lastSelectedId}
              onClick={() => lastSelectedId && setSelectedId(lastSelectedId)}>選択領域</button>
            <button type="button" onClick={() => window.open(handwritingImageUrl, "_blank", "noopener,noreferrer")}>手書きのみ ↗</button>
          </div>
          <div className="review-summary"><strong>{pageWarningCount}</strong><span>要確認項目</span>
            <p>「不確実」は空欄とは異なります。画像を見て判断してください。</p></div>
        </aside>
        <main className="field-pane">
          <ReviewResultsToolbar
            interactionMode={interactionMode}
            editingLocked={result.confirmed || lifecycleBusy || lifecycleUncertain}
            confirmed={result.confirmed}
            warningCount={totalWarningCount}
            cellCount={page.fields.length}
            onInteractionModeChange={(mode) => {
              setInteractionMode(mode);
              if (mode === "reorder") setSelectedId(null);
            }}
            onNextWarning={selectNextWarning}
          />
          <div className="ocr-edit-tables paged-review-results">
            {displayModel.recordSections.map((section) => (
              <GenericRecordReviewTable
                key={section.id}
                section={section}
                selectedPath={selectedEntry?.path ?? null}
                warningPaths={warningPaths}
                choicePaths={choicePaths}
                confirmed={result.confirmed || lifecycleBusy || lifecycleUncertain}
                interactionMode={interactionMode}
                onSelect={selectEntry}
                onEdit={editEntry}
                onMoveRow={moveRecordRow}
              />
            ))}
            {displayModel.parentSections.map((section) => (
              <ReviewTableSection key={section.id} title={section.title}>
                <KeyValueReviewTable
                  entries={section.entries}
                  selectedPath={selectedEntry?.path ?? null}
                  warningPaths={warningPaths}
                  choicePaths={choicePaths}
                  confirmed={result.confirmed || lifecycleBusy || lifecycleUncertain || interactionMode === "reorder"}
                  onSelect={selectEntry}
                  onEdit={editEntry}
                />
              </ReviewTableSection>
            ))}
            {displayModel.independentEntries.length > 0 && (
              <ReviewTableSection title="独立項目">
                <IndependentReviewGroups
                  groups={displayModel.independentGroups}
                  selectedPath={selectedEntry?.path ?? null}
                  warningPaths={warningPaths}
                  choicePaths={choicePaths}
                  confirmed={result.confirmed || lifecycleBusy || lifecycleUncertain || interactionMode === "reorder"}
                  onSelect={selectEntry}
                  onEdit={editEntry}
                />
              </ReviewTableSection>
            )}
          </div>
          {!result.confirmed && interactionMode === "edit" && selected && selectedEntry && (
            <AnchoredReviewPopover path={selectedEntry.path} onClose={() => setSelectedId(null)}>
              <CellStatusPopover
                entry={selectedEntry}
                confirmed={lifecycleBusy || lifecycleUncertain}
                onClose={() => setSelectedId(null)}
                onChange={(patch) => edit(selected.mappingId, pageFieldPatch(patch))}
              />
            </AnchoredReviewPopover>
          )}
          {page.validation.issues.length > 0 && <div className="paged-review-validation">
            {page.validation.issues.map((issue, index) => <p key={index}>{issue.message}</p>)}</div>}
          {!result.confirmed && result.confirmAvailable && <label className="paged-review-acknowledgement">
            <input type="checkbox" checked={Boolean(reviewed[page.pageId])}
              disabled={busy || needsRefresh || Boolean(Object.keys(drafts[page.pageId] ?? {}).length)}
              onChange={(event) => setReviewed((old) => ({ ...old, [page.pageId]: event.target.checked }))} />
            このページの内容を確認しました
          </label>}
        </main>
      </div>}
      <p className="paged-review-library-note">書類一覧への登録は準備中です。</p>
      <div className="decision-bar sticky"><div><strong>{result.confirmed
        ? "確定済みの結果です"
        : !documentName.trim() ? "書類名を入力してください" : `${totalWarningCount}件の要確認項目があります`}</strong>
        <span>{result.confirmed ? "確定結果から入力済みExcelを保存できます。"
          : `ページ ${page?.pageNumber ?? "-"} / ${result.pages.length}を確認し、修正を保存してください。`}</span></div>
        {result.confirmed ? <div className="job-action-buttons">
          <button className="secondary" disabled={busy || downloadBusy || needsRefresh}
            onClick={() => void changeState("reopen")}>再編集する</button>
          {result.outputAvailable && <button className="primary" disabled={busy || downloadBusy || needsRefresh}
            onClick={() => void download()}>{downloadBusy ? "XLSXを取得中…" : "全ページの記入済みXLSXを取得"}</button>}
        </div> : <>
          <button className="secondary" disabled={busy || needsRefresh || !page || !Object.keys(drafts[page.pageId] ?? {}).length}
            onClick={() => void save()}>{busy ? "保存処理中…" : "修正を保存"}</button>
          <button className="primary" disabled={busy || needsRefresh || dirty || !documentName.trim() || !result.confirmAvailable ||
            !result.pages.every((item) => reviewed[item.pageId])} onClick={() => void changeState("confirm")}>全ページを確定</button>
        </>}
      </div>
    </>}
  </div>;
}

function fieldEditPatch(text: string): Partial<PageFieldEdit> {
  return text
    ? { raw: text, normalized: text, status: "readable", alternatives: [], reviewReason: null }
    : { raw: null, normalized: null, status: "blank", alternatives: [], reviewReason: null };
}

function editableReadingPatch(
  reading: Pick<FieldReading, "raw" | "normalized" | "status" | "alternatives" | "reviewReason">,
): Partial<PageFieldEdit> {
  return {
    raw: reading.raw,
    normalized: reading.normalized,
    status: reading.status,
    alternatives: [...reading.alternatives],
    reviewReason: reading.reviewReason,
  };
}

function pageFieldPatch(patch: Partial<FieldReading>): Partial<PageFieldEdit> {
  const editable: Partial<PageFieldEdit> = {};
  if ("raw" in patch) editable.raw = patch.raw;
  if ("normalized" in patch) editable.normalized = patch.normalized;
  if ("status" in patch) editable.status = patch.status;
  if ("alternatives" in patch) editable.alternatives = patch.alternatives;
  if ("reviewReason" in patch) editable.reviewReason = patch.reviewReason;
  return editable;
}

function mergePagedReading(reading: FieldReading, edit?: PageFieldEdit): FieldReading {
  return edit
    ? { ...reading, ...editableReadingPatch(edit) }
    : reading;
}

function summarizeUsage(records: OpenAIUsageRecord[]) {
  if (records.length === 0) return null;
  const allInputKnown = records.every((record) => record.inputTokens !== null);
  const allOutputKnown = records.every((record) => record.outputTokens !== null);
  const allTotalKnown = records.every((record) => record.totalTokens !== null);
  const allCostKnown = records.every((record) => record.estimatedCost !== null);
  return {
    models: Array.from(new Set(records.map((record) => record.modelId).filter(Boolean))).join(" / "),
    apiCallCount: records.reduce((sum, record) => sum + record.apiCallCount, 0),
    inputTokens: allInputKnown ? records.reduce((sum, record) => sum + (record.inputTokens ?? 0), 0) : null,
    outputTokens: allOutputKnown ? records.reduce((sum, record) => sum + (record.outputTokens ?? 0), 0) : null,
    totalTokens: allTotalKnown ? records.reduce((sum, record) => sum + (record.totalTokens ?? 0), 0) : null,
    estimatedCost: allCostKnown ? records.reduce((sum, record) => sum + Number(record.estimatedCost ?? 0), 0) : null,
  };
}
