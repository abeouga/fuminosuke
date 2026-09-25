import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type {
  FieldReading,
  OcrDocument,
  OcrFieldPreview,
  OcrResult,
  ReviewItem,
  ReviewStructure,
} from "../types";
import {
  buildReviewDisplayModel,
  hydrateMappedReadings,
  positionReviewPopover,
  ReviewPage,
  swapCollectionRowsAtFixedRegions,
  swapGenericRecordRowsAtFixedRegions,
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

test("builds an additive review model in record, parent, independent priority", () => {
  const field = (path: string, mappingId: string, label: string) => ({
    path,
    label,
    value: { ...reading, sourceRegionId: mappingId },
  });
  const entries = [
    field("customFields.record_city", "mapping-record", "市区町村"),
    field("customFields.parent_a_city", "mapping-parent-a", "市区町村"),
    field("customFields.parent_a_city_detail", "mapping-parent-a", "市区町村詳細"),
    field("customFields.parent_b_city", "mapping-parent-b", "市区町村"),
    field("customFields.unclassified", "mapping-unclassified", "未分類"),
  ];
  const structure = {
    schemaVersion: 1,
    fields: [
      { id: "mapping-record", mappingId: "mapping-record", targetPath: "customFields.record_city", label: "市区町村", inputCellId: "record-cell", relationId: "record-relation" },
      { id: "mapping-parent-a", mappingId: "mapping-parent-a", targetPath: "customFields.parent_a_city", label: "市区町村", inputCellId: "parent-a-cell", relationId: "parent-a-relation" },
      { id: "mapping-parent-b", mappingId: "mapping-parent-b", targetPath: "customFields.parent_b_city", label: "市区町村", inputCellId: "parent-b-cell", relationId: "parent-b-relation" },
    ],
    recordSections: [{
      id: "record-section",
      title: "訪問履歴",
      collectionPath: "genericRecords.visits",
      columns: [{ id: "column-city", label: "市区町村" }, { id: "column-note", label: "備考" }],
      rows: [{
        id: "row-1",
        order: 1,
        cells: [
          { columnId: "column-city", fieldId: "mapping-record" },
          { columnId: "column-note", fieldId: null },
        ],
      }],
    }],
    parentSections: [
      { id: "parent-a", title: "帰省先", fieldIds: ["mapping-parent-a"] },
      { id: "parent-b", title: "緊急連絡先", fieldIds: ["mapping-parent-b"] },
    ],
    independentFieldIds: [],
  } satisfies ReviewStructure;

  const model = buildReviewDisplayModel(entries, [], structure);

  expect(model.recordSections[0].rows[0].cells[0].entry?.path).toBe(
    "customFields.record_city",
  );
  expect(model.recordSections[0].rows[0].cells[1].entry).toBeNull();
  expect(model.parentSections.map((section) => [
    section.id,
    section.entries[0]?.path,
  ])).toEqual([
    ["parent-a", "customFields.parent_a_city"],
    ["parent-b", "customFields.parent_b_city"],
  ]);
  expect(model.independentEntries.map((entry) => entry.path)).toEqual([
    "customFields.parent_a_city_detail",
    "customFields.unclassified",
  ]);
  expect(entries[0].label).toBe("市区町村");

  const repeatedTitleModel = buildReviewDisplayModel(entries, [], {
    ...structure,
    recordSections: [
      structure.recordSections[0],
      { ...structure.recordSections[0], id: "record-section-2" },
    ],
  });
  expect(repeatedTitleModel.recordSections.map((section) => section.title)).toEqual([
    "訪問履歴 1",
    "訪問履歴 2",
  ]);
});

test("groups independent values by structural group id without merging equal labels", () => {
  const entries = [
    ["customFields.furigana_1", "mapping-1", "ヤマダ"],
    ["customFields.furigana_2", "mapping-2", "タロウ"],
    ["customFields.other_furigana", "mapping-3", "ベツジン"],
  ].map(([path, sourceRegionId, raw]) => ({
    path,
    label: "フリガナ",
    value: { ...reading, raw, normalized: raw, sourceRegionId },
  }));
  const structure = {
    schemaVersion: 1,
    fields: [
      ["mapping-1", "customFields.furigana_1", "label-cell-a"],
      ["mapping-2", "customFields.furigana_2", "label-cell-a"],
      ["mapping-3", "customFields.other_furigana", "label-cell-b"],
    ].map(([id, targetPath, groupId]) => ({
      id,
      mappingId: id,
      targetPath,
      label: "フリガナ",
      groupId,
      inputCellId: `${id}-cell`,
      relationId: `${id}-relation`,
    })),
    recordSections: [],
    parentSections: [],
    independentFieldIds: ["mapping-1", "mapping-2", "mapping-3"],
  } satisfies ReviewStructure;

  const model = buildReviewDisplayModel(entries, [], structure);

  expect(model.independentGroups.map((group) => [
    group.id,
    group.entries.map((entry) => [entry.path, entry.value.sourceRegionId]),
  ])).toEqual([
    ["label-cell-a", [
      ["customFields.furigana_1", "mapping-1"],
      ["customFields.furigana_2", "mapping-2"],
    ]],
    ["label-cell-b", [["customFields.other_furigana", "mapping-3"]]],
  ]);
});

test("positions the selected-cell popover inside the visible boundary", () => {
  expect(positionReviewPopover(
    { left: 180, top: 4, right: 220, bottom: 24 },
    { width: 100, height: 60 },
    { left: 8, top: 8, right: 300, bottom: 200 },
  )).toEqual({ left: 150, top: 32, placement: "below" });
  expect(positionReviewPopover(
    { left: 275, top: 170, right: 295, bottom: 190 },
    { width: 100, height: 60 },
    { left: 8, top: 8, right: 300, bottom: 200 },
  )).toEqual({ left: 200, top: 102, placement: "above" });
});

test("swaps every fixed-format row value while keeping each physical crop identity", () => {
  const located = (value: string, sourceRegionId: string, y: number) => ({
    raw: value,
    normalized: value,
    status: "readable" as const,
    alternatives: [`${value}-candidate`],
    reviewReason: null,
    sourceRegionId,
    boundingBox: { x: 10, y, width: 80, height: 20 },
  });
  const row = (label: string, index: number) => Object.fromEntries(
    ["parity", "ageDays", "weightKg", "priceYen", "sireName", "saleDate", "notes"]
      .map((field, fieldIndex) => [
        field,
        located(`${label}-${field}`, `${field}-${index}`, index * 100 + fieldIndex * 20),
      ]),
  );
  const document = {
    schemaVersion: "1.0",
    templateId: "template-1",
    templateVersion: "1",
    document: {},
    saleCalves: [
      row("A", 0),
      row("B", 1),
      row("C", 2),
    ],
    pedigree: {},
    calvings: [],
    customFields: {},
    reviewItems: [{
      code: "CHECK_PRICE",
      path: "saleCalves.1.priceYen",
      message: "確認",
      severity: "warning" as const,
      sourceRegionId: "priceYen-1",
    }],
    processingMetadata: {},
  } satisfies OcrDocument;

  const swapped = swapCollectionRowsAtFixedRegions(document, "saleCalves", 1, 0);

  expect(Object.values(swapped.saleCalves[0]).map((value) => value.raw))
    .toEqual(Object.values(document.saleCalves[1]).map((value) => value.raw));
  expect(Object.values(swapped.saleCalves[1]).map((value) => value.raw))
    .toEqual(Object.values(document.saleCalves[0]).map((value) => value.raw));
  expect(swapped.saleCalves[0].priceYen).toMatchObject({
    raw: "B-priceYen",
    normalized: "B-priceYen",
    alternatives: ["B-priceYen-candidate"],
    status: "readable",
    sourceRegionId: "priceYen-0",
    boundingBox: document.saleCalves[0].priceYen.boundingBox,
  });
  expect(swapped.saleCalves[1].priceYen.sourceRegionId).toBe("priceYen-1");
  expect(swapped.reviewItems[0]).toMatchObject({
    path: "saleCalves.0.priceYen",
    sourceRegionId: "priceYen-0",
  });
  expect(document.saleCalves[0].priceYen.raw).toBe("A-priceYen");
  expect(swapCollectionRowsAtFixedRegions(swapped, "saleCalves", 0, 1).saleCalves
    .map((item) => item.priceYen.raw)).toEqual(["A-priceYen", "B-priceYen", "C-priceYen"]);
  expect(swapCollectionRowsAtFixedRegions(document, "saleCalves", 1, 2).saleCalves
    .map((item) => item.priceYen.raw)).toEqual(["A-priceYen", "C-priceYen", "B-priceYen"]);
  expect(swapCollectionRowsAtFixedRegions(document, "saleCalves", 0, 3)).toBe(document);
  const incompatibleRows = structuredClone(document);
  delete incompatibleRows.saleCalves[1].notes;
  expect(swapCollectionRowsAtFixedRegions(incompatibleRows, "saleCalves", 0, 1))
    .toBe(incompatibleRows);
});

test("swaps generic record recognition payloads without changing row identities", () => {
  const readable = (value: string, sourceRegionId: string, y: number) => ({
    ...reading,
    raw: value,
    normalized: value,
    status: "readable" as const,
    reviewReason: null,
    sourceRegionId,
    boundingBox: { x: 10, y, width: 80, height: 20 },
  });
  const document = {
    schemaVersion: "1.0",
    templateId: "template-1",
    templateVersion: "1",
    document: {},
    saleCalves: [],
    pedigree: {},
    calvings: [],
    customFields: {
      city_1: readable("札幌市", "city-1", 10),
      note_1: readable("要確認", "note-1", 30),
      city_2: readable("旭川市", "city-2", 110),
      note_2: readable("確認済み", "note-2", 130),
    },
    reviewItems: [{
      code: "CHECK_NOTE",
      path: "customFields.note_1",
      message: "確認",
      severity: "warning" as const,
      sourceRegionId: "note-1",
    }],
    processingMetadata: {},
  } satisfies OcrDocument;

  const entries = Object.entries(document.customFields).map(([key, value]) => ({
    path: `customFields.${key}`,
    label: key.startsWith("city") ? "市区町村" : "備考",
    value,
  }));
  const section = {
    id: "visits",
    title: "訪問履歴",
    collectionPath: "genericRecords.visits",
    columns: [{ id: "city", label: "市区町村" }, { id: "note", label: "備考" }],
    rows: [
      { id: "record-row-1", cells: [
        { columnId: "city", entry: entries[0] },
        { columnId: "note", entry: entries[1] },
      ] },
      { id: "record-row-2", cells: [
        { columnId: "city", entry: entries[2] },
        { columnId: "note", entry: entries[3] },
      ] },
    ],
  };

  const swapped = swapGenericRecordRowsAtFixedRegions(document, section, 0, 1);

  expect(swapped.customFields.city_1).toMatchObject({
    raw: "旭川市",
    normalized: "旭川市",
    sourceRegionId: "city-1",
    boundingBox: document.customFields.city_1.boundingBox,
  });
  expect(swapped.customFields.note_1.raw).toBe("確認済み");
  expect(swapped.customFields.city_2.raw).toBe("札幌市");
  expect(swapped.reviewItems[0]).toMatchObject({
    path: "customFields.note_2",
    sourceRegionId: "note-2",
  });
  expect(section.rows.map((item) => item.id)).toEqual(["record-row-1", "record-row-2"]);
  expect(section.id).toBe("visits");
  expect(section.title).toBe("訪問履歴");
  expect(section.collectionPath).toBe("genericRecords.visits");
  const sparseSection = {
    ...section,
    rows: [
      section.rows[0],
      { ...section.rows[1], cells: section.rows[1].cells.slice(0, 1) },
    ],
  };
  expect(swapGenericRecordRowsAtFixedRegions(document, sparseSection, 0, 1)).toBe(document);
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

function reviewResultFixture({
  jobId,
  confirmed = false,
  field = {
    ...reading,
    raw: "OCR初期値",
    sourceRegionId: "value-cell",
    boundingBox: { x: 10, y: 20, width: 100, height: 40 },
  },
  reviewItems = [],
  revision = 1,
  spreadsheetSync,
}: {
  jobId: string;
  confirmed?: boolean;
  field?: FieldReading;
  reviewItems?: ReviewItem[];
  revision?: number;
  spreadsheetSync?: OcrResult["spreadsheetSync"];
}): OcrResult {
  return {
    jobId,
    revision,
    result: {
      schemaVersion: "1.0",
      templateId: "template-review-completion",
      templateVersion: "1",
      document: {},
      saleCalves: [],
      pedigree: {},
      calvings: [],
      customFields: { value: field },
      reviewItems,
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
    confirmed,
    individualId: null,
    fieldPreviews: [{
      targetPath: "customFields.value",
      mappingId: "value-cell",
      artifactUrl: "/value-cell.jpg",
      boundingBox: { x: 10, y: 20, width: 100, height: 40 },
    }],
    spreadsheetSync,
    documentName: "確認書類",
  };
}

test("shows the custom output layout entry as unavailable while it is being implemented", async () => {
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
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-layout/result")) return jsonResponse(resultBody);
    if (path.endsWith("/jobs/job-layout/artifacts")) return jsonResponse({ items: [] });
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<ReviewPage jobId="job-layout" onClose={() => undefined} />);

  expect(await screen.findByText("カスタム出力レイアウト · 実装中")).toBeInTheDocument();
  expect(screen.getByText(/出力先セルを紐付ける設定機能を実装中です/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "カスタム出力用シートを取り込む" }),
  ).toBeDisabled();
  expect(screen.queryByRole("button", { name: "レイアウト用シートを初期出力" })).not.toBeInTheDocument();
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

test("moves and persists complete fixed-format rows while preserving physical crops", async () => {
  const locatedReading = (value: string, sourceRegionId: string, y: number) => ({
    ...reading,
    raw: value,
    normalized: value,
    status: "readable" as const,
    reviewReason: null,
    sourceRegionId,
    boundingBox: { x: 10, y, width: 80, height: 30 },
  });
  const initialDocument = {
    schemaVersion: "1.0",
    templateId: "template-1",
    templateVersion: "1",
    document: {},
    saleCalves: [
      {
        priceYen: locatedReading("100", "price-0", 0),
        notes: locatedReading("一行目", "notes-0", 30),
      },
      {
        priceYen: locatedReading("200", "price-1", 60),
        notes: {
          ...locatedReading("二行目", "notes-1", 90),
          normalized: null,
          status: "uncertain" as const,
          alternatives: ["二列目"],
          reviewReason: "OCR_LOW_CONFIDENCE",
        },
      },
      {
        priceYen: locatedReading("300", "price-2", 120),
        notes: locatedReading("三行目", "notes-2", 150),
      },
    ],
    pedigree: {},
    calvings: [],
    customFields: {},
    reviewItems: [{
      code: "CHECK_NOTE",
      path: "saleCalves.1.notes",
      message: "確認",
      severity: "warning" as const,
      sourceRegionId: "notes-1",
    }],
    processingMetadata: {},
  } satisfies OcrDocument;
  const previews = initialDocument.saleCalves.flatMap((_, index) => [
    {
      targetPath: `saleCalves.${index}.priceYen`,
      mappingId: `price-${index}`,
      artifactUrl: `/price-${index}.png`,
      boundingBox: initialDocument.saleCalves[index].priceYen.boundingBox,
    },
    {
      targetPath: `saleCalves.${index}.notes`,
      mappingId: `notes-${index}`,
      artifactUrl: `/notes-${index}.png`,
      boundingBox: initialDocument.saleCalves[index].notes.boundingBox,
    },
  ]);
  const persistence = { document: null as OcrDocument | null };
  let persistedRevision = 1;
  const resultBody = () => ({
    jobId: "job-row-shift",
    revision: persistedRevision,
    result: persistence.document ?? initialDocument,
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
    fieldPreviews: previews,
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-row-shift/corrections") && init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as {
        baseRevision: number;
        document: OcrDocument["document"];
        saleCalves: OcrDocument["saleCalves"];
        pedigree: OcrDocument["pedigree"];
        calvings: OcrDocument["calvings"];
        customFields: OcrDocument["customFields"];
        reviewItems: OcrDocument["reviewItems"];
      };
      persistedRevision = body.baseRevision + 1;
      persistence.document = {
        ...initialDocument,
        document: body.document,
        saleCalves: body.saleCalves,
        pedigree: body.pedigree,
        calvings: body.calvings,
        customFields: body.customFields,
        reviewItems: body.reviewItems,
      };
      return jsonResponse(resultBody());
    }
    if (path.endsWith("/ocr-jobs/job-row-shift/result")) return jsonResponse(resultBody());
    if (path.endsWith("/jobs/job-row-shift/artifacts")) return jsonResponse({ items: [] });
    return jsonResponse({});
  }));

  const firstRender = render(<ReviewPage jobId="job-row-shift" onClose={() => undefined} />);

  const editMode = await screen.findByRole("button", { name: "結果編集モード" });
  const reorderMode = screen.getByRole("button", { name: "行移動モード" });
  expect(editMode).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("button", { name: "売却子牛一覧 2行目を上へ移動" }))
    .not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "価格 saleCalves.1.priceYen を編集" }));
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "備考 saleCalves.1.notes を編集" }));
  expect(screen.getByLabelText("選択セルの編集")).toBeInTheDocument();

  fireEvent.click(reorderMode);

  expect(screen.queryByLabelText("選択セルの判定")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("選択セルの編集")).not.toBeInTheDocument();
  const firstUp = screen.getByRole("button", { name: "売却子牛一覧 1行目を上へ移動" });
  const secondUp = screen.getByRole("button", { name: "売却子牛一覧 2行目を上へ移動" });
  const thirdDown = screen.getByRole("button", { name: "売却子牛一覧 3行目を下へ移動" });
  expect(firstUp).toBeDisabled();
  expect(secondUp).toBeEnabled();
  expect(thirdDown).toBeDisabled();

  fireEvent.click(secondUp);

  expect(screen.getByRole("button", { name: "価格 saleCalves.0.priceYen を編集" }))
    .toHaveTextContent("200");
  expect(screen.getByRole("button", { name: "備考 saleCalves.0.notes を編集" }))
    .toHaveTextContent("二行目");
  expect(screen.getByRole("button", { name: "備考 saleCalves.0.notes を編集" }).closest("td"))
    .toHaveClass("ocr-cell-warning");
  expect(screen.getByRole("button", { name: "価格 saleCalves.1.priceYen を編集" }))
    .toHaveTextContent("100");
  expect(screen.getByRole("button", { name: "備考 saleCalves.1.notes を編集" }))
    .toHaveTextContent("一行目");
  expect(screen.getByText("2行目を1行目と交換しました。")).toBeInTheDocument();

  fireEvent.click(editMode);
  fireEvent.click(screen.getByRole("button", { name: "価格 saleCalves.0.priceYen を編集" }));
  expect(await screen.findByAltText("saleCalves.0.priceYen の切り出し"))
    .toHaveAttribute("src", "/price-0.png");

  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  expect(await screen.findByText("修正を保存しました。")).toBeInTheDocument();
  expect(persistence.document).not.toBeNull();
  expect(persistence.document?.saleCalves[0]).toMatchObject({
    priceYen: {
      raw: "200",
      normalized: "200",
      status: "readable",
      sourceRegionId: "price-0",
      boundingBox: { x: 10, y: 0, width: 80, height: 30 },
    },
    notes: {
      raw: "二行目",
      normalized: null,
      status: "uncertain",
      alternatives: ["二列目"],
      reviewReason: "OCR_LOW_CONFIDENCE",
      sourceRegionId: "notes-0",
      boundingBox: { x: 10, y: 30, width: 80, height: 30 },
    },
  });
  expect(persistence.document?.reviewItems[0]).toMatchObject({
    path: "saleCalves.0.notes",
    sourceRegionId: "notes-0",
  });

  firstRender.unmount();
  render(<ReviewPage jobId="job-row-shift" onClose={() => undefined} />);
  expect(await screen.findByRole("button", { name: "価格 saleCalves.0.priceYen を編集" }))
    .toHaveTextContent("200");
  expect(screen.getByRole("button", { name: "備考 saleCalves.0.notes を編集" }))
    .toHaveTextContent("二行目");
  expect(screen.getByRole("button", { name: "備考 saleCalves.0.notes を編集" }).closest("td"))
    .toHaveClass("ocr-cell-warning");
  fireEvent.click(screen.getByRole("button", { name: "備考 saleCalves.0.notes を編集" }));
  expect(await screen.findByAltText("saleCalves.0.notes の切り出し"))
    .toHaveAttribute("src", "/notes-0.png");
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
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  expect(screen.queryByLabelText("選択セルの編集")).not.toBeInTheDocument();
  expect(screen.getByLabelText("選択セルの判定").closest(".ocr-review-popover")?.previousElementSibling)
    .toHaveClass("ocr-edit-tables");
  fireEvent.click(screen.getByRole("button", { name: "空欄" }));
  expect(cattleCellButton).toHaveTextContent("空欄");
  fireEvent.click(cattleCellButton);
  fireEvent.click(screen.getByRole("button", { name: "不確実" }));
  expect(cattleCellButton.closest("td")).toHaveClass("ocr-cell-warning");
  fireEvent.click(cattleCellButton);
  fireEvent.click(screen.getByRole("button", { name: "読めた" }));
  expect(screen.getByRole("button", {
    name: "牛名 document.cattleName を編集",
  }).closest("td")).toHaveClass("ocr-cell-ok");
  fireEvent.change(screen.getByRole("slider", { name: "プレビュー表示幅" }), { target: { value: "50" } });
  expect(container.querySelector(".ocr-review-layout")).toHaveStyle("--preview-ratio: 50%");
  fireEvent.doubleClick(cattleCellButton);
  const inlineInput = screen.getByRole("textbox", { name: "牛名 document.cattleName を直接編集" });
  fireEvent.change(inlineInput, { target: { value: "修正した牛名" } });
  fireEvent.keyDown(inlineInput, { key: "Enter" });
  expect(screen.getByRole("button", { name: "牛名 document.cattleName を編集" }))
    .toHaveTextContent("修正した牛名");
  expect(screen.getByRole("button", {
    name: "牛名 document.cattleName を編集",
  }).closest("td")).toHaveClass("ocr-cell-ok");

  fireEvent.click(screen.getByRole("button", { name: "牛名 document.cattleName を編集" }));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByLabelText("選択セルの判定")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "生年月日 document.birthDate を編集" }));
  await waitFor(() => {
    expect(screen.getByAltText("document.birthDate の切り出し")).toHaveAttribute("src", "/birth-date-crop.jpg");
  });
  expect(screen.getByText(/x 380 .* y 120 .* 幅 180 .* 高さ 60/)).toBeInTheDocument();
  expect(screen.getByLabelText("選択セルの編集")).toBeInTheDocument();
  expect(container.querySelectorAll(".ocr-cell-warning")).toHaveLength(0);

  fireEvent.click(screen.getByRole("button", { name: "帳票全体" }));
  expect(screen.getByAltText("位置合わせ済み帳票")).toHaveAttribute("src", "/full.jpg");
  fireEvent.click(screen.getByRole("button", { name: "選択領域" }));
  expect(await screen.findByAltText("document.birthDate の切り出し"))
    .toHaveAttribute("src", "/birth-date-crop.jpg");
});

test("renders generic records, parent sections, and independent fields without duplication", async () => {
  const structuredReading = (raw: string, sourceRegionId: string) => ({
    ...reading,
    raw,
    normalized: raw,
    status: "readable" as const,
    reviewReason: null,
    sourceRegionId,
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-structured/result")) {
      return jsonResponse({
        jobId: "job-structured",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-structured",
          templateVersion: "1",
          document: {},
          saleCalves: [],
          pedigree: {},
          calvings: [],
          customFields: {
            record_city_1: structuredReading("札幌市", "mapping-record-city-1"),
            record_note_1: structuredReading("訪問済み", "mapping-record-note-1"),
            record_city_2: structuredReading("旭川市", "mapping-record-city-2"),
            record_note_2: structuredReading("再訪予定", "mapping-record-note-2"),
            sparse_city_1: structuredReading("帯広市", "mapping-sparse-city-1"),
            sparse_note_1: structuredReading("訪問済み", "mapping-sparse-note-1"),
            sparse_city_2: structuredReading("釧路市", "mapping-sparse-city-2"),
            home_city: structuredReading("函館市", "mapping-home-city"),
            emergency_city: structuredReading("小樽市", "mapping-emergency-city"),
            memo: structuredReading("要連絡", "mapping-memo"),
            memo_2: structuredReading("折返し希望", "mapping-memo-2"),
            memo_3: structuredReading("別用途", "mapping-memo-3"),
          },
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
          ["mapping-record-city-1", "customFields.record_city_1"],
          ["mapping-record-note-1", "customFields.record_note_1"],
          ["mapping-record-city-2", "customFields.record_city_2"],
          ["mapping-record-note-2", "customFields.record_note_2"],
          ["mapping-sparse-city-1", "customFields.sparse_city_1"],
          ["mapping-sparse-note-1", "customFields.sparse_note_1"],
          ["mapping-sparse-city-2", "customFields.sparse_city_2"],
          ["mapping-home-city", "customFields.home_city"],
          ["mapping-emergency-city", "customFields.emergency_city"],
          ["mapping-memo", "customFields.memo"],
          ["mapping-memo-2", "customFields.memo_2"],
          ["mapping-memo-3", "customFields.memo_3"],
        ].map(([mappingId, targetPath], index) => ({
          mappingId,
          targetPath,
          artifactUrl: `/${mappingId}.png`,
          boundingBox: { x: index * 10, y: index * 20, width: 80, height: 30 },
        })),
        reviewStructure: {
          schemaVersion: 1,
          fields: [
            ["mapping-record-city-1", "customFields.record_city_1", "市区町村", "record-city"],
            ["mapping-record-note-1", "customFields.record_note_1", "備考", "record-note"],
            ["mapping-record-city-2", "customFields.record_city_2", "市区町村", "record-city"],
            ["mapping-record-note-2", "customFields.record_note_2", "備考", "record-note"],
            ["mapping-sparse-city-1", "customFields.sparse_city_1", "市区町村", "sparse-city"],
            ["mapping-sparse-note-1", "customFields.sparse_note_1", "備考", "sparse-note"],
            ["mapping-sparse-city-2", "customFields.sparse_city_2", "市区町村", "sparse-city"],
            ["mapping-home-city", "customFields.home_city", "市区町村", "home-city"],
            ["mapping-emergency-city", "customFields.emergency_city", "市区町村", "emergency-city"],
            ["mapping-memo", "customFields.memo", "これまでに力を入れて取り組んだことを、立場・課題・具体的な行動を含めて記載してください", "memo-label-a"],
            ["mapping-memo-2", "customFields.memo_2", "これまでに力を入れて取り組んだことを、立場・課題・具体的な行動を含めて記載してください", "memo-label-a"],
            ["mapping-memo-3", "customFields.memo_3", "これまでに力を入れて取り組んだことを、立場・課題・具体的な行動を含めて記載してください", "memo-label-b"],
          ].map(([id, targetPath, label, groupId]) => ({
            id,
            mappingId: id,
            targetPath,
            label,
            groupId,
            inputCellId: `${id}-cell`,
            relationId: `${id}-relation`,
          })),
          recordSections: [
            {
              id: "visits",
              title: "訪問履歴",
              collectionPath: "genericRecords.visits",
              columns: [
                { id: "city-column", label: "市区町村" },
                { id: "note-column", label: "備考" },
              ],
              rows: [
                {
                  id: "visit-1",
                  order: 1,
                  cells: [
                    { columnId: "city-column", fieldId: "mapping-record-city-1" },
                    { columnId: "note-column", fieldId: "mapping-record-note-1" },
                  ],
                },
                {
                  id: "visit-2",
                  order: 2,
                  cells: [
                    { columnId: "city-column", fieldId: "mapping-record-city-2" },
                    { columnId: "note-column", fieldId: "mapping-record-note-2" },
                  ],
                },
              ],
            },
            {
              id: "sparse-visits",
              title: "疎な履歴",
              collectionPath: "genericRecords.sparseVisits",
              columns: [
                { id: "sparse-city-column", label: "市区町村" },
                { id: "sparse-note-column", label: "備考" },
              ],
              rows: [
                {
                  id: "sparse-visit-1",
                  order: 1,
                  cells: [
                    { columnId: "sparse-city-column", fieldId: "mapping-sparse-city-1" },
                    { columnId: "sparse-note-column", fieldId: "mapping-sparse-note-1" },
                  ],
                },
                {
                  id: "sparse-visit-2",
                  order: 2,
                  cells: [
                    { columnId: "sparse-city-column", fieldId: "mapping-sparse-city-2" },
                    { columnId: "sparse-note-column", fieldId: null },
                  ],
                },
              ],
            },
          ],
          parentSections: [
            { id: "home", title: "帰省先", fieldIds: ["mapping-home-city"] },
            { id: "emergency", title: "緊急連絡先", fieldIds: ["mapping-emergency-city"] },
          ],
          independentFieldIds: ["mapping-memo", "mapping-memo-2", "mapping-memo-3"],
        },
      });
    }
    if (path.endsWith("/jobs/job-structured/artifacts")) {
      return jsonResponse({ items: [] });
    }
    return jsonResponse({});
  }));

  render(<ReviewPage jobId="job-structured" onClose={() => undefined} />);

  const recordSection = await screen.findByRole("region", { name: "訪問履歴" });
  const sparseSection = screen.getByRole("region", { name: "疎な履歴" });
  expect(within(recordSection).getByRole("table", { name: "訪問履歴のレコード" }))
    .toBeInTheDocument();
  expect(within(recordSection).getByText("札幌市")).toBeInTheDocument();
  const homeSection = screen.getByRole("region", { name: "帰省先" });
  const emergencySection = screen.getByRole("region", { name: "緊急連絡先" });
  expect(within(homeSection).getByText("函館市")).toBeInTheDocument();
  expect(within(emergencySection).getByText("小樽市")).toBeInTheDocument();
  const independentSection = screen.getByRole("region", { name: "独立項目" });
  expect(within(independentSection).getByText("要連絡")).toBeInTheDocument();
  const independentHeadings = within(independentSection).getAllByRole("heading", {
    name: "これまでに力を入れて取り組んだことを、立場・課題・具体的な行動を含めて記載してください",
  });
  expect(independentHeadings).toHaveLength(2);
  expect(independentHeadings[0]).toHaveClass("ocr-independent-label");
  expect(independentHeadings[0].closest(".ocr-independent-group")?.querySelectorAll("td"))
    .toHaveLength(2);
  expect(screen.getAllByText("函館市")).toHaveLength(1);

  expect(within(recordSection).queryByRole("button", { name: "訪問履歴 2行目を上へ移動" }))
    .not.toBeInTheDocument();
  expect(within(independentSection).queryByRole("button", { name: /行目を.*移動/ }))
    .not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "行移動モード" }));
  expect(within(recordSection).getByRole("button", { name: "訪問履歴 1行目を上へ移動" }))
    .toBeDisabled();
  expect(within(recordSection).getByRole("button", { name: "訪問履歴 2行目を下へ移動" }))
    .toBeDisabled();
  expect(within(sparseSection).getByRole("button", { name: "疎な履歴 1行目を下へ移動" }))
    .toBeDisabled();
  expect(within(sparseSection).getByRole("button", { name: "疎な履歴 2行目を上へ移動" }))
    .toBeDisabled();
  fireEvent.click(within(recordSection).getByRole("button", {
    name: "訪問履歴 2行目を上へ移動",
  }));
  expect(within(recordSection).getByRole("button", {
    name: "市区町村 customFields.record_city_1 を編集",
  })).toHaveTextContent("旭川市");
  expect(within(recordSection).getByRole("button", {
    name: "備考 customFields.record_note_1 を編集",
  })).toHaveTextContent("再訪予定");
  expect(within(recordSection).getByRole("button", {
    name: "市区町村 customFields.record_city_2 を編集",
  })).toHaveTextContent("札幌市");
  expect(within(homeSection).getByText("函館市")).toBeInTheDocument();
  expect(within(independentSection).getByText("要連絡")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "訪問履歴" })).toBe(recordSection);

  fireEvent.click(screen.getByRole("button", { name: "結果編集モード" }));
  const firstMemo = within(independentSection).getByRole("button", {
    name: /customFields\.memo を編集$/,
  });
  const secondMemo = within(independentSection).getByRole("button", {
    name: /customFields\.memo_2 を編集$/,
  });
  fireEvent.click(firstMemo);
  expect(await screen.findByAltText("customFields.memo の切り出し"))
    .toHaveAttribute("src", "/mapping-memo.png");
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "不確実" }));
  expect(firstMemo.closest("td")).toHaveClass("ocr-cell-warning");
  expect(secondMemo.closest("td")).toHaveClass("ocr-cell-ok");
  fireEvent.doubleClick(firstMemo);
  const memoInput = screen.getByRole("textbox", { name: /customFields\.memo を直接編集$/ });
  fireEvent.change(memoInput, { target: { value: "要連絡・修正済み" } });
  fireEvent.keyDown(memoInput, { key: "Enter" });
  expect(within(independentSection).getByRole("button", {
    name: /customFields\.memo を編集$/,
  })).toHaveTextContent("要連絡・修正済み");
  expect(secondMemo).toHaveTextContent("折返し希望");

  fireEvent.click(within(homeSection).getByRole("button", {
    name: "市区町村 customFields.home_city を編集",
  }));
  expect(await screen.findByAltText("customFields.home_city の切り出し"))
    .toHaveAttribute("src", "/mapping-home-city.png");
  expect(screen.getAllByText(/mapping-home-city/).length).toBeGreaterThan(0);
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

test("keeps confirmed OCR evidence while suppressing review warnings and editors after reload", async () => {
  const warningItem: ReviewItem = {
    code: "OCR_LOW_CONFIDENCE",
    path: "customFields.value",
    message: "OCR由来の確認情報",
    severity: "warning",
    sourceRegionId: "value-cell",
  };
  const confirmedResult = reviewResultFixture({
    jobId: "job-confirmed-reload",
    confirmed: true,
    reviewItems: [warningItem],
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-confirmed-reload/result")) {
      return jsonResponse(confirmedResult);
    }
    if (path.endsWith("/jobs/job-confirmed-reload/artifacts")) {
      return jsonResponse({ items: [{ name: "alignedSource", mediaType: "image/jpeg", url: "/full.jpg" }] });
    }
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    return jsonResponse({ code: "NOT_FOUND", detail: "not found" }, 404);
  }));

  const { container } = render(
    <ReviewPage jobId="job-confirmed-reload" onClose={() => undefined} />,
  );

  const cell = await screen.findByRole("button", { name: "value customFields.value を編集" });
  expect(cell).toHaveTextContent("OCR初期値");
  expect(cell).toHaveTextContent("OK");
  expect(cell.closest("td")).toHaveClass("ocr-cell-ok");
  expect(cell.closest("td")).not.toHaveClass("ocr-cell-warning");
  expect(container.querySelector(".review-summary strong")).toHaveTextContent("0");
  expect(screen.queryByRole("button", { name: /次の要確認/ })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("選択セルの判定")).not.toBeInTheDocument();
  expect(confirmedResult.result.customFields.value).toMatchObject({
    status: "uncertain",
    reviewReason: "check",
  });
  expect(confirmedResult.result.reviewItems).toEqual([warningItem]);

  fireEvent.click(cell);
  expect(await screen.findByAltText("customFields.value の切り出し"))
    .toHaveAttribute("src", "/value-cell.jpg");
  expect(screen.queryByLabelText("選択セルの判定")).not.toBeInTheDocument();
});

test("corrects a symbol through the existing single-choice review editor", async () => {
  const result = reviewResultFixture({ jobId: "job-symbol" });
  result.fieldPreviews[0] = { ...result.fieldPreviews[0], mappingType: "mark",
    markRecognitionMode: "symbol", markSelectionMode: "single",
    markOptions: ["○", "◎", "×", "△"].map(label => ({ label, value: label })) };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-symbol/result")) return jsonResponse(result);
    return jsonResponse({ items: [] });
  }));
  render(<ReviewPage jobId="job-symbol" onClose={() => undefined} />);
  const cell = await screen.findByRole("button", { name: "value customFields.value を編集" });
  fireEvent.click(cell);
  expect(await screen.findByRole("dialog", { name: "valueを選択" })).toHaveClass("generic-choice-modal");
  expect(screen.getByAltText("valueの選択欄")).toHaveAttribute("src", "/value-cell.jpg");
  fireEvent.click(screen.getByRole("button", { name: "○" }));
  fireEvent.click(screen.getByRole("button", { name: "◎" }));
  expect(screen.getByRole("button", { name: "○" })).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(screen.getByRole("button", { name: "この選択を反映" }));
  expect(cell).toHaveTextContent("◎");
  expect(cell).toHaveTextContent("OK");
});

test.each(["manual", "symbol"] as const)("does not render a confirmed %s choice editor while keeping its crop available", async (recognitionMode) => {
  const confirmedResult = reviewResultFixture({
    jobId: "job-confirmed-choice",
    confirmed: true,
  });
  confirmedResult.fieldPreviews[0] = {
    ...confirmedResult.fieldPreviews[0],
    mappingType: "mark",
    markRecognitionMode: recognitionMode,
    markSelectionMode: "single",
    markOptions: [{ label: "対象", value: "target" }],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-confirmed-choice/result")) {
      return jsonResponse(confirmedResult);
    }
    if (path.endsWith("/jobs/job-confirmed-choice/artifacts")) return jsonResponse({ items: [] });
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    return jsonResponse({ code: "NOT_FOUND", detail: "not found" }, 404);
  }));

  render(
    <ReviewPage
      jobId="job-confirmed-choice"
      initialSelectedPath="customFields.value"
      onClose={() => undefined}
    />,
  );

  expect(await screen.findByAltText("customFields.value の切り出し"))
    .toHaveAttribute("src", "/value-cell.jpg");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
  const cell = screen.getByRole("button", { name: "牛名 document.cattleName を編集" });
  expect(cell.closest("td")).toHaveClass("ocr-cell-warning");
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "次の要確認（1件）" })).toBeInTheDocument();
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

test("requires the user to resolve generic manual single and multiple choices", async () => {
  let selectionMode: "single" | "multiple" = "single";
  const manualChoice = {
    ...reading,
    raw: null,
    normalized: null,
    status: "uncertain" as const,
    reviewReason: "MANUAL_CHOICE_REQUIRED",
    sourceRegionId: "return-prefecture-type",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-manual-choice/result")) {
      return jsonResponse({
        jobId: "job-manual-choice",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "1",
          document: {},
          saleCalves: [],
          pedigree: {},
          calvings: [],
          customFields: { returnPrefectureType: manualChoice },
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: true, issues: [] },
        provider: "local-manual-choice",
        modelId: null,
        promptVersion: null,
        apiCallCount: 0,
        inputTokens: null,
        outputTokens: null,
        estimatedCost: null,
        confirmed: false,
        individualId: null,
        fieldPreviews: [{
          targetPath: "customFields.returnPrefectureType",
          mappingId: "return-prefecture-type",
          mappingType: "mark",
          markRecognitionMode: "manual",
          markSelectionMode: selectionMode,
          markOptions: [
            { label: "都", value: "metropolis" },
            { label: "道", value: "circuit" },
            { label: "府", value: "urban-prefecture" },
            { label: "県", value: "prefecture" },
          ],
          artifactUrl: "/return-prefecture-type.png",
          boundingBox: { x: 10, y: 20, width: 300, height: 60 },
        }],
      });
    }
    if (path.endsWith("/jobs/job-manual-choice/artifacts")) return jsonResponse({ items: [] });
    return jsonResponse({});
  }));

  render(<ReviewPage jobId="job-manual-choice" onClose={() => undefined} />);

  const cell = await screen.findByRole("button", {
    name: "returnPrefectureType customFields.returnPrefectureType を編集",
  });
  expect(cell).toHaveTextContent("未入力");
  fireEvent.doubleClick(cell);
  expect(screen.queryByLabelText(
    "returnPrefectureType customFields.returnPrefectureType を直接編集",
  )).not.toBeInTheDocument();

  const dialog = await screen.findByRole("dialog", { name: "returnPrefectureTypeを選択" });
  expect(dialog).toHaveClass("generic-choice-modal");
  expect(screen.getByAltText("returnPrefectureTypeの選択欄"))
    .toHaveAttribute("src", "/return-prefecture-type.png");
  fireEvent.click(screen.getByRole("button", { name: "都" }));
  expect(screen.getByRole("button", { name: "都" }))
    .toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "道" }));
  expect(screen.getByRole("button", { name: "都" }))
    .toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("button", { name: "道" }))
    .toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "この選択を反映" }));

  expect(cell).toHaveTextContent("道");
  expect(cell).toHaveTextContent("OK");

  cleanup();
  selectionMode = "multiple";
  render(<ReviewPage jobId="job-manual-choice" onClose={() => undefined} />);
  const multipleCell = await screen.findByRole("button", {
    name: "returnPrefectureType customFields.returnPrefectureType を編集",
  });
  fireEvent.click(multipleCell);
  fireEvent.click(await screen.findByRole("button", { name: "都" }));
  fireEvent.click(screen.getByRole("button", { name: "道" }));
  expect(screen.getByRole("button", { name: "都" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "道" }))
    .toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "この選択を反映" }));
  expect(multipleCell).toHaveTextContent("都・道");
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
  const cellButton = await screen.findByRole("button", { name: "牛名 document.cattleName を編集" });
  fireEvent.doubleClick(cellButton);
  const inlineInput = screen.getByRole("textbox", { name: "牛名 document.cattleName を直接編集" });
  fireEvent.change(inlineInput, { target: { value: "手修正" } });
  fireEvent.keyDown(inlineInput, { key: "Enter" });
  expect(screen.getByRole("button", { name: "牛名 document.cattleName を編集" }))
    .toHaveTextContent("手修正");
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "読めた" }));
  expect(screen.getByRole("button", {
    name: "牛名 document.cattleName を編集",
  }).closest("td")).toHaveClass("ocr-cell-ok");
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

  await screen.findByRole("button", {
    name: "統一耳標番号 document.unifiedEarTag を編集",
  });
  expect(screen.getAllByText("統一耳標番号").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "統一耳標番号 document.unifiedEarTag を編集" }).closest("td")).toHaveClass("ocr-cell-ok");
  expect(screen.getByRole("button", { name: "牛名 document.cattleName を編集" }).closest("td")).toHaveClass("ocr-cell-ok");
  expect(screen.queryByText("個体識別番号を確認してください")).not.toBeInTheDocument();
  const confirmButton = screen.getByRole("button", { name: "内容を確定" });
  expect(confirmButton).toBeEnabled();
  fireEvent.click(confirmButton);
  expect(screen.getByRole("status", { name: "スプレッドシートを作成中" })).toBeInTheDocument();
  await waitFor(() => {
    expect(confirmationBodies).toEqual([{ baseRevision: 1 }]);
    expect(
      container.querySelector(".revision-conflict-recovery"),
    ).toBeInTheDocument();
  });
  expect(screen.queryByRole("status", { name: "スプレッドシートを作成中" })).not.toBeInTheDocument();
  confirmDialog.mockRestore();
});

test("ends review UI after confirmation while retaining the corrected value", async () => {
  const confirmDialog = vi.spyOn(window, "confirm").mockReturnValue(true);
  const initialResult = reviewResultFixture({ jobId: "job-confirm-success" });
  const correctedField: FieldReading = {
    ...initialResult.result.customFields.value,
    raw: "ユーザー修正値",
    normalized: "ユーザー修正値",
    status: "readable",
    reviewReason: null,
  };
  const correctedResult = reviewResultFixture({
    jobId: "job-confirm-success",
    field: correctedField,
    revision: 2,
  });
  const confirmedResult = reviewResultFixture({
    jobId: "job-confirm-success",
    confirmed: true,
    field: correctedField,
    revision: 2,
    spreadsheetSync: {
      enabled: true,
      status: "synced",
      spreadsheetUrl: "https://docs.google.com/spreadsheets/d/confirmed/edit",
      lastError: null,
    },
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-confirm-success/result")) return jsonResponse(initialResult);
    if (path.endsWith("/ocr-jobs/job-confirm-success/corrections") && init?.method === "PUT") {
      return jsonResponse(correctedResult);
    }
    if (path.endsWith("/ocr-jobs/job-confirm-success/confirm") && init?.method === "POST") {
      return jsonResponse(confirmedResult);
    }
    if (path.endsWith("/jobs/job-confirm-success/artifacts")) return jsonResponse({ items: [] });
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    return jsonResponse({ code: "NOT_FOUND", detail: "not found" }, 404);
  }));

  const { container } = render(
    <ReviewPage jobId="job-confirm-success" onClose={() => undefined} />,
  );

  const cell = await screen.findByRole("button", { name: "value customFields.value を編集" });
  fireEvent.click(cell);
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "読めた" }));
  fireEvent.doubleClick(cell);
  const input = screen.getByRole("textbox", { name: "value customFields.value を直接編集" });
  fireEvent.change(input, { target: { value: "ユーザー修正値" } });
  fireEvent.keyDown(input, { key: "Enter" });

  fireEvent.click(screen.getByRole("button", { name: "内容を確定" }));
  expect(screen.getByRole("status", { name: "スプレッドシートを作成中" })).toBeInTheDocument();
  expect(await screen.findByText("確定済みの結果です")).toBeInTheDocument();
  expect(screen.queryByRole("status", { name: "スプレッドシートを作成中" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("選択セルの判定")).not.toBeInTheDocument();
  const confirmedCell = screen.getByRole("button", { name: "value customFields.value を編集" });
  expect(confirmedCell).toHaveTextContent("ユーザー修正値");
  expect(confirmedCell.closest("td")).not.toHaveClass("ocr-cell-selected");
  expect(confirmedCell.closest("td")).not.toHaveClass("ocr-cell-warning");
  expect(container.querySelector(".review-summary strong")).toHaveTextContent("0");
  confirmDialog.mockRestore();
});

test("keeps review complete when Google Sheets sync fails after confirmation", async () => {
  const confirmDialog = vi.spyOn(window, "confirm").mockReturnValue(true);
  const readableField: FieldReading = {
    ...reading,
    raw: "確定値",
    normalized: "確定値",
    status: "readable",
    reviewReason: null,
    sourceRegionId: "value-cell",
    boundingBox: { x: 10, y: 20, width: 100, height: 40 },
  };
  const initialResult = reviewResultFixture({
    jobId: "job-confirm-sync-failed",
    field: readableField,
  });
  const confirmedResult = reviewResultFixture({
    jobId: "job-confirm-sync-failed",
    confirmed: true,
    field: readableField,
    spreadsheetSync: {
      enabled: true,
      status: "failed",
      spreadsheetUrl: null,
      lastError: "Google Sheetsへ接続できませんでした。",
    },
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-confirm-sync-failed/result")) return jsonResponse(initialResult);
    if (path.endsWith("/ocr-jobs/job-confirm-sync-failed/confirm") && init?.method === "POST") {
      return jsonResponse(confirmedResult);
    }
    if (path.endsWith("/jobs/job-confirm-sync-failed/artifacts")) return jsonResponse({ items: [] });
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    return jsonResponse({ code: "NOT_FOUND", detail: "not found" }, 404);
  }));

  render(<ReviewPage jobId="job-confirm-sync-failed" onClose={() => undefined} />);

  const cell = await screen.findByRole("button", { name: "value customFields.value を編集" });
  fireEvent.click(cell);
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "内容を確定" }));

  expect(await screen.findByText("確定済みの結果です")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "再同期" })).toBeInTheDocument();
  expect(screen.queryByLabelText("選択セルの判定")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "value customFields.value を編集" }))
    .toHaveTextContent("確定値");
  confirmDialog.mockRestore();
});

test("retains the selected review state and corrected value when confirmation fails", async () => {
  const confirmDialog = vi.spyOn(window, "confirm").mockReturnValue(true);
  const readableField: FieldReading = {
    ...reading,
    raw: "OCR初期値",
    normalized: "OCR初期値",
    status: "readable",
    reviewReason: null,
    sourceRegionId: "value-cell",
    boundingBox: { x: 10, y: 20, width: 100, height: 40 },
  };
  const initialResult = reviewResultFixture({
    jobId: "job-confirm-failed",
    field: readableField,
  });
  const correctedResult = reviewResultFixture({
    jobId: "job-confirm-failed",
    field: { ...readableField, raw: "失敗時も保持", normalized: "失敗時も保持" },
    revision: 2,
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-confirm-failed/result")) return jsonResponse(initialResult);
    if (path.endsWith("/ocr-jobs/job-confirm-failed/corrections") && init?.method === "PUT") {
      return jsonResponse(correctedResult);
    }
    if (path.endsWith("/ocr-jobs/job-confirm-failed/confirm") && init?.method === "POST") {
      return jsonResponse({
        code: "CONFIRMATION_FAILED",
        detail: "確定処理に失敗しました。",
      }, 500);
    }
    if (path.endsWith("/jobs/job-confirm-failed/artifacts")) return jsonResponse({ items: [] });
    if (path.endsWith("/sire-names")) return jsonResponse({ items: [] });
    return jsonResponse({ code: "NOT_FOUND", detail: "not found" }, 404);
  }));

  render(<ReviewPage jobId="job-confirm-failed" onClose={() => undefined} />);

  const cell = await screen.findByRole("button", { name: "value customFields.value を編集" });
  fireEvent.click(cell);
  fireEvent.doubleClick(cell);
  const input = screen.getByRole("textbox", { name: "value customFields.value を直接編集" });
  fireEvent.change(input, { target: { value: "失敗時も保持" } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "内容を確定" }));

  expect(screen.getByRole("status", { name: "スプレッドシートを作成中" })).toBeInTheDocument();
  expect(await screen.findByText("確定処理に失敗しました。")).toBeInTheDocument();
  expect(screen.queryByRole("status", { name: "スプレッドシートを作成中" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  const retainedCell = screen.getByRole("button", { name: "value customFields.value を編集" });
  expect(retainedCell).toHaveTextContent("失敗時も保持");
  expect(retainedCell.closest("td")).toHaveClass("ocr-cell-selected");
  expect(screen.getByRole("button", { name: "内容を確定" })).toBeEnabled();
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

  await screen.findByRole("heading", { name: "売却子牛一覧" });
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
  expect(screen.getByLabelText("選択セルの判定")).toBeInTheDocument();
  expect(screen.queryByLabelText("選択セルの編集")).not.toBeInTheDocument();

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
  fireEvent.doubleClick(screen.getByRole("button", {
    name: "牛名 document.cattleName を編集",
  }));
  fireEvent.change(screen.getByRole("textbox", {
    name: "牛名 document.cattleName を直接編集",
  }), {
    target: { value: "手元の修正" },
  });
  fireEvent.keyDown(screen.getByRole("textbox", {
    name: "牛名 document.cattleName を直接編集",
  }), { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));

  expect(await screen.findByRole("heading", {
    name: "別の画面で修正が保存されました",
  })).toBeInTheDocument();
  expect(screen.getByText(/勝手に破棄しません/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "修正を保存" })).toBeDisabled();
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
