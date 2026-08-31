import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ApiError, api } from "../api";
import type { Template, TemplatePackageDriveItem, TemplatePackageOperation } from "../types";
import { TemplatePackageDialog } from "./TemplatePackageDialog";

const template: Template = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "面接カード",
  documentType: "interview-card",
  activeVersionId: "22222222-2222-2222-2222-222222222222",
  createdAt: "2026-09-01T00:00:00Z",
  versions: [{
    id: "22222222-2222-2222-2222-222222222222",
    versionNumber: 1,
    mode: "REFERENCE_TEMPLATE",
    status: "ACTIVE",
    sourceJobId: "job-1",
    canonicalWidth: 300,
    canonicalHeight: 100,
    quality: { layoutVerified: true },
    createdAt: "2026-09-01T00:00:00Z",
  }],
};

const baseOperation: TemplatePackageOperation = {
  operationId: "33333333-3333-3333-3333-333333333333",
  kind: "IMPORT",
  status: "READY",
  stage: "READY_TO_IMPORT",
  progressPercent: 55,
  message: "packageの検証が完了しました。内容を確認してください。",
  createdAt: "2026-09-01T00:00:00Z",
  updatedAt: "2026-09-01T00:00:00Z",
  expiresAt: "2026-09-01T00:10:00Z",
  fileName: "card.llmocr-template",
  packageSha256: "a".repeat(64),
  confirmationToken: "confirmation-token-value-1234567890",
  exportInspection: null,
  importInspection: {
    formatVersion: 1,
    schemaVersion: 1,
    publisherId: "jp.example",
    packageId: "interview-card",
    version: "1.0.0",
    name: "面接カード",
    contentDigest: `sha256:${"b".repeat(64)}`,
    packageSha256: "a".repeat(64),
    minimumAppVersion: "0.1.0",
    requiredFeatures: ["template-package-core-v1"],
    optionalFeatures: [],
    mode: "REFERENCE_TEMPLATE",
    conflictStatus: "NEW_IDENTITY",
    existingLocalTemplateId: null,
    existingLocalTemplateVersionId: null,
    documentIdentityFieldCount: 2,
    documentNameFieldPaths: ["document.lastName", "document.firstName"],
    candidateSetCount: 1,
    assets: [{
      path: "assets/clean-template.png",
      role: "clean-template",
      mediaType: "image/png",
      size: 1024,
      sha256: "c".repeat(64),
      width: 300,
      height: 100,
    }],
    spreadsheetSourceIncluded: false,
    outputLayoutIncluded: true,
    trustStatus: "UNVERIFIED",
  },
  result: null,
  errorCode: null,
  errorMessage: null,
  errorCategory: null,
};

const driveItem: TemplatePackageDriveItem = {
  kind: "RELEASE",
  fileId: "drive-template-file-1",
  fileName: "interview-card-1.0.0.llmocr-template",
  publisherId: "jp.example",
  packageId: "interview-card",
  releaseVersion: "1.0.0",
  contentDigest: `sha256:${"b".repeat(64)}`,
  packageSha256: "a".repeat(64),
  draftId: null,
  draftRevision: null,
  baseReleaseVersion: null,
  driveVersion: "1",
  headRevisionId: "head-1",
  etag: '"etag-1"',
  modifiedTime: "2026-09-01T00:00:00Z",
  fileBytes: 1024,
  trustStatus: "UNVERIFIED",
  syncStatus: "SYNCED",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("shows export inspection, sensitive candidate values, assets, and keyboard close", async () => {
  const onClose = vi.fn();
  vi.spyOn(api, "artifacts").mockResolvedValue({ items: [] });
  vi.spyOn(api, "templatePackageDriveItems").mockResolvedValue([]);
  vi.spyOn(api, "deleteTemplatePackageOperation").mockResolvedValue(undefined);
  vi.spyOn(api, "inspectTemplatePackageExport").mockResolvedValue({
    ...baseOperation,
    kind: "EXPORT",
    importInspection: null,
    exportInspection: {
      templateName: "面接カード",
      templateVersionNumber: 1,
      mode: "REFERENCE_TEMPLATE",
      candidateSets: [{
        name: "氏名候補",
        valueCount: 1,
        values: ["山田太郎"],
        targetPatterns: ["document.personName"],
      }],
      assets: [{
        role: "clean-template",
        mediaType: "image/png",
        size: 1024,
        sha256: "c".repeat(64),
        width: 300,
        height: 100,
      }],
      outputLayoutIncluded: false,
      spreadsheetSourceIncluded: false,
      warnings: [],
      contentDigest: `sha256:${"b".repeat(64)}`,
      confirmationDigest: `sha256:${"d".repeat(64)}`,
    },
  });

  render(<TemplatePackageDialog
    mode="export"
    template={template}
    version={template.versions[0]}
    refreshTemplates={async () => undefined}
    onClose={onClose}
    onReviewImported={() => undefined}
    onActivated={() => undefined}
  />);

  fireEvent.change(screen.getByLabelText("配布者ID"), { target: { value: "jp.example" } });
  fireEvent.click(screen.getByRole("button", { name: "書き出す内容を確認" }));
  expect(await screen.findByText("山田太郎")).toBeInTheDocument();
  expect(screen.getByText("APIキー・OAuth token")).toBeInTheDocument();
  expect(screen.getByText("clean-template")).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();
});

test("publishes a confirmed release to the separate Drive template flow", async () => {
  vi.spyOn(api, "artifacts").mockResolvedValue({ items: [] });
  vi.spyOn(api, "templatePackageDriveItems").mockResolvedValue([]);
  vi.spyOn(api, "inspectTemplatePackageExport").mockResolvedValue({
    ...baseOperation,
    kind: "EXPORT",
    importInspection: null,
    exportInspection: {
      templateName: "面接カード",
      templateVersionNumber: 1,
      mode: "REFERENCE_TEMPLATE",
      candidateSets: [],
      assets: [],
      outputLayoutIncluded: false,
      spreadsheetSourceIncluded: false,
      warnings: [],
      contentDigest: driveItem.contentDigest,
      confirmationDigest: `sha256:${"d".repeat(64)}`,
    },
  });
  const publish = vi.spyOn(api, "publishTemplatePackageDriveRelease")
    .mockResolvedValue(driveItem);
  render(<TemplatePackageDialog
    mode="export"
    template={template}
    version={template.versions[0]}
    refreshTemplates={async () => undefined}
    onClose={() => undefined}
    onReviewImported={() => undefined}
    onActivated={() => undefined}
  />);
  fireEvent.change(screen.getByLabelText("配布者ID"), { target: { value: "jp.example" } });
  fireEvent.click(screen.getByRole("button", { name: "書き出す内容を確認" }));
  await screen.findByRole("button", { name: "正式版をDriveへ同期" });
  fireEvent.click(screen.getByRole("button", { name: "正式版をDriveへ同期" }));
  expect(await screen.findByText("正式版同期完了")).toBeInTheDocument();
  expect(publish).toHaveBeenCalledWith(baseOperation.confirmationToken);
});

test("selects a Drive release for inspection without activating it", async () => {
  vi.spyOn(api, "templatePackageDriveItems").mockResolvedValue([
    { ...driveItem, syncStatus: "REMOTE_ONLY" },
  ]);
  const inspect = vi.spyOn(api, "inspectDriveTemplatePackage")
    .mockResolvedValue(baseOperation);
  render(<TemplatePackageDialog
    mode="import"
    refreshTemplates={async () => undefined}
    onClose={() => undefined}
    onReviewImported={() => undefined}
    onActivated={() => undefined}
  />);
  fireEvent.click(screen.getByRole("button", { name: "Driveの一覧を取得" }));
  expect(await screen.findByText(/このPCでは未取得/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "内容を確認" }));
  expect(await screen.findByText("配布者未確認")).toBeInTheDocument();
  expect(inspect).toHaveBeenCalledWith(driveItem.fileId, "RELEASE");
  expect(screen.getByRole("button", { name: "有効化せず取り込む" })).toBeInTheDocument();
});

test("classifies package rejection as a security error", async () => {
  vi.spyOn(api, "artifacts").mockResolvedValue({ items: [] });
  vi.spyOn(api, "inspectTemplatePackageExport").mockRejectedValue(
    new ApiError("ZIP内pathが不正です。", "PACKAGE_PATH_INVALID"),
  );
  render(<TemplatePackageDialog
    mode="export"
    template={template}
    version={template.versions[0]}
    refreshTemplates={async () => undefined}
    onClose={() => undefined}
    onReviewImported={() => undefined}
    onActivated={() => undefined}
  />);
  fireEvent.change(screen.getByLabelText("配布者ID"), { target: { value: "jp.example" } });
  fireEvent.click(screen.getByRole("button", { name: "書き出す内容を確認" }));
  expect(await screen.findByText("セキュリティ検証エラー")).toBeInTheDocument();
  expect(screen.getByText("PACKAGE_PATH_INVALID")).toBeInTheDocument();
});

test("imports without activation and exposes review and activation as separate actions", async () => {
  class MockXmlHttpRequest {
    status = 200;
    response: object = baseOperation;
    withCredentials = false;
    responseType = "";
    private listeners: Record<string, () => void> = {};
    upload = { addEventListener: (_name: string, _listener: (event: ProgressEvent) => void) => undefined };
    open() {}
    addEventListener(name: string, listener: () => void) { this.listeners[name] = listener; }
    send() { this.listeners.load?.(); }
  }
  vi.stubGlobal("XMLHttpRequest", MockXmlHttpRequest);
  vi.spyOn(api, "confirmTemplatePackageImport").mockResolvedValue({
    ...baseOperation,
    status: "RUNNING",
    stage: "IMPORTING",
    progressPercent: 70,
    confirmationToken: null,
  });
  vi.spyOn(api, "templatePackageOperation").mockResolvedValue({
    ...baseOperation,
    status: "COMPLETED",
    stage: "COMPLETED",
    progressPercent: 100,
    confirmationToken: null,
    result: {
      installationId: "44444444-4444-4444-4444-444444444444",
      localTemplateId: template.id,
      localTemplateVersionId: template.versions[0].id,
      localJobId: "job-import",
      contentDigest: `sha256:${"b".repeat(64)}`,
      releaseVersion: "1.0.0",
      created: true,
      activeVersionChanged: false,
    },
  });
  vi.spyOn(api, "template").mockResolvedValue({ ...template, activeVersionId: null });
  const activate = vi.spyOn(api, "activateTemplatePackage").mockResolvedValue({
    installationId: "44444444-4444-4444-4444-444444444444",
    localTemplateId: template.id,
    localTemplateVersionId: template.versions[0].id,
    previousActiveVersionId: null,
    activeVersionId: template.versions[0].id,
    candidateSetCount: 1,
    outputLayoutMaterialized: true,
    alreadyActive: false,
  });
  const onActivated = vi.fn();
  render(<TemplatePackageDialog
    mode="import"
    refreshTemplates={async () => undefined}
    onClose={() => undefined}
    onReviewImported={() => undefined}
    onActivated={onActivated}
  />);
  const file = new File(["package"], "card.llmocr-template");
  fireEvent.change(screen.getByLabelText(/取り込むpackage/), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "packageを検証" }));
  expect(await screen.findByText("配布者未確認")).toBeInTheDocument();
  expect(screen.getByText(/document\.lastName \+ document\.firstName/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "有効化せず取り込む" }));
  expect(await screen.findByRole("button", { name: "取り込んだ版を確認" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "有効版にする" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "有効版にする" }));
  await waitFor(() => expect(activate).toHaveBeenCalledWith(
    "44444444-4444-4444-4444-444444444444",
    null,
  ));
  expect(onActivated).toHaveBeenCalledWith(template.versions[0].id);
});
