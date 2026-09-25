import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ReferenceQualification } from "./ReferenceQualification";

vi.mock("../api", () => ({ api: { referenceQualification: vi.fn(), qualifyReference: vi.fn(), prepareCandidateReadiness: vi.fn() } }));
afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.referenceQualification).mockResolvedValue({ status: "UNVERIFIED", qualified: false });
});
it("explicitly prepares review candidates and keeps formal qualification distinct", async () => {
  vi.mocked(api.prepareCandidateReadiness).mockResolvedValue({
    status: "CANDIDATE_READY", qualified: false, qualificationIssued: false,
    qualificationPolicy: "paged-candidate-readiness-v2", reviewRequired: true,
    validUntilChange: true,
    geometryQualified: true, codes: [], productionBlockers: [], unresolvedBlockers: [],
    issuedAt: 1, expiresAt: null, environmentDifference: { changed: true },
  });
  const busy = vi.fn();
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady hasStoredEvidence onBusyChange={busy} />);
  await screen.findByText("UNVERIFIED");
  fireEvent.click(screen.getByRole("button", { name: "候補OCRの準備を確認" }));
  expect(await screen.findByText("候補OCR利用可")).toBeInTheDocument();
  expect(api.prepareCandidateReadiness).toHaveBeenCalledWith("v", "r");
  expect(api.qualifyReference).not.toHaveBeenCalled();
  expect(screen.queryByText("この版の正式資格は有効です。")).not.toBeInTheDocument();
  expect(screen.getByText(/全ページの画像と文字/)).toBeInTheDocument();
  expect(busy.mock.calls).toEqual([[true], [false]]);
});

it("keeps candidate retry available after a failed replay", async () => {
  vi.mocked(api.prepareCandidateReadiness).mockRejectedValue(new Error("再描画不一致"));
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady hasStoredEvidence onBusyChange={vi.fn()} />);
  await screen.findByText("UNVERIFIED");
  fireEvent.click(screen.getByRole("button", { name: "候補OCRの準備を確認" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("再描画不一致");
  expect(screen.queryByText("候補OCR利用可")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "候補OCRの準備を確認" })).toBeEnabled();
});
it("reports measured failure and releases the editing lock", async () => {
  const busy = vi.fn();
  vi.mocked(api.qualifyReference).mockResolvedValue({ status: "FAIL", qualified: false,
    codes: ["MAX_ERROR_EXCEEDED"], pages: [{ page: 1, status: "FAIL", maxErrorPx: 7.5, meanErrorPx: 2.4 }] });
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady hasStoredEvidence={false} onBusyChange={busy} />);
  await screen.findByText("UNVERIFIED");
  fireEvent.click(screen.getByRole("button", { name: "資格を確認" }));
  await screen.findByText("MAX_ERROR_EXCEEDED");
  expect(screen.getByRole("button", { name: "資格を再確認" })).toBeEnabled();
  expect(screen.getByText(/最大誤差 7.5/)).toBeInTheDocument();
  expect(busy.mock.calls).toEqual([[true], [false]]);
});
it("keeps retry available after a server conflict", async () => {
  vi.mocked(api.qualifyReference).mockRejectedValue(new Error("設定競合"));
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady hasStoredEvidence={false} onBusyChange={vi.fn()} />);
  await screen.findByText("UNVERIFIED");
  fireEvent.click(screen.getByRole("button"));
  await screen.findByRole("alert");
  expect(screen.getByRole("button")).toBeEnabled();
});
it("does not allow validation of unsaved or read-only settings", async () => {
  render(<ReferenceQualification versionId="v" revision="r" disabled
    preparationReady hasStoredEvidence={false} onBusyChange={vi.fn()} />);
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
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady hasStoredEvidence={false} onBusyChange={vi.fn()} />);
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
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady hasStoredEvidence onBusyChange={vi.fn()} />);
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
    render(<ReferenceQualification versionId="v" revision="r" disabled={false}
      preparationReady hasStoredEvidence onBusyChange={vi.fn()} />);
    await screen.findByText(/事前の記入画像は有効化の前提ではありません/);
    expect(screen.getByText(/有効化の検証/)).toHaveTextContent(
      status === "BLOCKED" ? "未完了" : "資格判定待ち",
    );
    expect(screen.getByText(/入力画像の検証: 未実施/)).toHaveTextContent("全ページの人間による確認が必要です");
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
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady hasStoredEvidence onBusyChange={vi.fn()} />);
  await screen.findByText("POLICY_CHANGED");
  expect(screen.queryByText(/資格判定待ち/)).not.toBeInTheDocument();
});

it("requires formal print preparation before starting qualification", async () => {
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady={false} hasStoredEvidence={false} onBusyChange={vi.fn()} />);
  await screen.findByText("UNVERIFIED");
  expect(screen.getByRole("button", { name: "資格を確認" })).toBeDisabled();
  expect(screen.getByText(/「印刷用ファイルを確定」を実行/)).toBeInTheDocument();
  expect(api.qualifyReference).not.toHaveBeenCalled();
});

it("uses recheck wording when the version already has stored evidence", async () => {
  render(<ReferenceQualification versionId="v" revision="r" disabled={false}
    preparationReady hasStoredEvidence onBusyChange={vi.fn()} />);
  await screen.findByText("UNVERIFIED");
  expect(screen.getByRole("button", { name: "資格を再確認" })).toBeEnabled();
});
