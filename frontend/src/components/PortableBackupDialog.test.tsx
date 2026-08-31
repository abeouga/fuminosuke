import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { PortableBackupDialog } from "./PortableBackupDialog";

const exportResult = {
  id: "11111111-1111-4111-8111-111111111111",
  fileName: "llm-ocr-portable-backup-20260831-120000.zip",
  fileBytes: 6,
  fileCount: 3,
  sourceBytes: 12,
  createdAt: "2026-08-31T03:00:00Z",
  expiresAt: "2026-09-01T03:00:00Z",
  downloadUrl: "/api/v1/system-backups/portable/11111111-1111-4111-8111-111111111111",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "showSaveFilePicker");
  Reflect.deleteProperty(window, "isSecureContext");
});

test("streams a generated backup to the OS-selected file without creating a blob", async () => {
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  const operationOrder: string[] = [];
  const write = vi.fn(async () => undefined);
  const close = vi.fn(async () => undefined);
  const abort = vi.fn(async () => undefined);
  const picker = vi.fn(async () => ({
    name: "external-backup.zip",
    createWritable: vi.fn(async () => {
      operationOrder.push("open-destination");
      return { write, close, abort };
    }),
  }));
  Object.defineProperty(window, "showSaveFilePicker", {
    value: picker,
    configurable: true,
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/portable-operations/")) return jsonResponse(exportProgress(path));
    if (path.endsWith("/system-backups/portable") && init?.method === "POST") {
      return jsonResponse(exportResult);
    }
    if (path.endsWith(exportResult.id) && init?.method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (path.endsWith(exportResult.id)) {
      operationOrder.push("start-download");
      return new Response(new Uint8Array([1, 2, 3, 4, 5, 6]), {
        status: 200,
        headers: { "Content-Length": "6", "Content-Type": "application/zip" },
      });
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <PortableBackupDialog
      onClose={vi.fn()}
      returnFocusRef={{ current: null }}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "保存先を選んで作成" }));

  expect(picker).toHaveBeenCalledWith(expect.objectContaining({
    id: "ledger-ocr-portable-backup",
    excludeAcceptAllOption: true,
    types: [{
      description: "ZIPバックアップ",
      accept: { "application/zip": [".zip"] },
    }],
  }));
  expect(await screen.findByText("保存が完了しました")).toBeInTheDocument();
  expect(screen.getByText(/external-backup\.zip/)).toBeInTheDocument();
  expect(write).toHaveBeenCalled();
  expect(close).toHaveBeenCalledTimes(1);
  expect(abort).not.toHaveBeenCalled();
  expect(operationOrder).toEqual(["open-destination", "start-download"]);
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(exportResult.id),
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

test("opens a new destination before retrying a generated backup", async () => {
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  const firstCreateWritable = vi.fn(async () => {
    throw new Error("保存先を開けませんでした。");
  });
  const write = vi.fn(async () => undefined);
  const close = vi.fn(async () => undefined);
  const secondCreateWritable = vi.fn(async () => ({
    write,
    close,
    abort: vi.fn(async () => undefined),
  }));
  const picker = vi.fn()
    .mockResolvedValueOnce({ name: "unavailable.zip", createWritable: firstCreateWritable })
    .mockResolvedValueOnce({ name: "replacement.zip", createWritable: secondCreateWritable });
  Object.defineProperty(window, "showSaveFilePicker", {
    value: picker,
    configurable: true,
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/portable-operations/")) return jsonResponse(exportProgress(path));
    if (path.endsWith("/system-backups/portable") && init?.method === "POST") {
      return jsonResponse(exportResult);
    }
    if (path.endsWith(exportResult.id) && init?.method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (path.endsWith(exportResult.id)) {
      return new Response(new Uint8Array([1, 2, 3, 4, 5, 6]), {
        status: 200,
        headers: { "Content-Length": "6", "Content-Type": "application/zip" },
      });
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <PortableBackupDialog
      onClose={vi.fn()}
      returnFocusRef={{ current: null }}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "保存先を選んで作成" }));

  const reselect = await screen.findByRole("button", { name: "保存先を選び直す" });
  expect(firstCreateWritable).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls.filter(([input, init]) => (
    String(input).endsWith(exportResult.id) && init?.method !== "DELETE"
  ))).toHaveLength(0);

  fireEvent.click(reselect);

  expect(await screen.findByText("保存が完了しました")).toBeInTheDocument();
  expect(screen.getByText(/replacement\.zip/)).toBeInTheDocument();
  expect(picker).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(fetchMock.mock.calls.filter(([input, init]) => (
    String(input).endsWith(exportResult.id) && init?.method !== "DELETE"
  ))).toHaveLength(1);
});

test("does not create a backup when the OS save dialog is cancelled", async () => {
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  const picker = vi.fn(async () => {
    throw new DOMException("cancelled", "AbortError");
  });
  Object.defineProperty(window, "showSaveFilePicker", {
    value: picker,
    configurable: true,
  });
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);

  render(
    <PortableBackupDialog
      onClose={vi.fn()}
      returnFocusRef={{ current: null }}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "保存先を選んで作成" }));

  await waitFor(() => expect(picker).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole("button", { name: "保存先を選んで作成" })).toBeEnabled();
  expect(fetchMock).not.toHaveBeenCalled();
});

test("uses a memory-safe browser download fallback when the save picker is unavailable", async () => {
  Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
  const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/portable-operations/")) return jsonResponse(exportProgress(path));
    if (path.endsWith("/system-backups/portable") && init?.method === "POST") {
      return jsonResponse(exportResult);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <PortableBackupDialog
      onClose={vi.fn()}
      returnFocusRef={{ current: null }}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "バックアップを作成" }));

  expect(await screen.findByText("ブラウザのダウンロードを開始しました")).toBeInTheDocument();
  expect(screen.getByText(/保存中の進捗と完了を判定できません/)).toBeInTheDocument();
  expect(anchorClick).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});

test("validates and applies an imported backup only after explicit replacement confirmation", async () => {
  const ready = restoreOperation("READY", "READY_TO_APPLY", 75);
  const completed = restoreOperation("COMPLETED", "COMPLETED", 100);
  const xhr = installMockUpload();
  let resolveApply!: (response: Response) => void;
  const applyResponse = new Promise<Response>((resolve) => {
    resolveApply = resolve;
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/apply") && init?.method === "POST") return applyResponse;
    if (path.includes("/portable-imports/")) {
      return jsonResponse(ready);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);

  const { container } = render(
    <PortableBackupDialog
      onClose={vi.fn()}
      returnFocusRef={{ current: null }}
    />,
  );

  fireEvent.click(screen.getByRole("tab", { name: "取り込み" }));
  expect(screen.getByText(/現在のDB・帳票画像・テンプレート・出力ファイルを置換/)).toBeInTheDocument();
  const validateButton = screen.getByRole("button", { name: "ZIPを検証" });
  expect(validateButton).toBeDisabled();
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).not.toBeNull();
  fireEvent.change(input!, {
    target: { files: [new File(["backup"], "portable-backup.zip", { type: "application/zip" })] },
  });
  expect(screen.getByText("portable-backup.zip")).toBeInTheDocument();
  expect(validateButton).toBeEnabled();

  fireEvent.click(validateButton);

  expect(await screen.findByRole("progressbar", { name: "バックアップ取り込み進捗" })).toHaveAttribute("aria-valuenow", "75");
  const steps = screen.getByRole("list", { name: "取り込み工程" });
  for (const label of ["ZIP受信", "ZIP検証", "DB検証", "データ切替", "再起動"]) {
    expect(steps).toHaveTextContent(label);
  }
  xhr.complete(ready);

  expect(await screen.findByText("ZIPの検証が完了しました")).toBeInTheDocument();
  expect(screen.getByText("2026/08/31 12:00")).toBeInTheDocument();
  const applyButton = screen.getByRole("button", { name: "現在データを置換して取り込む" });
  expect(applyButton).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "現在の保存データが置換されることを確認しました" }));
  expect(applyButton).toBeEnabled();

  fireEvent.click(applyButton);

  expect(await screen.findByRole("progressbar", { name: "バックアップ取り込み進捗" })).toHaveAttribute("aria-valuenow", "78");
  expect(screen.getByText("保存処理を停止し、検証済みデータへ切り替えています。")).toBeInTheDocument();
  resolveApply(jsonResponse(completed));
  expect(await screen.findByText("取り込みが完了しました")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取り込んだデータで再読み込み" })).toBeEnabled();
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringMatching(/portable-imports\/.+\/apply$/),
    expect.objectContaining({ method: "POST" }),
  );
});

test("shows a validation failure without presenting the destructive apply action", async () => {
  const xhr = installMockUpload();
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
    detail: "取り込み処理がまだ登録されていません。",
    code: "RESTORE_OPERATION_NOT_FOUND",
  }, 404)));
  const { container } = render(
    <PortableBackupDialog
      onClose={vi.fn()}
      returnFocusRef={{ current: null }}
    />,
  );
  fireEvent.click(screen.getByRole("tab", { name: "取り込み" }));
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  fireEvent.change(input, {
    target: { files: [new File(["bad"], "broken.zip", { type: "application/zip" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "ZIPを検証" }));
  xhr.fail({
    detail: "ZIPの構造または内容を検証できませんでした。",
    code: "RESTORE_ARCHIVE_INVALID",
  });

  expect(await screen.findByText("バックアップを取り込めませんでした")).toBeInTheDocument();
  expect(screen.getByText("ZIPの構造または内容を検証できませんでした。")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "現在データを置換して取り込む" })).not.toBeInTheDocument();
});

test("enables Drive auto-sync and shows verified snapshot history", async () => {
  let enabled = false;
  let synced = false;
  const status = () => ({
    enabled,
    status: !enabled ? "DISABLED" : synced ? "SYNCED" : "PENDING",
    stage: !enabled ? "IDLE" : synced ? "COMPLETED" : "WAITING_FOR_IDLE",
    progressPercent: synced ? 100 : 0,
    message: !enabled
      ? "Google Drive自動同期は停止しています。"
      : synced
        ? "Google Driveと同期済みです。"
        : "未同期の変更があります。自動同期を待っています。",
    dirty: enabled && !synced,
    remoteRevision: synced ? 1 : 0,
    latestSnapshotFileId: synced ? "snapshot-1" : null,
    latestSnapshotSha256: synced ? "a".repeat(64) : null,
    latestSnapshotCreatedAt: synced ? "2026-09-01T00:00:00Z" : null,
    leaseOwnerDeviceId: enabled ? "device-1" : null,
    leaseOwnerDeviceName: enabled ? "OCR-PC" : null,
    leaseExpiresAt: enabled ? "2026-09-01T00:02:00Z" : null,
    lastAttemptAt: synced ? "2026-09-01T00:00:00Z" : null,
    lastSuccessAt: synced ? "2026-09-01T00:00:00Z" : null,
    error: null,
    deviceId: "device-1",
    deviceName: "OCR-PC",
    readOnly: false,
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/workspace-sync") && init?.method === "PUT") {
      enabled = true;
      return jsonResponse(status());
    }
    if (path.endsWith("/workspace-sync/sync-now") && init?.method === "POST") {
      synced = true;
      return jsonResponse(status());
    }
    if (path.endsWith("/workspace-sync/snapshots")) {
      return jsonResponse({
        items: synced ? [{
          fileId: "snapshot-1",
          fileName: "workspace-1.zip",
          revision: 1,
          sha256: "a".repeat(64),
          deviceId: "device-1",
          createdAt: "2026-09-01T00:00:00Z",
          fileBytes: 4096,
        }] : [],
      });
    }
    if (path.endsWith("/workspace-sync")) return jsonResponse(status());
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <PortableBackupDialog
      onClose={vi.fn()}
      returnFocusRef={{ current: null }}
    />,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Drive自動同期" }));
  fireEvent.click(await screen.findByRole("button", { name: "自動同期を有効化" }));

  expect(await screen.findByText("自動同期待ち")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "今すぐ同期" }));

  expect(await screen.findByText("同期済み")).toBeInTheDocument();
  expect(screen.getByText("第1版")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "内容を検証して取り込む" })).toBeInTheDocument();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function exportProgress(path: string) {
  return {
    operationId: path.split("/").at(-1),
    status: "READY",
    stage: "READY",
    progressPercent: 100,
    message: "バックアップの作成が完了しました。",
    bytesProcessed: 12,
    totalBytes: 12,
    backupId: exportResult.id,
    errorCode: null,
    errorMessage: null,
    updatedAt: "2026-08-31T03:00:00Z",
  };
}

function restoreOperation(status: "READY" | "COMPLETED", stage: string, progressPercent: number) {
  return {
    operationId: "22222222-2222-4222-8222-222222222222",
    status,
    stage,
    progressPercent,
    message: status === "COMPLETED" ? "取り込みが完了しました。" : "検証が完了しました。",
    fileName: "portable-backup.zip",
    fileBytes: 6,
    bytesProcessed: 12,
    totalBytes: 12,
    fileCount: 3,
    sourceBytes: 12,
    backupCreatedAt: "2026-08-31T03:00:00Z",
    schemaRevision: "0022_archive_in_use_templates",
    errorCode: null,
    errorMessage: null,
    rollbackExpiresAt: status === "COMPLETED" ? "2026-09-01T03:00:00Z" : null,
    updatedAt: "2026-08-31T03:00:00Z",
  };
}

function installMockUpload() {
  let pending: MockXMLHttpRequest | null = null;
  class MockXMLHttpRequest extends EventTarget {
    upload = new EventTarget();
    status = 0;
    response: unknown = null;
    responseType = "";
    withCredentials = false;
    open() {}
    send() {
      pending = this;
      this.upload.dispatchEvent(new ProgressEvent("progress", {
        lengthComputable: true,
        loaded: 6,
        total: 6,
      }));
    }
  }
  vi.stubGlobal("XMLHttpRequest", MockXMLHttpRequest);
  const finish = (body: unknown, status: number) => {
    if (!pending) throw new Error("Upload was not started");
    pending.status = status;
    pending.response = body;
    pending.dispatchEvent(new Event("load"));
  };
  return {
    complete: (body: unknown) => finish(body, 200),
    fail: (body: unknown) => finish(body, 422),
  };
}
