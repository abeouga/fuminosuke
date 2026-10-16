import type { LayoutCell, LayoutCellMerge, LayoutCellMergePart, LayoutDefinition } from "../types";
import { type Rect, intervalOverlap } from "./layoutEditorGeometry";

export function removeLayoutCells(
  layout: LayoutDefinition,
  cellIds: Iterable<string>,
): LayoutDefinition {
  const removedIds = new Set(cellIds);
  if (removedIds.size === 0) return layout;
  const invalidatedSectionIds = new Set(
    (layout.recordSections ?? [])
      .filter((section) => section.recordLabelCellIds.some((id) => removedIds.has(id)))
      .map((section) => section.id),
  );
  return {
    ...layout,
    cells: layout.cells
      .filter((cell) => !removedIds.has(cell.id))
      .map((cell) => removedIds.has(cell.parentLabelCellId ?? "")
        ? { ...cell, parentLabelCellId: null }
        : cell),
    cellMerges: layout.cellMerges?.filter((merge) => (
      !removedIds.has(merge.parentCellId)
      && !merge.parts.some((part) => removedIds.has(part.cell.id))
    )),
    relations: layout.relations
      .filter((relation) => (
        !removedIds.has(relation.inputCellId)
        && !relation.labelCellIds.some((id) => removedIds.has(id))
      ))
      .map((relation) => removedIds.has(relation.parentLabelCellId ?? "")
        ? { ...relation, parentLabelCellId: null }
        : relation),
    recordGroups: (layout.recordGroups ?? [])
      .filter((group) => !invalidatedSectionIds.has(group.sectionId))
      .map((group) => ({
        ...group,
        inputCellIds: group.inputCellIds.filter((id) => !removedIds.has(id)),
        recordKeyCellId: removedIds.has(group.recordKeyCellId ?? "")
          ? null
          : group.recordKeyCellId,
      }))
      .filter((group) => group.inputCellIds.length > 0),
    recordSections: (layout.recordSections ?? []).map((section) => ({
      ...section,
      recordLabelCellIds: invalidatedSectionIds.has(section.id)
        ? []
        : section.recordLabelCellIds,
      parentLabelCellId: removedIds.has(section.parentLabelCellId ?? "")
        ? null
        : section.parentLabelCellId,
      rowKeyCellIds: section.rowKeyCellIds?.filter((id) => !removedIds.has(id)),
    })),
    recordStructureVerified: false,
  };
}
type ManualCellMergePlan = {
  direction: "horizontal" | "vertical";
  orderedCells: LayoutCell[];
  bounds: Rect;
};
export function unselectedCellsInsideMerge(
  cells: LayoutCell[], selected: LayoutCell[], bounds: Rect,
): LayoutCell[] {
  const selectedIds = new Set(selected.map((cell) => cell.id));
  return cells.filter((cell) => !selectedIds.has(cell.id)
    && Math.min(cell.x + cell.width, bounds.x + bounds.width) - Math.max(cell.x, bounds.x) > 1
    && Math.min(cell.y + cell.height, bounds.y + bounds.height) - Math.max(cell.y, bounds.y) > 1);
}
export function planManualCellMerge(
  cells: LayoutCell[],
  canonicalWidth: number,
  canonicalHeight: number,
): ManualCellMergePlan | string {
  if (cells.length < 2) return "2つ以上のセルを選択してください。";
  const bounds: Rect = {
    x: Math.min(...cells.map((cell) => cell.x)),
    y: Math.min(...cells.map((cell) => cell.y)),
    width: Math.max(...cells.map((cell) => cell.x + cell.width))
      - Math.min(...cells.map((cell) => cell.x)),
    height: Math.max(...cells.map((cell) => cell.y + cell.height))
      - Math.min(...cells.map((cell) => cell.y)),
  };
  const gapLimit = Math.max(8, Math.min(canonicalWidth, canonicalHeight) * 0.012);
  const horizontalCells = [...cells].sort((first, second) => first.x - second.x || first.y - second.y);
  const verticalCells = [...cells].sort((first, second) => first.y - second.y || first.x - second.x);
  const horizontal = horizontalCells.every((cell, index) => {
    if (index === 0) return true;
    const previous = horizontalCells[index - 1];
    const overlap = intervalOverlap(cell.y, cell.height, previous.y, previous.height);
    const gap = cell.x - (previous.x + previous.width);
    return overlap >= Math.min(cell.height, previous.height) * 0.25 && gap <= gapLimit;
  });
  const vertical = verticalCells.every((cell, index) => {
    if (index === 0) return true;
    const previous = verticalCells[index - 1];
    const overlap = intervalOverlap(cell.x, cell.width, previous.x, previous.width);
    const gap = cell.y - (previous.y + previous.height);
    return overlap >= Math.min(cell.width, previous.width) * 0.25 && gap <= gapLimit;
  });
  if (!horizontal && !vertical) {
    return "離れているセル、または読み取り順序を一意に決められないセルは結合できません。";
  }
  const direction = horizontal && vertical
    ? (bounds.width >= bounds.height ? "horizontal" : "vertical")
    : horizontal ? "horizontal" : "vertical";
  return {
    direction,
    orderedCells: direction === "horizontal" ? horizontalCells : verticalCells,
    bounds,
  };
}
export function cloneCellMerge(merge: LayoutCellMerge): LayoutCellMerge {
  return {
    ...merge,
    parts: merge.parts.map((part) => ({
      ...part,
      cell: {
        ...part.cell,
        markSets: (part.cell.markSets ?? []).map((markSet) => ({
          ...markSet,
          options: markSet.options.map((option) => ({ ...option })),
        })),
        compositeSubfields: (part.cell.compositeSubfields ?? []).map((subfield) => ({ ...subfield })),
      },
    })),
  };
}
function mergePartMatchesCell(part: LayoutCellMergePart, cell: LayoutCell): boolean {
  if (part.partType === "input") return cell.role === "input";
  if (part.partType === "ignored") {
    return cell.role === "ignored" && !cell.text.trim() && !cell.printedLabelText?.trim();
  }
  const expected = part.literal.normalize("NFKC").replace(/\s+/g, "").toLowerCase();
  const actual = (cell.text.trim() || cell.printedLabelText?.trim() || "")
    .normalize("NFKC")
    .replace(/\s+/g, "")
    .toLowerCase();
  return Boolean(expected) && expected === actual;
}
export function findCopiedMergeTargetCells(
  merge: LayoutCellMerge,
  selectedCells: LayoutCell[],
  activeCells: LayoutCell[],
  mergedParentIds: ReadonlySet<string>,
  canonicalWidth: number,
  canonicalHeight: number,
): LayoutCell[] | string {
  if (merge.parts.length < 2) return "コピーした結合パターンが不完全です。";
  if (selectedCells.length > 1) {
    if (selectedCells.length !== merge.parts.length) {
      return `コピー元と同じ${merge.parts.length}個のセルを選択してください。`;
    }
    const plan = planManualCellMerge(selectedCells, canonicalWidth, canonicalHeight);
    if (typeof plan === "string") return plan;
    if (
      plan.direction !== merge.direction
      || !plan.orderedCells.every((cell, index) => mergePartMatchesCell(merge.parts[index], cell))
    ) return "選択したセルの並びまたは区分がコピー元と一致しません。";
    return plan.orderedCells;
  }
  if (selectedCells.length === 0) {
    return "貼り付け先の先頭セル、または直前に結合したセルを選択してください。";
  }

  const selected = selectedCells[0];
  const sourceFirst = merge.parts[0].cell;
  const availableCells = activeCells.filter((cell) => !mergedParentIds.has(cell.id));
  let anchorCandidates: LayoutCell[];
  if (mergedParentIds.has(selected.id)) {
    const sourceLeft = Math.min(...merge.parts.map((part) => part.cell.x));
    const sourceTop = Math.min(...merge.parts.map((part) => part.cell.y));
    const expectedX = selected.x + sourceFirst.x - sourceLeft;
    const expectedY = selected.y + sourceFirst.y - sourceTop;
    anchorCandidates = availableCells
      .filter((cell) => (
        cell.y > selected.y + selected.height * 0.25
        && mergePartMatchesCell(merge.parts[0], cell)
      ))
      .sort((first, second) => (
        first.y - second.y
        || Math.abs(first.x - expectedX) - Math.abs(second.x - expectedX)
        || Math.abs(first.y - expectedY) - Math.abs(second.y - expectedY)
      ));
  } else {
    if (!mergePartMatchesCell(merge.parts[0], selected)) {
      return "貼り付け先では、コピー元の並びに対応する先頭セルを選択してください。";
    }
    anchorCandidates = [selected];
  }

  const baseTolerance = Math.max(6, Math.min(canonicalWidth, canonicalHeight) * 0.008);
  for (const anchor of anchorCandidates) {
    const offsetX = anchor.x - sourceFirst.x;
    const offsetY = anchor.y - sourceFirst.y;
    const usedIds = new Set<string>();
    const matched: LayoutCell[] = [];
    let complete = true;
    for (const part of merge.parts) {
      const expectedX = part.cell.x + offsetX;
      const expectedY = part.cell.y + offsetY;
      const candidate = availableCells
        .filter((cell) => (
          !usedIds.has(cell.id)
          && mergePartMatchesCell(part, cell)
          && Math.abs(cell.x - expectedX) <= Math.max(baseTolerance, part.cell.width * 0.3)
          && Math.abs(cell.y - expectedY) <= Math.max(baseTolerance, part.cell.height * 0.3)
          && Math.abs(cell.width - part.cell.width) <= Math.max(baseTolerance, part.cell.width * 0.35)
          && Math.abs(cell.height - part.cell.height) <= Math.max(baseTolerance, part.cell.height * 0.35)
        ))
        .sort((first, second) => (
          Math.abs(first.x - expectedX) + Math.abs(first.y - expectedY)
          - Math.abs(second.x - expectedX) - Math.abs(second.y - expectedY)
        ))[0];
      if (!candidate) {
        complete = false;
        break;
      }
      usedIds.add(candidate.id);
      matched.push(candidate);
    }
    if (!complete) continue;
    const plan = planManualCellMerge(matched, canonicalWidth, canonicalHeight);
    if (typeof plan !== "string" && plan.direction === merge.direction) return plan.orderedCells;
  }
  return "下にコピー元と同じ並びの未結合セルが見つかりませんでした。";
}
