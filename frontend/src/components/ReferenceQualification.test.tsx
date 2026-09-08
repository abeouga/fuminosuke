import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ReferenceQualification } from "./ReferenceQualification";

vi.mock("../api", () => ({ api: { referenceQualification: vi.fn(), qualifyReference: vi.fn() } }));
afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.referenceQualification).mockResolvedValue({ status: "UNVERIFIED", qualified: false });
});
it("reports measured failure and releases the editing lock", async () => {
  const busy = vi.fn();
  vi.mocked(api.qualifyReference).mockResolvedValue({ status: "FAIL", qualified: false,
    codes: ["MAX_ERROR_EXCEEDED"], pages: [{ page: 1, status: "FAIL", maxErrorPx: 7.5, meanErrorPx: 2.4 }] });
  render(<ReferenceQualification versionId="v" revision="r" disabled={false} onBusyChange={busy} />);
  await screen.findByText("UNVERIFIED");
  fireEvent.click(screen.getByRole("button"));
  await screen.findByText("MAX_ERROR_EXCEEDED");
  expect(screen.getByText(/最大誤差 7.5/)).toBeInTheDocument();
  expect(busy.mock.calls).toEqual([[true], [false]]);
});
it("keeps retry available after a server conflict", async () => {
  vi.mocked(api.qualifyReference).mockRejectedValue(new Error("設定競合"));
  render(<ReferenceQualification versionId="v" revision="r" disabled={false} onBusyChange={vi.fn()} />);
  await screen.findByText("UNVERIFIED");
  fireEvent.click(screen.getByRole("button"));
  await screen.findByRole("alert");
  expect(screen.getByRole("button")).toBeEnabled();
});
it("does not allow validation of unsaved or read-only settings", async () => {
  render(<ReferenceQualification versionId="v" revision="r" disabled onBusyChange={vi.fn()} />);
  await screen.findByText("UNVERIFIED");
  expect(screen.getByRole("button")).toBeDisabled();
  expect(api.qualifyReference).not.toHaveBeenCalled();
});
