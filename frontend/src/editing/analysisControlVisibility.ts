/** A connected DOM control can still be clipped, offscreen or covered by a fixed bar. */
export function analysisControlVisibility(element: HTMLElement | null): { visible: boolean; hitTest: boolean } {
  const unavailable = { visible: false, hitTest: false };
  if (!element?.isConnected || document.visibilityState !== "visible") return unavailable;
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0 || rect.left < 0 || rect.top < 0 || rect.right > innerWidth || rect.bottom > innerHeight) return unavailable;
  for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" || style.opacity === "0") return unavailable;
    const bounds = parent.getBoundingClientRect();
    const left = bounds.left + parent.clientLeft, top = bounds.top + parent.clientTop;
    const right = left + (parent.clientWidth || bounds.width), bottom = top + (parent.clientHeight || bounds.height);
    if (parent !== element && ((/(auto|scroll|hidden|clip)/.test(style.overflowX) && (rect.left < left || rect.right > right)) ||
      (/(auto|scroll|hidden|clip)/.test(style.overflowY) && (rect.top < top || rect.bottom > bottom)))) return unavailable;
  }
  const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
  return { visible: true, hitTest: !!hit && (hit === element || element.contains(hit)) };
}
