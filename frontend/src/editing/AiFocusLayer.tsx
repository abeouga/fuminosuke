import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import type { LayoutCell, LayoutRecordGroup, LayoutRecordSection } from "../types";
import { validFocus, type AiFocusState, type AiPreviewRegion } from "./focusState";
import type { ContextResult, FocusAnchor } from "./v2.generated";
import { clip, intersects, scrollDelta, scrollTarget, stickyTitle, union, type Box } from "./aiFocusGeometry";
import "./editing.css";

type Location = { box: Box; element: HTMLElement; kind?: AiPreviewRegion["kind"]; label?: string };
type Table = { id: string; title: string; header: Box; body: Box; element: HTMLElement; headerElement?: HTMLElement };
export type TargetAnchorRegistry = { resolve: (anchor: FocusAnchor) => Location | null; tables: () => Table[] };
type Props = { focus: AiFocusState | null; context: ContextResult | null; viewRevision: number; registry: TargetAnchorRegistry; suspendScroll?: boolean };
type Painted = { id: string; box: Box; viewport: Box; phase: AiFocusState["phase"]; kind?: AiPreviewRegion["kind"]; label?: string; exiting?: boolean };
const screen = (): Box => {
  const nav = document.querySelector<HTMLElement>(".side-nav"), rect = nav?.getBoundingClientRect();
  const top = rect && rect.width > innerWidth / 2 && rect.height < 120 && rect.top <= 8 && nav && /fixed|sticky/.test(getComputedStyle(nav).position) ? Math.max(8, rect.bottom + 8) : 8;
  // The voice panel can be pinned to the bottom of the screen; targets must stay above it.
  const panel = document.querySelector<HTMLElement>(".review-assistant-workspace > .editing-controls"), box = panel?.getBoundingClientRect();
  const bottom = box && panel && getComputedStyle(panel).position === "sticky" && box.bottom >= innerHeight - 24 && box.top > top ? box.top - 8 : innerHeight - 8;
  return { left: 8, top, width: Math.max(0, innerWidth - 16), height: Math.max(0, bottom - top) };
};
function parents(element: HTMLElement) {
  const result: HTMLElement[] = [];
  for (let p = element.parentElement; p && p !== document.body; p = p.parentElement) {
    if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflow + getComputedStyle(p).overflowX + getComputedStyle(p).overflowY)) result.push(p);
  }
  return result;
}
function viewport(element: HTMLElement): Box {
  let result = screen();
  for (const p of parents(element)) { const r = p.getBoundingClientRect(); result = clip(result, { left: r.left + p.clientLeft, top: r.top + p.clientTop, width: p.clientWidth, height: p.clientHeight }); }
  return result;
}
function inset(box: Box, top = 0): Box { return { left: box.left + 5, top: box.top + 5 + top, width: Math.max(1, box.width - 10), height: Math.max(1, box.height - 10 - top) }; }

/** Geometry is resolved from the current preview. It never selects or edits a cell. */
export function AiFocusLayer(props: Props) {
  const latest = useRef(props); latest.current = props;
  const [painted, setPainted] = useState<Painted[]>([]), [titles, setTitles] = useState<Array<{ table: Table; view: Box }>>([]);
  const prior = useRef<FocusAnchor[]>([]), old = useRef<FocusAnchor | null>(null), movedAt = useRef(0);
  const movementOrigin = useRef<Box | null>(null);
  const previousPaint = useRef<Painted[]>([]), retiring = useRef(new Map<string, { value: Painted; until: number }>()), paintGeneration = useRef("");
  const focusKey = props.focus ? `${props.focus.commandId}:${props.focus.phase}:${props.focus.focus.viewRevision}:${props.focus.focus.anchors.map(a => a.anchorId).join()}` : "";
  const suppressedUntil = useRef(0), pointer = useRef(false), composing = useRef(false), scrollPending = useRef(false);
  const lastPhase = useRef<AiFocusState["phase"] | null>(null), previewing = useRef(false);
  useEffect(() => {
    const anchors = props.focus?.focus.anchors ?? [], phase = props.focus?.phase ?? null;
    // A hovered choice moves nothing: no sliding outline, and leaving the hover does not
    // scroll back to the previous outline.
    previewing.current = phase === "preview";
    old.current = !previewing.current && anchors.length === 1 && prior.current.length === 1 && anchors[0].anchorId !== prior.current[0].anchorId ? prior.current[0] : null;
    movementOrigin.current = old.current ? previousPaint.current.find(p => p.id === old.current!.anchorId)?.box ?? null : null;
    movedAt.current = performance.now(); prior.current = anchors;
    scrollPending.current = Boolean(anchors.length) && lastPhase.current !== "preview";
    lastPhase.current = phase;
  }, [focusKey]);
  useEffect(() => {
    let frame = 0, timer: ReturnType<typeof setTimeout> | null = null, alive = true;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const schedule = () => { if (!frame && alive) frame = requestAnimationFrame(draw); };
    function draw() {
      frame = 0; const p = latest.current, now = performance.now();
      const valid = p.context && p.focus && validFocus(p.focus.focus, p.context, p.viewRevision);
      const targets = valid ? p.focus!.focus.anchors.flatMap(anchor => { const found = p.registry.resolve(anchor); return found ? [{ anchor, ...found }] : []; }) : [];
      if (!valid) scrollPending.current = false;
      // A hovered choice scrolls only when its cell is entirely out of sight.
      if (previewing.current && scrollPending.current && targets.length) {
        // Just enough to bring the hovered cell into view, at once; no centering.
        const target = targets[0], view = screen();
        const hidden = (box: Box) => !intersects(box, view) || box.top + box.height / 2 > view.top + view.height || box.top + box.height / 2 < view.top;
        if (hidden(target.box)) {
          // A template cell is a region of one canvas; only a review cell is its own element.
          const own = !(target.element instanceof HTMLCanvasElement);
          if (own) target.element.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
          const box = own ? target.element.getBoundingClientRect() : target.box, bottom = view.top + view.height;
          const end = box.top + box.height;
          const dy = end > bottom ? end - bottom + 12 : box.top < view.top ? box.top - view.top - 12 : 0;
          if (dy) window.scrollBy({ top: dy, behavior: "instant" });
        }
        scrollPending.current = false;
      }
      if (targets.length && scrollPending.current && !pointer.current && !composing.current && !p.suspendScroll) {
        if (now >= suppressedUntil.current) {
          const first = targets[0], groupView = viewport(first.element);
          const chosen = scrollTarget(targets.map(t => t.box), inset(groupView, 32));
          const destination = targets.find(t => intersects(t.box, chosen)) ?? first;
          const chain = parents(destination.element).filter(el => /auto|scroll/.test(getComputedStyle(el).overflow + getComputedStyle(el).overflowY + getComputedStyle(el).overflowX));
          // Scroll the innermost obstruction first, then resolve again on its scroll event.
          let scrolled = false;
          for (const container of chain) {
            const header = container.querySelector("thead"), top = header?.getBoundingClientRect().height ?? 0;
            const rect = container.getBoundingClientRect();
            const inner = inset({ left: rect.left + container.clientLeft, top: rect.top + container.clientTop, width: container.clientWidth, height: container.clientHeight }, top);
            const members = targets.filter(t => container.contains(t.element)).map(t => t.box);
            const delta = scrollDelta(members.length ? scrollTarget(members, inner) : destination.box, inner);
            const canX = delta.left < 0 ? container.scrollLeft > 0 : container.scrollLeft < container.scrollWidth - container.clientWidth - 1;
            const canY = delta.top < 0 ? container.scrollTop > 0 : container.scrollTop < container.scrollHeight - container.clientHeight - 1;
            if ((Math.abs(delta.left) > 1 && canX) || (Math.abs(delta.top) > 1 && canY)) { container.scrollBy({ left: canX ? delta.left : 0, top: canY ? delta.top : 0, behavior: "instant" }); scrolled = true; break; }
          }
          if (!scrolled) {
            const delta = scrollDelta(chosen, inset(screen(), 32));
            if (Math.abs(delta.left) > 1 || Math.abs(delta.top) > 1) window.scrollBy({ ...delta, behavior: reduced.matches || previewing.current ? "instant" : "smooth" });
            scrollPending.current = false;
          }
          if (scrolled) schedule();
        } else if (!timer) timer = setTimeout(() => { timer = null; schedule(); }, Math.max(1, suppressedUntil.current - now));
      }
      const fraction = reduced.matches ? 1 : Math.min(1, (now - movedAt.current) / 250), ease = 1 - Math.pow(1 - fraction, 3);
      const generation = p.focus ? `${p.focus.focus.viewRevision}:${p.focus.focus.contextRevision}:${p.focus.focus.interactionEpoch}` : "";
      if (!valid || generation !== paintGeneration.current) movementOrigin.current = null;
      const previous = old.current && targets.length === 1 && generation === paintGeneration.current
        ? p.registry.resolve(old.current) ?? (movementOrigin.current ? { box: movementOrigin.current } : null) : null;
      const next = targets.map(t => {
        let box = t.box;
        if (previous && fraction < 1) box = { left: previous.box.left + (box.left - previous.box.left) * ease, top: previous.box.top + (box.top - previous.box.top) * ease, width: previous.box.width + (box.width - previous.box.width) * ease, height: previous.box.height + (box.height - previous.box.height) * ease };
        return { id: t.anchor.anchorId, box, viewport: viewport(t.element), phase: p.focus!.phase, kind: t.kind, label: t.label };
      });
      if (!valid || generation !== paintGeneration.current) retiring.current.clear();
      else if (targets.length > 1 || previousPaint.current.length > 1) {
        for (const value of previousPaint.current) if (!next.some(n => n.id === value.id) && !retiring.current.has(value.id)) retiring.current.set(value.id, { value, until: now + 160 });
      }
      for (const [id, entry] of retiring.current) if (entry.until <= now || next.some(n => n.id === id) || reduced.matches) retiring.current.delete(id);
      previousPaint.current = next; paintGeneration.current = generation;
      setPainted([...next, ...[...retiring.current.values()].map(entry => ({ ...entry.value, exiting: true }))]);
      setTitles(p.registry.tables().flatMap(table => { const view = viewport(table.element), headerView = table.headerElement ? viewport(table.headerElement) : view; return stickyTitle(table.header, table.body, view, headerView) ? [{ table, view }] : []; }));
      if ((previous && fraction < 1) || retiring.current.size) schedule();
    }
    const interact = (e: Event) => {
      if (e.type === "pointerdown") pointer.current = true;
      if (e.type === "pointerup" || e.type === "pointercancel") pointer.current = false;
      if (e.type === "compositionstart") composing.current = true;
      if (e.type === "compositionend") composing.current = false;
      suppressedUntil.current = performance.now() + 800; schedule();
    };
    const events = ["wheel", "pointerdown", "pointerup", "pointercancel", "keydown", "compositionstart", "compositionend"];
    events.forEach(name => window.addEventListener(name, interact, { capture: true, passive: true }));
    window.addEventListener("scroll", schedule, true); window.addEventListener("resize", schedule);
    const observer = new ResizeObserver(schedule); observer.observe(document.body);
    schedule();
    return () => { alive = false; cancelAnimationFrame(frame); if (timer) clearTimeout(timer); observer.disconnect(); events.forEach(name => window.removeEventListener(name, interact, true)); window.removeEventListener("scroll", schedule, true); window.removeEventListener("resize", schedule); };
  }, [props.registry, focusKey, props.context, props.viewRevision, props.suspendScroll]);
  return createPortal(<div className="ai-focus-layer" aria-hidden="true">
    {painted.map(p => <div key={p.id} className="ai-focus-clip" style={{ left: p.viewport.left, top: p.viewport.top, width: p.viewport.width, height: p.viewport.height }}><div data-ai-anchor={p.id} data-ai-change={p.kind} className={`ai-focus-outline is-${p.phase}${p.kind ? ` is-${p.kind}` : ""}${p.exiting ? " is-exiting" : ""}`} style={{ left: p.box.left - p.viewport.left, top: p.box.top - p.viewport.top, width: p.box.width, height: p.box.height }}>{p.label && <span className="ai-focus-label">{p.label}</span>}</div></div>)}
    {titles.map(({ table, view }) => <div key={table.id} className="ai-table-title" data-ai-sticky-table={table.id} style={{ left: Math.max(view.left, table.body.left), top: view.top, maxWidth: Math.min(view.width, table.body.width) }}>{table.title}</div>)}
  </div>, document.body);
}

export function ReviewAiFocus({ rootRef, ...props }: Omit<Props, "registry"> & { rootRef: RefObject<HTMLElement | null> }) {
  const registry = useMemo<TargetAnchorRegistry>(() => ({
    resolve: anchor => {
      if (anchor.kind !== "review_field") return null;
      const element = [...(rootRef.current?.querySelectorAll<HTMLElement>("[data-review-path]") ?? [])].find(e => e.dataset.reviewPath === `fields.${anchor.fieldId}`);
      return element ? { element, box: element.getBoundingClientRect() } : null;
    },
    tables: () => [...(rootRef.current?.querySelectorAll<HTMLElement>("[data-ai-table-id]") ?? [])].flatMap(element => {
      const header = element.querySelector<HTMLElement>("header"), body = element.querySelector<HTMLElement>("tbody");
      return header && body ? [{ id: element.dataset.aiTableId!, title: element.getAttribute("aria-label") ?? "", header: header.getBoundingClientRect(), body: body.getBoundingClientRect(), element: body, headerElement: header }] : [];
    }),
  }), [rootRef]);
  return <AiFocusLayer {...props} registry={registry} />;
}

export function TemplateAiFocus({ canvasRef, cells, width, height, sections, groups, ...props }: Omit<Props, "registry"> & { canvasRef: RefObject<HTMLCanvasElement | null>; cells: LayoutCell[]; width: number; height: number; sections: LayoutRecordSection[]; groups: LayoutRecordGroup[] }) {
  const regions = props.focus?.regions;
  const registry = useMemo<TargetAnchorRegistry>(() => {
    const cellMap = new Map(cells.map(c => [c.id, c]));
    const regionMap = new Map(regions?.map(r => [r.anchorId, r]));
    const box = (cell: Pick<LayoutCell, "x" | "y" | "width" | "height">): Box => { const r = canvasRef.current!.getBoundingClientRect(); return { left: r.left + cell.x / width * r.width, top: r.top + cell.y / height * r.height, width: cell.width / width * r.width, height: cell.height / height * r.height }; };
    return {
      resolve: anchor => {
        if (!canvasRef.current) return null;
        const region = regionMap.get(anchor.anchorId);
        if (region && region.cellId === anchor.cellId) return { element: canvasRef.current, box: box(region), kind: region.kind, label: region.label };
        const cell = anchor.cellId && cellMap.get(anchor.cellId);
        return cell ? { element: canvasRef.current, box: box(cell) } : null;
      },
      tables: () => !canvasRef.current ? [] : sections.flatMap(section => {
        const headers = (section.recordLabelCellIds ?? []).flatMap(id => { const c = cellMap.get(id); return c ? [box(c)] : []; });
        const body = groups.filter(g => g.sectionId === section.id).flatMap(g => g.inputCellIds.flatMap(id => { const c = cellMap.get(id); return c ? [box(c)] : []; }));
        return headers.length && body.length ? [{ id: section.id, title: section.displayName || section.title, header: union(headers), body: union(body), element: canvasRef.current! }] : [];
      }),
    };
  }, [canvasRef, cells, width, height, sections, groups, regions]);
  return <AiFocusLayer {...props} registry={registry} />;
}
