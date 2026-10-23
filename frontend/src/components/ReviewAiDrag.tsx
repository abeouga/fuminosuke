import { createContext, useContext, useLayoutEffect, useRef, useState, type HTMLAttributes, type ReactNode } from "react";
import { DndContext, DragOverlay, PointerSensor, pointerWithin, useDndContext, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent, type DragStartEvent } from "@dnd-kit/core";

/** The AI toolbar's drop area: a result cell or item dropped here is quoted in the typed instruction. */
export const AI_TOOLBAR_DROP_ID = "ai-toolbar";
const Enabled = createContext(false);
/**
 * What a drag carries: a cell or item by its result path (the page describes it), or a column/row
 * with its own names.
 */
export type AiDragData = { kind: AiAttachment["kind"]; path?: string; title?: string; detail?: string };
/** A cell attached to the typed instruction: shown as a small card, sent as 「detail title」. */
export type AiAttachment = { kind: "cell" | "item" | "column" | "row"; quote: string; title: string; detail: string; value: string; tone: "ok" | "warning" | "blank" | "group"; path?: string };

/**
 * Lets result cells and item names be dragged onto the floating AI toolbar. A short move (6px)
 * starts the drag, so clicking and double-clicking work as before. The dragged piece is a copy
 * of the cell itself, lifted out of the table like a puzzle piece.
 */
export function ReviewAiDragProvider({ enabled, describe, onDrop, children }: {
  enabled: boolean; describe: (data: AiDragData) => AiAttachment | null; onDrop: (attachment: AiAttachment) => void; children: ReactNode;
}) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const [piece, setPiece] = useState<HTMLElement | null>(null);
  // The cells an item, column or row owns gather onto the piece like a stack of cards.
  const [stack, setStack] = useState<string[]>([]);
  const stopGathering = useRef<() => void>(() => undefined);
  function start(event: DragStartEvent) {
    const source = document.querySelector<HTMLElement>(`[data-ai-drag-id="${CSS.escape(String(event.active.id))}"]`);
    setPiece(source ? snapshot(source) : null);
    const cells = source ? ownedCells(source) : [];
    setStack([]);
    stopGathering.current = gather(cells, color => setStack(current => [...current, color]));
  }
  function finish() { stopGathering.current(); stopGathering.current = () => undefined; setPiece(null); setStack([]); }
  function end(event: DragEndEvent) {
    finish();
    const data = event.active.data.current as AiDragData | undefined;
    const attachment = event.over?.id === AI_TOOLBAR_DROP_ID && data ? describe(data) : null;
    if (attachment) onDrop(attachment);
  }
  // No auto-scroll: the toolbar sits at the bottom edge and must not run away from the pointer.
  return <Enabled.Provider value={enabled}>
    <DndContext sensors={sensors} autoScroll={false} collisionDetection={pointerWithin} onDragStart={start} onDragEnd={end} onDragCancel={finish}>
      {children}
      <DragOverlay dropAnimation={null}>{piece && <LiftedPiece piece={piece} stack={stack} />}</DragOverlay>
    </DndContext>
  </Enabled.Provider>;
}

function LiftedPiece({ piece, stack }: { piece: HTMLElement; stack: string[] }) {
  const host = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { host.current?.replaceChildren(piece); }, [piece]);
  const behind = stack.slice(-3);
  return <div className="ai-drag-piece" style={{ width: piece.dataset.width + "px", height: piece.dataset.height + "px" }}>
    {behind.map((color, index) => <span key={stack.length - behind.length + index} className="ai-drag-card" aria-hidden
      style={{ background: color, transform: `translate(${(behind.length - index) * 5}px, ${(behind.length - index) * 5}px)` }} />)}
    <div ref={host} className="ai-drag-face" />
  </div>;
}

const TRANSPARENT = /^rgba\(.*, 0\)$|^transparent$/;

/** The cells a header owns: a column header the cells below it, an item name or row number those to its right. */
function ownedCells(source: HTMLElement): HTMLElement[] {
  if (!(source instanceof HTMLTableCellElement) || source.tagName !== "TH") return [];
  const table = source.closest("table");
  if (source.closest("thead") && table) {
    const index = source.cellIndex;
    return [...table.tBodies].flatMap(body => [...body.rows]).map(row => row.cells[index]).filter(cell => cell?.tagName === "TD");
  }
  const cells: HTMLElement[] = [];
  for (let cell = source.nextElementSibling; cell instanceof HTMLElement && !(cell.tagName === "TH" && cell.dataset.aiDragId); cell = cell.nextElementSibling)
    if (cell.tagName === "TD" && !cell.classList.contains("ocr-empty-cell")) cells.push(cell);
  return cells;
}

/**
 * Copies of the owned cells slide from their places onto the lifted piece one after another and
 * join its stack; their places stay empty while the drag lasts. Returns a function that stops and
 * puts everything back.
 */
function gather(cells: HTMLElement[], arrived: (color: string) => void): () => void {
  if (!cells.length) return () => undefined;
  cells.forEach(cell => cell.setAttribute("data-ai-gathered", ""));
  const restore = () => cells.forEach(cell => cell.removeAttribute("data-ai-gathered"));
  const color = (cell: HTMLElement) => { const value = getComputedStyle(cell).backgroundColor; return TRANSPARENT.test(value) ? "#fff" : value; };
  const onScreen = (cell: HTMLElement) => { const box = cell.getBoundingClientRect(); return box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth; };
  // Cells out of sight join at once; only those on screen fly (none when motion is reduced).
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const visible = still ? [] : cells.filter(onScreen);
  cells.filter(cell => !visible.includes(cell)).forEach(cell => arrived(color(cell)));
  if (!visible.length) return restore;
  const layer = document.createElement("div");
  layer.className = "ai-gather-layer";
  document.body.appendChild(layer);
  const step = Math.min(15, 150 / visible.length), duration = 200, began = performance.now();
  const flights = visible.map((cell, index) => {
    const from = cell.getBoundingClientRect(), card = snapshot(cell), tint = color(cell);
    card.classList.add("ai-gather-card");
    Object.assign(card.style, { left: `${from.left}px`, top: `${from.top}px` });
    layer.appendChild(card);
    return { from, card, tint, delay: index * step, done: false };
  });
  let frame = requestAnimationFrame(function tick(now) {
    const target = document.querySelector(".ai-drag-piece")?.getBoundingClientRect();
    for (const flight of flights) {
      if (flight.done) continue;
      const t = Math.min(1, Math.max(0, (now - began - flight.delay) / duration));
      if (!target || t === 0) continue;
      const ease = 1 - (1 - t) ** 3, { from } = flight;
      const x = (target.left - from.left) * ease, y = (target.top - from.top) * ease;
      const sx = 1 + (target.width / from.width - 1) * ease, sy = 1 + (target.height / from.height - 1) * ease;
      flight.card.style.transform = `translate(${x}px, ${y}px) scale(${sx}, ${sy}) rotate(${-2 * ease}deg)`;
      flight.card.style.opacity = String(1 - 0.35 * ease);
      if (t === 1) { flight.done = true; flight.card.remove(); arrived(flight.tint); }
    }
    frame = flights.every(flight => flight.done) ? 0 : requestAnimationFrame(tick);
  });
  return () => { if (frame) cancelAnimationFrame(frame); layer.remove(); restore(); };
}

/** A copy of a table cell with its look written inline, so it renders the same outside its table. */
function snapshot(source: HTMLElement): HTMLElement {
  const box = source.getBoundingClientRect();
  const copy = (from: Element): Element => {
    const to = from.cloneNode(false) as Element;
    if (to instanceof HTMLElement || to instanceof SVGElement) {
      const style = getComputedStyle(from);
      for (const name of Array.from(style)) to.style.setProperty(name, style.getPropertyValue(name));
      // The copy is not a hole nor something to drag: only the look is kept.
      for (const name of ["id", "data-ai-gathered", "data-ai-drag-id", "data-review-path"]) to.removeAttribute(name);
    }
    from.childNodes.forEach(child => to.appendChild(child.nodeType === Node.ELEMENT_NODE ? copy(child as Element) : child.cloneNode(false)));
    return to;
  };
  const cell = copy(source) as HTMLElement;
  // A lone td/th lays itself out as a table of one, at exactly the size it had in the table.
  const table = document.createElement("div");
  Object.assign(table.style, { display: "table", width: `${box.width}px`, height: `${box.height}px`, tableLayout: "fixed", borderCollapse: "collapse" });
  Object.assign(cell.style, { display: "table-cell", position: "static", width: `${box.width}px`, height: `${box.height}px`, boxSizing: "border-box" });
  // A cell without its own background shows its row's (or table's) behind it.
  for (let at: HTMLElement | null = source; at && TRANSPARENT.test(cell.style.backgroundColor); at = at.parentElement) {
    const color = getComputedStyle(at).backgroundColor;
    if (!TRANSPARENT.test(color)) cell.style.backgroundColor = color;
  }
  table.appendChild(cell);
  table.dataset.width = String(box.width); table.dataset.height = String(box.height);
  return table;
}

/**
 * Makes a result cell or item name draggable to the AI toolbar (only while editing on the review
 * screen). `holder` goes on the td/th whose look is lifted; `handle` on what the pointer grabs.
 */
type Handle = { ref?: (element: HTMLElement | null) => void } & Partial<Record<string, (event: never) => void>>;
export function useAiDraggable(id: string, data: AiDragData, disabled = false) {
  const enabled = useContext(Enabled);
  const { setNodeRef, listeners, isDragging } = useDraggable({ id, data, disabled: !enabled || disabled });
  if (!enabled || disabled) return { holder: {}, handle: {} as Handle, dragging: false, active: false };
  return { holder: { "data-ai-drag-id": id }, handle: { ref: setNodeRef, ...listeners } as Handle, dragging: isDragging, active: true };
}

/** A header cell (item name, column, row number) that can be pulled out to the AI toolbar. */
export function AiDragTh({ id, data, className, children, ...rest }: { id: string; data: AiDragData } & HTMLAttributes<HTMLTableCellElement> & { scope?: string }) {
  const drag = useAiDraggable(id, data), { ref, ...listeners } = drag.handle;
  return <th {...rest} {...drag.holder} ref={ref} {...listeners}
    className={[className, drag.active ? "is-ai-draggable" : "", drag.dragging ? "is-ai-dragging" : ""].filter(Boolean).join(" ") || undefined}
    title={drag.active ? "AIのツールバーへドラッグで指示に引用" : rest.title}>{children}</th>;
}

/** The toolbar side: where to drop, whether something is being dragged and whether it is over it. */
export function useAiDropTarget() {
  const { setNodeRef, isOver } = useDroppable({ id: AI_TOOLBAR_DROP_ID });
  const { active } = useDndContext();
  return { ref: setNodeRef, over: isOver, armed: Boolean(active) };
}
