import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FiCpu, FiEdit2, FiMove, FiRotateCcw, FiTrash2, FiX } from "react-icons/fi";
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type React from "react";
import type { FieldMappingTarget, LayoutCell, LayoutDefinition, LayoutMarkSet } from "../types";
import { type Rect, type Point } from "./layoutEditorGeometry";

function suggestedChoiceLabels(text: string): string[] {
  const normalized = text.normalize("NFKC").trim();
  const compact = normalized.replace(/[\s　・,、/／]+/g, "");
  const knownGroups = [
    ["都", "道", "府", "県"],
    ["市", "区", "町", "村"],
  ];
  const known = knownGroups.find((labels) => (
    labels.every((label) => compact.includes(label))
    && compact.length <= labels.join("").length + 4
  ));
  if (known) return known;
  const separated = normalized
    .split(/[\s　・,、/／]+/)
    .map((value) => value.trim())
    .filter(Boolean);
  return separated.length >= 2 && separated.length <= 12
    ? [...new Set(separated)]
    : [];
}
function reflowChoiceOptions(
  cell: LayoutCell,
  options: LayoutMarkSet["options"],
): LayoutMarkSet["options"] {
  if (options.length === 0) return [];
  return options.map((option, index) => {
    const left = Math.round(cell.width * index / options.length);
    const right = Math.round(cell.width * (index + 1) / options.length);
    return {
      ...option,
      x: left,
      y: 0,
      width: Math.max(1, right - left),
      height: Math.max(1, cell.height),
    };
  });
}
/**
 * New choice IDs are never fixed names such as "choice_1": a draft made from an active
 * template refuses IDs the earlier version used, so a reused fixed name would block saving.
 */
/** Checklist results join checked names with "・", so a name may not contain a separator. */
export function checklistLabel(name: string): string {
  return name.replace(/[・、,;\n\r]+/g, "／").replace(/^／+|／+$/g, "").trim();
}
export function newMarkSetId(): string {
  return `mark_set_${crypto.randomUUID().replace(/-/g, "")}`;
}
export function newMarkOptionId(): string {
  return `mark_option_${crypto.randomUUID().replace(/-/g, "")}`;
}
function nextChoiceOptionId(_options: LayoutMarkSet["options"]): string {
  return newMarkOptionId();
}
export function genericManualChoicePreset(
  cell: LayoutCell,
  targetPath = "",
): LayoutMarkSet[] {
  const labels = suggestedChoiceLabels(cell.text);
  return [{
    id: newMarkSetId(),
    targetPath,
    recognitionMode: "automatic",
    selectionMode: "single",
    options: reflowChoiceOptions(
      cell,
      labels.map((label) => ({
        id: newMarkOptionId(),
        label,
        value: label,
        x: 0,
        y: 0,
        width: 1,
        height: Math.max(1, cell.height),
      })),
    ),
  }];
}
export function checklistPreset(cell: LayoutCell, targetPath = ""): LayoutMarkSet[] {
  const listed = [...new Set(cell.text.split(/[□☐☑☒■]/).slice(1).map(label => checklistLabel(label)).filter(Boolean))];
  // A box without listed choices (a lone "□", or a box imported without text) is one checkbox.
  const labels = listed.length ? listed : ["チェック済み"];
  return [{ id: newMarkSetId(), targetPath, recognitionMode: "checklist", selectionMode: "multiple",
    options: labels.map((label) => ({ id: newMarkOptionId(), label, value: label,
      x: 0, y: 0, width: cell.width, height: cell.height })) }];
}
const CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳";
export function choiceNumber(index: number): string {
  return CIRCLED[index] ?? `(${index + 1})`;
}

export function MarkCellEditor({
  cell,
  sets,
  targets,
  writeInTargets = [],
  source,
  canonicalWidth,
  canonicalHeight,
  onChange,
  selectedChoiceId,
  onChoiceSelect,
  hideTarget = false,
  onSuggest,
  suggesting = false,
  onUndoSuggest,
  similarOffer = null,
}: {
  cell: LayoutCell;
  sets: LayoutMarkSet[];
  targets: FieldMappingTarget[];
  writeInTargets?: FieldMappingTarget[];
  source: string;
  canonicalWidth: number;
  canonicalHeight: number;
  onChange: (sets: LayoutMarkSet[]) => void;
  selectedChoiceId?: string | null;
  onChoiceSelect?: (markSetId: string, choiceId: string) => void;
  /** The storage target already comes from the label connection drawn on the form. */
  hideTarget?: boolean;
  /** Reads the printed choices from the template image (the model); absent when not configured. */
  onSuggest?: (hint: string) => void;
  suggesting?: boolean;
  /** Puts back the choices the last AI reading replaced. */
  onUndoSuggest?: () => void;
  /** Look-alike boxes that can take the same choices with one click. */
  similarOffer?: { count: number; apply: () => void } | null;
}) {
  const [newOptionLabel, setNewOptionLabel] = useState("");
  const [draft, setDraft] = useState<Rect | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  // The choice picked on the image (or by its name in the list); it wins where choices overlap.
  const [picked, setPicked] = useState<string | null>(null);
  const [focusOptionId, setFocusOptionId] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  // The box is drawn large in its own panel: the sidebar shows it only ~200px wide.
  const [zoomed, setZoomed] = useState(false);
  useEffect(() => {
    if (!zoomed) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setZoomed(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [zoomed]);
  useEffect(() => { setZoomed(false); }, [cell.id]);
  // Tools of the enlarged panel: draw a choice, move or resize one, delete one.
  const [tool, setTool] = useState<"pen" | "move" | "erase">("pen");
  const activeTool = zoomed ? tool : "pen";
  const moveRef = useRef<{ id: string; mode: "move" | "resize"; start: Point; origin: Rect; cycle: string | null } | null>(null);
  const [moving, setMoving] = useState<{ id: string; rect: Rect } | null>(null);
  const keyRef = useRef<((event: KeyboardEvent) => void) | null>(null);
  useEffect(() => {
    if (!zoomed) return;
    const onKey = (event: KeyboardEvent) => keyRef.current?.(event);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomed]);
  const dragStart = useRef<Point | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Image units per screen pixel, so the resize handle keeps a fixed on-screen size.
  const [unitsPerPx, setUnitsPerPx] = useState(1);
  const [overCorner, setOverCorner] = useState(false);
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const measure = () => {
      const bounds = svg.getBoundingClientRect();
      const scale = Math.min(bounds.width / cell.width, bounds.height / cell.height);
      if (scale > 0) setUnitsPerPx(1 / scale);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(svg);
    return () => observer.disconnect();
  }, [zoomed, cell.width, cell.height]);
  const markSet = sets[0];
  const isSymbol = markSet?.recognitionMode === "symbol";
  const isChecklist = markSet?.recognitionMode === "checklist";
  const sections = [...new Set(targets.map((target) => target.section))];
  if (!markSet) {
    return (
      <fieldset className="mark-cell-editor generic-choice-editor">
        <legend>選択欄の設定</legend>
        <button type="button" className="primary compact" onClick={() => onChange(genericManualChoicePreset(cell))}>
          選択欄を設定する
        </button>
      </fieldset>
    );
  }
  const patchSet = (patch: Partial<LayoutMarkSet>) => onChange([
    { ...markSet, ...patch },
    ...sets.slice(1),
  ]);
  const patchOption = (optionId: string, patch: Partial<LayoutMarkSet["options"][number]>) => {
    patchSet({
      options: markSet.options.map((option) => (
        option.id === optionId ? { ...option, ...patch } : option
      )),
    });
  };
  const removeOption = (optionId: string) => patchSet({
    options: isSymbol || isChecklist
      ? markSet.options.filter((item) => item.id !== optionId)
      : markSet.options.filter((item) => item.id !== optionId),
  });
  const cleanedNewOption = newOptionLabel.trim();
  const duplicateNewOption = Boolean(cleanedNewOption) && markSet.options.some((option) => (
    option.label === cleanedNewOption || option.value === cleanedNewOption
  ));
  const addOption = () => {
    const label = cleanedNewOption;
    if (!label || duplicateNewOption || markSet.options.length >= 50) return;
    if (isChecklist && /[・、,;\n\r]/.test(label)) return;
    patchSet({ options: [...markSet.options, {
      id: nextChoiceOptionId(markSet.options), label, value: label, x: 0, y: 0, width: cell.width, height: cell.height,
    }] });
    setNewOptionLabel("");
  };
  // Choices of a printed-choice box are drawn on its image; the name is typed after.
  const pointInCell = (event: React.PointerEvent<SVGSVGElement>): Point | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const bounds = svg.getBoundingClientRect();
    const scale = Math.min(bounds.width / cell.width, bounds.height / cell.height);
    const offsetX = (bounds.width - cell.width * scale) / 2, offsetY = (bounds.height - cell.height * scale) / 2;
    return {
      x: Math.min(cell.width, Math.max(0, (event.clientX - bounds.left - offsetX) / scale)),
      y: Math.min(cell.height, Math.max(0, (event.clientY - bounds.top - offsetY) / scale)),
    };
  };
  const rectFrom = (a: Point, b: Point): Rect => ({
    x: Math.round(Math.min(a.x, b.x)), y: Math.round(Math.min(a.y, b.y)),
    width: Math.round(Math.abs(a.x - b.x)), height: Math.round(Math.abs(a.y - b.y)),
  });
  // Choices under the point, topmost first; the picked one counts as topmost.
  const hitsAt = (point: Point) => [...markSet.options].reverse().filter(option => point.x >= option.x
    && point.x <= option.x + option.width && point.y >= option.y && point.y <= option.y + option.height)
    .sort((a, b) => Number(b.id === picked) - Number(a.id === picked));
  const hitAt = (point: Point) => hitsAt(point)[0];
  // The handle sits on each choice's bottom-right corner (about 12px on screen); grabbing near it resizes.
  const handleSize = 12 * unitsPerPx;
  const cornerAt = (point: Point) => {
    const reach = handleSize * 0.75;
    // Outside the corner the full reach counts; inside only a third of the choice, so a small one can still be moved.
    const near = (option: Rect) => {
      const dx = point.x - (option.x + option.width), dy = point.y - (option.y + option.height);
      return dx <= reach && dy <= reach && dx >= -Math.min(reach, option.width / 3) && dy >= -Math.min(reach, option.height / 3);
    };
    // The nearest corner wins, so a corner can be grabbed even where choices overlap.
    const distance = (option: Rect) => Math.hypot(point.x - (option.x + option.width), point.y - (option.y + option.height));
    const candidates = markSet.options.filter(near);
    return candidates.find(option => option.id === picked)
      ?? candidates.sort((a, b) => distance(a) - distance(b))[0];
  };
  const moveTo = (point: Point): Rect | null => {
    const drag = moveRef.current;
    if (!drag) return null;
    const dx = point.x - drag.start.x, dy = point.y - drag.start.y, o = drag.origin;
    if (drag.mode === "resize") {
      const width = Math.round(Math.max(2, Math.min(cell.width - o.x, o.width + dx)));
      const height = Math.round(Math.max(2, Math.min(cell.height - o.y, o.height + dy)));
      return { x: o.x, y: o.y, width, height };
    }
    return {
      x: Math.round(Math.max(0, Math.min(cell.width - o.width, o.x + dx))),
      y: Math.round(Math.max(0, Math.min(cell.height - o.height, o.y + dy))),
      width: o.width, height: o.height,
    };
  };
  const deleteHighlighted = () => {
    if (!highlighted) return;
    removeOption(highlighted);
    setPicked(null);
    setHighlighted(null);
  };
  keyRef.current = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.isContentEditable) return;
      if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); deleteHighlighted(); }
      else if (event.key === "p") setTool("pen");
      else if (event.key === "m") setTool("move");
      else if (event.key === "e") setTool("erase");
  };
  const finishDraw = () => {
    const start = dragStart.current;
    dragStart.current = null;
    const area = draft;
    setDraft(null);
    if (!start) return;
    if (!area || area.width < 4 || area.height < 4) {
      // A click (no drag) picks the choice under it.
      const hit = [...markSet.options].reverse().find(option => start.x >= option.x && start.x <= option.x + option.width
        && start.y >= option.y && start.y <= option.y + option.height);
      setHighlighted(hit?.id ?? null);
      setPicked(hit?.id ?? null);
      if (hit) onChoiceSelect?.(markSet.id, hit.id);
      return;
    }
    if (markSet.options.length >= 50) return;
    const id = newMarkOptionId();
    // A choice always has a name (the server refuses an empty one); the person renames it next.
    const taken = new Set(markSet.options.flatMap(option => [option.label, option.value]));
    let number = markSet.options.length + 1;
    while (taken.has(`選択肢${number}`)) number++;
    patchSet({ options: [...markSet.options, {
      id, label: `選択肢${number}`, value: `選択肢${number}`, x: area.x, y: area.y,
      width: Math.max(1, Math.min(cell.width - area.x, area.width)),
      height: Math.max(1, Math.min(cell.height - area.y, area.height)),
    }] });
    setFocusOptionId(id);
  };
  const ordered = markSet.options;
  const numberOf = new Map(ordered.map((option, index) => [option.id, index]));
  const showImage = !isSymbol && !isChecklist;
  // While the model reads the box, a spinner sits over its image.
  const thinking = suggesting ? (
    <div className="mark-option-thinking" role="status"><span className="ai-spinner" aria-hidden="true" />AIが選択肢を読んでいます…</div>
  ) : null;
  const canvas = (
      <svg
        ref={svgRef}
        className={`mark-option-canvas${draft ? " drawing" : ""}${zoomed ? " zoomed" : ""} tool-${activeTool}`}
        viewBox={`0 0 ${cell.width} ${cell.height}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ aspectRatio: `${cell.width} / ${cell.height}`, ...(activeTool === "move" && (overCorner || moving && moveRef.current?.mode === "resize") ? { cursor: "nwse-resize" } : {}) }}
        aria-label={zoomed ? "選択欄の画像。ツールで選択肢を囲む・動かす・消す。" : "選択欄の画像。押すと拡大して編集します。"}
        role={zoomed ? undefined : "button"}
        tabIndex={zoomed ? undefined : 0}
        onKeyDown={zoomed ? undefined : (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setZoomed(true); } }}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          // The small preview in the sidebar only opens the enlarged panel; editing happens there.
          if (!zoomed) { event.preventDefault(); setZoomed(true); return; }
          const point = pointInCell(event);
          if (!point) return;
          if (activeTool === "erase") {
            const hit = hitAt(point);
            if (hit) { removeOption(hit.id); setHighlighted(null); }
            return;
          }
          if (activeTool === "move") {
            const corner = cornerAt(point);
            const hits = hitsAt(point);
            const hit = corner ?? hits[0];
            setHighlighted(hit?.id ?? null);
            setPicked(hit?.id ?? null);
            if (!hit) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            // Clicking the picked choice again where others overlap it picks the next one underneath.
            const cycle = !corner && hits.length > 1 && hit.id === picked ? hits[1].id : null;
            moveRef.current = { id: hit.id, mode: corner ? "resize" : "move", start: point,
              origin: { x: hit.x, y: hit.y, width: hit.width, height: hit.height }, cycle };
            setMoving({ id: hit.id, rect: moveRef.current.origin });
            return;
          }
          event.currentTarget.setPointerCapture(event.pointerId);
          dragStart.current = point;
          setDraft({ ...point, width: 0, height: 0 });
        }}
        onPointerMove={(event) => {
          const point = pointInCell(event);
          if (!point) return;
          if (moveRef.current) {
            const rect = moveTo(point);
            if (rect) setMoving({ id: moveRef.current.id, rect });
            return;
          }
          if (activeTool === "move") setOverCorner(Boolean(cornerAt(point)));
          if (dragStart.current) setDraft(rectFrom(dragStart.current, point));
        }}
        onPointerUp={() => {
          const drag = moveRef.current;
          if (drag) {
            moveRef.current = null;
            const rect = moving?.id === drag.id ? moving.rect : null;
            setMoving(null);
            if (rect && (rect.x !== drag.origin.x || rect.y !== drag.origin.y
              || rect.width !== drag.origin.width || rect.height !== drag.origin.height)) patchOption(drag.id, rect);
            else if (drag.cycle) { setPicked(drag.cycle); setHighlighted(drag.cycle); }
            return;
          }
          finishDraw();
        }}
        onPointerLeave={() => setOverCorner(false)}
        onPointerCancel={() => { dragStart.current = null; setDraft(null); moveRef.current = null; setMoving(null); }}
      >
        <image
          href={source}
          x={-cell.x}
          y={-cell.y}
          width={canonicalWidth}
          height={canonicalHeight}
          preserveAspectRatio="none"
        />
        {ordered.map((stored, index) => [stored, index] as const)
          .sort(([a], [b]) => Number(a.id === picked) - Number(b.id === picked))
          .map(([stored, index]) => {
          const option = moving?.id === stored.id ? { ...stored, ...moving.rect } : stored;
          return (
          <g key={option.id} className={`mark-option-region${selectedChoiceId === option.id || highlighted === option.id || picked === option.id ? " active" : ""}`}>
            <rect x={option.x} y={option.y} width={option.width} height={option.height} />
            <text x={option.x + 0.5} y={option.y + 0.5} style={{ fontSize: Math.max(4, Math.min(option.height * 0.32, cell.height * 0.13)) }}>
              {choiceNumber(index)}
            </text>
          </g>
          );
        })}
        {activeTool === "move" && [...ordered].sort((a, b) => Number(a.id === picked) - Number(b.id === picked)).map(stored => {
          const option = moving?.id === stored.id ? { ...stored, ...moving.rect } : stored;
          // Small choices get a smaller square so it does not hide them; the grab area stays the same.
          const size = Math.min(handleSize, Math.max(6 * unitsPerPx, Math.min(option.width, option.height) * 0.7));
          return <rect key={`handle-${option.id}`} className="mark-option-handle" x={option.x + option.width - size / 2}
            y={option.y + option.height - size / 2} width={size} height={size} />;
        })}
        {draft && <rect className="mark-option-draft" x={draft.x} y={draft.y} width={draft.width} height={draft.height} />}
      </svg>
  );
  return (
    <fieldset className="mark-cell-editor generic-choice-editor">
      <legend>{isSymbol ? "記号の候補" : isChecklist ? "チェック項目" : "選択肢"}</legend>
      {!hideTarget && <label>
        保存する情報
        <select
          value={markSet.targetPath}
          onChange={(event) => patchSet({ targetPath: event.target.value })}
        >
          <option value="">保存先を選択</option>
          {sections.map((section) => (
            <optgroup label={section} key={section}>
              {targets.filter((target) => target.section === section).map((target) => (
                <option value={target.path} key={target.path}>{target.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>}
      {showImage && (zoomed ? <div className="mark-option-thumb" aria-hidden="true" /> : <div className="mark-option-frame" title="押すと拡大して編集します">
        {canvas}
        {thinking}
      </div>)}
      {showImage && zoomed && createPortal(<div className="mark-option-zoom" role="dialog" aria-label="選択肢を囲む">
        <div className="mark-option-zoom-stage">
          <div className="mark-option-zoom-canvas">{canvas}{thinking}</div>
          {/* The same floating column as the form's own editing tools. */}
          <div className="layout-floating-tools mark-option-floating">
            <div className="layout-commandbar" role="toolbar" aria-orientation="vertical" aria-label="選択肢の編集">
              {([["pen", FiEdit2, "ペン", "ドラッグで選択肢を囲む（P）"], ["move", FiMove, "移動", "枠をドラッグで移動、右下の角で大きさを変更（M）"],
                ["erase", FiTrash2, "消しゴム", "押した枠を削除（E）"]] as const).map(([key, Icon, label, tip]) => (
                <button type="button" key={key} className={`mode-button-with-icon${tool === key ? " active" : ""}`}
                  aria-pressed={tool === key} title={tip} onClick={() => setTool(key)}>
                  <Icon aria-hidden="true" />{label}
                </button>
              ))}
              <button type="button" className="mode-button-with-icon mark-option-delete" disabled={!highlighted} title="選んでいる枠を削除（Delete）"
                onClick={deleteHighlighted}><FiTrash2 aria-hidden="true" />選んだ枠を削除</button>
              {onSuggest && <>
                <span className="mark-option-tools-rule" aria-hidden="true" />
                <button type="button" className={`mode-button-with-icon mark-option-ai${suggesting ? " is-thinking" : ""}`} disabled={suggesting}
                  title={ordered.length ? "一覧の名前を手がかりに、AIが印字を読んで位置を合わせます。" : "AIが印字を読んで選択肢を作ります。"}
                  onClick={() => onSuggest(namesHint(ordered.map(option => option.label)))}>
                  {suggesting ? <span className="ai-spinner" aria-hidden="true" /> : <FiCpu aria-hidden="true" />}
                  {suggesting ? "AIが読んでいます…" : ordered.length ? "AIで読み直す" : "AIで読む"}
                </button>
                <button type="button" className="mode-button-with-icon" disabled={suggesting || !onUndoSuggest} onClick={onUndoSuggest}
                  title="AIで読む前の選択肢に戻します"><FiRotateCcw aria-hidden="true" />元に戻す</button>
              </>}
            </div>
          </div>
          <button type="button" className="mark-option-zoom-close" aria-label="閉じる" title="閉じる（Esc）" onClick={() => setZoomed(false)}><FiX aria-hidden="true" /></button>
        </div>
        <p className="mark-option-zoom-hint">{ordered.length}件。{tool === "pen" ? "ドラッグで囲む（枠の上からでも可）" : tool === "move" ? "枠をドラッグで移動、右下の角で大きさ（重なった枠はもう一度押すと下の枠を選ぶ）" : "押した枠を削除"}。名前は右の一覧で入力します。</p>
      </div>, document.body)}
      {showImage && <p className="choice-hint">
        {ordered.length ? `${ordered.length}件の選択肢。` : "選択肢がありません。"}
        画像を押すと拡大して、選択肢を囲む・動かす・消すことができます。
      </p>}
      {!showImage && <p>
        {isSymbol ? "欄全体の画像から記号を1つ判定します。" : "欄全体を読み、チェックされた項目を取得します。"}
      </p>}
      {isSymbol && <label title="「その他（　）」などの記入欄。確認画面で記号と並べて表示します。">
        記入欄
        <select
          value={markSet.options.find((option) => option.writeInTargetPath)?.writeInTargetPath ?? ""}
          onChange={(event) => {
            const path = event.target.value || undefined;
            // One symbol (○, or the first) holds the item's write-in.
            const holder = markSet.options.find((option) => option.label === "○") ?? markSet.options[0];
            patchSet({ options: markSet.options.map(({ writeInTargetPath: _old, ...option }) => (
              path && option.id === holder?.id ? { ...option, writeInTargetPath: path } : option
            )) });
          }}
        >
          <option value="">なし</option>
          {writeInTargets.map((target) => <option value={target.path} key={target.path}>{target.label}</option>)}
        </select>
      </label>}
      <ul className="choice-list" aria-label={isSymbol ? "記号" : "選択肢"}>
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={(event: DragEndEvent) => {
          const { active, over } = event;
          if (!over || active.id === over.id) return;
          const ids = markSet.options.map(option => option.id);
          patchSet({ options: arrayMove(markSet.options, ids.indexOf(String(active.id)), ids.indexOf(String(over.id))) });
        }}>
          <SortableContext items={markSet.options.map(option => option.id)} strategy={verticalListSortingStrategy}>
        {ordered.map((option) => (
          <SortableChoice key={option.id} id={option.id} label={option.label}
            className={selectedChoiceId === option.id || highlighted === option.id ? "active" : undefined}
            onMouseEnter={() => showImage && setHighlighted(option.id)} onMouseLeave={() => showImage && setHighlighted(null)}
            number={choiceNumber(numberOf.get(option.id) ?? 0)}>
            <input
              aria-label={isSymbol ? "記号" : "選択肢の名前"}
              value={option.label}
              maxLength={100}
              placeholder={showImage ? "名前を入力" : undefined}
              autoFocus={focusOptionId === option.id}
              onFocusCapture={(event) => { if (focusOptionId === option.id) event.currentTarget.select(); }}
              onFocus={() => { if (showImage) setPicked(option.id); else onChoiceSelect?.(markSet.id, option.id); }}
              onBlur={() => {
                if (focusOptionId === option.id) setFocusOptionId(null);
                // A name another choice already has is refused on save; number it instead.
                if (option.label.trim() && markSet.options.some(other => other.id !== option.id && other.label === option.label)) {
                  const taken = new Set(markSet.options.filter(other => other.id !== option.id).map(other => other.label));
                  let number = 2;
                  while (taken.has(`${option.label}${number}`)) number++;
                  patchOption(option.id, { label: `${option.label}${number}`, ...(option.value === option.label ? { value: `${option.label}${number}` } : {}) });
                }
                // An emptied name is refused on save; give it a placeholder name instead.
                if (!option.label.trim()) {
                  const taken = new Set(markSet.options.flatMap(other => [other.label, other.value]));
                  let number = (numberOf.get(option.id) ?? 0) + 1;
                  while (taken.has(`選択肢${number}`)) number++;
                  patchOption(option.id, { label: `選択肢${number}`, value: `選択肢${number}` });
                }
              }}
              onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) (event.target as HTMLInputElement).blur(); }}
              onChange={(event) => {
                const label = isChecklist ? event.target.value.replace(/[・、,;\n\r]/g, "／") : event.target.value;
                // The saved value follows the name unless it was set apart earlier.
                patchOption(option.id, { label, ...((isSymbol || isChecklist || option.value === option.label) ? { value: label } : {}) });
              }}
            />
            {isChecklist && <select aria-label={`${option.label}の記入欄`} title="この記入欄に文字があれば、チェックありとして扱います。"
              value={option.writeInTargetPath ?? ""}
              onChange={(event) => patchOption(option.id, { writeInTargetPath: event.target.value || undefined })}>
              <option value="">記入欄なし</option>
              {writeInTargets.map((target) => (
                <option value={target.path} key={target.path}
                  disabled={markSet.options.some((other) => other.id !== option.id && other.writeInTargetPath === target.path)}>
                  {target.label}
                </option>
              ))}
            </select>}
            <button type="button" className="choice-remove" aria-label={`${option.label || "選択肢"}を削除`} title="削除"
              onClick={() => removeOption(option.id)}>×</button>
          </SortableChoice>
        ))}
          </SortableContext>
        </DndContext>
        {<li className="choice-add">
          <input
            aria-label={isSymbol ? "新しい記号" : "新しい選択肢"}
            value={newOptionLabel}
            maxLength={100}
            placeholder={isSymbol ? "記号を追加（Enter）" : isChecklist ? "項目を追加（Enter）" : "選択肢の名前を追加（Enter）"}
            onChange={(event) => setNewOptionLabel(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); addOption(); } }}
          />
          <button type="button" className="secondary compact" onClick={addOption}
            disabled={!cleanedNewOption || duplicateNewOption || (isChecklist && /[・、,;\n\r]/.test(cleanedNewOption)) || markSet.options.length >= 50}>追加</button>
        </li>}
      </ul>
      {(duplicateNewOption || new Set(ordered.map(option => option.label)).size < ordered.length)
        && <small className="error-text">同じ名前の選択肢があります（入力を終えると番号を付けます）。</small>}
      {showImage && <>
        <ChoiceSegments label="選択数" value={markSet.selectionMode}
          options={SELECTION_CHOICES} onChange={(selectionMode) => patchSet({ selectionMode })} />
        {onSuggest && <div className="choice-suggest">
          <button type="button" className="secondary compact" disabled={suggesting}
            title={ordered.length ? "一覧の名前を手がかりに、AIが印字を読んで位置を合わせます。" : "AIが印字を読んで選択肢を作ります。"}
            onClick={() => onSuggest(namesHint(ordered.map(option => option.label)))}>
            {suggesting && <span className="ai-spinner" aria-hidden="true" />}
            {suggesting ? "AIが読んでいます…" : ordered.length ? "AIで読み直す" : "AIで選択肢を読む"}
          </button>
          <button type="button" className="secondary compact" disabled={suggesting || !onUndoSuggest} onClick={onUndoSuggest}
            title="AIで読む前の選択肢に戻します"><FiRotateCcw aria-hidden="true" />元に戻す</button>
        </div>}
        {similarOffer && similarOffer.count > 0 && <div className="similar-offer">
          <span>同じ見た目の欄があと{similarOffer.count}件あります。</span>
          <button type="button" className="primary compact" onClick={similarOffer.apply}>{similarOffer.count}件に同じ選択肢を設定</button>
        </div>}
      </>}
    </fieldset>
  );
}

/**
 * Choice names must differ within a box (the server refuses the whole draft otherwise);
 * a repeated name gets a number. Returns null when nothing had to change.
 */
export function uniqueChoiceLabels(sets: LayoutMarkSet[]): LayoutMarkSet[] | null {
  let changed = false;
  const fixed = sets.map(set => {
    const taken = new Set<string>();
    const options = set.options.map(option => {
      if (!taken.has(option.label)) { taken.add(option.label); return option; }
      let number = 2;
      while (taken.has(`${option.label}${number}`)) number++;
      const label = `${option.label}${number}`;
      taken.add(label);
      changed = true;
      return { ...option, label, ...(option.value === option.label ? { value: label } : {}) };
    });
    return { ...set, options };
  });
  return changed ? fixed : null;
}

/** The names typed in the list tell the model how many choices to look for and what they say. */
export function namesHint(labels: string[]): string {
  const names = labels.map(label => label.trim()).filter(label => label && !/^選択肢\d+$/.test(label));
  return names.length ? `選択肢は${names.length}つ: ${names.join("、")}`.slice(0, 200) : "";
}

/** One row of the choice list; only the grip drags, so the name can still be typed. */
function SortableChoice({ id, label, number, className, onMouseEnter, onMouseLeave, children }: {
  id: string; label: string; number: string; className?: string;
  onMouseEnter: () => void; onMouseLeave: () => void; children: React.ReactNode;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  return (
    <li ref={setNodeRef} className={`${className ?? ""}${isDragging ? " dragging" : ""}`}
      style={{ transform: CSS.Transform.toString(transform), transition }} onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}>
      <span className="choice-grip" {...attributes} {...listeners} aria-label={`${label || "選択肢"}を並べ替える`} title="ドラッグで並べ替え">
        <span aria-hidden="true">⠿</span>
      </span>
      <span className="choice-number" aria-hidden="true">{number}</span>
      {children}
    </li>
  );
}

const SELECTION_CHOICES = [["single", "1つだけ"], ["multiple", "複数可"]] as const;
function ChoiceSegments<T extends string>({ label, value, options, onChange }: {
  label: string; value: T | null; options: readonly (readonly [T, string])[]; onChange: (value: T) => void;
}) {
  return (
    <div className="choice-segments" role="group" aria-label={label}>
      <span>{label}</span>
      <div>
        {options.map(([key, text]) => (
          <button type="button" key={key} aria-pressed={value === key} onClick={() => onChange(key)}>{text}</button>
        ))}
      </div>
    </div>
  );
}

/** One setting for many selection boxes at once; symbol and checklist boxes keep their own reading. */
export function MarkBulkEditor({ cells, onApply, onSuggest, suggesting = false, onUndoSuggest, similarOffer = null }: {
  cells: LayoutCell[];
  onApply: (markSets: Map<string, LayoutMarkSet[]>, note: string) => void;
  onSuggest?: (cellIds: string[], hint: string) => void;
  suggesting?: boolean;
  onUndoSuggest?: () => void;
  similarOffer?: { count: number; apply: () => void } | null;
}) {
  const editable = cells.filter((cell) => !cell.markSets?.length
    || (cell.markSets.length === 1 && !["symbol", "checklist"].includes(cell.markSets[0].recognitionMode ?? "")));
  const skipped = cells.length - editable.length;
  // The choice names every selected box already shares, so the field shows them as they are.
  const sharedNames = (() => {
    const lists = new Set(editable.map((cell) => (cell.markSets?.[0]?.options ?? []).map((option) => option.label).join("、")));
    return lists.size === 1 ? [...lists][0] : "";
  })();
  const [names, setNames] = useState(sharedNames);
  const [shownFor, setShownFor] = useState(sharedNames);
  if (shownFor !== sharedNames) { setShownFor(sharedNames); setNames(sharedNames); }
  const selection = (() => {
    const values = new Set(editable.map((cell) => cell.markSets?.[0]?.selectionMode ?? "single"));
    return values.size === 1 ? [...values][0] : null;
  })();
  if (editable.length === 0) return null;
  const setsOf = (cell: LayoutCell) => cell.markSets?.length ? cell.markSets : genericManualChoicePreset(cell);
  const apply = (patch: Partial<LayoutMarkSet>, text: string) => {
    const updates = new Map(editable.map((cell) => {
      const sets = setsOf(cell);
      return [cell.id, [{ ...sets[0], ...patch }, ...sets.slice(1)]] as [string, LayoutMarkSet[]];
    }));
    onApply(updates, `選択欄${updates.size}件の${text}`);
  };
  const labels = [...new Set(names.split(/[、,，・/／\s　]+/).map((label) => label.trim()).filter(Boolean))].slice(0, 50);
  const applyNames = () => {
    if (!labels.length) return;
    const updates = new Map(editable.map((cell) => {
      const sets = setsOf(cell);
      // A box that already had a choice of the same name keeps its ID and area.
      const kept = new Map((sets[0].options ?? []).map((option) => [option.label, option]));
      const options = reflowChoiceOptions(cell, labels.map((label) => kept.get(label) ?? {
        id: newMarkOptionId(), label, value: label, x: 0, y: 0, width: 1, height: Math.max(1, cell.height),
      }));
      return [cell.id, [{ ...sets[0], options }, ...sets.slice(1)]] as [string, LayoutMarkSet[]];
    }));
    onApply(updates, `選択肢を「${labels.join("・")}」にしました。`);
  };
  return (
    <fieldset className="mark-cell-editor mark-bulk-editor">
      <legend>選択欄 {editable.length}件をまとめて設定</legend>
      {onSuggest && <div className="choice-suggest">
        <button type="button" className="secondary compact" disabled={suggesting}
          title="下の選択肢の名前を手がかりに、AIが印字を読んで位置を合わせます。"
          onClick={() => onSuggest(editable.map((cell) => cell.id), namesHint(labels))}>
          {suggesting ? "AIが読んでいます…" : "AIで選択肢を読む"}
        </button>
        <button type="button" className="secondary compact" disabled={suggesting || !onUndoSuggest} onClick={onUndoSuggest}
          title="AIで読む前の選択肢に戻します"><FiRotateCcw aria-hidden="true" />元に戻す</button>
      </div>}
      {similarOffer && similarOffer.count > 0 && <div className="similar-offer">
        <span>同じ見た目の欄があと{similarOffer.count}件あります。</span>
        <button type="button" className="primary compact" onClick={similarOffer.apply}>{similarOffer.count}件に同じ選択肢を設定</button>
      </div>}
      <label className="bulk-choice-names">
        選択肢（「、」やスペースで区切る）
        <span>
          <input value={names} maxLength={500} placeholder="例: 安産、介助、難産"
            onChange={(event) => setNames(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); applyNames(); } }} />
          <button type="button" className="secondary compact" disabled={!labels.length || labels.join("、") === sharedNames}
            onClick={applyNames}>全件に設定</button>
        </span>
      </label>
      <ChoiceSegments label="選択数" value={selection}
        options={SELECTION_CHOICES} onChange={(selectionMode) => apply({ selectionMode },
          `選択数を「${SELECTION_CHOICES.find(([key]) => key === selectionMode)![1]}」にしました。`)} />
      {skipped > 0 && <p>記号・チェックリストの{skipped}件は対象外です。</p>}
    </fieldset>
  );
}
