import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import {
  cellsSelectedByRect,
  expandTargetPaths,
  inferRecordCollectionPath,
  inferRecordTargetPath,
  inferTableColumnTargetPath,
  inferTargetPath,
  LayoutEditor,
  linkLaneCells,
  mergeLabelRelations,
  segmentIntersectsRect,
  targetPathsFromRecordGroups,
  targetPathsForInputs,
} from "./LayoutEditor";
import type { FieldMappingTarget, LayoutCell, LayoutDefinition, LayoutRecordGroup, LayoutRecordSection, LayoutRelation } from "../types";

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

  test("corrects common PaddleOCR glyph errors in the sale date label", () => {
    expect(inferTargetPath("壳却日", 1, allSaleTargets, new Set())).toBe(
      "saleCalves.0.saleDate",
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
        onDetect={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const canvas = screen.getByLabelText("帳票レイアウト編集キャンバス");
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
    const classifyAsLabel = screen.getByRole("button", { name: "ラベルにする" });
    expect(classifyAsLabel).toBeTruthy();
    expect(screen.getByRole("button", { name: "記入欄にする" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "無視する" })).toBeTruthy();
    fireEvent.click(classifyAsLabel);
    expect(screen.getByText("ラベル 2")).toBeTruthy();
    expect(screen.getByText("手書き文字欄 0")).toBeTruthy();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
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

  test("creates the missing sale section and groups parity through notes", () => {
    const originalPointerEvent = window.PointerEvent;
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
    });
    const row: LayoutCell[] = [
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
    const relations: LayoutRelation[] = [
      {
        id: "price_relation",
        inputCellId: "priceYen",
        labelCellIds: [],
        axis: "horizontal",
        order: 1,
        targetPath: "saleCalves.0.priceYen",
      },
      {
        id: "sire_relation",
        inputCellId: "sireName",
        labelCellIds: [],
        axis: "horizontal",
        order: 1,
        targetPath: "saleCalves.0.sireName",
      },
    ];
    const view = render(
      <LayoutEditor
        source="data:image/png;base64,"
        initialLayout={{
          canonicalWidth: 1240,
          canonicalHeight: 1754,
          detector: "test",
          ocrAvailable: false,
          cells: row,
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
    fireEvent.click(editor.getByRole("button", { name: "レコード作成" }));
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
      clientY: 297,
      pointerId: 1,
    });
    fireEvent.pointerMove(canvas, {
      clientX: 1139,
      clientY: 292,
      pointerId: 1,
    });
    fireEvent.pointerUp(canvas, {
      clientX: 1139,
      clientY: 292,
      pointerId: 1,
    });

    expect(editor.getByText("1産次のレコード")).toBeTruthy();
    expect(editor.getByText(/7セル/)).toBeTruthy();
    expect(editor.getByText(/売却子牛一覧を自動作成/)).toBeTruthy();
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: originalPointerEvent,
    });
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
  test("adds a missing label target from the frontend", () => {
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
        onSave={vi.fn()}
      />,
    );

    const editor = within(view.container);
    fireEvent.change(editor.getByLabelText("表示名"), { target: { value: "治療歴" } });
    fireEvent.change(editor.getByLabelText("JSONキー"), {
      target: { value: "treatmentHistory" },
    });
    fireEvent.click(editor.getByRole("button", { name: "項目を追加" }));

    expect(editor.getByRole("option", { name: "治療歴" })).toHaveValue(
      "customFields.treatmentHistory",
    );
    expect(editor.getByText(/customFields\.treatmentHistory として追加/)).toBeTruthy();
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
    fireEvent.click(editor.getByRole("button", { name: "枠と文字を再検出" }));

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
    fireEvent.change(properties.getByLabelText("X"), { target: { value: "42" } });

    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(editor.getByText("保存していないレイアウト変更があります。")).toBeInTheDocument();

    fireEvent.click(editor.getByRole("button", { name: "枠と文字を再検出" }));

    expect(confirm).toHaveBeenCalledWith(
      "保存していないレイアウト変更があります。変更を破棄して枠を再検出しますか？",
    );
    expect(onDetect).not.toHaveBeenCalled();
    expect(properties.getByLabelText("X")).toHaveValue(42);
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
    fireEvent.change(properties.getByLabelText("X"), { target: { value: "44" } });

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

    expect(properties.getByLabelText("X")).toHaveValue(44);
    expect(editor.getByText("保存していないレイアウト変更があります。")).toBeInTheDocument();
  });

  test("supports list selection, coordinate entry, and meaning assignment without canvas gestures", () => {
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

    fireEvent.change(editor.getByLabelText("セル一覧から選択"), {
      target: { value: labelCell.id },
    });
    const properties = within(view.container.querySelector(".property-form")!);
    fireEvent.change(properties.getByLabelText("OCRラベル文字"), {
      target: { value: "母牛名" },
    });
    expect(properties.getByLabelText("OCRラベル文字")).toHaveValue("母牛名");

    const coordinateAdder = within(
      view.container.querySelector(".accessible-layout-adder")!,
    );
    fireEvent.change(coordinateAdder.getByLabelText("X"), { target: { value: "120" } });
    fireEvent.change(coordinateAdder.getByLabelText("Y"), { target: { value: "45" } });
    fireEvent.change(coordinateAdder.getByLabelText("幅"), { target: { value: "80" } });
    fireEvent.change(coordinateAdder.getByLabelText("高さ"), { target: { value: "32" } });
    fireEvent.click(coordinateAdder.getByRole("button", { name: "記入欄を追加" }));

    const linkEditor = within(view.container.querySelector(".accessible-link-editor")!);
    const inputOption = linkEditor.getByRole("option", { name: /manual_/ }) as HTMLOptionElement;
    fireEvent.change(editor.getByLabelText("JSON項目"), {
      target: { value: scalarTarget.path },
    });
    fireEvent.change(linkEditor.getByLabelText("ラベルセル"), {
      target: { value: labelCell.id },
    });
    fireEvent.change(linkEditor.getByLabelText("手書き文字欄"), {
      target: { value: inputOption.value },
    });
    fireEvent.click(linkEditor.getByRole("button", { name: "選んだ2セルを意味づけ" }));

    const relationTable = within(view.container.querySelector(".relation-table")!);
    expect(relationTable.getByText("母牛名")).toBeInTheDocument();
    expect(relationTable.getByText(inputOption.value)).toBeInTheDocument();
    expect(relationTable.getByText("牛名")).toBeInTheDocument();
    expect(editor.getByText("保存していないレイアウト変更があります。")).toBeInTheDocument();
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
    fireEvent.click(
      within(view.container.querySelector(".direct-meaning-editor")!).getByRole(
        "button",
        { name: "選択した保存先をこの矩形に設定" },
      ),
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
        allowUnchangedSave
      />,
    );
    const editor = within(view.container);
    const saveButton = editor.getByRole("button", {
      name: "レイアウトと意味づけをDBへ保存",
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
      expect(editor.getByText("テンプレートへ1件の意味づけを保存しました。")).toBeInTheDocument();
    });
  });
});
