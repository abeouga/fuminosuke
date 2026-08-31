import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { mountGoogleDriveSpreadsheetPicker, type PickedDriveSpreadsheet } from "../googlePicker";
import { DocumentLibraryPage } from "./DocumentLibraryPage";

vi.mock("../googlePicker", () => ({
  mountGoogleDriveSpreadsheetPicker: vi.fn(),
}));

function syncResponse(overrides: Record<string, unknown> = {}) {
  return new Response(JSON.stringify({
    status: "SYNCED",
    lastAttemptAt: "2026-08-31T01:00:00Z",
    lastSuccessAt: "2026-08-31T01:00:00Z",
    foldersImported: 0,
    foldersMirrored: 0,
    foldersUpdated: 0,
    documentsUpdated: 0,
    conflicts: 0,
    conflictItems: [],
    error: null,
    ...overrides,
  }), { status: 200 });
}

afterEach(() => {
  cleanup();
  vi.mocked(mountGoogleDriveSpreadsheetPicker).mockReset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("groups a ledger and starts page-specific reading actions", async () => {
  const startReading = vi.fn();
  const openReview = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/document-library/sync")
    ? syncResponse()
    : new Response(JSON.stringify({
    count: 1,
    folders: [],
    documents: [{
      documentId: "document-1",
      templateId: "template-1",
      templateName: "繁殖台帳",
      individualId: "1355480523",
      cattleName: "こよみ",
      currentJobId: "job-2",
      updatedAt: "2026-08-21T10:00:00Z",
      pageCount: 2,
      pages: [{
        pageId: "page-1",
        pageNumber: 1,
        sourceJobId: "job-1",
        templateVersionId: "version-1",
        thumbnailUrl: "/api/v1/jobs/job-1/artifacts/alignedSource",
        confirmedAt: "2026-08-20T10:00:00Z",
        versionCount: 2,
        history: [],
      }, {
        pageId: "page-2",
        pageNumber: 2,
        sourceJobId: "job-2",
        templateVersionId: "version-1",
        thumbnailUrl: "/api/v1/jobs/job-2/artifacts/alignedSource",
        confirmedAt: "2026-08-21T10:00:00Z",
        versionCount: 1,
        history: [],
      }],
      spreadsheetId: "sheet-1",
      spreadsheetUrl: "https://docs.google.com/spreadsheets/d/sheet-1/edit",
      driveFolderPath: "帳票OCR > 2026年度",
      spreadsheetSyncStatus: "synced",
      spreadsheetSyncError: null,
      libraryFolderId: null,
      locationVersion: 1,
    }],
    items: [{
      individualId: "1355480523",
      cattleName: "こよみ",
      ledgerCount: 1,
      pageCount: 2,
      lastUpdatedAt: "2026-08-21T10:00:00Z",
      ledgers: [{
        documentId: "document-1",
        templateId: "template-1",
        templateName: "繁殖台帳",
        individualId: "1355480523",
        cattleName: "こよみ",
        currentJobId: "job-2",
        updatedAt: "2026-08-21T10:00:00Z",
        pageCount: 2,
        pages: [{
          pageId: "page-1",
          pageNumber: 1,
          sourceJobId: "job-1",
          templateVersionId: "version-1",
          thumbnailUrl: "/api/v1/jobs/job-1/artifacts/alignedSource",
          confirmedAt: "2026-08-20T10:00:00Z",
          versionCount: 2,
          history: [],
        }, {
          pageId: "page-2",
          pageNumber: 2,
          sourceJobId: "job-2",
          templateVersionId: "version-1",
          thumbnailUrl: "/api/v1/jobs/job-2/artifacts/alignedSource",
          confirmedAt: "2026-08-21T10:00:00Z",
          versionCount: 1,
          history: [],
        }],
        spreadsheetId: "sheet-1",
        spreadsheetUrl: "https://docs.google.com/spreadsheets/d/sheet-1/edit",
        driveFolderPath: "帳票OCR > 2026年度",
        spreadsheetSyncStatus: "synced",
        spreadsheetSyncError: null,
      }],
    }],
  }), { status: 200 })));

  render(<DocumentLibraryPage startReading={startReading} openReview={openReview} />);
  const libraryCard = await screen.findByRole("button", { name: /こよみ.*1355480523/ });
  const interactiveCard = libraryCard.closest("article");
  expect(interactiveCard).not.toBeNull();
  vi.spyOn(interactiveCard!, "getBoundingClientRect").mockReturnValue({
    left: 100,
    top: 50,
    width: 200,
    height: 100,
    right: 300,
    bottom: 150,
    x: 100,
    y: 50,
    toJSON: () => ({}),
  });
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    callback(0);
    return 1;
  });
  fireEvent(
    interactiveCard!,
    new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 50 }),
  );
  expect(interactiveCard!.style.getPropertyValue("--card-glare-opacity")).toBe("1");
  expect(interactiveCard!.style.getPropertyValue("--card-tilt-x")).toBe("2.40deg");
  expect(interactiveCard!.style.getPropertyValue("--card-tilt-y")).toBe("2.40deg");

  fireEvent.pointerLeave(interactiveCard!, { pointerType: "mouse" });
  expect(interactiveCard!.style.getPropertyValue("--card-glare-opacity")).toBe("0");
  expect(interactiveCard!.style.getPropertyValue("--card-tilt-x")).toBe("0deg");
  expect(interactiveCard!.style.getPropertyValue("--card-tilt-y")).toBe("0deg");

  fireEvent.click(libraryCard);

  expect(screen.getByRole("heading", { name: "こよみの書類" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "このページを読み直す" })).toHaveLength(2);
  expect(screen.getByRole("link", { name: /スプレッドシートを開く/ })).toHaveAttribute(
    "href",
    "https://docs.google.com/spreadsheets/d/sheet-1/edit",
  );
  expect(screen.getByRole("link", { name: /スプレッドシートを開く/ })).toHaveAttribute("target", "_blank");

  fireEvent.click(screen.getAllByRole("button", { name: "このページを読み直す" })[1]);
  expect(startReading).toHaveBeenLastCalledWith(expect.objectContaining({
    action: "RESCAN_PAGE",
    documentId: "document-1",
    pageId: "page-2",
    pageNumber: 2,
  }));

  fireEvent.click(screen.getByRole("button", { name: /続きのページを追加/ }));
  expect(startReading).toHaveBeenLastCalledWith(expect.objectContaining({
    action: "ADD_PAGE",
    documentId: "document-1",
  }));

  fireEvent.click(screen.getByRole("button", { name: "別の台帳を読み取る" }));
  expect(startReading).toHaveBeenLastCalledWith(expect.objectContaining({
    action: "NEW_DOCUMENT",
    individualId: "1355480523",
  }));

  fireEvent.click(screen.getByRole("button", { name: "現在の結果を開く" }));
  await waitFor(() => expect(openReview).toHaveBeenCalledWith("job-2"));
});

test("selects, renames, and bulk deletes library documents", async () => {
  const document = {
    documentId: "document-1",
    templateId: "template-1",
    templateName: "面接カード",
    individualId: "candidate-1",
    cattleName: null,
    displayTitle: "面接カード 2026",
    displayTitleLabel: "書類名",
    displaySubtitle: "candidate-1",
    displaySubtitleLabel: "識別子",
    currentJobId: "job-1",
    updatedAt: "2026-08-30T10:00:00Z",
    pageCount: 1,
    pages: [],
    spreadsheetId: "sheet-1",
    spreadsheetUrl: "https://docs.google.com/spreadsheets/d/sheet-1/edit",
    driveFolderPath: "帳票OCR > 2026年度",
    spreadsheetSyncStatus: "synced",
    spreadsheetSyncError: null,
    libraryFolderId: null,
    locationVersion: 1,
  };
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(_input);
    if (path.endsWith("/document-library/sync")) {
      return syncResponse();
    }
    if (init?.method === "PATCH") {
      return new Response(JSON.stringify({ documentId: "document-1", name: "面接カード 変更後", spreadsheetRenamed: true }), { status: 200 });
    }
    if (path.endsWith("/document-library/documents/bulk-delete") && init?.method === "POST") {
      return new Response(JSON.stringify({ deletedCount: 1, driveFilesPreserved: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ count: 1, folders: [], documents: [document], items: [] }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "prompt").mockReturnValue("面接カード 変更後");
  vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<DocumentLibraryPage startReading={vi.fn()} openReview={vi.fn()} />);
  const openSheet = await screen.findByRole("link", { name: /スプレッドシートを開く/ });
  expect(openSheet).toHaveAttribute("target", "_blank");

  fireEvent.click(screen.getByRole("button", { name: "名前変更" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));

  const checkboxes = screen.getAllByRole("checkbox");
  fireEvent.click(checkboxes[1]);
  expect(screen.getByText("1件選択中")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "選択した書類を削除" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith("/document-library/documents/bulk-delete") && init?.method === "POST")).toBe(true));
  const deleteCall = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/document-library/documents/bulk-delete") && init?.method === "POST");
  expect(deleteCall?.[1]?.body).toBe(JSON.stringify({ documentIds: ["document-1"] }));
});

test("automatically syncs the library and reports conflicts, failures, and recovery", async () => {
  const requestedPaths: string[] = [];
  let syncAttempt = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    requestedPaths.push(path);
    if (path.endsWith("/document-library/sync")) {
      syncAttempt += 1;
      if (syncAttempt === 1) {
        return syncResponse({
          status: "PARTIAL",
          lastSuccessAt: null,
          foldersImported: 1,
          conflicts: 2,
          conflictItems: [
            {
              code: "BOTH_MODIFIED",
              resourceId: "folder-1",
              resourceName: "2026年度",
              driveRootId: "root-a",
              driveRootName: "共通保存先",
              detail: "Drive側と独自ライブラリ側の両方が変更されています。",
            },
          ],
        });
      }
      if (syncAttempt === 2) {
        return syncResponse({
          status: "ERROR",
          lastSuccessAt: null,
          error: "Google Driveの変更を取得できませんでした。",
        });
      }
      return syncResponse({ foldersUpdated: 1 });
    }
    if (path.endsWith("/document-library")) {
      return new Response(JSON.stringify({ count: 0, folders: [], documents: [], items: [] }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<DocumentLibraryPage startReading={vi.fn()} openReview={vi.fn()} />);

  expect(await screen.findByText("一部を同期しました")).toBeInTheDocument();
  expect(screen.getByText(
    "2件の競合を検出しました。データを失わないよう独自ライブラリの内容を保持しました。",
  )).toBeInTheDocument();
  expect(screen.getByText("競合の詳細を表示")).toBeInTheDocument();
  expect(screen.getByText("2026年度")).toBeInTheDocument();
  expect(screen.getByText("保存先: 共通保存先")).toBeInTheDocument();
  expect(requestedPaths.slice(0, 2).map((path) => path.replace("/api/v1", ""))).toEqual([
    "/document-library",
    "/document-library/sync",
  ]);

  fireEvent.click(screen.getByRole("button", { name: "再同期" }));
  expect(await screen.findByText("Google Driveと同期できませんでした")).toBeInTheDocument();
  expect(screen.getByText("Google Driveの変更を取得できませんでした。")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "再同期" }));
  expect(await screen.findByText("Google Driveと同期済み")).toBeInTheDocument();
  expect(screen.getByText("1フォルダ更新")).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/document-library/sync"))).toHaveLength(3);
  expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/document-library"))).toHaveLength(4);
});

test("pushes only unreflected library items to Drive with dedicated progress and result", async () => {
  let resolvePush!: (response: Response) => void;
  const pushResponse = new Promise<Response>((resolve) => {
    resolvePush = resolve;
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/document-library/sync-to-drive")) {
      return pushResponse;
    }
    if (path.endsWith("/document-library/sync")) {
      return syncResponse();
    }
    if (path.endsWith("/document-library")) {
      return new Response(JSON.stringify({ count: 0, folders: [], documents: [], items: [] }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<DocumentLibraryPage startReading={vi.fn()} openReview={vi.fn()} />);

  const pushButton = await screen.findByRole("button", { name: "未反映をGoogle Driveへ同期" });
  fireEvent.click(pushButton);

  expect(await screen.findByText("Google Driveへ未反映を同期中")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Driveへ反映中…" })).toBeDisabled();
  resolvePush(syncResponse({
    mode: "LIBRARY_TO_DRIVE",
    foldersMirrored: 2,
    documentsUpdated: 1,
  }));

  expect(await screen.findByText("Google Driveへの反映が完了しました")).toBeInTheDocument();
  expect(screen.getByText("Driveへ2フォルダ作成・1書類更新")).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([input]) => (
    String(input).endsWith("/document-library/sync-to-drive")
  ))).toHaveLength(1);
  expect(fetchMock.mock.calls.filter(([input]) => (
    String(input).endsWith("/document-library/sync")
  ))).toHaveLength(1);
});

test("offers non-destructive Google reconnection when sync needs authorization", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/document-library/sync")) {
      return syncResponse({
        status: "REAUTHORIZATION_REQUIRED",
        lastSuccessAt: null,
        error: "Google Driveのフォルダ同期権限が不足しています。",
      });
    }
    if (path.endsWith("/document-library")) {
      return new Response(JSON.stringify({ count: 0, folders: [], documents: [], items: [] }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<DocumentLibraryPage startReading={vi.fn()} openReview={vi.fn()} />);

  expect(await screen.findByText("Googleアカウントへの再接続が必要です")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Google連携を更新" })).toBeEnabled();
  expect(fetchMock.mock.calls.some(([input]) => (
    String(input).endsWith("/auth/google/disconnect")
  ))).toBe(false);
});

test("requires explicit confirmation before claiming a legacy Drive document", async () => {
  let syncAttempt = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/document-library/sync")) {
      syncAttempt += 1;
      return syncAttempt === 1
        ? syncResponse({
          status: "PARTIAL",
          conflicts: 1,
          conflictItems: [{
            code: "DRIVE_DOCUMENT_OWNER_UNCONFIRMED",
            resourceId: "document-legacy",
            resourceName: "旧面接カード",
            driveRootId: "root-a",
            driveRootName: "共通保存先",
            detail: "所有アカウントを確認してください。",
          }],
        })
        : syncResponse();
    }
    if (path.endsWith("/document-library/documents/document-legacy/drive-ownership")) {
      return new Response(JSON.stringify({
        documentId: "document-legacy",
        googleSubject: "subject-1",
        driveRootFolderId: "root-a",
        alreadyConfirmed: false,
      }), { status: 200 });
    }
    if (path.endsWith("/document-library")) {
      return new Response(JSON.stringify({ count: 0, folders: [], documents: [], items: [] }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const confirmation = vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<DocumentLibraryPage startReading={vi.fn()} openReview={vi.fn()} />);

  const claimButton = await screen.findByRole("button", { name: "現在のGoogleアカウントで確認" });
  fireEvent.click(claimButton);

  expect(confirmation).toHaveBeenCalledWith(expect.stringContaining("保存先: 共通保存先"));
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => (
    String(input).endsWith("/document-library/documents/document-legacy/drive-ownership")
    && init?.method === "POST"
    && init.body === JSON.stringify({ confirm: true })
  ))).toBe(true));
  await waitFor(() => expect(syncAttempt).toBe(2));
});

test("switches to Google Drive picker mode without changing the library UI", async () => {
  const spreadsheet = {
    id: "sheet-1",
    name: "面接カード.xlsx",
    url: "https://docs.google.com/spreadsheets/d/sheet-1/edit",
  };
  const pickerCallbacks: { onPicked?: (spreadsheet: PickedDriveSpreadsheet) => void } = {};
  const disposePicker = vi.fn();
  vi.mocked(mountGoogleDriveSpreadsheetPicker).mockImplementation(async (container, _session, _folderId, onPicked) => {
    pickerCallbacks.onPicked = onPicked;
    const iframe = document.createElement("iframe");
    iframe.title = "Google Driveのスプレッドシート選択";
    container.appendChild(iframe);
    return { dispose: disposePicker };
  });
  const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/document-library/sync")) {
      return syncResponse();
    }
    if (path.endsWith("/document-library")) {
      return new Response(JSON.stringify({ count: 0, folders: [], documents: [], items: [] }), { status: 200 });
    }
    if (path.endsWith("/google-drive/output-folder")) {
      return new Response(JSON.stringify({
        pickerConfigured: true,
        pickerConfigurationError: null,
        folderId: "drive-root-1",
        folderName: "帳票OCR",
        roots: [{ folderId: "drive-root-1", folderName: "帳票OCR" }],
      }), { status: 200 });
    }
    if (path.endsWith("/google-drive/picker-session")) {
      return new Response(JSON.stringify({
        accessToken: "token",
        developerKey: "developer-key",
        appId: "app-id",
      }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  }));

  render(<DocumentLibraryPage startReading={vi.fn()} openReview={vi.fn()} />);

  expect(await screen.findByText("Google Driveと同期済み")).toBeInTheDocument();
  expect(await screen.findByRole("tab", { name: "独自ライブラリ" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByLabelText("書類を検索")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Google Drive" }));

  const pickerFrame = await screen.findByTitle("Google Driveのスプレッドシート選択");
  expect(screen.queryByLabelText("書類を検索")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Google Driveで開く/ })).toHaveAttribute(
    "href",
    "https://drive.google.com/drive/folders/drive-root-1",
  );

  await waitFor(() => expect(mountGoogleDriveSpreadsheetPicker).toHaveBeenCalledWith(
    expect.any(HTMLElement),
    { accessToken: "token", developerKey: "developer-key", appId: "app-id" },
    "drive-root-1",
    expect.any(Function),
  ));
  fireEvent.click(screen.getByRole("button", { name: "再同期" }));
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([input]) => String(input).endsWith("/document-library/sync"))).toHaveLength(2));
  expect(mountGoogleDriveSpreadsheetPicker).toHaveBeenCalledOnce();
  expect(disposePicker).not.toHaveBeenCalled();
  pickerCallbacks.onPicked?.(spreadsheet);
  await waitFor(() => expect(openSpy).toHaveBeenCalledWith(
    spreadsheet.url,
    "_blank",
    "noopener,noreferrer",
  ));
  expect(screen.getByRole("link", { name: /スプレッドシートを開く/ })).toHaveAttribute("href", spreadsheet.url);

  fireEvent.click(screen.getByRole("tab", { name: "独自ライブラリ" }));
  expect(screen.getByLabelText("書類を検索")).toBeInTheDocument();
  expect(screen.queryByTitle("Google Driveのスプレッドシート選択")).not.toBeInTheDocument();
  expect(disposePicker).toHaveBeenCalledOnce();
});
