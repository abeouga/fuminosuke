import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";

export type Corner = "top-left" | "top-right" | "bottom-left" | "bottom-right";
const CORNERS: Corner[] = ["top-left", "top-right", "bottom-left", "bottom-right"];
const MARGIN = 12;

function storedCorner(key: string, fallback: Corner): Corner {
  try {
    const value = window.localStorage.getItem(key) as Corner | null;
    return value && CORNERS.includes(value) ? value : fallback;
  } catch { return fallback; }
}

/** Height of the sticky step tabs above the preview; panels stay below them. */
function stickyTop(): number {
  const bar = document.querySelector(".layout-wizard-steps");
  if (!(bar instanceof HTMLElement) || getComputedStyle(bar).position !== "sticky") return 0;
  return (parseFloat(getComputedStyle(bar).top) || 0) + bar.offsetHeight;
}

/**
 * One zero-height sticky layer per corner inside the preview frame. The browser keeps it
 * in place while scrolling (no script, no lag) and never lets it leave the frame; the
 * panels of that corner stack inside it in `order`.
 */
const docks = new WeakMap<HTMLElement, Map<Corner, HTMLElement>>();
function dockFor(host: HTMLElement, corner: Corner): HTMLElement {
  const byCorner = docks.get(host) ?? new Map<Corner, HTMLElement>();
  docks.set(host, byCorner);
  const existing = byCorner.get(corner);
  if (existing?.isConnected) return existing;
  const top = corner.startsWith("top"), left = corner.endsWith("left");
  const layer = document.createElement("div");
  layer.className = `corner-dock is-${corner}`;
  Object.assign(layer.style, {
    position: "sticky", height: "0", margin: "0", zIndex: "66", pointerEvents: "none",
    ...(top ? { top: `${stickyTop()}px` } : { bottom: "0" }),
  });
  const stack = document.createElement("div");
  stack.className = "corner-stack";
  Object.assign(stack.style, {
    position: "absolute", display: "flex", gap: "8px",
    flexDirection: top ? "column" : "column-reverse",
    alignItems: left ? "flex-start" : "flex-end",
    ...(top ? { top: `${MARGIN}px` } : { bottom: `${MARGIN}px` }),
    ...(left ? { left: `${MARGIN}px` } : { right: `${MARGIN}px` }),
  });
  layer.appendChild(stack);
  if (top) host.prepend(layer); else host.append(layer);
  byCorner.set(corner, stack);
  return stack;
}

/**
 * Keeps a floating panel in one corner of the preview frame while scrolling; the panel
 * is dragged or arrow-keyed to another corner. Panels sharing a corner stack by `order`
 * (lower sits nearest the corner).
 */
export function useCornerPin(host: () => HTMLElement | null, storageKey: string, fallback: Corner, order = 0) {
  const [corner, setCornerState] = useState<Corner>(() => storedCorner(storageKey, fallback));
  const [drag, setDrag] = useState<{ x: number; y: number; dx: number; dy: number } | null>(null);
  const [hostElement, setHostElement] = useState<HTMLElement | null>(() => host());

  useEffect(() => {
    // The preview mounts after the panel on some steps and is replaced between steps.
    const check = () => setHostElement((current) => {
      const next = host();
      return next === current && (!next || next.isConnected) ? current : next;
    });
    check();
    const timer = window.setInterval(check, 500);
    return () => window.clearInterval(timer);
  }, [host]);
  useEffect(() => {
    // Keep the top layers below the step tabs when their height changes (narrow screens).
    const bar = document.querySelector(".layout-wizard-steps");
    if (!hostElement || !bar || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      for (const top of ["top-left", "top-right"] as const) {
        const layer = docks.get(hostElement)?.get(top)?.parentElement;
        if (layer) layer.style.top = `${stickyTop()}px`;
      }
    });
    observer.observe(bar);
    return () => observer.disconnect();
  }, [hostElement]);

  const container = hostElement?.isConnected ? dockFor(hostElement, corner) : null;
  // Moving to another corner remounts the panel there; keep keyboard focus on its handle.
  const refocus = useRef(false);
  useEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    document.querySelector<HTMLElement>(`[data-corner-pin="${storageKey}"] [data-corner-grip]`)?.focus();
  }, [corner, storageKey]);
  const setCorner = (next: Corner) => {
    setCornerState(next);
    try { window.localStorage.setItem(storageKey, next); } catch { /* per-viewer convenience only */ }
  };
  const top = corner.startsWith("top"), left = corner.endsWith("left");
  const style: CSSProperties = drag
    ? { position: "fixed", left: drag.x - drag.dx, top: drag.y - drag.dy, order }
    : container
      ? { order }
      // Without a preview the panel waits in the window corner.
      : { position: "fixed", [left ? "left" : "right"]: MARGIN, [top ? "top" : "bottom"]: MARGIN };

  const grip = {
    "data-corner-grip": "",
    onPointerDown: (event: PointerEvent<HTMLElement>) => {
      const panel = event.currentTarget.closest("[data-corner-pin]");
      if (event.button !== 0 || !panel) return;
      const box = panel.getBoundingClientRect();
      event.currentTarget.setPointerCapture(event.pointerId);
      setDrag({ x: event.clientX, y: event.clientY, dx: event.clientX - box.left, dy: event.clientY - box.top });
    },
    onPointerMove: (event: PointerEvent<HTMLElement>) => { if (drag) setDrag({ ...drag, x: event.clientX, y: event.clientY }); },
    onPointerUp: (event: PointerEvent<HTMLElement>) => {
      if (!drag) return;
      const box = hostElement?.getBoundingClientRect();
      const area = {
        left: Math.max(0, box?.left ?? 0), right: Math.min(window.innerWidth, box?.right ?? window.innerWidth),
        top: Math.max(stickyTop(), box?.top ?? 0), bottom: Math.min(window.innerHeight, box?.bottom ?? window.innerHeight),
      };
      setDrag(null);
      setCorner(`${event.clientY < (area.top + area.bottom) / 2 ? "top" : "bottom"}-${event.clientX < (area.left + area.right) / 2 ? "left" : "right"}`);
    },
    onPointerCancel: () => setDrag(null),
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
      const index = CORNERS.indexOf(corner);
      const keys: Record<string, number> = { ArrowLeft: index & 2, ArrowRight: (index & 2) | 1, ArrowUp: index & 1, ArrowDown: (index & 1) | 2 };
      if (!(event.key in keys)) return;
      event.preventDefault();
      refocus.current = true;
      setCorner(CORNERS[keys[event.key]]);
    },
  };
  return { corner, style, dragging: drag !== null, grip, pin: { "data-corner-pin": storageKey }, container: container ?? document.body };
}
