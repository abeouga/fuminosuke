import type { LayoutCell, LayoutRecordGroup, LayoutRecordSection, LayoutRelation, } from "../types";
import { type Point, segmentIntersectsRect, distanceToCenterSquared, intervalOverlap, } from "./layoutEditorGeometry";
export function recordSectionDisplayName(section: LayoutRecordSection, cellsById?: ReadonlyMap<string, LayoutCell>): string {
    const displayName = section.displayName?.trim();
    if (displayName)
        return displayName;
    const headingText = cellsById?.get(section.parentLabelCellId ?? "")?.text.trim();
    if (headingText)
        return headingText;
    return section.title?.trim() || "明細";
}
function recordSectionBand(section: LayoutRecordSection, cells: readonly LayoutCell[]): {
    start: Point;
    end: Point;
    anchor: Point;
} | null {
    const cellById = new Map(cells.map((cell) => [cell.id, cell]));
    const anchors = section.recordLabelCellIds
        .map((labelId) => cellById.get(labelId))
        .filter((cell): cell is LayoutCell => Boolean(cell))
        .sort((first, second) => first.x - second.x || first.y - second.y);
    if (anchors.length === 0)
        return null;
    const y = anchors.reduce((total, cell) => total + cell.y + cell.height / 2, 0) / anchors.length;
    const start = {
        x: anchors[0].x + anchors[0].width / 2,
        y,
    };
    const last = anchors[anchors.length - 1];
    const end = { x: last.x + last.width / 2, y };
    return {
        start,
        end,
        anchor: { x: (start.x + end.x) / 2, y },
    };
}
export function recordSectionAtPoint(location: Point, sections: readonly LayoutRecordSection[], cells: readonly LayoutCell[], tolerance: number): LayoutRecordSection | null {
    return [...sections].reverse().find((section) => {
        const band = recordSectionBand(section, cells);
        return band
            && location.x >= Math.min(band.start.x, band.end.x) - tolerance
            && location.x <= Math.max(band.start.x, band.end.x) + tolerance
            && Math.abs(location.y - band.start.y) <= tolerance;
    }) ?? null;
}
export function recordRowsFromColumnLabels(labelIds: string[], cells: LayoutCell[], relations: LayoutRelation[]): {
    rows: Array<{
        order: number;
        inputCellIds: string[];
    }>;
} | null {
    const cellById = new Map(cells.map((cell) => [cell.id, cell]));
    const genericRows = (): {
        rows: Array<{
            order: number;
            inputCellIds: string[];
        }>;
    } | null => {
        const columns = [...new Set(labelIds)].map((labelId) => ({
            labelId,
            cells: relations
                .filter((relation) => relation.labelCellIds.includes(labelId))
                .flatMap((relation) => {
                const input = cellById.get(relation.inputCellId);
                return input && (input.role === "input" || input.role === "mark")
                    ? [input]
                    : [];
            })
                .filter((cell, index, items) => items.findIndex((item) => item.id === cell.id) === index)
                .sort((first, second) => (first.y + first.height / 2 - (second.y + second.height / 2)
                || first.x - second.x)),
        }));
        const valueColumns = columns.filter((column) => column.cells.length > 0);
        const emptyColumns = columns.filter((column) => column.cells.length === 0);
        const counts = new Set(valueColumns.map((column) => column.cells.length));
        if (columns.length < 2
            || valueColumns.length === 0
            || emptyColumns.length > 1
            || counts.size !== 1
            || valueColumns[0].cells.length === 0)
            return null;
        const rows = Array.from({ length: valueColumns[0].cells.length }, (_, index) => ({
            order: index + 1,
            inputCellIds: valueColumns.map((column) => column.cells[index].id),
        }));
        return { rows };
    };
    return genericRows();
}
export function genericRecordCollectionKey(title: string, sections: LayoutRecordSection[]): string {
    const base = `section_${Array.from(title.normalize("NFKC"))
        .reduce((hash, character) => ((hash * 31) + (character.codePointAt(0) ?? 0)) >>> 0, 2166136261)
        .toString(36)}`;
    const used = new Set(sections.map((section) => section.collectionPath));
    let key = base;
    let suffix = 2;
    while (used.has(`genericRecords.${key}`)) {
        key = `${base}_${suffix}`;
        suffix += 1;
    }
    return key;
}
export function recordLabelCellsCrossedByLine(start: Point, end: Point, labelCells: LayoutCell[]): LayoutCell[] {
    return labelCells
        .filter((cell) => segmentIntersectsRect(start, end, cell, 8))
        .sort((first, second) => first.x - second.x || first.y - second.y);
}
export function recordStructureIsLinked(sections: LayoutRecordSection[], groups: LayoutRecordGroup[]): boolean {
    if (groups.length === 0)
        return false;
    const groupedSectionIds = new Set(groups.map((group) => group.sectionId));
    return [...groupedSectionIds].every((sectionId) => (Boolean((() => {
        const section = sections.find((candidate) => candidate.id === sectionId);
        if (!section || section.recordLabelCellIds.length === 0)
            return false;
        return true;
    })())));
}
export function linkLaneCells(start: Point, end: Point, cells: LayoutCell[]): LayoutCell[] {
    const intersecting = cells.filter((cell) => segmentIntersectsRect(start, end, cell, 12));
    if (intersecting.length <= 1)
        return intersecting;
    const vertical = Math.abs(end.y - start.y) > Math.abs(end.x - start.x);
    const seed = intersecting.reduce((nearest, cell) => (distanceToCenterSquared(cell, end) < distanceToCenterSquared(nearest, end)
        ? cell
        : nearest));
    const longitudinalStart = vertical
        ? Math.min(start.y, end.y)
        : Math.min(start.x, end.x);
    const longitudinalEnd = vertical
        ? Math.max(start.y, end.y)
        : Math.max(start.x, end.x);
    const sameLane = cells.filter((cell) => {
        const laneOverlap = vertical
            ? intervalOverlap(cell.x, cell.width, seed.x, seed.width)
                >= Math.min(cell.width, seed.width) * 0.35
            : intervalOverlap(cell.y, cell.height, seed.y, seed.height)
                >= Math.min(cell.height, seed.height) * 0.35;
        const center = vertical
            ? cell.y + cell.height / 2
            : cell.x + cell.width / 2;
        return laneOverlap
            && center >= longitudinalStart - 12
            && center <= longitudinalEnd + 12;
    });
    const deltaX = end.x - start.x;
    const deltaY = end.y - start.y;
    return sameLane.sort((a, b) => {
        const aProgress = (a.x + a.width / 2 - start.x) * deltaX
            + (a.y + a.height / 2 - start.y) * deltaY;
        const bProgress = (b.x + b.width / 2 - start.x) * deltaX
            + (b.y + b.height / 2 - start.y) * deltaY;
        return aProgress - bProgress;
    });
}
