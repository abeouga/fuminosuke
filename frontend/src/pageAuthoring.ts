import type { PrintPage } from "./printLayout";
import type { LayoutCell, LayoutDefinition } from "./types";

export type PageView = PrintPage & { imageUrl: string };

/** Display projection only. Persisted geometry and physical IDs stay untouched. */
export function pageDisplayCells(layout: LayoutDefinition, page?: PageView): LayoutCell[] {
  if (!page) return layout.cells;
  const rectangles = new Map(page.fields.map(field => [field.cellId, field.rectangle]));
  const sourceRectangles = new Map(page.cells.map(cell => [cell.sourceRange, cell.rectangle]));
  // Saved merge parts retain their exact source ranges, including after unmerge.
  for (const field of page.fields) {
    for (const part of field.sourceParts ?? []) {
      const rectangle = sourceRectangles.get(part.sourceRange);
      if (rectangle) rectangles.set(part.cellId, rectangle);
    }
  }
  for (const merge of layout.cellMerges ?? []) {
    if (rectangles.has(merge.parentCellId)) continue;
    const parts = merge.parts.map(part => rectangles.get(part.cell.id));
    if (!parts.length || parts.some(part => !part)) continue;
    const resolved = parts.filter(part => part !== undefined);
    const x = Math.min(...resolved.map(part => part.x));
    const y = Math.min(...resolved.map(part => part.y));
    rectangles.set(merge.parentCellId, { x, y,
      width: Math.max(...resolved.map(part => part.x + part.width)) - x,
      height: Math.max(...resolved.map(part => part.y + part.height)) - y });
  }
  return layout.cells.flatMap(cell => {
    const rectangle = rectangles.get(cell.id);
    if (!rectangle) return [];
    const sx = rectangle.width / cell.width, sy = rectangle.height / cell.height;
    return [{ ...cell, ...rectangle,
      vertices: cell.vertices?.map(point => ({
        x: rectangle.x + (point.x - cell.x) * sx,
        y: rectangle.y + (point.y - cell.y) * sy,
      })), markSets: cell.markSets?.map(set => ({
      ...set, options: set.options.map(option => ({ ...option,
        x: option.x * sx, y: option.y * sy, width: option.width * sx, height: option.height * sy,
      })),
    })) }];
  });
}
