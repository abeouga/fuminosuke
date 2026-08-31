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
  expect(screen.getByText(/保存完了はこの画面では判定できません/)).toBeInTheDocument();
  expect(anchorClick).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
