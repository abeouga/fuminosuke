export type Box = { left: number; top: number; width: number; height: number };
export const right = (r: Box) => r.left + r.width;
export const bottom = (r: Box) => r.top + r.height;
export const intersects = (a: Box, b: Box) => Math.min(right(a), right(b)) - Math.max(a.left, b.left) >= 1 && Math.min(bottom(a), bottom(b)) - Math.max(a.top, b.top) >= 1;
export function union(boxes: Box[]): Box { const left = Math.min(...boxes.map(b => b.left)), top = Math.min(...boxes.map(b => b.top)); return { left, top, width: Math.max(...boxes.map(right)) - left, height: Math.max(...boxes.map(bottom)) - top }; }
export function clip(a: Box, b: Box): Box { const left = Math.max(a.left, b.left), top = Math.max(a.top, b.top); return { left, top, width: Math.max(0, Math.min(right(a), right(b)) - left), height: Math.max(0, Math.min(bottom(a), bottom(b)) - top) }; }
export function scrollDelta(target: Box, viewport: Box): { left: number; top: number } {
  const axis = (start: number, size: number, edge: number, extent: number) => {
    if (size > extent) return start - edge;
    if (start >= edge && start + size <= edge + extent) return 0;
    if (start + size < edge || start > edge + extent) return start + size / 2 - edge - extent / 2;
    return start < edge ? start - edge : start + size - edge - extent;
  };
  return { left: axis(target.left, target.width, viewport.left, viewport.width), top: axis(target.top, target.height, viewport.top, viewport.height) };
}
export function scrollTarget(boxes: Box[], viewport: Box): Box {
  const all = union(boxes);
  if (all.width <= viewport.width && all.height <= viewport.height) return all;
  return boxes.find(box => intersects(box, viewport)) ?? boxes[0];
}
export function stickyTitle(header: Box, body: Box, viewport: Box, headerViewport: Box = viewport): boolean { return bottom(header) <= headerViewport.top && intersects(body, viewport); }
