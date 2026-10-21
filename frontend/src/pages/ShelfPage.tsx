import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { displayOrders, joinByPath, joinedText, sortByOrder, type ItemJoin } from "../itemJoins";
import { onItemJoinsSaved } from "../components/TemplateItemJoinEditor";
import { FiArrowLeft, FiDownload, FiEdit3, FiExternalLink, FiFileText, FiFolder, FiMinus, FiPlus, FiRefreshCw, FiSearch, FiX } from "react-icons/fi";
import { api } from "../api";
import { ExcelExportActions } from "../components/ExcelExportActions";
import { EditingError } from "../editing/client";
import {
  structuredClient,
  type DocumentPage,
  type DocumentSummary,
  type StructuredDocument,
  type StructuredReading,
} from "../structured/client";

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
    return "この書類は再編集中です。確認画面で確定すると、書類棚に戻ります。";
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
    try { setDocuments((await structuredClient.allDocuments()).items); }
    catch (reason) { setListError(errorText(reason, "書類の一覧を取得できませんでした。")); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const folderName = useCallback((doc: DocumentSummary) =>
    templateNames[doc.templateId] || doc.templateName || "（名称未設定のテンプレート）", [templateNames]);
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
  const folder = folders.find((item) => item.templateId === (selected?.templateId ?? selection.templateId)) ?? folders[0] ?? null;
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

  const mainRef = useRef<HTMLElement>(null);
  const show = (next: ShelfSelection) => {
    onSelectionChange(next);
    window.requestAnimationFrame(() => {
      const top = mainRef.current?.getBoundingClientRect().top ?? 0;
      if (top < 0 || top > window.innerHeight * 0.6) mainRef.current?.scrollIntoView({ block: "start" });
    });
  };

  return <div className="page-stack shelf-page">
    <header className="shelf-header">
      <div><h1>書類棚</h1><p>確定済みの書類を、テンプレートごとに見られます。</p></div>
      <div className="shelf-header-actions">
        <label className="shelf-search">
          <FiSearch aria-hidden="true" />
          <input type="search" value={selection.query} placeholder="書類名・識別値で探す" aria-label="書類を探す"
            onChange={(event) => onSelectionChange({ ...selection, jobId: null, query: event.target.value })} />
          {selection.query && <button type="button" className="shelf-search-clear" aria-label="検索を消す"
            onClick={() => onSelectionChange({ ...selection, query: "" })}><FiX aria-hidden="true" /></button>}
        </label>
        <button type="button" className="secondary compact" disabled={loading} onClick={() => { cache.current.clear(); void load(); setRetry((n) => n + 1); }}>
          <FiRefreshCw aria-hidden="true" />{loading ? "更新中…" : "更新"}
        </button>
      </div>
    </header>
    {listError && <p className="error-box" role="alert">{listError}</p>}
    {documents === null && !listError && <p className="shelf-empty" role="status">書類を読み込んでいます。</p>}
    {documents?.length === 0 && <section className="shelf-empty-card">
      <FiFolder aria-hidden="true" />
      <div><strong>確定済みの書類はまだありません</strong><p>読み取り結果を確認して確定すると、ここにテンプレートごとに並びます。</p></div>
    </section>}
    {documents && documents.length > 0 && <div className={`shelf-layout${selected ? " reading" : ""}`}>
      {/* An open document takes the full width; its back button returns to the folder. */}
      {!selected && <nav className="shelf-folders" aria-label="テンプレート">
        {folders.map((item) => {
          const active = !query && item.templateId === folder?.templateId;
          return <button key={item.templateId} type="button" className={active ? "active" : ""} aria-current={active ? "true" : undefined}
            onClick={() => show({ templateId: item.templateId, jobId: null, query: "" })}>
            <FiFolder aria-hidden="true" /><span>{item.name}</span><small>{item.documents.length}</small>
          </button>;
        })}
      </nav>}
      <main ref={mainRef} className="shelf-main">
        {selected
          ? <DocumentView key={selected.jobId} summary={selected} folderName={folderName(selected)}
            detail={detail?.jobId === selected.jobId ? detail : { jobId: selected.jobId }}
            backLabel={query ? "検索結果" : folderName(selected)}
            onBack={() => show({ ...selection, jobId: null })}
            onRetry={() => setRetry((n) => n + 1)} onOpenReview={onOpenReview} />
          : <section aria-label="書類">
            <div className="shelf-main-head">
              <h2>{query ? `「${query}」の検索結果` : folder?.name}</h2>
              <span>{listed.length}件</span>
            </div>
            {listed.length === 0 && <p className="shelf-empty">一致する書類はありません。</p>}
            <ul className="shelf-grid">
              {listed.map((doc) => <li key={doc.jobId}>
                <DocumentCard doc={doc} caption={query ? folderName(doc) : identityLine(doc)}
                  onOpen={() => show({ ...selection, templateId: doc.templateId, jobId: doc.jobId })} />
              </li>)}
            </ul>
          </section>}
      </main>
    </div>}
  </div>;
}

function DocumentCard({ doc, caption, onOpen }: { doc: DocumentSummary; caption: string; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  return <button type="button" className="shelf-card" onClick={onOpen}>
    <span className="shelf-card-thumb">
      {doc.thumbnailUrl && !failed
        ? <img src={doc.thumbnailUrl} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
        : <FiFileText aria-hidden="true" />}
      {doc.kind === "paged" && <span className="shelf-card-badge">複数ページ</span>}
    </span>
    <span className="shelf-card-body">
      <strong>{documentTitle(doc)}</strong>
      {caption && <small>{caption}</small>}
      <span className="shelf-card-meta">
        <time dateTime={doc.confirmedAt}>{dayFormat.format(new Date(doc.confirmedAt))} 確定</time>
        {doc.seriesVersion > 1 && <span className="shelf-version">版{doc.seriesVersion}</span>}
      </span>
    </span>
  </button>;
}

function readingPlace(reading: StructuredReading, tableTitle: string | null) {
  return reading.rowNumber ? `${tableTitle ?? reading.section ?? ""} ${reading.rowNumber}行目・${reading.label}` : reading.label;
}

function DocumentView({ summary, folderName, detail, backLabel, onBack, onRetry, onOpenReview }: {
  summary: DocumentSummary;
  folderName: string;
  detail: Detail;
  backLabel: string;
  onBack: () => void;
  onRetry: () => void;
  onOpenReview: (jobId: string, field: string | null) => void;
}) {
  const [picked, setPicked] = useState<{ id: string; fromImage: boolean } | null>(null);
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
  const shownSections = useMemo(() => {
    const byPath = joinByPath(joins);
    return sections.map(([name, readings]) => {
      const placed = new Set<ItemJoin>();
      const lines: Array<StructuredReading | { join: ItemJoin; readings: StructuredReading[] }> = [];
      for (const reading of readings) {
        const join = byPath.get(reading.targetPath);
        const members = join ? join.members.flatMap((member) => readings.filter((item) => item.targetPath === member.targetPath)) : [];
        if (!join || members.length < 2) { lines.push(reading); continue; }
        if (placed.has(join)) continue;
        placed.add(join);
        lines.push({ join, readings: members });
      }
      const sorted = sortByOrder(lines, (line) => "join" in line ? line.readings[0].targetPath : line.targetPath, displayOrders(joins));
      return [name, sorted] as const;
    });
  }, [joins, sections]);
  const current = picked ? byId.get(picked.id) ?? null : null;
  const valueRefs = useRef(new Map<string, HTMLButtonElement>());
  useEffect(() => {
    if (picked?.fromImage) valueRefs.current.get(picked.id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [picked]);
  const value = (reading: StructuredReading) => <ReadingValue key={reading.fieldId} reading={reading} active={current?.fieldId === reading.fieldId}
    buttonRef={(element) => { if (element) valueRefs.current.set(reading.fieldId, element); else valueRefs.current.delete(reading.fieldId); }}
    onPick={() => setPicked({ id: reading.fieldId, fromImage: false })} />;

  return <article className="shelf-document">
    <header className="shelf-document-head">
      <button type="button" className="ghost compact shelf-back" onClick={onBack}><FiArrowLeft aria-hidden="true" />{backLabel}</button>
      <div className="shelf-document-title">
        <h2>{documentTitle(summary)}</h2>
        <p>{folderName} ・ {timeFormat.format(new Date(summary.confirmedAt))} 確定{summary.seriesVersion > 1 ? ` ・ 版${summary.seriesVersion}` : ""}</p>
        {identityValues(summary).length > 0 && <dl className="shelf-identity">
          {identityValues(summary).map((item) => <div key={item.targetPath}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}
        </dl>}
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
            onPick={(reading) => setPicked({ id: reading.fieldId, fromImage: true })} />
          {current
            ? <PickedField reading={current} place={readingPlace(current, current.tableId ? tableTitles.get(current.tableId) ?? null : null)}
              onOpen={() => onOpenReview(summary.jobId, current.targetPath)} />
            : <p className="shelf-viewer-note">値か原本の枠を押すと、対応する場所を強調します。</p>}
        </section>
        <section className="shelf-values" aria-label="読み取り値">
          {shownSections.map(([name, lines]) => <section key={name} className="shelf-section">
            <h3>{name}</h3>
            <div className="shelf-fields">
              {lines.map((line) => "join" in line
                ? <div key={`join:${line.join.name}`} className="shelf-field shelf-joined"><span>{line.join.name}</span>
                  <button type="button" className={`shelf-value${line.readings.some((item) => item.fieldId === current?.fieldId) ? " active" : ""}`}
                    title="原本の欄を表示" onClick={() => setPicked({ id: line.readings[0].fieldId, fromImage: false })}>
                    {joinedText(line.join.members, (path) => line.readings.find((item) => item.targetPath === path)?.value, line.join.separator ?? "") || <em>空欄</em>}
                    {line.readings.some((item) => item.status === "uncertain") && <small className="shelf-uncertain">要確認</small>}
                  </button></div>
                : <div key={line.fieldId} className="shelf-field"><span>{line.label}</span>{value(line)}</div>)}
            </div>
          </section>)}
          {document.tables.map((table) => <section key={table.id} className="shelf-section">
            <h3>{table.title}</h3>
            <div className="shelf-table-wrap">
              <table className="shelf-table">
                <thead><tr><th scope="col" className="shelf-row-number">行</th>{table.columns.map((column) => <th key={column.id} scope="col">{column.label}</th>)}</tr></thead>
                <tbody>{table.rows.map((row) => <tr key={row.id}>
                  <th scope="row" className="shelf-row-number">{row.rowNumber}</th>
                  {table.columns.map((column) => {
                    const fieldId = row.cells.find((cell) => cell.columnId === column.id)?.fieldId;
                    const reading = fieldId ? byId.get(fieldId) : undefined;
                    return <td key={column.id}>{reading ? value(reading) : null}</td>;
                  })}
                </tr>)}</tbody>
              </table>
            </div>
          </section>)}
          {document.extraFields.length > 0 && <section className="shelf-section">
            <h3>その他の読み取り値</h3>
            <div className="shelf-fields">
              {document.extraFields.map((extra) => <div key={extra.key} className="shelf-field"><span>{extra.label}</span><span className="shelf-value static">{extra.value ?? <em>空欄</em>}</span></div>)}
            </div>
          </section>}
        </section>
      </div>}
  </article>;
}

function ReadingValue({ reading, active, buttonRef, onPick }: {
  reading: StructuredReading;
  active: boolean;
  buttonRef: (element: HTMLButtonElement | null) => void;
  onPick: () => void;
}) {
  return <button ref={buttonRef} type="button" className={`shelf-value${active ? " active" : ""}`} aria-pressed={active} title="原本のこの欄を表示" onClick={onPick}>
    {reading.value ?? <em>空欄</em>}
    {reading.status === "uncertain" && <small className="shelf-uncertain">要確認</small>}
  </button>;
}

/** The aligned page image with every read cell outlined; the picked cell is highlighted. */
function PageViewer({ pages, readings, picked, onPick }: {
  pages: DocumentPage[];
  readings: StructuredReading[];
  picked: StructuredReading | null;
  onPick: (reading: StructuredReading) => void;
}) {
  const [pageIndex, setPageIndex] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const page = pages[pageIndex] ?? null;
  // A value picked on another page turns to that page.
  useEffect(() => {
    if (!picked) return;
    const index = pages.findIndex((item) => item.pageId === (picked.pageId ?? null));
    if (index >= 0) setPageIndex(index);
  }, [picked, pages]);
  useEffect(() => { setSize(null); setFailed(false); }, [page?.imageUrl]);
  // Bring the picked cell into the visible part of the image.
  useEffect(() => {
    const frame = scroller.current;
    if (!frame || !picked || !size) return;
    const target = frame.querySelector<HTMLElement>(`[data-field="${CSS.escape(picked.fieldId)}"]`);
    if (!target) return;
    const outer = frame.getBoundingClientRect(), inner = target.getBoundingClientRect();
    if (inner.top >= outer.top && inner.bottom <= outer.bottom && inner.left >= outer.left && inner.right <= outer.right) return;
    frame.scrollTo({
      top: frame.scrollTop + inner.top - outer.top - (outer.height - inner.height) / 2,
      left: frame.scrollLeft + inner.left - outer.left - (outer.width - inner.width) / 2,
      behavior: "smooth",
    });
  }, [picked, size, zoom, pageIndex]);
  if (!page) return <div className="shelf-viewer empty"><p>原本画像はありません。</p></div>;
  const shown = readings.filter((reading) => reading.box && (reading.pageId ?? null) === page.pageId);
  const zoomIndex = ZOOMS.indexOf(zoom);
  return <div className="shelf-viewer">
    <div className="shelf-viewer-tools">
      {pages.length > 1 && <div className="shelf-pages" role="group" aria-label="ページ">
        {pages.map((item, index) => <button key={item.pageId ?? index} type="button" className={index === pageIndex ? "active" : ""} aria-pressed={index === pageIndex}
          onClick={() => setPageIndex(index)}>{item.pageNumber}</button>)}
      </div>}
      <div className="shelf-zoom" role="group" aria-label="表示倍率">
        <button type="button" aria-label="縮小" disabled={zoomIndex <= 0} onClick={() => setZoom((z) => ZOOMS[Math.max(ZOOMS.indexOf(z) - 1, 0)])}><FiMinus aria-hidden="true" /></button>
        <span>{Math.round(zoom * 100)}%</span>
        <button type="button" aria-label="拡大" disabled={zoomIndex >= ZOOMS.length - 1} onClick={() => setZoom((z) => ZOOMS[Math.min(ZOOMS.indexOf(z) + 1, ZOOMS.length - 1)])}><FiPlus aria-hidden="true" /></button>
      </div>
    </div>
    <div ref={scroller} className="shelf-viewer-scroll">
      {failed ? <p className="shelf-viewer-missing">原本画像を表示できません。</p>
        : <div className="shelf-page-stage" style={{ width: `${zoom * 100}%` }}>
          <img src={page.imageUrl} alt={`${page.pageNumber}ページ目の原本`} draggable={false}
            onLoad={(event) => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
            onError={() => setFailed(true)} />
          {size && shown.map((reading) => {
            const box = reading.box!;
            const active = picked?.fieldId === reading.fieldId;
            return <button key={reading.fieldId} type="button" tabIndex={-1} data-field={reading.fieldId}
              className={`shelf-box${active ? " active" : ""}${reading.value === null ? " blank" : ""}`}
              style={{ left: `${(box.x / size.width) * 100}%`, top: `${(box.y / size.height) * 100}%`, width: `${(box.width / size.width) * 100}%`, height: `${(box.height / size.height) * 100}%` }}
              title={`${reading.label}：${reading.value ?? "空欄"}`} aria-label={`${reading.label}を選ぶ`} onClick={() => onPick(reading)} />;
          })}
        </div>}
    </div>
  </div>;
}

function PickedField({ reading, place, onOpen }: { reading: StructuredReading; place: string; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [reading.fieldId]);
  return <div className="shelf-picked" aria-live="polite">
    {reading.cropUrl && !failed && <img src={reading.cropUrl} alt={`${place}の原本`} onError={() => setFailed(true)} />}
    <div className="shelf-picked-text">
      <span>{place}</span>
      <strong>{reading.value ?? "空欄"}</strong>
    </div>
    <button type="button" className="ghost compact" onClick={onOpen}><FiExternalLink aria-hidden="true" />確認画面で開く</button>
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
    <button type="button" className="primary compact" disabled={busy} onClick={() => void download()}><FiDownload aria-hidden="true" />{busy ? "XLSXを取得中…" : ".xlsxを保存"}</button>
    {error && <p className="error-box" role="alert">{error}</p>}
  </div>;
}
