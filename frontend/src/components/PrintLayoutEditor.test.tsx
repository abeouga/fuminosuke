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
    expect(saved).toHaveBeenLastCalledWith(true);
    expect(dirty).toHaveBeenLastCalledWith(false);
    vi.mocked(api.clearPrintLayout).mockResolvedValue({ ...state, revision: "c".repeat(64) });
    fireEvent.click(screen.getByRole("button", { name: "印刷ページ設定を解除して旧方式を使う" }));
    await waitFor(() => expect(api.clearPrintLayout).toHaveBeenCalledWith("v1", "b".repeat(64)));
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
