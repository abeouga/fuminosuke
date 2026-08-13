import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ReviewPage } from "./ReviewPage";

const reading = {
  raw: "sample",
  normalized: null,
  status: "uncertain" as const,
  alternatives: [],
  reviewReason: "check",
  sourceRegionId: "header_identity",
  boundingBox: null,
};

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

test("selects the exact crop and opens the editor when a table cell is clicked", async () => {
  const { container } = render(<ReviewPage jobId="job-1" onClose={() => undefined} />);

  expect(await screen.findByAltText("位置合わせ済み帳票")).toHaveAttribute("src", "/full.jpg");

  fireEvent.click(screen.getByRole("button", { name: "次の要確認（2件）" }));
  expect(await screen.findByAltText("document.cattleName の切り出し")).toHaveAttribute("src", "/correct-crop.jpg");
  fireEvent.click(screen.getByRole("button", { name: "次の要確認（2件）" }));
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
  expect(container.querySelectorAll(".ocr-cell-warning")).toHaveLength(1);

  fireEvent.click(screen.getByRole("button", { name: "帳票全体" }));
  expect(screen.getByAltText("位置合わせ済み帳票")).toHaveAttribute("src", "/full.jpg");
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
          saleCalves: [{ notes: saleNote }],
          pedigree: {},
          calvings: [],
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
