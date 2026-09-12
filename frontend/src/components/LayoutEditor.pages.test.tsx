import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { pageDisplayCells, type PageView } from "../pageAuthoring";
import type { LayoutDefinition } from "../types";
import { LayoutEditor, normalizeLayout, type LayoutEditorHandle } from "./LayoutEditor";

async function flushEditor(ref: { current: LayoutEditorHandle | null }) {
  let pending!: Promise<boolean>;
  // The next request uses props/revision from the committed response.
  await act(async () => { pending = ref.current!.saveIfNeeded(); });
  return pending;
}

const initial: LayoutDefinition = {
  canonicalWidth: 1000, canonicalHeight: 2000, detector: "test", ocrAvailable: false,
  warnings: [], relations: [], allowCrossAxisLabels: false,
  cells: [
    { id: "xlsx_B2", role: "label", x: 400, y: 600, width: 40, height: 20,
      text: "氏名", confidence: 1, textConfidence: 1, source: "manual" },
    { id: "xlsx_C2", role: "input", x: 440, y: 600, width: 60, height: 20,
      text: "", confidence: 1, textConfidence: null, source: "manual" },
    { id: "xlsx_C32", role: "input", x: 440, y: 1200, width: 60, height: 20,
      text: "", confidence: 1, textConfidence: null, source: "manual" },
  ],
};
const first: PageView = {
  page: 1, range: "A1:D31", startRow: 1, endRow: 31, width: 400, height: 200, cells: [],
  imageUrl: "data:image/png;base64,", transform: { scale: 2, offsetX: 0, offsetY: 0, originX: 0, originY: 0 },
  fields: [
    { cellId: "xlsx_B2", sourceRange: "B2", templatePage: 1, rectangle: { x: 20, y: 40, width: 100, height: 50 } },
    { cellId: "xlsx_C2", sourceRange: "C2", templatePage: 1, rectangle: { x: 120, y: 40, width: 180, height: 50 } },
  ],
};
const second: PageView = { ...first, page: 2, range: "A32:D63", startRow: 32, endRow: 63,
  fields: [{ cellId: "xlsx_C32", sourceRange: "C32", templatePage: 2, rectangle: { x: 120, y: 40, width: 180, height: 50 } }],
};
beforeEach(() => vi.stubGlobal("PointerEvent", MouseEvent));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function canvas() {
  const element = screen.getByLabelText("帳票レイアウト編集キャンバス");
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 400, bottom: 200, width: 400, height: 200, toJSON: () => ({}),
  });
  return element;
}
function clickAt(element: HTMLElement, x: number, y: number) {
  fireEvent.pointerDown(element, { clientX: x, clientY: y, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: x, clientY: y, pointerId: 1 });
}
const targets = [{ path: "document.unifiedEarTag", label: "識別番号", section: "基本" }];

test("page display is keyed by physical ID and never mutates stored cells", () => {
  const before = structuredClone(initial);
  expect(pageDisplayCells(initial, second).map(cell => cell.id)).toEqual(["xlsx_C32"]);
  expect(pageDisplayCells(initial, second)[0]).toMatchObject({ x: 120, y: 40, width: 180 });
  expect(initial).toEqual(before);
});

test("spreadsheet authoring preserves detected headings on untouched pages", async () => {
  const ref = createRef<LayoutEditorHandle>();
  const raw = { ...initial, detector: "xlsx-structure", cells: initial.cells.map((cell, i) => (
    i === 0 ? { ...cell, role: "parent_label" as const, source: "detected" as const } : cell
  )) };
  expect(normalizeLayout(raw).cells[0].role).toBe("label"); // legacy repair fixture
  const save = vi.fn(async (layout: LayoutDefinition) => layout);
  const props = { ref, source: "legacy.png", initialLayout: raw, targets, onDetect: vi.fn(),
    onSave: save, workflowStep: "classify" as const, preserveDetectedRoles: true };
  const view = render(<LayoutEditor {...props} />); // page data arrives after layout
  view.rerender(<LayoutEditor {...props} pageView={second} />);
  clickAt(canvas(), 180, 65);
  fireEvent.click(screen.getByRole("button", { name: "読み取らない" }));
  expect(await flushEditor(ref)).toBe(true);
  expect(save.mock.calls[0][0].cells[0].role).toBe("parent_label");
  expect(save.mock.calls[0][0].cells[0].source).toBe("detected");
});

test("page-local classification and switching preserve canonical coordinates and other pages", async () => {
  const ref = createRef<LayoutEditorHandle>();
  const save = vi.fn(async (layout: LayoutDefinition) => layout);
  const props = { ref, source: "legacy.png", initialLayout: initial, targets, onDetect: vi.fn(), onSave: save, workflowStep: "classify" as const };
  const view = render(<LayoutEditor {...props} pageView={first} />);
  expect(canvas()).toHaveAttribute("width", "400");
  expect(screen.queryByRole("button", { name: "枠を追加" })).not.toBeInTheDocument();
  clickAt(canvas(), 180, 65);
  fireEvent.click(screen.getByRole("button", { name: "読み取らない" }));
  view.rerender(<LayoutEditor {...props} pageView={second} />);
  clickAt(canvas(), 180, 65);
  fireEvent.click(screen.getByRole("button", { name: "項目名" }));
  expect(await flushEditor(ref)).toBe(true);
  const saved = save.mock.calls.at(-1)![0];
  expect(saved.cells.map(cell => cell.role)).toEqual(["label", "ignored", "label"]);
  saved.cells.forEach((cell, i) => {
    const original = initial.cells[i];
    expect([cell.id, cell.x, cell.y, cell.width, cell.height])
      .toEqual([original.id, original.x, original.y, original.width, original.height]);
  });
});

test("page-local range selection deletes selected rectangles and preserves other pages", async () => {
  const ref = createRef<LayoutEditorHandle>();
  const save = vi.fn(async (layout: LayoutDefinition) => layout);
  const props = { ref, source: "legacy.png", initialLayout: initial, targets, onDetect: vi.fn(),
    onSave: save, workflowStep: "classify" as const };
  const view = render(<LayoutEditor {...props} pageView={first} />);
  const element = canvas();
  fireEvent.pointerDown(element, { clientX: 5, clientY: 20, pointerId: 1 });
  fireEvent.pointerMove(element, { clientX: 310, clientY: 100, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: 310, clientY: 100, pointerId: 1 });

  expect(screen.getByText("範囲選択: 2 セル")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "選択セルを結合" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "選択した矩形を完全に削除" }));
  view.rerender(<LayoutEditor {...props} pageView={second} />);
  clickAt(canvas(), 180, 65);
  expect(screen.getByRole("button", { name: "矩形を完全に削除" })).toBeEnabled();

  expect(await flushEditor(ref)).toBe(true);
  expect(save.mock.calls.at(-1)![0].cells).toEqual(normalizeLayout(initial).cells.slice(2));
});

test("page-local meaning drag connects only the selected page's existing IDs", async () => {
  const ref = createRef<LayoutEditorHandle>();
  const save = vi.fn(async (layout: LayoutDefinition) => layout);
  render(<LayoutEditor ref={ref} source="legacy.png" initialLayout={initial} targets={targets}
    onDetect={vi.fn()} onSave={save} workflowStep="meaning" pageView={first} />);
  const element = canvas();
  fireEvent.click(screen.getByRole("button", { name: "意味付け" }));
  fireEvent.change(screen.getByLabelText("保存する情報"), { target: { value: "document.unifiedEarTag" } });
  fireEvent.pointerDown(element, { clientX: 60, clientY: 65, pointerId: 1 });
  fireEvent.pointerMove(element, { clientX: 230, clientY: 65, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: 230, clientY: 65, pointerId: 1 });
  expect(await flushEditor(ref)).toBe(true);
  const saved = save.mock.calls.at(-1)![0];
  expect(saved.relations).toHaveLength(1);
  expect(saved.relations[0]).toMatchObject({ inputCellId: "xlsx_C2", labelCellIds: ["xlsx_B2"],
    targetPath: "document.unifiedEarTag", axis: "horizontal" });
  expect(saved.cells).toEqual(normalizeLayout(initial).cells);
});

test("failed page save retains human edits for retry", async () => {
  const ref = createRef<LayoutEditorHandle>();
  const save = vi.fn<(layout: LayoutDefinition) => Promise<LayoutDefinition>>()
    .mockRejectedValueOnce(new Error("revision conflict"))
    .mockImplementation(async layout => layout);
  render(<LayoutEditor ref={ref} source="legacy.png" initialLayout={initial} targets={targets}
    onDetect={vi.fn()} onSave={save} workflowStep="classify" pageView={second} />);
  clickAt(canvas(), 180, 65);
  fireEvent.click(screen.getByRole("button", { name: "読み取らない" }));
  expect(await flushEditor(ref)).toBe(false);
  await waitFor(() => expect(screen.getByRole("button", { name: "読み取らない" })).toHaveClass("active"));
  expect(await flushEditor(ref)).toBe(true);
  expect(save.mock.calls[0][0]).toEqual(save.mock.calls[1][0]);
});

test("page merge remains visible across switching, failed save, reload and unmerge", async () => {
  const ref = createRef<LayoutEditorHandle>();
  const save = vi.fn<(layout: LayoutDefinition) => Promise<LayoutDefinition>>()
    .mockRejectedValueOnce(new Error("revision conflict"))
    .mockImplementation(async layout => layout);
  const props = { ref, source: "legacy.png", initialLayout: initial, targets,
    onDetect: vi.fn(), onSave: save, workflowStep: "classify" as const };
  const view = render(<LayoutEditor {...props} pageView={first} />);
  const element = canvas();
  clickAt(element, 60, 65);
  fireEvent.pointerDown(element, { clientX: 180, clientY: 65, pointerId: 1, ctrlKey: true });
  fireEvent.pointerUp(element, { clientX: 180, clientY: 65, pointerId: 1, ctrlKey: true });
  expect(screen.queryByText(/セルを複数選択しました/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "選択セルを結合" }));
  expect(screen.getByRole("button", { name: "結合を解除" })).toBeEnabled();
  view.rerender(<LayoutEditor {...props} pageView={second} />);
  expect(screen.queryByRole("button", { name: "結合を解除" })).not.toBeInTheDocument();
  view.rerender(<LayoutEditor {...props} pageView={first} />);
  clickAt(canvas(), 60, 65);
  expect(screen.getByRole("button", { name: "結合を解除" })).toBeEnabled();
  expect(await flushEditor(ref)).toBe(false);
  expect(await flushEditor(ref)).toBe(true);
  expect(save.mock.calls[0][0]).toEqual(save.mock.calls[1][0]);
  const saved = save.mock.calls[1][0];
  const parent = saved.cells.find(cell => cell.id.startsWith("merged_"))!;
  expect(parent).toMatchObject({ x: 400, y: 600, width: 100, height: 20 });
  expect(saved.cellMerges![0].parts.map(part => part.cell)).toEqual(normalizeLayout(initial).cells.slice(0, 2));
  expect(pageDisplayCells(saved, first)).toEqual([
    { ...parent, x: 20, y: 40, width: 280, height: 50 },
  ]);
  view.unmount();
  const savedPage: PageView = { ...first,
    fields: [{ cellId: parent.id, sourceRange: "B2:C2", templatePage: 1,
      rectangle: { x: 20, y: 40, width: 280, height: 50 },
      sourceParts: first.fields.map(field => ({ cellId: field.cellId, sourceRange: field.sourceRange })) }],
    cells: first.fields.map(field => ({ sourceRange: field.sourceRange, startRow: 2, endRow: 2,
      ...field.rectangle, text: "", fill: "#ffffff", fontSize: 12, bold: false, align: "left",
      borders: {}, rectangle: field.rectangle })),
  };
  render(<LayoutEditor {...props} initialLayout={saved} pageView={savedPage} />);
  clickAt(canvas(), 60, 65);
  fireEvent.click(screen.getByRole("button", { name: "結合を解除" }));
  expect(await flushEditor(ref)).toBe(true);
  const restored = save.mock.calls.at(-1)![0];
  expect(restored.cells).toEqual(normalizeLayout(initial).cells);
  expect(pageDisplayCells(restored, savedPage)).toEqual(pageDisplayCells(normalizeLayout(initial), first));
});
