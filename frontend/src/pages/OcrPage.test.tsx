import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ToastViewport } from "../components/Toast";
import type { Template } from "../types";
import { OcrPage } from "./OcrPage";

const templates: Template[] = [
  {
    id: "template-1",
    name: "繁殖台帳A",
    documentType: "cow-ledger",
    activeVersionId: "version-12345678",
    createdAt: "2026-07-26T00:00:00Z",
    versions: [
      {
        id: "version-12345678",
        versionNumber: 3,
        mode: "REFERENCE_TEMPLATE",
        status: "ACTIVE",
        sourceJobId: "template-job",
        canonicalWidth: 1400,
        canonicalHeight: 990,
        quality: { layoutVerified: true },
        createdAt: "2026-07-26T00:00:00Z",
      },
    ],
  },
];

beforeEach(() => {
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:preview"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("guides the user to settings when every active template needs verification", () => {
  const openTemplates = vi.fn();
  const unverified = templates.map((template) => ({
    ...template,
    versions: template.versions.map((version) => ({
      ...version,
      quality: { layoutVerified: false },
    })),
  }));
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);

  render(
    <OcrPage
      templates={unverified}
      rememberJob={() => undefined}
      openReview={() => undefined}
      openTemplates={openTemplates}
    />,
  );

  expect(screen.getByRole("heading", {
    name: "テンプレートの設定確認が必要です",
  })).toBeInTheDocument();
  expect(screen.getByText(/「記入欄を再検出」を実行してください/)).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "テンプレート設定を開く" }));
  expect(openTemplates).toHaveBeenCalledOnce();
  expect(fetchMock).not.toHaveBeenCalled();
});

test("shows unverified active templates but does not allow selecting them", async () => {
  const openTemplates = vi.fn();
  const unverifiedVersion = {
    ...templates[0].versions[0],
    id: "version-unverified",
    versionNumber: 4,
    quality: { layoutVerified: false },
  };
  const mixedTemplates = [{
    ...templates[0],
    versions: [...templates[0].versions, unverifiedVersion],
  }];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    canonicalWidth: 1400,
    canonicalHeight: 990,
    mappings: [],
    targets: [],
    validForOcr: false,
    warnings: [],
  }), { status: 200 })));

  render(
    <OcrPage
      templates={mixedTemplates}
      rememberJob={() => undefined}
      openReview={() => undefined}
      openTemplates={openTemplates}
    />,
  );

  expect(screen.getByRole("status")).toHaveTextContent(
    "読み取り欄を変更したテンプレートは選択できません",
  );
  fireEvent.click(screen.getByRole("button", { name: "テンプレート設定を開く" }));
  expect(openTemplates).toHaveBeenCalledOnce();
  const selectedCard = screen.getByRole("button", { name: "繁殖台帳A v3を選択" });
  expect(selectedCard).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("button", { name: "繁殖台帳A v4を選択" })).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});

test("blocks progress when the active template has no mapped cells", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    canonicalWidth: 1400,
    canonicalHeight: 990,
    mappings: [],
    targets: [],
    validForOcr: false,
    warnings: ["読み取りに使用できる意味づけ済みセルがありません。"],
  }), { status: 200 })));
  render(
    <OcrPage
      templates={templates}
      rememberJob={() => undefined}
      openReview={() => undefined}
      openTemplates={() => undefined}
    />,
  );

  expect(screen.getByLabelText("読み取り手順")).toBeInTheDocument();
  expect(await screen.findByText("このテンプレートは現在読み取りに使用できません。テンプレート設定を確認してください。")).toBeInTheDocument();
  expect(screen.queryByText(/読み取りセルの意味づけがありません/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeDisabled();
});

test("protects an in-progress reading wizard from accidental navigation", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    canonicalWidth: 1400,
    canonicalHeight: 990,
    mappings: [{
      mappingId: "field_cattle_name",
      targetPath: "document.cattleName",
      label: "牛名",
      x: 120,
      y: 20,
      width: 240,
      height: 80,
      padding: 4,
      enabled: true,
      modelDetail: "high",
    }],
    targets: [],
    validForOcr: true,
    warnings: [],
  }), { status: 200 })));
  const guard = vi.fn();
  render(
    <OcrPage
      templates={templates}
      rememberJob={() => undefined}
      openReview={() => undefined}
      openTemplates={() => undefined}
      onNavigationGuardChange={guard}
    />,
  );

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));

  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(true));
});

test("shows a retry action when template mappings cannot be loaded", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    code: "MAPPING_LOAD_FAILED",
    detail: "意味づけの取得に失敗しました。",
  }), { status: 500 })));
  render(
    <OcrPage
      templates={templates}
      rememberJob={() => undefined}
      openReview={() => undefined}
      openTemplates={() => undefined}
    />,
  );

  expect(await screen.findByText("テンプレート設定を確認できませんでした。")).toBeInTheDocument();
  expect(screen.queryByText("意味づけの取得に失敗しました。")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "もう一度読み込む" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeDisabled();
});

test("selects a template with a graphical card without exposing implementation details", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    if (String(input).includes("/jobs/template-job/artifacts")) {
      return new Response(JSON.stringify({
        items: [{ name: "cleanPreview", url: "/api/v1/jobs/template-job/artifacts/cleanPreview" }],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({
    canonicalWidth: 1400,
    canonicalHeight: 990,
    mappings: [
      {
        mappingId: "field_cattle_name",
        targetPath: "document.cattleName",
        label: "牛名",
        x: 120,
        y: 20,
        width: 240,
        height: 80,
        padding: 4,
        enabled: true,
        modelDetail: "high",
      },
    ],
    targets: [],
    validForOcr: true,
    warnings: [],
    }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const { container } = render(
    <OcrPage
      templates={templates}
      rememberJob={() => undefined}
      openReview={() => undefined}
      openTemplates={() => undefined}
    />,
  );

  const card = screen.getByRole("button", { name: "繁殖台帳A v3を選択" });
  expect(card).toHaveAttribute("aria-pressed", "true");
  await waitFor(() => expect(card.querySelector("img")).toHaveAttribute("src", "/api/v1/jobs/template-job/artifacts/cleanPreview"));
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes("boundary-image"))).toBe(false);
  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  expect(screen.queryByText(/OpenAIへ送信/)).not.toBeInTheDocument();
  expect(screen.queryByText("document.cattleName · field_cattle_name")).not.toBeInTheDocument();
  expect(screen.queryByText(/1 項目/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  fireEvent.change(input!, {
    target: { files: [new File(["image"], "ledger.png", { type: "image/png" })] },
  });

  fireEvent.click(screen.getByRole("button", { name: "内容確認へ" }));
  expect(screen.getByText("テンプレートに設定した項目を読み取ります")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "読み取りを開始" })).toBeEnabled();
});

test("opens template creation inside reading and reuses the existing segments", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/artifacts")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    return new Response(JSON.stringify({
      canonicalWidth: 1400,
      canonicalHeight: 990,
      mappings: [],
      targets: [],
      validForOcr: false,
      warnings: [],
    }), { status: 200 });
  }));
  render(
    <OcrPage
      templates={templates}
      refreshTemplates={vi.fn().mockResolvedValue(undefined)}
      rememberJob={() => undefined}
      openReview={() => undefined}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "新しいテンプレート" }));

  expect(screen.getByRole("heading", { name: "テンプレート作成・編集" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "新規作成" })).toHaveAttribute("aria-selected", "true");
  const manageTab = screen.getByRole("tab", { name: /既存を確認・修正/ });
  fireEvent.click(manageTab);
  expect(manageTab).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("繁殖台帳A")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "← 読み取りへ戻る" }));
  expect(screen.getByRole("heading", { name: "書類を読み取る" })).toBeInTheDocument();
});

test("keeps the reading draft while directly editing a template from its card", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.includes("/artifacts")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    if (path.endsWith("/layout")) {
      return new Response(JSON.stringify({ code: "NOT_READY", detail: "準備中" }), { status: 503 });
    }
    return new Response(JSON.stringify({
      canonicalWidth: 1400,
      canonicalHeight: 990,
      mappings: [{
        mappingId: "field_cattle_name",
        targetPath: "document.cattleName",
        label: "牛名",
        x: 120,
        y: 20,
        width: 240,
        height: 80,
        padding: 4,
        enabled: true,
        modelDetail: "high",
      }],
      targets: [],
      validForOcr: true,
      warnings: [],
    }), { status: 200 });
  }));
  const refreshTemplates = vi.fn().mockResolvedValue(undefined);
  const { container } = render(
    <OcrPage
      templates={templates}
      refreshTemplates={refreshTemplates}
      rememberJob={() => undefined}
      openReview={() => undefined}
    />,
  );

  const selection = screen.getByRole("button", { name: "繁殖台帳A v3を選択" });
  const card = selection.closest("article")!;
  expect(within(card).getByRole("button", { name: "繁殖台帳Aの設定を確認・修正" })).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: "繁殖台帳Aを削除" })).toBeInTheDocument();

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  fireEvent.change(container.querySelector<HTMLInputElement>('input[type="file"]')!, {
    target: { files: [new File(["image"], "kept-reading.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "戻る" }));
  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aの設定を確認・修正" }));

  expect(screen.getByRole("heading", { name: "読み取る欄を設定" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "← 読み取りへ戻る" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "書類を読み取る" })).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  expect(screen.getByText("選択中:")).toHaveTextContent("kept-reading.png");
});

test("does not delete a template when the card confirmation is cancelled", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    if (String(input).includes("/artifacts")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    return new Response(JSON.stringify({
      canonicalWidth: 1400,
      canonicalHeight: 990,
      mappings: [],
      targets: [],
      validForOcr: false,
      warnings: [],
    }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <OcrPage
      templates={templates}
      refreshTemplates={vi.fn().mockResolvedValue(undefined)}
      rememberJob={() => undefined}
      openReview={() => undefined}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを削除" }));

  expect(confirm).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  expect(screen.getByRole("button", { name: "繁殖台帳A v3を選択" })).toHaveAttribute("aria-pressed", "true");
});

test("reports an archived deletion accurately and clears the selected template", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      return new Response(JSON.stringify({ outcome: "archived" }), { status: 200 });
    }
    if (String(input).includes("/artifacts")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    return new Response(JSON.stringify({
      canonicalWidth: 1400,
      canonicalHeight: 990,
      mappings: [],
      targets: [],
      validForOcr: false,
      warnings: [],
    }), { status: 200 });
  }));
  const refreshTemplates = vi.fn().mockResolvedValue(undefined);
  render(
    <>
      <OcrPage
        templates={templates}
        refreshTemplates={refreshTemplates}
        rememberJob={() => undefined}
        openReview={() => undefined}
      />
      <ToastViewport />
    </>,
  );

  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを削除" }));

  expect(await screen.findByText("テンプレートを読み取り一覧から削除しました")).toBeInTheDocument();
  expect(screen.getByText(/テンプレート本体はアーカイブされています/)).toBeInTheDocument();
  expect(refreshTemplates).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "繁殖台帳A v3を選択" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeDisabled();
});

test("starts OCR without sending a manually entered individual id", async () => {
  const submitted: FormData[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      submitted.push(init.body as FormData);
      return new Response(JSON.stringify({ jobId: "job-1" }), { status: 202 });
    }
    if (String(input).endsWith("/jobs/job-1")) {
      return new Response(JSON.stringify({
        id: "job-1",
        jobType: "EXTRACT_DOCUMENT",
        status: "PROCESSING",
        progressStage: "VALIDATING_INPUT",
        progressPercent: 5,
        attemptNumber: 1,
        error: null,
        createdAt: "2026-07-30T00:00:00Z",
        startedAt: "2026-07-30T00:00:00Z",
        completedAt: null,
      }), { status: 200 });
    }
    return new Response(JSON.stringify({
      canonicalWidth: 1400,
      canonicalHeight: 990,
      mappings: [
        {
          mappingId: "field_cattle_name",
          targetPath: "document.cattleName",
          label: "牛名",
          x: 120,
          y: 20,
          width: 240,
          height: 80,
          padding: 4,
          enabled: true,
          modelDetail: "high",
        },
      ],
      targets: [],
      validForOcr: true,
      warnings: [],
    }), { status: 200 });
  }));
  const rememberJob = vi.fn();
  const { container } = render(
    <OcrPage
      templates={templates}
      rememberJob={rememberJob}
      openReview={() => undefined}
      openTemplates={() => undefined}
    />,
  );

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  fireEvent.change(input!, {
    target: { files: [new File(["image"], "ledger.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "内容確認へ" }));
  fireEvent.click(screen.getByRole("button", { name: "読み取りを開始" }));

  expect(await screen.findByRole("button", { name: "読み取りを開始しました" })).toBeDisabled();
  expect(submitted[0]?.has("motherIndividualId")).toBe(false);
  expect(rememberJob).toHaveBeenCalledWith("job-1");
});

test("navigates to Google Sheets when the initial OCR sync completes", async () => {
  const onSpreadsheetNavigate = vi.fn();
  const openReview = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs") && init?.method === "POST") {
      return new Response(JSON.stringify({ jobId: "job-sheet" }), { status: 202 });
    }
    if (path.endsWith("/jobs/job-sheet")) {
      return new Response(JSON.stringify({
        id: "job-sheet",
        jobType: "EXTRACT_DOCUMENT",
        status: "REVIEW_REQUIRED",
        progressStage: "COMPLETED",
        progressPercent: 100,
        attemptNumber: 1,
        error: null,
        createdAt: "2026-07-30T00:00:00Z",
        startedAt: "2026-07-30T00:00:00Z",
        completedAt: "2026-07-30T00:01:00Z",
      }), { status: 200 });
    }
    if (path.endsWith("/ocr-jobs/job-sheet/result")) {
      return new Response(JSON.stringify({
        jobId: "job-sheet",
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
        spreadsheetSync: {
          enabled: true,
          status: "synced",
          spreadsheetUrl: "https://docs.google.com/spreadsheets/d/test/edit",
          lastError: null,
        },
      }), { status: 200 });
    }
    return new Response(JSON.stringify({
      canonicalWidth: 1400,
      canonicalHeight: 990,
      mappings: [{
        mappingId: "field_cattle_name",
        targetPath: "document.cattleName",
        label: "牛名",
        x: 120,
        y: 20,
        width: 240,
        height: 80,
        padding: 4,
        enabled: true,
        modelDetail: "high",
      }],
      targets: [],
      validForOcr: true,
      warnings: [],
    }), { status: 200 });
  }));

  const { container } = render(
    <OcrPage
      templates={templates}
      rememberJob={() => undefined}
      openReview={openReview}
      openTemplates={() => undefined}
      onSpreadsheetNavigate={onSpreadsheetNavigate}
    />,
  );

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  fireEvent.change(container.querySelector<HTMLInputElement>('input[type="file"]')!, {
    target: { files: [new File(["image"], "ledger.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "内容確認へ" }));
  fireEvent.click(screen.getByRole("button", { name: "読み取りを開始" }));

  await waitFor(() => {
    expect(onSpreadsheetNavigate).toHaveBeenCalledWith(
      "https://docs.google.com/spreadsheets/d/test/edit",
    );
  });
  expect(openReview).not.toHaveBeenCalled();
});

test("reuses the OCR idempotency key for a communication retry and rotates it after input changes", async () => {
  const idempotencyKeys: Array<string | null> = [];
  let createCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs") && init?.method === "POST") {
      createCalls += 1;
      idempotencyKeys.push(new Headers(init.headers).get("Idempotency-Key"));
      if (createCalls < 3) throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify({ jobId: "job-retried" }), { status: 202 });
    }
    if (path.endsWith("/jobs/job-retried")) {
      return new Response(JSON.stringify({
        id: "job-retried",
        jobType: "EXTRACT_DOCUMENT",
        status: "PROCESSING",
        progressStage: "VALIDATING_INPUT",
        progressPercent: 5,
        attemptNumber: 1,
        error: null,
        createdAt: "2026-07-30T00:00:00Z",
        startedAt: "2026-07-30T00:00:00Z",
        completedAt: null,
      }), { status: 200 });
    }
    return new Response(JSON.stringify({
      canonicalWidth: 1400,
      canonicalHeight: 990,
      mappings: [{
        mappingId: "field_cattle_name",
        targetPath: "document.cattleName",
        label: "牛名",
        x: 120,
        y: 20,
        width: 240,
        height: 80,
        padding: 4,
        enabled: true,
        modelDetail: "high",
      }],
      targets: [],
      validForOcr: true,
      warnings: [],
    }), { status: 200 });
  }));
  const { container } = render(
    <>
      <OcrPage
        templates={templates}
        rememberJob={() => undefined}
        openReview={() => undefined}
        openTemplates={() => undefined}
      />
      <ToastViewport />
    </>,
  );

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  fireEvent.change(container.querySelector<HTMLInputElement>('input[type="file"]')!, {
    target: { files: [new File(["image"], "ledger.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "内容確認へ" }));

  fireEvent.click(screen.getByRole("button", { name: "読み取りを開始" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("読み取りを開始できませんでした");
  expect(alert).toHaveTextContent("バックエンドとの接続を確認してください。");
  fireEvent.click(screen.getByRole("button", { name: "読み取りを開始" }));
  await waitFor(() => expect(idempotencyKeys).toHaveLength(2));

  expect(idempotencyKeys[0]).toMatch(/^ocr-/);
  expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);

  fireEvent.click(screen.getByRole("button", { name: "戻る" }));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "内容確認へ" }));
  fireEvent.click(screen.getByRole("button", { name: "画像処理を開始" }));

  await waitFor(() => expect(idempotencyKeys).toHaveLength(3));
  expect(idempotencyKeys[2]).toMatch(/^ocr-/);
  expect(idempotencyKeys[2]).not.toBe(idempotencyKeys[0]);
  expect(await screen.findByRole("button", {
    name: "読み取りを開始しました",
  })).toBeDisabled();
});
