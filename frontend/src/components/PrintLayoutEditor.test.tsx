import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { nearestBreak, printDefaults, type PrintPlan, type PrintRequest, type PrintState } from "../printLayout";
import { PrintLayoutEditor } from "./PrintLayoutEditor";

vi.mock("../api", () => ({ api: {
  printLayout: vi.fn(), previewPrintLayout: vi.fn(), savePrintLayout: vi.fn(), clearPrintLayout: vi.fn(),
  referenceQualification: vi.fn(), qualifyReference: vi.fn(),
} }));

const state: PrintState = {
  revision: "a".repeat(64), editable: true, saved: null,
  source: {
    sheetName: "帳票", startRow: 2, endRow: 6, width: 200, height: 100,
    rowBoundaries: [2, 3, 4, 5, 6, 7].map((row, i) => ({ row, y: i * 20 })),
    validBreakRows: [2, 3, 5], cells: [], unsupported: [], warnings: [],
    importedSettings: { pageSetup: null, margins: null, printOptions: null },
  },
};
function planned(body: PrintRequest): PrintPlan {
  const breaks = body.breakAfterRows ?? [3];
  return { ...body, commonScale: .96, warnings: [], ocrReady: false,
    pages: [...breaks, 6].map((endRow, i) => ({ page: i + 1,
      startRow: i ? breaks[i - 1] + 1 : 2, endRow, range: `B${i ? breaks[i - 1] + 1 : 2}:D${endRow}`,
      width: 1240, height: 1754, transform: { scale: 2, offsetX: 20, offsetY: 20, originX: 0, originY: 0 },
      cells: [], fields: [],
    })),
  };
}
const dirty = vi.fn();
const busy = vi.fn();
const saved = vi.fn();
afterEach(cleanup);
async function open() {
  render(<PrintLayoutEditor versionId="v1" disabled={false} onDirtyChange={dirty} onBusyChange={busy} onSavedChange={saved} />);
  fireEvent.click(screen.getByRole("button", { name: "印刷ページを編集" }));
  await screen.findByText("2ページ / 共通縮小率 96.0%");
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.referenceQualification).mockResolvedValue({ status: "UNVERIFIED", qualified: false });
  vi.mocked(api.printLayout).mockResolvedValue(structuredClone(state));
  vi.mocked(api.previewPrintLayout).mockImplementation(async (_id, body) => planned(body));
});

describe("印刷ページ編集", () => {
  it("zooms a long continuous sheet without changing page settings", async () => {
    vi.mocked(api.printLayout).mockResolvedValue({ ...state, source: { ...state.source, height: 4200 } });
    await open();
    const sheet = screen.getByLabelText("連続シートの改ページ");
    expect(sheet).toHaveStyle({ minWidth: "960px" });
    const calls = vi.mocked(api.previewPrintLayout).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "拡大" }));
    expect(sheet).toHaveStyle({ width: "125%", minWidth: "1200px" });
    expect(screen.getByLabelText("表示倍率")).toHaveTextContent("125%");
    fireEvent.click(screen.getByRole("button", { name: "倍率をリセット" }));
    expect(sheet).toHaveStyle({ width: "100%" });
    expect(api.previewPrintLayout).toHaveBeenCalledTimes(calls);
    expect(dirty).toHaveBeenLastCalledWith(false);
  });
  it("adds an explicit valid row and reloads the saved boundaries", async () => {
    await open();
    const choices = screen.getByLabelText("追加する境界（行の後）");
    expect(choices.querySelector('option[value="4"]')).toBeNull();
    fireEvent.change(choices, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "改ページを追加" }));
    await screen.findByLabelText("Page 2 最終行");
    expect(screen.getByText("未保存")).toBeVisible();
    await screen.findByText("3ページ / 共通縮小率 96.0%");
    let persisted: PrintState = state;
    vi.mocked(api.savePrintLayout).mockImplementation(async (_id, body) => {
      expect(body.breakAfterRows).toEqual([3, 5]);
      expect(body.baseRevision).toBe(state.revision);
      persisted = { ...state, revision: "b".repeat(64), saved: planned(body) };
      return persisted;
    });
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを下書き保存" }));
    await screen.findByText("保存済み・3ページ");
    vi.mocked(api.printLayout).mockImplementation(async () => persisted);
    fireEvent.click(screen.getByRole("button", { name: "保存済み設定を再取得" }));
    await waitFor(() => expect(api.printLayout).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("Page 2 最終行")).toHaveValue("5");
  });
  it("drags in displayed coordinates after zoom and snaps around merged rows", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "拡大" }));
    const sheet = screen.getByLabelText("連続シートの改ページ");
    Object.defineProperty(sheet, "setPointerCapture", { value: vi.fn() });
    vi.spyOn(sheet, "getBoundingClientRect").mockReturnValue({ top: -50, height: 250 } as DOMRect);
    fireEvent.pointerDown(screen.getByLabelText("改ページ 1 を移動"), { pointerId: 1 });
    const event = new Event("pointermove", { bubbles: true });
    Object.defineProperty(event, "clientY", { value: 145 }); // logical y=78 -> row 5
    fireEvent(sheet, event);
    fireEvent.pointerUp(sheet);
    await waitFor(() => expect(screen.getByLabelText("Page 1 最終行")).toHaveValue("5"));
    await waitFor(() => expect(api.previewPrintLayout).toHaveBeenLastCalledWith("v1", expect.objectContaining({ breakAfterRows: [5] })));
  });
  it("explains read-only versions and exhausted legal boundaries", async () => {
    vi.mocked(api.printLayout).mockResolvedValue({ ...state, editable: false, source: { ...state.source, validBreakRows: [] } });
    await open();
    expect(screen.getByText("この版は閲覧専用です。編集には下書き版が必要です。")).toBeVisible();
    expect(screen.getByText("結合セルを分断せずに追加できる行境界がありません。")).toBeVisible();
    expect(screen.getByRole("button", { name: "改ページを追加" })).toBeDisabled();
  });
  it("snaps only to legal rows, excluding occupied boundaries", () => {
    expect(nearestBreak(60, state.source, [])).toBe(3); // row 4 is a merge interior
    expect(nearestBreak(40, state.source, [3])).toBe(2);
    expect(nearestBreak(40, state.source, [2, 3, 5])).toBeNull();
  });
  it("keeps saved page ranges when a target count is edited", async () => {
    const existing = planned({ ...printDefaults(state), breakAfterRows: [2, 3] });
    vi.mocked(api.printLayout).mockResolvedValue({ ...state, saved: existing });
    render(<PrintLayoutEditor versionId="v1" disabled={false} onDirtyChange={dirty} onBusyChange={busy} onSavedChange={saved} />);
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを編集" }));
    await screen.findByText("3ページ / 共通縮小率 96.0%");
    fireEvent.change(screen.getByLabelText("目標の縦ページ数"), { target: { value: "2" } });
    await waitFor(() => expect(api.previewPrintLayout).toHaveBeenLastCalledWith("v1", expect.objectContaining({
      breakAfterRows: [2, 3], targetPagesTall: 2, autoArrange: false,
    })));
    fireEvent.click(screen.getByRole("button", { name: "自動配置" }));
    await waitFor(() => expect(api.previewPrintLayout).toHaveBeenLastCalledWith("v1", expect.objectContaining({
      breakAfterRows: null, targetPagesTall: 2, autoArrange: true,
    })));
  });
  it("preserves edits on save conflict and allows explicit cancellation", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("Page 1 最終行"), { target: { value: "5" } });
    await waitFor(() => expect(api.previewPrintLayout).toHaveBeenLastCalledWith("v1", expect.objectContaining({ breakAfterRows: [5] })));
    vi.mocked(api.savePrintLayout).mockRejectedValue(new Error("競合しました"));
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを下書き保存" }));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Page 1 最終行")).toHaveValue("5");
    expect(dirty).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "変更を取消" }));
    await waitFor(() => expect(screen.getByLabelText("Page 1 最終行")).toHaveValue("3"));
    expect(dirty).toHaveBeenLastCalledWith(false);
  });
  it("adds and deletes boundaries and saves a server revision", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "改ページを追加" }));
    await screen.findByLabelText("Page 2 最終行");
    fireEvent.click(screen.getByRole("button", { name: "改ページ 2 を削除" }));
    await waitFor(() => expect(screen.queryByLabelText("Page 2 最終行")).toBeNull());
    vi.mocked(api.savePrintLayout).mockImplementation(async (_id, body) => ({ ...state, revision: "b".repeat(64), saved: planned(body) }));
    await screen.findByText("2ページ / 共通縮小率 96.0%");
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを下書き保存" }));
    await screen.findByRole("button", { name: "印刷ページ設定を解除して旧方式を使う" });
    expect(screen.getByRole("button", { name: "印刷用XLSX" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Page 1 最終行"), { target: { value: "5" } });
    expect(screen.getByRole("button", { name: "印刷用XLSX" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "印刷用PDF" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "変更を取消" }));
    expect(saved).toHaveBeenLastCalledWith(true);
    expect(dirty).toHaveBeenLastCalledWith(false);
    vi.mocked(api.clearPrintLayout).mockResolvedValue({ ...state, revision: "c".repeat(64) });
    fireEvent.click(screen.getByRole("button", { name: "印刷ページ設定を解除して旧方式を使う" }));
    await waitFor(() => expect(api.clearPrintLayout).toHaveBeenCalledWith("v1", "b".repeat(64)));
    await waitFor(() => expect(screen.queryByRole("button", { name: "印刷用XLSX" })).toBeNull());
  });
  it("disables editing for unsupported source features", async () => {
    vi.mocked(api.printLayout).mockResolvedValue({ ...state, source: { ...state.source, unsupported: ["非表示行は未対応です。"] } });
    render(<PrintLayoutEditor versionId="v1" disabled={false} onDirtyChange={dirty} onBusyChange={busy} onSavedChange={saved} />);
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを編集" }));
    await screen.findByText("非表示行は未対応です。");
    expect(screen.getByRole("button", { name: "改ページを追加" })).toBeDisabled();
    expect(api.previewPrintLayout).not.toHaveBeenCalled();
  });
});
