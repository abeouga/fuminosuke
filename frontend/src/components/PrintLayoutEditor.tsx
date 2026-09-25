import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { nearestBreak, printDefaults, type PrintCell, type PrintPlan, type PrintRequest, type PrintState } from "../printLayout";
import "./PrintLayoutEditor.css";
import { TemplatePrintFiles } from "./TemplatePrintFiles";

function Cells({ cells, physical = false }: { cells: PrintCell[]; physical?: boolean }) {
  return <>{cells.map((cell) => {
    const r = physical ? cell.rectangle! : cell;
    return <g key={cell.sourceRange}>
      <rect x={r.x} y={r.y} width={r.width} height={r.height} fill={cell.fill} />
      {Object.entries(cell.borders).filter(([, enabled]) => enabled).map(([side]) => {
        const horizontal = side === "top" || side === "bottom";
        const x = r.x + (side === "right" ? r.width : 0);
        const y = r.y + (side === "bottom" ? r.height : 0);
        return <line key={side} x1={x} y1={y} x2={x + (horizontal ? r.width : 0)}
          y2={y + (horizontal ? 0 : r.height)} stroke="#555" strokeWidth={0.7} />;
      })}
      <svg x={r.x} y={r.y} width={r.width} height={r.height} overflow="hidden">
        <text x={2} y={Math.min(r.height - 1, cell.fontSize * (physical ? r.width / cell.width : 1))}
          fontSize={cell.fontSize * (physical ? r.width / cell.width : 1)}
          fontWeight={cell.bold ? "bold" : "normal"} fill="#222">{cell.text}</text>
      </svg>
    </g>;
  })}</>;
}

export function PrintLayoutEditor({ versionId, disabled, onDirtyChange, onBusyChange, onSavedChange,
  onStateChange, onLayoutSaved, externalState, initialSaved = false }: {
  versionId: string; disabled: boolean;
  onDirtyChange: (dirty: boolean) => void; onBusyChange: (busy: boolean) => void;
  onSavedChange: (saved: boolean) => void;
  onStateChange?: (state: PrintState) => void;
  onLayoutSaved?: (layout: NonNullable<PrintState["layout"]>) => void;
  externalState?: PrintState | null;
  initialSaved?: boolean;
}) {
  const [opened, setOpened] = useState(false);
  const [state, setState] = useState<PrintState | null>(null);
  const [request, setRequest] = useState<PrintRequest | null>(null);
  const [plan, setPlan] = useState<PrintPlan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [prepareMessage, setPrepareMessage] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pendingPreview, setPendingPreview] = useState(false);
  const [selectedPage, setSelectedPage] = useState(0);
  const [overlay, setOverlay] = useState(true);
  const [drag, setDrag] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [addRow, setAddRow] = useState("");
  const sequence = useRef(0);
  const prepareSequence = useRef(0);
  const lock = useRef(false);
  const svg = useRef<SVGSVGElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => onBusyChange(busy || preparing), [busy, preparing, onBusyChange]);
  useEffect(() => {
    setPreparing(false); setPrepareMessage("");
    return () => { ++prepareSequence.current; };
  }, [versionId]);
  const apply = (next: PrintState) => {
    setState(next); setRequest(printDefaults(next)); setPlan(next.saved);
    setDirty(false); onSavedChange(Boolean(next.saved)); setSelectedPage(0);
    setAddRow("");
    onStateChange?.(next);
  };
  const load = async () => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try { apply(await api.printLayout(versionId)); }
    catch (e) { setError(e instanceof Error ? e.message : "印刷設定を取得できません。"); }
    finally { lock.current = false; setBusy(false); }
  };
  useEffect(() => {
    if (initialSaved) void load();
    // Saved versions need their revision even while the page planner is collapsed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionId]);
  useEffect(() => {
    if (externalState && externalState !== state && !dirty) apply(externalState);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalState]);
  useEffect(() => {
    if (!opened || !request || !state || state.source.unsupported.length) return;
    if (state.saved && !dirty) {
      ++sequence.current;
      setPlan(state.saved); setPendingPreview(false);
      return;
    }
    const token = ++sequence.current;
    setPendingPreview(true);
    setPlan(null);
    const timeout = window.setTimeout(() => {
      void api.previewPrintLayout(versionId, request).then(result => {
        if (sequence.current !== token) return;
        setPlan(result); setError(""); setPendingPreview(false);
        setSelectedPage(p => Math.min(p, result.pages.length - 1));
      }).catch((e: unknown) => {
        if (sequence.current !== token) return;
        setError(e instanceof Error ? e.message : "ページを計算できません。"); setPendingPreview(false);
      });
    }, 180);
    return () => { window.clearTimeout(timeout); ++sequence.current; };
  }, [opened, request, state, versionId, dirty]);
  const update = (next: Partial<PrintRequest>) => {
    if (!request) return;
    // Freeze current candidate ranges before changing paper/margins/targets.
    const breaks = request.breakAfterRows ?? plan?.pages.slice(0, -1).map(p => p.endRow) ?? null;
    ++sequence.current; setPendingPreview(true); setPlan(null);
    setRequest({ ...request, breakAfterRows: breaks, autoArrange: false, ...next });
    setDirty(true);
  };
  const save = async (clear = false) => {
    if (!request || !state || lock.current) return;
    lock.current = true; setBusy(true); setError(""); ++sequence.current;
    try {
      const next = await (clear ? api.clearPrintLayout(versionId, state.revision)
        : api.savePrintLayout(versionId, request));
      if (next.layout) onLayoutSaved?.(next.layout);
      apply(next);
    }
    catch (e) { setError(e instanceof Error ? e.message : "保存できません。編集内容は保持しています。"); }
    finally { lock.current = false; setBusy(false); }
  };
  const prepare = async () => {
    if (!state?.saved || !state.editable || dirty || busy || preparing || disabled) return;
    const token = ++prepareSequence.current;
    const editingToken = sequence.current;
    setPreparing(true); setPrepareMessage(""); setError("");
    try {
      const next = await api.preparePrintFiles(versionId, state.revision);
      if (prepareSequence.current !== token) return;
      if (sequence.current !== editingToken) {
        setPrepareMessage("印刷用ファイルを生成しました。新しい編集内容は保持しています。保存後に設定を再取得してください。");
        return;
      }
      apply(next);
      setPrepareMessage(`印刷用ファイルを確定しました（${next.saved?.pages.length}ページ・${((next.saved?.commonScale ?? 0) * 100).toFixed(0)}%）。`);
    } catch (e) {
      if (prepareSequence.current === token) setError(e instanceof Error ? e.message : "印刷用ファイルを生成できません。編集内容は保持しています。");
    } finally {
      if (prepareSequence.current === token) setPreparing(false);
    }
  };
  const breaks = request?.breakAfterRows ?? plan?.pages.slice(0, -1).map(p => p.endRow) ?? [];
  const blocked = disabled || busy || preparing || !state?.editable || Boolean(state?.source.unsupported.length);
  const current = plan?.pages[selectedPage];
  const availableRows = state?.source.validBreakRows.filter(r => !breaks.includes(r)) ?? [];
  const addBoundary = () => {
    if (!state || blocked) return;
    let y = state.source.height / 2;
    if (scroll.current && svg.current) {
      const frame = scroll.current.getBoundingClientRect();
      const sheet = svg.current.getBoundingClientRect();
      if (sheet.height > 0) y = (frame.top + frame.height / 2 - sheet.top) * state.source.height / sheet.height;
    }
    const row = addRow ? Number(addRow) : nearestBreak(y, state.source, breaks);
    if (row !== null && availableRows.includes(row)) {
      update({ breakAfterRows: [...breaks, row].sort((a, b) => a - b) }); setAddRow("");
    }
  };
  const moveBreak = (index: number, row: number) => {
    if (!state || !state.source.validBreakRows.includes(row) || breaks.some((b, i) => i !== index && b === row)) return;
    // A dragged boundary cannot cross its neighbors.
    if ((index > 0 && row <= breaks[index - 1]) || (index + 1 < breaks.length && row >= breaks[index + 1])) return;
    update({ breakAfterRows: breaks.map((b, i) => i === index ? row : b) });
  };
  return <section className="form-card print-editor">
    <button type="button" className="secondary" disabled={busy} aria-expanded={opened}
      onClick={() => { setOpened(!opened); if (!state && !opened) void load(); }}>印刷ページを編集</button>
    {opened && <>
      <p>青い区切り線をドラッグして、各ページに印刷する範囲を調整してください。保存後は「このテンプレートでOCRを使う」から続けられます。</p>
      {error && <p role="alert" className="error-box">{error}</p>}
      {!state && <button type="button" disabled={busy} onClick={() => void load()}>読み込みを再試行</button>}
      {state && request && <>
        {state.source.unsupported.length > 0 && <div className="error-box" role="status">
          <strong>このXLSXは現在のページ編集対象外です。境界の追加・保存はできません。</strong>
          <ul>{state.source.unsupported.map(w => <li key={w}>{w}</li>)}</ul>
          <p>Excel側で該当する構造・設定を確認して再取り込みしてください。下の表示は拡大・スクロールで確認できます。</p>
        </div>}
        {!state.editable && <p className="inline-warning">この版は閲覧専用です。編集には下書き版が必要です。</p>}
        {disabled && <p className="inline-warning">テンプレート側の編集中の内容を保存してからページ境界を編集してください。</p>}
        <fieldset disabled={blocked} className="print-controls">
          <label>用紙<select value={request.paperSize} onChange={e => update({ paperSize: e.target.value as PrintRequest["paperSize"] })}>
            {["A4", "A3", "Letter"].map(v => <option key={v}>{v}</option>)}</select></label>
          <label>方向<select value={request.orientation} onChange={e => update({ orientation: e.target.value as PrintRequest["orientation"] })}>
            <option value="portrait">縦</option><option value="landscape">横</option></select></label>
          <button type="button" onClick={() => update({ breakAfterRows: null, autoArrange: true })}>自動配置</button>
          <button type="button" onClick={() => update({ breakAfterRows: [] })}>全範囲を1ページにする</button>
          <details><summary>用紙の詳細設定</summary>
          <label>目標の縦ページ数<input type="number" min={1} max={50} value={request.targetPagesTall ?? ""}
            onChange={e => update({ targetPagesTall: e.target.value ? Number(e.target.value) : null })} /></label>
          {(["top", "bottom", "left", "right"] as const).map((key, i) => <label key={key}>
            {["上", "下", "左", "右"][i]}余白 mm<input type="number" min={0} max={100} step={0.1} value={request.margins[key]}
              onChange={e => update({ margins: { ...request.margins, [key]: Number(e.target.value) } })} /></label>)}
          <label><input type="checkbox" checked={request.horizontalCentering} onChange={e => update({ horizontalCentering: e.target.checked })} />左右中央</label>
          <label><input type="checkbox" checked={request.verticalCentering} onChange={e => update({ verticalCentering: e.target.checked })} />上下中央</label>
          </details>
        </fieldset>
        <div className="print-controls print-actions">
          <button type="button" disabled={blocked || pendingPreview || !plan} onClick={() => void save()}>印刷ページを下書き保存</button>
          <button type="button" disabled={busy || !dirty} onClick={() => { apply(state); setError(""); }}>変更を取消</button>
          <button type="button" disabled={busy || dirty} onClick={() => void load()}>保存済み設定を再取得</button>
          <span role="status">{dirty ? "未保存" : state.saved ? `保存済み・${state.saved.pages.length}ページ` : "未保存のページ案"}</span>
        </div>
        {(state.pageHeadings?.length ?? 0) > 0 && <details open={Boolean(plan && state.pageHeadings?.some(
          heading => !plan.pages.some(page => page.range === heading.pageRange),
        ))}>
          <summary>見出しを載せるページ</summary>
          <fieldset disabled={blocked || pendingPreview}>
          <p>区切り線を変えて見出しの所属ページが変わる場合は、移動先のページを選んでください。</p>
          {state.pageHeadings!.map(heading => <label key={heading.cellId}>
            {heading.text}（現在: {heading.pageRange}）
            <select aria-label={`仮想見出し ${heading.text} の移動先範囲`}
              value={request.pageHeadingMoves?.find(move => move.cellId === heading.cellId)?.toRange ?? ""}
              onChange={e => update({ pageHeadingMoves: [
                ...(request.pageHeadingMoves ?? []).filter(move => move.cellId !== heading.cellId),
                ...(e.target.value ? [{ cellId: heading.cellId, fromRange: heading.pageRange, toRange: e.target.value }] : []),
              ] })}>
              <option value="">所属を変更しない</option>
              {(plan?.pages ?? state.saved?.pages ?? []).map(page => <option key={page.range} value={page.range}>
                Page {page.page}: {page.range}
              </option>)}
            </select>
          </label>)}
          </fieldset>
        </details>}
        <div className="print-views">
          <div><h3>連続シート・行境界の編集</h3>
            <div className="print-controls">
              <button type="button" disabled={zoom <= .5} onClick={() => setZoom(z => Math.max(.5, z - .25))}>縮小</button>
              <output aria-label="表示倍率">{Math.round(zoom * 100)}%</output>
              <button type="button" disabled={zoom >= 3} onClick={() => setZoom(z => Math.min(3, z + .25))}>拡大</button>
              <button type="button" onClick={() => setZoom(1)}>倍率をリセット</button>
            </div>
            <p>横幅基準の表示です。上下にスクロールし、青い「移動」ハンドルをドラッグするか、最終行を選択します。結合セルの途中には区切れません。</p>
            <div className="print-controls">
              <label>追加する境界（行の後）<select disabled={blocked || !availableRows.length} value={addRow} onChange={e => setAddRow(e.target.value)}>
                <option value="">表示中の位置に追加</option>
                {availableRows.map(row => <option key={row} value={row}>{row}行の後</option>)}
              </select></label>
              <button type="button" disabled={blocked || breaks.length >= 49 || !availableRows.length} onClick={addBoundary}>改ページを追加</button>
            </div>
            {!state.source.validBreakRows.length && <p className="inline-warning">結合セルを分断せずに追加できる行境界がありません。</p>}
            <div ref={scroll} className="print-logical-scroll" tabIndex={0} aria-label="連続シートのスクロール領域"><svg ref={svg} viewBox={`-22 0 ${state.source.width + 22} ${state.source.height}`}
              style={{ width: `${zoom * 100}%`, minWidth: `${960 * zoom}px` }}
              aria-label="連続シートの改ページ"
              onPointerMove={e => {
                if (drag === null || blocked || !svg.current) return;
                const bounds = svg.current.getBoundingClientRect();
                const y = (e.clientY - bounds.top) * state.source.height / bounds.height;
                const row = nearestBreak(y, state.source, breaks.filter((_, i) => i !== drag));
                if (row !== null) moveBreak(drag, row);
              }} onPointerUp={() => setDrag(null)} onPointerCancel={() => setDrag(null)}>
              <Cells cells={state.source.cells} />
              {[...breaks, state.source.endRow].map((end, i) => {
                const start = i ? breaks[i - 1] + 1 : state.source.startRow;
                const y = state.source.rowBoundaries.find(b => b.row === start)!.y;
                const bottom = state.source.rowBoundaries.find(b => b.row === end + 1)!.y;
                return <rect key={`page-${i}`} x={0} y={y} width={state.source.width} height={bottom - y}
                  fill={i % 2 ? "#267aaf" : "#6b8c67"} fillOpacity={0.05}
                  stroke="#6696b1" strokeWidth={0.7} pointerEvents="none" />;
              })}
              {state.source.rowBoundaries.filter(b => b.row <= state.source.endRow).map(b =>
                <text key={b.row} x={-3} y={b.y + 9} textAnchor="end" fontSize={7} fill="#526270" pointerEvents="none">{b.row}</text>)}
              {breaks.map((row, i) => {
                const y = state.source.rowBoundaries.find(b => b.row === row + 1)!.y;
                return <g key={i} style={{ cursor: blocked ? "default" : "ns-resize", touchAction: "none" }}
                  onPointerDown={e => { if (!blocked) { e.preventDefault(); svg.current?.setPointerCapture(e.pointerId); setDrag(i); } }}>
                  <line x1={0} x2={state.source.width} y1={y} y2={y} stroke={dirty ? "#a45000" : "#156ec5"} strokeWidth={2} vectorEffect="non-scaling-stroke" />
                  <rect x={4} y={Math.max(0, y - 21)} width={155} height={20} rx={3} fill="#e8f3ff" />
                  <text x={8} y={Math.max(14, y - 6)} fontSize={12} fill="#064b8b" pointerEvents="none">移動 · Page {i + 1} / {row}行の後</text>
                  <line x1={0} x2={state.source.width} y1={y} y2={y} stroke="transparent" strokeWidth={18}
                    aria-label={`改ページ ${i + 1} を移動`} vectorEffect="non-scaling-stroke"
                    style={{ touchAction: "none" }} />
                </g>;
              })}
            </svg></div>
            <div className="print-boundary-list">{breaks.map((row, i) => <div key={i} className="print-boundary-controls">
              <label>Page {i + 1} 最終行<select disabled={blocked} value={row} onChange={e => moveBreak(i, Number(e.target.value))}>
                {state.source.validBreakRows.filter(r => (i === 0 || r > breaks[i - 1]) && (i === breaks.length - 1 || r < breaks[i + 1]))
                  .map(r => <option key={r} value={r}>{r}</option>)}</select></label>
              <button type="button" disabled={blocked} onClick={() => update({ breakAfterRows: breaks.filter((_, n) => n !== i) })}>改ページ {i + 1} を削除</button>
              <span>{i ? breaks[i - 1] + 1 : state.source.startRow}〜{row}行</span>
            </div>)}</div>
          </div>
          <div><h3>用紙上の配置プレビュー</h3>
            {pendingPreview && <p role="status">計算中…</p>}
            {plan && <><p>{plan.pages.length}ページ / 共通縮小率 {(plan.commonScale * 100).toFixed(1)}%</p>
              <label>表示ページ<select value={selectedPage} onChange={e => setSelectedPage(Number(e.target.value))}>
                {plan.pages.map((p, i) => <option key={p.page} value={i}>Page {p.page}: {p.range}</option>)}</select></label>
              <label><input type="checkbox" checked={overlay} onChange={e => setOverlay(e.target.checked)} />OCR範囲の候補を表示</label>
              {current && <svg viewBox={`0 0 ${current.width} ${current.height}`} className="print-physical" aria-label={`用紙 Page ${current.page}`}>
                {!dirty && state.saved?.pages[selectedPage]?.referenceImage
                  ? <image href={`/api/v1/template-versions/${versionId}/print-layout/pages/${current.page}/image?revision=${state.revision}`}
                    onError={() => setError("保存済みページ画像を取得できません。設定を再取得して確認してください。")}
                    width={current.width} height={current.height} />
                  : <Cells cells={current.cells} physical />}
                {overlay && current.fields.map(f => <rect key={f.cellId} {...f.rectangle} fill="none" stroke="#1571c4" strokeWidth={2} />)}
              </svg>}
              {plan.warnings.length > 0 && <details><summary>ページ計算の詳細</summary>
                {plan.warnings.map(w => <p key={w}>{w}</p>)}
              </details>}
            </>}
          </div>
        </div>
        <div className="print-controls">
          {state.saved && <details><summary>旧方式への切り替え</summary><button type="button" disabled={disabled || busy || preparing || !state.editable} onClick={() => void save(true)}>印刷ページ設定を解除して旧方式を使う</button></details>}
          {state.saved && state.editable && <button type="button" disabled={blocked || dirty || pendingPreview}
            onClick={() => void prepare()}>{preparing ? "印刷見本を作成中…" : "印刷見本を作る"}</button>}
        </div>
        {state.saved && state.editable && <p>先に印刷を試す場合は、保存したページ範囲から印刷見本を作成できます。有効化時にも自動で準備します。</p>}
        {prepareMessage && <p role="status">{prepareMessage}</p>}
        {state.saved && <TemplatePrintFiles key={versionId} versionId={versionId} revision={state.revision}
          disabled={disabled || busy || dirty} />}
      </>}
    </>}
  </section>;
}
