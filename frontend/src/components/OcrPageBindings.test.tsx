import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { OcrPageBindings, type PageCatalog } from "./OcrPageBindings";

vi.mock("../api", () => ({ api: { getOcrPages: vi.fn(), createOcrPageJob: vi.fn() },
  createIdempotencyKey: () => "test-key" }));
const catalog: PageCatalog = { templateVersionId: "v1", templateRevision: "a".repeat(64),
  available: false, reason: "PRINT_REFERENCE_NOT_VALIDATED", pages: [
    { pageId: "p1", pageNumber: 1, range: "A1:D30", referenceUrl: "/p1.png" },
    { pageId: "p2", pageNumber: 2, range: "A31:D60", referenceUrl: "/p2.png" },
  ] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getOcrPages).mockResolvedValue(catalog);
  vi.stubGlobal("URL", { createObjectURL: () => "blob:test", revokeObjectURL: vi.fn() });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("shows explicit page ranges and blocks unqualified reference even with all inputs", async () => {
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 2 · A31:D60");
  expect(screen.getAllByText("入力画像が未指定です")).toHaveLength(2);
  for (const number of [2, 1]) {
    fireEvent.change(screen.getByLabelText(`Page ${number} の画像を選択`, { exact: false }), {
      target: { files: [new File(["image"], `${number}.png`, { type: "image/png" })] },
    });
  }
  expect(screen.getAllByText("入力画像を指定済み")).toHaveLength(2);
  expect(screen.getByRole("button", { name: "全ページを読み取る" })).toBeDisabled();
  expect(api.createOcrPageJob).not.toHaveBeenCalled();
});

it("does not start a qualified fixture when page 2 is missing", async () => {
  vi.mocked(api.getOcrPages).mockResolvedValue({ ...catalog, available: true });
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  await screen.findByText("Page 1 · A1:D30");
  fireEvent.change(screen.getByLabelText("Page 1 の画像を選択", { exact: false }), {
    target: { files: [new File(["image"], "1.png", { type: "image/png" })] },
  });
  expect(screen.getByRole("button", { name: "全ページを読み取る" })).toBeDisabled();
});

it("reports catalog failure without offering a legacy page 1 fallback", async () => {
  vi.mocked(api.getOcrPages).mockRejectedValue(new Error("ページ構成の取得失敗"));
  render(<OcrPageBindings versionId="v1" onAccepted={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("ページ構成の取得失敗");
  expect(screen.queryByRole("button", { name: "全ページを読み取る" })).not.toBeInTheDocument();
});
