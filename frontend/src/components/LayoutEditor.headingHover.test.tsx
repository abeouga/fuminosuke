import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { LayoutDefinition } from "../types";
import type { PageView } from "../pageAuthoring";
import { LayoutEditor } from "./LayoutEditor";
import { createRef } from "react";
import type { LayoutEditorHandle } from "./LayoutEditor";

const initial: LayoutDefinition = { canonicalWidth: 400, canonicalHeight: 300, relations: [], warnings: [],
  detector: "test", ocrAvailable: false, allowCrossAxisLabels: true, cells: [
  { id: "h", role: "parent_label", virtual: true, source: "manual", text: "見出し",
    x: 10, y: 10, width: 40, height: 30, confidence: 1, textConfidence: null },
  ...[0, 1].map(i => ({ id: `child${i}`, role: "label" as const, parentLabelCellId: "h", source: "manual" as const,
    text: `項目${i}`, x: 200 + i * 60, y: 120, width: 40, height: 30, confidence: 1, textConfidence: null })),
] };
let curves = vi.fn();
let strokes = vi.fn();
let imageDraws = vi.fn();
beforeEach(() => {
  vi.stubGlobal("PointerEvent", MouseEvent);
  vi.stubGlobal("Image", class {
    onload: (() => void) | null = null;
    set src(_value: string) { this.onload?.(); }
  });
  curves = vi.fn();
  strokes = vi.fn();
  imageDraws = vi.fn();
  const context = new Proxy({ bezierCurveTo: curves, strokeRect: strokes, drawImage: imageDraws,
    measureText: () => ({ width: 20 }) }, {
    get: (target, property) => Reflect.get(target, property) ?? vi.fn(),
    set: (target, property, value) => Reflect.set(target, property, value),
  });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function canvas() {
  const element = screen.getByLabelText("帳票レイアウト編集キャンバス");
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0,
    right: 400, bottom: 300, width: 400, height: 300, toJSON: () => ({}) });
  return element;
}

test("a captured hand-drawn draft accepts its AI proposal but rejects later drawing", () => {
  const ref = createRef<LayoutEditorHandle>(), onSave = vi.fn();
  render(<LayoutEditor ref={ref} source="preview.png" initialLayout={initial} targets={[]}
    onSave={onSave} onDetect={vi.fn()} workflowStep="classify" operationEndSave />);
  const element = canvas();
  const draw = (x: number) => {
    fireEvent.click(screen.getByRole("button", { name: "枠を追加" }));
    fireEvent.pointerDown(element, { clientX: x, clientY: 220, button: 0, buttons: 1 });
    fireEvent.pointerUp(element, { clientX: x + 40, clientY: 250, button: 0, buttons: 0 });
  };
  draw(20);
  const input = ref.current!.readAnalysisDraft()!;
  const manual = input.cells.find(c => c.id.startsWith("manual_"))!;
  expect(manual).toMatchObject({ x: 20, y: 220, width: 40, height: 30, source: "manual" });
  const merged = { ...input, cells: input.cells.map(c => c.id === "child0" ? { ...c, text: "AI案" } : c) };
  act(() => { expect(ref.current!.applyAnalysisDraft(input, merged)).toBe(true); });
  expect(ref.current!.readAnalysisDraft()!.cells.find(c => c.id === manual.id)).toMatchObject(manual);
  act(() => { expect(ref.current!.applyAnalysisDraft(merged, input, "undo")).toBe(true); });
  draw(100);
  act(() => { expect(ref.current!.applyAnalysisDraft(input, merged)).toBe(false); });
  expect(ref.current!.readAnalysisDraft()!.cells).toHaveLength(initial.cells.length + 2);
  expect(onSave).not.toHaveBeenCalled();
});

test("a separate editor cannot automatically save a restored unconfirmed AI draft", async () => {
  const key = "phase6-unconfirmed-ai", ref = createRef<LayoutEditorHandle>();
  const props = { source: "preview.png", initialLayout: initial, targets: [], onSave: vi.fn(),
    onDetect: vi.fn(), operationEndSave: true, draftStorageKey: key };
  localStorage.removeItem(key); localStorage.removeItem(`${key}:baseline`);
  const view = render(<LayoutEditor {...props} ref={ref} />);
  const input = ref.current!.readAnalysisDraft()!;
  const merged = { ...input, cells: input.cells.map(c => c.id === "child0" ? { ...c, text: "AIの変更" } : c) };
  act(() => { expect(ref.current!.applyAnalysisDraft(input, merged)).toBe(true); });
  view.unmount();
  const restored = createRef<LayoutEditorHandle>();
  render(<LayoutEditor {...props} ref={restored} />);
  expect(screen.getByRole("region", { name: "復元した下書きの確認" })).toBeInTheDocument();
  await expect(restored.current!.saveIfNeeded()).resolves.toBe(false);
  expect(props.onSave).not.toHaveBeenCalled();
  localStorage.removeItem(key); localStorage.removeItem(`${key}:baseline`);
});

test("classification preserves detected geometry provenance and journals same-value intent", () => {
  const onManualFacets = vi.fn(), ref = createRef<LayoutEditorHandle>();
  const detected = { ...initial, cells: [{ ...initial.cells[1], source: "detected" as const }] };
  render(<LayoutEditor ref={ref} source="preview.png" initialLayout={detected} targets={[]}
    onSave={vi.fn()} onDetect={vi.fn()} workflowStep="classify" onManualFacets={onManualFacets} />);
  fireEvent.change(screen.getByLabelText("セル一覧から選択"), { target: { value: "child0" } });
  fireEvent.click(screen.getByRole("button", { name: "項目名" }));
  expect(ref.current!.readAnalysisDraft()!.cells[0]).toMatchObject({ role: "label", source: "detected", x: 200, y: 120 });
  expect(onManualFacets.mock.calls.flat(2)).toContainEqual({ target: { draftEntityId: "child0" }, facet: "role" });
});

test("saved voice layout retains an existing selection without inventing a selection change", () => {
  const onAssistantSelectionChange = vi.fn();
  const props = { source: "preview.png", initialLayout: initial, targets: [], onSave: vi.fn(),
    onDetect: vi.fn(), workflowStep: "classify" as const, operationEndSave: true, onAssistantSelectionChange };
  const view = render(<LayoutEditor {...props} />);
  fireEvent.change(screen.getByLabelText("セル一覧から選択"), { target: { value: "child0" } });
  const selected = onAssistantSelectionChange.mock.lastCall![0];
  expect(selected.selectedIds).toEqual(["child0"]);
  const saved = { ...initial, cells: initial.cells.map(cell => cell.id === "child0" ? { ...cell, fieldLabel: "受理番号" } : cell) };
  view.rerender(<LayoutEditor {...props} initialLayout={saved} />);
  expect(onAssistantSelectionChange.mock.lastCall![0]).toMatchObject({ selectedIds: ["child0"], selectionEpoch: selected.selectionEpoch, cell: { fieldLabel: "受理番号" } });
  expect(props.onSave).not.toHaveBeenCalled();
  view.rerender(<LayoutEditor {...props} initialLayout={{ ...saved, cells: saved.cells.filter(cell => cell.id !== "child0") }} />);
  expect(onAssistantSelectionChange.mock.lastCall![0].selectedIds).toEqual([]);
});

test("hides every canvas rectangle while using the auxiliary eraser without changing the layout", () => {
  const onSave = vi.fn(), onDirtyChange = vi.fn();
  const onDetect = vi.fn();
  const view = render(<LayoutEditor source="preview.png" initialLayout={initial} targets={[]} onSave={onSave}
    onDetect={onDetect} onDirtyChange={onDirtyChange} workflowStep="classify" />);
  fireEvent.click(screen.getByText("補助操作"));
  const erase = screen.getByRole("button", { name: "消しゴム" });
  const hide = screen.getByRole("button", { name: "すべての矩形を非表示" });
  const element = canvas();
  expect(erase.closest("details")).toHaveAttribute("open");
  expect(strokes).toHaveBeenCalled();

  fireEvent.click(erase);
  strokes.mockClear();
  imageDraws.mockClear();
  fireEvent.click(hide);
  expect(hide).toHaveAttribute("aria-pressed", "true");
  expect(imageDraws).toHaveBeenCalled();
  expect(strokes).not.toHaveBeenCalled();
  expect(element).toHaveStyle({ pointerEvents: "none" });
  fireEvent.pointerDown(element, { clientX: 25, clientY: 25, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: 25, clientY: 25, pointerId: 1 });
  expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  expect(onSave).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "すべての矩形を表示" }));
  expect(strokes).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "すべての矩形を非表示" }))
    .toHaveAttribute("aria-pressed", "false");

  fireEvent.click(screen.getByRole("button", { name: "すべての矩形を非表示" }));
  strokes.mockClear();
  view.rerender(<LayoutEditor source="preview.png" initialLayout={initial} targets={[]} onSave={onSave}
    onDetect={onDetect} onDirtyChange={onDirtyChange} workflowStep="review" />);
  expect(strokes).toHaveBeenCalled();
  expect(element).toHaveAttribute("tabindex", "0");
});

test.each(["classify", "meaning", "records", "review"] as const)("%s shows only hovered heading connections, not persistent selection", workflowStep => {
  const onSave = vi.fn(), onDirtyChange = vi.fn();
  render(<LayoutEditor source="preview.png" initialLayout={initial} targets={[]} onSave={onSave}
    onDetect={vi.fn()} onDirtyChange={onDirtyChange} workflowStep={workflowStep} />);
  const element = canvas();
  expect(curves).not.toHaveBeenCalled();
  fireEvent.pointerMove(element, { clientX: 20, clientY: 20, buttons: 0 });
  expect(curves).toHaveBeenCalledTimes(2);
  curves.mockClear();
  fireEvent.pointerMove(element, { clientX: 210, clientY: 130, buttons: 0 });
  expect(curves).toHaveBeenCalledTimes(1);
  fireEvent.pointerDown(element, { clientX: 210, clientY: 130 });
  fireEvent.pointerUp(element, { clientX: 210, clientY: 130 });
  curves.mockClear();
  fireEvent.pointerMove(element, { clientX: 380, clientY: 280, buttons: 0 });
  expect(curves).not.toHaveBeenCalled();
  fireEvent.pointerMove(element, { clientX: 20, clientY: 20, buttons: 0 });
  curves.mockClear();
  fireEvent.pointerLeave(element);
  expect(curves).not.toHaveBeenCalled();
  expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  expect(onSave).not.toHaveBeenCalled();
});

test("page switching clears hover and uses page-local coordinates without revealing other pages", () => {
  const layout = { ...initial, cells: initial.cells.map(cell => cell.id === "h" ? { ...cell, pageRange: "A1:D10" } : cell) };
  const page: PageView = { page: 1, range: "A1:D10", startRow: 1, endRow: 10, width: 400, height: 300,
    imageUrl: "page.png", cells: [], transform: { scale: 1, offsetX: 0, offsetY: 0, originX: 0, originY: 0 },
    fields: initial.cells.slice(1).map(cell => ({ cellId: cell.id, sourceRange: "B2", templatePage: 1,
      rectangle: { x: cell.x, y: cell.y, width: cell.width, height: cell.height } })) };
  const props = { source: "preview.png", initialLayout: layout, targets: [], onSave: vi.fn(), onDetect: vi.fn(), workflowStep: "meaning" as const };
  const view = render(<LayoutEditor {...props} pageView={page} />);
  fireEvent.pointerMove(canvas(), { clientX: 20, clientY: 20, buttons: 0 });
  expect(curves).toHaveBeenCalledTimes(2);
  curves.mockClear();
  view.rerender(<LayoutEditor {...props} pageView={{ ...page, page: 2, range: "A11:D20", fields: [] }} />);
  fireEvent.pointerMove(canvas(), { clientX: 20, clientY: 20, buttons: 0 });
  expect(curves).not.toHaveBeenCalled();
  view.rerender(<LayoutEditor {...props} pageView={page} />);
  expect(curves).not.toHaveBeenCalled();
});

test("hovering the repeated band's gap reveals one group connection without changing column ownership", () => {
  const layout: LayoutDefinition = { ...initial, cells: initial.cells.map(cell => ({ ...cell, parentLabelCellId: null })),
    recordSections: [{ id: "band", title: "一覧", recordType: "generic", collectionPath: "genericRecords.test",
      recordLabelCellIds: ["child0", "child1"], parentLabelCellId: "h", rowKeyStrategy: "position", source: "manual" }] };
  const onSave = vi.fn(), onDirtyChange = vi.fn();
  render(<LayoutEditor source="preview.png" initialLayout={layout} targets={[]} onSave={onSave}
    onDetect={vi.fn()} onDirtyChange={onDirtyChange} workflowStep="records" />);
  const element = canvas();
  expect(curves).not.toHaveBeenCalled();
  fireEvent.pointerMove(element, { clientX: 250, clientY: 135, buttons: 0 });
  expect(curves).toHaveBeenCalledTimes(1);
  curves.mockClear();
  fireEvent.pointerLeave(element);
  expect(curves).not.toHaveBeenCalled();
  expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  expect(onSave).not.toHaveBeenCalled();
});
