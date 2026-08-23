import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { OcrDocument, OcrFieldPreview } from "../types";
import {
  canShiftCollectionColumn,
  hydrateMappedReadings,
  reorderCollectionColumn,
  ReviewPage,
  shiftCollectionColumn,
} from "./ReviewPage";

const reading = {
  raw: "sample",
  normalized: null,
  status: "uncertain" as const,
  alternatives: [],
  reviewReason: "check",
  sourceRegionId: "header_identity",
  boundingBox: null,
};

test("materializes configured collection fields omitted by the OCR provider", () => {
  const document = {
    schemaVersion: "1.0",
    templateId: "template-1",
    templateVersion: "1",
    document: {},
    saleCalves: [],
    pedigree: {},
    calvings: [],
    customFields: {},
    reviewItems: [],
    processingMetadata: {},
  } satisfies OcrDocument;
  const previews = Array.from({ length: 10 }, (_, index) => ({
    targetPath: `saleCalves.${index}.priceYen`,
    mappingId: `price-${index}`,
    artifactUrl: `/price-${index}.png`,
    boundingBox: { x: 10, y: index * 20, width: 50, height: 20 },
  })) satisfies OcrFieldPreview[];

  const hydrated = hydrateMappedReadings(document, previews);

  expect(hydrated.saleCalves).toHaveLength(10);
  expect(hydrated.saleCalves[9].priceYen).toMatchObject({
    status: "blank",
    sourceRegionId: "price-9",
    boundingBox: { x: 10, y: 180, width: 50, height: 20 },
  });
  expect(hydrated.saleCalves[9].sireName).toMatchObject({
    status: "blank",
    sourceRegionId: "unmapped",
  });
  expect(document.saleCalves).toHaveLength(0);
});

test("shifts one collection column without changing adjacent columns or losing review paths", () => {
  const readable = (value: string, sourceRegionId: string) => ({
    ...reading,
    raw: value,
    normalized: value,
    status: "readable" as const,
    reviewReason: null,
    sourceRegionId,
  });
  const blank = {
    ...reading,
    raw: null,
    normalized: null,
    status: "blank" as const,
    reviewReason: null,
    sourceRegionId: "sale-date-0",
  };
  const document = {
    schemaVersion: "1.0",
    templateId: "template-1",
    templateVersion: "1",
    document: {},
    saleCalves: [
      { saleDate: blank, priceYen: readable("100", "price-0") },
      { saleDate: readable("2026/01/10", "sale-date-1"), priceYen: readable("200", "price-1") },
      { saleDate: readable("2026/02/20", "sale-date-2"), priceYen: readable("300", "price-2") },
    ],
    pedigree: {},
    calvings: [],
    customFields: {},
    reviewItems: [{
      code: "CHECK_DATE",
      path: "saleCalves.1.saleDate",
      message: "確認",
      severity: "warning" as const,
      sourceRegionId: "sale-date-1",
    }],
    processingMetadata: {},
  } satisfies OcrDocument;

  expect(canShiftCollectionColumn(document, "saleCalves", "saleDate", "up")).toBe(true);
  const shifted = shiftCollectionColumn(document, "saleCalves", "saleDate", "up");

  expect(shifted.saleCalves.map((row) => row.saleDate.raw)).toEqual([
    "2026/01/10",
    "2026/02/20",
    null,
  ]);
  expect(shifted.saleCalves.map((row) => row.priceYen.raw)).toEqual(["100", "200", "300"]);
  expect(shifted.saleCalves[0].saleDate.sourceRegionId).toBe("sale-date-0");
  expect(shifted.reviewItems[0].path).toBe("saleCalves.0.saleDate");
  expect(document.saleCalves[0].saleDate.raw).toBeNull();
  expect(canShiftCollectionColumn(shifted, "saleCalves", "saleDate", "up")).toBe(false);
  expect(canShiftCollectionColumn(shifted, "saleCalves", "saleDate", "down")).toBe(true);
  expect(shiftCollectionColumn(shifted, "saleCalves", "saleDate", "down").saleCalves
    .map((row) => row.saleDate.raw)).toEqual([null, "2026/01/10", "2026/02/20"]);
});

test("reorders only the dragged column and follows its review metadata", () => {
  const readable = (value: string, sourceRegionId: string) => ({
    ...reading,
    raw: value,
    normalized: value,
    status: "readable" as const,
    reviewReason: null,
    sourceRegionId,
  });
  const document = {
    schemaVersion: "1.0",
    templateId: "template-1",
    templateVersion: "1",
    document: {},
    saleCalves: [
      { saleDate: readable("A", "date-0"), priceYen: readable("100", "price-0") },
      { saleDate: readable("B", "date-1"), priceYen: readable("200", "price-1") },
      { saleDate: readable("C", "date-2"), priceYen: readable("300", "price-2") },
      { saleDate: readable("D", "date-3"), priceYen: readable("400", "price-3") },
    ],
    pedigree: {},
    calvings: [],
    customFields: {},
    reviewItems: [{
      code: "CHECK_DATE",
      path: "saleCalves.1.saleDate",
      message: "確認",
      severity: "warning" as const,
      sourceRegionId: "date-1",
    }],
    processingMetadata: {},
  } satisfies OcrDocument;

  const reordered = reorderCollectionColumn(document, "saleCalves", "saleDate", 3, 1);

  expect(reordered.saleCalves.map((row) => row.saleDate.raw)).toEqual(["A", "D", "B", "C"]);
  expect(reordered.saleCalves.map((row) => row.priceYen.raw)).toEqual(["100", "200", "300", "400"]);
  expect(reordered.saleCalves[1].saleDate.sourceRegionId).toBe("date-1");
  expect(reordered.reviewItems[0].path).toBe("saleCalves.2.saleDate");
  expect(reordered.reviewItems[0].sourceRegionId).toBe("date-2");
  expect(document.saleCalves.map((row) => row.saleDate.raw)).toEqual(["A", "B", "C", "D"]);
  expect(reorderCollectionColumn(document, "saleCalves", "saleDate", 1, 1)).toBe(document);
});

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-1/result")) {
      return new Response(JSON.stringify({
        jobId: "job-1",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "version-1",
          document: {
            cattleName: reading,
            birthDate: {
              ...reading,
              raw: "2020.1.2",
              sourceRegionId: "correct_birth_date_cell",
            },
          },
          saleCalves: [],
          pedigree: {},
          calvings: [],
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: false, issues: [] },
        provider: "openai",
        modelId: "gpt-5-mini",
        promptVersion: "test",
        apiCallCount: 1,
        inputTokens: 1,
        outputTokens: 1,
        estimatedCost: 0.001,
        confirmed: false,
        individualId: null,
        fieldPreviews: [
          {
            targetPath: "document.cattleName",
            mappingId: "correct_cattle_name_cell",
            artifactUrl: "/correct-crop.jpg",
            boundingBox: { x: 120, y: 20, width: 240, height: 80 },
          },
          {
            targetPath: "document.birthDate",
            mappingId: "correct_birth_date_cell",
            artifactUrl: "/birth-date-crop.jpg",
            boundingBox: { x: 380, y: 120, width: 180, height: 60 },
          },
        ],
      }), { status: 200 });
    }
    if (path.endsWith("/jobs/job-1/artifacts")) {
      return new Response(JSON.stringify({ items: [
        { name: "alignedSource", mediaType: "image/jpeg", url: "/full.jpg" },
        { name: "fieldCrop.header_identity.alignedColor", mediaType: "image/jpeg", url: "/wrong-crop.jpg" },
      ] }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("imports the edited Google Sheets layout as a versioned output template", async () => {
  const resultBody = {
    jobId: "job-layout",
    revision: 2,
    result: {
      schemaVersion: "1.0",
      templateId: "template-layout",
      templateVersion: "1",
      document: { cattleName: reading },
      saleCalves: [],
      pedigree: {},
      calvings: [],
      customFields: {},
      reviewItems: [],
      processingMetadata: {},
    },
    validation: { valid: true, issues: [] },
    provider: "mock",
    modelId: null,
    promptVersion: null,
    apiCallCount: 0,
    inputTokens: null,
    outputTokens: null,
    estimatedCost: null,
    confirmed: false,
    individualId: null,
    fieldPreviews: [],
    spreadsheetSync: {
      enabled: true,
      status: "synced",
      spreadsheetUrl: "https://docs.google.com/spreadsheets/d/test/edit",
      lastError: null,
    },
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-layout/result")) return jsonResponse(resultBody);
    if (path.endsWith("/jobs/job-layout/artifacts")) return jsonResponse({ items: [] });
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    if (path.endsWith("/ocr-jobs/job-layout/output-layout/import")) {
      expect(init?.method).toBe("POST");
      return jsonResponse({
        active: true,
        templateVersionId: "version-layout",
        revision: 1,
        sheetName: "帳票レイアウト",
        bindingCount: 4,
        sourceJobId: "job-layout",
        createdAt: "2026-08-20T00:00:00+00:00",
      });
    }
    if (path.endsWith("/ocr-jobs/job-layout/output-layout")) {
      return jsonResponse({
        active: false,
        templateVersionId: "version-layout",
        revision: null,
        sheetName: "帳票レイアウト",
        bindingCount: 0,
        sourceJobId: null,
        createdAt: null,
      });
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<ReviewPage jobId="job-layout" onClose={() => undefined} />);

  expect(await screen.findByText("出力レイアウトは未保存です")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "レイアウト用シートを初期出力" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "シートのレイアウトを取り込む" }));
  expect(
    await screen.findByText("出力レイアウト保存済み · REVISION 1"),
  ).toBeInTheDocument();
  expect(screen.getByText("帳票レイアウトをREVISION 1として保存しました。")).toBeInTheDocument();
});

test("confirms OCR-detected identity before showing the full result table", async () => {
  const makeReading = (value: string, sourceRegionId: string) => ({
    ...reading,
    raw: value,
    normalized: value,
    status: "readable" as const,
    reviewReason: null,
    sourceRegionId,
  });
  const document = {
    schemaVersion: "1.0",
    templateId: "template-4",
    templateVersion: "4",
    document: {
      unifiedEarTag: makeReading("1234567890", "ear-tag-cell"),
      cattleName: makeReading("はなこ", "cattle-name-cell"),
    },
    saleCalves: [],
    pedigree: {},
    calvings: [],
    customFields: {},
    reviewItems: [],
    processingMetadata: {},
  } satisfies OcrDocument;
  const previews = [
    {
      targetPath: "document.unifiedEarTag",
      mappingId: "ear-tag-cell",
      artifactUrl: "/ear-tag-crop.jpg",
      boundingBox: { x: 10, y: 10, width: 200, height: 50 },
    },
    {
      targetPath: "document.cattleName",
      mappingId: "cattle-name-cell",
      artifactUrl: "/cattle-name-crop.jpg",
      boundingBox: { x: 10, y: 70, width: 200, height: 50 },
    },
  ] satisfies OcrFieldPreview[];
  let correctionBody: Record<string, unknown> | null = null;
  const resultBody = {
    jobId: "job-identity",
    revision: 1,
    result: document,
    validation: { valid: true, issues: [] },
    provider: "openai",
    modelId: "gpt-5-mini",
    promptVersion: "test",
    apiCallCount: 1,
    inputTokens: 10,
    outputTokens: 5,
    estimatedCost: 0.001,
    confirmed: false,
    individualId: null,
    fieldPreviews: previews,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-identity/corrections") && init?.method === "PUT") {
      correctionBody = JSON.parse(String(init.body)) as Record<string, unknown>;
      return jsonResponse({
        ...resultBody,
        revision: 2,
        result: {
          ...document,
          document: correctionBody.document,
        },
      });
    }
    if (path.endsWith("/ocr-jobs/job-identity/result")) return jsonResponse(resultBody);
    if (path.endsWith("/jobs/job-identity/artifacts")) {
      return jsonResponse({ items: [{ name: "alignedSource", mediaType: "image/jpeg", url: "/full.jpg" }] });
    }
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    return jsonResponse({});
  }));

  render(
    <ReviewPage
      jobId="job-identity"
      requireIdentityConfirmation
      onClose={() => undefined}
    />,
  );

  expect(await screen.findByRole("heading", { name: "この個体で間違いありませんか？" })).toBeInTheDocument();
  expect(screen.getByAltText("個体識別番号の読み取り範囲")).toHaveAttribute("src", "/ear-tag-crop.jpg");
  expect(screen.getByAltText("牛名の読み取り範囲")).toHaveAttribute("src", "/cattle-name-crop.jpg");
  fireEvent.change(screen.getByLabelText("個体識別番号（統一耳標）"), {
    target: { value: "９８７-６５４３-２１０" },
  });
  fireEvent.change(screen.getByLabelText("牛名"), { target: { value: "さくら" } });
  fireEvent.click(screen.getByRole("button", { name: "この個体で結果表へ進む" }));

  expect(await screen.findByRole("heading", { name: "読み取り結果を確認" })).toBeInTheDocument();
  expect(correctionBody).toMatchObject({
    document: {
      unifiedEarTag: { raw: "9876543210", normalized: "9876543210" },
      cattleName: { raw: "さくら", normalized: "さくら" },
    },
  });
});

test("navigates to Google Sheets after a successful manual resync", async () => {
  const onSpreadsheetNavigate = vi.fn();
  const resultBody = {
    jobId: "job-resync",
    revision: 1,
    result: {
      schemaVersion: "1.0",
      templateId: "template-1",
      templateVersion: "1",
      document: {},
      saleCalves: [],
      pedigree: {},
      calvings: [],
      customFields: {},
      reviewItems: [],
      processingMetadata: {},
    },
    validation: { valid: true, issues: [] },
    provider: "openai",
    modelId: "gpt-5-mini",
    promptVersion: "test",
    apiCallCount: 1,
    inputTokens: 10,
    outputTokens: 5,
    estimatedCost: 0.001,
    confirmed: false,
    individualId: null,
    fieldPreviews: [],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-resync/spreadsheet-sync") && init?.method === "POST") {
      return jsonResponse({
        ...resultBody,
        spreadsheetSync: {
          enabled: true,
          status: "synced",
          spreadsheetUrl: "https://docs.google.com/spreadsheets/d/test/edit",
          lastError: null,
        },
      });
    }
    if (path.endsWith("/ocr-jobs/job-resync/result")) {
      return jsonResponse({
        ...resultBody,
        spreadsheetSync: {
          enabled: true,
          status: "failed",
          spreadsheetUrl: "https://docs.google.com/spreadsheets/d/test/edit",
          lastError: "一時的に同期できませんでした。",
        },
      });
    }
    if (path.endsWith("/jobs/job-resync/artifacts")) {
      return jsonResponse({ items: [] });
    }
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    return jsonResponse({});
  }));

  render(
    <ReviewPage
      jobId="job-resync"
      onClose={() => undefined}
      onSpreadsheetNavigate={onSpreadsheetNavigate}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "再同期" }));

  await waitFor(() => {
    expect(onSpreadsheetNavigate).toHaveBeenCalledWith(
      "https://docs.google.com/spreadsheets/d/test/edit",
    );
  });
});

test("moves an entire result column from its header controls", async () => {
  const readableDate = (value: string, sourceRegionId: string) => ({
    ...reading,
    raw: value,
    normalized: value,
    status: "readable" as const,
    reviewReason: null,
    sourceRegionId,
  });
  const blankDate = {
    ...reading,
    raw: null,
    normalized: null,
    status: "blank" as const,
    reviewReason: null,
    sourceRegionId: "sale-date-0",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-column-shift/result")) {
      return jsonResponse({
        jobId: "job-column-shift",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "1",
          document: {},
          saleCalves: [
            { saleDate: blankDate },
            { saleDate: readableDate("2026/01/10", "sale-date-1") },
            { saleDate: readableDate("2026/02/20", "sale-date-2") },
          ],
          pedigree: {},
          calvings: [],
          customFields: {},
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: true, issues: [] },
        provider: "openai",
        modelId: "gpt-5-mini",
        promptVersion: "test",
        apiCallCount: 1,
        inputTokens: 1,
        outputTokens: 1,
        estimatedCost: 0.001,
        confirmed: false,
        individualId: null,
        fieldPreviews: [0, 1, 2].map((index) => ({
          targetPath: `saleCalves.${index}.saleDate`,
          mappingId: `sale-date-${index}`,
          artifactUrl: `/sale-date-${index}.png`,
          boundingBox: { x: 10, y: index * 30, width: 80, height: 30 },
        })),
      });
    }
    if (path.endsWith("/jobs/job-column-shift/artifacts")) {
      return jsonResponse({ items: [] });
    }
    return jsonResponse({});
  }));

  render(<ReviewPage jobId="job-column-shift" onClose={() => undefined} />);

  const editMode = await screen.findByRole("button", { name: "結果編集モード" });
  const reorderMode = screen.getByRole("button", { name: "ドラッグアンドドロップ移動モード" });
  expect(editMode).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("button", { name: "売却日列を上へ1行移動" })).not.toBeInTheDocument();

  fireEvent.click(reorderMode);

  expect(reorderMode).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("button", { name: "売却日 saleCalves.1.saleDate を編集" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "売却日 saleCalves.1.saleDate を移動" })).toBeInTheDocument();
  const moveUp = screen.getByRole("button", { name: "売却日列を上へ1行移動" });
  const moveDown = screen.getByRole("button", { name: "売却日列を下へ1行移動" });
  expect(moveUp).toBeEnabled();
  expect(moveDown).toBeDisabled();

  fireEvent.click(moveUp);

  expect(screen.getByRole("button", { name: "売却日 saleCalves.0.saleDate を移動" }))
    .toHaveTextContent("2026年1月10日");
  expect(screen.getByRole("button", { name: "売却日 saleCalves.1.saleDate を移動" }))
    .toHaveTextContent("2026年2月20日");
  expect(screen.getByRole("button", { name: "売却日 saleCalves.2.saleDate を移動" }))
    .toHaveTextContent("空欄");
  expect(screen.getByText("売却日列を上へ1行移動しました。")).toBeInTheDocument();
  expect(moveUp).toBeDisabled();
  expect(moveDown).toBeEnabled();

  fireEvent.click(editMode);

  expect(editMode).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("button", { name: "売却日列を下へ1行移動" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "売却日 saleCalves.0.saleDate を編集" }))
    .toHaveTextContent("2026年1月10日");
  fireEvent.click(screen.getByRole("button", { name: "売却日 saleCalves.0.saleDate を編集" }));
  expect(screen.getByAltText("saleCalves.0.saleDate の切り出し"))
    .toHaveAttribute("src", "/sale-date-0.png");
});

test("selects the exact crop and opens the editor when a table cell is clicked", async () => {
  const { container } = render(<ReviewPage jobId="job-1" onClose={() => undefined} />);

  expect(await screen.findByAltText("位置合わせ済み帳票")).toHaveAttribute("src", "/full.jpg");

  fireEvent.click(screen.getByRole("button", { name: "次の要確認（1件）" }));
  expect(await screen.findByAltText("document.cattleName の切り出し")).toHaveAttribute("src", "/correct-crop.jpg");
  fireEvent.click(screen.getByRole("button", { name: "生年月日 document.birthDate を編集" }));
  expect(await screen.findByAltText("document.birthDate の切り出し")).toHaveAttribute("src", "/birth-date-crop.jpg");

  const cattleCellButton = screen.getByRole("button", { name: "牛名 document.cattleName を編集" });
  expect(cattleCellButton.closest("td")).toHaveClass("ocr-cell-warning");
  fireEvent.click(cattleCellButton);
  await waitFor(() => {
    expect(screen.getByAltText("document.cattleName の切り出し")).toHaveAttribute("src", "/correct-crop.jpg");
  });
  expect(screen.getByText(/x 120 .* y 20 .* 幅 240 .* 高さ 80/)).toBeInTheDocument();
  expect(screen.getByLabelText("選択セルの編集")).toBeInTheDocument();
  expect(screen.getAllByRole("textbox")).toHaveLength(2);
  fireEvent.change(screen.getByRole("slider", { name: "プレビュー表示幅" }), { target: { value: "50" } });
  expect(container.querySelector(".ocr-review-layout")).toHaveStyle("--preview-ratio: 50%");
  fireEvent.change(screen.getAllByRole("textbox")[0], { target: { value: "修正した牛名" } });
  expect(cattleCellButton).toHaveTextContent("修正した牛名");
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "readable" } });
  expect(cattleCellButton.closest("td")).toHaveClass("ocr-cell-ok");

  fireEvent.click(screen.getByRole("button", { name: "生年月日 document.birthDate を編集" }));
  await waitFor(() => {
    expect(screen.getByAltText("document.birthDate の切り出し")).toHaveAttribute("src", "/birth-date-crop.jpg");
  });
  expect(screen.getByText(/x 380 .* y 120 .* 幅 180 .* 高さ 60/)).toBeInTheDocument();
  expect(container.querySelectorAll(".ocr-cell-warning")).toHaveLength(0);

  fireEvent.click(screen.getByRole("button", { name: "帳票全体" }));
  expect(screen.getByAltText("位置合わせ済み帳票")).toHaveAttribute("src", "/full.jpg");
});

test("reports a missing persisted preview image instead of showing a silent blank", async () => {
  render(<ReviewPage jobId="job-1" onClose={() => undefined} />);

  const image = await screen.findByAltText("位置合わせ済み帳票");
  fireEvent.error(image);

  expect(screen.getByText("画像表示のみ利用できません")).toBeInTheDocument();
  expect(screen.getByText(/DBを別PCから移した旧データ/)).toBeInTheDocument();
});

test("opens the correction cell requested by a Google Sheets deep link", async () => {
  render(
    <ReviewPage
      jobId="job-1"
      initialSelectedPath="document.birthDate"
      onClose={() => undefined}
    />,
  );

  expect(await screen.findByAltText("document.birthDate の切り出し"))
    .toHaveAttribute("src", "/birth-date-crop.jpg");
  expect(screen.getByLabelText("選択セルの編集")).toBeInTheDocument();
});

test("reopens a confirmed result when requested by a Google Sheets correction link", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const confirmed = path.endsWith("/ocr-jobs/job-sheet-link/result");
    if (confirmed || path.endsWith("/ocr-jobs/job-sheet-link/reopen")) {
      return jsonResponse({
        jobId: "job-sheet-link",
        revision: 4,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "1",
          document: { cattleName: { ...reading, raw: "花子", normalized: "花子" } },
          saleCalves: [],
          pedigree: {},
          calvings: [],
          customFields: {},
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: true, issues: [] },
        provider: "mock",
        modelId: null,
        promptVersion: null,
        apiCallCount: 0,
        inputTokens: null,
        outputTokens: null,
        estimatedCost: null,
        confirmed,
        individualId: "1234567890",
        fieldPreviews: [{
          targetPath: "document.cattleName",
          mappingId: "header_identity",
          artifactUrl: "/cattle-name.jpg",
          boundingBox: null,
        }],
      });
    }
    if (path.endsWith("/jobs/job-sheet-link/artifacts")) {
      return jsonResponse({ items: [] });
    }
    return jsonResponse({ items: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <ReviewPage
      jobId="job-sheet-link"
      initialSelectedPath="document.cattleName"
      autoReopenConfirmed
      onClose={() => undefined}
    />,
  );

  expect(await screen.findByRole("button", { name: "修正を保存" })).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => (
    String(input).endsWith("/ocr-jobs/job-sheet-link/reopen")
  ))).toBe(true);
});

test("opens a full-screen six-choice editor for a calving mark cell", async () => {
  const blank = {
    ...reading,
    raw: null,
    normalized: null,
    status: "blank" as const,
    reviewReason: null,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-mark/result")) {
      return jsonResponse({
        jobId: "job-mark",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "1",
          document: {},
          saleCalves: [],
          pedigree: {},
          calvings: [{ outcome: blank, sex: blank }],
          customFields: {},
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: true, issues: [] },
        provider: "local-mark-detector",
        modelId: null,
        promptVersion: null,
        apiCallCount: 0,
        inputTokens: null,
        outputTokens: null,
        estimatedCost: null,
        confirmed: false,
        individualId: null,
        fieldPreviews: [
          {
            targetPath: "calvings.0.outcome",
            mappingId: "calving-mark-outcome",
            mappingType: "mark",
            markOptions: [
              { label: "双", value: "twin" },
              { label: "早", value: "premature" },
              { label: "流", value: "miscarriage" },
              { label: "死", value: "stillbirth" },
            ],
            artifactUrl: "/calving-mark.png",
            boundingBox: { x: 10, y: 20, width: 300, height: 60 },
          },
          {
            targetPath: "calvings.0.sex",
            mappingId: "calving-mark-sex",
            mappingType: "mark",
            markOptions: [
              { label: "♂", value: "male" },
              { label: "♀", value: "female" },
            ],
            artifactUrl: "/calving-mark.png",
            boundingBox: { x: 10, y: 20, width: 300, height: 60 },
          },
        ],
      });
    }
    if (path.endsWith("/jobs/job-mark/artifacts")) return jsonResponse({ items: [] });
    return jsonResponse({});
  }));

  render(<ReviewPage jobId="job-mark" onClose={() => undefined} />);

  fireEvent.click(await screen.findByRole("button", { name: "結果 calvings.0.outcome を編集" }));
  const dialog = await screen.findByRole("dialog", { name: "1行目の分娩状況を修正" });
  expect(dialog).toHaveClass("calving-mark-modal");
  expect(screen.getByAltText("分娩状況の読み取り矩形")).toHaveAttribute("src", "/calving-mark.png");

  fireEvent.click(screen.getByRole("button", { name: "双 双子" }));
  fireEvent.click(screen.getByRole("button", { name: "♂ 雄" }));
  expect(screen.getByRole("button", { name: "双 双子" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "♂ 雄" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "この選択を反映" }));

  expect(screen.getByRole("button", { name: "結果 calvings.0.outcome を編集" })).toHaveTextContent("双");
  expect(screen.getByRole("button", { name: "性別 calvings.0.sex を編集" })).toHaveTextContent("♂");
});

test("keeps the normalized value in sync when a readable raw value is corrected", async () => {
  const readableWithAlternative = {
    ...reading,
    raw: "誤読",
    normalized: "誤読",
    status: "readable",
    alternatives: ["正読"],
    reviewReason: "候補を確認",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-sync/result")) {
      return new Response(JSON.stringify({
        jobId: "job-sync",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "1",
          document: { cattleName: readableWithAlternative },
          saleCalves: [],
          pedigree: {},
          calvings: [],
          customFields: {},
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: true, issues: [] },
        provider: "openai",
        modelId: "gpt-5-mini",
        promptVersion: "test",
        apiCallCount: 1,
        inputTokens: 1,
        outputTokens: 1,
        estimatedCost: 0.001,
        confirmed: false,
        individualId: null,
        fieldPreviews: [],
      }), { status: 200 });
    }
    if (path.endsWith("/jobs/job-sync/artifacts")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(<ReviewPage jobId="job-sync" onClose={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "牛名 document.cattleName を編集" }));
  const [rawInput, normalizedInput] = screen.getAllByRole("textbox");
  fireEvent.change(rawInput, { target: { value: "手修正" } });
  expect(normalizedInput).toHaveValue("手修正");
  expect(screen.getByRole("button", { name: "牛名 document.cattleName を編集" })).toHaveTextContent("手修正");

  fireEvent.click(screen.getByRole("button", { name: "正読" }));
  expect(rawInput).toHaveValue("正読");
  expect(normalizedInput).toHaveValue("正読");
  expect(screen.getByRole("combobox")).toHaveValue("readable");
  expect(screen.queryByText(/確認理由/)).not.toBeInTheDocument();
});

test("converts an era date and offers only male or female note prefixes", async () => {
  const birthDate = {
    ...reading,
    raw: "26 9 27 生",
    reviewReason: "BIRTH_DATE_ERA_REQUIRED",
    sourceRegionId: "birth_date_cell",
  };
  const saleNote = {
    ...reading,
    raw: "る",
    reviewReason: "SALE_NOTE_SEX_SYMBOL_UNRESOLVED",
    sourceRegionId: "sale_note_cell",
  };
  const saleDate = {
    ...reading,
    raw: "30 8-10",
    reviewReason: "BIRTH_DATE_ERA_REQUIRED",
    sourceRegionId: "sale_date_cell",
  };
  const dottedSaleDate = {
    ...reading,
    raw: "5.11.16",
    reviewReason: "BIRTH_DATE_ERA_REQUIRED",
    sourceRegionId: "sale_date_dotted_cell",
  };
  const calvingDate = {
    ...reading,
    raw: "28 7 25",
    reviewReason: "BIRTH_DATE_ERA_REQUIRED",
    sourceRegionId: "calving_date_cell",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-era/result")) {
      return jsonResponse({
        jobId: "job-era",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "1",
          document: { birthDate },
          saleCalves: [
            { saleDate, notes: saleNote },
            { saleDate: dottedSaleDate },
          ],
          pedigree: {},
          calvings: [{ calvingDate }],
          customFields: {},
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: false, issues: [] },
        provider: "openai",
        modelId: "gpt-5-mini",
        promptVersion: "test",
        apiCallCount: 1,
        inputTokens: 1,
        outputTokens: 1,
        estimatedCost: 0.001,
        confirmed: false,
        individualId: null,
        fieldPreviews: [],
      });
    }
    if (path.endsWith("/jobs/job-era/artifacts")) {
      return jsonResponse({ items: [] });
    }
    return jsonResponse({});
  }));

  render(<ReviewPage jobId="job-era" onClose={() => undefined} />);

  const birthCell = await screen.findByRole("button", {
    name: "生年月日 document.birthDate を編集",
  });
  fireEvent.click(birthCell);
  expect(screen.getByLabelText("元号年・月・日（数字のみ）")).toHaveValue("26 9 27");
  fireEvent.change(screen.getByLabelText("元号"), { target: { value: "heisei" } });
  expect(screen.getByLabelText("西暦表記")).toHaveValue("2014年9月27日");
  expect(birthCell).toHaveTextContent("2014年9月27日");

  const calvingDateCell = screen.getByRole("button", {
    name: "分娩日 calvings.0.calvingDate を編集",
  });
  fireEvent.click(calvingDateCell);
  expect(screen.getByLabelText("元号年・月・日（数字のみ）")).toHaveValue("28 7 25");
  expect(screen.getByLabelText("元号")).toHaveValue("heisei");
  fireEvent.change(screen.getByLabelText("元号"), { target: { value: "heisei" } });
  expect(screen.getByLabelText("西暦表記")).toHaveValue("2016年7月25日");
  expect(calvingDateCell).toHaveTextContent("2016年7月25日");

  fireEvent.change(screen.getByLabelText("元号年・月・日（数字のみ）"), {
    target: { value: "7 7 25" },
  });
  expect(screen.getByLabelText("元号")).toHaveValue("reiwa");
  expect(screen.getByLabelText("西暦表記")).toHaveValue("2025年7月25日");
  expect(calvingDateCell).toHaveTextContent("2025年7月25日");

  const saleDateCell = screen.getByRole("button", {
    name: "売却日 saleCalves.0.saleDate を編集",
  });
  expect(saleDateCell).toHaveTextContent("2018年8月10日");
  expect(screen.getByRole("button", {
    name: "売却日 saleCalves.1.saleDate を編集",
  })).toHaveTextContent("2023年11月16日");
  fireEvent.click(saleDateCell);
  expect(screen.getByLabelText("元号年・月・日（数字のみ）")).toHaveValue("30 8 10");
  expect(screen.getByLabelText("元号")).toHaveValue("heisei");
  expect(screen.getByLabelText("西暦表記")).toHaveValue("2018年8月10日");

  const noteCell = screen.getByRole("button", {
    name: "備考 saleCalves.0.notes を編集",
  });
  fireEvent.click(noteCell);
  expect(screen.getByRole("button", { name: "♂" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "♀" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "♂" }));
  expect(noteCell).toHaveTextContent("♂");
  expect(screen.queryByText(/備考の先頭を♂または♀/)).not.toBeInTheDocument();
});

test("allows confirmation with the manually entered individual ID", async () => {
  const confirmationBodies: Array<Record<string, unknown>> = [];
  const confirmDialog = vi.spyOn(window, "confirm").mockReturnValue(true);
  const manualIdentity = {
    raw: "0000000001",
    normalized: "0000000001",
    status: "readable" as const,
    alternatives: [],
    reviewReason: null,
    sourceRegionId: "manual_mother_individual_id",
    boundingBox: null,
  };
  const mappedCattleName = {
    raw: "よみ",
    normalized: "よみ",
    status: "readable" as const,
    alternatives: [],
    reviewReason: null,
    sourceRegionId: "cattle_name_cell",
    boundingBox: null,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-manual-id/result")) {
      return new Response(JSON.stringify({
        jobId: "job-manual-id",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "1",
          document: {
            unifiedEarTag: manualIdentity,
            cattleName: mappedCattleName,
          },
          saleCalves: [],
          pedigree: {},
          calvings: [],
          customFields: {},
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: true, issues: [] },
        provider: "openai",
        modelId: "gpt-5-mini",
        promptVersion: "test",
        apiCallCount: 1,
        inputTokens: 1,
        outputTokens: 1,
        estimatedCost: 0.001,
        confirmed: false,
        individualId: "0000000001",
        fieldPreviews: [{
          targetPath: "document.cattleName",
          mappingId: "cattle_name_cell",
          artifactUrl: "/cattle-name.jpg",
          boundingBox: { x: 10, y: 20, width: 100, height: 40 },
        }],
      }), { status: 200 });
    }
    if (path.endsWith("/jobs/job-manual-id/artifacts")) {
      return new Response(JSON.stringify({
        items: [{ name: "alignedSource", mediaType: "image/jpeg", url: "/full.jpg" }],
      }), { status: 200 });
    }
    if (path.endsWith("/ocr-jobs/job-manual-id/confirm")) {
      confirmationBodies.push(
        JSON.parse(String(init?.body)) as Record<string, unknown>,
      );
      return jsonResponse({
        code: "REVISION_CONFLICT",
        detail: "別の画面で結果が更新されました。",
      }, 409);
    }
    return new Response("{}", { status: 200 });
  }));

  const { container } = render(
    <ReviewPage jobId="job-manual-id" onClose={() => undefined} />,
  );

  expect(await screen.findByText("意味づけ済み項目のみ表示")).toBeInTheDocument();
  expect(screen.getAllByText("統一耳標番号").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "統一耳標番号 document.unifiedEarTag を編集" }).closest("td")).toHaveClass("ocr-cell-ok");
  expect(screen.getByRole("button", { name: "牛名 document.cattleName を編集" }).closest("td")).toHaveClass("ocr-cell-ok");
  expect(screen.queryByText("個体識別番号を確認してください")).not.toBeInTheDocument();
  const confirmButton = screen.getByRole("button", { name: "内容を確定" });
  expect(confirmButton).toBeEnabled();
  fireEvent.click(confirmButton);
  await waitFor(() => {
    expect(confirmationBodies).toEqual([{ baseRevision: 1 }]);
    expect(
      container.querySelector(".revision-conflict-recovery"),
    ).toBeInTheDocument();
  });
  confirmDialog.mockRestore();
});

test("shows only the twenty fields mapped by the template", async () => {
  const mappedReading = {
    raw: "100",
    normalized: "100",
    status: "readable",
    alternatives: [],
    reviewReason: null,
    sourceRegionId: "mapped",
    boundingBox: null,
  };
  const blankReading = {
    raw: null,
    normalized: null,
    status: "blank",
    alternatives: [],
    reviewReason: null,
    sourceRegionId: "unmapped",
    boundingBox: null,
  };
  const saleCalves = Array.from({ length: 10 }, (_, index) => ({
    parity: blankReading,
    ageDays: blankReading,
    weightKg: blankReading,
    priceYen: { ...mappedReading, sourceRegionId: `price_${index}` },
    sireName: { ...mappedReading, raw: `種雄牛${index + 1}`, normalized: `種雄牛${index + 1}`, sourceRegionId: `sire_${index}` },
    saleDate: blankReading,
    notes: blankReading,
  }));
  const fieldPreviews = Array.from({ length: 10 }, (_, index) => [
    {
      targetPath: `saleCalves.${index}.priceYen`,
      mappingId: `price_${index}`,
      artifactUrl: `/price-${index}.jpg`,
      boundingBox: { x: 10, y: index * 20, width: 50, height: 20 },
      recordGroupId: `row_${index + 1}`,
      fieldKey: "priceYen",
    },
    {
      targetPath: `saleCalves.${index}.sireName`,
      mappingId: `sire_${index}`,
      artifactUrl: `/sire-${index}.jpg`,
      boundingBox: { x: 70, y: index * 20, width: 50, height: 20 },
      recordGroupId: `row_${index + 1}`,
      fieldKey: "sireName",
    },
  ]).flat();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-20/result")) {
      return new Response(JSON.stringify({
        jobId: "job-20",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-20",
          templateVersion: "1",
          document: {
            unifiedEarTag: blankReading,
            cattleName: blankReading,
            birthDate: blankReading,
            registrationNumber: blankReading,
            coatColor: blankReading,
            breeder: blankReading,
            owner: blankReading,
          },
          saleCalves,
          pedigree: {
            father: blankReading,
            mother: blankReading,
            paternalGrandfather: blankReading,
            paternalGrandmother: blankReading,
            maternalGrandfather: blankReading,
            maternalGrandmother: blankReading,
          },
          calvings: [],
          customFields: {},
          reviewItems: [{
            code: "INDIVIDUAL_ID_REQUIRED",
            path: "document.unifiedEarTag",
            message: "未設定",
            severity: "error",
            sourceRegionId: "unmapped",
          }],
          processingMetadata: {},
        },
        validation: { valid: false, issues: [] },
        provider: "openai",
        modelId: "gpt-5-mini",
        promptVersion: "test",
        apiCallCount: 1,
        inputTokens: 1,
        outputTokens: 1,
        estimatedCost: 0.001,
        confirmed: false,
        individualId: null,
        fieldPreviews,
        recordStructure: {
          layoutSchemaVersion: 3,
          verified: true,
          canonicalWidth: 200,
          canonicalHeight: 240,
          sections: [{
            id: "sale",
            title: "売却子牛一覧",
            collectionPath: "saleCalves",
            recordType: "saleCalf",
            recordLabelCellIds: [],
            parentLabelCellId: "sale_label",
            source: "manual",
          }],
          groups: Array.from({ length: 10 }, (_, index) => ({
            id: `row_${index + 1}`,
            sectionId: "sale",
            order: index + 1,
            x: 5,
            y: index * 20,
            width: 130,
            height: 20,
            inputCellIds: [`price_${index}`, `sire_${index}`],
            recordKeyField: "parity",
            recordKeyValue: String(index + 1),
            source: "manual",
          })),
          fieldBindings: [],
        },
      }), { status: 200 });
    }
    if (path.endsWith("/jobs/job-20/artifacts")) {
      return new Response(JSON.stringify({
        items: [{ name: "alignedSource", mediaType: "image/jpeg", url: "/full.jpg" }],
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  const { container } = render(<ReviewPage jobId="job-20" onClose={() => undefined} />);

  expect(await screen.findByText("意味づけ済み項目のみ表示")).toBeInTheDocument();
  expect(screen.queryByText("統一耳標番号")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "売却子牛一覧" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /価格 saleCalves\.\d+\.priceYen を編集/ })).toHaveLength(10);
  expect(screen.getAllByRole("button", { name: /種雄牛名 saleCalves\.\d+\.sireName を編集/ })).toHaveLength(10);
  expect(container.querySelectorAll(".ocr-cell-ok")).toHaveLength(20);
  expect(container.querySelectorAll(".ocr-cell-warning")).toHaveLength(0);
  const saleTable = screen.getByRole("heading", { name: "売却子牛一覧" }).closest(".ocr-edit-section")?.querySelector("table");
  expect(saleTable?.querySelectorAll("tbody tr")).toHaveLength(10);
  expect(screen.getByRole("rowheader", { name: "2産次" })).toBeInTheDocument();
  expect(screen.queryByText("父名", { exact: true })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "価格 saleCalves.0.priceYen を編集" }));
  expect(await screen.findByAltText("saleCalves.0.priceYen の切り出し")).toHaveAttribute("src", "/price-0.jpg");
  expect(screen.getByLabelText("選択セルの編集")).toBeInTheDocument();
  expect(screen.getAllByRole("textbox")).toHaveLength(2);

  fireEvent.doubleClick(screen.getByRole("button", { name: "価格 saleCalves.0.priceYen を編集" }));
  const inlineInput = screen.getByRole("textbox", { name: "価格 saleCalves.0.priceYen を直接編集" });
  fireEvent.change(inlineInput, { target: { value: "540000" } });
  fireEvent.keyDown(inlineInput, { key: "Enter" });
  expect(screen.getByRole("button", { name: "価格 saleCalves.0.priceYen を編集" })).toHaveTextContent("540000");
  expect(screen.getAllByText("個体識別番号を確認してください").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "内容を確定" })).toBeDisabled();
});

test("backs up local edits and lets the user restore them on the latest revision", async () => {
  const readable = (value: string, sourceRegionId: string) => ({
    raw: value,
    normalized: value,
    status: "readable" as const,
    alternatives: [],
    reviewReason: null,
    sourceRegionId,
    boundingBox: null,
  });
  const resultResponse = (revision: number, cattleName: string) => ({
    jobId: "job-conflict",
    revision,
    result: {
      schemaVersion: "1.0",
      templateId: "template-conflict",
      templateVersion: "1",
      document: {
        unifiedEarTag: readable("0000000001", "identity"),
        cattleName: readable(cattleName, "cattle_name"),
      },
      saleCalves: [],
      pedigree: {},
      calvings: [],
      customFields: {},
      reviewItems: [],
      processingMetadata: {},
    },
    validation: { valid: true, issues: [] },
    provider: "openai",
    modelId: "gpt-5-mini",
    promptVersion: "test",
    apiCallCount: 1,
    inputTokens: 1,
    outputTokens: 1,
    estimatedCost: 0.001,
    confirmed: false,
    individualId: "0000000001",
    fieldPreviews: [],
  });
  let resultReads = 0;
  let correctionCalls = 0;
  const correctionBodies: Array<Record<string, unknown>> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-conflict/result")) {
      resultReads += 1;
      return jsonResponse(
        resultReads === 1
          ? resultResponse(1, "初期値")
          : resultResponse(2, "別画面の修正"),
      );
    }
    if (path.endsWith("/jobs/job-conflict/artifacts")) {
      return jsonResponse({ items: [] });
    }
    if (path.endsWith("/ocr-jobs/job-conflict/corrections")) {
      correctionCalls += 1;
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      correctionBodies.push(body);
      if (correctionCalls === 1) {
        return jsonResponse({
          code: "REVISION_CONFLICT",
          detail: "別の画面で結果が更新されました。",
        }, 409);
      }
      return jsonResponse(resultResponse(3, "手元の修正"));
    }
    return jsonResponse({});
  }));

  render(<ReviewPage jobId="job-conflict" onClose={() => undefined} />);

  fireEvent.click(await screen.findByRole("button", {
    name: "牛名 document.cattleName を編集",
  }));
  fireEvent.change(screen.getAllByRole("textbox")[0], {
    target: { value: "手元の修正" },
  });
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));

  expect(await screen.findByRole("heading", {
    name: "別の画面で修正が保存されました",
  })).toBeInTheDocument();
  expect(screen.getByText(/勝手に破棄しません/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "修正を保存" })).toBeDisabled();
  expect(screen.getAllByRole("textbox")[0]).toBeDisabled();
  fireEvent.click(screen.getByText("退避した入力内容を表示"));
  expect(screen.getByText(/"raw": "手元の修正"/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "最新結果を再取得" }));
  await waitFor(() => {
    expect(screen.getByRole("button", {
      name: "牛名 document.cattleName を編集",
    })).toHaveTextContent("別画面の修正");
  });

  fireEvent.click(screen.getByRole("button", {
    name: "退避した入力を復元して再編集",
  }));
  expect(screen.getByRole("button", {
    name: "牛名 document.cattleName を編集",
  })).toHaveTextContent("手元の修正");

  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  expect(await screen.findByText("修正を保存しました。")).toBeInTheDocument();
  expect(correctionBodies).toHaveLength(2);
  expect(correctionBodies[1]).toMatchObject({
    baseRevision: 2,
    document: {
      cattleName: {
        raw: "手元の修正",
        normalized: "手元の修正",
      },
    },
  });
});
