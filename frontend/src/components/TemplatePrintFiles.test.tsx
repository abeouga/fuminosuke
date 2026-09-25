import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { TemplatePrintFiles } from "./TemplatePrintFiles";

vi.mock("../api", () => ({ api: {
  printLayout: vi.fn(), downloadTemplatePrint: vi.fn(), downloadLegacyTemplatePrint: vi.fn(),
} }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => "blob:print");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

it.each(["xlsx", "pdf"] as const)("downloads saved version and revision as %s", async format => {
  vi.mocked(api.downloadTemplatePrint).mockResolvedValue(new Blob(["fixture"]));
  render(<TemplatePrintFiles versionId="v1" revision="r1" />);
  fireEvent.click(screen.getByRole("button", { name: `印刷用${format.toUpperCase()}` }));
  await waitFor(() => expect(api.downloadTemplatePrint).toHaveBeenCalledWith("v1", "r1", format));
  await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce());
  expect(screen.getByRole("status")).toHaveTextContent(`印刷用${format.toUpperCase()}を取得`);
  expect(api.printLayout).not.toHaveBeenCalled();
});

it.each(["xlsx", "pdf"] as const)("downloads a legacy single-page blank %s from the same version", async format => {
  vi.mocked(api.downloadLegacyTemplatePrint).mockResolvedValue(new Blob(["blank fixture"]));
  render(<TemplatePrintFiles versionId="legacy-v1" legacy />);
  fireEvent.click(screen.getByRole("button", { name: `印刷用${format.toUpperCase()}` }));
  await waitFor(() => expect(api.downloadLegacyTemplatePrint).toHaveBeenCalledWith("legacy-v1", format));
  expect(api.printLayout).not.toHaveBeenCalled();
  expect(api.downloadTemplatePrint).not.toHaveBeenCalled();
  await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce());
});

it("keeps renderer failure visible and allows retry for the current revision", async () => {
  vi.mocked(api.printLayout).mockResolvedValue({ revision: "r2" } as never);
  vi.mocked(api.downloadTemplatePrint).mockRejectedValueOnce(new Error("隔離環境でPDFを生成できません"))
    .mockResolvedValue(new Blob(["fixture"]));
  render(<TemplatePrintFiles versionId="v2" />);
  fireEvent.click(screen.getByRole("button", { name: "印刷用PDF" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("隔離環境");
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "印刷用XLSX" }));
  await waitFor(() => expect(api.downloadTemplatePrint).toHaveBeenLastCalledWith("v2", "r2", "xlsx"));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
});

it("blocks downloads for unsaved edits and during an in-flight request", async () => {
  vi.mocked(api.downloadTemplatePrint).mockImplementation(() => new Promise(() => {}));
  const view = render(<TemplatePrintFiles versionId="v1" revision="r1" disabled />);
  expect(screen.getByRole("button", { name: "印刷用XLSX" })).toBeDisabled();
  view.rerender(<TemplatePrintFiles versionId="v1" revision="r1" />);
  fireEvent.click(screen.getByRole("button", { name: "印刷用XLSX" }));
  expect(screen.getByRole("button", { name: "印刷用PDF" })).toBeDisabled();
  expect(api.downloadTemplatePrint).toHaveBeenCalledOnce();
});

it("explains missing page settings without calling the server", () => {
  render(<TemplatePrintFiles versionId="v1" disabledReason="印刷ページ設定が保存されていません。" />);
  expect(screen.getByText("印刷ページ設定が保存されていません。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "印刷用XLSX" }));
  fireEvent.click(screen.getByRole("button", { name: "印刷用PDF" }));
  expect(api.downloadTemplatePrint).not.toHaveBeenCalled();
  expect(api.printLayout).not.toHaveBeenCalled();
});

it("reports stale data and retries using a freshly fetched revision of the same version", async () => {
  vi.mocked(api.printLayout).mockResolvedValueOnce({ revision: "old" } as never)
    .mockResolvedValueOnce({ revision: "new" } as never);
  vi.mocked(api.downloadTemplatePrint).mockRejectedValueOnce(new Error("矩形の編集後は印刷ページ設定を保存し直してください。"))
    .mockResolvedValueOnce(new Blob(["fixture"]));
  render(<TemplatePrintFiles versionId="v1" />);
  fireEvent.click(screen.getByRole("button", { name: "印刷用XLSX" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("保存し直してください");
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "印刷用XLSX" }));
  await waitFor(() => expect(api.downloadTemplatePrint).toHaveBeenLastCalledWith("v1", "new", "xlsx"));
  await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce());
});
