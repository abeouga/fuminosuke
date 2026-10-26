import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { displayOrders, joinByPath, joinedText, sortBlocks, sortByOrder, type ItemJoin } from "../itemJoins";
import { onItemJoinsSaved } from "../components/TemplateItemJoinEditor";
import { FiArrowLeft, FiCheck, FiCheckSquare, FiDownload, FiEdit3, FiFileText, FiFolder, FiMinus, FiPlus, FiRefreshCw, FiSearch, FiTrash2, FiX } from "react-icons/fi";
import { showToast } from "../components/Toast";
import { api } from "../api";
import { ExcelExportActions } from "../components/ExcelExportActions";
import { EditingError } from "../editing/client";
import {
  structuredClient,
  type DocumentPage,
  type DocumentSummary,
  type HeadingCell,
  type StructuredDocument,
  type StructuredReading,
  type StructuredTable,
} from "../structured/client";
import { ImageSpinner, Spinner } from "../components/Spinner";
import { choiceText } from "../components/OcrReviewResults";
import { leftReviewsSettled } from "../reviewLeave";

/** What the shelf shows; kept by the app so a review opened from here can come back to it. */
export type ShelfSelection = { templateId: string | null; jobId: string | null; query: string };

type Folder = { templateId: string; name: string; documents: DocumentSummary[] };
type Detail = { jobId: string; document?: StructuredDocument; error?: string };

const dayFormat = new Intl.DateTimeFormat("ja-JP", { year: "numeric", month: "2-digit", day: "2-digit" });
const timeFormat = new Intl.DateTimeFormat("ja-JP", { dateStyle: "medium", timeStyle: "short" });
const ZOOMS = [1, 1.5, 2, 3];

const fold = (text: string) => text.normalize("NFKC").toLowerCase();
const identityValues = (doc: DocumentSummary) => doc.identity.filter((item) => item.value);
const identityLine = (doc: DocumentSummary) => identityValues(doc).map((item) => `${item.label} ${item.value}`).join(" ・ ");

/** File name of a document: its confirmed name, else its identity values. */
export function documentTitle(doc: DocumentSummary): string {
  return doc.documentName?.trim() || identityValues(doc).map((item) => item.value).join("・") || "（名称なし）";
}

function errorText(reason: unknown, fallback: string): string {
  if (reason instanceof EditingError && reason.code === "STRUCTURED_NOT_CONFIRMED")
    return "この書類は再編集中です。確認画面で確定すると、ライブラリに表示されます。";
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

export function ShelfPage({ templateNames, selection, onSelectionChange, onOpenReview }: {
  templateNames: Record<string, string>;
  selection: ShelfSelection;
  onSelectionChange: (next: ShelfSelection) => void;
  onOpenReview: (jobId: string, field: string | null) => void;
}) {
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true); setListError(null);
    try {
      // A re-edit closed by the side menu is put back to its confirmed version first.
      await leftReviewsSettled();
      setDocuments((await structuredClient.allDocuments()).items);
    }
    catch (reason) { setListError(errorText(reason, "書類の一覧を取得できませんでした。")); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const folderName = useCallback((doc: DocumentSummary) =>
    templateNames[doc.templateId] || doc.templateName || "（名称未設定）", [templateNames]);
  // Folders follow the list order (newest confirmation first), so a recently used template is on top.
  const folders = useMemo(() => {
    const byTemplate = new Map<string, Folder>();
    for (const doc of documents ?? []) {
      let folder = byTemplate.get(doc.templateId);
      if (!folder) {
        folder = { templateId: doc.templateId, name: folderName(doc), documents: [] };
        byTemplate.set(doc.templateId, folder);
      }
      folder.documents.push(doc);
    }
    return [...byTemplate.values()];
  }, [documents, folderName]);

  const query = selection.query.trim();
  const selected = documents?.find((doc) => doc.jobId === selection.jobId) ?? null;
  // No template chosen: the shelf shows its templates as cards (like the reading screen).
  const folder = folders.find((item) => item.templateId === (selected?.templateId ?? selection.templateId)) ?? null;
  const listed = useMemo(() => {
    if (!query) return folder?.documents ?? [];
    const needle = fold(query);
    return (documents ?? []).filter((doc) =>
      [documentTitle(doc), folderName(doc), ...identityValues(doc).map((item) => item.value ?? "")]
        .some((text) => fold(text).includes(needle)));
  }, [documents, folder, folderName, query]);

  const cache = useRef(new Map<string, StructuredDocument>());
  const [detail, setDetail] = useState<Detail | null>(null);
  const [retry, setRetry] = useState(0);
  const selectedId = selected?.jobId ?? null;
  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    const cached = cache.current.get(selectedId);
    if (cached) { setDetail({ jobId: selectedId, document: cached }); return; }
    let stopped = false;
    setDetail({ jobId: selectedId });
    structuredClient.document(selectedId).then((document) => {
      if (stopped) return;
      cache.current.set(selectedId, document);
      setDetail({ jobId: selectedId, document });
    }).catch((reason) => {
      if (!stopped) setDetail({ jobId: selectedId, error: errorText(reason, "書類の内容を取得できませんでした。") });
    });
    return () => { stopped = true; };
  }, [selectedId, retry]);

  // Choosing documents to delete: the cards get check marks and an action bar appears.
  const [choosing, setChoosing] = useState(false);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [askDelete, setAskDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const listKey = `${folder?.templateId ?? ""}|${query}`;
  useEffect(() => { setChoosing(false); setChosen(new Set()); }, [listKey]);
  const toggleChosen = (jobId: string) => setChosen((current) => {
    const next = new Set(current);
    if (next.has(jobId)) next.delete(jobId); else next.add(jobId);
    return next;
  });
  const chosenDocs = listed.filter((doc) => chosen.has(doc.jobId));
  const olderVersions = chosenDocs.some((doc) => doc.seriesVersion > 1);
  async function deleteChosen() {
    setDeleting(true);
    const failed: string[] = [];
    let deleted = 0;
    for (const doc of chosenDocs) {
      try {
        await api.deleteJob(doc.jobId, true);
        // The shelf shows only a document's newest version; the earlier ones go with it.
        for (let left = doc.seriesId ? 50 : 0; left > 0; left--) {
          const earlier = (await structuredClient.allDocuments()).items.find((item) => item.seriesId === doc.seriesId);
          if (!earlier) break;
          await api.deleteJob(earlier.jobId, true);
        }
        deleted++;
      } catch (reason) {
        failed.push(`${documentTitle(doc)}：${errorText(reason, "削除できませんでした。")}`);
      }
    }
    cache.current.clear();
    setAskDelete(false); setDeleting(false); setChosen(new Set()); setChoosing(false);
    await load();
    if (deleted) showToast({ tone: "success", quiet: true, title: `${deleted}件の書類を削除しました` });
    if (failed.length) showToast({ tone: "error", title: "削除できなかった書類があります", message: failed.join(" / "), duration: 12000 });
  }

  const mainRef = useRef<HTMLElement>(null);
  const show = (next: ShelfSelection) => {
    onSelectionChange(next);
    window.requestAnimationFrame(() => {
      const top = mainRef.current?.getBoundingClientRect().top ?? 0;
      if (top < 0 || top > window.innerHeight * 0.6) mainRef.current?.scrollIntoView({ block: "start" });
    });
  };
  const listing = Boolean(query || folder);
  const heading = listing && !selected && Boolean(documents?.length);

  return <div className="page-stack shelf-page">
    <main ref={mainRef} className="shelf-main">
      <div className="shelf-header-actions">
        {!listing && !selected && <div className="shelf-pick-head">
          <span className="ocr-template-heading-icon shelf-pick-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none">
              <path d="M3.5 6.5a1 1 0 0 1 1-1h4.75l2 2h8.25a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z" />
              <path d="M3.5 10h17" />
            </svg>
          </span>
          <h2>閲覧する書類のテンプレートを選ぶ</h2>
        </div>}
        {selected && <div className="shelf-main-head">
          <button type="button" className="ghost compact shelf-up" onClick={() => show({ ...selection, jobId: null })}>
            <FiArrowLeft aria-hidden="true" />{query ? "検索結果" : folderName(selected)}</button>
        </div>}
        {heading && <div className="shelf-main-head">
          {!query && <button type="button" className="ghost compact shelf-up" onClick={() => show({ templateId: null, jobId: null, query: "" })}>
            <FiArrowLeft aria-hidden="true" />一覧</button>}
          <h2>{query ? `「${query}」の検索結果` : folder?.name}</h2>
          <span>{listed.length}件</span>
        </div>}
        {heading && listed.length > 0 && <div className="shelf-choose-actions">
          {choosing
            ? <>
              <span className="shelf-chosen-count">{chosen.size}件を選択中</span>
              <button type="button" className="ghost compact" onClick={() => setChosen(chosen.size === listed.length ? new Set() : new Set(listed.map((doc) => doc.jobId)))}>
                {chosen.size === listed.length ? "選択を解除" : "すべて選択"}</button>
              <button type="button" className="danger-button compact" disabled={chosen.size === 0} onClick={() => setAskDelete(true)}>
                <FiTrash2 aria-hidden="true" />削除</button>
              <button type="button" className="secondary compact" onClick={() => { setChoosing(false); setChosen(new Set()); }}>キャンセル</button>
            </>
            : <button type="button" className="secondary compact shelf-choose" onClick={() => setChoosing(true)}><FiCheckSquare aria-hidden="true" />選択</button>}
        </div>}
        <label className="shelf-search">
          <FiSearch aria-hidden="true" />
          <input type="search" value={selection.query} placeholder="書類名・識別値で探す" aria-label="書類を探す"
            onChange={(event) => onSelectionChange({ ...selection, jobId: null, query: event.target.value })} />
          {selection.query && <button type="button" className="shelf-search-clear" aria-label="検索を消す"
            onClick={() => onSelectionChange({ ...selection, query: "" })}><FiX aria-hidden="true" /></button>}
        </label>
        <button type="button" className="secondary compact shelf-refresh" disabled={loading} aria-label={loading ? "更新中" : "更新"} title="更新"
          onClick={() => { cache.current.clear(); void load(); setRetry((n) => n + 1); }}>
          {loading ? <Spinner /> : <FiRefreshCw aria-hidden="true" />}
        </button>
      </div>
    {listError && <p className="error-box" role="alert">{listError}</p>}
    {documents === null && !listError && <p className="shelf-empty" role="status">書類を読み込んでいます。</p>}
    {documents?.length === 0 && <section className="shelf-empty-card">
      <FiFolder aria-hidden="true" />
      <div><strong>確定済みの書類はまだありません</strong><p>読み取り結果を確認して確定すると、ここに並びます。</p></div>
    </section>}
    {documents && documents.length > 0 && <>
      {selected
        ? <DocumentView key={selected.jobId} summary={selected} folderName={folderName(selected)}
          detail={detail?.jobId === selected.jobId ? detail : { jobId: selected.jobId }}
          onRetry={() => setRetry((n) => n + 1)} onOpenReview={onOpenReview} />
        : !listing
          ? <section aria-label="書類の種類">
            <ul className="shelf-grid">
              {folders.map((item) => <li key={item.templateId}>
                <FolderCard folder={item} onOpen={() => show({ templateId: item.templateId, jobId: null, query: "" })} />
              </li>)}
            </ul>
          </section>
          : <section aria-label="書類">
            {listed.length === 0 && <p className="shelf-empty">一致する書類はありません。</p>}
            <ul className={`shelf-list${query ? "" : " is-rising"}`}>
              {listed.map((doc, index) => <li key={doc.jobId} style={{ "--rise-index": Math.min(index, 12) } as CSSProperties}>
                <DocumentCard doc={doc} caption={query ? folderName(doc) : identityLine(doc)}
                  choosing={choosing} chosen={chosen.has(doc.jobId)}
                  onOpen={() => choosing ? toggleChosen(doc.jobId) : show({ ...selection, templateId: doc.templateId, jobId: doc.jobId })} />
              </li>)}
            </ul>
          </section>}
    </>}
    </main>
    {askDelete && <div className="modal-backdrop"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="shelf-delete-title">
      <h2 id="shelf-delete-title">{chosenDocs.length}件の書類を削除しますか？</h2>
      <p>読み取り結果・元の画像・保存したXLSXも削除され、元に戻せません。{olderVersions ? "前の版がある書類は、前の版も削除します。" : ""}</p>
      <ul className="shelf-delete-list">{chosenDocs.slice(0, 8).map((doc) => <li key={doc.jobId}>{documentTitle(doc)}</li>)}
        {chosenDocs.length > 8 && <li>ほか{chosenDocs.length - 8}件</li>}</ul>
      <div className="page-actions">
        <button type="button" className="secondary" disabled={deleting} onClick={() => setAskDelete(false)}>やめる</button>
        <button type="button" className="danger-button" disabled={deleting} onClick={() => void deleteChosen()}><FiTrash2 aria-hidden="true" />{deleting ? <><Spinner />削除しています…</> : "削除する"}</button>
      </div>
    </section></div>}
  </div>;
}

/** A template on the shelf: its newest document as a small stack of paper, the name and the count. */
function FolderCard({ folder, onOpen }: { folder: Folder; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const newest = folder.documents[0];
  return <button type="button" className="shelf-card" onClick={onOpen}>
    <span className="shelf-card-thumb">
      {newest?.thumbnailUrl && !failed
        ? <>{!loaded && <ImageSpinner />}<img src={newest.thumbnailUrl} alt="" loading="lazy" decoding="async" draggable={false} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} /></>
        : <FiFileText aria-hidden="true" />}
    </span>
    <span className="shelf-card-body">
      <strong>{folder.name}</strong>
      <small>{folder.documents.length}件</small>
      {newest && <span className="shelf-card-meta">
        <time dateTime={newest.confirmedAt}>最終 {dayFormat.format(new Date(newest.confirmedAt))}</time>
      </span>}
    </span>
  </button>;
}

/** A document inside a template: a wide row (small thumbnail, name, date), so the list reads differently from the template cards. */
function DocumentCard({ doc, caption, choosing, chosen, onOpen }: { doc: DocumentSummary; caption: string; choosing: boolean; chosen: boolean; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  return <button type="button" className={`shelf-row${choosing ? " choosing" : ""}${chosen ? " chosen" : ""}`} onClick={onOpen}
    {...(choosing ? { role: "checkbox", "aria-checked": chosen } : {})}>
    {choosing && <span className="shelf-row-check" aria-hidden="true">{chosen && <FiCheck />}</span>}
    <span className="shelf-row-thumb">
      {doc.thumbnailUrl && !failed
        ? <>{!loaded && <ImageSpinner />}<img src={doc.thumbnailUrl} alt="" loading="lazy" decoding="async" draggable={false} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} /></>
        : <FiFileText aria-hidden="true" />}
    </span>
    <span className="shelf-row-body">
      <strong>{documentTitle(doc)}</strong>
      {caption && <small>{caption}</small>}
    </span>
    <span className="shelf-row-meta">
      {doc.kind === "paged" && <span className="shelf-version">複数ページ</span>}
      {doc.seriesVersion > 1 && <span className="shelf-version">版{doc.seriesVersion}</span>}
      <time dateTime={doc.confirmedAt}>{dayFormat.format(new Date(doc.confirmedAt))} 確定</time>
    </span>
  </button>;
}

function readingPlace(reading: StructuredReading, tableTitle: string | null) {
  return reading.rowNumber ? `${tableTitle ?? reading.section ?? ""} ${reading.rowNumber}行目・${reading.label}` : reading.label;
}

type ShelfLine = StructuredReading | { join: ItemJoin; readings: StructuredReading[] };
/** A table, or a heading (or items under no heading) with its lines. */
type ItemGroup = { name: string; fieldIds: string[] };
type ShelfBlock = { key: string; table: StructuredTable; lines?: ShelfLine[]; groups?: ItemGroup[] } | { key: string; name: string; lines: ShelfLine[]; groups?: ItemGroup[] };
const firstReading = (line: ShelfLine) => "join" in line ? line.readings[0] : line;
/** A selection box's name without its printed choices: 「区分（高・特・繁殖）」 → 「区分」. */
function readingLabel(reading: StructuredReading): string {
  if (!reading.choice) return reading.label;
  const bare = reading.label.replace(/\s*[（(][^（）()]*[・、,，/／][^（）()]*[）)]\s*$/, "").trim();
  return bare || reading.label;
}
/** 「登録No.の区分（…）」 under 「登録No.」 reads 「区分（…）」; a bare bracket keeps the whole name. */
function subLabel(label: string, group: string): string {
  const rest = label.startsWith(group) ? label.slice(group.length).replace(/^[の\s・]+/, "") : "";
  return rest && !/^[（(]/.test(rest) ? rest : label;
}

function DocumentView({ summary, folderName, detail, onRetry, onOpenReview }: {
  summary: DocumentSummary;
  folderName: string;
  detail: Detail;
  onRetry: () => void;
  onOpenReview: (jobId: string, field: string | null) => void;
}) {
  const [picked, setPicked] = useState<{ id: string; fromImage: boolean; heading?: boolean } | null>(null);
  // A table row picked by its number: the whole row is outlined on the image and shown enlarged below it.
  const [pickedRow, setPickedRow] = useState<{ tableId: string; rowId: string } | null>(null);
  const pick = (next: { id: string; fromImage: boolean; heading?: boolean }) => { setPickedRow(null); setPicked(next); };
  const document = detail.document;
  const byId = useMemo(() => new Map(document?.fields.map((reading) => [reading.fieldId, reading]) ?? []), [document]);
  const tableTitles = useMemo(() => new Map(document?.tables.map((table) => [table.id, table.title]) ?? []), [document]);
  // Fields outside tables, grouped by their section in document order.
  const sections = useMemo(() => {
    const groups = new Map<string, StructuredReading[]>();
    for (const reading of document?.fields ?? []) {
      if (reading.tableId !== null) continue;
      const name = reading.section ?? "基本項目";
      groups.set(name, [...(groups.get(name) ?? []), reading]);
    }
    return [...groups];
  }, [document]);
  const [joins, setJoins] = useState<ItemJoin[]>([]);
  useEffect(() => {
    let active = true;
    void api.itemJoins(summary.templateId).then((value) => { if (active) setJoins(value.items); }).catch(() => undefined);
    const stop = onItemJoinsSaved((templateId, items) => { if (active && templateId === summary.templateId) setJoins(items); });
    return () => { active = false; stop(); };
  }, [summary.templateId]);
  // Items joined into one value (「調査場所」) are one line at their first item.
  const linesOf = useCallback((readings: StructuredReading[]) => {
    const byPath = joinByPath(joins);
    const placed = new Set<ItemJoin>();
    const lines: ShelfLine[] = [];
    for (const reading of readings) {
      const join = byPath.get(reading.targetPath);
      const members = join ? join.members.flatMap((member) => readings.filter((item) => item.targetPath === member.targetPath)) : [];
      if (!join || members.length < 2) { lines.push(reading); continue; }
      if (placed.has(join)) continue;
      placed.add(join);
      lines.push({ join, readings: members });
    }
    return sortByOrder(lines, (line) => "join" in line ? line.readings[0].targetPath : line.targetPath, displayOrders(joins));
  }, [joins]);
  // Blocks from the top of the form (or the saved block order); older servers: fields, then tables.
  const shownBlocks = useMemo((): ShelfBlock[] => {
    const tables = document?.tables ?? [];
    if (!document?.blocks?.length) return [
      ...sections.map(([name, readings]) => ({ key: `fields:${name}`, name, lines: linesOf(readings) })),
      ...tables.map((table) => ({ key: `table:${table.id}`, table })),
    ];
    const result: ShelfBlock[] = [];
    const shown = new Set<string>();
    // Readings outside tables in the block's order (an item name's items kept together).
    const inBlockOrder = (ids: readonly string[]) => ids
      .map((id) => document.fields.find((reading) => reading.fieldId === id && reading.tableId === null))
      .filter((reading): reading is StructuredReading => reading !== undefined);
    for (const block of sortBlocks(document.blocks, joins)) {
      if (block.kind === "record") {
        const table = tables.find((item) => item.id === block.id);
        // The heading's items outside the table's rows (「導入年月日・金額」) go with the table.
        const attached = inBlockOrder(block.attachedFieldIds ?? []);
        if (table) { result.push({ key: `table:${table.id}`, table, lines: attached.length ? linesOf(attached) : undefined, groups: block.itemGroups }); shown.add(table.id); }
        continue;
      }
      const readings = inBlockOrder(block.fieldIds);
      if (!readings.length) continue;
      const name = block.kind === "parent" ? readings[0].section ?? "項目" : "基本項目";
      result.push({ key: `fields:${block.kind}:${block.id ?? block.paths[0] ?? result.length}`, name, lines: linesOf(readings), groups: block.itemGroups });
    }
    for (const table of tables) if (!shown.has(table.id)) result.push({ key: `table:${table.id}`, table });
    return result;
  }, [document, joins, linesOf, sections]);
  const current = picked && !picked.heading && !pickedRow ? byId.get(picked.id) ?? null : null;
  const rowView = useMemo(() => {
    if (!pickedRow || !document) return null;
    const table = document.tables.find((item) => item.id === pickedRow.tableId);
    const row = table?.rows.find((item) => item.id === pickedRow.rowId);
    if (!table || !row) return null;
    const cells = table.columns.flatMap((column) => {
      const fieldId = row.cells.find((cell) => cell.columnId === column.id)?.fieldId;
      const reading = fieldId ? byId.get(fieldId) : undefined;
      return reading ? [{ label: column.label, reading }] : [];
    });
    return { table, rowNumber: row.rowNumber, cells, readings: cells.map((cell) => cell.reading).filter((reading) => reading.box) };
  }, [pickedRow, document, byId]);
  // A heading or item name picked on the image highlights what it names on the right.
  const heading = picked?.heading ? document?.headings?.find((item) => item.cellId === picked.id) ?? null : null;
  const headingFields = new Set(heading?.kind === "item" ? heading.fieldIds : []);
  const headsSection = (name: string) => heading?.section === name;
  const headsTable = (id: string) => heading?.kind === "table" && heading.tableIds.includes(id);
  const headsColumn = (tableId: string, columnId: string) => heading?.kind === "column" && heading.columnId === columnId && heading.tableIds.includes(tableId);
  const valueRefs = useRef(new Map<string, HTMLButtonElement>());
  const valuesPane = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!picked?.fromImage) return;
    if (picked.heading) valuesPane.current?.querySelector(".heading-hit")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    else valueRefs.current.get(picked.id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [picked]);
  const value = (reading: StructuredReading) => <ReadingValue key={reading.fieldId} reading={reading} active={current?.fieldId === reading.fieldId}
    buttonRef={(element) => { if (element) valueRefs.current.set(reading.fieldId, element); else valueRefs.current.delete(reading.fieldId); }}
    onPick={() => pick({ id: reading.fieldId, fromImage: false })} />;

  // Items outside tables are a 2-column table (name, value) framed like the tables; no header row.
  const fieldsView = (lines: ShelfLine[], groups: readonly ItemGroup[] = []) => {
    // Items under one item name (「登録No.」) share a first cell spanning their rows.
    const groupOf = new Map(groups.flatMap((group) => group.fieldIds.map((id) => [id, group] as const)));
    const span = new Map<ItemGroup, number>();
    for (const line of lines) {
      const group = groupOf.get(firstReading(line).fieldId);
      if (group) span.set(group, (span.get(group) ?? 0) + 1);
    }
    const nested = [...span.values()].some((count) => count >= 2);
    const started = new Set<ItemGroup>();
    const nameCells = (line: ShelfLine, label: string) => {
      const group = groupOf.get(firstReading(line).fieldId);
      if (!nested) return <th scope="row">{label}</th>;
      if (!group || (span.get(group) ?? 0) < 2) return <th scope="row" colSpan={2}>{label}</th>;
      const first = !started.has(group);
      started.add(group);
      return <>{first && <th scope="rowgroup" rowSpan={span.get(group)} className="shelf-item-group">{group.name}</th>}<th scope="row" className="shelf-item-sub">{subLabel(label, group.name)}</th></>;
    };
    return (
      <div className="shelf-table-wrap shelf-fields">
        <table className="shelf-table shelf-kv-table">
          <tbody>{lines.map((line) => "join" in line
            ? <tr key={`join:${line.join.name}`} className={`shelf-joined${line.readings.some((item) => headingFields.has(item.fieldId)) ? " heading-hit" : ""}`}>
              {nameCells(line, line.join.name)}
              <td><button type="button" className={`shelf-value${line.readings.some((item) => item.fieldId === current?.fieldId) ? " active" : ""}`}
                title="原本の欄を表示" onClick={() => pick({ id: line.readings[0].fieldId, fromImage: false })}>
                {joinedText(line.join.members, (path) => line.readings.find((item) => item.targetPath === path)?.value, line.join.separator ?? "") || <em>空欄</em>}
                {line.readings.some((item) => item.status === "uncertain") && <small className="shelf-uncertain">要確認</small>}
              </button></td>
            </tr>
            : <tr key={line.fieldId} className={headingFields.has(line.fieldId) ? "heading-hit" : undefined}>{nameCells(line, readingLabel(line))}<td>{value(line)}</td></tr>)}
          </tbody>
        </table>
      </div>
    );
  };
  return <article className="shelf-document">
    <header className="shelf-document-head">
      <div className="shelf-document-title">
        <h2>{documentTitle(summary)}</h2>
        <p>{folderName} ・ {timeFormat.format(new Date(summary.confirmedAt))}</p>
      </div>
      <div className="shelf-document-actions">
        <button type="button" className="secondary compact" onClick={() => onOpenReview(summary.jobId, null)}><FiEdit3 aria-hidden="true" />修正する</button>
        {summary.kind === "paged"
          ? <PagedXlsxButton jobId={summary.jobId} revision={summary.confirmedRevision} documentName={summary.documentName} />
          : <ExcelExportActions compact jobId={summary.jobId} documentName={summary.documentName} />}
      </div>
    </header>
    {detail.error ? <div className="error-box" role="alert"><p>{detail.error}</p><button type="button" className="secondary compact" onClick={onRetry}>もう一度取得</button></div>
      : !document ? <p className="shelf-empty" role="status">内容を読み込んでいます。</p>
      : <div className="shelf-document-body">
        <section className="shelf-viewer-pane" aria-label="原本">
          <PageViewer pages={document.pages ?? []} readings={document.fields} picked={current}
            headings={document.headings ?? []} pickedHeading={heading}
            rowFieldIds={rowView?.readings.map((reading) => reading.fieldId) ?? null}
            onPick={(reading) => pick({ id: reading.fieldId, fromImage: true })}
            onPickHeading={(item) => pick({ id: item.cellId, fromImage: true, heading: true })} />
          {rowView
            ? <PickedRow key={`${rowView.table.id}:${rowView.rowNumber}`} title={rowView.table.title} rowNumber={rowView.rowNumber} cells={rowView.cells}
              imageUrl={(document.pages ?? []).find((page) => page.pageId === (rowView.readings[0]?.pageId ?? null))?.imageUrl ?? null}
              box={unionBox(rowView.readings)} />
            : current
            ? <PickedField reading={current} place={readingPlace(current, current.tableId ? tableTitles.get(current.tableId) ?? null : null)} />
            : heading ? <p className="shelf-picked-heading" aria-live="polite">{headingText(heading, document, byId)}</p>
            : null}
        </section>
        <section ref={valuesPane} className="shelf-values" aria-label="読み取り値">
          {shownBlocks.map((block) => "table" in block
            ? <section key={block.key} className={`shelf-section${headsTable(block.table.id) ? " heading-hit" : ""}`}>
            <h3>{block.table.title}</h3>
            <div className="shelf-table-wrap">
              <table className="shelf-table">
                <thead><tr><th scope="col" className="shelf-row-number">行</th>{block.table.columns.map((column) => <th key={column.id} scope="col" className={headsColumn(block.table.id, column.id) ? "heading-hit" : undefined}>{column.label}</th>)}</tr></thead>
                <tbody>{block.table.rows.map((row) => {
                  const rowPicked = pickedRow?.tableId === block.table.id && pickedRow.rowId === row.id;
                  return <tr key={row.id} className={rowPicked ? "row-hit" : undefined}>
                  <th scope="row" className="shelf-row-number">
                    <button type="button" className="shelf-row-pick" aria-pressed={rowPicked} title="この行全体を原本で表示"
                      onClick={() => { setPicked(null); setPickedRow(rowPicked ? null : { tableId: block.table.id, rowId: row.id }); }}>{row.rowNumber}</button>
                  </th>
                  {block.table.columns.map((column) => {
                    const fieldId = row.cells.find((cell) => cell.columnId === column.id)?.fieldId;
                    const reading = fieldId ? byId.get(fieldId) : undefined;
                    return <td key={column.id} className={headsColumn(block.table.id, column.id) ? "column-hit" : undefined}>{reading ? value(reading) : null}</td>;
                  })}
                </tr>;
                })}</tbody>
              </table>
            </div>
            {block.lines && fieldsView(block.lines, block.groups)}
          </section>
            : <section key={block.key} className={`shelf-section${headsSection(block.name) ? " heading-hit" : ""}`}>
            <h3>{block.name}</h3>
            {fieldsView(block.lines, block.groups)}
          </section>)}
          {document.extraFields.length > 0 && <section className="shelf-section">
            <h3>その他の読み取り値</h3>
            <div className="shelf-table-wrap shelf-fields">
              <table className="shelf-table shelf-kv-table">
                <tbody>{document.extraFields.map((extra) => <tr key={extra.key}><th scope="row">{extra.label}</th><td><span className="shelf-value static">{extra.value ?? <em>空欄</em>}</span></td></tr>)}</tbody>
              </table>
            </div>
          </section>}
        </section>
      </div>}
  </article>;
}

/** A repeated choice stored numbered (黒2) is shown by its printed name (黒). */
function shownValue(reading: StructuredReading): string | null {
  return reading.value === null ? null : choiceText(reading.value, reading.choiceLabels ?? undefined);
}

function ReadingValue({ reading, active, buttonRef, onPick }: {
  reading: StructuredReading;
  active: boolean;
  buttonRef: (element: HTMLButtonElement | null) => void;
  onPick: () => void;
}) {
  return <button ref={buttonRef} type="button" className={`shelf-value${active ? " active" : ""}`} aria-pressed={active} title="原本のこの欄を表示" onClick={onPick}>
    {shownValue(reading) ?? <em>空欄</em>}
    {reading.status === "uncertain" && <small className="shelf-uncertain">要確認</small>}
  </button>;
}

/** The aligned page image with every read cell outlined; the picked cell is highlighted. */
export function PageViewer({ pages, readings, picked, headings, pickedHeading, marked = false, rowFieldIds = null, onPick, onPickHeading, title, actions }: {
  pages: DocumentPage[];
  readings: StructuredReading[];
  picked: StructuredReading | null;
  /** The cells of a picked table row: outlined together. */
  rowFieldIds?: string[] | null;
  headings: HeadingCell[];
  pickedHeading: HeadingCell | null;
  /** Every shown cell is highlighted (the chat's answer cells), the picked one most. */
  marked?: boolean;
  onPick: (reading: StructuredReading) => void;
  onPickHeading: (heading: HeadingCell) => void;
  /** Shown at the left of the tool row (the chat puts the document's name there). */
  title?: ReactNode;
  /** Shown after the zoom buttons (the chat's close button). */
  actions?: ReactNode;
}) {
  const [pageIndex, setPageIndex] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const page = pages[pageIndex] ?? null;
  const rowReadings = useMemo(() => {
    const ids = new Set(rowFieldIds ?? []);
    return readings.filter((reading) => ids.has(reading.fieldId) && reading.box);
  }, [readings, rowFieldIds]);
  // A value (or row) picked on another page turns to that page.
  const target = picked ?? rowReadings[0] ?? null;
  useEffect(() => {
    if (!target) return;
    const index = pages.findIndex((item) => item.pageId === (target.pageId ?? null));
    if (index >= 0) setPageIndex(index);
  }, [target, pages]);
  const image = useRef<HTMLImageElement>(null);
  // A cached image may have loaded before this runs; then its size is taken from it rather than cleared.
  useEffect(() => {
    const loaded = image.current?.complete && image.current.naturalWidth > 0 ? image.current : null;
    setSize(loaded && { width: loaded.naturalWidth, height: loaded.naturalHeight });
    setFailed(false);
  }, [page?.imageUrl]);
  // Bring the picked cell into the visible part of the image.
  useEffect(() => {
    const frame = scroller.current;
    if (!frame || !size) return;
    const element = picked ? frame.querySelector<HTMLElement>(`[data-field="${CSS.escape(picked.fieldId)}"]`)
      : rowReadings.length ? frame.querySelector<HTMLElement>(".shelf-row-outline") : null;
    if (!element) return;
    const outer = frame.getBoundingClientRect(), inner = element.getBoundingClientRect();
    if (inner.top >= outer.top && inner.bottom <= outer.bottom && inner.left >= outer.left && inner.right <= outer.right) return;
    frame.scrollTo({
      top: frame.scrollTop + inner.top - outer.top - (outer.height - inner.height) / 2,
      left: frame.scrollLeft + inner.left - outer.left - (outer.width - inner.width) / 2,
      behavior: "smooth",
    });
  }, [picked, rowReadings, size, zoom, pageIndex]);
  if (!page) return <div className="shelf-viewer empty"><p>原本画像はありません。</p></div>;
  const shown = readings.filter((reading) => reading.box && (reading.pageId ?? null) === page.pageId);
  const rowIds = new Set(rowReadings.map((reading) => reading.fieldId));
  const rowBox = unionBox(rowReadings.filter((reading) => (reading.pageId ?? null) === page.pageId));
  const zoomIndex = ZOOMS.indexOf(zoom);
  return <div className="shelf-viewer">
    <div className="shelf-viewer-tools">
      {title !== undefined && <div className="shelf-viewer-title">{title}</div>}
      {pages.length > 1 && <div className="shelf-pages" role="group" aria-label="ページ">
        {pages.map((item, index) => <button key={item.pageId ?? index} type="button" className={index === pageIndex ? "active" : ""} aria-pressed={index === pageIndex}
          onClick={() => setPageIndex(index)}>{item.pageNumber}</button>)}
      </div>}
      <div className="shelf-zoom" role="group" aria-label="表示倍率">
        <button type="button" aria-label="縮小" disabled={zoomIndex <= 0} onClick={() => setZoom((z) => ZOOMS[Math.max(ZOOMS.indexOf(z) - 1, 0)])}><FiMinus aria-hidden="true" /></button>
        <span>{Math.round(zoom * 100)}%</span>
        <button type="button" aria-label="拡大" disabled={zoomIndex >= ZOOMS.length - 1} onClick={() => setZoom((z) => ZOOMS[Math.min(ZOOMS.indexOf(z) + 1, ZOOMS.length - 1)])}><FiPlus aria-hidden="true" /></button>
      </div>
      {actions}
    </div>
    <div ref={scroller} className="shelf-viewer-scroll">
      {!failed && !size && <ImageSpinner label="原本画像を読み込んでいます" />}
      {failed ? <p className="shelf-viewer-missing">原本画像を表示できません。</p>
        : <div className="shelf-page-stage" style={{ width: `${zoom * 100}%` }}>
          <img ref={image} src={page.imageUrl} alt={`${page.pageNumber}ページ目の原本`} draggable={false}
            onLoad={(event) => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
            onError={() => setFailed(true)} />
          {size && headings.filter((item) => item.pageId === page.pageId).map((item) => {
            const box = item.box;
            return <button key={item.cellId} type="button" tabIndex={-1}
              className={`shelf-heading-box${pickedHeading?.cellId === item.cellId ? " active" : ""}`}
              style={{ left: `${(box.x / size.width) * 100}%`, top: `${(box.y / size.height) * 100}%`, width: `${(box.width / size.width) * 100}%`, height: `${(box.height / size.height) * 100}%` }}
              aria-label="この見出しを選ぶ" onClick={() => onPickHeading(item)} />;
          })}
          {size && shown.map((reading) => {
            const box = reading.box!;
            const active = picked?.fieldId === reading.fieldId;
            return <button key={reading.fieldId} type="button" tabIndex={-1} data-field={reading.fieldId}
              className={`shelf-box${marked ? " marked" : ""}${active ? " active" : ""}${rowIds.has(reading.fieldId) ? " in-row" : ""}${reading.value === null ? " blank" : ""}`}
              style={{ left: `${(box.x / size.width) * 100}%`, top: `${(box.y / size.height) * 100}%`, width: `${(box.width / size.width) * 100}%`, height: `${(box.height / size.height) * 100}%` }}
              title={`${reading.label}：${reading.value ?? "空欄"}`} aria-label={`${reading.label}を選ぶ`} onClick={() => onPick(reading)} />;
          })}
          {size && rowBox && <div className="shelf-row-outline" aria-hidden="true"
            style={{ left: `${(rowBox.x / size.width) * 100}%`, top: `${(rowBox.y / size.height) * 100}%`, width: `${(rowBox.width / size.width) * 100}%`, height: `${(rowBox.height / size.height) * 100}%` }} />}
        </div>}
    </div>
  </div>;
}

/** What a picked heading names, in words. */
function headingText(heading: HeadingCell, document: StructuredDocument, byId: Map<string, StructuredReading>): string {
  const tables = document.tables.filter((table) => heading.tableIds.includes(table.id));
  switch (heading.kind) {
    case "column": {
      const column = tables[0]?.columns.find((item) => item.id === heading.columnId);
      return `列「${column?.label ?? "名称未設定"}」（${tables[0]?.title ?? "表"}）`;
    }
    case "table":
      return `表の見出し「${tables.map((table) => table.title).join("」「")}」`;
    case "section":
      return `見出し「${heading.section}」`;
    default: {
      const names = [...new Set(heading.fieldIds.map((id) => byId.get(id)?.label).filter(Boolean))];
      return `項目名「${names.join("」「") || "名称未設定"}」`;
    }
  }
}

type Box = NonNullable<StructuredReading["box"]>;
/** The rectangle around every given cell, with a little margin. */
function unionBox(readings: StructuredReading[]): Box | null {
  const boxes = readings.flatMap((reading) => reading.box ? [reading.box] : []);
  if (!boxes.length) return null;
  const pad = 4;
  const x = Math.max(Math.min(...boxes.map((box) => box.x)) - pad, 0);
  const y = Math.max(Math.min(...boxes.map((box) => box.y)) - pad, 0);
  return { x, y, width: Math.max(...boxes.map((box) => box.x + box.width)) + pad - x, height: Math.max(...boxes.map((box) => box.y + box.height)) + pad - y };
}

/** A picked table row: the row cut out of the page image, and its values. */
function PickedRow({ title, rowNumber, cells, imageUrl, box }: {
  title: string; rowNumber: number; cells: { label: string; reading: StructuredReading }[]; imageUrl: string | null; box: Box | null;
}) {
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null);
  return <div className="shelf-picked shelf-picked-row" aria-live="polite">
    {imageUrl && box && <div className="shelf-row-crop" style={{ aspectRatio: `${box.width} / ${box.height}` }}>
      <img src={imageUrl} alt={`${title} ${rowNumber}行目の原本`} draggable={false}
        onLoad={(event) => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
        style={natural ? { width: `${(natural.width / box.width) * 100}%`, left: `${(-box.x / box.width) * 100}%`, top: `${(-box.y / box.height) * 100}%` } : { visibility: "hidden" }} />
    </div>}
    <div className="shelf-picked-text">
      <span>{title} {rowNumber}行目</span>
      <dl className="shelf-row-values">{cells.map((cell) => <div key={cell.reading.fieldId}><dt>{cell.label}</dt><dd>{shownValue(cell.reading) ?? "空欄"}</dd></div>)}</dl>
    </div>
  </div>;
}

function PickedField({ reading, place }: { reading: StructuredReading; place: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [reading.fieldId]);
  return <div className="shelf-picked" aria-live="polite">
    {reading.cropUrl && !failed && <img src={reading.cropUrl} alt={`${place}の原本`} draggable={false} onError={() => setFailed(true)} />}
    <div className="shelf-picked-text">
      <span>{place}</span>
      <strong>{shownValue(reading) ?? "空欄"}</strong>
    </div>
  </div>;
}

function PagedXlsxButton({ jobId, revision, documentName }: { jobId: string; revision: number; documentName: string | null }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function download() {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const url = URL.createObjectURL(await api.downloadPageOutput(jobId, revision));
      const link = window.document.createElement("a");
      link.href = url; link.download = `${documentName?.trim() || "読み取り結果"}.xlsx`;
      window.document.body.appendChild(link);
      try { link.click(); } finally { link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "XLSXを取得できませんでした。");
    } finally { setBusy(false); }
  }
  return <div className="excel-export-actions compact-export">
    <button type="button" className="primary compact" disabled={busy} onClick={() => void download()}><FiDownload aria-hidden="true" />{busy ? <><Spinner />XLSXを取得中…</> : ".xlsxを保存"}</button>
    {error && <p className="error-box" role="alert">{error}</p>}
  </div>;
}
