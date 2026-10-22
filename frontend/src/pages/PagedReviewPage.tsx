import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { FiArrowLeft, FiArrowRight, FiCheck, FiDownload, FiEdit3, FiRefreshCw, FiSave } from "react-icons/fi";
import { api } from "../api";
import { displayName } from "../userMessages";
import {
  AnchoredReviewPopover,
  ReviewPaint,
  ReviewPenSurface,
  useReviewPaint,
  ReviewChecklists,
  ReviewJoins,
  ReviewRenaming,
  checklistOptionsByPath,
  buildReviewDisplayModel,
  reviewDisplayBlocks,
  CellStatusPopover,
  MarkStatusPopover,
  markPopoverIsWide,
  reviewEntryLabel,
  GenericRecordReviewTable,
  IndependentReviewGroups,
  KeyValueReviewTable,
  ReviewResultsToolbar,
  ReviewDraftBoundary,
  ReviewTableSection,
  reviewEntryContext,
  sectionItemPaths,
  type ReadingEntry,
  type ReviewDisplayModel,
  type ReviewDisplayRecordSection,
  type ReviewInteractionMode,
} from "../components/OcrReviewResults";
import type { PageFieldEdit, PagedReviewResult } from "../pagedReview";
import { displayOrders, joinByPath, type ItemJoin } from "../itemJoins";
import { onItemJoinsSaved, openItemJoinEditor } from "../components/TemplateItemJoinEditor";
import { showToast } from "../components/Toast";
import type { FieldReading, OcrFieldPreview } from "../types";
import { Spinner } from "../components/Spinner";

type Drafts = Record<string, Record<string, PageFieldEdit>>;

export function PagedReviewPage({ jobId, onClose, onConfirmed, onNavigationGuardChange, templateNames = {}, closeLabel = "ホームへ" }: {
  jobId: string;
  onClose: () => void;
  /** After all pages are confirmed (the app then shows the document shelf). */
  onConfirmed?: (confirmed: { templateId: string; documentName: string }) => void;
  closeLabel?: string;
  onNavigationGuardChange?: (dirty: boolean) => void;
  templateNames?: Record<string, string>;
}) {
  const [result, setResult] = useState<PagedReviewResult | null>(null);
  const [pageId, setPageId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null);
  const [previewRatio, setPreviewRatio] = useState(50);
  const [interactionMode, setInteractionMode] = useState<ReviewInteractionMode>("edit");
  // Names given to items on this screen (display only; per template).
  const [itemNames, setItemNames] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<Drafts>({});
  const [inlineDirty, setInlineDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [lifecycleUncertain, setLifecycleUncertain] = useState(false);
  const [reviewed, setReviewed] = useState<Record<string, boolean>>({});
  const [documentName, setDocumentName] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewImageFailures, setPreviewImageFailures] = useState<Record<string, boolean>>({});
  const generation = useRef(0);
  const inFlight = useRef(false);
  const downloading = useRef(false);
  const dirty = Object.values(drafts).some((fields) => Object.keys(fields).length > 0);
  // Once the pages are shown, a failure is a small corner notification instead of a box above them.
  const shown = Boolean(result);
  useEffect(() => {
    if (!error || !shown) return;
    showToast({ tone: "error", quiet: true, title: error });
    setError(null);
  }, [error, shown]);
  const unsaved = dirty || Boolean(result && !result.confirmed && documentName !== (result.documentName ?? ""));
  useEffect(() => {
    const current = ++generation.current;
    setResult(null); setDrafts({}); setPageId(null); setSelectedId(null); setLastSelectedId(null);
    setError(null); setNeedsRefresh(false); setBusy(false); inFlight.current = false;
    setDownloadBusy(false); downloading.current = false;
    setLifecycleBusy(false); setLifecycleUncertain(false); setReviewed({}); setDocumentName("");
    setPreviewImageFailures({}); setInteractionMode("edit");
    void api.pageReview(jobId).then((next) => {
      if (generation.current === current) {
        setResult(next); setPageId(next.pages[0]?.pageId ?? null);
        // Starts empty; the name is typed (or taken from the set items with the button below).
        setDocumentName(next.documentName ?? "");
      }
    }).catch((reason) => {
      if (generation.current === current) setError(reason instanceof Error ? reason.message : "結果を取得できませんでした。");
    });
    return () => { generation.current += 1; };
  }, [jobId]);
  useEffect(() => {
    onNavigationGuardChange?.(unsaved || inlineDirty || busy);
    const guard = (event: BeforeUnloadEvent) => {
      if (unsaved || inlineDirty || busy) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", guard);
    return () => { onNavigationGuardChange?.(false); window.removeEventListener("beforeunload", guard); };
  }, [unsaved, inlineDirty, busy, onNavigationGuardChange]);

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
  const pageEntries = useMemo<ReadingEntry[]>(() => page?.fields.map((field) => ({
    path: field.targetPath,
    label: displayName(field.label),
    value: result?.confirmed
      ? field.reading
      : mergePagedReading(field.reading, drafts[page.pageId]?.[field.mappingId]),
  })) ?? [], [drafts, page, result?.confirmed]);
  const pagePreviews = useMemo<OcrFieldPreview[]>(() => page?.fields.map((field) => ({
    mappingType: field.mappingType,
    markRecognitionMode: field.markRecognitionMode,
    markSelectionMode: field.markSelectionMode,
    markOptions: field.markOptions,
    targetPath: field.targetPath,
    mappingId: field.mappingId,
    artifactUrl: field.cropUrl,
    boundingBox: field.reading.boundingBox,
  })) ?? [], [page]);
  const displayModel = useMemo<ReviewDisplayModel>(() => {
    const model = buildReviewDisplayModel(pageEntries, pagePreviews, result?.reviewStructure ?? undefined, itemNames);
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
  }, [pageEntries, pagePreviews, result?.reviewStructure, itemNames]);
  const templateId = result?.pages[0]?.result.templateId ?? null;
  useEffect(() => {
    if (!templateId) return;
    let active = true;
    void api.displayNames(templateId).then((value) => { if (active) setItemNames(value.names); }).catch(() => undefined);
    return () => { active = false; };
  }, [templateId]);
  const [itemJoins, setItemJoins] = useState<ItemJoin[]>([]);
  useEffect(() => {
    if (!templateId) return;
    let active = true;
    void api.itemJoins(templateId).then((value) => { if (active) setItemJoins(value.items); }).catch(() => undefined);
    const stop = onItemJoinsSaved((savedTemplateId, items) => { if (active && savedTemplateId === templateId) setItemJoins(items); });
    return () => { active = false; stop(); };
  }, [templateId]);
  // Only this page's tables and headings; blocks keep the form order of the whole document.
  const displayBlocks = useMemo(() => reviewDisplayBlocks(displayModel, result?.reviewStructure, itemJoins), [displayModel, result?.reviewStructure, itemJoins]);
  const joinContext = useMemo(() => ({
    byPath: joinByPath(itemJoins),
        orders: displayOrders(itemJoins),
    configure: templateId ? (paths: string[], anchor: HTMLElement) => openItemJoinEditor(templateId, null, { paths, anchor }) : undefined,
  }), [itemJoins, templateId]);
  const renameItem = async (entry: ReadingEntry, name: string) => {
    if (!templateId) return;
    try { setItemNames((await api.renameItem(templateId, entry.path, name || null)).names); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "項目名を変更できませんでした。"); }
  };
  // The check pen paints traced cells green; it never changes a reading or its 要確認.
  const [pen, setPen] = useState(false);
  // Ending the check pen goes back to 記入欄 (the segments show nothing chosen while it is on).
  const changePen = (on: boolean) => { setPen(on); if (!on) { setInteractionMode("edit"); setSelectedId(null); } };
  const { painted, paint } = useReviewPaint(page ? `${jobId}.${page.pageId}` : null);
  const warningPaths = useMemo(() => new Set(pageEntries
    .filter((entry) => entry.value.status === "uncertain" || Boolean(entry.value.reviewReason))
    .map((entry) => entry.path)), [pageEntries]);
  const choicePaths = useMemo(() => new Set(page?.fields.filter(field => field.mappingType === "mark").map(field => field.targetPath)), [page]);
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
      if (action === "confirm" && next.confirmed && onConfirmed) onConfirmed({ templateId: next.pages[0]?.result.templateId ?? "", documentName: next.documentName ?? documentName });
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
  return <ReviewDraftBoundary onDirtyChange={setInlineDirty}><div className="page-stack review-page">
    <header className="page-header split has-back">
      <button className="ghost page-back" onClick={onClose}><FiArrowLeft aria-hidden="true" />{closeLabel}</button>
      <div><h1>読み取り結果を確認</h1>
        <p>全ページの画像と読み取り結果を確認・修正してから確定します。確定後にXLSXを取得できます。</p></div>
    </header>
    {error && <p className="error-box" role="alert">{error}</p>}
    {(needsRefresh || (!result && error)) && <button className="secondary" disabled={busy}
      onClick={() => void refresh()}><FiRefreshCw aria-hidden="true" />編集内容を保持して最新結果を取得</button>}
    {!result && !error && <p role="status"><Spinner />読み込み中…</p>}
    {result && <>
      <aside className="notice compact"><strong>使用テンプレート</strong><span>
        {templateNames[result.pages[0]?.result.templateId ?? ""] ?? "読み取り時のテンプレート"} · 全{result.pages.length}ページ
      </span></aside>
      {result.confirmed && <aside className="notice compact"><strong>確定した書類名</strong><span>{result.documentName}</span></aside>}

      <aside className="notice compact"><strong>全ページを確認してください</strong><span>
        OCR結果は候補です。各ページの画像と読み取りセルを確認してください。筆画の欠落・隣欄の混入を完全に自動検証した結果ではありません。
      </span></aside>
      {result.confirmed && <p role="status" className="success-box">全ページ確定済み</p>}
      {result.confirmed && dirty && <p className="notice compact">未保存の補正を保持しています。保存するには再編集してください。</p>}
      {page && <div className="ocr-review-layout paged-review-layout"
        style={{ "--preview-ratio": `${previewRatio}%` } as CSSProperties}>
        <aside className="source-pane">
          <div className="pane-title"><span>対応する画像</span><div className="pane-title-controls">
            <div className="paged-review-navigation">
              <button type="button" className="secondary compact" disabled={pageIndex <= 0}
                aria-label={`前のページ（現在 ${page.pageNumber} / ${result.pages.length}）`}
                onClick={() => showPage(pageIndex - 1)}><FiArrowLeft aria-hidden="true" /></button>
            <small>{`ページ ${page.pageNumber} / ${result.pages.length}`} · {reviewEntryContext(displayModel, selectedEntry)}</small>
              <button type="button" className="secondary compact"
                disabled={pageIndex < 0 || pageIndex >= result.pages.length - 1}
                aria-label={`次のページ（現在 ${page.pageNumber} / ${result.pages.length}）`}
                onClick={() => showPage(pageIndex + 1)}><FiArrowRight aria-hidden="true" /></button>
            </div>
            <label>プレビュー幅<input type="range" min="30" max="55" value={previewRatio}
              aria-label="プレビュー表示幅" onChange={(event) => setPreviewRatio(Number(event.target.value))} /></label>
          </div></div>
          <div className="source-image paged-review-source-image">
            {previewImageFailures[previewImageUrl]
              ? <div className="preview-missing">この画像を表示できません。</div>
              : <img key={previewImageUrl} src={previewImageUrl}
                  alt={selected ? `${selected.label}の読み取り領域` : `ページ ${page.pageNumber}の位置合わせ済み帳票`}
                  onLoad={() => setPreviewImageFailures((old) => ({ ...old, [previewImageUrl]: false }))}
                  onError={() => setPreviewImageFailures((old) => ({ ...old, [previewImageUrl]: true }))} />}
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
            penActive={pen && !result.confirmed}
            onPenChange={changePen}
          />
          <ReviewChecklists.Provider value={checklistOptionsByPath(pagePreviews)}><ReviewRenaming.Provider value={interactionMode === "rename" ? renameItem : null}><ReviewJoins.Provider value={joinContext}><ReviewPaint.Provider value={result.confirmed ? new Set<string>() : painted}><ReviewPenSurface active={pen && !result.confirmed} painted={painted} onPaint={paint} onHover={(path) => { const entry = pageEntries.find((item) => item.path === path); if (entry) selectEntry(entry); }} onExit={() => changePen(false)}><div className="ocr-edit-tables paged-review-results">
            {displayBlocks.map((block) => block.kind === "record" ? (
              <GenericRecordReviewTable
                key={block.key}
                section={block.section}
                after={block.attached && (
                  <KeyValueReviewTable
                    entries={block.attached.entries}
                    selectedPath={selectedEntry?.path ?? null}
                    warningPaths={warningPaths}
                    choicePaths={choicePaths}
                    confirmed={result.confirmed || lifecycleBusy || lifecycleUncertain || interactionMode !== "edit"}
                    onSelect={selectEntry}
                    onEdit={editEntry}
                  />
                )}
                selectedPath={selectedEntry?.path ?? null}
                warningPaths={warningPaths}
                choicePaths={choicePaths}
                confirmed={result.confirmed || lifecycleBusy || lifecycleUncertain}
                interactionMode={interactionMode}
                onSelect={selectEntry}
                onEdit={editEntry}
                onMoveRow={moveRecordRow}
              />
            ) : block.kind === "parent" ? (
              <ReviewTableSection key={block.key} title={block.section.title} joinPaths={sectionItemPaths(block.section.entries)}>
                <KeyValueReviewTable
                  entries={block.section.entries}
                  selectedPath={selectedEntry?.path ?? null}
                  warningPaths={warningPaths}
                  choicePaths={choicePaths}
                  confirmed={result.confirmed || lifecycleBusy || lifecycleUncertain || interactionMode !== "edit"}
                  onSelect={selectEntry}
                  onEdit={editEntry}
                />
              </ReviewTableSection>
            ) : (
              <ReviewTableSection key={block.key} title="独立項目">
                <IndependentReviewGroups
                  groups={block.groups}
                  selectedPath={selectedEntry?.path ?? null}
                  warningPaths={warningPaths}
                  choicePaths={choicePaths}
                  confirmed={result.confirmed || lifecycleBusy || lifecycleUncertain || interactionMode !== "edit"}
                  onSelect={selectEntry}
                  onEdit={editEntry}
                />
              </ReviewTableSection>
            ))}
          </div></ReviewPenSurface></ReviewPaint.Provider></ReviewJoins.Provider></ReviewRenaming.Provider></ReviewChecklists.Provider>
          {!result.confirmed && !pen && interactionMode === "edit" && selected && selectedEntry && selected.markRecognitionMode !== "checklist" && (() => {
            const markPreview = selected.mappingType === "mark"
              ? pagePreviews.find(preview => preview.mappingId === selected.mappingId) : undefined;
            return (
              <AnchoredReviewPopover path={selectedEntry.path} wide={markPopoverIsWide(markPreview)} onClose={() => setSelectedId(null)}>
                {markPreview
                  ? <MarkStatusPopover
                      entry={{ ...selectedEntry, label: reviewEntryLabel(displayModel, selectedEntry) }}
                      preview={markPreview}
                      confirmed={busy || lifecycleBusy || lifecycleUncertain}
                      onChange={(patch) => edit(selected.mappingId, pageFieldPatch(patch))}
                    />
                  : <CellStatusPopover
                      entry={{ ...selectedEntry, label: reviewEntryLabel(displayModel, selectedEntry) }}
                      confirmed={lifecycleBusy || lifecycleUncertain}
                      onChange={(patch) => edit(selected.mappingId, pageFieldPatch(patch))}
                    />}
              </AnchoredReviewPopover>
            );
          })()}
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
      {!result.confirmed && <section className="form-card document-name-confirmation">
        <div><span className="eyebrow">DOCUMENT NAME</span><h2>書類名を確認</h2>
          <p>この名前を、保存するXLSXのファイル名に使用します。</p></div>
        <div className="paged-document-name-field"><label>確定する書類名<input value={documentName} maxLength={180}
            aria-label="保存する書類名" disabled={lifecycleBusy || lifecycleUncertain}
            onChange={(event) => setDocumentName(event.target.value)} placeholder="書類名を入力してください" /></label>
          {result.documentMetadata?.proposedName && <button className="secondary compact"
            disabled={busy || needsRefresh || lifecycleUncertain}
            onClick={() => setDocumentName(result.documentMetadata?.proposedName ?? "")}>設定した項目から書類名を反映</button>}
        </div>
      </section>}
      <div className="decision-bar sticky"><div><strong>{result.confirmed
        ? "確定済みの結果です"
        : !documentName.trim() ? "書類名を入力してください" : `${totalWarningCount}件の要確認項目があります`}</strong>
        <span>{result.confirmed ? "確定結果から入力済みExcelを保存できます。"
          : `ページ ${page?.pageNumber ?? "-"} / ${result.pages.length}を確認し、修正を保存してください。`}</span></div>
        {result.confirmed ? <div className="job-action-buttons">
          <button className="secondary" disabled={busy || downloadBusy || needsRefresh}
            onClick={() => void changeState("reopen")}><FiEdit3 aria-hidden="true" />再編集する</button>
          {result.outputAvailable && <button className="primary" disabled={busy || downloadBusy || needsRefresh}
            onClick={() => void download()}><FiDownload aria-hidden="true" />{downloadBusy ? <><Spinner />XLSXを取得中…</> : "全ページの記入済みXLSXを取得"}</button>}
        </div> : <>
          <button className="secondary" disabled={busy || needsRefresh || !page || !Object.keys(drafts[page.pageId] ?? {}).length}
            onClick={() => void save()}><FiSave aria-hidden="true" />{busy ? <><Spinner />保存処理中…</> : "修正を保存"}</button>
          <button className="primary" disabled={busy || needsRefresh || dirty || !documentName.trim() || !result.confirmAvailable ||
            !result.pages.every((item) => reviewed[item.pageId])} onClick={() => void changeState("confirm")}><FiCheck aria-hidden="true" />全ページを確定</button>
        </>}
      </div>
    </>}
  </div></ReviewDraftBoundary>;
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
