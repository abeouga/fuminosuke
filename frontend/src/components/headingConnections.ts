import type { LayoutCell, LayoutDefinition } from "../types";

type Point = { x: number; y: number };
export type HeadingConnection = {
  key: string;
  start: Point;
  end: Point;
  hoverIds: string[];
  hoverBand?: { start: Point; end: Point };
  color: string;
};
const center = (cell: LayoutCell): Point => ({ x: cell.x + cell.width / 2, y: cell.y + cell.height / 2 });

/** View-only connections. Use projected cells and never infer or modify ownership. */
export function buildHeadingConnections(layout: LayoutDefinition, cells: ReadonlyMap<string, LayoutCell>): HeadingConnection[] {
  const connections = new Map<string, HeadingConnection>();
  const addCell = (parentId: string | null | undefined, childId: string, color: string) => {
    const parent = cells.get(parentId ?? ""), child = cells.get(childId);
    if (!parent || !child) return;
    const key = JSON.stringify([parent.id, child.id]);
    if (!connections.has(key)) connections.set(key, {
      key, start: center(parent), end: center(child), hoverIds: [parent.id, child.id], color,
    });
  };
  layout.cells.forEach(cell => addCell(cell.parentLabelCellId, cell.id, "#16805d"));
  layout.relations.forEach(relation => {
    const children = relation.labelCellIds.length ? relation.labelCellIds : [relation.inputCellId];
    children.forEach(id => addCell(relation.parentLabelCellId, id, "#16805d"));
  });
  (layout.recordSections ?? []).forEach(section => {
    const parent = cells.get(section.parentLabelCellId ?? "");
    const anchors = section.recordLabelCellIds.map(id => cells.get(id))
      .filter((cell): cell is LayoutCell => !!cell).sort((a, b) => a.x - b.x || a.y - b.y);
    if (!parent || !anchors.length) return;
    const key = `section:${section.id}`;
    const bandY = anchors.reduce((sum, cell) => sum + center(cell).y, 0) / anchors.length;
    connections.set(key, { key, start: center(parent),
      end: { x: (center(anchors[0]).x + center(anchors[anchors.length - 1]).x) / 2,
        y: bandY },
      hoverBand: { start: { x: center(anchors[0]).x, y: bandY },
        end: { x: center(anchors[anchors.length - 1]).x, y: bandY } },
      hoverIds: [parent.id, ...anchors.map(cell => cell.id)], color: "#16805d" });
  });
  return [...connections.values()];
}

function controls(start: Point, end: Point): [Point, Point] {
  return Math.abs(end.x - start.x) >= Math.abs(end.y - start.y)
    ? [{ x: (start.x + end.x) / 2, y: start.y }, { x: (start.x + end.x) / 2, y: end.y }]
    : [{ x: start.x, y: (start.y + end.y) / 2 }, { x: end.x, y: (start.y + end.y) / 2 }];
}

export function traceHeadingConnection(context: CanvasRenderingContext2D, start: Point, end: Point) {
  context.moveTo(start.x, start.y);
  if (start.x === end.x || start.y === end.y) context.lineTo(end.x, end.y);
  else {
    const [a, b] = controls(start, end);
    context.bezierCurveTo(a.x, a.y, b.x, b.y, end.x, end.y);
  }
}

/** Sample the same curve for hover/eraser hit testing, not the old diagonal chord. */
export function headingConnectionSegments(start: Point, end: Point): { start: Point; end: Point }[] {
  if (start.x === end.x || start.y === end.y) return [{ start, end }];
  const [a, b] = controls(start, end);
  const count = Math.max(16, Math.min(256, Math.ceil(Math.hypot(end.x - start.x, end.y - start.y) / 12)));
  let previous = start;
  return Array.from({ length: count }, (_, index) => {
    const t = (index + 1) / count, u = 1 - t;
    const next = { x: u ** 3 * start.x + 3 * u ** 2 * t * a.x + 3 * u * t ** 2 * b.x + t ** 3 * end.x,
      y: u ** 3 * start.y + 3 * u ** 2 * t * a.y + 3 * u * t ** 2 * b.y + t ** 3 * end.y };
    const segment = { start: previous, end: next };
    previous = next;
    return segment;
  });
}
