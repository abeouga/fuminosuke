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

it("reports a complete current certificate as PASS without offering an unnecessary reissue", async () => {
  vi.mocked(api.referenceQualification).mockResolvedValue({
    status: "PASS", qualified: true, qualificationIssued: true,
    environmentQualified: true, geometryQualified: true,
    productionBlockers: [], unresolvedBlockers: [], expiresAt: 4_102_444_800,
  });
  render(<ReferenceQualification versionId="v" revision="r" disabled={false} onBusyChange={vi.fn()} />);
  expect(await screen.findByText("PASS")).toBeInTheDocument();
  expect(screen.getByText("この版の正式資格は有効です。")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "資格を再確認" })).not.toBeInTheDocument();
});

it("separates an expired certificate from an unverified certificate", async () => {
  vi.mocked(api.referenceQualification).mockResolvedValue({
    status: "PASS", qualified: true, qualificationIssued: true,
    environmentQualified: true, geometryQualified: true,
    productionBlockers: [], unresolvedBlockers: [], expiresAt: 1,
  });
  render(<ReferenceQualification versionId="v" revision="r" disabled={false} onBusyChange={vi.fn()} />);
  expect(await screen.findByText("EXPIRED")).toBeInTheDocument();
  expect(screen.getByText(/資格期限切れ/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "資格を再確認" })).toBeEnabled();
});

it.each(["BLOCKED", "READY_FOR_QUALIFICATION"] as const)(
  "separates deferred input checks without treating %s as activation approval", async (status) => {
    vi.mocked(api.referenceQualification).mockResolvedValue({
      status: "UNSUPPORTED", qualified: false,
      safetyStages: {
        policy: "paged-two-stage-safety-v1",
        activation: { status, codes: status === "BLOCKED" ? ["ENVIRONMENT_UNVERIFIED"] : [],
          requiresSubmittedScan: false, certificateIssued: false },
        execution: { status: "NOT_EVALUATED", requiredBefore: "PROVIDER_REQUEST",
          checks: ["INK_CLEARANCE"], requiresSubmittedScan: true },
      },
    });
    render(<ReferenceQualification versionId="v" revision="r" disabled={false} onBusyChange={vi.fn()} />);
    await screen.findByText(/事前の記入画像は有効化の前提ではありません/);
    expect(screen.getByText(/有効化の検証/)).toHaveTextContent(
      status === "BLOCKED" ? "未完了" : "資格判定待ち",
    );
    expect(screen.getByText(/入力画像の検証: 未実施/)).toBeInTheDocument();
    if (status === "BLOCKED") {
      expect(screen.getByLabelText("有効化の未解決条件")).toHaveTextContent("ENVIRONMENT_UNVERIFIED");
    } else {
      expect(screen.queryByLabelText("有効化の未解決条件")).not.toBeInTheDocument();
    }
  },
);

it("keeps rejection codes visible for an unsupported stage policy", async () => {
  vi.mocked(api.referenceQualification).mockResolvedValue(JSON.parse(
    '{"status":"UNVERIFIED","qualified":false,"codes":["POLICY_CHANGED"],'
    + '"safetyStages":{"policy":"future-policy"}}',
  ));
  render(<ReferenceQualification versionId="v" revision="r" disabled={false} onBusyChange={vi.fn()} />);
  await screen.findByText("POLICY_CHANGED");
  expect(screen.queryByText(/資格判定待ち/)).not.toBeInTheDocument();
});
