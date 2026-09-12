import { describe, expect, test } from "vitest";
import type { LayoutCell, LayoutDefinition } from "../types";
import { attachHeading, enclosedHeadingChildren, moveLayoutCell, pointInCell, polygonCell, uniqueMeaningEntry } from "./layoutAuthoring";
import { normalizeLayout } from "./LayoutEditor";

const cell = (id: string, role: LayoutCell["role"] = "input", extra: Partial<LayoutCell> = {}): LayoutCell => ({
  id, role, x: 10, y: 10, width: 40, height: 40, text: "", confidence: 1,
  textConfidence: null, source: "manual", ...extra,
});
const layout = (cells: LayoutCell[]): LayoutDefinition => ({ canonicalWidth: 300, canonicalHeight: 200,
  cells, relations: [], detector: "test", ocrAvailable: false, allowCrossAxisLabels: true, warnings: [] });

test("moving a merged frame translates parts and row bounds without reassigning members", () => {
  const initial = layout([cell("merged", "input", { width: 80 }), cell("other", "input", { x: 120 })]);
  initial.cellMerges = [{ id: "merge", parentCellId: "merged", direction: "horizontal",
    parts: [cell("left"), cell("right", "input", { x: 50 })].map(cell => ({ cell, partType: "input", literal: "" })) }];
  initial.recordGroups = [{ id: "row", sectionId: "table", order: 1, x: 10, y: 10, width: 150, height: 40,
    inputCellIds: ["merged", "other"], recordKeyField: "index", recordKeyValue: null, recordKeyCellId: null, source: "manual" }];
  const moved = moveLayoutCell(initial, "merged", 20, 30);
  expect(moved.cells[0]).toMatchObject({ id: "merged", x: 30, y: 40, width: 80, height: 40 });
  expect(moved.cells[1]).toEqual(initial.cells[1]);
  expect(moved.cellMerges![0].parts.map(part => [part.cell.id, part.cell.x, part.cell.y])).toEqual([
    ["left", 30, 40], ["right", 70, 40],
  ]);
  expect(moved.recordGroups![0]).toEqual({ ...initial.recordGroups[0], x: 30, y: 10, width: 130, height: 70 });
  expect(moved.relations).toBe(initial.relations);
  expect(initial.cellMerges[0].parts[0].cell.x).toBe(10);
});

test("meaning names increment at creation and continue after save/reload in the same layout", () => {
  let current = layout([cell("label", "label", { text: "漢字" })]);
  for (const expected of ["漢字", "漢字2", "漢字3"]) {
    const entry = uniqueMeaningEntry("漢字", current);
    expect(entry.label).toBe(expected);
    expect(entry.targetPath).toBe(`customFields.${expected}`);
    current = { ...current, labelPool: [...(current.labelPool ?? []), entry] };
  }
  const restored = normalizeLayout(JSON.parse(JSON.stringify(current)));
  expect(uniqueMeaningEntry("漢字", restored).label).toBe("漢字4");
  expect(restored.cells[0].text).toBe("漢字");
  expect(uniqueMeaningEntry("漢字", layout([])).label).toBe("漢字");
  expect(uniqueMeaningEntry("フリガナ", restored).label).toBe("フリガナ");
});

test("reserves names of mapped legacy scalar targets but ignores unrelated target candidates", () => {
  const current = layout([cell("input")]);
  current.relations = [{ id: "legacy", inputCellId: "input", labelCellIds: [],
    targetPath: "document.cattleName", axis: "horizontal", order: 1 }];
  const targets = [{ path: "document.cattleName", label: "漢字", section: "legacy" },
    { path: "customFields.unused", label: "漢字2", section: "unused" }];
  expect(uniqueMeaningEntry("漢字", current, targets).label).toBe("漢字2");
  expect(current.relations[0].targetPath).toBe("document.cattleName");
});

describe("polygon geometry", () => {
  const vertices = [{ x: 10, y: 10 }, { x: 50, y: 10 }, { x: 10, y: 50 }];
  test("persists vertices and selects inside, boundary, and excludes the bounding box corner", () => {
    const polygon = polygonCell(vertices, "p")!;
    const restored = normalizeLayout(JSON.parse(JSON.stringify(layout([polygon]))));
    expect(restored.cells[0].vertices).toEqual(vertices);
    expect(pointInCell({ x: 15, y: 15 }, polygon)).toBe(true);
    expect(pointInCell({ x: 30, y: 30 }, polygon)).toBe(true);
    expect(pointInCell({ x: 45, y: 45 }, polygon)).toBe(false);
    expect(pointInCell({ x: 45, y: 45 }, cell("rect"))).toBe(true);
  });
  test("rejects fewer than three vertices, zero area, duplicates and self crossings", () => {
    expect(polygonCell(vertices.slice(0, 2), "p")).toBeNull();
    expect(polygonCell([...vertices, vertices[0]], "p")).toBeNull();
    expect(polygonCell([{ x: 10, y: 10 }, { x: 20, y: 20 }, { x: 30, y: 30 }], "p")).toBeNull();
    expect(polygonCell([{ x: 0, y: 0 }, { x: 30, y: 30 }, { x: 0, y: 30 }, { x: 30, y: 0 }], "p")).toBeNull();
  });
});

test("heading hierarchy persists with one parent and rejects cycles including legacy edges", () => {
  let current = layout([cell("A", "parent_label"), cell("B", "parent_label"), cell("C", "label")]);
  current = attachHeading(current, "A", ["B"]);
  current = attachHeading(current, "B", ["C"]);
  const restored = normalizeLayout(JSON.parse(JSON.stringify(current)));
  expect(restored.cells.map(cell => cell.parentLabelCellId)).toEqual([undefined, "A", "B"]);
  expect(() => attachHeading(restored, "A", ["A"])).toThrow(/循環/);
  expect(() => attachHeading(restored, "B", ["A"])).toThrow(/循環/);
  expect(() => attachHeading(restored, "C", ["A"])).toThrow(/循環/);
});

test("virtual heading nests current merged and mapped fields without changing meaning or merge identity", () => {
  const heading = cell("virtual", "parent_label", { text: "記入日", virtual: true, x: 0, y: 0, width: 8, height: 8 });
  let current = layout([heading, cell("label", "label"), cell("merged"), cell("mark", "mark"), cell("normal", "parent_label")]);
  current.cellMerges = [{ id: "merge", parentCellId: "merged", direction: "horizontal",
    parts: [cell("year"), cell("month")].map(cell => ({ cell, partType: "input", literal: "" })) }];
  current.relations = [{ id: "existing", inputCellId: "merged", labelCellIds: ["label"],
    axis: "horizontal", order: 1, targetPath: "customFields.漢字2" }];
  current = normalizeLayout(current);
  const before = structuredClone(current);
  const selected = enclosedHeadingChildren(current, { x: 0, y: 0, width: 60, height: 60 });
  expect(selected).toEqual(["label", "merged", "mark"]);
  current = attachHeading(current, "virtual", selected);
  current = attachHeading(current, "normal", ["virtual"]);
  current = normalizeLayout(JSON.parse(JSON.stringify(current)));
  expect(current.cellMerges).toEqual(before.cellMerges);
  expect(current.relations[0]).toEqual({ ...before.relations[0], parentLabelCellId: "virtual" });
  expect(current.cells.find(cell => cell.id === "virtual")?.parentLabelCellId).toBe("normal");
  expect(current.cells.filter(cell => selected.includes(cell.id)).every(cell => cell.parentLabelCellId === "virtual")).toBe(true);
  expect(current.cells.map(cell => cell.id)).toEqual(before.cells.map(cell => cell.id));
});
