import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { DocumentIdentity, FieldMappingsResponse, Job, LayoutDefinition, Template, TemplateVersion } from "../types";
import { mappingWarningsForIdentity, TemplatePage } from "./TemplatePage";

const createdAt = "2026-07-30T12:00:00Z";

const draftVersion: TemplateVersion = {
  id: "version-draft",
  versionNumber: 2,
  mode: "REFERENCE_TEMPLATE",
  status: "DRAFT",
  sourceJobId: "job-draft",
  canonicalWidth: 300,
  canonicalHeight: 100,
  quality: { layoutVerified: true },
  createdAt,
};

const draftTemplate: Template = {
  id: "template-draft",
  name: "繁殖台帳A",
  documentType: "cow-ledger",
  activeVersionId: null,
  createdAt,
  versions: [draftVersion],
};

const layout: LayoutDefinition = {
  canonicalWidth: 300,
  canonicalHeight: 100,
  detector: "test",
  ocrAvailable: false,
  cells: [{
    id: "identity-input",
    role: "input",
    x: 0,
    y: 0,
    width: 100,
    height: 30,
    confidence: 1,
    text: "",
    textConfidence: null,
    source: "manual",
  }],
  relations: [{
    id: "identity-relation",
    inputCellId: "identity-input",
    labelCellIds: [],
    axis: "horizontal",
    order: 1,
    targetPath: "document.unifiedEarTag",
  }],
  recordStructureVerified: true,
  allowCrossAxisLabels: false,
  warnings: [],
};

beforeEach(() => {
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:preview"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function job(status: string): Job {
  return {
    id: "template-job-1",
    jobType: "PREPARE_TEMPLATE",
    status,
    progressStage: status === "PROCESSING" ? "NORMALIZING_DOCUMENT" : null,
    progressPercent: status === "PROCESSING" ? 10 : 100,
    attemptNumber: 1,
    error: null,
    createdAt,
    startedAt: createdAt,
    completedAt: status === "PROCESSING" ? null : createdAt,
  };
}

function selectTemplateImage(container: HTMLElement) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).not.toBeNull();
  fireEvent.change(input!, {
    target: { files: [new File(["image"], "ledger.png", { type: "image/png" })] },
  });
}

function reviewFetch(
  actionPath?: string,
  actionResponse?: () => Promise<Response>,
  mappingOverrides: Partial<FieldMappingsResponse> = {},
) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(input);
    if (path.endsWith("/jobs/job-draft/artifacts")) {
      return Promise.resolve(jsonResponse({
        items: [{ name: "normalized", mediaType: "image/png", url: "/source.png" }],
      }));
    }
    if (path.endsWith("/template-versions/version-draft/field-mappings")) {
      return Promise.resolve(jsonResponse({
        canonicalWidth: 300,
        canonicalHeight: 100,
        mappings: [{
          mappingId: "identity-field",
          targetPath: "document.unifiedEarTag",
          label: "個体識別番号",
          x: 0,
          y: 0,
          width: 100,
          height: 30,
          padding: 4,
          enabled: true,
          modelDetail: "high",
        }],
        documentIdentity: {
          mappingId: "identity-field",
          targetPath: "document.unifiedEarTag",
          label: "個体識別番号",
          valueType: "digits",
          exactLength: 10,
          matchRole: "primary",
          displayRole: "title",
        },
        documentIdentityFields: [{
          mappingId: "identity-field",
          targetPath: "document.unifiedEarTag",
          label: "個体識別番号",
          valueType: "digits",
          exactLength: 10,
          matchRole: "primary",
          displayRole: "title",
        }],
        targets: [{
          path: "document.unifiedEarTag",
          label: "個体識別番号",
          section: "基本情報",
        }],
        validForOcr: true,
        warnings: [],
        ...mappingOverrides,
      }));
    }
    if (path.endsWith("/template-versions/version-draft/layout")) {
      return Promise.resolve(jsonResponse(layout));
    }
    if (actionPath && path.endsWith(actionPath) && init?.method === "POST" && actionResponse) {
      return actionResponse();
    }
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  });
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function openDraftReview() {
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: "承認して有効化" })).toBeEnabled());
}

test("removes a stale missing-identity warning when the current compound fields are ready", () => {
  const fields: DocumentIdentity[] = [
    {
      mappingId: "cattle-name",
      targetPath: "document.cattleName",
      label: "名号",
      valueType: "text",
      exactLength: null,
      matchRole: "confirmation",
      displayRole: "title",
    },
    {
      mappingId: "individual-id",
      targetPath: "document.unifiedEarTag",
      label: "個体識別番号",
      valueType: "digits",
      exactLength: 10,
      matchRole: "confirmation",
      displayRole: "subtitle",
    },
  ];

  expect(mappingWarningsForIdentity([
    "読み取りに使用できる意味づけ済みセルがありません。",
    "書類を見分ける項目が未設定です。",
  ], fields)).toEqual([
    "読み取りに使用できる意味づけ済みセルがありません。",
  ]);
});

test("treats persisted compound identity fields as a saved template setting", async () => {
  const compoundFields: DocumentIdentity[] = [
    {
      mappingId: "identity-field",
      targetPath: "document.unifiedEarTag",
      label: "個体識別番号",
      valueType: "digits",
      exactLength: 10,
      matchRole: "confirmation",
      displayRole: "subtitle",
    },
    {
      mappingId: "cattle-name",
      targetPath: "document.cattleName",
      label: "名号",
      valueType: "text",
      exactLength: null,
      matchRole: "confirmation",
      displayRole: "title",
    },
  ];
  vi.stubGlobal("fetch", reviewFetch(undefined, undefined, {
    documentIdentity: null,
    documentIdentityFields: compoundFields,
  }));
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));

  expect(await screen.findByLabelText("テンプレート設定の状態"))
    .toHaveTextContent("テンプレート設定保存済み");
  expect(screen.getByRole("button", { name: "承認して有効化" })).toBeEnabled();
});

test("shows only the filled-single and blank-template creation modes in that order", () => {
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  const modeGroup = screen.getByRole("group", { name: "準備方法" });
  const modeButtons = within(modeGroup).getAllByRole("button");
  expect(modeButtons).toHaveLength(2);
  expect(modeButtons[0]).toHaveTextContent("記入済みの一枚から");
  expect(modeButtons[1]).toHaveTextContent("白紙帳票から");
  expect(screen.queryByText(/複数スキャン/)).not.toBeInTheDocument();

  const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
  expect(fileInput).not.toHaveAttribute("multiple");
});

test("separates new creation from existing-template maintenance", () => {
  const { container } = render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  const createTab = screen.getByRole("tab", { name: "新規作成" });
  const manageTab = screen.getByRole("tab", { name: /既存を確認・修正/ });
  const createPanel = container.querySelector<HTMLElement>("#template-workspace-create-panel")!;
  const managePanel = container.querySelector<HTMLElement>("#template-workspace-manage-panel")!;

  expect(manageTab).toHaveAttribute("aria-selected", "true");
  expect(createPanel).toHaveAttribute("hidden");
  expect(managePanel).not.toHaveAttribute("hidden");
  expect(within(managePanel).getByText("繁殖台帳A")).toBeInTheDocument();

  fireEvent.click(createTab);
  expect(createTab).toHaveAttribute("aria-selected", "true");
  expect(createPanel).not.toHaveAttribute("hidden");
  expect(managePanel).toHaveAttribute("hidden");
});

test("keeps new-template input while switching between the segments", () => {
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("tab", { name: "新規作成" }));
  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "入力途中の帳票" },
  });
  fireEvent.click(screen.getByRole("tab", { name: /既存を確認・修正/ }));
  fireEvent.click(screen.getByRole("tab", { name: "新規作成" }));

  expect(screen.getByLabelText("テンプレート名")).toHaveValue("入力途中の帳票");
});

test("configures template-specific candidates by clicking repeated fields on the preview", async () => {
  let savedBody: Record<string, unknown> | null = null;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/jobs/job-draft/artifacts")) {
      return jsonResponse({
        items: [{ name: "normalized", mediaType: "image/png", url: "/source.png" }],
      });
    }
    if (path.endsWith("/template-versions/version-draft/field-mappings")) {
      return jsonResponse({
        canonicalWidth: 300,
        canonicalHeight: 100,
        mappings: [
          { mappingId: "sire-1", targetPath: "saleCalves.0.sireName", label: "種雄牛名", x: 0, y: 0, width: 100, height: 30, padding: 4, enabled: true, modelDetail: "high", mappingType: "text" },
          { mappingId: "sire-2", targetPath: "saleCalves.1.sireName", label: "種雄牛名", x: 0, y: 40, width: 100, height: 30, padding: 4, enabled: true, modelDetail: "high", mappingType: "text" },
        ],
        documentIdentity: null,
        documentIdentityFields: [],
        targets: [],
        validForOcr: true,
        warnings: [],
      });
    }
    if (path.endsWith("/templates/template-draft/candidate-settings")) {
      if (init?.method === "PUT") {
        savedBody = JSON.parse(String(init.body));
        return jsonResponse({
          templateId: "template-draft",
          sets: [{
            id: "candidate-set-1",
            name: "種雄牛名",
            values: ["勝忠平", "光晴重"],
            allowedSuffixes: [],
            bindings: [{ targetPattern: "saleCalves[].sireName", label: "種雄牛名" }],
          }],
        });
      }
      return jsonResponse({ templateId: "template-draft", sets: [] });
    }
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  }));
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aの読み取り候補を設定" }));
  expect(await screen.findByRole("heading", { name: "読み取り候補を設定" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("候補の種類名"), { target: { value: "種雄牛名" } });
  fireEvent.change(screen.getByLabelText("使われる文字"), { target: { value: "勝忠平\n光晴重" } });
  fireEvent.click(screen.getByRole("button", { name: "候補を追加して欄を選ぶ" }));

  const repeatedFields = screen.getAllByRole("button", { name: "種雄牛名に読み取り候補を適用" });
  expect(repeatedFields).toHaveLength(2);
  fireEvent.click(repeatedFields[0]);
  expect(screen.getByText(/1項目に適用/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "読み取り候補を保存" }));

  await waitFor(() => expect(savedBody).toEqual({
    templateVersionId: "version-draft",
    sets: [{
      name: "種雄牛名",
      values: ["勝忠平", "光晴重"],
      allowedSuffixes: [],
      targetPatterns: ["saleCalves[].sireName"],
    }],
  }));
  expect(await screen.findByText("読み取り候補と適用する欄を保存しました。次回の読み取りから使用されます。")).toBeInTheDocument();
});

test("requires template name before starting", () => {
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "" },
  });
  expect(screen.getByRole("button", { name: "テンプレート準備を開始" })).toBeDisabled();

  selectTemplateImage(container);
  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "  " },
  });
  expect(screen.getByRole("button", { name: "テンプレート準備を開始" })).toBeDisabled();
});

test("registers only the current template fields before template preparation", async () => {
  let submittedFieldNames: string[] | null = null;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/template-jobs") && init?.method === "POST") {
      const body = init.body as FormData;
      submittedFieldNames = JSON.parse(String(body.get("fieldNames")));
      return jsonResponse({ jobId: "template-job-1" }, 202);
    }
    if (path.endsWith("/jobs/template-job-1")) return jsonResponse(job("PROCESSING"));
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  }));
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  expect(screen.getByText("保存する項目は未登録です")).toBeInTheDocument();
  expect(screen.queryByText("個体識別番号")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("項目名"), { target: { value: "宛名" } });
  fireEvent.click(screen.getByRole("button", { name: "項目を追加" }));
  fireEvent.change(screen.getByLabelText("項目名"), { target: { value: "金額" } });
  fireEvent.click(screen.getByRole("button", { name: "項目を追加" }));

  expect(screen.getByRole("list", { name: "登録する保存項目" })).toHaveTextContent("宛名");
  expect(screen.getByRole("list", { name: "登録する保存項目" })).toHaveTextContent("金額");
  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "バイク領収書" },
  });
  selectTemplateImage(container);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));

  await waitFor(() => expect(submittedFieldNames).toEqual(["宛名", "金額"]));
});

test("locks template creation after the first job has started", async () => {
  let createCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/template-jobs") && init?.method === "POST") {
      createCalls += 1;
      return jsonResponse({ jobId: "template-job-1" }, 202);
    }
    if (path.endsWith("/jobs/template-job-1")) return jsonResponse(job("PROCESSING"));
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  }));
  const rememberJob = vi.fn();
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn()} rememberJob={rememberJob} />,
  );

  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "乳牛台帳A" },
  });
  selectTemplateImage(container);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));

  const lockedButton = await screen.findByRole("button", { name: "テンプレート準備を開始しました" });
  expect(lockedButton).toBeDisabled();
  fireEvent.click(lockedButton);
  expect(createCalls).toBe(1);
  expect(rememberJob).toHaveBeenCalledTimes(1);
  expect(rememberJob).toHaveBeenCalledWith("template-job-1");
});

test("lets the user confirm four corners after automatic boundary detection fails", async () => {
  let savedBody: unknown = null;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/template-jobs") && init?.method === "POST") {
      return jsonResponse({ jobId: "template-job-1" }, 202);
    }
    if (path.endsWith("/jobs/template-job-1")) {
      return jsonResponse({
        ...job("FAILED"),
        error: {
          code: "DOCUMENT_BOUNDARY_NOT_FOUND",
          message: "自動で書類の範囲を判断できませんでした。",
        },
      });
    }
    if (path.endsWith("/template-jobs/template-job-1/boundary") && init?.method === "PUT") {
      savedBody = JSON.parse(String(init.body));
      return jsonResponse({ ...job("QUEUED"), attemptNumber: 2 });
    }
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  }));
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "バイク領収書" },
  });
  selectTemplateImage(container);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));

  expect(await screen.findByRole("heading", { name: "書類の四隅を指定してください" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "左上の角" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "右上の角" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "右下の角" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "左下の角" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "この範囲で再処理する" }));
  await waitFor(() => expect(savedBody).toEqual({
    corners: [
      { x: 0.03, y: 0.03 },
      { x: 0.97, y: 0.03 },
      { x: 0.97, y: 0.97 },
      { x: 0.03, y: 0.97 },
    ],
  }));
});

test("reuses the template idempotency key for a communication retry and rotates it after input changes", async () => {
  const idempotencyKeys: Array<string | null> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/template-jobs") && init?.method === "POST") {
      idempotencyKeys.push(new Headers(init.headers).get("Idempotency-Key"));
      throw new TypeError("Failed to fetch");
    }
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  }));
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "乳牛台帳A" },
  });
  selectTemplateImage(container);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));
  await waitFor(() => expect(idempotencyKeys).toHaveLength(2));

  expect(idempotencyKeys[0]).toMatch(/^template-/);
  expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);

  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "牛繁殖台帳（差し替え）" },
  });
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));
  await waitFor(() => expect(idempotencyKeys).toHaveLength(3));

  expect(idempotencyKeys[2]).toMatch(/^template-/);
  expect(idempotencyKeys[2]).not.toBe(idempotencyKeys[0]);
});

test("shows a retryable error when the prepared template cannot be found", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/template-jobs") && init?.method === "POST") {
      return jsonResponse({ jobId: "template-job-1" }, 202);
    }
    if (path.endsWith("/jobs/template-job-1")) return jsonResponse(job("REVIEW_REQUIRED"));
    if (path.endsWith("/templates")) return jsonResponse([]);
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  }));
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn().mockResolvedValue(undefined)} rememberJob={vi.fn()} />,
  );

  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "乳牛台帳A" },
  });
  selectTemplateImage(container);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("確認対象のテンプレートを見つけられませんでした。");
  expect(screen.getByRole("button", { name: "確認画面を再取得" })).toBeEnabled();
});

test("identifies same-day drafts by time and a short ID in their accessible names", () => {
  const expectedDateTime = (value: string) => new Date(value).toLocaleString("ja-JP", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const laterDraft: TemplateVersion = {
    ...draftVersion,
    id: "second-draft-id",
    sourceJobId: "job-draft-2",
    createdAt: "2026-07-30T12:05:06Z",
  };
  render(
    <TemplatePage
      templates={[{
        ...draftTemplate,
        versions: [
          { ...draftVersion, id: "first-draft-id" },
          laterDraft,
        ],
      }]}
      refresh={vi.fn()}
      rememberJob={vi.fn()}
    />,
  );

  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.getByText("バージョン履歴（2件）")).toBeInTheDocument();
  fireEvent.click(screen.getByText("バージョン履歴（2件）"));
  const resumeButtons = screen.getAllByRole("button", { name: /確認を再開: 繁殖台帳A/ });
  expect(resumeButtons).toHaveLength(2);
  expect(resumeButtons[0]).toHaveAccessibleName(
    `確認を再開: 繁殖台帳A v2 · 白紙帳票から · ${expectedDateTime(laterDraft.createdAt)} · ID second-d`,
  );
  expect(resumeButtons[1]).toHaveAccessibleName(
    `確認を再開: 繁殖台帳A v2 · 白紙帳票から · ${expectedDateTime(createdAt)} · ID first-dr`,
  );
  expect(resumeButtons[0]).not.toHaveAccessibleName(resumeButtons[1].getAttribute("aria-label")!);
});

test("shows one flat five-step template workflow and reuses the canvas for document matching", async () => {
  vi.stubGlobal("fetch", reviewFetch());
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );
  await openDraftReview();

  const workflowNavigation = screen.getByRole("navigation", { name: "テンプレート設定の工程" });
  expect(within(workflowNavigation).getAllByRole("button")).toHaveLength(5);
  expect(screen.queryByRole("navigation", { name: "テンプレート設定フェーズ" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("テンプレート設定の状態")).toHaveTextContent("画像準備済み");
  expect(within(workflowNavigation).getByRole("button", { name: /1.*欄の種類/ })).toHaveAttribute("aria-current", "step");

  fireEvent.click(within(workflowNavigation).getByRole("button", { name: /4.*書類の見分け方/ }));

  expect(screen.getByLabelText("書類を見分ける欄を選択する帳票プレビュー")).toBeInTheDocument();
  expect(screen.getAllByText("同じ書類と判断する基準").length).toBeGreaterThan(0);
  expect(screen.getAllByText("個体識別番号").length).toBeGreaterThan(0);

  fireEvent.click(within(workflowNavigation).getByRole("button", { name: /5.*確認・保存/ }));
  expect(screen.getByText("照合条件")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "レイアウトは保存済み" })).toBeDisabled();
});

test("persists the selected document rectangle together with the template settings", async () => {
  let savedIdentity: unknown = null;
  let savedIdentityFields: unknown = null;
  const mappingResponse = (documentIdentity: unknown, validForOcr: boolean, documentIdentityFields: unknown[] = []) => ({
    canonicalWidth: 300,
    canonicalHeight: 100,
    mappings: [{
      mappingId: "layout_identity-input",
      targetPath: "document.unifiedEarTag",
      label: "個体識別番号",
      x: 0,
      y: 0,
      width: 100,
      height: 30,
      padding: 4,
      enabled: true,
      modelDetail: "high",
    }],
    documentIdentity,
    documentIdentityFields,
    targets: [{
      path: "document.unifiedEarTag",
      label: "個体識別番号",
      section: "基本情報",
    }],
    validForOcr,
    warnings: [],
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/jobs/job-draft/artifacts")) {
      return jsonResponse({
        items: [{ name: "normalized", mediaType: "image/png", url: "/source.png" }],
      });
    }
    if (path.endsWith("/template-versions/version-draft/layout")) {
      return jsonResponse(layout);
    }
    if (path.endsWith("/template-versions/version-draft/field-mappings")) {
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        savedIdentity = body.documentIdentity;
        savedIdentityFields = body.documentIdentityFields;
        return jsonResponse(mappingResponse(body.documentIdentity, true, body.documentIdentityFields));
      }
      return jsonResponse(mappingResponse(null, false));
    }
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  }));
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  const workflowNavigation = await screen.findByRole("navigation", { name: "テンプレート設定の工程" });
  fireEvent.click(within(workflowNavigation).getByRole("button", { name: /4.*書類の見分け方/ }));
  const canvas = screen.getByLabelText("書類を見分ける欄を選択する帳票プレビュー");
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 300, bottom: 100,
    width: 300, height: 100, toJSON: () => ({}),
  });
  fireEvent.click(canvas, { clientX: 50, clientY: 15 });
  fireEvent.click(within(workflowNavigation).getByRole("button", { name: /5.*確認・保存/ }));
  fireEvent.click(screen.getByRole("button", { name: "テンプレート設定をDBへ保存" }));

  await waitFor(() => expect(savedIdentity).toEqual({
    mappingId: "layout_identity-input",
    targetPath: "document.unifiedEarTag",
    label: "個体識別番号",
    valueType: "digits",
    exactLength: 10,
    matchRole: "primary",
    displayRole: "title",
  }));
  expect(savedIdentityFields).toEqual([savedIdentity]);
  expect(await screen.findByRole("button", { name: "レイアウトは保存済み" })).toBeDisabled();
});

describe.each([
  {
    label: "承認して有効化",
    path: "/template-versions/version-draft/approve",
    detail: "承認できませんでした。",
  },
  {
    label: "選別マスクから再生成",
    path: "/template-versions/version-draft/reprocess",
    detail: "再生成できませんでした。",
  },
  {
    label: "却下",
    path: "/template-versions/version-draft/reject",
    detail: "却下できませんでした。",
  },
])("$label", ({ label, path, detail }) => {
  test("shows API failures without leaving the review", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal("fetch", reviewFetch(
      path,
      () => Promise.resolve(jsonResponse({ detail }, 500)),
    ));
    render(
      <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
    );
    await openDraftReview();

    fireEvent.click(screen.getByRole("button", { name: label }));

    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.getByRole("heading", { name: "復元結果を確認" })).toBeInTheDocument();
  });
});

test("locks every decision while approval is running", async () => {
  let approveCalls = 0;
  let resolveApproval!: (response: Response) => void;
  const pendingApproval = new Promise<Response>((resolve) => {
    resolveApproval = resolve;
  });
  vi.stubGlobal("fetch", reviewFetch(
    "/template-versions/version-draft/approve",
    () => {
      approveCalls += 1;
      return pendingApproval;
    },
  ));
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );
  await openDraftReview();

  fireEvent.click(screen.getByRole("button", { name: "承認して有効化" }));

  const approving = await screen.findByRole("button", { name: "承認中…" });
  expect(approving).toBeDisabled();
  expect(screen.getByRole("button", { name: "却下" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "選別マスクから再生成" })).toBeDisabled();
  fireEvent.click(approving);
  expect(approveCalls).toBe(1);

  resolveApproval(jsonResponse({ detail: "承認できませんでした。" }, 500));
  expect(await screen.findByRole("alert")).toHaveTextContent("承認できませんでした。");
});
