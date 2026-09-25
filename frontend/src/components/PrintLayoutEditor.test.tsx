import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { nearestBreak, printDefaults, type PrintPlan, type PrintRequest, type PrintState } from "../printLayout";
import { PrintLayoutEditor } from "./PrintLayoutEditor";

vi.mock("../api", () => ({ api: {
  printLayout: vi.fn(), previewPrintLayout: vi.fn(), savePrintLayout: vi.fn(), clearPrintLayout: vi.fn(),
  referenceQualification: vi.fn(), qualifyReference: vi.fn(),
  preparePrintFiles: vi.fn(),
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
  it("publishes the authoritative moved layout only after save succeeds", async () => {
    const onLayoutSaved = vi.fn();
    const initial = { ...state, saved: planned(printDefaults(state)), pageHeadings: [
      { cellId: "virtual-1", text: "名前", pageRange: "B2:D3" },
    ] };
    const layout = { cells: [], relations: [] } as unknown as NonNullable<PrintState["layout"]>;
    vi.mocked(api.printLayout).mockResolvedValue(initial);
    vi.mocked(api.savePrintLayout).mockImplementation(async (_id, body) => ({
      ...initial, revision: "b".repeat(64), saved: planned(body), layout,
      pageHeadings: [{ ...initial.pageHeadings[0], pageRange: "B2:D2" }],
    }));
    render(<PrintLayoutEditor versionId="v1" disabled={false} onDirtyChange={dirty}
      onBusyChange={busy} onSavedChange={saved} onLayoutSaved={onLayoutSaved} />);
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを編集" }));
    await screen.findByText("2ページ / 共通縮小率 96.0%");
    fireEvent.change(screen.getByLabelText("Page 1 最終行"), { target: { value: "2" } });
    await waitFor(() => expect(screen.getByLabelText("仮想見出し 名前 の移動先範囲")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("仮想見出し 名前 の移動先範囲"), { target: { value: "B2:D2" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "印刷ページを下書き保存" })).not.toBeDisabled());
    expect(onLayoutSaved).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを下書き保存" }));
    await waitFor(() => expect(onLayoutSaved).toHaveBeenCalledExactlyOnceWith(layout));
    fireEvent.click(screen.getByText("見出しを載せるページ"));
    expect(screen.getByText("名前（現在: B2:D2）")).toBeVisible();
    expect(screen.getByLabelText("仮想見出し 名前 の移動先範囲")).toHaveValue("");
    await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(false));
    expect(api.preparePrintFiles).not.toHaveBeenCalled();
  });
  it("sends explicit heading bindings and keeps them after a save failure", async () => {
    const initial = { ...state, saved: planned(printDefaults(state)), pageHeadings: [
      { cellId: "virtual-1", text: "名前", pageRange: "B2:D3" },
    ] };
    vi.mocked(api.printLayout).mockResolvedValue(initial);
    vi.mocked(api.savePrintLayout).mockRejectedValue(new Error("保存競合"));
    await open();
    expect(api.savePrintLayout).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Page 1 最終行"), { target: { value: "2" } });
    await waitFor(() => expect(screen.getByLabelText("仮想見出し 名前 の移動先範囲")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("仮想見出し 名前 の移動先範囲"), { target: { value: "B2:D2" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "印刷ページを下書き保存" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを下書き保存" }));
    await screen.findByText("保存競合");
    expect(api.savePrintLayout).toHaveBeenLastCalledWith("v1", expect.objectContaining({
      baseRevision: state.revision, breakAfterRows: [2],
      pageHeadingMoves: [{ cellId: "virtual-1", fromRange: "B2:D3", toRange: "B2:D2" }],
    }));
    expect(screen.getByLabelText("仮想見出し 名前 の移動先範囲")).toHaveValue("B2:D2");
    expect(api.preparePrintFiles).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "変更を取消" }));
    expect(screen.getByLabelText("仮想見出し 名前 の移動先範囲")).toHaveValue("");
  });
  it("reports preparation busy to prevent concurrent activation while allowing preview zoom", async () => {
    const initial = { ...state, saved: planned(printDefaults(state)) };
    vi.mocked(api.printLayout).mockResolvedValue(initial);
    let finish!: (value: PrintState) => void;
    vi.mocked(api.preparePrintFiles).mockReturnValue(new Promise(resolve => { finish = resolve; }));
    await open();
    expect(api.preparePrintFiles).not.toHaveBeenCalled();
    expect(api.previewPrintLayout).not.toHaveBeenCalled();
    busy.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "印刷見本を作る" }));
    expect(api.preparePrintFiles).toHaveBeenCalledWith("v1", state.revision);
    expect(screen.getByRole("button", { name: "印刷見本を作成中…" })).toBeDisabled();
    expect(busy).toHaveBeenCalledWith(true);
    expect(screen.getByRole("button", { name: "拡大" })).not.toBeDisabled();
    finish({ ...initial, revision: "c".repeat(64), saved: { ...initial.saved, commonScale: .95 } });
    await screen.findByText("印刷用ファイルを確定しました（2ページ・95%）。");
    expect(saved).toHaveBeenLastCalledWith(true);
  });
  it("keeps saved settings and supports retry after preparation failure", async () => {
    vi.mocked(api.printLayout).mockResolvedValue({ ...state, saved: planned(printDefaults(state)) });
    vi.mocked(api.preparePrintFiles).mockRejectedValue(new Error("生成中に設定が変更されました"));
    await open();
    await waitFor(() => expect(screen.getByRole("button", { name: "印刷見本を作る" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "印刷見本を作る" }));
    expect(api.preparePrintFiles).toHaveBeenCalledWith("v1", state.revision);
    await screen.findByText("生成中に設定が変更されました");
    expect(screen.getByText("保存済み・2ページ")).toBeVisible();
    expect(screen.getByRole("button", { name: "印刷見本を作る" })).not.toBeDisabled();
    expect(api.savePrintLayout).not.toHaveBeenCalled();
  });
  it("does not replace newer authoring state with a delayed preparation response", async () => {
    const initial = { ...state, saved: planned(printDefaults(state)) };
    vi.mocked(api.printLayout).mockResolvedValue(initial);
    let finish!: (value: PrintState) => void;
    vi.mocked(api.preparePrintFiles).mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const props = { versionId: "v1", disabled: false, onDirtyChange: dirty, onBusyChange: busy, onSavedChange: saved };
    const view = render(<PrintLayoutEditor {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "印刷ページを編集" }));
    await screen.findByText("2ページ / 共通縮小率 96.0%");
    fireEvent.click(screen.getByRole("button", { name: "印刷見本を作る" }));
    const newer = { ...initial, revision: "n".repeat(64), saved: planned({ ...printDefaults(initial), breakAfterRows: [] }) };
    view.rerender(<PrintLayoutEditor {...props} externalState={newer} />);
    await screen.findByText("1ページ / 共通縮小率 96.0%");
    finish({ ...initial, revision: "p".repeat(64) });
    await screen.findByText("印刷用ファイルを生成しました。新しい編集内容は保持しています。保存後に設定を再取得してください。");
    expect(screen.getByText("1ページ / 共通縮小率 96.0%")).toBeVisible();
  });
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
    await screen.findByText("旧方式への切り替え");
    fireEvent.click(screen.getByText("旧方式への切り替え"));
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
