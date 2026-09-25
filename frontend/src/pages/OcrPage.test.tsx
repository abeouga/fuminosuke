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

test("opens published page bindings without the legacy layoutVerified flag and preserves server refusal", async () => {
  const paged: Template[] = [{ ...templates[0], versions: [{ ...templates[0].versions[0],
    mode: "SPREADSHEET_TEMPLATE", quality: { layoutVerified: false, printLayout: { pages: [{}, {}] } },
  }] }];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/artifacts")) return new Response(JSON.stringify({ items: [] }));
    if (path.endsWith("/field-mappings")) return new Response(JSON.stringify({
      mappings: [{ mappingId: "m1", enabled: true }], validForOcr: true, warnings: [], targets: [],
    }));
    if (path.endsWith("/qualification")) return new Response(JSON.stringify({ status: "PASS", qualified: true,
      qualificationIssued: true, environmentQualified: true, geometryQualified: true, expiresAt: Date.now() / 1000 + 3600 }));
    if (path.endsWith("/ocr-pages")) return new Response(JSON.stringify({
      templateVersionId: "version-12345678", templateRevision: "r1", available: false,
      reason: "PRINT_REFERENCE_NOT_VALIDATED", pages: [
        { pageId: "p1", pageNumber: 1, range: "A1:D30", referenceUrl: "/p1.png" },
        { pageId: "p2", pageNumber: 2, range: "A31:D60", referenceUrl: "/p2.png" },
      ],
    }));
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<OcrPage templates={paged} rememberJob={vi.fn()} openReview={vi.fn()} />);
  expect(screen.getByRole("button", { name: "繁殖台帳A v3を選択" })).toBeEnabled();
  expect(screen.queryByText("編集中")).not.toBeInTheDocument();
  expect(screen.getByText(/ページ別の受付条件を確認/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  expect(await screen.findByText("Page 2 · A31:D60")).toBeInTheDocument();
  expect(screen.getByText(/現在読み取りを開始できません/)).toHaveTextContent("PRINT_REFERENCE_NOT_VALIDATED");
  expect(screen.queryByText(/正式資格/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "全ページを読み取る" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "カメラを起動" })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => /ocr-page-jobs|ocr-jobs/.test(String(url)))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート選択へ戻る" }));
  expect(screen.getByRole("button", { name: "繁殖台帳A v3を選択" })).toHaveAttribute("aria-pressed", "true");
});

test("a newer draft still blocks selecting a published page template", () => {
  const active = { ...templates[0].versions[0], mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: false, printLayout: { pages: [{}] } } };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [] }))));
  render(<OcrPage templates={[{ ...templates[0], versions: [active,
    { ...active, id: "new-draft", status: "DRAFT", versionNumber: 4 }] }]}
    rememberJob={vi.fn()} openReview={vi.fn()} />);
  expect(screen.getByRole("button", { name: "繁殖台帳A v3は編集中のためOCRに使用できません" })).toBeDisabled();
});

test("keeps a previously active template visible while editing and blocks OCR use", () => {
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

  expect(screen.getByRole("status")).toHaveTextContent("編集中のテンプレートが 1 件あります");
  expect(screen.getByText("編集中")).toBeInTheDocument();
  expect(screen.getByText(/OCR利用不可/)).toBeInTheDocument();
  expect(screen.getByRole("button", {
    name: "繁殖台帳A v3は編集中のためOCRに使用できません",
  })).toBeDisabled();
  expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "テンプレート設定を開く" }));
  expect(openTemplates).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls.every(([input]) => String(input).includes("/artifacts"))).toBe(true);
});

test("active XLSX cards download their published print files without selecting another template", async () => {
  const published = { ...templates[0].versions[0], mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: true, printLayout: { pages: [{}] } } };
  const sheet: Template = { ...templates[0], id: "sheet-template", name: "公開帳票", versions: [published,
    { ...published, id: "draft-newer", status: "DRAFT", versionNumber: 4 } ] };
  const fetch = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/artifacts")) return Promise.resolve(new Response(JSON.stringify({ items: [] })));
    if (url.endsWith("/print-layout")) return Promise.resolve(new Response(JSON.stringify({ revision: "published-r" })));
    if (url.includes("/files/")) return Promise.resolve(new Response(new Blob(["print fixture"])));
    throw new Error(`Unexpected request ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  try {
    render(<OcrPage templates={[sheet]} rememberJob={vi.fn()} openReview={vi.fn()} openTemplates={vi.fn()} />);
    const card = screen.getByText("公開帳票").closest("article")!;
    for (const format of ["XLSX", "PDF"]) {
      fireEvent.click(within(card).getByRole("button", { name: `印刷用${format}` }));
      await within(card).findByText(`印刷用${format}を取得し、ブラウザーへダウンロードを渡しました。`);
      expect(fetch.mock.calls.some(([input]) => String(input).includes(
        `/version-12345678/print-layout/files/${format.toLowerCase()}?base_revision=published-r`))).toBe(true);
    }
    expect(fetch.mock.calls.some(([input]) => String(input).includes("draft-newer"))).toBe(false);
    expect(click).toHaveBeenCalledTimes(2);
  } finally { click.mockRestore(); }
});

test("active legacy XLSX card enables its existing blank print buttons", async () => {
  const published = { ...templates[0].versions[0], mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: true, sourceType: "xlsx" } };
  const sheet: Template = { ...templates[0], id: "legacy-sheet", name: "単一ページ帳票", versions: [published,
    { ...published, id: "newer-draft", status: "DRAFT", versionNumber: 4 } ] };
  const fetch = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/artifacts")) return Promise.resolve(new Response(JSON.stringify({ items: [] })));
    if (url.includes("/blank-files/")) return Promise.resolve(new Response(new Blob(["blank fixture"])));
    throw new Error(`Unexpected request ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  try {
    render(<OcrPage templates={[sheet]} rememberJob={vi.fn()} openReview={vi.fn()} openTemplates={vi.fn()} />);
    const card = screen.getByText("単一ページ帳票").closest("article")!;
    for (const format of ["XLSX", "PDF"]) {
      const button = within(card).getByRole("button", { name: `印刷用${format}` });
      expect(button).toBeEnabled();
      fireEvent.click(button);
      await within(card).findByText(`印刷用${format}を取得し、ブラウザーへダウンロードを渡しました。`);
      expect(fetch.mock.calls.some(([input]) => String(input).includes(
        `/version-12345678/blank-files/${format.toLowerCase()}`))).toBe(true);
    }
    expect(fetch.mock.calls.some(([input]) => String(input).includes("newer-draft"))).toBe(false);
    expect(fetch.mock.calls.some(([input]) => String(input).includes("/print-layout"))).toBe(false);
    expect(click).toHaveBeenCalledTimes(2);
  } finally { click.mockRestore(); }
});

test("keeps an active template with a newer draft visible but unavailable for OCR", () => {
  const activeWithDraft: Template[] = [{
    ...templates[0],
    versions: [
      templates[0].versions[0],
      {
        ...templates[0].versions[0],
        id: "draft-version",
        versionNumber: 4,
        status: "DRAFT",
        sourceJobId: "draft-job",
        quality: { layoutVerified: false },
      },
    ],
  }];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [] }), { status: 200 })));

  render(
    <OcrPage
      templates={activeWithDraft}
      rememberJob={() => undefined}
      openReview={() => undefined}
    />,
  );

  expect(screen.getByRole("button", {
    name: "繁殖台帳A v3は編集中のためOCRに使用できません",
  })).toBeDisabled();
  expect(screen.getByRole("button", { name: "繁殖台帳Aのテンプレートを編集" }))
    .toBeEnabled();
  expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeDisabled();
});

test("shows unverified active templates but does not allow selecting them", async () => {
  const openTemplates = vi.fn();
  const unverifiedVersion = {
    ...templates[0].versions[0],
    id: "version-unverified",
    versionNumber: 4,
    quality: { layoutVerified: false },
  };
  const mixedTemplates = [
    templates[0],
    {
      ...templates[0],
      id: "template-2",
      name: "面接カード",
      activeVersionId: unverifiedVersion.id,
      versions: [unverifiedVersion],
    },
  ];
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
    "一覧には表示されますが、設定確認が完了するまでOCRには使用できません",
  );
  fireEvent.click(screen.getByRole("button", { name: "テンプレート設定を開く" }));
  expect(openTemplates).toHaveBeenCalledOnce();
  const selectedCard = screen.getByRole("button", { name: "繁殖台帳A v3を選択" });
  expect(selectedCard).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", {
    name: "面接カード v4は編集中のためOCRに使用できません",
  })).toBeDisabled();
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
  expect(screen.getByRole("button", { name: "読み取りを開始" })).toBeEnabled();
});

test.each([
  ["画像由来テンプレート", templates],
  ["印刷レイアウトのないExcel由来テンプレート", [{
    ...templates[0],
    versions: [{ ...templates[0].versions[0], mode: "SPREADSHEET_TEMPLATE" as const }],
  }]],
])("accepts a dropped single-page OCR image for %s", async (_source, sourceTemplates) => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/artifacts")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
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
  render(<OcrPage templates={sourceTemplates} rememberJob={vi.fn()} openReview={vi.fn()} />);

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  const dropZone = screen.getByText("帳票画像を選択またはドロップ").closest("label")!;
  const file = new File(["image"], "dropped-ledger.png", { type: "image/png" });
  fireEvent.drop(dropZone, { dataTransfer: { files: [file] } });

  expect(screen.getByText("選択中:")).toHaveTextContent("dropped-ledger.png");
  expect(screen.getByRole("button", { name: "内容確認へ" })).toBeEnabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("rejects multiple or unsupported files dropped on single-page OCR without replacing the selection", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/artifacts")) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({
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
    }));
  }));
  render(<OcrPage templates={templates} rememberJob={vi.fn()} openReview={vi.fn()} />);

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  const dropZone = screen.getByText("帳票画像を選択またはドロップ").closest("label")!;
  const selected = new File(["image"], "kept.png", { type: "image/png" });
  fireEvent.drop(dropZone, { dataTransfer: { files: [selected] } });
  fireEvent.drop(dropZone, { dataTransfer: { files: [selected, selected] } });
  expect(screen.getByRole("alert")).toHaveTextContent("1件ずつ");
  expect(screen.getByText("選択中:")).toHaveTextContent("kept.png");

  fireEvent.drop(dropZone, {
    dataTransfer: { files: [new File(["text"], "notes.txt", { type: "text/plain" })] },
  });
  expect(screen.getByRole("alert")).toHaveTextContent("対応していないファイル形式");
  expect(screen.getByText("選択中:")).toHaveTextContent("kept.png");
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

  const addTemplate = screen.getByRole("button", { name: "テンプレートを追加" });
  expect(addTemplate).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(addTemplate);
  expect(screen.getByRole("group", { name: "テンプレートの追加方法" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "新しく作成" }));

  expect(screen.getByRole("heading", { name: "テンプレート作成・編集" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "新規作成" })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("button", { name: "テンプレートを取り込む" })).not.toBeInTheDocument();
  const manageTab = screen.getByRole("tab", { name: /既存を確認・修正/ });
  fireEvent.click(manageTab);
  expect(manageTab).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("繁殖台帳A")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "← 読み取りへ戻る" }));
  expect(screen.getByRole("heading", { name: "書類を読み取る" })).toBeInTheDocument();
});

test("hides a never-activated draft from the reading list", () => {
  const draftOnly: Template[] = [{
    ...templates[0],
    id: "draft-template",
    name: "未完成テンプレート",
    activeVersionId: null,
    versions: [{
      ...templates[0].versions[0],
      id: "draft-version",
      status: "DRAFT",
      quality: { layoutVerified: false },
    }],
  }];
  vi.stubGlobal("fetch", vi.fn());

  render(
    <OcrPage
      templates={draftOnly}
      rememberJob={() => undefined}
      openReview={() => undefined}
    />,
  );

  expect(screen.queryByText("未完成テンプレート")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "テンプレートを追加" })).toBeInTheDocument();
  expect(screen.getByText(/有効化済みのテンプレートはありません/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeDisabled();
});

test("uses the actual active version when a legacy active pointer is stale", () => {
  const stalePointerTemplate: Template[] = [{
    ...templates[0],
    activeVersionId: "archived-version",
    versions: [
      {
        ...templates[0].versions[0],
        id: "active-version",
        versionNumber: 2,
        sourceJobId: null,
      },
      {
        ...templates[0].versions[0],
        id: "archived-version",
        versionNumber: 1,
        status: "ARCHIVED",
        sourceJobId: null,
      },
    ],
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
      templates={stalePointerTemplate}
      rememberJob={() => undefined}
      openReview={() => undefined}
    />,
  );

  expect(screen.getByRole("button", { name: "繁殖台帳A v2を選択" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.queryByText("編集中")).not.toBeInTheDocument();
});

test("opens the existing template import dialog from the add card", () => {
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

  fireEvent.click(screen.getByRole("button", { name: "テンプレートを追加" }));
  fireEvent.click(screen.getByRole("button", { name: "テンプレートを取り込む" }));

  expect(screen.getByRole("dialog", { name: "テンプレートを取り込む" })).toBeInTheDocument();
});

test("keeps the reading draft while directly editing a template from its card", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/edit")) {
      return new Response(JSON.stringify({ ...templates[0].versions[0],
        id: "editing-draft", status: "DRAFT", versionNumber: 2,
        quality: { layoutVerified: false, forkParentVersionId: templates[0].versions[0].id },
      }), { status: 200 });
    }
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
  expect(within(card).getByRole("button", { name: "繁殖台帳Aを配布用に書き出す（準備中）" }).querySelector("svg")).not.toBeNull();
  expect(within(card).getByRole("button", { name: "繁殖台帳Aのテンプレートを編集" }).querySelector("svg")).not.toBeNull();
  expect(within(card).getByRole("button", { name: "繁殖台帳Aを削除" }).querySelector("svg")).not.toBeNull();

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  fireEvent.change(container.querySelector<HTMLInputElement>('input[type="file"]')!, {
    target: { files: [new File(["image"], "kept-reading.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "戻る" }));
  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aのテンプレートを編集" }));

  expect(await screen.findByRole("heading", { name: "読み取る欄を設定" })).toBeInTheDocument();
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

test("duplicates the active version in one click and places the ready copy beside its card", async () => {
  const copy: Template = { ...templates[0], id: "copied-template", name: "繁殖台帳A のコピー",
    activeVersionId: "copied-version", versions: [{ ...templates[0].versions[0], id: "copied-version",
      versionNumber: 1 }] };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/template-versions/version-12345678/duplicate")) {
      return new Response(JSON.stringify({
        templateId: "copied-template", templateVersionId: "copied-version",
      }), { status: 201 });
    }
    if (String(input).endsWith("/templates/copied-template")) {
      return new Response(JSON.stringify(copy), { status: 200 });
    }
    if (String(input).includes("/artifacts")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    return new Response(JSON.stringify({
      mappings: [], targets: [], validForOcr: false, warnings: [],
    }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const refreshTemplates = vi.fn().mockResolvedValue(undefined);
  const view = render(<OcrPage templates={templates} refreshTemplates={refreshTemplates}
    rememberJob={vi.fn()} openReview={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを複製" }));
  await waitFor(() => expect(refreshTemplates).toHaveBeenCalledOnce());
  const request = fetchMock.mock.calls.find(([url]) =>
    String(url).endsWith("/template-versions/version-12345678/duplicate"));
  expect(request?.[1]?.method).toBe("POST");
  expect(JSON.parse(String(request?.[1]?.body))).toEqual({ name: "繁殖台帳A のコピー", activate: true });
  expect(screen.queryByRole("dialog", { name: "テンプレートを複製" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "テンプレート作成・編集" })).not.toBeInTheDocument();
  view.rerender(<OcrPage templates={[copy, ...templates]} refreshTemplates={refreshTemplates}
    rememberJob={vi.fn()} openReview={vi.fn()} />);
  expect(screen.getAllByRole("article").slice(0, 2).map((card) => card.textContent)).toEqual([
    expect.stringContaining("繁殖台帳A"), expect.stringContaining("繁殖台帳A のコピー"),
  ]);
  expect(screen.getByRole("button", { name: "繁殖台帳A のコピー v1を選択" }))
    .toHaveAttribute("aria-pressed", "true");
});

test("does not report a reading-ready copy when the server leaves it as a draft", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/template-versions/version-12345678/duplicate")) {
      return new Response(JSON.stringify({
        templateId: "copied-template", templateVersionId: "copied-version",
      }), { status: 201 });
    }
    if (String(input).endsWith("/templates/copied-template")) {
      return new Response(JSON.stringify({
        ...templates[0], id: "copied-template", name: "繁殖台帳A のコピー",
        activeVersionId: null,
        versions: [{ ...templates[0].versions[0], id: "copied-version", status: "DRAFT" }],
      }), { status: 200 });
    }
    if (String(input).includes("/artifacts")) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ mappings: [], targets: [], validForOcr: false, warnings: [] }));
  }));
  const refreshTemplates = vi.fn().mockResolvedValue(undefined);
  render(<><OcrPage templates={templates} refreshTemplates={refreshTemplates}
    rememberJob={vi.fn()} openReview={vi.fn()} /><ToastViewport /></>);

  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを複製" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("読み取り用に有効化されていません");
  expect(screen.getByRole("button", { name: "繁殖台帳A v3を選択" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(refreshTemplates).toHaveBeenCalledOnce();
});

test("treats an unreadable copy detail as an unknown outcome, not a failed duplicate", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/template-versions/version-12345678/duplicate")) {
      return new Response(JSON.stringify({
        templateId: "copied-template", templateVersionId: "copied-version",
      }), { status: 201 });
    }
    if (String(input).endsWith("/templates/copied-template")) {
      return new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 });
    }
    if (String(input).includes("/artifacts")) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ mappings: [], targets: [], validForOcr: false, warnings: [] }));
  }));
  render(<><OcrPage templates={templates} refreshTemplates={vi.fn().mockResolvedValue(undefined)}
    rememberJob={vi.fn()} openReview={vi.fn()} /><ToastViewport /></>);

  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを複製" }));
  expect(await screen.findByText("複製結果を確認できませんでした")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "繁殖台帳A v3を選択" }))
    .toHaveAttribute("aria-pressed", "true");
});

test("reports a duplicate name conflict without leaving the reading tab", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/template-versions/version-12345678/duplicate")) {
      return new Response(JSON.stringify({
        code: "TEMPLATE_NAME_CONFLICT", title: "Template name already exists",
        detail: "同じ名前のテンプレートが既にあります。",
      }), { status: 409 });
    }
    if (String(input).includes("/artifacts")) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({
      mappings: [], targets: [], validForOcr: false, warnings: [],
    }));
  }));
  render(<><OcrPage templates={templates} refreshTemplates={vi.fn()}
    rememberJob={vi.fn()} openReview={vi.fn()} /><ToastViewport /></>);

  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを複製" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("同じ名前のテンプレート");
  expect(screen.queryByRole("dialog", { name: "テンプレートを複製" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "繁殖台帳A v3を選択" })).toBeInTheDocument();
});

test("reports that duplication completed when only the list refresh fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/template-versions/version-12345678/duplicate")) {
      return new Response(JSON.stringify({
        templateId: "copied-template", templateVersionId: "copied-version",
      }), { status: 201 });
    }
    if (String(input).endsWith("/templates/copied-template")) {
      return new Response(JSON.stringify({
        ...templates[0], id: "copied-template", name: "繁殖台帳A のコピー",
        activeVersionId: "copied-version",
        versions: [{ ...templates[0].versions[0], id: "copied-version", versionNumber: 1 }],
      }), { status: 200 });
    }
    if (String(input).includes("/artifacts")) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ mappings: [], targets: [], validForOcr: false, warnings: [] }));
  }));
  render(<><OcrPage templates={templates} refreshTemplates={vi.fn().mockRejectedValue(new Error("offline"))}
    rememberJob={vi.fn()} openReview={vi.fn()} /><ToastViewport /></>);
  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを複製" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("複製は完了しました");
  expect(screen.queryByRole("heading", { name: "テンプレート作成・編集" })).not.toBeInTheDocument();
});

test("rechecks the list when the duplicate request loses its response", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/template-versions/version-12345678/duplicate")) {
      throw new TypeError("Failed to fetch");
    }
    if (String(input).includes("/artifacts")) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ mappings: [], targets: [], validForOcr: false, warnings: [] }));
  }));
  const refreshTemplates = vi.fn().mockResolvedValue(undefined);
  render(<><OcrPage templates={templates} refreshTemplates={refreshTemplates}
    rememberJob={vi.fn()} openReview={vi.fn()} /><ToastViewport /></>);
  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを複製" }));
  await waitFor(() => expect(refreshTemplates).toHaveBeenCalledOnce());
  expect(screen.getByText("複製結果を確認できませんでした")).toBeInTheDocument();
});

test("duplicates the active card version while an edit draft exists", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/template-versions/version-12345678/duplicate")) {
      return new Response(JSON.stringify({
        templateId: "copied-template", templateVersionId: "copied-version",
      }), { status: 201 });
    }
    if (String(input).includes("/artifacts")) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ mappings: [], targets: [], validForOcr: false, warnings: [] }));
  });
  vi.stubGlobal("fetch", fetchMock);
  const withDraft: Template[] = [{ ...templates[0], versions: [
    templates[0].versions[0],
    { ...templates[0].versions[0], id: "draft-version", status: "DRAFT", versionNumber: 4 },
  ] }];
  render(<OcrPage templates={withDraft} refreshTemplates={vi.fn().mockResolvedValue(undefined)}
    rememberJob={vi.fn()} openReview={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "繁殖台帳Aを複製" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) =>
    String(url).endsWith("/template-versions/version-12345678/duplicate"))).toBe(true));
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

  const progress = await screen.findByRole("region", { name: "OCRの進捗" });
  await waitFor(() => {
    expect(progress).toHaveTextContent("5%");
    expect(progress).toHaveTextContent("画像を確認中");
  });
  expect(screen.queryByText("読み取り内容を確認")).not.toBeInTheDocument();
  expect(container.querySelector('input[type="file"]')).not.toBeInTheDocument();
  expect(submitted[0]?.has("motherIndividualId")).toBe(false);
  expect(rememberJob).toHaveBeenCalledWith("job-1");
});

test("retries only the failed pages from the reading progress screen", async () => {
  let retried = false;
  let createCalls = 0;
  let retryCalls = 0;
  const jobResponse = (
    status: string,
    error: { code: string; message: string } | null,
    attemptNumber: number,
  ) => ({
    id: "page-job",
    jobType: "EXTRACT_DOCUMENT",
    status,
    progressStage: status === "FAILED" ? "VALIDATING_RESULT" : "VALIDATING_INPUT",
    progressPercent: status === "FAILED" ? 90 : 5,
    attemptNumber,
    error,
    createdAt: "2026-09-22T00:00:00Z",
    startedAt: "2026-09-22T00:00:01Z",
    completedAt: status === "FAILED" ? "2026-09-22T00:00:02Z" : null,
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/field-mappings")) {
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
      }));
    }
    if (path.endsWith("/ocr-jobs") && init?.method === "POST") {
      createCalls += 1;
      return new Response(JSON.stringify({ jobId: "page-job" }), { status: 202 });
    }
    if (path.endsWith("/jobs/page-job/retry") && init?.method === "POST") {
      retryCalls += 1;
      retried = true;
      return new Response(JSON.stringify(jobResponse("QUEUED", null, 2)));
    }
    if (path.endsWith("/jobs/page-job")) {
      return new Response(JSON.stringify(retried
        ? jobResponse("PROCESSING", null, 2)
        : jobResponse("FAILED", {
          code: "MULTI_PAGE_PARTIAL_FAILURE",
          message: "失敗したページがあります。成功ページを保持して再試行できます。",
        }, 1)));
    }
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const { container } = render(
    <OcrPage templates={templates} rememberJob={vi.fn()} openReview={vi.fn()} openTemplates={vi.fn()} />,
  );

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  fireEvent.change(container.querySelector<HTMLInputElement>('input[type="file"]')!, {
    target: { files: [new File(["image"], "ledger.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "内容確認へ" }));
  fireEvent.click(screen.getByRole("button", { name: "読み取りを開始" }));

  expect(await screen.findByRole("button", { name: "失敗したページを再試行" })).toBeEnabled();
  expect(screen.getByText("成功したページは保持したまま、失敗したページだけを再試行します。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "失敗したページを再試行" }));

  await waitFor(() => expect(retryCalls).toBe(1));
  await waitFor(() => expect(screen.getByText("画像を確認中")).toBeInTheDocument());
  expect(createCalls).toBe(1);
  expect(fetchMock.mock.calls.filter(([url, options]) => (
    String(url).endsWith("/jobs/page-job/retry") && options?.method === "POST"
  ))).toHaveLength(1);
});

test("starts a new acceptance instead of retrying an exhausted OCR budget", async () => {
  let retryCalls = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/field-mappings")) {
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
      }));
    }
    if (path.endsWith("/ocr-jobs") && init?.method === "POST") {
      return new Response(JSON.stringify({ jobId: "budget-job" }), { status: 202 });
    }
    if (path.endsWith("/jobs/budget-job/retry") && init?.method === "POST") {
      retryCalls += 1;
    }
    if (path.endsWith("/jobs/budget-job")) {
      return new Response(JSON.stringify({
        id: "budget-job",
        jobType: "EXTRACT_DOCUMENT",
        status: "FAILED",
        progressStage: "VALIDATING_RESULT",
        progressPercent: 1,
        attemptNumber: 3,
        error: {
          code: "OCR_CALL_BUDGET_TOO_SMALL",
          message: "未完了の全ページを読むためのOCR呼び出し予算が不足しています。",
        },
        createdAt: "2026-09-22T00:00:00Z",
        startedAt: "2026-09-22T00:00:01Z",
        completedAt: "2026-09-22T00:00:02Z",
      }), { status: 200 });
    }
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const { container } = render(
    <OcrPage templates={templates} rememberJob={vi.fn()} openReview={vi.fn()} openTemplates={vi.fn()} />,
  );

  await waitFor(() => expect(screen.getByRole("button", { name: "画像の準備へ" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "画像の準備へ" }));
  fireEvent.change(container.querySelector<HTMLInputElement>('input[type="file"]')!, {
    target: { files: [new File(["image"], "ledger.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "内容確認へ" }));
  fireEvent.click(screen.getByRole("button", { name: "読み取りを開始" }));

  expect(await screen.findByText(/この受付の呼び出し予算または実行設定は変更できません/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "処理を再試行" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "新しい読み取りを開始" }));

  expect(await screen.findByText("読み取る帳票画像を準備")).toBeInTheDocument();
  expect(container.querySelector('input[type="file"]')).toBeInTheDocument();
  expect(retryCalls).toBe(0);
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
  await waitFor(() => expect(screen.getByRole("region", { name: "OCRの進捗" })).toHaveTextContent("5%"));
  expect(screen.queryByRole("button", { name: "読み取りを開始" })).not.toBeInTheDocument();
});
