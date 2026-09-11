import { act, fireEvent, render, waitFor, within } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { LayoutCell, LayoutDefinition } from "../types";
import { LayoutEditor, normalizeLayout, type LayoutEditorHandle } from "./LayoutEditor";
import { polygonCell } from "./layoutAuthoring";

beforeEach(() => vi.stubGlobal("PointerEvent", MouseEvent));
afterEach(() => vi.unstubAllGlobals());

const cell = (id: string, x: number, y: number, role: LayoutCell["role"] = "input", text = ""): LayoutCell => ({
  id, role, x, y, width: 40, height: 30, text, confidence: 1, textConfidence: null, source: "manual",
});
const layout = (cells: LayoutCell[]): LayoutDefinition => ({ canonicalWidth: 400, canonicalHeight: 300,
  cells, relations: [], detector: "test", ocrAvailable: false, allowCrossAxisLabels: true, warnings: [] });
function setup(initial: LayoutDefinition, workflowStep: "classify" | "meaning" | "records" = "classify") {
  const onSave = vi.fn(async (current: LayoutDefinition) => JSON.parse(JSON.stringify(current)) as LayoutDefinition);
  const ref = createRef<LayoutEditorHandle>();
  const onDirtyChange = vi.fn();
  const props = { source: "data:image/png;base64,", initialLayout: initial, targets: [],
    onDetect: vi.fn(), onSave, workflowStep, ref, onDirtyChange };
  const view = render(<LayoutEditor {...props} />);
  const editor = within(view.container);
  const canvas = editor.getByLabelText("帳票レイアウト編集キャンバス");
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0,
    right: 400, bottom: 300, width: 400, height: 300, toJSON: () => ({}) });
  const click = (x: number, y: number) => {
    fireEvent.pointerDown(canvas, { clientX: x, clientY: y, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: x, clientY: y, pointerId: 1 });
  };
  const drag = (x: number, y: number, endX: number, endY: number) => {
    fireEvent.pointerDown(canvas, { clientX: x, clientY: y, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: endX, clientY: endY, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: endX, clientY: endY, pointerId: 1 });
  };
  const save = async () => {
    const count = onSave.mock.calls.length;
    await act(async () => { expect(await ref.current!.saveIfNeeded()).toBe(true); });
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(count + 1));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    return onSave.mock.calls.at(-1)![0];
  };
  return { view, editor, canvas, click, drag, save, onSave, props };
}

test("draws polygon by clicks, undo and closing at the start, then saves, reclassifies and deletes it", async () => {
  const ui = setup(layout([]));
  fireEvent.click(ui.editor.getByRole("button", { name: "枠を追加" }));
  expect(ui.editor.getByRole("combobox", { name: "追加する枠の形状" })).toHaveValue("rectangle");
  fireEvent.change(ui.editor.getByRole("combobox", { name: "追加する枠の形状" }), { target: { value: "polygon" } });
  ui.click(20, 20); ui.click(100, 20);
  expect(ui.editor.getByRole("button", { name: "多角形を確定" })).toBeDisabled();
  ui.click(90, 70);
  fireEvent.click(ui.editor.getByRole("button", { name: "最後の頂点を取り消す" }));
  expect(ui.editor.getByRole("button", { name: "多角形を確定" })).toBeDisabled();
  ui.click(20, 100); ui.click(20, 20);
  const saved = await ui.save();
  expect(saved.cells[0].vertices).toEqual([{ x: 20, y: 20 }, { x: 100, y: 20 }, { x: 20, y: 100 }]);
  ui.view.rerender(<LayoutEditor {...ui.props} initialLayout={saved} />);
  ui.click(90, 90);
  expect(ui.editor.queryByRole("button", { name: "矩形を完全に削除" })).toBeNull();
  ui.click(30, 30);
  fireEvent.click(ui.editor.getByRole("button", { name: "項目名" }));
  const classified = await ui.save();
  expect(classified.cells[0].role).toBe("label");
  expect(classified.cells[0].vertices).toEqual(saved.cells[0].vertices);
  ui.click(30, 30);
  fireEvent.click(ui.editor.getByRole("button", { name: "矩形を完全に削除" }));
  expect((await ui.save()).cells).toEqual([]);
});

test("cancels unfinished polygons without adding any cells", () => {
  const ui = setup(layout([]));
  fireEvent.click(ui.editor.getByRole("button", { name: "枠を追加" }));
  fireEvent.change(ui.editor.getByRole("combobox", { name: "追加する枠の形状" }), { target: { value: "polygon" } });
  ui.click(20, 20); ui.click(100, 20);
  fireEvent.click(ui.editor.getByRole("button", { name: "作図をキャンセル" }));
  expect(ui.props.onDirtyChange).toHaveBeenLastCalledWith(false);
});

test.each(["漢字", "住所", "電話番号", "備考", "氏名", "年月日", "担当者", "所在地", "施設・事業所名"])("links identical %s labels to separate inputs and resumes numbering after reload", async (name) => {
  const key = name.replace(/・/g, "");
  const initial = layout([0, 1, 2, 3].flatMap(index => [
    cell(`label${index}`, 20, 20 + 60 * index, "label", name),
    index === 3 ? polygonCell([{ x: 100, y: 200 }, { x: 140, y: 200 }, { x: 100, y: 230 }], "input3")!
      : cell(`input${index}`, 100, 20 + 60 * index),
  ]));
  const ui = setup(initial, "meaning");
  fireEvent.click(ui.editor.getByRole("button", { name: "意味付け" }));
  for (let index = 0; index < 3; index++) ui.drag(40, 35 + 60 * index, 115, 30 + 60 * index);
  const saved = await ui.save();
  expect(saved.relations.map(r => r.targetPath)).toEqual([`customFields.${key}`, `customFields.${key}2`, `customFields.${key}3`]);
  ui.view.rerender(<LayoutEditor {...ui.props} initialLayout={saved} />);
  fireEvent.click(ui.editor.getByRole("button", { name: "意味付け" }));
  ui.drag(40, 215, 105, 210);
  const reloaded = await ui.save();
  expect(reloaded.relations.at(-1)?.targetPath).toBe(`customFields.${key}4`);
  expect(reloaded.cells.filter(cell => cell.role === "label").every(cell => cell.text === name)).toBe(true);
  expect(reloaded.relations.slice(0, 3).map(r => r.id)).toEqual(saved.relations.map(r => r.id));
});

test.each(["備考", "住所", "電話番号"])("links repeated %s across multiple inputs and resumes after reload", async (name) => {
  const initial = layout([0, 1, 2].flatMap(index => [
    cell(`label${index}`, 20, 20 + 100 * index, "label", name),
    cell(`input${index}a`, 100, 20 + 100 * index),
    cell(`input${index}b`, 180, 20 + 100 * index),
  ]));
  const ui = setup(initial, "meaning");
  fireEvent.click(ui.editor.getByRole("button", { name: "意味付け" }));
  ui.drag(40, 35, 200, 35);
  const first = await ui.save();
  fireEvent.click(ui.editor.getByRole("button", { name: "意味付け" }));
  ui.drag(40, 135, 200, 135);
  const saved = await ui.save();
  expect(saved.relations).toHaveLength(4);
  expect(new Set(saved.relations.map(r => r.targetPath)).size).toBe(4);
  expect(saved.relations.slice(0, 2)).toEqual(first.relations);
  expect(saved.labelPool?.map(entry => entry.label)).toEqual([
    `${name} 1行目`, `${name} 2行目`, `${name}2 1行目`, `${name}2 2行目`,
  ]);
  ui.view.rerender(<LayoutEditor {...ui.props} initialLayout={saved} />);
  fireEvent.click(ui.editor.getByRole("button", { name: "意味付け" }));
  ui.drag(40, 235, 200, 235);
  const reloaded = await ui.save();
  expect(reloaded.relations).toHaveLength(6);
  expect(new Set(reloaded.relations.map(r => r.targetPath)).size).toBe(6);
  expect(reloaded.relations.slice(0, 4)).toEqual(saved.relations);
  expect(reloaded.labelPool?.slice(-2).map(entry => entry.label)).toEqual([`${name}3 1行目`, `${name}3 2行目`]);
});

test("creates a virtual heading and groups mapped merged and mark cells, with a normal parent", async () => {
  const initial = layout([cell("normal", 250, 10, "parent_label", "日付情報"),
    cell("label", 20, 80, "label", "年月日"), cell("merged", 100, 80), cell("mark", 180, 80, "mark"),
    cell("excludedHeading", 260, 80, "parent_label", "別見出し")]);
  initial.cellMerges = [{ id: "merge", parentCellId: "merged", direction: "horizontal",
    parts: [cell("year", 100, 80), cell("month", 120, 80)].map(cell => ({ cell, partType: "input", literal: "" })) }];
  initial.labelPool = [{ id: "date", label: "年月日", targetPath: "customFields.date", section: "test", aliases: [], source: "manual" }];
  initial.relations = [{ id: "date-relation", inputCellId: "merged", labelCellIds: ["label"], targetPath: "customFields.date", axis: "horizontal", order: 1 }];
  const ui = setup(initial, "records");
  fireEvent.click(ui.editor.getByRole("button", { name: "仮想見出し" }));
  ui.click(5, 10);
  fireEvent.change(ui.editor.getByRole("textbox", { name: "仮想見出しの名称" }), { target: { value: "記入日" } });
  fireEvent.click(ui.editor.getByRole("button", { name: "見出しを配置" }));
  ui.drag(10, 20, 320, 120);
  fireEvent.click(ui.editor.getByRole("button", { name: "親項目を接続" }));
  ui.drag(270, 25, 20, 25);
  const saved = await ui.save();
  const virtual = saved.cells.find(cell => cell.virtual)!;
  expect(virtual).toMatchObject({ role: "parent_label", text: "記入日", parentLabelCellId: "normal" });
  expect(saved.cells.filter(cell => ["label", "merged", "mark"].includes(cell.id)).every(cell => cell.parentLabelCellId === virtual.id)).toBe(true);
  expect(saved.cells.find(cell => cell.id === "excludedHeading")?.parentLabelCellId).toBeFalsy();
  expect(saved.cellMerges).toEqual(normalizeLayout(initial).cellMerges);
  expect(saved.relations[0]).toEqual({ ...initial.relations[0], parentLabelCellId: virtual.id });
  ui.view.rerender(<LayoutEditor {...ui.props} initialLayout={saved} />);
  fireEvent.click(ui.editor.getByRole("button", { name: "親項目を接続" }));
  ui.drag(20, 25, 270, 25);
  expect(ui.editor.getByText(/見出しの自己参照・循環参照/)).toBeInTheDocument();
  expect(ui.props.onDirtyChange).toHaveBeenLastCalledWith(false);
});

test("eraser and undo keep cell and direct-meaning parent edges consistent", async () => {
  const initial = layout([cell("parent", 20, 20, "parent_label", "見出し"),
    { ...cell("input", 180, 100), parentLabelCellId: "parent" }]);
  initial.relations = [{ id: "direct", inputCellId: "input", labelCellIds: [],
    parentLabelCellId: "parent", targetPath: "customFields.name", axis: "horizontal", order: 1 }];
  const ui = setup(initial, "meaning");
  fireEvent.click(ui.editor.getByRole("button", { name: "消しゴム" }));
  ui.drag(120, 65, 120, 85);
  fireEvent.click(ui.editor.getByRole("button", { name: /意味づけを戻す/ }));
  expect(ui.props.onDirtyChange).toHaveBeenLastCalledWith(false);
  ui.drag(120, 65, 120, 85);
  const saved = await ui.save();
  expect(saved.cells[1].parentLabelCellId).toBeNull();
  expect(saved.relations).toEqual([{ ...initial.relations[0], parentLabelCellId: null }]);
});
