import type { LayoutCell } from "../types";
import type { Point } from "./layoutEditorGeometry";

/** Only called outside a cell. Never expand polygon bounding boxes into hit areas. */
export function nearbyWireCell(point: Point, cells: LayoutCell[], roles: LayoutCell["role"][], scale: number) {
  const candidates = cells.filter(cell => roles.includes(cell.role) && cell.shapeType !== "polygon")
    .map(cell => ({ cell, distance: Math.hypot(
      Math.max(cell.x - point.x, 0, point.x - cell.x - cell.width),
      Math.max(cell.y - point.y, 0, point.y - cell.y - cell.height),
    ) * scale }))
    .filter(item => item.distance <= 8)
    .sort((a, b) => a.distance - b.distance);
  const ambiguous = candidates.length > 1 && candidates[1].distance - candidates[0].distance < 2;
  return { cell: ambiguous ? undefined : candidates[0]?.cell, ambiguous };
}
