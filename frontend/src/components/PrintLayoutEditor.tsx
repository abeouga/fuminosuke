import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { nearestBreak, printDefaults, type PrintCell, type PrintPlan, type PrintRequest, type PrintState } from "../printLayout";
import "./PrintLayoutEditor.css";
import { ReferenceQualification } from "./ReferenceQualification";

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

export function PrintLayoutEditor({ versionId, disabled, onDirtyChange, onBusyChange, onSavedChange }: {
  versionId: string; disabled: boolean;
  onDirtyChange: (dirty: boolean) => void; onBusyChange: (busy: boolean) => void;
  onSavedChange: (saved: boolean) => void;
}) {
  const [opened, setOpened] = useState(false);
  const [state, setState] = useState<PrintState | null>(null);
  const [request, setRequest] = useState<PrintRequest | null>(null);
  const [plan, setPlan] = useState<PrintPlan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [pendingPreview, setPendingPreview] = useState(false);
  const [selectedPage, setSelectedPage] = useState(0);
  const [overlay, setOverlay] = useState(true);
  const [drag, setDrag] = useState<number | null>(null);
  const sequence = useRef(0);
  const lock = useRef(false);
  const svg = useRef<SVGSVGElement>(null);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => onBusyChange(busy), [busy, onBusyChange]);
  const apply = (next: PrintState) => {
    setState(next); setRequest(printDefaults(next)); setPlan(next.saved);
    setDirty(false); onSavedChange(Boolean(next.saved)); setSelectedPage(0);
  };
  const load = async () => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try { apply(await api.printLayout(versionId)); }
    catch (e) { setError(e instanceof Error ? e.message : "印刷設定を取得できません。"); }
    finally { lock.current = false; setBusy(false); }
  };
  useEffect(() => {
    if (!opened || !request || !state || state.source.unsupported.length) return;
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
  }, [opened, request, state, versionId]);
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
    try { apply(await (clear ? api.clearPrintLayout(versionId, state.revision)
      : api.savePrintLayout(versionId, request))); }
    catch (e) { setError(e instanceof Error ? e.message : "保存できません。編集内容は保持しています。"); }
    finally { lock.current = false; setBusy(false); }
  };
  const breaks = request?.breakAfterRows ?? plan?.pages.slice(0, -1).map(p => p.endRow) ?? [];
  const blocked = disabled || busy || !state?.editable || Boolean(state?.source.unsupported.length);
  const current = plan?.pages[selectedPage];
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
      <p className="inline-warning">下書きのページ設計に対応しています。ページ別の実OCR・補正・確定は未対応です。設定を保存した版は有効化できません。</p>
      {error && <p role="alert" className="error-box">{error}</p>}
      {!state && <button type="button" disabled={busy} onClick={() => void load()}>読み込みを再試行</button>}
      {state && request && <>
        {state.source.unsupported.map(w => <p className="error-box" key={w}>{w}</p>)}
        <fieldset disabled={blocked} className="print-controls">
          <label>用紙<select value={request.paperSize} onChange={e => update({ paperSize: e.target.value as PrintRequest["paperSize"] })}>
            {["A4", "A3", "Letter"].map(v => <option key={v}>{v}</option>)}</select></label>
          <label>方向<select value={request.orientation} onChange={e => update({ orientation: e.target.value as PrintRequest["orientation"] })}>
            <option value="portrait">縦</option><option value="landscape">横</option></select></label>
          <label>目標の縦ページ数<input type="number" min={1} max={50} value={request.targetPagesTall ?? ""}
            onChange={e => update({ targetPagesTall: e.target.value ? Number(e.target.value) : null })} /></label>
          {(["top", "bottom", "left", "right"] as const).map((key, i) => <label key={key}>
            {["上", "下", "左", "右"][i]}余白 mm<input type="number" min={0} max={100} step={0.1} value={request.margins[key]}
              onChange={e => update({ margins: { ...request.margins, [key]: Number(e.target.value) } })} /></label>)}
          <label><input type="checkbox" checked={request.horizontalCentering} onChange={e => update({ horizontalCentering: e.target.checked })} />左右中央</label>
          <label><input type="checkbox" checked={request.verticalCentering} onChange={e => update({ verticalCentering: e.target.checked })} />上下中央</label>
          <button type="button" onClick={() => update({ breakAfterRows: null, autoArrange: true })}>自動配置</button>
          <button type="button" disabled={breaks.length >= 49} onClick={() => {
            const row = nearestBreak(state.source.height / 2, state.source, breaks);
            if (row !== null) update({ breakAfterRows: [...breaks, row].sort((a, b) => a - b) });
          }}>改ページを追加</button>
          <button type="button" onClick={() => update({ breakAfterRows: [] })}>全範囲を1ページにする</button>
        </fieldset>
        <div className="print-views">
          <div><h3>連続シート・行境界の編集</h3><p>セル範囲の表示です。用紙の実寸ではありません。</p>
            <div className="print-logical-scroll"><svg ref={svg} viewBox={`-22 0 ${state.source.width + 22} ${state.source.height}`}
              aria-label="連続シートの改ページ" style={{ touchAction: drag === null ? "auto" : "none" }}
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
                return <g key={i}>
                  <line x1={0} x2={state.source.width} y1={y} y2={y} stroke="#156ec5" strokeWidth={2} />
                  <text x={4} y={Math.max(12, y - 3)} fontSize={12} fill="#064b8b">Page {i + 1} end / {row}行</text>
                  <line x1={0} x2={state.source.width} y1={y} y2={y} stroke="transparent" strokeWidth={18}
                    style={{ cursor: blocked ? "default" : "ns-resize", touchAction: "none" }}
                    onPointerDown={e => { if (!blocked) { e.currentTarget.setPointerCapture(e.pointerId); setDrag(i); } }} />
                </g>;
              })}
            </svg></div>
            {breaks.map((row, i) => <div key={i} className="print-boundary-controls">
              <label>Page {i + 1} 最終行<select disabled={blocked} value={row} onChange={e => moveBreak(i, Number(e.target.value))}>
                {state.source.validBreakRows.filter(r => (i === 0 || r > breaks[i - 1]) && (i === breaks.length - 1 || r < breaks[i + 1]))
                  .map(r => <option key={r} value={r}>{r}</option>)}</select></label>
              <button type="button" disabled={blocked} onClick={() => update({ breakAfterRows: breaks.filter((_, n) => n !== i) })}>改ページ {i + 1} を削除</button>
            </div>)}
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
              {plan.warnings.map(w => <p className="inline-warning" key={w}>{w}</p>)}
            </>}
          </div>
        </div>
        <div className="print-controls">
          <button type="button" disabled={blocked || pendingPreview || !plan} onClick={() => void save()}>印刷ページを下書き保存</button>
          <button type="button" disabled={busy || !dirty} onClick={() => { apply(state); setError(""); }}>変更を取消</button>
          <button type="button" disabled={busy || dirty} onClick={() => void load()}>保存済み設定を再取得</button>
          {state.saved && <button type="button" disabled={disabled || busy || !state.editable} onClick={() => void save(true)}>印刷ページ設定を解除して旧方式を使う</button>}
          {dirty && <span>未保存</span>}
        </div>
        {state.saved && <ReferenceQualification versionId={versionId} revision={state.revision}
          disabled={disabled || busy || dirty || !state.editable}
          onBusyChange={setBusy} />}
      </>}
    </>}
  </section>;
}
