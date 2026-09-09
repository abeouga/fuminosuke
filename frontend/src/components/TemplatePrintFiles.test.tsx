import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { TemplatePrintFiles } from "./TemplatePrintFiles";

vi.mock("../api", () => ({ api: { printLayout: vi.fn(), downloadTemplatePrint: vi.fn() } }));
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
  expect(api.printLayout).not.toHaveBeenCalled();
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
