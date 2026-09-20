import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";
import {
  buildLabelPoolEntries,
  cellsSelectedByRect,
  createAutoLabelPoolEntry,
  createAutoRepeatedLabelPoolEntries,
  detectCalvingChoiceArea,
  detectCalvingMarkCells,
  drawOcrLabelText,
  expandTargetPaths,
  inferCalvingMarkRowIndexes,
  inferRecordCollectionPath,
  inferRecordTargetPath,
  inferTableColumnTargetPath,
  inferTargetPath,
  LAYOUT_ROLE_STYLE,
  LayoutEditor,
  linkLaneCells,
  meaningLinkLaneCells,
  mergeLabelRelations,
  normalizeLayout,
  recordRowsFromColumnLabels,
  removeLayoutCells,
  recordSectionAtPoint,
  recordSectionDisplayName,
  repairDuplicateCalvingMarkRows,
  resolveAvailableLinkTargetPaths,
  resolveLabelPoolTargetPath,
  segmentIntersectsRect,
  targetPathsFromRecordGroups,
  targetPathsForInputs,
  unassignedMeaningCellIds,
  unselectedCellsInsideMerge,
} from "./LayoutEditor";
import type { DocumentIdentity, FieldMappingTarget, LayoutCell, LayoutDefinition, LayoutMarkSet, LayoutRecordGroup, LayoutRecordSection, LayoutRelation } from "../types";

const targets: FieldMappingTarget[] = Array.from({ length: 10 }, (_, index) => ({
  path: `saleCalves.${index}.priceYen`,
  label: `売却子牛 ${index + 1}行目・価格`,
  section: "売却子牛",
}));
const saleFieldNames = [
  ["parity", "産次"],
  ["ageDays", "日齢"],
  ["weightKg", "体重"],
  ["priceYen", "価格"],
  ["sireName", "種雄牛名"],
  ["saleDate", "売却日"],
  ["notes", "備考"],
] as const;
const allSaleTargets: FieldMappingTarget[] = Array.from(
  { length: 10 },
  (_, index) => saleFieldNames.map(([field, label]) => ({
    path: `saleCalves.${index}.${field}`,
    label: `売却子牛 ${index + 1}行目・${label}`,
    section: "売却子牛",
  })),
).flat();

test("merge rejects an unselected intervening label but permits explicitly selected cells", () => {
  const cells: LayoutCell[] = [
    { id: "xlsx_H16", role: "input", x: 161, y: 327, width: 53, height: 38,
      confidence: 1, text: "", textConfidence: null, source: "manual" },
    { id: "xlsx_L16", role: "label", x: 214, y: 327, width: 13, height: 38,
      confidence: 1, text: "", textConfidence: null, source: "manual" },
    { id: "xlsx_M16", role: "input", x: 227, y: 327, width: 78, height: 38,
      confidence: 1, text: "", textConfidence: null, source: "manual" },
    { id: "outside", role: "input", x: 305, y: 327, width: 20, height: 38,
      confidence: 1, text: "", textConfidence: null, source: "manual" },
  ];
  const original = structuredClone(cells);
  const bounds = { x: 161, y: 327, width: 144, height: 38 };
  expect(unselectedCellsInsideMerge(cells, [cells[0], cells[2]], bounds).map((c) => c.id))
    .toEqual(["xlsx_L16"]);
  expect(unselectedCellsInsideMerge(cells, cells.slice(0, 3), bounds)).toEqual([]);
  expect(cells).toEqual(original);
});

test("uses distinct high-contrast styles for active roles and input styling for legacy numbers", () => {
  const styles = [
    LAYOUT_ROLE_STYLE.label,
    LAYOUT_ROLE_STYLE.parent_label,
    LAYOUT_ROLE_STYLE.input,
    LAYOUT_ROLE_STYLE.mark,
    LAYOUT_ROLE_STYLE.ignored,
  ];
  expect(new Set(styles.map((style) => style.stroke)).size).toBe(styles.length);
  expect(LAYOUT_ROLE_STYLE.label.stroke).toBe("#d13c2f");
  expect(LAYOUT_ROLE_STYLE.input.stroke).toBe("#0067c0");
  expect(LAYOUT_ROLE_STYLE.label.stroke).not.toBe(LAYOUT_ROLE_STYLE.input.stroke);
  expect(LAYOUT_ROLE_STYLE.parent_label.dash).not.toEqual(LAYOUT_ROLE_STYLE.input.dash);
  expect(LAYOUT_ROLE_STYLE.record_key_cell).toEqual(LAYOUT_ROLE_STYLE.input);
  expect(LAYOUT_ROLE_STYLE.column_key_cell).toEqual(LAYOUT_ROLE_STYLE.input);
});

test("finds only input and mark cells without a valid save destination", () => {
  const cells: LayoutCell[] = [
    {
      id: "assigned_input", role: "input", x: 0, y: 0, width: 80, height: 30,
      confidence: 1, text: "", textConfidence: null, source: "manual",
    },
    {
      id: "unassigned_input", role: "input", x: 90, y: 0, width: 80, height: 30,
      confidence: 1, text: "", textConfidence: null, source: "manual",
    },
    {
      id: "assigned_mark", role: "mark", x: 0, y: 40, width: 80, height: 30,
      confidence: 1, text: "", textConfidence: null, source: "manual",
      markSets: [{
        id: "choice", targetPath: "customFields.choice",
        recognitionMode: "manual", selectionMode: "single", options: [],
      }],
    },
    {
      id: "unassigned_mark", role: "mark", x: 90, y: 40, width: 80, height: 30,
      confidence: 1, text: "", textConfidence: null, source: "manual",
      markSets: [{
        id: "choice", targetPath: "",
        recognitionMode: "manual", selectionMode: "single", options: [],
      }],
    },
    {
      id: "ignored", role: "ignored", x: 180, y: 0, width: 80, height: 30,
      confidence: 1, text: "", textConfidence: null, source: "manual",
    },
  ];
  const current: LayoutDefinition = {
    canonicalWidth: 300,
    canonicalHeight: 100,
    detector: "test",
    ocrAvailable: false,
    cells,
    relations: [{
      id: "assigned_relation",
      inputCellId: "assigned_input",
      labelCellIds: [],
      axis: "horizontal",
      order: 1,
      targetPath: "document.cattleName",
    }],
    allowCrossAxisLabels: false,
    warnings: [],
  };

  expect([...unassignedMeaningCellIds(current)]).toEqual([
    "unassigned_input",
    "unassigned_mark",
  ]);
});

test("detects a compact two-row choice area before the handwriting space", () => {
  const width = 300;
  const height = 100;
  const rgba = new Uint8ClampedArray(width * height * 4).fill(235);
  for (let index = 3; index < rgba.length; index += 4) rgba[index] = 255;
  const paint = (left: number, top: number, boxWidth: number, boxHeight: number) => {
    for (let y = top; y < top + boxHeight; y += 1) {
      for (let x = left; x < left + boxWidth; x += 1) {
        const offset = (y * width + x) * 4;
        rgba[offset] = 25;
        rgba[offset + 1] = 25;
        rgba[offset + 2] = 25;
      }
    }
  };
  for (const y of [14, 57]) {
    for (const x of [12, 42, 72]) paint(x, y, 14, 24);
  }
  paint(160, 34, 24, 30);

  const detected = detectCalvingChoiceArea(rgba, width, height);

  expect(detected).not.toBeNull();
  expect(detected?.x).toBeLessThanOrEqual(12);
  expect(detected?.width).toBeLessThan(120);
  expect(detected?.height).toBeGreaterThan(60);
});

test("batch-detects every selected mark cell and falls back per cell", async () => {
  const sets = (rowIndex: number): LayoutMarkSet[] => [{
    id: "outcome",
    targetPath: `calvings.${rowIndex}.outcome`,
    recognitionMode: "automatic",
    selectionMode: "multiple",
    options: [{
      id: "twin",
      label: "双",
      value: "twin",
      x: 1,
      y: 2,
      width: 3,
      height: 4,
    }],
  }];
  const cells: LayoutCell[] = [
    {
      id: "mark_1", role: "mark", x: 0, y: 0, width: 90, height: 50,
      confidence: 1, text: "", textConfidence: null, source: "manual",
      markSets: sets(0),
    },
    {
      id: "ignored_input", role: "input", x: 100, y: 0, width: 90, height: 50,
      confidence: 1, text: "", textConfidence: null, source: "manual",
    },
    {
      id: "mark_2", role: "mark", x: 0, y: 60, width: 90, height: 50,
      confidence: 1, text: "", textConfidence: null, source: "manual",
      markSets: sets(1),
    },
  ];
  const detector = vi.fn(async (
    _source: string,
    cell: LayoutCell,
    _canonicalWidth: number,
    _canonicalHeight: number,
    rowIndex: number,
  ) => {
    if (cell.id === "mark_2") throw new Error("test failure");
    return { sets: sets(rowIndex), usedImageDetection: true };
  });

  const result = await detectCalvingMarkCells("source", cells, 300, 200, detector);

  expect(detector).toHaveBeenCalledTimes(2);
  expect(detector.mock.calls.map((call) => [call[1].id, call[4]])).toEqual([
    ["mark_1", 0],
    ["mark_2", 1],
  ]);
  expect(result.total).toBe(2);
  expect(result.detectedCount).toBe(1);
  expect(result.fallbackCount).toBe(1);
  expect([...result.updates.keys()]).toEqual(["mark_1", "mark_2"]);
  expect(result.updates.get("mark_2")?.[0]?.targetPath).toBe("calvings.1.outcome");
  expect(result.updates.get("mark_2")?.flatMap((set) => set.options)).toHaveLength(6);
});

test("repairs duplicate calving mark rows in two-column reading order", () => {
  const markSets = (): LayoutMarkSet[] => ["outcome", "sex"].map((field) => ({
    id: field,
    targetPath: `calvings.0.${field}`,
    recognitionMode: "automatic",
    selectionMode: "multiple",
    options: [],
  }));
  const markCells: LayoutCell[] = [
    { id: "right_top", role: "mark", x: 200, y: 10, width: 80, height: 40, confidence: 1, text: "", textConfidence: null, source: "manual", markSets: markSets() },
    { id: "left_bottom", role: "mark", x: 10, y: 70, width: 80, height: 40, confidence: 1, text: "", textConfidence: null, source: "manual", markSets: markSets() },
    { id: "right_bottom", role: "mark", x: 200, y: 70, width: 80, height: 40, confidence: 1, text: "", textConfidence: null, source: "manual", markSets: markSets() },
    { id: "left_top", role: "mark", x: 10, y: 10, width: 80, height: 40, confidence: 1, text: "", textConfidence: null, source: "manual", markSets: markSets() },
  ];
  const duplicateRelations: LayoutRelation[] = markCells.map((cell, index) => ({
    id: `relation_${index}`,
    inputCellId: cell.id,
    labelCellIds: [],
    axis: "horizontal",
    order: 1,
    targetPath: "calvings.0.outcome",
  }));
  const duplicateLayout: LayoutDefinition = {
    canonicalWidth: 300,
    canonicalHeight: 120,
    detector: "test",
    ocrAvailable: false,
    cells: markCells,
    relations: duplicateRelations,
    allowCrossAxisLabels: false,
    warnings: [],
  };

  expect([...inferCalvingMarkRowIndexes(duplicateLayout, markCells).entries()])
    .toEqual([
      ["left_top", 0],
      ["left_bottom", 1],
      ["right_top", 2],
      ["right_bottom", 3],
    ]);

  const repaired = repairDuplicateCalvingMarkRows(duplicateLayout);
  const rowsByPosition = [...repaired.layout.cells]
    .sort((first, second) => first.x - second.x || first.y - second.y)
    .map((cell) => cell.markSets?.[0]?.targetPath);
  expect(rowsByPosition).toEqual([
    "calvings.0.outcome",
    "calvings.1.outcome",
    "calvings.2.outcome",
    "calvings.3.outcome",
  ]);
  expect(new Set(repaired.layout.relations.map((relation) => relation.targetPath)).size)
    .toBe(4);
  expect(repaired.repairedCellCount).toBe(3);
});

describe("expandTargetPaths", () => {
  test("expands a repeated table field in drag order", () => {
    expect(expandTargetPaths("saleCalves.0.priceYen", 3, targets)).toEqual([
      "saleCalves.0.priceYen",
      "saleCalves.1.priceYen",
      "saleCalves.2.priceYen",
    ]);
  });

  test("does not duplicate a scalar save target across multiple cells", () => {
    const scalar = { path: "document.cattleName", label: "牛名", section: "基本情報" };
    expect(expandTargetPaths(scalar.path, 2, [...targets, scalar])).toBeNull();
  });

  test("does not expand sale calf fields beyond the ten physical rows", () => {
    expect(expandTargetPaths("saleCalves.0.priceYen", 11, targets)).toBeNull();
  });

  test("infers the first repeated save target from the label text", () => {
    expect(inferTargetPath("価格", 3, targets, new Set())).toBe(
      "saleCalves.0.priceYen",
    );
  });

  test("uses registered OCR aliases when matching a label candidate", () => {
    const aliasTargets: FieldMappingTarget[] = [
      {
        path: "customFields.salePrice",
        label: "価格",
        section: "追加項目",
        aliases: ["売価"],
      },
    ];

    expect(inferTargetPath("売価", 1, aliasTargets, new Set())).toBe(
      "customFields.salePrice",
    );
  });

  test("corrects common PaddleOCR glyph errors in the sale date label", () => {
    expect(inferTargetPath("壳却日", 1, allSaleTargets, new Set())).toBe(
      "saleCalves.0.saleDate",
    );
  });

  test("recognizes the old-form glyph in the calving history label", () => {
    const calvingTargets: FieldMappingTarget[] = Array.from(
      { length: 20 },
      (_, index) => ({
        path: `calvings.${index}.parity`,
        label: `繁殖履歴 ${index + 1}行目・産歴番号`,
        section: "繁殖履歴",
      }),
    );

    expect(inferTargetPath("産歷", 10, calvingTargets, new Set())).toBe(
      "calvings.0.parity",
    );
  });

  test("does not guess when one OCR label contains both parity and age", () => {
    expect(inferTargetPath("産次日令", 1, allSaleTargets, new Set())).toBeNull();
  });
});

describe("targetPathsFromRecordGroups", () => {
  test("binds fields by explicit record membership instead of traversal order", () => {
    const inputs: LayoutCell[] = [
      {
        id: "price_row_2",
        role: "input",
        x: 100,
        y: 120,
        width: 80,
        height: 40,
        confidence: 1,
        text: "",
        textConfidence: null,
        source: "manual",
      },
      {
        id: "price_row_1",
        role: "input",
        x: 100,
        y: 60,
        width: 80,
        height: 40,
        confidence: 1,
        text: "",
        textConfidence: null,
        source: "manual",
      },
    ];
    const sections: LayoutRecordSection[] = [{
      id: "sale",
      title: "売却子牛一覧",
      collectionPath: "saleCalves",
      recordType: "saleCalf",
      recordLabelCellIds: [],
      parentLabelCellId: "sale_label",
      source: "manual",
    }];
    const groups: LayoutRecordGroup[] = [
      {
        id: "row_1",
        sectionId: "sale",
        order: 1,
        x: 90,
        y: 50,
        width: 200,
        height: 50,
        inputCellIds: ["price_row_1"],
        recordKeyField: "parity",
        recordKeyValue: "1",
        source: "manual",
      },
      {
        id: "row_2",
        sectionId: "sale",
        order: 2,
        x: 90,
        y: 110,
        width: 200,
        height: 50,
        inputCellIds: ["price_row_2"],
        recordKeyField: "parity",
        recordKeyValue: "2",
        source: "manual",
      },
    ];

    expect(targetPathsFromRecordGroups(
      "saleCalves.0.priceYen",
      inputs,
      groups,
      sections,
      targets,
    )).toEqual([
      "saleCalves.1.priceYen",
      "saleCalves.0.priceYen",
    ]);
  });
});

describe("resolveLabelPoolTargetPath", () => {
  test("creates a Unicode custom JSON field from an unmatched label", () => {
    expect(createAutoLabelPoolEntry("名号", [
      { path: "document.cattleName", label: "牛名", section: "基本情報" },
    ])).toMatchObject({
      label: "名号",
      targetPath: "customFields.名号",
      section: "自動追加",
    });
  });

  test("creates one safe custom destination per row for an unmatched column label", () => {
    expect(createAutoRepeatedLabelPoolEntries("車種名", 3, [])).toEqual([
      expect.objectContaining({
        label: "車種名 1行目",
        targetPath: "customFields.車種名_1",
        aliases: ["車種名"],
      }),
      expect.objectContaining({
        label: "車種名 2行目",
        targetPath: "customFields.車種名_2",
        aliases: ["車種名"],
      }),
      expect.objectContaining({
        label: "車種名 3行目",
        targetPath: "customFields.車種名_3",
        aliases: ["車種名"],
      }),
    ]);
  });

  test("uses the selected record row when assigning a repeated pool item", () => {
    const priceEntry = buildLabelPoolEntries(allSaleTargets).find(
      (entry) => entry.key === "saleCalves[].priceYen",
    )!;
    const sections: LayoutRecordSection[] = [{
      id: "sale_section",
      title: "売却子牛一覧",
      collectionPath: "saleCalves",
      recordType: "saleCalf",
      recordLabelCellIds: [],
      parentLabelCellId: null,
      source: "manual",
    }];
    const groups: LayoutRecordGroup[] = [{
      id: "sale_row_2",
      sectionId: "sale_section",
      order: 2,
      x: 0,
      y: 0,
      width: 100,
      height: 30,
      inputCellIds: ["price_input_2"],
      recordKeyField: "parity",
      recordKeyValue: "2",
      source: "manual",
    }];

    expect(resolveLabelPoolTargetPath(
      priceEntry,
      "price_input_2",
      groups,
      sections,
      new Set(),
    )).toBe("saleCalves.1.priceYen");
  });
});

describe("generic repeated table rows", () => {
  const labels: LayoutCell[] = [
    { id: "vehicle_label", role: "label", x: 100, y: 0, width: 100, height: 30, confidence: 1, text: "車種名", textConfidence: 1, source: "manual" },
    { id: "chassis_label", role: "label", x: 200, y: 0, width: 100, height: 30, confidence: 1, text: "車台番号", textConfidence: 1, source: "manual" },
  ];
  const rowKeys: LayoutCell[] = [1, 2].map((value, index) => ({
    id: `row_key_${value}`,
    role: "input",
    x: 60,
    y: 30 + index * 40,
    width: 40,
    height: 40,
    confidence: 1,
    text: String(value),
    textConfidence: 1,
    source: "manual",
  }));
  const inputs: LayoutCell[] = [
    { id: "vehicle_1", role: "input", x: 100, y: 30, width: 100, height: 40, confidence: 1, text: "", textConfidence: null, source: "manual" },
    { id: "chassis_1", role: "input", x: 200, y: 30, width: 100, height: 40, confidence: 1, text: "", textConfidence: null, source: "manual" },
    { id: "vehicle_2", role: "input", x: 100, y: 70, width: 100, height: 40, confidence: 1, text: "", textConfidence: null, source: "manual" },
    { id: "chassis_2", role: "input", x: 200, y: 70, width: 100, height: 40, confidence: 1, text: "", textConfidence: null, source: "manual" },
  ];
  const relations: LayoutRelation[] = [
    { id: "vehicle_relation_1", inputCellId: "vehicle_1", labelCellIds: ["vehicle_label"], axis: "vertical", order: 1, targetPath: "customFields.車種名_1" },
    { id: "vehicle_relation_2", inputCellId: "vehicle_2", labelCellIds: ["vehicle_label"], axis: "vertical", order: 2, targetPath: "customFields.車種名_2" },
    { id: "chassis_relation_1", inputCellId: "chassis_1", labelCellIds: ["chassis_label"], axis: "vertical", order: 1, targetPath: "customFields.車台番号_1" },
    { id: "chassis_relation_2", inputCellId: "chassis_2", labelCellIds: ["chassis_label"], axis: "vertical", order: 2, targetPath: "customFields.車台番号_2" },
  ];

  test("groups arbitrary custom-field columns by vertical row order", () => {
    const result = recordRowsFromColumnLabels(
      labels.map((cell) => cell.id),
      [...labels, ...rowKeys, ...inputs],
      relations,
    );
    expect(result).toEqual({
      collectionPath: null,
      rows: [
        { order: 1, inputCellIds: ["vehicle_1", "chassis_1"] },
        { order: 2, inputCellIds: ["vehicle_2", "chassis_2"] },
      ],
    });
  });

  test("includes a numbered column as ordinary meaning-assigned inputs", () => {
    const rankLabel: LayoutCell = {
      id: "rank_label",
      role: "label",
      x: 50,
      y: 0,
      width: 40,
      height: 24,
      confidence: 1,
      text: "希望順位",
      textConfidence: 1,
      source: "manual",
    };
    const result = recordRowsFromColumnLabels(
      [rankLabel.id, ...labels.map((cell) => cell.id)],
      [rankLabel, ...labels, ...rowKeys, ...inputs],
      [
        ...relations,
        ...rowKeys.map((cell, index): LayoutRelation => ({
          id: `rank_relation_${index + 1}`,
          inputCellId: cell.id,
          labelCellIds: [rankLabel.id],
          axis: "vertical",
          order: index + 1,
          targetPath: `customFields.希望順位_${index + 1}`,
        })),
      ],
    );

    expect(result).toEqual({
      collectionPath: null,
      rows: [
        { order: 1, inputCellIds: ["row_key_1", "vehicle_1", "chassis_1"] },
        { order: 2, inputCellIds: ["row_key_2", "vehicle_2", "chassis_2"] },
      ],
    });
  });
});

describe("inferRecordTargetPath", () => {
  test("uses the connected column when the printed label text is empty", () => {
    const rowCells: LayoutCell[] = saleFieldNames.map(([field], index) => ({
      id: `row_1_${field}`,
      role: "input",
      x: 100 + index * 90,
      y: 100,
      width: 80,
      height: 40,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    }));
    const section: LayoutRecordSection = {
      id: "sale",
      title: "売却子牛一覧",
      collectionPath: "saleCalves",
      recordType: "saleCalf",
      recordLabelCellIds: [],
      parentLabelCellId: null,
      source: "manual",
    };
    const group: LayoutRecordGroup = {
      id: "row_1",
      sectionId: "sale",
      order: 1,
      x: 90,
      y: 90,
      width: 640,
      height: 60,
      inputCellIds: rowCells.map((cell) => cell.id),
      recordKeyField: "parity",
      recordKeyValue: "1",
      source: "manual",
    };

    expect(inferRecordTargetPath(
      [rowCells[0]],
      rowCells,
      [group],
      [section],
      allSaleTargets,
    )).toBe("saleCalves.0.parity");
    expect(inferRecordTargetPath(
      [rowCells[5]],
      rowCells,
      [group],
      [section],
      allSaleTargets,
    )).toBe("saleCalves.0.saleDate");
  });
});

describe("inferTableColumnTargetPath", () => {
  const section: LayoutRecordSection = {
    id: "sale",
    title: "売却子牛一覧",
    collectionPath: "saleCalves",
    recordType: "saleCalf",
    recordLabelCellIds: [],
    parentLabelCellId: null,
    source: "manual",
  };
  const rows: LayoutCell[] = Array.from({ length: 3 }, (_, rowIndex) => (
    saleFieldNames.map(([field], columnIndex) => ({
      id: `row_${rowIndex + 1}_${field}`,
      role: "input" as const,
      x: 100 + columnIndex * 90,
      y: 100 + rowIndex * 50,
      width: 80,
      height: 40,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual" as const,
    }))
  )).flat();

  test("uses the table column when the parity label cell is blank", () => {
    const parityColumn = rows.filter((cell) => cell.id.endsWith("_parity"));

    expect(inferTableColumnTargetPath(
      parityColumn,
      rows,
      section,
      allSaleTargets,
    )).toBe("saleCalves.0.parity");
  });

  test("uses the table column when the sale date label is misread", () => {
    const saleDateColumn = rows.filter((cell) => cell.id.endsWith("_saleDate"));

    expect(inferTableColumnTargetPath(
      saleDateColumn,
      rows,
      section,
      allSaleTargets,
    )).toBe("saleCalves.0.saleDate");
  });

  test("does not guess when the visible table has an unexpected column count", () => {
    const extraCell: LayoutCell = {
      ...rows[0],
      id: "unexpected_column",
      x: 800,
    };

    expect(inferTableColumnTargetPath(
      rows.filter((cell) => cell.id.endsWith("_parity")),
      [...rows, extraCell],
      section,
      allSaleTargets,
    )).toBeNull();
  });
});

describe("segmentIntersectsRect", () => {
  test("connects when the drag crosses any part of the input cell", () => {
    expect(segmentIntersectsRect(
      { x: 20, y: 20 },
      { x: 240, y: 70 },
      { x: 180, y: 40, width: 80, height: 80 },
      12,
    )).toBe(true);
  });

  test("ignores cells away from the drag path", () => {
    expect(segmentIntersectsRect(
      { x: 20, y: 20 },
      { x: 240, y: 20 },
      { x: 180, y: 100, width: 80, height: 40 },
      12,
    )).toBe(false);
  });
});

describe("cellsSelectedByRect", () => {
  const cells: LayoutCell[] = [
    {
      id: "cell_1",
      role: "input",
      x: 10,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    },
    {
      id: "cell_2",
      role: "input",
      x: 110,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    },
    {
      id: "outside",
      role: "input",
      x: 220,
      y: 60,
      width: 70,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    },
  ];

  test("selects multiple cells covered by a marquee", () => {
    expect(cellsSelectedByRect(
      cells,
      { x: 5, y: 5, width: 190, height: 40 },
    ).map((cell) => cell.id)).toEqual(["cell_1", "cell_2"]);
  });

  test("includes a narrow cell when the marquee covers enough of its edge", () => {
    expect(cellsSelectedByRect(
      cells,
      { x: 70, y: 5, width: 30, height: 40 },
    ).map((cell) => cell.id)).toEqual(["cell_1"]);
  });
});

describe("range classification", () => {
  test("does not change a cell role when the rectangle is only selected", () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const input: LayoutCell = {
      id: "input_1",
      role: "input",
      x: 10,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells: [input],
          relations: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={targets}
        workflowStep="classify"
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const scope = within(view.container);
    const canvas = scope.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });
    const clickInput = () => {
      fireEvent.pointerDown(canvas, { clientX: 40, clientY: 25, pointerId: 1 });
      fireEvent.pointerUp(canvas, { clientX: 40, clientY: 25, pointerId: 1 });
    };

    clickInput();
    expect(scope.getByRole("button", { name: "記入欄" })).toHaveClass("active");
    expect(scope.getByRole("button", { name: "選択欄" })).not.toHaveClass("active");

    fireEvent.click(scope.getByRole("button", { name: "区分変更" }));
    clickInput();
    expect(scope.getByRole("button", { name: "記入欄" })).toHaveClass("active");
    expect(scope.getByRole("button", { name: "選択欄" })).not.toHaveClass("active");

    view.unmount();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("allows marquee selection from normal select mode", () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const cells: LayoutCell[] = [10, 110].map((x, index) => ({
      id: `input_${index + 1}`,
      role: "input",
      x,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    }));
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells,
          relations: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={targets}
        workflowStep="classify"
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const canvas = screen.getByLabelText("帳票レイアウト編集キャンバス");
    expect(screen.queryByText(/検出された枠を選び/)).not.toBeInTheDocument();
    expect(screen.queryByText(/クリックで1つの枠/)).not.toBeInTheDocument();
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });
    fireEvent.pointerDown(canvas, { clientX: 5, clientY: 5, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 195, clientY: 45, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 195, clientY: 45, pointerId: 1 });

    expect(screen.getByText("範囲選択: 2 セル")).toBeTruthy();
    expect(screen.queryByText(/セルをまとめて選択しました/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "項目名にする" })).not.toBeInTheDocument();
    const classifyAsLabel = screen.getByRole("button", { name: "項目名" });
    expect(classifyAsLabel).toBeTruthy();
    expect(screen.getByRole("button", { name: "記入欄" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "読み取らない" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "選択セルを結合" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "選択解除" })).toBeEnabled();
    fireEvent.click(classifyAsLabel);
    const cellSelect = screen.getByLabelText("セル一覧から選択");
    expect(within(cellSelect).getAllByRole("option", { name: /項目名/ })).toHaveLength(2);
    expect(within(cellSelect).queryAllByRole("option", { name: /記入欄/ })).toHaveLength(0);
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("deletes all cells in a marquee selection", async () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const cells: LayoutCell[] = [10, 110, 210].map((x, index) => ({
      id: `input_${index + 1}`,
      role: "input",
      x,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    }));
    const onSave = vi.fn(async (current: LayoutDefinition) => current);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells,
          relations: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={targets}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const scope = within(view.container);
    const canvas = scope.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(canvas, { clientX: 5, clientY: 5, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 195, clientY: 45, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 195, clientY: 45, pointerId: 1 });
    fireEvent.click(scope.getByRole("button", { name: "選択した矩形を完全に削除" }));

    expect(scope.queryByText("範囲選択: 2 セル")).toBeNull();
    expect(within(scope.getByLabelText("セル一覧から選択")).getAllByRole("option"))
      .toHaveLength(2);
    fireEvent.click(scope.getByRole("button", { name: /レイアウト.*保存/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls.at(-1)?.[0].cells.map((cell) => cell.id)).toEqual(["input_3"]);

    view.unmount();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("adds and removes cells with Ctrl+click while classifying", () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const cells: LayoutCell[] = [10, 110, 210].map((x, index) => ({
      id: `input_${index + 1}`,
      role: "input",
      x,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    }));
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells,
          relations: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={targets}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const scope = within(view.container);
    fireEvent.click(scope.getByRole("button", { name: "区分変更" }));
    const canvas = scope.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });
    const ctrlClick = (clientX: number) => {
      fireEvent.pointerDown(canvas, { clientX, clientY: 25, pointerId: 1, ctrlKey: true });
      fireEvent.pointerUp(canvas, { clientX, clientY: 25, pointerId: 1, ctrlKey: true });
    };

    ctrlClick(40);
    ctrlClick(140);

    expect(scope.getByText("範囲選択: 2 セル")).toBeTruthy();
    const cellSelect = scope.getByLabelText("セル一覧から選択");
    expect(within(cellSelect).queryAllByRole("option", { name: /項目名/ })).toHaveLength(0);
    expect(within(cellSelect).getAllByRole("option", { name: /記入欄/ })).toHaveLength(3);
    expect(scope.queryByRole("button", { name: "項目名にする" })).toBeNull();
    fireEvent.click(scope.getByRole("button", { name: "項目名" }));
    expect(within(cellSelect).getAllByRole("option", { name: /項目名/ })).toHaveLength(2);
    expect(within(cellSelect).getAllByRole("option", { name: /記入欄/ })).toHaveLength(1);

    ctrlClick(40);

    expect(scope.queryByText("範囲選択: 2 セル")).toBeNull();
    expect(scope.getByRole("button", { name: "項目名" })).toHaveClass("active");
    view.unmount();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });
});

test("removeLayoutCells clears references to every deleted rectangle", () => {
  const label: LayoutCell = {
    id: "label",
    role: "label",
    x: 10,
    y: 10,
    width: 80,
    height: 30,
    confidence: 1,
    text: "項目",
    textConfidence: null,
    source: "manual",
  };
  const input: LayoutCell = { ...label, id: "input", role: "input", x: 110, text: "" };
  const survivor: LayoutCell = {
    ...input,
    id: "survivor",
    x: 210,
    parentLabelCellId: "label",
  };
  const result = removeLayoutCells({
    canonicalWidth: 300,
    canonicalHeight: 100,
    detector: "test",
    ocrAvailable: false,
    cells: [label, input, survivor],
    cellMerges: [{
      id: "merge",
      parentCellId: "input",
      direction: "horizontal",
      parts: [{ cell: input, partType: "input", literal: "" }],
    }],
    relations: [{
      id: "relation",
      inputCellId: "input",
      labelCellIds: ["label"],
      parentLabelCellId: "label",
      axis: "horizontal",
      order: 1,
      targetPath: "customFields.value",
    }],
    recordSections: [{
      id: "section",
      title: "表",
      collectionPath: "genericRecords.table",
      recordType: "generic",
      recordLabelCellIds: ["label"],
      parentLabelCellId: "label",
      rowKeyCellIds: ["input"],
      source: "manual",
    }],
    recordGroups: [{
      id: "group",
      sectionId: "section",
      order: 1,
      x: 0,
      y: 0,
      width: 100,
      height: 30,
      inputCellIds: ["input"],
      recordKeyField: "row",
      recordKeyValue: null,
      recordKeyCellId: "input",
      source: "manual",
    }],
    recordStructureVerified: true,
    allowCrossAxisLabels: false,
    warnings: [],
  }, ["label", "input"]);

  expect(result.cells).toEqual([{ ...survivor, parentLabelCellId: null }]);
  expect(result.cellMerges).toEqual([]);
  expect(result.relations).toEqual([]);
  expect(result.recordSections).toEqual([expect.objectContaining({
    recordLabelCellIds: [],
    parentLabelCellId: null,
    rowKeyCellIds: [],
  })]);
  expect(result.recordGroups).toEqual([]);
  expect(result.recordStructureVerified).toBe(false);
});

describe("layout draft autosave", () => {
  const initialLayout: LayoutDefinition = {
    canonicalWidth: 300,
    canonicalHeight: 100,
    detector: "test",
    ocrAvailable: false,
    cells: [{
      id: "input_1",
      role: "input",
      x: 10,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    }],
    relations: [],
    allowCrossAxisLabels: false,
    warnings: [],
  };

  test("restores a browser draft before showing the server-backed layout", async () => {
    const draftStorageKey = "test-template-layout-draft:restore";
    window.localStorage.setItem(draftStorageKey, JSON.stringify({
      ...initialLayout,
      cells: initialLayout.cells.map((cell) => ({ ...cell, role: "ignored" as const })),
    }));
    const onDirtyChange = vi.fn();

    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={initialLayout}
        targets={targets}
        onDetect={vi.fn()}
        onSave={vi.fn()}
        onDirtyChange={onDirtyChange}
        draftStorageKey={draftStorageKey}
      />,
    );

    await waitFor(() => expect(onDirtyChange).toHaveBeenCalledWith(true));
    expect(JSON.parse(window.localStorage.getItem(draftStorageKey) ?? "null")?.cells[0].role)
      .toBe("ignored");

    view.unmount();
    window.localStorage.removeItem(draftStorageKey);
  });

  test("auto-saves an edited step-one layout after input settles", async () => {
    vi.useFakeTimers();
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const draftStorageKey = "test-template-layout-draft:autosave";
    const onSave = vi.fn(async (current: LayoutDefinition) => current);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={initialLayout}
        targets={targets}
        workflowStep="classify"
        onDetect={vi.fn()}
        onSave={onSave}
        draftStorageKey={draftStorageKey}
      />,
    );
    const editor = within(view.container);
    expect(view.container.querySelector(".layout-instructions")).not.toBeInTheDocument();
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 300, bottom: 100,
      width: 300, height: 100, toJSON: () => ({}),
    });

    fireEvent.pointerDown(canvas, { clientX: 40, clientY: 25, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 40, clientY: 25, pointerId: 1 });
    fireEvent.click(editor.getByRole("button", { name: "読み取らない" }));

    expect(JSON.parse(window.localStorage.getItem(draftStorageKey) ?? "null")?.cells[0].role)
      .toBe("ignored");
    await act(async () => {
      vi.advanceTimersByTime(1200);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0].cells[0].role).toBe("ignored");
    expect(window.localStorage.getItem(draftStorageKey)).toBeNull();

    view.unmount();
    window.localStorage.removeItem(draftStorageKey);
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
    vi.useRealTimers();
  });
});

describe("linkLaneCells", () => {
  const column = (prefix: string, x: number): LayoutCell[] => Array.from(
    { length: 10 },
    (_, index) => ({
      id: `${prefix}_${index + 1}`,
      role: "input",
      x,
      y: 80 + index * 60,
      width: 80,
      height: 42,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    }),
  );
  const priceCells = column("price", 100);
  const weightCells = column("weight", 180);

  test("keeps a vertical link inside one of two touching columns", () => {
    const selected = linkLaneCells(
      { x: 180, y: 40 },
      { x: 180, y: 680 },
      [...priceCells, ...weightCells],
    );

    expect(selected).toHaveLength(10);
    expect(selected.every((cell) => cell.id.startsWith("price_"))).toBe(true);
  });

  test("uses the release-side column when a diagonal crosses both columns", () => {
    const selected = linkLaneCells(
      { x: 140, y: 40 },
      { x: 220, y: 680 },
      [...priceCells, ...weightCells],
    );

    expect(selected).toHaveLength(10);
    expect(selected.every((cell) => cell.id.startsWith("weight_"))).toBe(true);
  });

  test("connects one complete record row without taking the next row", () => {
    const row = (prefix: string, y: number): LayoutCell[] => Array.from(
      { length: 7 },
      (_, index) => ({
        id: `${prefix}_${index + 1}`,
        role: "input",
        x: 100 + index * 90,
        y,
        width: 80,
        height: 40,
        confidence: 1,
        text: "",
        textConfidence: null,
        source: "manual",
      }),
    );
    const firstRow = row("row1", 100);
    const secondRow = row("row2", 150);

    const selected = linkLaneCells(
      { x: 140, y: 120 },
      { x: 680, y: 120 },
      [...firstRow, ...secondRow],
    );

    expect(selected).toHaveLength(7);
    expect(selected.every((cell) => cell.id.startsWith("row1_"))).toBe(true);
  });

  test("connects the detected parity-through-notes row despite small vertical offsets", () => {
    const detectedRow: LayoutCell[] = [
      ["parity", 668, 282, 36, 30],
      ["ageDays", 704, 282, 52, 30],
      ["weightKg", 756, 282, 52, 29],
      ["priceYen", 809, 281, 126, 30],
      ["sireName", 935, 280, 86, 31],
      ["saleDate", 1021, 279, 73, 30],
      ["notes", 1094, 276, 90, 32],
    ].map(([id, x, y, width, height]) => ({
      id: String(id),
      role: "input" as const,
      x: Number(x),
      y: Number(y),
      width: Number(width),
      height: Number(height),
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual" as const,
    }));

    expect(linkLaneCells(
      { x: 686, y: 297 },
      { x: 1139, y: 292 },
      detectedRow,
    ).map((cell) => cell.id)).toEqual([
      "parity",
      "ageDays",
      "weightKg",
      "priceYen",
      "sireName",
      "saleDate",
      "notes",
    ]);
  });

  test("ignores an intervening selection field when the drag ends on an input", () => {
    const targetInput: LayoutCell = {
      ...priceCells[0],
      id: "municipality_input",
      x: 220,
      y: 100,
    };
    const interveningMark: LayoutCell = {
      ...priceCells[0],
      id: "prefecture_choice",
      role: "mark",
      x: 120,
      y: 100,
    };

    expect(meaningLinkLaneCells(
      { x: 40, y: 120 },
      { x: 260, y: 120 },
      [interveningMark, targetInput],
      targetInput,
    ).map((cell) => cell.id)).toEqual([targetInput.id]);
  });

  test("ignores an intervening input when the drag ends on a selection field", () => {
    const interveningInput: LayoutCell = {
      ...priceCells[0],
      id: "address_input",
      x: 120,
      y: 100,
    };
    const targetMark: LayoutCell = {
      ...priceCells[0],
      id: "municipality_choice",
      role: "mark",
      x: 220,
      y: 100,
    };

    expect(meaningLinkLaneCells(
      { x: 40, y: 120 },
      { x: 260, y: 120 },
      [interveningInput, targetMark],
      targetMark,
    ).map((cell) => cell.id)).toEqual([targetMark.id]);
  });
});

describe("record section display names", () => {
  const sectionHeading: LayoutCell = {
    id: "education_heading", role: "parent_label", x: 100, y: 25, width: 220, height: 30,
    confidence: 1, text: "学歴見出し", textConfidence: 1, source: "manual",
  };
  const labels: LayoutCell[] = [
    {
      id: "year_label", role: "label", x: 100, y: 80, width: 80, height: 30,
      confidence: 1, text: "年", textConfidence: 1, source: "manual",
    },
    {
      id: "school_label", role: "label", x: 240, y: 80, width: 80, height: 30,
      confidence: 1, text: "学校名", textConfidence: 1, source: "manual",
    },
  ];
  const inputs: LayoutCell[] = [0, 1].flatMap((row) => labels.map((label, column) => ({
    id: `input_${row}_${column}`,
    role: "input" as const,
    x: label.x,
    y: 130 + row * 50,
    width: label.width,
    height: 35,
    confidence: 1,
    text: "",
    textConfidence: null,
    source: "manual" as const,
  })));
  const relations: LayoutRelation[] = inputs.map((cell, index) => ({
    id: `relation_${cell.id}`,
    inputCellId: cell.id,
    labelCellIds: [labels[index % labels.length].id],
    axis: "vertical",
    order: Math.floor(index / labels.length) + 1,
    targetPath: `customFields.${index % labels.length === 0 ? "年" : "学校名"}_${Math.floor(index / labels.length) + 1}`,
  }));

  test("creates an unnamed section, selects its band on click, and selects one cell on double click", async () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const initial: LayoutDefinition = {
      canonicalWidth: 500,
      canonicalHeight: 300,
      detector: "test",
      ocrAvailable: false,
      cells: [sectionHeading, ...labels, ...inputs],
      relations,
      allowCrossAxisLabels: true,
      warnings: [],
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={initial}
        targets={[]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 500, bottom: 300,
      width: 500, height: 300, toJSON: () => ({}),
    });
    fireEvent.click(editor.getByRole("button", { name: "繰り返し部分をまとめる" }));
    expect(canvas).toHaveClass("layout-mode-record");

    fireEvent.pointerDown(canvas, { clientX: 140, clientY: 95, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 280, clientY: 95, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 280, clientY: 95, pointerId: 1 });

    expect(editor.queryByRole("form", { name: "繰り返し表の名称編集" })).not.toBeInTheDocument();
    expect(editor.getByRole("button", { name: /名称なしの繰り返し表.*2レコード/ }))
      .toBeInTheDocument();
    expect(editor.getByRole("radio", { name: "名前を付けない" })).toBeChecked();
    expect(editor.queryByRole("textbox", { name: "繰り返し部分の名前" })).not.toBeInTheDocument();

    fireEvent.pointerDown(canvas, { clientX: 210, clientY: 95, pointerId: 2 });
    fireEvent.pointerUp(canvas, { clientX: 210, clientY: 95, pointerId: 2 });
    expect(editor.getByText(/繰り返し部分を選択しました/)).toBeInTheDocument();
    expect(editor.getByLabelText("セル一覧から選択")).toHaveValue("year_label");

    fireEvent.doubleClick(canvas, { clientX: 280, clientY: 95 });
    expect(editor.getByText("繰り返し部分のセルを1つ選択しました。")).toBeInTheDocument();
    expect(editor.getByLabelText("セル一覧から選択")).toHaveValue("school_label");

    fireEvent.change(editor.getByLabelText("帳票上の見出し（任意）"), {
      target: { value: sectionHeading.id },
    });
    expect(editor.getByRole("button", { name: /学歴見出し.*2レコード/ })).toBeInTheDocument();
    fireEvent.click(editor.getByRole("button", { name: /レイアウト.*保存/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.recordSections).toHaveLength(1);
    expect(saved.recordSections?.[0]).toEqual(expect.objectContaining({
      displayName: null,
      parentLabelCellId: sectionHeading.id,
    }));
    const originalId = saved.recordSections?.[0].id;
    const originalCollectionPath = saved.recordSections?.[0].collectionPath;
    const originalGroupIds = saved.recordGroups?.map((group) => group.id);

    view.unmount();
    const reloaded = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={saved}
        targets={[]}
        onDetect={vi.fn()}
        onSave={vi.fn(async (current: LayoutDefinition) => current)}
      />,
    );
    const reloadedEditor = within(reloaded.container);
    expect(reloadedEditor.getByRole("button", { name: /学歴見出し.*2レコード/ }))
      .toBeInTheDocument();
    expect(reloadedEditor.getByRole("radio", { name: "名前を付けない" })).toBeChecked();
    expect(reloadedEditor.getByLabelText("帳票上の見出し（任意）"))
      .toHaveValue(sectionHeading.id);
    expect(saved.recordSections?.[0].id).toBe(originalId);
    expect(saved.recordSections?.[0].collectionPath).toBe(originalCollectionPath);
    expect(saved.recordGroups?.map((group) => group.id)).toEqual(originalGroupIds);
    reloaded.unmount();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("adds and removes only the optional section name from the sidebar", async () => {
    const section: LayoutRecordSection = {
      id: "education",
      title: "明細",
      displayName: null,
      collectionPath: "genericRecords.education",
      recordType: "generic",
      recordLabelCellIds: labels.map((label) => label.id),
      parentLabelCellId: sectionHeading.id,
      rowKeyStrategy: "position",
      rowKeyCellIds: [],
      includeEmptyRows: false,
      source: "manual",
    };
    const groups: LayoutRecordGroup[] = [0, 1].map((row) => ({
      id: `record_education_${row + 1}`,
      sectionId: section.id,
      order: row + 1,
      x: 100,
      y: 130 + row * 50,
      width: 220,
      height: 35,
      inputCellIds: inputs.slice(row * 2, row * 2 + 2).map((cell) => cell.id),
      recordKeyField: "rowNumber",
      recordKeyValue: String(row + 1),
      recordKeyCellId: null,
      source: "manual",
    }));
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 500,
          canonicalHeight: 300,
          detector: "test",
          ocrAvailable: false,
          cells: [sectionHeading, ...labels, ...inputs],
          relations,
          recordSections: [section],
          recordGroups: groups,
          recordStructureVerified: true,
          allowCrossAxisLabels: true,
          warnings: [],
        }}
        targets={[]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    fireEvent.click(editor.getByRole("radio", { name: "名前を付ける" }));
    const nameInput = editor.getByRole("textbox", { name: "繰り返し部分の名前" });
    expect(nameInput).toHaveValue("明細");
    fireEvent.change(nameInput, { target: { value: "学歴" } });
    fireEvent.blur(nameInput);
    expect(editor.getByRole("button", { name: /学歴.*2レコード/ })).toBeInTheDocument();

    fireEvent.click(editor.getByRole("button", { name: /レイアウト.*保存/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const named = onSave.mock.calls[0]![0];
    expect(named.recordSections?.[0]).toEqual(expect.objectContaining({
      id: section.id,
      displayName: "学歴",
      collectionPath: section.collectionPath,
      parentLabelCellId: sectionHeading.id,
      recordLabelCellIds: section.recordLabelCellIds,
    }));
    expect(named.recordGroups).toEqual(groups);

    fireEvent.click(editor.getByRole("radio", { name: "名前を付けない" }));
    expect(editor.queryByRole("textbox", { name: "繰り返し部分の名前" })).not.toBeInTheDocument();
    fireEvent.click(editor.getByRole("button", { name: /レイアウト.*保存/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
    const unnamed = onSave.mock.calls[1]![0];
    expect(unnamed.recordSections?.[0]).toEqual(expect.objectContaining({
      id: section.id,
      displayName: null,
      collectionPath: section.collectionPath,
      parentLabelCellId: sectionHeading.id,
      recordLabelCellIds: section.recordLabelCellIds,
    }));
    expect(unnamed.recordGroups).toEqual(groups);
  });

  test("keeps legacy titles as fallback and maps three same-title sections by ID", () => {
    const sections: LayoutRecordSection[] = ["学歴", "職歴", "資格"].map((displayName, index) => ({
      id: `section_${index + 1}`,
      title: "明細",
      displayName,
      collectionPath: `genericRecords.section_${index + 1}`,
      recordType: "generic",
      recordLabelCellIds: labels.map((label) => `${label.id}_${index + 1}`),
      parentLabelCellId: null,
      source: "manual",
    }));
    const legacy = { ...sections[0], id: "legacy", displayName: undefined };
    const unnamed = {
      ...sections[0],
      id: "unnamed",
      displayName: null,
      parentLabelCellId: sectionHeading.id,
    };

    expect(recordSectionDisplayName(legacy)).toBe("明細");
    expect(recordSectionDisplayName(unnamed)).toBe("名称なしの繰り返し表");
    expect(recordSectionDisplayName(unnamed, new Map([[sectionHeading.id, sectionHeading]])))
      .toBe("学歴見出し");
    expect(sections.map((section) => recordSectionDisplayName(section)))
      .toEqual(["学歴", "職歴", "資格"]);
    expect(new Set(sections.map((section) => section.id)).size).toBe(3);
  });

  test("uses a wider hit area than the visible record line without changing section identity", () => {
    const section: LayoutRecordSection = {
      id: "education",
      title: "明細",
      displayName: "学歴",
      collectionPath: "genericRecords.education",
      recordType: "generic",
      recordLabelCellIds: labels.map((label) => label.id),
      parentLabelCellId: null,
      source: "manual",
    };

    expect(recordSectionAtPoint({ x: 210, y: 108 }, [section], labels, 16)?.id)
      .toBe("education");
    expect(recordSectionAtPoint({ x: 210, y: 125 }, [section], labels, 16)).toBeNull();
  });

  test("allows duplicate display names while retaining distinct internal IDs", async () => {
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 500,
          canonicalHeight: 300,
          detector: "test",
          ocrAvailable: false,
          cells: [],
          relations: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={[]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    fireEvent.click(editor.getByRole("button", { name: "繰り返し表を追加" }));
    fireEvent.click(editor.getByRole("button", { name: "繰り返し表を追加" }));
    expect(editor.getAllByRole("button", { name: /明細.*0レコード/ })).toHaveLength(2);

    fireEvent.click(editor.getByRole("button", { name: /レイアウト.*保存/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const sections = onSave.mock.calls[0]![0].recordSections ?? [];
    expect(sections.map((section) => recordSectionDisplayName(section)))
      .toEqual(["明細", "明細"]);
    expect(new Set(sections.map((section) => section.id)).size).toBe(2);
    expect(new Set(sections.map((section) => section.collectionPath)).size).toBe(2);
  });
});

describe("record creation", () => {
  test("infers the collection from mapped cells", () => {
    const inputs: LayoutCell[] = ["price", "sire"].map((id) => ({
      id,
      role: "input",
      x: 0,
      y: 0,
      width: 20,
      height: 20,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    }));
    const relations: LayoutRelation[] = [
      {
        id: "price_relation",
        inputCellId: "price",
        labelCellIds: [],
        axis: "horizontal",
        order: 1,
        targetPath: "saleCalves.0.priceYen",
      },
      {
        id: "sire_relation",
        inputCellId: "sire",
        labelCellIds: [],
        axis: "horizontal",
        order: 1,
        targetPath: "saleCalves.0.sireName",
      },
    ];

    expect(inferRecordCollectionPath(inputs, relations)).toBe("saleCalves");
  });

  test("creates complete sale records by dragging across all mapped column labels", () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const rowGeometry = [
      ["parity", 668, 36, 30],
      ["ageDays", 704, 52, 30],
      ["weightKg", 756, 52, 29],
      ["priceYen", 809, 126, 30],
      ["sireName", 935, 86, 31],
      ["saleDate", 1021, 73, 30],
      ["notes", 1094, 90, 32],
    ] as const;
    const labelByField = new Map(saleFieldNames);
    const columnLabels: LayoutCell[] = rowGeometry.map(([field, x, width, height]) => ({
      id: `${field}_label`,
      role: "label",
      x,
      y: 240,
      width,
      height,
      confidence: 1,
      text: labelByField.get(field) ?? field,
      textConfidence: 1,
      source: "manual",
    }));
    const parentLabel: LayoutCell = {
      id: "sale_parent_label",
      role: "parent_label",
      x: 500,
      y: 240,
      width: 150,
      height: 30,
      confidence: 1,
      text: "売却子牛一覧",
      textConfidence: 1,
      source: "manual",
    };
    const rows: LayoutCell[] = [0, 1].flatMap((rowIndex) => rowGeometry.map(
      ([field, x, width, height]) => ({
        id: `${field}_${rowIndex + 1}`,
        role: "input" as const,
        x,
        y: 282 + rowIndex * 40,
        width,
        height,
        confidence: 1,
        text: "",
        textConfidence: null,
        source: "manual" as const,
      }),
    ));
    const relations: LayoutRelation[] = rows.map((cell) => {
      const [field, rowNumber] = cell.id.split("_");
      const rowIndex = Number(rowNumber) - 1;
      return {
        id: `relation_${cell.id}`,
        inputCellId: cell.id,
        labelCellIds: [`${field}_label`],
        axis: "vertical",
        order: rowIndex + 1,
        targetPath: `saleCalves.${rowIndex}.${field}`,
      };
    });
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 1240,
          canonicalHeight: 1754,
          detector: "test",
          ocrAvailable: false,
          cells: [parentLabel, ...columnLabels, ...rows],
          relations,
          allowCrossAxisLabels: true,
          warnings: [],
        }}
        targets={allSaleTargets}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const editor = within(view.container);
    fireEvent.click(editor.getByRole("button", { name: "繰り返し部分をまとめる" }));
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1240,
      bottom: 1754,
      width: 1240,
      height: 1754,
      toJSON: () => ({}),
    });
    fireEvent.pointerDown(canvas, {
      clientX: 686,
      clientY: 255,
      pointerId: 1,
    });
    fireEvent.pointerMove(canvas, { clientX: 1139, clientY: 255, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 1139, clientY: 255, pointerId: 1 });

    expect(editor.getByText("2行")).toBeTruthy();
    expect(editor.getByText("14個")).toBeTruthy();
    expect(editor.getByText("帳票上の見出しが未設定")).toBeTruthy();
    expect(editor.getByText("産次 / 日齢 / 体重 / 価格 / 種雄牛名 / 売却日 / 備考"))
      .toBeTruthy();
    expect(editor.getByText(/産次 \/ 日齢 \/ 体重 \/ 価格 \/ 種雄牛名 \/ 売却日 \/ 備考を「売却子牛一覧」の列として指定し、2行・14個の記入欄を自動編成/))
      .toBeTruthy();

    fireEvent.click(editor.getByRole("button", { name: "見出しへ接続" }));
    fireEvent.pointerDown(canvas, { clientX: 575, clientY: 255, pointerId: 2 });
    fireEvent.pointerMove(canvas, { clientX: 870, clientY: 255, pointerId: 2 });
    fireEvent.pointerUp(canvas, { clientX: 870, clientY: 255, pointerId: 2 });

    expect(editor.getByText("繰り返し表を定義済み")).toBeTruthy();
    expect(editor.getByText("帳票上の見出し設定済み")).toBeTruthy();
    expect(editor.getByText(/売却子牛一覧を売却子牛一覧の帳票上の見出しに設定しました/)).toBeTruthy();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("keeps two record label bands that belong to the same JSON collection", async () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const fields = [
      ["parity", "産歴"],
      ["birthDate", "分娩年月日"],
      ["outcome", "分娩状況"],
      ["notes", "備考"],
    ] as const;
    const xByField = new Map(fields.map(([field], index) => [field, 100 + index * 100]));
    const labels: LayoutCell[] = [0, 1].flatMap((bandIndex) => fields.map(([field, text]) => ({
      id: `band_${bandIndex + 1}_${field}_label`,
      role: "label" as const,
      x: xByField.get(field) ?? 0,
      y: 100 + bandIndex * 200,
      width: 40,
      height: 30,
      confidence: 1,
      text,
      textConfidence: 1,
      source: "manual" as const,
    })));
    const inputs: LayoutCell[] = [0, 1].flatMap((bandIndex) => fields.map(([field]) => ({
      id: `band_${bandIndex + 1}_${field}_input`,
      role: "input" as const,
      x: xByField.get(field) ?? 0,
      y: 140 + bandIndex * 200,
      width: 40,
      height: 35,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual" as const,
    })));
    const parentLabel: LayoutCell = {
      id: "calving_parent",
      role: "parent_label",
      x: 20,
      y: 100,
      width: 60,
      height: 30,
      confidence: 1,
      text: "繁殖関係",
      textConfidence: 1,
      source: "manual",
    };
    const relations: LayoutRelation[] = inputs.map((input) => {
      const match = /^band_(\d+)_(.+)_input$/.exec(input.id)!;
      const bandIndex = Number(match[1]) - 1;
      const field = match[2];
      return {
        id: `relation_${input.id}`,
        inputCellId: input.id,
        labelCellIds: [`band_${bandIndex + 1}_${field}_label`],
        axis: "vertical",
        order: bandIndex + 1,
        targetPath: `calvings.${bandIndex}.${field}`,
      };
    });
    const calvingTargets: FieldMappingTarget[] = [0, 1].flatMap((rowIndex) => (
      fields.map(([field, label]) => ({
        path: `calvings.${rowIndex}.${field}`,
        label: `繁殖履歴 ${rowIndex + 1}行目・${label}`,
        section: "繁殖履歴",
      }))
    ));
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 600,
          canonicalHeight: 600,
          detector: "test",
          ocrAvailable: false,
          cells: [parentLabel, ...labels, ...inputs],
          relations,
          allowCrossAxisLabels: true,
          warnings: [],
        }}
        targets={calvingTargets}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    fireEvent.click(editor.getByRole("button", { name: "繰り返し部分をまとめる" }));
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 600,
      bottom: 600,
      width: 600,
      height: 600,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(canvas, { clientX: 120, clientY: 115, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 420, clientY: 115, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 420, clientY: 115, pointerId: 1 });
    fireEvent.pointerDown(canvas, { clientX: 120, clientY: 315, pointerId: 2 });
    fireEvent.pointerMove(canvas, { clientX: 420, clientY: 315, pointerId: 2 });
    fireEvent.pointerUp(canvas, { clientX: 420, clientY: 315, pointerId: 2 });

    fireEvent.click(editor.getByRole("button", { name: "見出しへ接続" }));
    fireEvent.pointerDown(canvas, { clientX: 50, clientY: 115, pointerId: 3 });
    fireEvent.pointerMove(canvas, { clientX: 120, clientY: 115, pointerId: 3 });
    fireEvent.pointerUp(canvas, { clientX: 120, clientY: 115, pointerId: 3 });

    fireEvent.click(editor.getByRole("button", { name: /レイアウト.*保存/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.recordSections).toHaveLength(2);
    expect(saved.recordGroups).toHaveLength(2);
    expect(new Set(saved.recordGroups?.map((group) => group.sectionId)).size).toBe(2);
    expect(saved.recordSections?.map((section) => section.recordLabelCellIds)).toEqual([
      fields.map(([field]) => `band_1_${field}_label`),
      fields.map(([field]) => `band_2_${field}_label`),
    ]);
    expect(saved.recordSections?.map((section) => section.parentLabelCellId)).toEqual([
      "calving_parent",
      "calving_parent",
    ]);

    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("repairs a stale record verification flag from complete saved links", () => {
    const parentLabel: LayoutCell = {
      id: "sale_parent",
      role: "parent_label",
      x: 10,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "売却子牛一覧",
      textConfidence: 1,
      source: "manual",
    };
    const columnLabel: LayoutCell = {
      ...parentLabel,
      id: "price_label",
      role: "label",
      x: 100,
      text: "価格",
    };
    const input: LayoutCell = {
      ...columnLabel,
      id: "price_input",
      role: "input",
      y: 50,
      text: "",
      textConfidence: null,
    };
    const section: LayoutRecordSection = {
      id: "sale_section",
      title: "売却子牛一覧",
      collectionPath: "saleCalves",
      recordType: "saleCalf",
      recordLabelCellIds: [columnLabel.id],
      parentLabelCellId: parentLabel.id,
      source: "manual",
    };
    const group: LayoutRecordGroup = {
      id: "sale_row_1",
      sectionId: section.id,
      order: 1,
      x: input.x,
      y: input.y,
      width: input.width,
      height: input.height,
      inputCellIds: [input.id],
      recordKeyField: "parity",
      recordKeyValue: "1",
      source: "manual",
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 120,
          detector: "test",
          ocrAvailable: false,
          cells: [parentLabel, columnLabel, input],
          relations: [{
            id: "price_relation",
            inputCellId: input.id,
            labelCellIds: [columnLabel.id],
            axis: "vertical",
            order: 1,
            targetPath: "saleCalves.0.priceYen",
          }],
          recordSections: [section],
          recordGroups: [group],
          recordStructureVerified: false,
          allowCrossAxisLabels: true,
          warnings: [],
        }}
        targets={allSaleTargets}
        workflowStep="records"
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    expect(within(view.container).getByText("繰り返し表を定義済み")).toBeInTheDocument();
    expect(within(view.container).queryByText("表の列が未設定")).not.toBeInTheDocument();
  });
});

describe("mergeLabelRelations", () => {
  const inputs: LayoutCell[] = [0, 1].map((index) => ({
    id: `input_${index + 1}`,
    role: "input",
    x: 100 + index * 120,
    y: 40,
    width: 100,
    height: 40,
    confidence: 1,
    text: "",
    textConfidence: null,
    source: "manual",
  }));
  const targetPaths = [
    "saleCalves.0.priceYen",
    "saleCalves.1.priceYen",
  ];
  const firstLabelRelations: LayoutRelation[] = inputs.map((input, index) => ({
    id: `first_${index + 1}`,
    inputCellId: input.id,
    labelCellIds: ["label_price"],
    axis: "horizontal",
    order: index + 1,
    targetPath: targetPaths[index],
  }));

  test("keeps the previous label lines when a second label is attached", () => {
    const merged = mergeLabelRelations(
      firstLabelRelations,
      inputs,
      "label_sale_calf",
      "vertical",
      targetPaths,
      123,
    );

    expect(merged).toHaveLength(4);
    expect(merged.filter((relation) => relation.labelCellIds[0] === "label_price")).toHaveLength(2);
    expect(merged.filter((relation) => relation.labelCellIds[0] === "label_sale_calf")).toHaveLength(2);
  });

  test("reuses each existing target when the same inputs are dragged in reverse", () => {
    expect(targetPathsForInputs(
      [...inputs].reverse(),
      firstLabelRelations,
    )).toEqual([
      "saleCalves.1.priceYen",
      "saleCalves.0.priceYen",
    ]);
  });

  test("advances a repeated save target instead of replacing a previous input", () => {
    expect(resolveAvailableLinkTargetPaths(
      ["saleCalves.0.priceYen"],
      [inputs[1]],
      [firstLabelRelations[0]],
      targets,
    )).toEqual(["saleCalves.1.priceYen"]);
  });

  test("rejects reusing a scalar save target for a different input", () => {
    const scalar = { path: "document.cattleName", label: "牛名", section: "基本情報" };
    expect(resolveAvailableLinkTargetPaths(
      [scalar.path],
      [inputs[1]],
      [{ ...firstLabelRelations[0], targetPath: scalar.path }],
      [scalar],
    )).toBeNull();
  });
});

describe("meaning history undo", () => {
  test("restores a removed relation with Ctrl+Z", () => {
    const label: LayoutCell = {
      id: "label_price",
      role: "label",
      x: 10,
      y: 10,
      width: 80,
      height: 30,
      confidence: 1,
      text: "価格",
      textConfidence: 1,
      source: "manual",
    };
    const input: LayoutCell = {
      id: "input_1",
      role: "input",
      x: 100,
      y: 10,
      width: 100,
      height: 30,
      confidence: 1,
      text: "",
      textConfidence: null,
      source: "manual",
    };
    const relation: LayoutRelation = {
      id: "relation_1",
      inputCellId: input.id,
      labelCellIds: [label.id],
      axis: "horizontal",
      order: 1,
      targetPath: "saleCalves.0.priceYen",
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells: [label, input],
          relations: [relation],
          allowCrossAxisLabels: true,
          warnings: [],
        }}
        targets={targets}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const editor = within(view.container);
    fireEvent.click(editor.getByRole("button", { name: "解除" }));
    expect(editor.queryByRole("button", { name: "解除" })).toBeNull();

    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    expect(editor.getByRole("button", { name: "解除" })).toBeTruthy();
  });
});

describe("custom JSON target", () => {
  test("does not show unrelated fallback targets when the template catalog is empty", () => {
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells: [],
          relations: [],
          labelPool: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={[]}
        workflowStep="meaning"
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const editor = within(view.container);
    expect(editor.getByText("このテンプレートには保存する項目が登録されていません"))
      .toBeVisible();
    expect(editor.queryByLabelText("登録済み項目を検索")).not.toBeInTheDocument();
    expect(editor.queryByLabelText("登録済みの保存項目")).not.toBeInTheDocument();
    expect(editor.getByRole("button", { name: "一覧にない項目を追加" })).toBeEnabled();
    expect(editor.queryByText("個体識別番号")).not.toBeInTheDocument();
  });

  test("shows the registered item catalog below the preview before a cell is selected", () => {
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells: [],
          relations: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={targets}
        workflowStep="meaning"
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const editor = within(view.container);
    const preview = view.container.querySelector(".layout-workbench")!;
    const catalog = view.container.querySelector(".meaning-target-manager")!;
    expect(preview.compareDocumentPosition(catalog) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const catalogAddition = editor.getByRole("button", { name: "一覧にない項目を追加" })
      .closest(".meaning-target-addition") as HTMLElement;
    const filters = view.container.querySelector(".meaning-target-filters")!;
    const pool = view.container.querySelector(".meaning-pool-list")!;
    expect(catalogAddition.compareDocumentPosition(filters) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(catalogAddition.compareDocumentPosition(pool) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    fireEvent.click(editor.getByRole("button", { name: "一覧にない項目を追加" }));
    expect(within(catalogAddition).getByLabelText("新しい項目名")).toBeVisible();
    fireEvent.click(within(catalogAddition).getByRole("button", { name: "追加を閉じる" }));
    expect(editor.getByRole("button", { name: "売却子牛・価格" }))
      .toHaveTextContent("未使用");

    fireEvent.change(editor.getByLabelText("登録済み項目を検索"), {
      target: { value: "該当なし" },
    });
    expect(editor.getByText("条件に一致する登録済み項目はありません。")).toBeVisible();

    const disclosure = view.container.querySelector(".relation-table-disclosure") as HTMLDetailsElement;
    expect(disclosure.open).toBe(false);
    expect(editor.getByText("設定済みのセル対応を確認")).toBeVisible();
  });

  test("adds and saves a reusable item without asking for an internal key or OCR aliases", async () => {
    const onSave = vi.fn(async (current: LayoutDefinition) => current);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells: [],
          relations: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={targets}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );

    const editor = within(view.container);
    expect(editor.queryByText("一覧から意味づけ")).not.toBeInTheDocument();
    expect(editor.queryByLabelText("JSONキー")).not.toBeInTheDocument();
    expect(editor.queryByLabelText("OCR別名（任意・カンマ区切り）")).not.toBeInTheDocument();
    fireEvent.click(editor.getByRole("button", { name: "一覧にない項目を追加" }));
    fireEvent.change(editor.getByLabelText("新しい項目名"), { target: { value: "治療歴" } });
    fireEvent.click(editor.getByRole("button", { name: "この項目を追加" }));

    expect(editor.getByRole("option", { name: "治療歴" })).toHaveValue(
      "customFields.治療歴",
    );
    expect(editor.getByText(/「治療歴」を保存する項目へ追加しました/)).toBeTruthy();
    fireEvent.click(editor.getByRole("button", { name: "レイアウトを途中保存" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0]![0].labelPool).toEqual([
      expect.objectContaining({
        label: "治療歴",
        targetPath: "customFields.治療歴",
        aliases: [],
      }),
    ]);
  });

  test("shows a blocking loading overlay while redetecting cells", () => {
    const pending = new Promise<never>(() => undefined);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 300,
          canonicalHeight: 100,
          detector: "test",
          ocrAvailable: false,
          cells: [],
          relations: [],
          allowCrossAxisLabels: false,
          warnings: [],
        }}
        targets={targets}
        onDetect={() => pending}
        onSave={vi.fn()}
      />,
    );

    const editor = within(view.container);
      fireEvent.click(editor.getByRole("button", { name: "枠を再検出" }));

    expect(editor.getByRole("status")).toHaveTextContent("記入欄を再検出しています");
  });
});

describe("editing safety regressions", () => {
  const scalarTarget: FieldMappingTarget = {
    path: "document.cattleName",
    label: "牛名",
    section: "基本情報",
  };
  const labelCell: LayoutCell = {
    id: "label_name",
    role: "label",
    x: 10,
    y: 10,
    width: 70,
    height: 30,
    confidence: 1,
    text: "牛名",
    textConfidence: 1,
    source: "manual",
  };
  const inputCell: LayoutCell = {
    id: "input_name",
    role: "input",
    x: 100,
    y: 10,
    width: 100,
    height: 30,
    confidence: 1,
    text: "",
    textConfidence: null,
    source: "manual",
  };
  const relation: LayoutRelation = {
    id: "relation_name",
    inputCellId: inputCell.id,
    labelCellIds: [labelCell.id],
    axis: "horizontal",
    order: 1,
    targetPath: scalarTarget.path,
  };
  const layout = (
    cells: LayoutCell[] = [labelCell, inputCell],
    relations: LayoutRelation[] = [relation],
  ): LayoutDefinition => ({
    canonicalWidth: 300,
    canonicalHeight: 100,
    detector: "test",
    ocrAvailable: false,
    cells,
    relations,
    allowCrossAxisLabels: false,
    warnings: [],
  });

  test("normalizes obsolete number roles and repairs adjacent legacy labels", () => {
    const heading: LayoutCell = {
      ...labelCell,
      id: "section_heading",
      role: "parent_label",
      x: 10,
      y: 0,
      width: 280,
      text: "申請情報",
    };
    const staleLabel: LayoutCell = {
      ...labelCell,
      id: "stale_label",
      role: "parent_label",
      source: "detected",
      y: 40,
      text: "申請理由",
    };
    const adjacentInput: LayoutCell = {
      ...inputCell,
      id: "adjacent_input",
      x: 80,
      y: 40,
    };
    const legacyRowNumber: LayoutCell = {
      ...inputCell,
      id: "legacy_row_number",
      role: "record_key_cell",
      x: 10,
      y: 75,
      width: 40,
      text: "1",
    };
    const legacyColumnNumber: LayoutCell = {
      ...inputCell,
      id: "legacy_column_number",
      role: "column_key_cell",
      x: 55,
      y: 75,
      width: 40,
      text: "2",
    };
    const normalized = normalizeLayout({
      ...layout([
        heading,
        staleLabel,
        adjacentInput,
        legacyRowNumber,
        legacyColumnNumber,
      ], [{
        id: "existing_relation",
        inputCellId: adjacentInput.id,
        labelCellIds: [staleLabel.id],
        parentLabelCellId: heading.id,
        axis: "horizontal",
        order: 1,
        targetPath: scalarTarget.path,
      }]),
      recordSections: [{
        id: "section",
        title: "明細",
        collectionPath: "genericRecords.lines",
        recordType: "generic",
        recordLabelCellIds: [staleLabel.id],
        parentLabelCellId: heading.id,
        rowKeyStrategy: "printed",
        rowKeyCellIds: [legacyRowNumber.id],
        source: "manual",
      }],
      recordGroups: [{
        id: "group",
        sectionId: "section",
        order: 1,
        x: 10,
        y: 40,
        width: 190,
        height: 65,
        inputCellIds: [adjacentInput.id],
        recordKeyField: "rowNumber",
        recordKeyValue: "1",
        recordKeyCellId: legacyRowNumber.id,
        source: "manual",
      }],
      detector: "xlsx-structure",
    });
    const roles = new Map(normalized.cells.map((cell) => [cell.id, cell.role]));

    expect(roles.get(heading.id)).toBe("parent_label");
    expect(roles.get(staleLabel.id)).toBe("label");
    expect(roles.get(legacyRowNumber.id)).toBe("input");
    expect(roles.get(legacyColumnNumber.id)).toBe("input");
    expect(normalized.recordSections?.[0]).toEqual(expect.objectContaining({
      rowKeyStrategy: "position",
      rowKeyCellIds: [],
    }));
    expect(normalized.recordGroups?.[0]).toEqual(expect.objectContaining({
      inputCellIds: [adjacentInput.id, legacyRowNumber.id],
      recordKeyCellId: null,
    }));
  });

  test("keeps non-spreadsheet and manually classified parent labels unchanged", () => {
    const adjacentInput: LayoutCell = {
      ...inputCell,
      id: "adjacent_input",
      x: 80,
    };
    const detectedParent: LayoutCell = {
      ...labelCell,
      id: "detected_parent",
      role: "parent_label",
      source: "detected",
    };
    const manualParent: LayoutCell = {
      ...detectedParent,
      id: "manual_parent",
      source: "manual",
    };

    const imageLayout = normalizeLayout({
      ...layout([detectedParent, adjacentInput], []),
      detector: "opencv",
    });
    const spreadsheetLayout = normalizeLayout({
      ...layout([manualParent, adjacentInput], []),
      detector: "xlsx-structure",
    });

    expect(imageLayout.cells.find((cell) => cell.id === detectedParent.id)?.role)
      .toBe("parent_label");
    expect(spreadsheetLayout.cells.find((cell) => cell.id === manualParent.id)?.role)
      .toBe("parent_label");
  });

  test("links a stale imported heading-like label after generic normalization", () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const staleLabel = {
      ...labelCell,
      role: "parent_label" as const,
      source: "detected" as const,
    };
    const touchingInput = { ...inputCell, x: 80 };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          ...layout([staleLabel, touchingInput], []),
          detector: "xlsx-structure",
        }}
        workflowStep="meaning"
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });

    fireEvent.click(editor.getByRole("button", { name: "意味付け" }));
    expect(editor.queryByText(/項目名から記入欄を通過するようにドラッグ/)).not.toBeInTheDocument();
    fireEvent.pointerDown(canvas, { clientX: 45, clientY: 25, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 120, clientY: 25, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 120, clientY: 25, pointerId: 1 });

    expect(editor.queryByText(/から1個の手書き文字欄へ/)).not.toBeInTheDocument();
    expect(within(view.container.querySelector(".relation-table")!).getByText(touchingInput.id))
      .toBeInTheDocument();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("does not expose obsolete row or column number classifications", () => {
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout()}
        workflowStep="classify"
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);

    expect(editor.queryByRole("button", { name: "行番号" })).not.toBeInTheDocument();
    expect(editor.queryByRole("button", { name: "列番号" })).not.toBeInTheDocument();
    expect(editor.queryByRole("button", { name: "行番号にする" })).not.toBeInTheDocument();
    expect(editor.queryByRole("button", { name: "列番号にする" })).not.toBeInTheDocument();
  });

  test("deduplicates repeated JSON rows into a reusable label candidate pool", () => {
    const pool = buildLabelPoolEntries(allSaleTargets);

    expect(pool).toHaveLength(7);
    expect(pool.find((entry) => entry.key === "saleCalves[].priceYen"))
      .toEqual(expect.objectContaining({ label: "価格", paths: expect.arrayContaining([
        "saleCalves.0.priceYen",
        "saleCalves.9.priceYen",
      ]) }));
  });

  test("shows only the controls required by each guided wizard step", () => {
    const commonProps = {
      source: "data:image/png;base64,",
      initialLayout: layout(),
      targets: [scalarTarget],
      onDetect: vi.fn(),
      onRecognizeLabels: vi.fn(async (current: LayoutDefinition) => current),
      onSave: vi.fn(async (current: LayoutDefinition) => current),
    };
    const view = render(<LayoutEditor {...commonProps} workflowStep="classify" />);
    const editor = within(view.container);

    expect(editor.getByRole("button", { name: "拡大鏡をオンにする" })).toHaveAttribute("aria-pressed", "false");
    expect(editor.getByRole("slider", { name: "拡大率" })).toBeDisabled();
    expect(editor.getByRole("slider", { name: "拡大窓の大きさ" })).toBeDisabled();
    expect(editor.getByRole("button", { name: "項目名の文字候補を取得" })).toBeInTheDocument();
    expect(view.container.querySelector(".label-pool-review")).not.toBeInTheDocument();
    expect(view.container.querySelector(".cell-counts")).not.toBeInTheDocument();
    expect(view.container.querySelector(".cell-select-details")).not.toBeInTheDocument();
    expect(editor.getByRole("button", { name: "区分変更" })).toBeInTheDocument();
    expect(editor.getByRole("button", { name: "枠を追加" })).toBeInTheDocument();
    expect(editor.getByRole("button", { name: "消しゴム" })).toBeInTheDocument();
    expect(editor.queryByRole("button", { name: /未設定/ })).not.toBeInTheDocument();

    view.rerender(<LayoutEditor {...commonProps} workflowStep="meaning" />);
    expect(editor.getByRole("button", { name: "意味付け" })).toBeInTheDocument();
    expect(editor.getByText("読み取る内容の保存先")).toBeInTheDocument();
    expect(editor.getByRole("button", { name: "未設定なし" })).toBeDisabled();
    expect(editor.queryByRole("button", { name: "繰り返し部分をまとめる" })).not.toBeInTheDocument();

    view.rerender(<LayoutEditor {...commonProps} workflowStep="records" />);
    expect(editor.getByRole("button", { name: "繰り返し部分をまとめる" })).toBeInTheDocument();
    expect(editor.getByRole("button", { name: "見出しへ接続" })).toBeInTheDocument();
    expect(editor.queryByText("読み取る内容の保存先")).not.toBeInTheDocument();

    view.rerender(<LayoutEditor {...commonProps} workflowStep="identity" />);
    expect(editor.getByLabelText("書類を見分ける欄を選択する帳票プレビュー")).toBeInTheDocument();
    expect(editor.getByText("4-A 同じ書類を探す条件")).toBeInTheDocument();
    expect(editor.getByText("4-B ライブラリ・出力で使う書類名")).toBeInTheDocument();
    expect(editor.queryByRole("button", { name: "項目名の文字候補を取得" })).not.toBeInTheDocument();
    expect(editor.queryByRole("button", { name: "消しゴム" })).not.toBeInTheDocument();

    view.rerender(<LayoutEditor {...commonProps} workflowStep="review" />);
    expect(editor.getByRole("button", { name: "拡大鏡をオンにする" })).toBeInTheDocument();
    expect(editor.getAllByText("確認・保存")).toHaveLength(2);
    expect(editor.getByText("保存判定")).toBeInTheDocument();
    expect(editor.queryByRole("button", { name: /未設定/ })).not.toBeInTheDocument();
    expect(editor.queryByRole("button", { name: "繰り返し部分をまとめる" })).not.toBeInTheDocument();
  });

  test("strongly focuses unassigned meaning cells without making the layout dirty", async () => {
    const secondInput: LayoutCell = {
      ...inputCell,
      id: "input_name_second",
      y: 55,
    };
    const onDirtyChange = vi.fn();
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelCell, inputCell, secondInput], [relation])}
        targets={[scalarTarget]}
        workflowStep="meaning"
        onDetect={vi.fn()}
        onSave={vi.fn(async (current: LayoutDefinition) => current)}
        onDirtyChange={onDirtyChange}
      />,
    );
    const editor = within(view.container);
    const toggle = editor.getByRole("button", {
      name: "未設定の記入欄を強調（1件）",
    });

    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(view.container.querySelector(".mapping-canvas-wrap"))
      .toHaveClass("unassigned-highlight-active");
    expect(editor.getByLabelText("セル一覧から選択")).toHaveValue(secondInput.id);
    expect(editor.getByRole("option", { name: /input_name_second.*未設定/ }))
      .toBeInTheDocument();
    expect(onDirtyChange).not.toHaveBeenCalledWith(true);

    fireEvent.change(
      view.container.querySelector(".property-select select") as HTMLSelectElement,
      { target: { value: scalarTarget.path } },
    );

    await waitFor(() => {
      expect(editor.getByRole("button", { name: "未設定なし" })).toBeDisabled();
    });
    expect(view.container.querySelector(".mapping-canvas-wrap"))
      .not.toHaveClass("unassigned-highlight-active");
    expect(editor.getByText(
      "読み取る内容が未設定の記入欄はありません",
      { exact: false },
    )).toBeInTheDocument();
  });

  test("keeps the XLSX cell restore action available from the meaning step", () => {
    const onDetect = vi.fn(async () => layout());
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout()}
        targets={[scalarTarget]}
        workflowStep="meaning"
        detectLabel="元のExcelセル構造に戻す"
        onDetect={onDetect}
        onSave={vi.fn(async (current: LayoutDefinition) => current)}
      />,
    );
    const editor = within(view.container);

    expect(editor.getByRole("button", { name: "元のExcelセル構造に戻す" }))
      .toBeInTheDocument();
    expect(editor.queryByRole("button", { name: "枠を再検出" }))
      .not.toBeInTheDocument();
  });

  test("shows the resizable magnifier beside the pointer using the selected scale", () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    let animationFrame = 0;
    const requestFrame = vi.spyOn(window, "requestAnimationFrame")
      .mockImplementation((callback) => {
        animationFrame += 1;
        callback(animationFrame);
        return animationFrame;
      });
    const cancelFrame = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout()}
        targets={[scalarTarget]}
        workflowStep="classify"
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス") as HTMLCanvasElement;
    const wrapper = view.container.querySelector(".mapping-canvas-wrap") as HTMLDivElement;
    const lens = view.container.querySelector(".layout-magnifier") as HTMLDivElement;
    const lensCanvas = view.container.querySelector(".layout-magnifier-canvas") as HTMLCanvasElement;
    const drawImage = vi.fn();
    const lensContext = {
      clearRect: vi.fn(),
      drawImage,
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      imageSmoothingEnabled: false,
      imageSmoothingQuality: "low",
      strokeStyle: "",
      lineWidth: 1,
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(lensCanvas, "getContext").mockReturnValue(lensContext);
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 600, bottom: 400,
      width: 600, height: 400, toJSON: () => ({}),
    });
    vi.spyOn(wrapper, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 800, bottom: 700,
      width: 800, height: 700, toJSON: () => ({}),
    });

    expect(lens).toHaveAttribute("hidden");
    fireEvent.click(editor.getByRole("button", { name: "拡大鏡をオンにする" }));
    expect(editor.getByRole("button", { name: "拡大鏡をオフにする" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(editor.getByRole("slider", { name: "拡大率" }), {
      target: { value: "3.25" },
    });
    fireEvent.change(editor.getByRole("slider", { name: "拡大窓の大きさ" }), {
      target: { value: "300" },
    });
    fireEvent.pointerEnter(canvas, { clientX: 300, clientY: 200, pointerId: 1 });

    expect(drawImage).toHaveBeenCalledTimes(1);
    expect(lens).not.toHaveAttribute("hidden");
    expect(lens).toHaveTextContent("3.25×");
    expect(lens).toHaveAttribute("aria-hidden", "true");
    expect(lens).toHaveStyle({ left: "318px", top: "218px", width: "300px", height: "300px" });
    expect(lensCanvas).toHaveStyle({ width: "300px", height: "300px" });

    fireEvent.pointerLeave(canvas, { pointerId: 1 });
    expect(lens).toHaveAttribute("hidden");
    fireEvent.click(editor.getByRole("button", { name: "拡大鏡をオフにする" }));
    expect(lens).toHaveAttribute("hidden");

    view.unmount();
    requestFrame.mockRestore();
    cancelFrame.mockRestore();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("exposes only a standalone text rectangle as the document identity", async () => {
    const repeatedInput: LayoutCell = {
      ...inputCell,
      id: "sale_price",
      x: 210,
      width: 80,
    };
    const identityLayout: LayoutDefinition = {
      ...layout([labelCell, inputCell, repeatedInput], [
        relation,
        {
          ...relation,
          id: "sale_relation",
          inputCellId: repeatedInput.id,
          targetPath: "saleCalves.0.priceYen",
        },
      ]),
      recordSections: [{
        id: "sales",
        title: "売却子牛一覧",
        collectionPath: "saleCalves",
        recordType: "saleCalf",
        recordLabelCellIds: [],
        parentLabelCellId: null,
        source: "manual",
      }],
      recordGroups: [{
        id: "sale-row-1",
        sectionId: "sales",
        order: 1,
        x: 210,
        y: 10,
        width: 80,
        height: 30,
        inputCellIds: [repeatedInput.id],
        recordKeyField: "parity",
        recordKeyValue: null,
        source: "manual",
      }],
    };
    const onIdentityChange = vi.fn();
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={identityLayout}
        targets={[scalarTarget, targets[0]]}
        workflowStep="identity"
        documentIdentityFields={[]}
        onDocumentIdentityFieldsChange={onIdentityChange}
        onDetect={vi.fn()}
        onSave={vi.fn(async (current: LayoutDefinition) => current)}
      />,
    );
    const editor = within(view.container);
    const canvas = editor.getByLabelText("書類を見分ける欄を選択する帳票プレビュー");
    await waitFor(() => expect(canvas).toHaveClass("layout-mode-identity"));
    expect(editor.getByText("選択できる欄").nextSibling).toHaveTextContent("1件");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });

    fireEvent.click(canvas, { clientX: 150, clientY: 25 });
    expect(onIdentityChange).toHaveBeenCalledWith([
      expect.objectContaining({
        targetPath: "document.cattleName",
        matchRole: "confirmation",
        displayRole: "none",
        matchGroupId: "identity_group_1",
        matchGroupLabel: "照合条件1",
        componentOrder: 0,
      }),
    ]);
    expect(editor.getByText("「牛名」を書類を見分ける情報へ追加しました。")).toBeInTheDocument();

    fireEvent.click(canvas, { clientX: 250, clientY: 25 });
    expect(onIdentityChange).toHaveBeenCalledTimes(1);
    expect(editor.getByText(/この矩形は書類を見分ける欄には使用できません/)).toBeInTheDocument();
  });

  test("selects multiple identity fields and separates matching from card display", async () => {
    const idInput: LayoutCell = { ...inputCell, id: "input_id", x: 0, width: 90 };
    const nameInput: LayoutCell = { ...inputCell, id: "input_name", x: 110, width: 90 };
    const identityLayout = layout([idInput, nameInput], [
      { ...relation, id: "relation_id", inputCellId: idInput.id, labelCellIds: [], targetPath: "document.unifiedEarTag" },
      { ...relation, id: "relation_name", inputCellId: nameInput.id, labelCellIds: [], targetPath: "document.cattleName" },
    ]);
    const identityTargets: FieldMappingTarget[] = [
      { path: "document.unifiedEarTag", label: "個体識別番号", section: "基本情報" },
      scalarTarget,
    ];
    function Harness() {
      const [fields, setFields] = useState<DocumentIdentity[]>([]);
      const [nameRule, setNameRule] = useState<{
        fieldPaths: string[];
        separator: string;
        prefix: string;
        suffix: string;
      } | null>(null);
      return (
        <LayoutEditor
          source="data:image/png;base64,"
          initialLayout={identityLayout}
          targets={identityTargets}
          workflowStep="identity"
          documentIdentityFields={fields}
          onDocumentIdentityFieldsChange={setFields}
          documentNameRule={nameRule}
          onDocumentNameRuleChange={setNameRule}
          onDetect={vi.fn()}
          onSave={vi.fn(async (current: LayoutDefinition) => current)}
        />
      );
    }
    const view = render(<Harness />);
    const editor = within(view.container);
    const canvas = editor.getByLabelText("書類を見分ける欄を選択する帳票プレビュー");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 300, bottom: 100,
      width: 300, height: 100, toJSON: () => ({}),
    });

    fireEvent.click(canvas, { clientX: 45, clientY: 25 });
    fireEvent.click(canvas, { clientX: 155, clientY: 25 });

    expect(editor.getByText("選択中").nextSibling).toHaveTextContent("2件");
    expect(editor.getByLabelText("個体識別番号の照合グループ")).toHaveValue("identity_group_1");
    expect(editor.getByLabelText("牛名の照合グループ")).toHaveValue("identity_group_1");
    fireEvent.change(editor.getByLabelText("選択中のグループ名"), {
      target: { value: "氏名" },
    });
    expect(editor.getByText("氏名（個体識別番号＋牛名）がすべて一致")).toBeInTheDocument();

    fireEvent.click(editor.getByRole("checkbox", { name: "個体識別番号" }));
    fireEvent.click(editor.getByRole("checkbox", { name: "牛名" }));
    fireEvent.change(editor.getByLabelText("項目間の区切り"), {
      target: { value: "_" },
    });
    const preview = editor.getByLabelText("書類名の生成プレビュー");
    expect(within(preview).getByText("個体識別番号_牛名")).toBeInTheDocument();
  });

  test("saves classified rectangles before they are linked to records", async () => {
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([inputCell], [])}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);

    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: inputCell.id },
    });
    const properties = within(view.container.querySelector(".property-form")!);
    fireEvent.click(properties.getByRole("button", { name: "項目名" }));

    expect(editor.getByText(
      "途中保存できます。OCRへ進むには、手書き文字欄または選択マーク欄を保存先へ意味づけしてください。",
    )).toBeInTheDocument();
    fireEvent.click(editor.getByRole("button", { name: "レイアウトを途中保存" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0]![0]).toEqual(expect.objectContaining({
      relations: [],
      cells: [expect.objectContaining({ id: inputCell.id, role: "label" })],
    }));
  });

  test("treats newly classified fields as a draft beside completed mappings", async () => {
    const extraInput: LayoutCell = {
      ...inputCell,
      id: "unassigned_history",
      x: 220,
    };
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelCell, inputCell, extraInput], [relation])}
        targets={[scalarTarget]}
        allowUnchangedSave
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);

    expect(editor.getByText(
      "途中保存できます。OCRへ進むには、未紐づけの手書き文字欄・選択マーク欄を意味づけするか、除外へ変更してください。",
    )).toBeInTheDocument();
    fireEvent.click(editor.getByRole("button", { name: "レイアウトを途中保存" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  });

  test("keeps unsaved edits when redetection is cancelled", () => {
    const onDetect = vi.fn();
    const onDirtyChange = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout()}
        targets={[scalarTarget]}
        onDetect={onDetect}
        onSave={vi.fn()}
        onDirtyChange={onDirtyChange}
      />,
    );
    const editor = within(view.container);

    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: inputCell.id },
    });
    const properties = within(view.container.querySelector(".property-form")!);
    fireEvent.click(properties.getByRole("button", { name: "項目名" }));

    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(editor.getByText("保存していないレイアウト変更があります。")).toBeInTheDocument();

    fireEvent.click(editor.getByRole("button", { name: "枠を再検出" }));

    expect(confirm).toHaveBeenCalledWith(
      "保存していないレイアウト変更があります。変更を破棄して「枠を再検出」を実行しますか？",
    );
    expect(onDetect).not.toHaveBeenCalled();
    expect(properties.getByRole("button", { name: "項目名" })).toHaveClass("active");
  });

  test("does not overwrite dirty edits when the parent redraws initialLayout", () => {
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout()}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);
    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: inputCell.id },
    });
    const properties = within(view.container.querySelector(".property-form")!);
    fireEvent.click(properties.getByRole("button", { name: "項目名" }));

    view.rerender(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([
          labelCell,
          { ...inputCell, x: 180 },
        ])}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    expect(properties.getByRole("button", { name: "項目名" })).toHaveClass("active");
    expect(editor.getByText("保存していないレイアウト変更があります。")).toBeInTheDocument();
  });

  test("supports direct assignment without the duplicate list-based mapping panel", () => {
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelCell, inputCell], [])}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);

    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: labelCell.id },
    });
    const properties = within(view.container.querySelector(".property-form")!);
    fireEvent.change(properties.getByLabelText("項目名の文字候補"), {
      target: { value: "母牛名" },
    });
    expect(properties.getByLabelText("項目名の文字候補")).toHaveValue("母牛名");

    expect(editor.queryByText("座標を指定して記入欄を追加")).not.toBeInTheDocument();
    expect(editor.queryByText("位置と大きさ")).not.toBeInTheDocument();

    expect(editor.queryByText("一覧から意味づけ")).not.toBeInTheDocument();
    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: inputCell.id },
    });
    fireEvent.click(editor.getByRole("button", { name: "基本情報・牛名をこの欄に設定" }));

    const relationTable = within(view.container.querySelector(".relation-table")!);
    expect(relationTable.getByText(inputCell.id)).toBeInTheDocument();
    expect(relationTable.getByText("牛名")).toBeInTheDocument();
    expect(editor.getByText("保存していないレイアウト変更があります。")).toBeInTheDocument();
  });

  test("automatically adds an edited unknown label to the candidate pool", async () => {
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([{ ...labelCell, text: "" }, inputCell], [])}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);

    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: labelCell.id },
    });
    const labelText = editor.getByLabelText("項目名の文字候補");
    fireEvent.change(labelText, { target: { value: "名号" } });
    fireEvent.blur(labelText);

    expect(editor.getByText("未登録ラベル「名号」を候補プールへ自動追加しました。"))
      .toBeInTheDocument();
    fireEvent.click(editor.getByRole("button", { name: /レイアウト.*保存/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0]![0].labelPool).toContainEqual(expect.objectContaining({
      label: "名号",
      targetPath: "customFields.名号",
      section: "自動追加",
    }));
  });

  test("adds a new save item and assigns it to the selected input in one action", async () => {
    const unknownLabel = { ...labelCell, text: "名号" };
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([unknownLabel, inputCell], [])}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: inputCell.id },
    });
    const sidebarAdditionButton = editor.getByRole("button", { name: "新しい保存項目を追加" });
    fireEvent.click(sidebarAdditionButton);
    const sidebarAddition = sidebarAdditionButton.closest(".meaning-target-addition") as HTMLElement;
    expect(sidebarAddition).toHaveClass("compact-addition");
    fireEvent.change(within(sidebarAddition).getByLabelText("新しい項目名"), {
      target: { value: unknownLabel.text },
    });
    fireEvent.click(within(sidebarAddition).getByRole("button", { name: "この項目を追加" }));

    expect(editor.getByText(/「名号」を保存する項目へ追加し、選択中の記入欄へ設定しました/))
      .toBeInTheDocument();
    fireEvent.click(editor.getByRole("button", { name: /レイアウト.*保存/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.labelPool).toContainEqual(expect.objectContaining({
      label: "名号",
      targetPath: "customFields.名号",
    }));
    expect(saved.relations).toContainEqual(expect.objectContaining({
      inputCellId: inputCell.id,
      labelCellIds: [],
      targetPath: "customFields.名号",
    }));
  });

  test("OCRs only label cells and shows the result in the existing cell editor", async () => {
    const onRecognizeLabels = vi.fn(async (current: LayoutDefinition) => ({
      ...current,
      ocrAvailable: true,
      cells: current.cells.map((cell) => cell.id === labelCell.id
        ? { ...cell, text: "価格", textConfidence: .96 }
        : cell),
    }));
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelCell, inputCell], [])}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onRecognizeLabels={onRecognizeLabels}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);

    fireEvent.click(editor.getByRole("button", { name: "項目名の文字候補を取得" }));

    await waitFor(() => expect(onRecognizeLabels).toHaveBeenCalledTimes(1));
    expect(onRecognizeLabels.mock.calls[0]![0].cells.find((cell) => cell.id === inputCell.id)?.text).toBe("");
    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: labelCell.id },
    });
    expect(editor.getByLabelText("項目名の文字候補")).toHaveValue("価格");
    expect(editor.getByLabelText("保存する情報")).toHaveDisplayValue("価格（文字候補）");
    expect(view.container.querySelector(".selected-label-ocr-result")).not.toBeInTheDocument();
    expect(editor.getByText(/項目名を確認し、1個に文字候補を反映しました/)).toBeInTheDocument();
  });

  test("does not re-OCR labels already used by meaning definitions", () => {
    const onRecognizeLabels = vi.fn(async (current: LayoutDefinition) => current);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelCell, inputCell], [relation])}
        targets={[scalarTarget]}
        workflowStep="classify"
        onDetect={vi.fn()}
        onRecognizeLabels={onRecognizeLabels}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);

    fireEvent.click(editor.getByRole("button", { name: "項目名の文字候補を取得" }));

    expect(onRecognizeLabels).not.toHaveBeenCalled();
    expect(editor.getByText(
      "文字候補を取得する未設定の項目名がありません。読み取る内容や繰り返し部分を設定済みの項目名は対象から除外されます。",
    )).toBeInTheDocument();
  });

  test("draws the OCR result inside its label rectangle", () => {
    const fillStyles: string[] = [];
    let fillStyle = "";
    const context = {
      save: vi.fn(),
      restore: vi.fn(),
      fillRect: vi.fn(),
      fillText: vi.fn(),
      measureText: vi.fn((value: string) => ({ width: value.length * 12 })),
      get fillStyle() {
        return fillStyle;
      },
      set fillStyle(value: string) {
        fillStyle = value;
        fillStyles.push(value);
      },
      font: "",
      textBaseline: "alphabetic",
      textAlign: "start",
    } as unknown as CanvasRenderingContext2D;

    drawOcrLabelText(context, { ...labelCell, text: "価格" }, 300);

    expect(context.fillRect).toHaveBeenCalledWith(12, 12, 66, 17.2);
    expect(fillStyles).toEqual(["rgba(255, 240, 213, 0.92)", "#5f3a00"]);
    expect(context.textAlign).toBe("left");
    expect(context.fillText).toHaveBeenCalledWith("価格", 16, 16, 58);
  });

  test.each([
    ["center", 45],
    ["right", 74],
  ] as const)("draws OCR text using %s alignment", (alignment, expectedX) => {
    const context = {
      save: vi.fn(),
      restore: vi.fn(),
      fillRect: vi.fn(),
      fillText: vi.fn(),
      measureText: vi.fn((value: string) => ({ width: value.length * 12 })),
      fillStyle: "",
      font: "",
      textBaseline: "alphabetic",
      textAlign: "start",
    } as unknown as CanvasRenderingContext2D;

    drawOcrLabelText(context, { ...labelCell, text: "価格" }, 300, alignment);

    expect(context.textAlign).toBe(alignment);
    expect(context.fillText).toHaveBeenCalledWith("価格", expectedX, 16, 58);
  });

  test("switches the OCR label text alignment from the toolbar", () => {
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelCell], [])}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);

    expect(editor.getByRole("button", { name: "自動配置" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(editor.getByRole("button", { name: "右揃え" }));

    expect(editor.getByRole("button", { name: "左揃え" })).toHaveAttribute("aria-pressed", "false");
    expect(editor.getByRole("button", { name: "右揃え" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(editor.getByRole("button", { name: "上下中央揃え" }));
    expect(editor.getByRole("button", { name: "右揃え" })).toHaveAttribute("aria-pressed", "true");
    expect(editor.getByRole("button", { name: "上下中央揃え" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(editor.getByRole("button", { name: "下揃え" }));
    expect(editor.getByRole("button", { name: "上下中央揃え" })).toHaveAttribute("aria-pressed", "false");
    expect(editor.getByRole("button", { name: "下揃え" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(editor.getByRole("button", { name: "自動配置" }));
    expect(editor.getByRole("button", { name: "自動配置" })).toHaveAttribute("aria-pressed", "true");
    expect(editor.getByRole("button", { name: "縦位置を自動配置" })).toHaveAttribute("aria-pressed", "true");
    expect(editor.getByRole("button", { name: "右揃え" })).toHaveAttribute("aria-pressed", "false");
    expect(editor.getByRole("button", { name: "下揃え" })).toHaveAttribute("aria-pressed", "false");
  });

  test("auto resolves different horizontal and vertical positions for each rectangle", () => {
    for (const horizontal of ["left", "center", "right"] as const) {
      for (const vertical of ["top", "middle", "bottom"] as const) {
        const cell = { ...labelCell, width: 160, height: 100, text: "見出し",
          textDirection: "horizontal" as const,
          textAlignment: horizontal, textVerticalAlignment: vertical };
        const fillRect = vi.fn();
        const fillText = vi.fn();
        const context = { save: vi.fn(), restore: vi.fn(), fillRect, fillText,
          measureText: vi.fn((text: string) => ({ width: text.length * 12 })),
        } as unknown as CanvasRenderingContext2D;
        drawOcrLabelText(context, cell, 1200, "auto", "auto");
        const [x, y, width, height] = fillRect.mock.calls[0] as number[];
        expect(context.textAlign).toBe(horizontal);
        if (horizontal === "center") expect(fillText.mock.calls[0][1]).toBe(cell.x + cell.width / 2);
        if (horizontal === "left") expect(fillText.mock.calls[0][1]).toBe(x + 4);
        if (horizontal === "right") expect(fillText.mock.calls[0][1]).toBe(x + width - 4);
        if (vertical === "top") expect(y).toBe(cell.y + 2);
        if (vertical === "middle") expect(y + height / 2).toBeCloseTo(cell.y + cell.height / 2);
        if (vertical === "bottom") expect(y + height).toBeCloseTo(cell.y + cell.height - 2);
      }
    }
  });

  test.each(["horizontal", "vertical", "multiline"] as const)(
    "positions %s text and its background together on both axes without changing the cell",
    (direction) => {
      const cell = { ...labelCell, width: 120, height: 180,
        text: direction === "multiline" ? "項目名\n見出し" : "項目名",
        textDirection: direction === "vertical" ? "vertical" as const : "horizontal" as const };
      const original = structuredClone(cell);
      for (const horizontal of ["left", "center", "right"] as const) {
        const ys: number[] = [];
        for (const vertical of ["top", "middle", "bottom"] as const) {
          const fillRect = vi.fn();
          const fillText = vi.fn();
          const context = { save: vi.fn(), restore: vi.fn(), fillRect, fillText,
            measureText: vi.fn((value: string) => ({ width: value.length * 12 })),
          } as unknown as CanvasRenderingContext2D;
          drawOcrLabelText(context, cell, 1200, horizontal, vertical);
          const [x, y, width, height] = fillRect.mock.calls[0] as number[];
          ys.push(y);
          if (vertical === "top") expect(y).toBe(cell.y + 2);
          if (vertical === "middle") expect(y + height / 2).toBeCloseTo(cell.y + cell.height / 2);
          if (vertical === "bottom") expect(y + height).toBeCloseTo(cell.y + cell.height - 2);
          for (const [, textX, textY] of fillText.mock.calls) {
            expect(textX).toBeGreaterThanOrEqual(x);
            expect(textX).toBeLessThanOrEqual(x + width);
            expect(textY).toBeGreaterThan(y);
            expect(textY).toBeLessThan(y + height);
          }
          expect(fillText).toHaveBeenCalledTimes(direction === "vertical" ? 3 : direction === "multiline" ? 2 : 1);
        }
        expect(ys[0]).toBeLessThan(ys[1]);
        expect(ys[1]).toBeLessThan(ys[2]);
      }
      expect(cell).toEqual(original);
    },
  );

  test("shows OCR toolbar and alignment switches in non-label workflow steps", () => {
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelCell], [])}
        workflowStep="meaning"
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onRecognizeLabels={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);

    expect(editor.getByRole("button", { name: "項目名の文字候補を取得" })).toBeInTheDocument();
    expect(editor.getByRole("button", { name: "左揃え" })).toBeInTheDocument();
    expect(editor.getByRole("button", { name: "中央揃え" })).toBeInTheDocument();
    expect(editor.getByRole("button", { name: "右揃え" })).toBeInTheDocument();
  });

  test("keeps the first meaning line when a second line is created", async () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const birthTarget: FieldMappingTarget = {
      path: "document.birthDate",
      label: "生年月日",
      section: "基本情報",
    };
    const birthLabel: LayoutCell = {
      ...labelCell,
      id: "label_birth",
      y: 60,
      text: "生年月日",
    };
    const birthInput: LayoutCell = {
      ...inputCell,
      id: "input_birth",
      y: 60,
    };
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelCell, inputCell, birthLabel, birthInput], [])}
        targets={[scalarTarget, birthTarget]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });
    const drag = (from: [number, number], to: [number, number]) => {
      fireEvent.pointerDown(canvas, {
        clientX: from[0], clientY: from[1], pointerId: 1,
      });
      fireEvent.pointerMove(canvas, {
        clientX: to[0], clientY: to[1], pointerId: 1,
      });
      fireEvent.pointerUp(canvas, {
        clientX: to[0], clientY: to[1], pointerId: 1,
      });
    };

    fireEvent.click(editor.getByRole("button", { name: "意味付け" }));
    fireEvent.change(editor.getByLabelText("保存する情報"), {
      target: { value: scalarTarget.path },
    });
    drag([45, 25], [150, 25]);
    drag([45, 75], [150, 75]);
    fireEvent.click(view.container.querySelector(".layout-save") as HTMLButtonElement);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.relations).toEqual(expect.arrayContaining([
      expect.objectContaining({
        inputCellId: inputCell.id,
        labelCellIds: [labelCell.id],
        targetPath: scalarTarget.path,
      }),
      expect.objectContaining({
        inputCellId: birthInput.id,
        labelCellIds: [birthLabel.id],
        targetPath: birthTarget.path,
      }),
    ]));
    expect(saved.relations).toHaveLength(2);
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("connects one label to multiple inputs without replacing the first line", async () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const priceLabel: LayoutCell = {
      ...labelCell,
      id: "label_price",
      text: "価格",
    };
    const firstPriceInput: LayoutCell = {
      ...inputCell,
      id: "input_price_1",
    };
    const secondPriceInput: LayoutCell = {
      ...inputCell,
      id: "input_price_2",
      y: 60,
    };
    const priceTargets = targets.slice(0, 2);
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([priceLabel, firstPriceInput, secondPriceInput], [])}
        targets={priceTargets}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });
    const drag = (to: [number, number]) => {
      fireEvent.pointerDown(canvas, {
        clientX: 45, clientY: 25, pointerId: 1,
      });
      fireEvent.pointerMove(canvas, {
        clientX: to[0], clientY: to[1], pointerId: 1,
      });
      fireEvent.pointerUp(canvas, {
        clientX: to[0], clientY: to[1], pointerId: 1,
      });
    };

    fireEvent.click(editor.getByRole("button", { name: "意味付け" }));
    fireEvent.change(editor.getByLabelText("保存する情報"), {
      target: { value: priceTargets[0].path },
    });
    drag([150, 25]);
    fireEvent.change(editor.getByLabelText("保存する情報"), {
      target: { value: priceTargets[0].path },
    });
    drag([150, 75]);
    fireEvent.click(view.container.querySelector(".layout-save") as HTMLButtonElement);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.relations).toEqual(expect.arrayContaining([
      expect.objectContaining({
        inputCellId: firstPriceInput.id,
        labelCellIds: [priceLabel.id],
        targetPath: priceTargets[0].path,
      }),
      expect.objectContaining({
        inputCellId: secondPriceInput.id,
        labelCellIds: [priceLabel.id],
        targetPath: priceTargets[1].path,
      }),
    ]));
    expect(saved.relations).toHaveLength(2);
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("connects an unregistered column label to every vertical input without a preset target", async () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const vehicleLabel: LayoutCell = {
      ...labelCell,
      id: "label_vehicle_name",
      text: "車種名",
      x: 20,
      y: 10,
      width: 80,
      height: 30,
    };
    const vehicleInputs: LayoutCell[] = Array.from({ length: 3 }, (_, index) => ({
      ...inputCell,
      id: `input_vehicle_${index + 1}`,
      x: 20,
      y: 38 + index * 30,
      width: 80,
      height: 30,
    }));
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          ...layout([vehicleLabel, ...vehicleInputs], []),
          canonicalHeight: 160,
        }}
        targets={[]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 300, bottom: 160,
      width: 300, height: 160, toJSON: () => ({}),
    });

    fireEvent.click(editor.getByRole("button", { name: "意味付け" }));
    fireEvent.pointerDown(canvas, { clientX: 60, clientY: 25, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 60, clientY: 113, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 60, clientY: 113, pointerId: 1 });

    expect(editor.queryByText(/車種名から3個の手書き文字欄へ、縦方向にまとめて意味づけしました/))
      .not.toBeInTheDocument();
    fireEvent.click(view.container.querySelector(".layout-save") as HTMLButtonElement);
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.relations.map((relation) => relation.targetPath)).toEqual([
      "customFields.車種名_1",
      "customFields.車種名_2",
      "customFields.車種名_3",
    ]);
    expect(saved.relations.every((relation) => (
      relation.labelCellIds.includes(vehicleLabel.id)
      && relation.axis === "vertical"
    ))).toBe(true);
    expect(saved.labelPool?.map((entry) => entry.targetPath)).toEqual([
      "customFields.車種名_1",
      "customFields.車種名_2",
      "customFields.車種名_3",
    ]);

    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("connects a label to a mark cell and saves the relation", async () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const calvingTargets: FieldMappingTarget[] = [
      { path: "calvings.0.outcome", label: "繁殖履歴 1行目・分娩状況", section: "繁殖履歴" },
      { path: "calvings.0.sex", label: "繁殖履歴 1行目・性別", section: "繁殖履歴" },
    ];
    const markCell: LayoutCell = {
      ...inputCell,
      id: "calving_mark",
      role: "mark",
      markSets: [
        {
          id: "outcome",
          targetPath: "calvings.0.outcome",
          recognitionMode: "automatic",
          selectionMode: "multiple",
          options: [{
            id: "twin", label: "双", value: "twin",
            x: 0, y: 0, width: 50, height: 30,
          }],
        },
        {
          id: "sex",
          targetPath: "calvings.0.sex",
          recognitionMode: "automatic",
          selectionMode: "multiple",
          options: [{
            id: "male", label: "♂", value: "male",
            x: 50, y: 0, width: 50, height: 30,
          }],
        },
      ],
    };
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const markLayout: LayoutDefinition = {
      ...layout([labelCell, markCell], []),
      recordSections: [{
        id: "section_calvings",
        title: "繁殖履歴",
        collectionPath: "calvings",
        recordType: "calving",
        recordLabelCellIds: [],
        parentLabelCellId: null,
        source: "manual",
      }],
      recordGroups: [{
        id: "record_calving_1",
        sectionId: "section_calvings",
        order: 1,
        x: markCell.x,
        y: markCell.y,
        width: markCell.width,
        height: markCell.height,
        inputCellIds: [markCell.id],
        recordKeyField: "parity",
        recordKeyValue: "1",
        source: "manual",
      }],
      recordStructureVerified: true,
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={markLayout}
        targets={calvingTargets}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const editor = within(view.container);
    const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 300,
      bottom: 100,
      width: 300,
      height: 100,
      toJSON: () => ({}),
    });

    fireEvent.click(editor.getByRole("button", { name: "意味付け" }));
    fireEvent.pointerDown(canvas, { clientX: 45, clientY: 25, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 150, clientY: 25, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 150, clientY: 25, pointerId: 1 });
    fireEvent.click(view.container.querySelector(".layout-save") as HTMLButtonElement);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.relations).toEqual([
      expect.objectContaining({
        inputCellId: markCell.id,
        labelCellIds: [labelCell.id],
        targetPath: "calvings.0.outcome",
      }),
    ]);
    expect(saved.cells.find((cell) => cell.id === markCell.id)?.markSets)
      .toEqual(markCell.markSets);
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
  });

  test("turns a rectangle into a six-choice calving mark field", () => {
    const calvingTargets: FieldMappingTarget[] = [
      { path: "calvings.0.outcome", label: "繁殖履歴 1行目・結果", section: "繁殖履歴" },
      { path: "calvings.0.sex", label: "繁殖履歴 1行目・性別", section: "繁殖履歴" },
    ];
    const calvingLayout: LayoutDefinition = {
      ...layout([inputCell], []),
      recordSections: [{
        id: "calvings",
        title: "繁殖履歴",
        collectionPath: "calvings",
        recordType: "calving",
        recordLabelCellIds: [],
        parentLabelCellId: null,
        source: "manual",
      }],
      recordGroups: [{
        id: "calving_1",
        sectionId: "calvings",
        order: 1,
        x: inputCell.x,
        y: inputCell.y,
        width: inputCell.width,
        height: inputCell.height,
        inputCellIds: [inputCell.id],
        recordKeyField: "parity",
        recordKeyValue: "1",
        source: "manual",
      }],
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={calvingLayout}
        targets={calvingTargets}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    fireEvent.change(view.container.querySelector(".accessible-cell-select select")!, {
      target: { value: inputCell.id },
    });
    const properties = within(view.container.querySelector(".property-form")!);

    fireEvent.click(properties.getByRole("button", { name: "選択欄" }));

    expect(properties.getByText("分娩状況の選択マーク")).toBeInTheDocument();
    for (const label of ["双", "早", "流", "死", "♂", "♀"]) {
      expect(properties.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(properties.getByText("calvings.0.outcome")).toBeInTheDocument();
    expect(properties.getByText("calvings.0.sex")).toBeInTheDocument();
    expect(properties.getByLabelText("選択マークの判定範囲を画像上で調整"))
      .toBeInTheDocument();
    expect(properties.getByRole("button", {
      name: "画像から2行×3列の枠を自動検出",
    })).toBeInTheDocument();
    expect(properties.getByRole("button", { name: "2行×3列の標準配置" }))
      .toBeInTheDocument();
    expect(properties.getByText("数値で微調整する")).toBeInTheDocument();

    const visualEditor = view.container.querySelector(".mark-option-canvas") as SVGSVGElement;
    vi.spyOn(visualEditor, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 600,
      bottom: 180,
      width: 600,
      height: 180,
      toJSON: () => ({}),
    });
    const firstRegion = view.container.querySelector(".mark-option-region") as SVGRectElement;
    const initialX = Number(firstRegion.getAttribute("x"));
    const dispatchPointer = (
      target: Element,
      type: "pointerdown" | "pointermove" | "pointerup",
      clientX: number,
      clientY: number,
    ) => {
      const event = new MouseEvent(type, { bubbles: true, clientX, clientY });
      Object.defineProperty(event, "pointerId", { value: 1 });
      fireEvent(target, event);
    };
    dispatchPointer(firstRegion, "pointerdown", 10, 20);
    dispatchPointer(visualEditor, "pointermove", 70, 20);
    dispatchPointer(visualEditor, "pointerup", 70, 20);
    expect(view.container.querySelector(".mark-option-region"))
      .toHaveAttribute("x", String(initialX + 10));
  });

  test("does not treat an unrelated choice as calving from the target catalog alone", () => {
    const unrelatedChoice: LayoutCell = {
      ...inputCell,
      id: "unrelated_choice",
      text: "有 無",
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([unrelatedChoice], [])}
        targets={[
          { path: "calvings.0.outcome", label: "繁殖履歴 1行目・結果", section: "繁殖履歴" },
          { path: "calvings.0.sex", label: "繁殖履歴 1行目・性別", section: "繁殖履歴" },
        ]}
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    fireEvent.change(view.container.querySelector(".accessible-cell-select select")!, {
      target: { value: unrelatedChoice.id },
    });
    const properties = within(view.container.querySelector(".property-form")!);

    fireEvent.click(properties.getByRole("button", { name: "選択欄" }));

    expect(properties.getByText("選択欄の設定")).toBeInTheDocument();
    expect(properties.queryByText("分娩状況の選択マーク")).not.toBeInTheDocument();
  });

  test("configures a generic manual single-choice field with arbitrary options", async () => {
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const choiceCell: LayoutCell = {
      ...inputCell,
      id: "attendance_choice",
      text: "有 無",
    };
    const choiceTargets: FieldMappingTarget[] = [
      { path: "customFields.attendance", label: "受験の有無", section: "この書類" },
    ];
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([choiceCell], [])}
        targets={choiceTargets}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    fireEvent.change(view.container.querySelector(".accessible-cell-select select")!, {
      target: { value: choiceCell.id },
    });
    const properties = within(view.container.querySelector(".property-form")!);

    fireEvent.click(properties.getByRole("button", { name: "選択欄" }));

    expect(properties.getByText("選択欄の設定")).toBeInTheDocument();
    expect(properties.getByLabelText("判定方法")).toHaveValue("manual");
    expect(properties.getByLabelText("選択数")).toHaveValue("single");
    expect(properties.getAllByDisplayValue("有")).toHaveLength(2);
    expect(properties.getAllByDisplayValue("無")).toHaveLength(2);
    fireEvent.change(properties.getByLabelText("保存する情報"), {
      target: { value: "customFields.attendance" },
    });
    fireEvent.click(view.container.querySelector(".layout-save") as HTMLButtonElement);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.cells[0].markSets?.[0]).toMatchObject({
      targetPath: "customFields.attendance",
      recognitionMode: "manual",
      selectionMode: "single",
      options: [
        expect.objectContaining({ label: "有", value: "有" }),
        expect.objectContaining({ label: "無", value: "無" }),
      ],
    });
  });

  test("runs the existing auto-detection button for every selected mark cell", async () => {
    const originalPointerEvent = window.PointerEvent;
    const originalImage = globalThis.Image;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    class FailingImage {
      onload: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      set src(_value: string) {
        queueMicrotask(() => this.onerror?.(new Event("error")));
      }
    }
    Object.defineProperty(globalThis, "Image", {
      configurable: true,
      writable: true,
      value: FailingImage,
    });
    const initialSets = (rowIndex: number): LayoutMarkSet[] => [{
      id: "outcome",
      targetPath: `calvings.${rowIndex}.outcome`,
      recognitionMode: "automatic",
      selectionMode: "multiple",
      options: [{
        id: "twin", label: "双", value: "twin",
        x: 1, y: 1, width: 2, height: 2,
      }],
    }];
    const markCells: LayoutCell[] = [
      {
        ...inputCell,
        id: "mark_row_1",
        role: "mark",
        x: 10,
        y: 10,
        width: 100,
        height: 80,
        markSets: initialSets(0),
      },
      {
        ...inputCell,
        id: "mark_row_2",
        role: "mark",
        x: 160,
        y: 10,
        width: 100,
        height: 80,
        markSets: initialSets(1),
      },
    ];
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);

    try {
      const view = render(
        <LayoutEditor
          source="data:image/png;base64,invalid"
          initialLayout={layout(markCells, [])}
          targets={[]}
          onDetect={vi.fn()}
          onSave={onSave}
        />,
      );
      const editor = within(view.container);
      const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
      vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
        x: 0,
        y: 0,
        left: 0,
        top: 0,
        right: 300,
        bottom: 100,
        width: 300,
        height: 100,
        toJSON: () => ({}),
      });
      fireEvent.pointerDown(canvas, { clientX: 1, clientY: 1, pointerId: 1 });
      fireEvent.pointerMove(canvas, { clientX: 299, clientY: 99, pointerId: 1 });
      fireEvent.pointerUp(canvas, { clientX: 299, clientY: 99, pointerId: 1 });

      expect(editor.getByText("範囲選択: 2 セル")).toBeInTheDocument();
      const autoButtons = editor.getAllByRole("button", {
        name: "画像から2行×3列の枠を自動検出",
      });
      expect(autoButtons).toHaveLength(1);
      fireEvent.click(autoButtons[0]);

      await waitFor(() => expect(editor.getByText(
        "2個の選択マーク欄をまとめて処理しました。 自動検出 0件、標準配置 2件。",
      )).toBeInTheDocument());
      fireEvent.click(view.container.querySelector(".layout-save") as HTMLButtonElement);
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));

      const saved = onSave.mock.calls[0]![0];
      const savedMarks = saved.cells.filter((cell) => cell.role === "mark");
      expect(savedMarks).toHaveLength(2);
      expect(savedMarks.map((cell) => cell.markSets?.[0]?.targetPath)).toEqual([
        "calvings.0.outcome",
        "calvings.1.outcome",
      ]);
      savedMarks.forEach((cell) => {
        expect(cell.markSets?.flatMap((set) => set.options)).toHaveLength(6);
      });
      view.unmount();
    } finally {
      Object.defineProperty(globalThis, "Image", {
        configurable: true,
        writable: true,
        value: originalImage,
      });
      Object.defineProperty(window, "PointerEvent", {
        configurable: true,
        writable: true,
        value: originalPointerEvent,
      });
    }
  });

  test("assigns meaning directly to a mixed input cell and stores its printed label", async () => {
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const mixedInput: LayoutCell = {
      ...inputCell,
      id: "mixed_name",
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([mixedInput], [])}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );

    const cellSelect = view.container.querySelector(
      ".accessible-cell-select select",
    ) as HTMLSelectElement;
    fireEvent.change(cellSelect, { target: { value: mixedInput.id } });

    fireEvent.change(
      within(view.container.querySelector(".property-form")!).getByLabelText(
        "矩形内の印刷ラベル（読み取りから除外）",
      ),
      { target: { value: "名号" } },
    );
    fireEvent.change(
      view.container.querySelector(".property-select select") as HTMLSelectElement,
      { target: { value: scalarTarget.path } },
    );
    fireEvent.click(view.container.querySelector(".layout-save") as HTMLButtonElement);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0]![0];
    expect(saved.cells[0]?.printedLabelText).toBe("名号");
    expect(saved.relations[0]).toMatchObject({
      inputCellId: mixedInput.id,
      labelCellIds: [],
      targetPath: scalarTarget.path,
    });
  });

  test("assigns a pool candidate by clicking it after selecting a label-less input", () => {
    const labelLessInput: LayoutCell = {
      ...inputCell,
      id: "label_less_name",
    };
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([labelLessInput], [])}
        targets={[scalarTarget]}
        workflowStep="meaning"
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    const editor = within(view.container);
    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: labelLessInput.id },
    });

    fireEvent.click(editor.getByRole("button", {
      name: "基本情報・牛名をこの欄に設定",
    }));

    expect(editor.getByText(`${labelLessInput.id} を 牛名 として設定しました。`))
      .toBeInTheDocument();
    const relationTable = within(view.container.querySelector(".relation-table")!);
    expect(relationTable.getByText(labelLessInput.id)).toBeInTheDocument();
    expect(relationTable.getByText("牛名")).toBeInTheDocument();
    expect(editor.getByRole("button", {
      name: "基本情報・牛名をこの欄に設定",
    })).toHaveClass("active");
  });

  test("keeps each mixed cell JSON target when switching between name and birth date", async () => {
    const motherTarget: FieldMappingTarget = {
      path: "pedigree.mother",
      label: "母",
      section: "血統",
    };
    const birthDateTarget: FieldMappingTarget = {
      path: "document.birthDate",
      label: "生年月日",
      section: "基本情報",
    };
    const nameInput: LayoutCell = {
      ...inputCell,
      id: "mixed_name",
      printedLabelText: "名号",
    };
    const birthDateInput: LayoutCell = {
      ...inputCell,
      id: "mixed_birth_date",
      y: 50,
    };
    const onSave = vi.fn(async (saved: LayoutDefinition) => saved);
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={layout([nameInput, birthDateInput], [])}
        targets={[motherTarget, birthDateTarget]}
        onDetect={vi.fn()}
        onSave={onSave}
      />,
    );
    const cellSelect = view.container.querySelector(
      ".accessible-cell-select select",
    ) as HTMLSelectElement;
    const targetSelect = view.container.querySelector(
      ".property-select select",
    ) as HTMLSelectElement;

    fireEvent.change(cellSelect, { target: { value: nameInput.id } });
    fireEvent.change(targetSelect, { target: { value: motherTarget.path } });

    fireEvent.change(cellSelect, { target: { value: birthDateInput.id } });
    await waitFor(() => expect(targetSelect).toHaveValue(""));
    fireEvent.change(targetSelect, { target: { value: birthDateTarget.path } });

    fireEvent.change(cellSelect, { target: { value: nameInput.id } });
    await waitFor(() => expect(targetSelect).toHaveValue(motherTarget.path));

    fireEvent.click(view.container.querySelector(".layout-save") as HTMLButtonElement);
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));

    const saved = onSave.mock.calls[0]![0];
    expect(saved.relations).toEqual(expect.arrayContaining([
      expect.objectContaining({
        inputCellId: nameInput.id,
        targetPath: motherTarget.path,
      }),
      expect.objectContaining({
        inputCellId: birthDateInput.id,
        targetPath: birthDateTarget.path,
      }),
    ]));
  });

  test("locks a pending save against duplicate submission", async () => {
    let resolveSave!: (saved: LayoutDefinition) => void;
    const pendingSave = new Promise<LayoutDefinition>((resolve) => {
      resolveSave = resolve;
    });
    const onSave = vi.fn(() => pendingSave);
    const initialLayout = layout();
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={initialLayout}
        targets={[scalarTarget]}
        onDetect={vi.fn()}
        onSave={onSave}
        documentIdentityFields={[{
          mappingId: "relation_1",
          targetPath: "document.cattleName",
          label: "牛名",
          valueType: "text",
          exactLength: null,
          matchRole: "primary",
          displayRole: "title",
        }]}
        allowUnchangedSave
      />,
    );
    const editor = within(view.container);
    const saveButton = editor.getByRole("button", {
      name: "テンプレート設定をDBへ保存",
    });

    fireEvent.click(saveButton);
    fireEvent.click(saveButton);

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(editor.getByRole("button", { name: "処理中…" })).toBeDisabled();

    await act(async () => {
      resolveSave(initialLayout);
      await pendingSave;
    });
    await waitFor(() => {
      expect(editor.getByText("テンプレートへ2個の矩形を保存しました。")).toBeInTheDocument();
    });
  });
});
