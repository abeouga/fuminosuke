import type { PrintPage } from "./printLayout";
import type { LayoutCell, LayoutDefinition } from "./types";

export type PageView = PrintPage & { imageUrl: string };

/** Display projection only. Persisted geometry and physical IDs stay untouched. */
export function pageDisplayCells(layout: LayoutDefinition, page?: PageView): LayoutCell[] {
  if (!page) return layout.cells;
  const rectangles = new Map(page.fields.map(field => [field.cellId, field.rectangle]));
  return layout.cells.flatMap(cell => {
    const rectangle = rectangles.get(cell.id);
    if (!rectangle) return [];
    const sx = rectangle.width / cell.width, sy = rectangle.height / cell.height;
    return [{ ...cell, ...rectangle, markSets: cell.markSets?.map(set => ({
      ...set, options: set.options.map(option => ({ ...option,
        x: option.x * sx, y: option.y * sy, width: option.width * sx, height: option.height * sy,
      })),
    })) }];
  });
}
