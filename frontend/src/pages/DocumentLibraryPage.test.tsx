import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DocumentLibraryPage } from "./DocumentLibraryPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("groups a ledger and starts page-specific reading actions", async () => {
  const startReading = vi.fn();
  const openReview = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    count: 1,
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
  vi.spyOn(libraryCard, "getBoundingClientRect").mockReturnValue({
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
    libraryCard,
    new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 50 }),
  );
  expect(libraryCard.style.getPropertyValue("--card-glare-opacity")).toBe("1");
  expect(libraryCard.style.getPropertyValue("--card-tilt-x")).toBe("2.40deg");
  expect(libraryCard.style.getPropertyValue("--card-tilt-y")).toBe("2.40deg");

  fireEvent.pointerLeave(libraryCard, { pointerType: "mouse" });
  expect(libraryCard.style.getPropertyValue("--card-glare-opacity")).toBe("0");
  expect(libraryCard.style.getPropertyValue("--card-tilt-x")).toBe("0deg");
  expect(libraryCard.style.getPropertyValue("--card-tilt-y")).toBe("0deg");

  fireEvent.click(libraryCard);

  expect(screen.getByRole("heading", { name: "こよみの書類" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "このページを読み直す" })).toHaveLength(2);
  expect(screen.getByRole("link", { name: /スプレッドシートを開く/ })).toHaveAttribute(
    "href",
    "https://docs.google.com/spreadsheets/d/sheet-1/edit",
  );

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
