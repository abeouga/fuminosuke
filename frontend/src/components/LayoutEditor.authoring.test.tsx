import { act, fireEvent, render, waitFor, within } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { LayoutCell, LayoutDefinition } from "../types";
import { createAutoLabelPoolEntry, LayoutEditor, normalizeLayout, type LayoutEditorHandle } from "./LayoutEditor";
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
    let pending!: Promise<boolean>;
    // Let React commit each save response before awaiting the flush boundary.
    await act(async () => { pending = ref.current!.saveIfNeeded(); });
    expect(await pending).toBe(true);
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(count + 1));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    return onSave.mock.calls.at(-1)![0];
  };
  return { view, editor, canvas, click, drag, save, onSave, props };
}

test.each([
  ["氏名", "氏名（カナ）"], ["番号備考", "番号"], ["記入日", "記 入日"],
  ["A", "Ａ"], ["Field", "field"], ["歴", "暦"],
])("manual addition keeps %s and %s separate", async (existingName, newName) => {
  const initial = layout([cell("input", 100, 20)]);
  initial.labelPool = [{ id: "original", label: existingName, targetPath: "customFields.original",
    aliases: [newName], section: "test", source: "manual" }];
  const ui = setup(initial, "meaning");
  fireEvent.click(ui.editor.getByRole("button", { name: "一覧にない項目を追加" }));
  fireEvent.change(ui.editor.getByRole("textbox", { name: "新しい項目名" }), { target: { value: newName } });
  fireEvent.click(ui.editor.getByRole("button", { name: "この項目を追加" }));
  const saved = await ui.save();
  expect(saved.labelPool).toHaveLength(2);
  expect(saved.labelPool![0]).toEqual(initial.labelPool[0]);
  expect(saved.labelPool![1].label).toBe(newName);
  ui.view.rerender(<LayoutEditor {...ui.props} initialLayout={saved} />);
  fireEvent.click(ui.editor.getByRole("button", { name: "一覧にない項目を追加" }));
  fireEvent.change(ui.editor.getByRole("textbox", { name: "新しい項目名" }), { target: { value: newName } });
  fireEvent.click(ui.editor.getByRole("button", { name: "この項目を追加" }));
  expect(ui.editor.getByText(`「${newName}」は保存する項目に登録済みです。`)).toBeInTheDocument();
  expect(ui.props.onDirtyChange).toHaveBeenLastCalledWith(false);
});

test("exact manual names allocate distinct paths even when sanitized keys collide", () => {
  const entry = createAutoLabelPoolEntry("記 入日", [
    { path: "customFields.記入日", label: "記入日", section: "test", aliases: ["記 入日"] },
  ], "exact");
  expect(entry).toMatchObject({ label: "記 入日", targetPath: "customFields.記入日_2" });
});

test("manually editing a printed label also adds a similar name without absorbing it", async () => {
  const initial = layout([cell("label", 20, 20, "label", "氏名")]);
  initial.labelPool = [{ id: "name", label: "氏名", targetPath: "customFields.name", aliases: [], section: "test", source: "manual" }];
  const ui = setup(initial);
  ui.click(30, 30);
  const input = ui.editor.getByRole("textbox", { name: "項目名の文字候補" });
  fireEvent.change(input, { target: { value: "氏名（カナ）" } });
  fireEvent.blur(input);
  const saved = await ui.save();
  expect(saved.labelPool?.map(entry => entry.label)).toEqual(["氏名", "氏名（カナ）"]);
});

test("hand mode alone moves a polygon and virtual heading, preserving meaning through save/reload", async () => {
  const initial = layout([polygonCell([{ x: 20, y: 20 }, { x: 60, y: 20 }, { x: 20, y: 50 }], "polygon")!,
    { ...cell("heading", 200, 20, "parent_label", "仮想"), virtual: true }]);
  initial.relations = [{ id: "meaning", inputCellId: "polygon", labelCellIds: [],
    targetPath: "customFields.test", axis: "horizontal", order: 1 }];
  const ui = setup(initial);
  ui.drag(25, 25, 85, 75);
  expect(ui.props.onDirtyChange).toHaveBeenLastCalledWith(false);
  const hand = ui.editor.getByRole("button", { name: "矩形を移動" });
  expect(hand).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(hand);
  expect(hand).toHaveAttribute("aria-pressed", "true");
  expect(ui.editor.queryByText(/矩形を動かせるモード|ドラッグアンドドロップしてください/)).toBeNull();
  ui.drag(25, 25, 85, 75);
  ui.drag(210, 30, 250, 110);
  const saved = await ui.save();
  expect(saved.cells[0]).toMatchObject({ id: "polygon", x: 80, y: 70,
    vertices: [{ x: 80, y: 70 }, { x: 120, y: 70 }, { x: 80, y: 100 }] });
  expect(saved.cells[1]).toMatchObject({ id: "heading", x: 240, y: 100, virtual: true });
  expect(saved.relations).toEqual(initial.relations);
  ui.view.rerender(<LayoutEditor {...ui.props} initialLayout={saved} />);
  fireEvent.click(hand);
  ui.drag(85, 75, 125, 105);
  expect(ui.props.onDirtyChange).toHaveBeenLastCalledWith(false);
});

test("move cancellation does not dirty the draft and dropping clamps to canvas bounds", async () => {
  const initial = layout([cell("rect", 20, 20)]);
  const ui = setup(initial);
  fireEvent.click(ui.editor.getByRole("button", { name: "矩形を移動" }));
  fireEvent.pointerDown(ui.canvas, { clientX: 25, clientY: 25 });
  fireEvent.pointerMove(ui.canvas, { clientX: 100, clientY: 100 });
  fireEvent.pointerCancel(ui.canvas);
  expect(ui.props.onDirtyChange).toHaveBeenLastCalledWith(false);
  fireEvent.pointerDown(ui.canvas, { clientX: 25, clientY: 25 });
  fireEvent.pointerMove(ui.canvas, { clientX: 100, clientY: 100 });
  fireEvent.keyDown(ui.canvas, { key: "Escape" });
  fireEvent.pointerUp(ui.canvas, { clientX: 100, clientY: 100 });
  expect(ui.props.onDirtyChange).toHaveBeenLastCalledWith(false);
  fireEvent.click(ui.editor.getByRole("button", { name: "矩形を移動" }));
  ui.drag(25, 25, 800, 800);
  expect((await ui.save()).cells[0]).toMatchObject({ id: "rect", x: 360, y: 270, width: 40, height: 30 });
});

test("diagnosis: changing a label to heading removes its link but retains the input destination", async () => {
  const initial = layout([cell("label", 20, 20, "label", "検査名"), cell("input", 100, 20)]);
  initial.relations = [{ id: "meaning", inputCellId: "input", labelCellIds: ["label"],
    targetPath: "customFields.test", axis: "horizontal", order: 1 }];
  const ui = setup(initial);
  ui.click(30, 30);
  fireEvent.click(ui.editor.getByRole("button", { name: "見出し" }));
  const saved = await ui.save();
  expect(saved.cells[0].role).toBe("parent_label");
  expect(saved.relations).toEqual([{ ...initial.relations[0], labelCellIds: [] }]);
});

test.each(["meaning", "records"] as const)("%s connects a heading to the entire repeated band and nests headings", async step => {
  const initial = layout([cell("outer", 20, 10, "parent_label", "全体"),
    cell("heading", 100, 60, "parent_label", "検査一覧"),
    cell("a", 100, 150, "label", "日付"), cell("b", 200, 150, "label", "結果")]);
  initial.recordSections = [{ id: "section", collectionPath: "genericRecords.tests", recordType: "generic",
    title: "検査", parentLabelCellId: null, recordLabelCellIds: ["a", "b"], rowKeyStrategy: "position", source: "manual" }];
  const ui = setup(initial, step);
  fireEvent.click(ui.editor.getByRole("button", { name: "親項目を接続" }));
  ui.drag(30, 20, 110, 70);
  if (step === "records") fireEvent.click(ui.editor.getByRole("button", { name: "見出しへ接続" }));
  ui.drag(110, 70, 210, 160);
  const saved = await ui.save();
  expect(saved.cells[1].parentLabelCellId).toBe("outer");
  expect(saved.recordSections![0]).toEqual({ ...normalizeLayout(initial).recordSections![0], parentLabelCellId: "heading" });
  expect(saved.cells.slice(2)).toEqual(normalizeLayout(initial).cells.slice(2));
  expect(saved.relations).toEqual(initial.relations);
  ui.view.rerender(<LayoutEditor {...ui.props} initialLayout={saved} />);
  fireEvent.click(ui.editor.getByRole("button", { name: "親項目を接続" }));
  ui.drag(110, 160, 30, 20);
  expect(ui.editor.getByText(/循環参照/)).toBeInTheDocument();
});

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
