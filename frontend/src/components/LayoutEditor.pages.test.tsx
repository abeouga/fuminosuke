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

test("paged authoring does not offer unsupported symbol OCR", () => {
  render(<LayoutEditor source="data:image/png;base64," initialLayout={initial}
    pageView={first} workflowStep="classify" targets={targets} onDetect={vi.fn()} onSave={vi.fn()} />);
  clickAt(canvas(), 200, 60);
  expect(screen.getByRole("button", { name: "この欄に書かれた記号を判定する" })).toBeDisabled();
  expect(screen.getByText("記号の判定は単一ページのテンプレートで利用できます。")).toBeInTheDocument();
});

test("page display is keyed by physical ID and never mutates stored cells", () => {
  const before = structuredClone(initial);
  expect(pageDisplayCells(initial, second).map(cell => cell.id)).toEqual(["xlsx_C32"]);
  expect(pageDisplayCells(initial, second)[0]).toMatchObject({ x: 120, y: 40, width: 180 });
  expect(initial).toEqual(before);
});

test("a mark-options focus request selects and highlights only a mark visible on the current page", async () => {
  const markLayout: LayoutDefinition = {
    ...initial,
    cells: initial.cells.map((cell) => cell.id === "xlsx_C32" ? {
      ...cell,
      role: "mark" as const,
      markSets: [{ id: "choice", targetPath: "customFields.choice", recognitionMode: "manual" as const,
        selectionMode: "single" as const, options: [] }],
    } : cell),
  };
  const save = vi.fn(async (next: LayoutDefinition) => next);
  const onFocusRequestResult = vi.fn();
  render(<LayoutEditor source="legacy.png" initialLayout={markLayout} targets={targets}
    onDetect={vi.fn()} onSave={save} workflowStep="meaning" pageView={second}
    focusRequest={{ requestId: 41, cellId: "xlsx_C32", markSetId: "choice", purpose: "mark-options" }}
    onFocusRequestResult={onFocusRequestResult} />);

  await waitFor(() => expect(onFocusRequestResult).toHaveBeenCalledWith({ requestId: 41, focused: true }));
  expect(screen.getByLabelText("セル一覧から選択")).toHaveValue("xlsx_C32");
  expect(screen.getByLabelText("対象セルの選択肢エディタ")).toHaveFocus();
  expect(document.querySelector('.layout-warning-cell-highlight[data-cell-id="xlsx_C32"]')).not.toBeNull();
  fireEvent.change(screen.getByLabelText("セル一覧から選択"), { target: { value: "" } });
  await waitFor(() => expect(document.querySelector(".layout-warning-cell-highlight")).toBeNull());
  expect(save).not.toHaveBeenCalled();
});

test.each([
  ["wrong page", second, first, "xlsx_C32", "choice"],
  ["missing cell", second, second, "missing", "choice"],
  ["missing mark set", second, second, "xlsx_C32", "unknown"],
] as const)("rejects a mark-options focus request for %s", async (_state, sourcePage, displayedPage, cellId, markSetId) => {
  const markLayout: LayoutDefinition = {
    ...initial,
    cells: initial.cells.map((cell) => cell.id === "xlsx_C32" ? {
      ...cell,
      role: "mark" as const,
      markSets: [{ id: "choice", targetPath: "customFields.choice", recognitionMode: "manual" as const,
        selectionMode: "single" as const, options: [] }],
    } : cell),
  };
  expect(sourcePage.fields.some((field) => field.cellId === "xlsx_C32")).toBe(true);
  const onFocusRequestResult = vi.fn();
  render(<LayoutEditor source="legacy.png" initialLayout={markLayout} targets={targets}
    onDetect={vi.fn()} onSave={vi.fn(async (next: LayoutDefinition) => next)} workflowStep="meaning"
    pageView={displayedPage} focusRequest={{ requestId: 42, cellId, markSetId, purpose: "mark-options" }}
    onFocusRequestResult={onFocusRequestResult} />);
  await waitFor(() => expect(onFocusRequestResult).toHaveBeenCalledWith({ requestId: 42, focused: false }));
  expect(screen.getByLabelText("セル一覧から選択")).toHaveValue("");
  expect(document.querySelector(".layout-warning-cell-highlight")).toBeNull();
});

test("page virtual headings are added, grouped, moved and restored only on their saved page", async () => {
  const ref = createRef<LayoutEditorHandle>();
  const save = vi.fn<(layout: LayoutDefinition) => Promise<LayoutDefinition>>()
    .mockRejectedValueOnce(new Error("revision conflict")).mockImplementation(async layout => layout);
  const props = { ref, source: "legacy.png", initialLayout: initial, targets, onDetect: vi.fn(),
    onSave: save, workflowStep: "classify" as const };
  const view = render(<LayoutEditor {...props} pageView={second} />);
  const element = canvas();
  fireEvent.click(screen.getByRole("button", { name: "仮想見出し" }));
  expect(fireEvent.pointerDown(element, { clientX: 5, clientY: 5, pointerId: 1 })).toBe(false);
  fireEvent.pointerUp(element, { clientX: 5, clientY: 5, pointerId: 1 });
  const inlineEditor = screen.getByRole("textbox", { name: "仮想見出し名" });
  expect(inlineEditor).toHaveFocus();
  expect(screen.queryByRole("button", { name: "見出しを配置" })).not.toBeInTheDocument();
  fireEvent.change(inlineEditor, { target: { value: "第2ページの分類" } });
  fireEvent.keyDown(inlineEditor, { key: "Enter" });
  // Canonical C32 is at y=1200, outside this page-local enclosure.
  fireEvent.pointerDown(element, { clientX: 10, clientY: 10, pointerId: 1 });
  fireEvent.pointerMove(element, { clientX: 310, clientY: 100, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: 310, clientY: 100, pointerId: 1 });
  expect(screen.getByText("1個の項目を仮想見出しへ接続しました。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "矩形を移動" }));
  expect(screen.queryByText(/矩形を動かせるモード/)).not.toBeInTheDocument();
  // Physical cells cannot move even when the hand is enabled for a virtual heading.
  fireEvent.pointerDown(element, { clientX: 180, clientY: 65, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: 250, clientY: 120, pointerId: 1 });
  fireEvent.pointerDown(element, { clientX: 10, clientY: 10, pointerId: 1 });
  fireEvent.pointerMove(element, { clientX: 390, clientY: 195, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: 390, clientY: 195, pointerId: 1 });
  expect(await flushEditor(ref)).toBe(false);
  expect(await flushEditor(ref)).toBe(true);
  expect(save.mock.calls[0][0]).toEqual(save.mock.calls[1][0]);
  const saved = save.mock.calls.at(-1)![0];
  const heading = saved.cells.find(cell => cell.virtual)!;
  expect(heading).toMatchObject({ role: "parent_label", text: "第2ページの分類", pageRange: second.range,
    x: 260, y: 168, width: 140, height: 32 });
  expect(saved.cells[2]).toEqual({ ...normalizeLayout(initial).cells[2], parentLabelCellId: heading.id });
  expect(saved.cells.slice(0, 2)).toEqual(normalizeLayout(initial).cells.slice(0, 2));
  expect(pageDisplayCells(saved, first).some(cell => cell.id === heading.id)).toBe(false);
  expect(pageDisplayCells(saved, second).find(cell => cell.id === heading.id)).toEqual(heading);
  expect(pageDisplayCells(saved).some(cell => cell.id === heading.id)).toBe(false);
  view.unmount();
  render(<LayoutEditor {...props} initialLayout={saved} pageView={second} />);
  clickAt(canvas(), 270, 180);
  fireEvent.click(screen.getByRole("button", { name: "矩形を完全に削除" }));
  expect(await flushEditor(ref)).toBe(true);
  expect(save.mock.calls.at(-1)![0].cells.some(cell => cell.id === heading.id)).toBe(false);
  expect(save.mock.calls.at(-1)![0].cells[2].parentLabelCellId).toBeNull();
});

test("changing pages cancels a pending inline virtual heading and unfinished enclosure", () => {
  const props = { source: "legacy.png", initialLayout: initial, targets, onDetect: vi.fn(),
    onSave: vi.fn(async (layout: LayoutDefinition) => layout), workflowStep: "classify" as const };
  const view = render(<LayoutEditor {...props} pageView={first} />);
  fireEvent.click(screen.getByRole("button", { name: "仮想見出し" }));
  clickAt(canvas(), 5, 5);
  expect(screen.getByRole("textbox", { name: "仮想見出し名" })).toBeInTheDocument();
  view.rerender(<LayoutEditor {...props} pageView={second} />);
  expect(screen.queryByRole("textbox", { name: "仮想見出し名" })).not.toBeInTheDocument();
  clickAt(canvas(), 5, 5);
  const inlineEditor = screen.getByRole("textbox", { name: "仮想見出し名" });
  fireEvent.change(inlineEditor, { target: { value: "分類" } });
  fireEvent.keyDown(inlineEditor, { key: "Enter" });
  expect(screen.getByRole("button", { name: "囲い込みをキャンセル" })).toBeInTheDocument();
  view.rerender(<LayoutEditor {...props} pageView={first} />);
  expect(screen.queryByRole("button", { name: "囲い込みをキャンセル" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "矩形を移動" })).toBeDisabled();
});

test("clicking a saved virtual heading renames it inline and clicking outside commits the same identity", async () => {
  const ref = createRef<LayoutEditorHandle>();
  const save = vi.fn(async (layout: LayoutDefinition) => layout);
  const layout: LayoutDefinition = {
    ...initial,
    cells: [
      ...initial.cells.map(cell => cell.id === "xlsx_C32"
        ? { ...cell, parentLabelCellId: "virtual_saved" }
        : cell),
      { id: "virtual_saved", role: "parent_label", virtual: true, pageRange: second.range,
        source: "manual", text: "旧分類", x: 5, y: 5, width: 140, height: 32,
        confidence: 1, textConfidence: null },
    ],
  };
  render(<LayoutEditor ref={ref} source="legacy.png" initialLayout={layout} targets={targets}
    onDetect={vi.fn()} onSave={save} workflowStep="classify" pageView={second} />);
  const element = canvas();
  fireEvent.click(screen.getByRole("button", { name: "仮想見出し" }));
  expect(fireEvent.pointerDown(element, { clientX: 10, clientY: 10, pointerId: 1 })).toBe(false);
  fireEvent.pointerUp(element, { clientX: 10, clientY: 10, pointerId: 1 });
  const inlineEditor = screen.getByRole("textbox", { name: "仮想見出し名" });
  expect(inlineEditor).toHaveValue("旧分類");
  expect(inlineEditor).toHaveClass("editing-existing");
  expect(inlineEditor).not.toHaveClass("creating-new");
  expect(screen.queryByText("項目名の文字候補")).not.toBeInTheDocument();
  fireEvent.change(inlineEditor, { target: { value: "新分類" } });
  clickAt(element, 350, 150);
  expect(screen.queryByRole("textbox", { name: "仮想見出し名" })).not.toBeInTheDocument();
  expect(await flushEditor(ref)).toBe(true);
  const saved = save.mock.calls.at(-1)![0];
  expect(saved.cells.find(cell => cell.id === "virtual_saved")).toMatchObject({
    text: "新分類", pageRange: second.range, x: 5, y: 5, width: 140, height: 32,
  });
  expect(saved.cells).toHaveLength(layout.cells.length);
  expect(saved.cells.find(cell => cell.id === "xlsx_C32")?.parentLabelCellId).toBe("virtual_saved");
});

test.each(["meaning", "records"] as const)("%s connects page virtual headings to headings and the whole repeated band", async workflowStep => {
  const ref = createRef<LayoutEditorHandle>();
  const save = vi.fn(async (layout: LayoutDefinition) => layout);
  const layout: LayoutDefinition = { ...initial,
    cells: [...initial.cells.map(cell => cell.id === "xlsx_C2" ? { ...cell, role: "label" as const, text: "結果" } : cell),
      { id: "outer", role: "parent_label", virtual: true, pageRange: first.range, source: "manual",
        text: "全体", x: 5, y: 2, width: 30, height: 25, confidence: 1, textConfidence: null },
      { id: "inner", role: "parent_label", virtual: true, pageRange: first.range, source: "manual",
        text: "検査一覧", x: 5, y: 120, width: 100, height: 30, confidence: 1, textConfidence: null }],
    recordSections: [{ id: "band", title: "検査一覧", collectionPath: "genericRecords.tests", recordType: "generic",
      recordLabelCellIds: ["xlsx_B2", "xlsx_C2"], parentLabelCellId: null, rowKeyStrategy: "position", source: "manual" }],
  };
  render(<LayoutEditor ref={ref} source="legacy.png" initialLayout={layout} targets={targets}
    onDetect={vi.fn()} onSave={save} workflowStep={workflowStep} pageView={first} />);
  const element = canvas();
  fireEvent.click(screen.getByRole("button", { name: "親項目を接続" }));
  fireEvent.pointerDown(element, { clientX: 10, clientY: 10, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: 20, clientY: 130, pointerId: 1 });
  if (workflowStep === "records") fireEvent.click(screen.getByRole("button", { name: "見出しへ接続" }));
  fireEvent.pointerDown(element, { clientX: 20, clientY: 130, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: 180, clientY: 65, pointerId: 1 });
  expect(await flushEditor(ref)).toBe(true);
  const saved = save.mock.calls.at(-1)![0];
  expect(saved.cells.find(cell => cell.id === "inner")).toMatchObject({ parentLabelCellId: "outer", pageRange: first.range });
  expect(saved.recordSections![0].parentLabelCellId).toBe("inner");
  expect(saved.cells.slice(0, 3)).toEqual(normalizeLayout(layout).cells.slice(0, 3));
  expect(saved.relations).toEqual(layout.relations);
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
