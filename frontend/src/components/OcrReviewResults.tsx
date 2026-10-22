import { createContext, useCallback, type PointerEvent as ReactPointerEvent, type RefObject, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { FiArrowRight, FiEdit2, FiPenTool } from "react-icons/fi";
import { privateLocalStorage } from "../auth/transport";
import type { FieldReading, OcrFieldPreview, ReviewStructure, ReviewStructureField } from "../types";
import { displayName } from "../userMessages";
import { ITEM_JOIN_EDITING_VISIBLE } from "./TemplateItemJoinEditor";
import { joinedText, orderMark, sortBlocks, sortByOrder, type ItemJoin, type ItemJoinMember } from "../itemJoins";

/** `writeIn`: the 「その他（　）」 text a symbol/check box's item owns, shown on the same row. */
export type ReadingEntry = { path: string; label: string; value: FieldReading; writeIn?: ReadingEntry;
  /** The template's own name when a person renamed the item on the result screen. */
  originalLabel?: string };

const InlineEditActivity = createContext<(path: string, dirty: boolean) => void>(() => undefined);
/** Update of a confirmed document: newly filled cells and cells kept from the previous version. */
export const ReviewSeriesMarks = createContext<{ added: ReadonlySet<string>; carried: ReadonlySet<string> } | null>(null);
/** Checklist fields (path → their check items): the result shows ☑/☐ boxes, not the stored text. */
/**
 * Items shown as one value (path → its join; display only). `configure` opens the
 * template's 「項目のまとめ方・順番」 for one heading (its items, in the order listed here).
 */
export const ReviewJoins = createContext<{ byPath: ReadonlyMap<string, ItemJoin>; orders?: readonly ItemJoin[]; configure?: (paths: string[], anchor: HTMLElement) => void }>({ byPath: new Map() });
export const ReviewChecklists = createContext<ReadonlyMap<string, readonly string[]>>(new Map());
export function checklistOptionsByPath(previews: readonly OcrFieldPreview[]): Map<string, string[]> {
  return new Map(previews
    .filter((preview) => preview.markRecognitionMode === "checklist")
    .map((preview) => [preview.targetPath, (preview.markOptions ?? []).map((option) => option.label)]));
}

/** Checked items of a checklist reading (an undecided one counts what was read). */
function checkedItems(value: FieldReading): Set<string> {
  const text = value.status === "blank" ? "" : (value.normalized ?? value.raw ?? "");
  return new Set(text.split("・").map((item) => item.trim()).filter(Boolean));
}

/** Check boxes toggled in place: a click checks or unchecks, no popover. */
/** Check boxes only toggle: the cell is never selected and no dialog or crop opens. */
function ChecklistBoxes({ labels, entry, confirmed, onEdit }: {
  labels: readonly string[];
  entry: ReadingEntry;
  confirmed: boolean;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  const checked = checkedItems(entry.value);
  const toggle = (label: string) => {
    if (confirmed) return;
    const next = new Set(checked);
    if (next.has(label)) next.delete(label); else next.add(label);
    onEdit(entry, labels.filter((item) => next.has(item)).join("・"));
  };
  return <span className="ocr-checkbox-list">{labels.map((label) => {
    const on = checked.has(label);
    // A lone box (its only item is "チェック済み") is just the box.
    const name = labels.length === 1 ? entry.label : label;
    return (
      <button type="button" key={label} className={`ocr-checkbox-toggle${on ? " checked" : ""}`}
        role="checkbox" aria-checked={on} aria-label={name}
        title={confirmed ? undefined : on ? "クリックでチェックを外す" : "クリックでチェックを付ける"}
        onClick={() => toggle(label)}>
        <span className="ocr-checkbox-value" aria-hidden="true">{on ? "☑" : "☐"}</span>
        {labels.length > 1 && label}
      </button>
    );
  })}</span>;
}
/**
 * Cells a person traced over with the check pen (shown green). Display only: the reading,
 * its 要確認 status and reviewReason are never changed by it. Kept in this browser.
 */
export const ReviewPaint = createContext<ReadonlySet<string>>(new Set());

export function useReviewPaint(storageKey: string | null) {
  const key = storageKey ? `fuminosuke.reviewPaint.${storageKey}` : null;
  const read = (): Set<string> => {
    if (!key) return new Set();
    try {
      const stored: unknown = JSON.parse(privateLocalStorage.getItem(key) ?? "[]");
      return new Set(Array.isArray(stored) ? stored.filter((item): item is string => typeof item === "string") : []);
    } catch { return new Set(); }
  };
  const [painted, setPainted] = useState<ReadonlySet<string>>(read);
  const [loadedKey, setLoadedKey] = useState(key);
  if (loadedKey !== key) { setLoadedKey(key); setPainted(read()); }
  const paint = useCallback((path: string, on: boolean) => setPainted((current) => {
    if (current.has(path) === on) return current;
    const next = new Set(current);
    if (on) next.add(path); else next.delete(path);
    if (key) {
      try { privateLocalStorage.setItem(key, JSON.stringify([...next])); } catch { /* display only */ }
    }
    return next;
  }), [key]);
  return { painted, paint };
}

/**
 * The check pen: dragging over result cells paints them green (a stroke that starts on a
 * green cell takes the green off instead). Cell buttons and check boxes do not react meanwhile.
 * Just hovering a cell (no stroke) reports it through `onHover`, so its crop can be shown.
 */
export function ReviewPenSurface({ active, painted, onPaint, onExit, onHover, children }: {
  active: boolean;
  painted: ReadonlySet<string>;
  onPaint: (path: string, on: boolean) => void;
  onExit?: () => void;
  onHover?: (path: string) => void;
  children: ReactNode;
}) {
  const stroke = useRef<{ pointerId: number; on: boolean | null; x: number; y: number; wrap: HTMLElement | null } | null>(null);
  const scrollFrame = useRef<number | null>(null);
  const hovered = useRef<string | null>(null);
  useEffect(() => { if (!active) hovered.current = null; }, [active]);
  useEffect(() => () => { if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current); }, []);
  useEffect(() => {
    if (!active || !onExit) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onExit(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [active, onExit]);
  const hit = (x: number, y: number) => {
    const cell = document.elementFromPoint(x, y)?.closest<HTMLElement>("[data-review-path]");
    const current = stroke.current;
    const path = cell?.dataset.reviewPath;
    if (!path || !current) return;
    current.wrap = cell.closest<HTMLElement>(".ocr-edit-table-wrap") ?? current.wrap;
    // The first cell decides whether this stroke paints or takes the green off.
    if (current.on === null) current.on = !painted.has(path);
    onPaint(path, current.on);
  };
  // Near an edge the table (or the page) scrolls on by itself, so cells cut off below can be
  // painted too. The closer to the edge (or past it), the faster.
  const autoScroll = () => {
    scrollFrame.current = null;
    const current = stroke.current;
    if (!current) return;
    const EDGE = 48, MAX = 20;
    const speed = (distanceIn: number) => (distanceIn >= EDGE ? 0 : Math.round(MAX * Math.min(1, (EDGE - distanceIn) / EDGE)));
    const push = (position: number, start: number, end: number) => speed(position - start) > 0 ? -speed(position - start) : speed(end - position);
    let dy = 0, dx = 0, moved = false;
    const wrap = current.wrap;
    if (wrap) {
      const rect = wrap.getBoundingClientRect();
      const top = Math.max(rect.top, 0), bottom = Math.min(rect.bottom, window.innerHeight);
      dy = push(current.y, top, bottom);
      dx = push(current.x, Math.max(rect.left, 0), Math.min(rect.right, window.innerWidth));
      const beforeTop = wrap.scrollTop, beforeLeft = wrap.scrollLeft;
      if (dy || dx) wrap.scrollBy(dx, dy);
      moved = wrap.scrollTop !== beforeTop || wrap.scrollLeft !== beforeLeft;
    }
    // Past what the table itself can scroll, the page scrolls when the pointer is at the window's edge.
    const pageDy = push(current.y, 0, window.innerHeight);
    if (pageDy) {
      const page = document.scrollingElement ?? document.documentElement;
      const before = page.scrollTop;
      page.scrollBy(0, pageDy);
      moved = moved || page.scrollTop !== before;
    }
    if (moved) hit(current.x, current.y);
    scrollFrame.current = requestAnimationFrame(autoScroll);
  };
  // Painting selects nothing: the cell under the pointer when it lifts is not opened.
  const end = (event: ReactPointerEvent) => {
    if (stroke.current?.pointerId !== event.pointerId) return;
    stroke.current = null;
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
    scrollFrame.current = null;
  };
  return (
    <div
      className={`review-pen-surface${active ? " is-pen" : ""}`}
      onPointerDown={(event) => {
        if (!active || event.button !== 0) return;
        if (!(event.target instanceof Element) || !event.target.closest(".ocr-edit-table")) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        stroke.current = { pointerId: event.pointerId, on: null, x: event.clientX, y: event.clientY, wrap: event.target.closest<HTMLElement>(".ocr-edit-table-wrap") };
        hit(event.clientX, event.clientY);
        if (scrollFrame.current === null) scrollFrame.current = requestAnimationFrame(autoScroll);
      }}
      onPointerMove={(event) => {
        const current = stroke.current;
        if (!current) {
          if (!active || !onHover || !(event.target instanceof Element)) return;
          const path = event.target.closest<HTMLElement>(".ocr-edit-table [data-review-path]")?.dataset.reviewPath;
          // Leaving a cell keeps the last crop, so it does not flicker between cells.
          if (path && path !== hovered.current) { hovered.current = path; onHover(path); }
          return;
        }
        if (current.pointerId !== event.pointerId) return;
        // A quick stroke still marks every cell it passes over.
        const steps = Math.max(1, Math.ceil(Math.hypot(event.clientX - current.x, event.clientY - current.y) / 8));
        for (let step = 1; step <= steps; step += 1)
          hit(current.x + ((event.clientX - current.x) * step) / steps, current.y + ((event.clientY - current.y) * step) / steps);
        current.x = event.clientX; current.y = event.clientY;
      }}
      onPointerUp={end}
      onPointerCancel={end}
    >
      {children}
    </div>
  );
}

type Box = { x: number; y: number; width: number; height: number };

/**
 * The selected cell's crop, shown over a part of the page image as before. It flies out of
 * the cell's place on the page (「シュン」) so one sees where it was cut from; the page itself
 * never zooms. `box` is in the page image's own pixels.
 */
export function RegionPreviewFlyIn({ src, alt, caption, box, pageImage }: {
  src: string;
  alt: string;
  caption: ReactNode;
  box: Box | null;
  pageImage: RefObject<HTMLImageElement | null>;
}) {
  const figure = useRef<HTMLElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const [shown, setShown] = useState(false);
  const played = useRef(false);
  const play = useCallback(() => {
    if (played.current) return;
    played.current = true;
    setShown(true);
    const element = figure.current, crop = image.current, page = pageImage.current;
    if (!element || !crop || !page || !box || !page.naturalWidth || !crop.naturalWidth) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const pageRect = page.getBoundingClientRect();
    const scaleX = pageRect.width / page.naturalWidth, scaleY = pageRect.height / page.naturalHeight;
    const from = { left: pageRect.left + box.x * scaleX, top: pageRect.top + box.y * scaleY, width: box.width * scaleX, height: box.height * scaleY };
    if (from.width < 1 || from.height < 1) return;
    // Where the crop is drawn inside its <img> (object-fit: contain).
    const imgRect = crop.getBoundingClientRect();
    const fit = Math.min(imgRect.width / crop.naturalWidth, imgRect.height / crop.naturalHeight);
    const drawn = { width: crop.naturalWidth * fit, height: crop.naturalHeight * fit };
    const content = { left: imgRect.left + (imgRect.width - drawn.width) / 2, top: imgRect.top + (imgRect.height - drawn.height) / 2, ...drawn };
    const to = element.getBoundingClientRect();
    const sx = from.width / content.width, sy = from.height / content.height;
    const tx = from.left - to.left - (content.left - to.left) * sx, ty = from.top - to.top - (content.top - to.top) * sy;
    element.animate([
      { transform: `translate(${tx}px, ${ty}px) scale(${sx}, ${sy})`, opacity: 0.55, boxShadow: "0 0 0 2px #2468a2" },
      { opacity: 1, offset: 0.35 },
      { transform: "none", opacity: 1 },
    ], { duration: 300, easing: "cubic-bezier(.16, 1, .3, 1)" });
  }, [box, pageImage]);
  useLayoutEffect(() => {
    if (image.current?.complete && image.current.naturalWidth) play();
  }, [play]);
  return (
    <figure ref={figure} className="ocr-region-preview" style={shown ? undefined : { opacity: 0 }}>
      <img ref={image} src={src} alt={alt} onLoad={play} onError={() => setShown(true)} />
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

export function ReviewDraftBoundary({ children, onDirtyChange }: {
  children: ReactNode; onDirtyChange: (dirty: boolean) => void;
}) {
  const editingPaths = useRef(new Set<string>());
  const report = useCallback((path: string, dirty: boolean) => {
    if (dirty) editingPaths.current.add(path); else editingPaths.current.delete(path);
    onDirtyChange(editingPaths.current.size > 0);
  }, [onDirtyChange]);
  return <InlineEditActivity.Provider value={report}>{children}</InlineEditActivity.Provider>;
}

export type ReviewDisplayRecordSection = {
  id: string;
  title: string;
  collectionPath: string;
  columns: Array<{ id: string; label: string }>;
  rows: Array<{
    id: string;
    cells: Array<{ columnId: string; entry: ReadingEntry | null }>;
  }>;
};

export type ReviewDisplayModel = {
  recordSections: ReviewDisplayRecordSection[];
  parentSections: Array<{ id: string; title: string; entries: ReadingEntry[] }>;
  independentEntries: ReadingEntry[];
  independentGroups: Array<{ id: string; label: string; entries: ReadingEntry[] }>;
};

export type ReviewInteractionMode = "edit" | "reorder" | "rename";

/** In the 「項目」 mode an item name is clicked to rename it (display only). */
export const ReviewRenaming = createContext<((entry: ReadingEntry, name: string) => Promise<void>) | null>(null);

export function ReviewItemName({ entry }: { entry: ReadingEntry }) {
  const rename = useContext(ReviewRenaming);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(entry.label);
  if (!rename) return <>{entry.label}</>;
  const commit = () => {
    setEditing(false);
    const next = value.trim();
    // An empty name goes back to the template's own name.
    if (next !== entry.label) void rename(entry, next);
  };
  return editing
    ? <input className="ocr-item-name-input" autoFocus value={value} maxLength={100}
        placeholder={entry.originalLabel ?? entry.label} aria-label={`${entry.label}の項目名`}
        onChange={(event) => setValue(event.target.value)} onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") { event.preventDefault(); commit(); }
          if (event.key === "Escape") { event.preventDefault(); setEditing(false); }
        }} />
    : <button type="button" className="ocr-item-name-button"
        title={entry.originalLabel ? `元の名前: ${entry.originalLabel}（空にすると戻ります）` : "クリックで項目名を変更"}
        onClick={() => { setValue(entry.label); setEditing(true); }}>
        {entry.label}<FiEdit2 aria-hidden="true" />
      </button>;
}

export function reviewEntryContext(model: ReviewDisplayModel, entry: ReadingEntry | null | undefined): string {
  if (!entry) return "帳票全体";
  for (const section of model.recordSections) {
    for (const [index, row] of section.rows.entries()) {
      const cell = row.cells.find((item) => item.entry?.path === entry.path);
      if (cell) return `${section.title} · ${index + 1}行目 · ${section.columns.find(column => column.id === cell.columnId)?.label ?? entry.label}`;
    }
  }
  for (const section of model.parentSections) {
    const item = section.entries.find(item => item.path === entry.path || item.writeIn?.path === entry.path);
    if (item) return `${section.title} · ${item.path === entry.path ? item.label : `${item.label}（記入）`}`;
  }
  return entry.label;
}
/** The name an entry has in the result table (heading prefix dropped, a write-in named after its box). */
export function reviewEntryLabel(model: ReviewDisplayModel, entry: ReadingEntry): string {
  for (const item of [...model.parentSections.flatMap(section => section.entries), ...model.independentEntries]) {
    if (item.path === entry.path) return item.label;
    if (item.writeIn?.path === entry.path) return `${item.label}（記入）`;
  }
  return entry.label;
}
type RectBounds = { left: number; top: number; right: number; bottom: number };
type PopoverSize = { width: number; height: number };
export type ReviewPopoverPosition = {
  left: number;
  top: number;
  placement: "above" | "below";
};

export function positionReviewPopover(
  anchor: RectBounds,
  popover: PopoverSize,
  boundary: RectBounds,
  gap = 8,
): ReviewPopoverPosition {
  const availableAbove = anchor.top - boundary.top;
  const availableBelow = boundary.bottom - anchor.bottom;
  const placement = availableAbove >= popover.height + gap || availableAbove >= availableBelow
    ? "above"
    : "below";
  const desiredTop = placement === "above"
    ? anchor.top - popover.height - gap
    : anchor.bottom + gap;
  const maxLeft = Math.max(boundary.left, boundary.right - popover.width);
  const maxTop = Math.max(boundary.top, boundary.bottom - popover.height);
  return {
    left: Math.min(
      Math.max(anchor.left + (anchor.right - anchor.left - popover.width) / 2, boundary.left),
      maxLeft,
    ),
    top: Math.min(Math.max(desiredTop, boundary.top), maxTop),
    placement,
  };
}

export function PenModeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 20l4.2-1 10.6-10.6-3.2-3.2L5 15.8 4 20zm10.5-13.7 3.2 3.2" />
    </svg>
  );
}

export function PointerModeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 3l11.5 10.2-5.2.8 2.7 5.2-2.5 1.3-2.7-5.2L6 19V3z" />
    </svg>
  );
}

export function ReviewResultsToolbar({
  interactionMode,
  editingLocked,
  confirmed,
  warningCount,
  cellCount,
  onInteractionModeChange,
  onNextWarning,
  penActive = false,
  onPenChange,
}: {
  interactionMode: ReviewInteractionMode;
  editingLocked: boolean;
  confirmed: boolean;
  warningCount: number;
  cellCount: number;
  onInteractionModeChange: (mode: ReviewInteractionMode) => void;
  onNextWarning: () => void;
  penActive?: boolean;
  onPenChange?: (active: boolean) => void;
}) {
  // While the check pen is on, neither segment is chosen; choosing one ends the pen.
  const shownMode = penActive ? null : interactionMode;
  const chooseMode = (mode: ReviewInteractionMode) => {
    if (penActive) onPenChange?.(false);
    onInteractionModeChange(mode);
  };
  return (
    <div className="field-toolbar">
      <div className="ocr-cell-legend">
        <span><i className="legend-ok" />{confirmed ? "確定済み" : "候補"}</span>
        {!confirmed && <span><i className="legend-warning" />要確認</span>}
        {!confirmed && onPenChange && <span><i className="legend-painted" />確認済み（ペン）</span>}
      </div>
      <div className="field-toolbar-actions">
        {/* 編集: a segmented control of what is edited (the values in the cells, or the item names).
            Row moving (「移動」) is no longer offered here; its mode stays for the record tables. */}
        <div className="review-edit-group" role="group" aria-label="編集">
          <span className="review-edit-label"><PenModeIcon />編集</span>
          <div className="review-mode-switch" role="radiogroup" aria-label="編集する対象">
            <button
              type="button"
              role="radio"
              className={shownMode === "edit" ? "active" : ""}
              aria-checked={shownMode === "edit"}
              title="セルの内容を確認・修正"
              disabled={editingLocked && !confirmed}
              onClick={() => chooseMode("edit")}
            >記入欄</button>
            <button
              type="button"
              role="radio"
              className={shownMode === "rename" ? "active" : ""}
              aria-checked={shownMode === "rename"}
              title="項目名をクリックして表示名を変更（保存した値は変わりません）"
              onClick={() => chooseMode("rename")}
            >項目</button>
          </div>
        </div>
        {!confirmed && onPenChange && (
          <button
            type="button"
            className={`review-pen-toggle${penActive ? " active" : ""}`}
            aria-pressed={penActive}
            title={penActive ? "ペンをやめる（Esc）" : "確認したセルをなぞると緑になります（緑のセルからなぞると戻ります）"}
            onClick={() => onPenChange(!penActive)}
          >
            <FiPenTool aria-hidden="true" />確認ペン
          </button>
        )}
        {!confirmed && (
          <button
            type="button"
            className="secondary compact"
            disabled={warningCount === 0}
            onClick={onNextWarning}
          >
            <FiArrowRight aria-hidden="true" />次の要確認（{warningCount}件）
          </button>
        )}
        {/* No 「nセルを編集中」 count; only a hint where clicking does something different. */}
        {(confirmed || interactionMode !== "edit") && <span>{confirmed
          ? "セルを選ぶと画像を確認できます"
          : interactionMode === "reorder"
            ? "行移動モード"
            : "項目名をクリックして変更"}</span>}
      </div>
    </div>
  );
}

export function AnchoredReviewPopover({
  path,
  wide = false,
  children,
  onClose,
}: {
  path: string;
  wide?: boolean;
  children: ReactNode;
  onClose: () => void;
}) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const [position, setPosition] = useState<ReviewPopoverPosition | null>(null);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useLayoutEffect(() => {
    const updatePosition = () => {
      const anchor = Array.from(
        document.querySelectorAll<HTMLElement>("[data-review-path]"),
      ).find((candidate) => candidate.dataset.reviewPath === path);
      const popover = popoverRef.current;
      if (!anchor || !popover) {
        onCloseRef.current();
        return;
      }
      const anchorRect = anchor.getBoundingClientRect();
      const paneRect = anchor.closest<HTMLElement>(".field-pane")?.getBoundingClientRect();
      const viewport = {
        left: 8,
        top: 8,
        right: Math.max(8, window.innerWidth - 8),
        bottom: Math.max(8, window.innerHeight - 8),
      };
      const hasUsablePane = Boolean(
        paneRect && paneRect.right > paneRect.left && paneRect.bottom > paneRect.top,
      );
      const boundary = hasUsablePane && paneRect
        ? {
          left: Math.max(viewport.left, paneRect.left + 8),
          top: Math.max(viewport.top, paneRect.top + 8),
          right: Math.min(viewport.right, paneRect.right - 8),
          bottom: Math.min(viewport.bottom, paneRect.bottom - 8),
        }
        : viewport;
      setPosition(positionReviewPopover(
        anchorRect,
        {
          width: popover.offsetWidth || (wide ? 520 : 260),
          height: popover.offsetHeight || (wide ? 220 : 96),
        },
        boundary,
      ));
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [path, wide]);

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
    };
    // A press outside closes it. Result cells keep their own handling (selecting another cell
    // moves the dialog there), and the voice controls act on the selected cell.
    const handlePress = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target || popoverRef.current?.contains(target)) return;
      // Check boxes only toggle, so pressing one closes another cell's dialog.
      if (!target.closest(".ocr-checkbox-cell") && target.closest("[data-review-path], .editing-controls, .voice-overlay")) return;
      onCloseRef.current();
    };
    window.addEventListener("keydown", handleEscape);
    window.addEventListener("pointerdown", handlePress, true);
    return () => {
      window.removeEventListener("keydown", handleEscape);
      window.removeEventListener("pointerdown", handlePress, true);
    };
  }, []);

  return (
    <div
      ref={popoverRef}
      className={`ocr-review-popover${wide ? " ocr-review-popover-wide" : ""}`}
      data-placement={position?.placement}
      style={{
        left: position?.left ?? 0,
        top: position?.top ?? 0,
        visibility: position ? "visible" : "hidden",
      }}
    >
      {children}
    </div>
  );
}

/** The cell's dialog: only 「空欄にする」 (and the reading's other candidates); no item name. */
export function CellStatusPopover({
  entry,
  confirmed,
  onChange,
}: {
  entry: ReadingEntry;
  confirmed: boolean;
  onChange: (patch: Partial<FieldReading>) => void;
}) {
  const current = entry.value.normalized ?? entry.value.raw;
  const choices = readingChoices(entry.value);
  const blank = entry.value.status === "blank";
  // The value being replaced stays as a choice, so one click can go back to it.
  const choose = (value: string) => onChange({
    raw: value,
    normalized: value,
    status: "readable",
    reviewReason: null,
    alternatives: [...new Set([...(current ? [current] : []), ...entry.value.alternatives])].filter((item) => item !== value),
  });
  return (
    <section className="ocr-cell-status-popover" aria-label={`${entry.label}の判定`}>
      <div role="group" aria-label={`${entry.label}の判定`}>
        <button type="button" className={blank ? "active" : ""} aria-pressed={blank} disabled={confirmed || blank}
          onClick={() => onChange({ status: "blank", raw: null, normalized: null, reviewReason: null })}>
          {blank ? "空欄です" : "空欄にする"}
        </button>
      </div>
      {choices.length > 0 && (
        <div className="ocr-cell-choices" role="group" aria-label={`${entry.label}の候補`}>
          <span>候補</span>
          {choices.map((value) => (
            <button type="button" key={value} disabled={confirmed} title={`「${value}」に直す`} onClick={() => choose(value)}>
              {value}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

/** A symbol box (○×) is decided like other cells: its symbols and 「空欄にする」, no item name. */
export function MarkStatusPopover({
  entry,
  preview,
  confirmed,
  onChange,
}: {
  entry: ReadingEntry;
  preview: OcrFieldPreview;
  confirmed: boolean;
  onChange: (patch: Partial<FieldReading>) => void;
}) {
  const labels = (preview.markOptions ?? []).map((option) => option.label);
  const multiple = preview.markSelectionMode === "multiple";
  const current = entry.value.normalized ?? entry.value.raw ?? "";
  const blank = entry.value.status === "blank";
  // Symbols shown as chosen are the ones a click adds to (an unsure reading starts from none).
  const selected = entry.value.status === "readable"
    ? current.split("・").map((value) => value.trim()).filter((value) => labels.includes(value)) : [];
  const apply = (next: string[]) => {
    const value = labels.filter((label) => next.includes(label)).join("・");
    onChange(value
      ? { raw: value, normalized: value, status: "readable", reviewReason: null }
      : { raw: null, normalized: null, status: "blank", reviewReason: null });
  };
  const toggle = (label: string) => apply(multiple
    ? (selected.includes(label) ? selected.filter((value) => value !== label) : [...selected, label])
    : [label]);
  return (
    <section className="ocr-cell-status-popover ocr-mark-status-popover" aria-label={`${entry.label}の判定`}>
      <div className="ocr-mark-options" role="group" aria-label={`${entry.label}の記号`}>
        {labels.map((label) => {
          const active = selected.includes(label);
          return (
            <button type="button" key={label} className={active ? "active" : ""} aria-pressed={active}
              disabled={confirmed} onClick={() => toggle(label)}>{label}</button>
          );
        })}
        {labels.length === 0 && <span>テンプレートに選択肢が設定されていません。</span>}
      </div>
      <div role="group" aria-label={`${entry.label}の判定`}>
        <button type="button" className={blank ? "active" : ""} aria-pressed={blank} disabled={confirmed || blank}
          onClick={() => onChange({ raw: null, normalized: null, status: "blank", reviewReason: null })}>
          {blank ? "空欄です" : "空欄にする"}
        </button>
      </div>
    </section>
  );
}

/** A box with several long choices needs the wider popover. */
export function markPopoverIsWide(preview: OcrFieldPreview | undefined): boolean {
  const labels = (preview?.markOptions ?? []).map((option) => option.label);
  return labels.length > 4 || labels.join("").length > 16;
}

/** Values the reading offers to switch to (the current value excluded). */
export function readingChoices(reading: FieldReading): string[] {
  const current = reading.normalized ?? reading.raw;
  return [...new Set(reading.alternatives ?? [])].filter((value) => value.trim() && value !== current);
}

/** Voice input target table: clicking a table name focuses voice instructions on it. */
export const VoiceTableTarget = createContext<{ selected: string | null; select: (tableId: string | null) => void } | null>(null);

/** A heading's items including the write-ins shown on their rows, in the listed order. */
export function sectionItemPaths(entries: readonly ReadingEntry[]): string[] {
  return entries.flatMap((entry) => entry.writeIn ? [entry.path, entry.writeIn.path] : [entry.path]);
}

export function ReviewTableSection({
  tableId,
  title,
  joinPaths,
  children,
}: {
  tableId?: string;
  title: string;
  /** The heading's items as listed; given where 「まとめ方・順番」 can be set for it. */
  joinPaths?: string[];
  children: ReactNode;
}) {
  const sectionRef = useRef<HTMLElement>(null);
  const [overflow, setOverflow] = useState(false);
  const voiceTarget = useContext(VoiceTableTarget);
  const joinSettings = useContext(ReviewJoins);
  const voiceSelected = Boolean(voiceTarget && tableId && voiceTarget.selected === tableId);
  useLayoutEffect(() => {
    const element = sectionRef.current?.querySelector<HTMLElement>(".ocr-edit-table-wrap");
    if (!element) return;
    const update = () => setOverflow(element.scrollWidth > element.clientWidth + 2);
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [children]);
  const scrollColumns = (direction: number) => {
    const element = sectionRef.current?.querySelector<HTMLElement>(".ocr-edit-table-wrap");
    element?.scrollBy({ left: direction * element.clientWidth * 0.7, behavior: "smooth" });
  };
  if (!children) return null;
  return (
    <section ref={sectionRef} data-ai-table-id={tableId} className={voiceSelected ? "ocr-edit-section is-voice-target" : "ocr-edit-section"} aria-label={title}>
      <header>{voiceTarget && tableId
        ? <h3><button type="button" className="voice-table-target" aria-pressed={voiceSelected}
            title={voiceSelected ? "音声の対象を自動に戻す" : "音声の指示をこの表に絞る"}
            onClick={() => voiceTarget.select(voiceSelected ? null : tableId)}>{title}{voiceSelected && <span className="voice-table-badge">音声の対象</span>}</button></h3>
        : <h3>{title}</h3>}{ITEM_JOIN_EDITING_VISIBLE && !tableId && joinPaths?.length && joinSettings.configure && <button type="button" className="ghost compact ocr-join-configure" aria-haspopup="dialog"
          onClick={(event) => joinSettings.configure?.(joinPaths, event.currentTarget)}>まとめ方・順番</button>}{overflow && <div className="review-column-navigation"><span>横に続きます</span>
        <button type="button" aria-label={`${title}の左の列を表示`} onClick={() => scrollColumns(-1)}>←</button>
        <button type="button" aria-label={`${title}の右の列を表示`} onClick={() => scrollColumns(1)}>→</button>
      </div>}</header>
      {children}
    </section>
  );
}

function canSwapVisibleRecordRows(
  section: ReviewDisplayRecordSection,
  fromIndex: number,
  toIndex: number,
) {
  if (
    Math.abs(fromIndex - toIndex) !== 1
    || fromIndex < 0
    || toIndex < 0
    || fromIndex >= section.rows.length
    || toIndex >= section.rows.length
  ) return false;
  const fromRow = section.rows[fromIndex];
  const toRow = section.rows[toIndex];
  const paths = section.columns.flatMap((column) => {
    const firstPath = fromRow.cells.find((cell) => cell.columnId === column.id)?.entry?.path;
    const secondPath = toRow.cells.find((cell) => cell.columnId === column.id)?.entry?.path;
    return firstPath && secondPath ? [firstPath, secondPath] : [];
  });
  return paths.length === section.columns.length * 2 && new Set(paths).size === paths.length;
}

export function GenericRecordReviewTable({
  section,
  selectedPath,
  warningPaths,
  choicePaths,
  confirmed,
  interactionMode,
  onSelect,
  onEdit,
  onMoveRow,
  after,
}: {
  /** Shown under the table in the same section (its heading's items outside the rows). */
  after?: ReactNode;
  section: ReviewDisplayRecordSection;
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  choicePaths: ReadonlySet<string>;
  confirmed: boolean;
  interactionMode: ReviewInteractionMode;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
  onMoveRow: (
    section: ReviewDisplayRecordSection,
    fromIndex: number,
    toIndex: number,
  ) => void;
}) {
  return (
    <ReviewTableSection tableId={section.id} title={section.title}>
      <div className="ocr-edit-table-wrap">
        <table
          className="ocr-edit-table ocr-collection-table ocr-generic-record-table"
          aria-label={`${section.title}のレコード`}
        >
          <thead>
            <tr>
              <th scope="col">行</th>
              {section.columns.map((column) => (
                <th scope="col" key={column.id}>{column.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {section.rows.map((row, rowIndex) => (
              <tr key={row.id}>
                <th scope="row" aria-label={`${rowIndex + 1}行目`}>
                  <div className="ocr-row-heading">
                    <span>{rowIndex + 1}</span>
                    {interactionMode === "reorder" && (
                      <span className="ocr-row-shift-controls">
                        <button
                          type="button"
                          aria-label={`${section.title} ${rowIndex + 1}行目を上へ移動`}
                          title="この行の読み取り結果を一行上へ移動"
                          disabled={confirmed || !canSwapVisibleRecordRows(section, rowIndex, rowIndex - 1)}
                          onClick={() => onMoveRow(section, rowIndex, rowIndex - 1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          aria-label={`${section.title} ${rowIndex + 1}行目を下へ移動`}
                          title="この行の読み取り結果を一行下へ移動"
                          disabled={confirmed || !canSwapVisibleRecordRows(section, rowIndex, rowIndex + 1)}
                          onClick={() => onMoveRow(section, rowIndex, rowIndex + 1)}
                        >
                          ↓
                        </button>
                      </span>
                    )}
                  </div>
                </th>
                {section.columns.map((column) => {
                  const cell = row.cells.find((item) => item.columnId === column.id);
                  const entry = cell?.entry;
                  if (!entry) {
                    return (
                      <td
                        className="ocr-unmapped-cell"
                        key={column.id}
                        aria-label={`${rowIndex + 1}行目 ${column.label} 未設定`}
                      >
                        —
                      </td>
                    );
                  }
                  return (
                    <ReviewValueCell
                      key={column.id}
                      entry={entry}
                      selected={selectedPath === entry.path}
                      warning={warningPaths.has(entry.path)}
                      choice={choicePaths.has(entry.path)}
                      confirmed={confirmed || interactionMode !== "edit"}
                      onSelect={onSelect}
                      onEdit={onEdit}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {after}
    </ReviewTableSection>
  );
}

type JoinedUnit = { join: ItemJoin; members: Array<{ member: ItemJoinMember; entry: ReadingEntry }> };

export function KeyValueReviewTable({
  entries,
  selectedPath,
  warningPaths,
  choicePaths,
  confirmed,
  onSelect,
  onEdit,
}: {
  entries: ReadingEntry[];
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  choicePaths: ReadonlySet<string>;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  const { byPath: joins, orders = [] } = useContext(ReviewJoins);
  const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
  if (entries.length === 0) return null;
  // Items joined into one value (「調査場所」) take the place of their first item.
  const collected: Array<ReadingEntry | JoinedUnit> = [];
  const placed = new Set<ItemJoin>();
  for (const entry of entries) {
    const join = joins.get(entry.path);
    const members = join ? join.members.flatMap((member) => {
      const item = entries.find((candidate) => candidate.path === member.targetPath);
      return item ? [{ member, entry: item }] : [];
    }) : [];
    if (!join || members.length < 2) { collected.push(entry); continue; }
    if (placed.has(join)) continue;
    placed.add(join);
    collected.push({ join, members });
  }
  // The template's 「表示の順番」 for this heading, if any (a join counts at its first item).
  const units = sortByOrder(collected, (unit) => "join" in unit ? unit.members[0].entry.path : unit.path, orders);
  // A box with its write-in (「その他 ○ ／ 記入 …」) or a joined item fills a row of its own.
  const ownRow = (unit: ReadingEntry | JoinedUnit) => "join" in unit || Boolean(unit.writeIn);
  const rows = units.reduce<Array<Array<ReadingEntry | JoinedUnit>>>((result, unit) => {
    const last = result[result.length - 1];
    if (ownRow(unit) || !last || last.length === 2 || ownRow(last[0])) result.push([unit]);
    else last.push(unit);
    return result;
  }, []);
  const single = units.length === 1 && !ownRow(units[0]);
  const wide = units.length > 1;
  const cell = (entry: ReadingEntry) => (
    <ReviewValueCell
      entry={entry}
      selected={selectedPath === entry.path}
      warning={warningPaths.has(entry.path)}
      choice={choicePaths.has(entry.path)}
      confirmed={confirmed}
      onSelect={onSelect}
      onEdit={onEdit}
    />
  );
  return (
    <div className="ocr-edit-table-wrap">
      <table className={`ocr-edit-table ocr-key-value-table${single ? " ocr-single-field-table" : ""}`}>
        <tbody>
          {rows.map((row, index) => {
            const first = row[0];
            if ("join" in first) {
              const key = first.join.id ?? first.join.name;
              const memberPaths = first.members.map((item) => item.entry.path);
              const warned = !confirmed && memberPaths.some((path) => warningPaths.has(path));
              const open = opened.has(key) || warned || (selectedPath !== null && memberPaths.includes(selectedPath));
              const text = joinedText(first.members.map((item) => item.member), (path) => {
                const value = first.members.find((item) => item.entry.path === path)?.entry.value;
                return value ? value.normalized ?? value.raw : null;
              }, first.join.separator ?? "");
              const toggle = () => setOpened((current) => {
                const next = new Set(current);
                if (next.has(key)) next.delete(key); else next.add(key);
                return next;
              });
              return [
                <tr key={`join:${key}`} className="ocr-join-row">
                  <th scope="row">{first.join.name}</th>
                  <td colSpan={wide ? 3 : 1} className={warned ? "ocr-join-value warning" : "ocr-join-value"}>
                    <button type="button" className="ocr-join-toggle" aria-expanded={open}
                      title={open ? "まとめた欄を閉じる" : "まとめた欄を開いて確認・修正"} onClick={toggle}>
                      <span className="ocr-join-text">{text || <em>空欄</em>}</span>
                      <span className="ocr-join-count" aria-hidden="true">{open ? "▲" : "▼"}</span>
                    </button>
                  </td>
                </tr>,
                ...(open ? first.members.map((item, order) => (
                  <tr key={`join:${key}:${item.entry.path}`} className="ocr-join-member">
                    <th scope="row"><span className="ocr-join-order">{orderMark(order)}</span><ReviewItemName entry={item.entry} /></th>
                    {cell(item.entry)}
                    {wide && <><th aria-hidden="true" /><td className="ocr-empty-cell" /></>}
                  </tr>
                )) : []),
              ];
            }
            const plain = row as ReadingEntry[];
            return (
            <tr key={index} className={plain[0].writeIn ? "ocr-write-in-row" : undefined}>
              {plain[0].writeIn
                ? <>
                    <th scope="row"><ReviewItemName entry={plain[0]} /></th>
                    {cell(plain[0])}
                    <th scope="row" className="ocr-write-in-label" title={plain[0].writeIn.label}>記入</th>
                    {cell(plain[0].writeIn)}
                  </>
                : plain.map((entry) => (
                  <ReviewLabelAndCell
                    key={entry.path}
                    entry={entry}
                    selected={selectedPath === entry.path}
                    warning={warningPaths.has(entry.path)}
                    choice={choicePaths.has(entry.path)}
                    confirmed={confirmed}
                    onSelect={onSelect}
                    onEdit={onEdit}
                  />
                ))}
              {plain.length === 1 && !plain[0].writeIn && wide && <><th aria-hidden="true" /><td className="ocr-empty-cell" /></>}
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function IndependentReviewGroups({
  groups,
  selectedPath,
  warningPaths,
  choicePaths,
  confirmed,
  onSelect,
  onEdit,
}: {
  groups: ReviewDisplayModel["independentGroups"];
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  choicePaths: ReadonlySet<string>;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  return (
    <div className="ocr-independent-groups">
      {groups.map((group) => (
        <section className="ocr-independent-group" key={group.id}>
          <h4 className="ocr-independent-label">{group.entries.length === 1 ? <ReviewItemName entry={group.entries[0]} /> : group.label}</h4>
          <table className="ocr-edit-table ocr-independent-values">
            <tbody>
              <tr>
                {group.entries.flatMap((entry) => entry.writeIn ? [entry, entry.writeIn] : [entry]).map((entry) => (
                  <ReviewValueCell
                    key={entry.path}
                    entry={entry}
                    selected={selectedPath === entry.path}
                    warning={warningPaths.has(entry.path)}
                    choice={choicePaths.has(entry.path)}
                    confirmed={confirmed}
                    onSelect={onSelect}
                    onEdit={onEdit}
                  />
                ))}
              </tr>
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

function ReviewLabelAndCell({
  entry,
  selected,
  warning,
  choice,
  confirmed,
  onSelect,
  onEdit,
}: {
  entry: ReadingEntry;
  selected: boolean;
  warning: boolean;
  choice: boolean;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  return (
    <>
      <th scope="row"><ReviewItemName entry={entry} /></th>
      <ReviewValueCell
        entry={entry}
        selected={selected}
        warning={warning}
        choice={choice}
        confirmed={confirmed}
        onSelect={onSelect}
        onEdit={onEdit}
      />
    </>
  );
}

export function ReviewValueCell({
  entry,
  selected,
  warning,
  choice,
  confirmed,
  onSelect,
  onEdit,
}: {
  entry: ReadingEntry;
  selected: boolean;
  warning: boolean;
  choice: boolean;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  const value = entry.value.normalized ?? entry.value.raw;
  const hasChoices = !confirmed && !choice && readingChoices(entry.value).length > 0;
  const marks = useContext(ReviewSeriesMarks);
  const added = marks?.added.has(entry.path) ?? false, carried = marks?.carried.has(entry.path) ?? false;
  const checklistLabels = useContext(ReviewChecklists).get(entry.path);
  const painted = useContext(ReviewPaint).has(entry.path);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(value ?? "");
  const reportInlineEdit = useContext(InlineEditActivity);
  useEffect(() => {
    reportInlineEdit(entry.path, editing && editValue !== (value ?? ""));
    return () => reportInlineEdit(entry.path, false);
  }, [entry.path, editing, editValue, value, reportInlineEdit]);
  useEffect(() => {
    if (!editing) setEditValue(value ?? "");
  }, [editing, value]);
  const commit = () => {
    if (!editing || confirmed) return;
    setEditing(false);
    onEdit(entry, editValue);
  };
  return (
    <td
      className={`${warning ? "ocr-cell-warning" : "ocr-cell-ok"} ${selected ? "ocr-cell-selected" : ""}${added ? " ocr-cell-added" : ""}${carried ? " ocr-cell-carried" : ""}${hasChoices ? " ocr-cell-has-choices" : ""}${painted ? " ocr-cell-painted" : ""}`}
      data-review-path={entry.path}
    >
      {checklistLabels ? (
        <div className="ocr-checkbox-cell">
          <ChecklistBoxes labels={checklistLabels} entry={entry} confirmed={confirmed} onEdit={onEdit} />
          {/* No 「OK」 beside a check box; only a note that asks for attention. */}
          {(warning || added || carried) && <small>{warning ? "要確認" : added ? "追加" : "前の版"}</small>}
        </div>
      ) : editing ? (
        <textarea
          className="ocr-inline-cell-input"
          rows={Math.min(6, Math.max(1, editValue.split("\n").length))}
          autoFocus
          aria-label={`${entry.label}を直接編集`}
          value={editValue}
          onChange={(event) => setEditValue(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              commit();
            }
            if (event.key === "Escape") { event.preventDefault(); setEditing(false); }
          }}
        />
      ) : (
        <button
          type="button"
          aria-label={`${entry.label}：${value || (entry.value.status === "blank" ? (choice ? "未選択" : "空欄") : "未入力")}。${confirmed ? "画像を確認" : "選択して確認・編集"}`}
          title={confirmed || choice ? undefined : "ダブルクリックで直接編集"}
          onClick={() => onSelect(entry)}
          onDoubleClick={(event) => {
            if (confirmed) return;
            event.preventDefault();
            onSelect(entry);
            if (choice) return;
            setEditValue(value ?? "");
            setEditing(true);
          }}
        >
          {/* An unmarked symbol box reads 「-」 (screen readers still hear 「未選択」); a blank write-in stays 「空欄」. */}
          <span>{value || (entry.value.status === "blank" ? (choice ? "-" : "空欄") : "未入力")}</span>
          <small>{warning ? "要確認" : hasChoices ? "候補あり" : added ? "追加" : carried ? "前の版" : "OK"}</small>
        </button>
      )}
    </td>
  );
}

export function buildReviewDisplayModel(
  entries: readonly ReadingEntry[],
  previews: readonly OcrFieldPreview[],
  structure: ReviewStructure | undefined,
  /** Names given on the result screen (path → name), shown over the template's own. */
  names: Readonly<Record<string, string>> = {},
): ReviewDisplayModel {
  const named = (entry: ReadingEntry): ReadingEntry => names[entry.path]
    ? { ...entry, label: names[entry.path], originalLabel: entry.label } : entry;
  if (!structure) {
    const independentEntries = entries.map(named);
    return {
      recordSections: [],
      parentSections: [],
      independentEntries,
      independentGroups: independentEntries.map((entry) => ({
        id: `path:${entry.path}`,
        label: entry.label,
        entries: [entry],
      })),
    };
  }

  const entryByPath = new Map(entries.map((entry) => [entry.path, entry]));
  const entriesBySource = new Map<string, ReadingEntry[]>();
  entries.forEach((entry) => {
    if (entry.value.sourceRegionId !== "unmapped") {
      const candidates = entriesBySource.get(entry.value.sourceRegionId) ?? [];
      candidates.push(entry);
      entriesBySource.set(entry.value.sourceRegionId, candidates);
    }
  });
  const previewByMappingId = new Map(
    previews.map((preview) => [preview.mappingId, preview]),
  );
  const fieldById = new Map(structure.fields.map((field) => [field.id, field]));
  const consumedPaths = new Set<string>();
  // 「その他（　）」: the box and its item's write-in are shown as one row, at the box.
  const writeInByMark = pairWriteIns(structure.fields, previewByMappingId);
  const attachedWriteIns = new Set(writeInByMark.values());
  const resolveField = (fieldId: string | null, attaching = false): ReadingEntry | null => {
    if (!fieldId || (!attaching && attachedWriteIns.has(fieldId))) return null;
    const field = fieldById.get(fieldId);
    if (!field) return null;
    const sourceCandidates = entriesBySource.get(field.mappingId) ?? [];
    const entry = (sourceCandidates.length === 1 ? sourceCandidates[0] : undefined)
      ?? sourceCandidates.find((candidate) => candidate.path === field.targetPath)
      ?? sourceCandidates.find(
        (candidate) => candidate.path === previewByMappingId.get(field.mappingId)?.targetPath,
      )
      ?? entryByPath.get(previewByMappingId.get(field.mappingId)?.targetPath ?? "")
      ?? entryByPath.get(field.targetPath);
    if (!entry || consumedPaths.has(entry.path)) return null;
    consumedPaths.add(entry.path);
    const resolved = named({ ...entry, label: displayName(field.label || entry.label) });
    const writeInId = writeInByMark.get(fieldId);
    const writeIn = writeInId ? resolveField(writeInId, true) : null;
    return writeIn ? { ...resolved, writeIn } : resolved;
  };

  const recordTitleCounts = new Map<string, number>();
  structure.recordSections.forEach((section) => {
    recordTitleCounts.set(section.title, (recordTitleCounts.get(section.title) ?? 0) + 1);
  });
  const recordTitleIndexes = new Map<string, number>();
  const recordSections = structure.recordSections.map((section) => {
    const titleIndex = (recordTitleIndexes.get(section.title) ?? 0) + 1;
    recordTitleIndexes.set(section.title, titleIndex);
    return {
      id: section.id,
      title: (recordTitleCounts.get(section.title) ?? 0) > 1
        ? `${section.title} ${titleIndex}`
        : section.title,
      collectionPath: section.collectionPath,
      columns: section.columns.map((column) => ({ ...column })),
      rows: section.rows.map((row) => ({
        id: row.id,
        cells: row.cells.map((cell) => ({
          columnId: cell.columnId,
          entry: resolveField(cell.fieldId),
        })),
      })),
    };
  });
  // Inside its heading's table a name need not repeat the heading (「利用目的 > その他」).
  const withinHeading = (title: string, entry: ReadingEntry): ReadingEntry => {
    const prefix = `${title} > `;
    const local = (item: ReadingEntry) => !item.originalLabel && item.label.startsWith(prefix) && item.label.length > prefix.length
      ? { ...item, label: item.label.slice(prefix.length) } : item;
    return entry.writeIn ? { ...local(entry), writeIn: local(entry.writeIn) } : local(entry);
  };
  const parentSections = structure.parentSections.map((section) => ({
    id: section.id,
    title: section.title,
    entries: section.fieldIds
      .map((fieldId) => resolveField(fieldId))
      .filter((entry): entry is ReadingEntry => entry !== null)
      .map((entry) => withinHeading(section.title, entry)),
  }));

  const independentEntries: ReadingEntry[] = [];
  const independentGroups: ReviewDisplayModel["independentGroups"] = [];
  const independentGroupById = new Map<string, ReviewDisplayModel["independentGroups"][number]>();
  const appendIndependent = (
    entry: ReadingEntry,
    groupId: string,
    label: string,
  ) => {
    independentEntries.push(entry);
    const existing = independentGroupById.get(groupId);
    if (existing) {
      existing.entries.push(entry);
      return;
    }
    const group = { id: groupId, label, entries: [entry] };
    independentGroupById.set(groupId, group);
    independentGroups.push(group);
  };
  for (const fieldId of [
    ...structure.independentFieldIds,
    ...structure.fields.map((field) => field.id),
  ]) {
    const entry = resolveField(fieldId);
    const field = fieldById.get(fieldId);
    if (entry && field) {
      appendIndependent(entry, field.groupId ?? `field:${field.id}`, entry.label);
    }
  }
  entries.forEach((entry) => {
    if (!consumedPaths.has(entry.path)) {
      consumedPaths.add(entry.path);
      const shown = named(entry);
      appendIndependent(shown, `path:${entry.path}`, shown.label);
    }
  });
  return { recordSections, parentSections, independentEntries, independentGroups };
}

export type ReviewDisplayBlock =
  | { key: string; kind: "record"; section: ReviewDisplayRecordSection; attached?: ReviewDisplayModel["parentSections"][number] }
  | { key: string; kind: "parent"; section: ReviewDisplayModel["parentSections"][number] }
  | { key: string; kind: "independent"; groups: ReviewDisplayModel["independentGroups"] };

/**
 * The result's blocks in display order: the server's form order (from the top, a line
 * left to right), then a saved block order. Without blocks (an older server): tables,
 * headings, then items under no heading, as before.
 */
export function reviewDisplayBlocks(
  display: ReviewDisplayModel,
  structure: ReviewStructure | undefined | null,
  joins: readonly ItemJoin[],
): ReviewDisplayBlock[] {
  const independentAll = display.independentGroups;
  const blocks = structure?.blocks;
  if (!blocks?.length) return [
    ...display.recordSections.map((section) => ({ key: `record:${section.id}`, kind: "record" as const, section })),
    ...display.parentSections.map((section) => ({ key: `parent:${section.id}`, kind: "parent" as const, section })),
    ...(independentAll.length ? [{ key: "independent", kind: "independent" as const, groups: independentAll }] : []),
  ];
  const records = new Map(display.recordSections.map((section) => [section.id, section]));
  const parents = new Map(display.parentSections.map((section) => [section.id, section]));
  const runs = blocks.filter((block) => block.kind === "independent");
  const runOfPath = new Map(runs.flatMap((block, index) => block.paths.map((path) => [path, index] as const)));
  const groupsByRun = runs.map(() => [] as ReviewDisplayModel["independentGroups"]);
  const extra: ReviewDisplayModel["independentGroups"] = [];
  for (const group of independentAll) {
    const run = group.entries.map((entry) => runOfPath.get(entry.path)).find((index) => index !== undefined);
    (run === undefined ? extra : groupsByRun[run]).push(group);
  }
  const result: ReviewDisplayBlock[] = [];
  for (const block of sortBlocks(blocks, joins)) {
    if (block.kind === "record") {
      const section = records.get(block.id ?? "");
      // The heading's items outside the table's rows are shown with the table, not apart.
      const attached = block.attachedId ? parents.get(block.attachedId) : undefined;
      if (section) result.push({ key: `record:${section.id}`, kind: "record", section, attached: attached?.entries.length ? attached : undefined });
      else if (attached?.entries.length) result.push({ key: `parent:${attached.id}`, kind: "parent", section: attached });
    } else if (block.kind === "parent") {
      const section = parents.get(block.id ?? "");
      if (section) result.push({ key: `parent:${section.id}`, kind: "parent", section });
    } else {
      const groups = groupsByRun[runs.indexOf(block)];
      if (groups.length) result.push({ key: `independent:${block.paths[0] ?? runs.indexOf(block)}`, kind: "independent", groups });
    }
  }
  // Items the structure does not place (added later) go last, as before.
  if (extra.length) result.push({ key: "independent:extra", kind: "independent", groups: extra });
  return result;
}

/** Box field ID → the write-in field its item owns (「その他（　）」 next to ○× or □). */
function pairWriteIns(
  fields: readonly ReviewStructureField[],
  previewByMappingId: ReadonlyMap<string, OcrFieldPreview>,
): Map<string, string> {
  const isMark = (field: ReviewStructureField) => previewByMappingId.get(field.mappingId)?.mappingType === "mark";
  const fieldByPath = new Map(fields.map((field) => [field.targetPath, field]));
  const pairs = new Map<string, string>();
  const taken = new Set<string>();
  const pair = (mark: ReviewStructureField, text: ReviewStructureField | undefined) => {
    if (!text || isMark(text) || pairs.has(mark.id) || taken.has(text.id)) return;
    pairs.set(mark.id, text.id);
    taken.add(text.id);
  };
  // The link saved on the box's option.
  for (const field of fields.filter(isMark)) {
    const paths = [...new Set((previewByMappingId.get(field.mappingId)?.markOptions ?? [])
      .map((option) => option.writeInTargetPath).filter((path): path is string => Boolean(path)))];
    if (paths.length === 1) pair(field, fieldByPath.get(paths[0]));
  }
  // Older templates: one box and one text field tied to the same item name.
  const byItemName = new Map<string, ReviewStructureField[]>();
  for (const field of fields) {
    const item = /^labels:[^:]*:(.+)$/.exec(field.groupId ?? "")?.[1];
    if (item) byItemName.set(item, [...(byItemName.get(item) ?? []), field]);
  }
  for (const group of byItemName.values()) {
    const marks = group.filter(isMark), texts = group.filter((field) => !isMark(field));
    if (marks.length === 1 && texts.length === 1) pair(marks[0], texts[0]);
  }
  return pairs;
}
