import { act, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createRef } from "react";
import { LayoutEditor, type LayoutEditorHandle } from "./LayoutEditor";
import type { LayoutDefinition } from "../types";

const initial: LayoutDefinition = {
  canonicalWidth: 300, canonicalHeight: 100, detector: "test", ocrAvailable: false,
  cells: [{ id: "cell", role: "input", x: 10, y: 10, width: 80, height: 30,
    confidence: 1, text: "", textConfidence: null, source: "manual" }],
  relations: [], allowCrossAxisLabels: false, warnings: [],
};
const key = "test-background-layout-save";

beforeEach(() => { vi.useFakeTimers(); window.localStorage.removeItem(key); });
afterEach(() => { vi.useRealTimers(); window.localStorage.removeItem(key); });

function setup() {
  const pending: Array<{ layout: LayoutDefinition; resolve: (value: LayoutDefinition) => void;
    reject: (reason: Error) => void }> = [];
  const onSave = vi.fn((layout: LayoutDefinition) => new Promise<LayoutDefinition>((resolve, reject) => {
    pending.push({ layout, resolve, reject });
  }));
  const onBusyChange = vi.fn();
  const onSavingChange = vi.fn();
  const ref = createRef<LayoutEditorHandle>();
  const props = { source: "data:image/png;base64,", initialLayout: initial, targets: [],
    ref, workflowStep: "classify" as const, onDetect: vi.fn(), onSave, onBusyChange, onSavingChange, draftStorageKey: key };
  const view = render(<LayoutEditor {...props} />);
  const ui = within(view.container);
  fireEvent.change(ui.getByLabelText("セル一覧から選択"), { target: { value: "cell" } });
  return { pending, onSave, onBusyChange, props, view, ui };
}
async function settleInput() {
  await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
}

test("keeps editing and the newest browser draft while serializing slow saves", async () => {
  const { pending, onSave, onBusyChange, props, view, ui } = setup();
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  await settleInput();
  expect(pending).toHaveLength(1);
  expect(onBusyChange).toHaveBeenLastCalledWith(false);
  expect(props.onSavingChange).toHaveBeenLastCalledWith(true);
  expect(view.container.querySelector("fieldset")).not.toBeDisabled();
  expect(ui.getByRole("button", { name: "枠を再検出" })).toBeDisabled();
  fireEvent.click(ui.getByRole("button", { name: "見出し" }));
  await settleInput();
  expect(onSave).toHaveBeenCalledTimes(1);
  const newer = window.localStorage.getItem(key);
  expect(JSON.parse(newer!).cells[0].role).toBe("parent_label");
  // The parent receives the older API response before its mapping refresh ends.
  view.rerender(<LayoutEditor {...props} initialLayout={pending[0].layout} />);
  await act(async () => pending[0].resolve(pending[0].layout));
  expect(window.localStorage.getItem(key)).toBe(newer);
  await settleInput();
  expect(pending).toHaveLength(2);
  expect(pending[1].layout.cells[0].role).toBe("parent_label");
  await act(async () => pending[1].resolve(pending[1].layout));
  expect(window.localStorage.getItem(key)).toBeNull();
  expect(onBusyChange).toHaveBeenLastCalledWith(false);
  expect(props.onSavingChange).toHaveBeenLastCalledWith(false);
  expect(ui.queryByText(/編集中の内容を自動保存しました/)).not.toBeInTheDocument();
  view.unmount();
});

test("a save response does not clear the meaning chosen for the next connection", async () => {
  const { pending, props, view, ui } = setup();
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  await settleInput();
  view.rerender(<LayoutEditor {...props} workflowStep="meaning"
    targets={[{ path: "customFields.arbitrary", label: "任意の項目", section: "独立項目" }]} />);
  fireEvent.click(ui.getByRole("button", { name: "意味付け" }));
  fireEvent.change(ui.getByLabelText("保存する情報"), { target: { value: "customFields.arbitrary" } });
  expect(ui.getByLabelText("保存する情報")).toHaveValue("customFields.arbitrary");
  await act(async () => pending[0].resolve(structuredClone(pending[0].layout)));
  expect(ui.getByLabelText("保存する情報")).toHaveValue("customFields.arbitrary");
  view.unmount();
});

test("navigation flush stays editable and waits for edits made during an existing save", async () => {
  const { pending, props, view, ui } = setup();
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  await settleInput();
  let finished: boolean | undefined;
  let flush!: Promise<boolean>;
  act(() => { flush = props.ref.current!.saveIfNeeded(); void flush.then(value => { finished = value; }); });
  fireEvent.click(ui.getByRole("button", { name: "見出し" }));
  await act(async () => pending[0].resolve(pending[0].layout));
  expect(finished).toBeUndefined();
  expect(pending).toHaveLength(2);
  expect(view.container.querySelector("fieldset")).not.toBeDisabled();
  expect(pending[1].layout.cells[0].role).toBe("parent_label");
  await act(async () => { pending[1].resolve(pending[1].layout); });
  await flush;
  expect(finished).toBe(true);
  expect(window.localStorage.getItem(key)).toBeNull();
  view.unmount();
});

test("persists a return to the original value made during an in-flight save", async () => {
  const { pending, view, ui } = setup();
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  await settleInput();
  fireEvent.click(ui.getByRole("button", { name: "記入欄" }));
  expect(JSON.parse(window.localStorage.getItem(key)!).cells[0].role).toBe("input");
  await act(async () => pending[0].resolve(pending[0].layout));
  await settleInput();
  expect(pending[1].layout.cells[0].role).toBe("input");
  await act(async () => pending[1].resolve(pending[1].layout));
  expect(window.localStorage.getItem(key)).toBeNull();
  view.unmount();
});

test("an explicit authoring flush does not lock editing and saves subsequent changes", async () => {
  const { pending, props, view, ui } = setup();
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  let flush!: Promise<boolean>;
  act(() => { flush = props.ref.current!.saveIfNeeded(); });
  expect(pending).toHaveLength(1);
  expect(view.container.querySelector("fieldset")).not.toBeDisabled();
  expect(props.onBusyChange).toHaveBeenLastCalledWith(false);
  fireEvent.click(ui.getByRole("button", { name: "見出し" }));
  await act(async () => pending[0].resolve(pending[0].layout));
  expect(pending).toHaveLength(2);
  await act(async () => pending[1].resolve(pending[1].layout));
  expect(await flush).toBe(true);
  view.unmount();
});

test("a rectangle drag spanning a save response remains active and persists its drop", async () => {
  vi.stubGlobal("PointerEvent", MouseEvent);
  const { pending, view, ui } = setup();
  try {
    const canvas = ui.getByLabelText("帳票レイアウト編集キャンバス");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0,
      right: 300, bottom: 100, width: 300, height: 100, toJSON: () => ({}) });
    fireEvent.click(ui.getByRole("button", { name: "見出し" }));
    await settleInput();
    fireEvent.click(ui.getByRole("button", { name: "矩形を移動" }));
    fireEvent.pointerDown(canvas, { clientX: 20, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 50, clientY: 40, pointerId: 1 });
    await act(async () => pending[0].resolve(pending[0].layout));
    fireEvent.pointerUp(canvas, { clientX: 50, clientY: 40, pointerId: 1 });
    expect(JSON.parse(window.localStorage.getItem(key)!).cells[0]).toMatchObject({ id: "cell", x: 40, y: 30 });
    await settleInput();
    expect(pending[1].layout.cells[0]).toMatchObject({ id: "cell", x: 40, y: 30, role: "parent_label" });
    await act(async () => pending[1].resolve(pending[1].layout));
    expect(window.localStorage.getItem(key)).toBeNull();
  } finally {
    view.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks();
  }
});

test("a failed background save cancels a pending navigation flush without retrying it", async () => {
  const { pending, props, view, ui } = setup();
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  await settleInput();
  let flush!: Promise<boolean>;
  act(() => { flush = props.ref.current!.saveIfNeeded(); });
  await act(async () => pending[0].reject(new Error("保存競合")));
  expect(await flush).toBe(false);
  await settleInput();
  expect(pending).toHaveLength(1);
  expect(window.localStorage.getItem(key)).not.toBeNull();
  expect(view.container.querySelector("fieldset")).not.toBeDisabled();
  // A deliberate retry is still available.
  act(() => { flush = props.ref.current!.saveIfNeeded(); });
  expect(pending).toHaveLength(2);
  await act(async () => pending[1].resolve(pending[1].layout));
  expect(await flush).toBe(true);
  view.unmount();
});

test("retains edits on rejection, does not spin retries, and restores them on reopening", async () => {
  const { pending, onSave, props, view, ui } = setup();
  const statusSlot = view.container.querySelector(".layout-status-slot");
  expect(statusSlot).toBeInTheDocument();
  expect(statusSlot).toBeEmptyDOMElement();
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  await settleInput();
  await act(async () => pending[0].reject(new Error("保存競合: 再確認してください")));
  await settleInput();
  expect(onSave).toHaveBeenCalledTimes(1);
  expect(ui.getByText("保存競合: 再確認してください")).toBeInTheDocument();
  expect(view.container.querySelector(".layout-status-slot")).toBe(statusSlot);
  expect(view.container.querySelector("fieldset")).not.toBeDisabled();
  expect(JSON.parse(window.localStorage.getItem(key)!).cells[0].role).toBe("ignored");
  view.unmount();
  const reopened = render(<LayoutEditor {...props} />);
  expect(window.localStorage.getItem(key)).not.toBeNull();
  await settleInput();
  expect(pending[1].layout.cells[0].role).toBe("ignored");
  await act(async () => pending[1].resolve(pending[1].layout));
  expect(window.localStorage.getItem(key)).toBeNull();
  reopened.unmount();
});

test("never resurrects a rectangle deleted during persistence", async () => {
  const { pending, view, ui } = setup();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  await settleInput();
  fireEvent.click(ui.getByRole("button", { name: "矩形を完全に削除" }));
  await act(async () => pending[0].resolve(pending[0].layout));
  expect(JSON.parse(window.localStorage.getItem(key)!).cells).toEqual([]);
  await settleInput();
  expect(pending[1].layout.cells).toEqual([]);
  await act(async () => pending[1].resolve(pending[1].layout));
  expect(window.localStorage.getItem(key)).toBeNull();
  view.unmount();
  vi.restoreAllMocks();
});

test("retains a reverted draft when an error leaves server persistence uncertain", async () => {
  const { pending, view, ui } = setup();
  fireEvent.click(ui.getByRole("button", { name: "読み取らない" }));
  await settleInput();
  fireEvent.click(ui.getByRole("button", { name: "記入欄" }));
  await act(async () => pending[0].reject(new Error("保存結果を取得できません")));
  expect(JSON.parse(window.localStorage.getItem(key)!).cells[0].role).toBe("input");
  await settleInput();
  expect(pending[1].layout.cells[0].role).toBe("input");
  await act(async () => pending[1].resolve(pending[1].layout));
  view.unmount();
});
