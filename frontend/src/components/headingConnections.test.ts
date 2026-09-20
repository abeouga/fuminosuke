import { expect, test, vi } from "vitest";
import type { LayoutCell, LayoutDefinition } from "../types";
import { buildHeadingConnections, headingConnectionSegments, traceHeadingConnection } from "./headingConnections";
import { eraseMeaningParentLinksAlongSegment } from "./LayoutEditor";

const cell = (id: string, x: number, y: number, role: LayoutCell["role"] = "label"): LayoutCell => ({
  id, role, x, y, width: 20, height: 20, text: id, confidence: 1, source: "manual", textConfidence: null,
});

test("collects and deduplicates explicit, legacy and band connections without changing saved data", () => {
  const layout: LayoutDefinition = { canonicalWidth: 600, canonicalHeight: 400, warnings: [],
    detector: "test", ocrAvailable: false, allowCrossAxisLabels: true, cells: [
    cell("heading", 10, 10, "parent_label"), { ...cell("a", 200, 100), parentLabelCellId: "heading" },
    cell("b", 300, 100), cell("input", 400, 100, "input"),
  ], relations: [{ id: "meaning", parentLabelCellId: "heading", inputCellId: "input", labelCellIds: ["a"],
    targetPath: "customFields.test", axis: "horizontal", order: 1 }],
    recordSections: [{ id: "band", parentLabelCellId: "heading", recordLabelCellIds: ["a", "b"],
      title: "一覧", recordType: "generic", collectionPath: "genericRecords.test", rowKeyStrategy: "position", source: "manual" }] };
  const before = structuredClone(layout);
  const routes = buildHeadingConnections(layout, new Map(layout.cells.map(cell => [cell.id, cell])));
  expect(routes).toHaveLength(2);
  expect(routes[0]).toMatchObject({ hoverIds: ["heading", "a"], start: { x: 20, y: 20 }, end: { x: 210, y: 110 } });
  expect(routes[1]).toMatchObject({ hoverIds: ["heading", "a", "b"], end: { x: 260, y: 110 } });
  // A page projection must not fall back to an invisible canonical parent.
  expect(buildHeadingConnections(layout, new Map(layout.cells.slice(1).map(cell => [cell.id, cell])))).toEqual([]);
  expect(layout).toEqual(before);
});

test.each([[0, 0, 240, 120], [240, 120, 0, 0], [0, 0, 120, 240]])("diagonal connections use smooth cubic control points (%s,%s -> %s,%s)", (x, y, ex, ey) => {
  const context = { moveTo: vi.fn(), lineTo: vi.fn(), bezierCurveTo: vi.fn() };
  const start = { x, y }, end = { x: ex, y: ey };
  traceHeadingConnection(context as unknown as CanvasRenderingContext2D, start, end);
  expect(context.moveTo).toHaveBeenCalledWith(x, y);
  expect(context.lineTo).not.toHaveBeenCalled();
  expect(context.bezierCurveTo).toHaveBeenCalledTimes(1);
  const segments = headingConnectionSegments(start, end);
  expect(segments[0].start).toEqual(start);
  expect(segments.at(-1)!.end).toEqual(end);
  expect(segments.every(s => s.start.x >= Math.min(x, ex) && s.end.x <= Math.max(x, ex)
    && s.start.y >= Math.min(y, ey) && s.end.y <= Math.max(y, ey))).toBe(true);
});

test.each([[0, 0, 0, 100], [0, 0, 100, 0]])("axis-aligned connections remain straight (%s,%s -> %s,%s)", (x, y, ex, ey) => {
  const context = { moveTo: vi.fn(), lineTo: vi.fn(), bezierCurveTo: vi.fn() };
  traceHeadingConnection(context as unknown as CanvasRenderingContext2D, { x, y }, { x: ex, y: ey });
  expect(context.lineTo).toHaveBeenCalledWith(ex, ey);
  expect(context.bezierCurveTo).not.toHaveBeenCalled();
});

test("eraser follows the displayed curve rather than the old diagonal and preserves the mapping", () => {
  const cells = new Map([cell("heading", 0, 0, "parent_label"), cell("label", 240, 120)]
    .map(cell => [cell.id, cell]));
  const relations = [{ id: "meaning", inputCellId: "input", labelCellIds: ["label"],
    parentLabelCellId: "heading", targetPath: "customFields.test", axis: "horizontal" as const, order: 1 }];
  // At t=.25 the cubic is (81.25, 28.75); the old straight chord is y=45.625.
  const chord = { x: 81.25, y: 45.625 }, curve = { x: 81.25, y: 28.75 };
  expect(eraseMeaningParentLinksAlongSegment(relations, cells, chord, chord, 3).changed).toBe(false);
  const result = eraseMeaningParentLinksAlongSegment(relations, cells, curve, curve, 3);
  expect(result.relations).toEqual([{ ...relations[0], parentLabelCellId: null }]);
  expect(relations[0].parentLabelCellId).toBe("heading");
});
