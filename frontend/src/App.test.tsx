import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/health")) return new Response(JSON.stringify({ status: "ok", database: "ok", jobWorker: "running", ocrProvider: "mock" }), { status: 200 });
    if (path.endsWith("/templates")) return new Response("[]", { status: 200 });
    if (path.includes("/jobs?limit=")) return new Response("[]", { status: 200 });
    if (path.endsWith("/sire-names")) return new Response(JSON.stringify({
      items: [
        { id: "sire-1", name: "勝忠平", createdAt: "2026-07-30T00:00:00Z" },
      ],
      count: 1,
      allowedSuffixes: ["ET"],
    }), { status: 200 });
    if (path.endsWith("/data-search/catalog")) return jsonResponse({
      count: 1,
      templates: [{
        templateId: "template-1",
        templateName: "繁殖台帳",
        documentCount: 1,
        fields: [{
          fieldKey: "document.unifiedEarTag",
          label: "個体識別番号",
          sectionTitle: "基本情報",
          valueType: "text",
          repeated: false,
          identity: true,
          suggestions: [],
        }],
      }],
    });
    if (path.endsWith("/data-search")) return jsonResponse({
      query: "123-4567-890",
      count: 1,
      page: 1,
      pageSize: 20,
      items: [{
        documentId: "document-1",
        templateId: "template-1",
        templateName: "繁殖台帳",
        sourceJobId: "job-1",
        confirmedAt: "2026-07-22T12:00:00Z",
        pageCount: 1,
        spreadsheetUrl: null,
        displayTitle: "修正花子",
        displayTitleLabel: "牛名",
        displaySubtitle: "123-4567-890",
        displaySubtitleLabel: "個体識別番号",
        matches: [],
      }],
    });
    if (path.endsWith("/individual-records/catalog")) return new Response(JSON.stringify({
      count: 1,
      items: [{
        individualId: "1234567890",
        cattleName: "修正花子",
        recordCount: 1,
        lastConfirmedAt: "2026-07-22T12:00:00Z",
      }],
    }), { status: 200 });
    if (path.endsWith("/ocr-jobs/job-review/result")) return new Response(JSON.stringify({
      jobId: "job-review",
      revision: 1,
      result: {
        schemaVersion: "1.0",
        templateId: "template-1",
        templateVersion: "1",
        document: {
          unifiedEarTag: { raw: "1234567890", normalized: "1234567890", status: "readable", alternatives: [], reviewReason: null, sourceRegionId: "identity", boundingBox: null },
          cattleName: { raw: "花子", normalized: "花子", status: "readable", alternatives: [], reviewReason: null, sourceRegionId: "identity", boundingBox: null },
        },
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
      individualId: "1234567890",
      fieldPreviews: [],
    }), { status: 200 });
    if (path.endsWith("/jobs/job-review/artifacts")) return new Response(JSON.stringify({
      items: [],
    }), { status: 200 });
    if (path.includes("/individual-records?individualId=")) return new Response(JSON.stringify({
      query: "123-4567-890",
      individualId: "1234567890",
      count: 1,
      records: [{
        recordId: "record-1",
        individualId: "1234567890",
        sourceJobId: "job-1",
        ocrResultRevision: 2,
        confirmedAt: "2026-07-22T12:00:00Z",
        data: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "version-1",
          document: {
            cattleName: { raw: "修正花子", normalized: "修正花子", status: "readable", alternatives: [], reviewReason: null, sourceRegionId: "identity", boundingBox: null },
          },
          saleCalves: [],
          pedigree: {},
          calvings: [],
          reviewItems: [],
          processingMetadata: {},
        },
      }],
    }), { status: 200 });
    return new Response("{}", { status: 200 });
  }));
});

afterEach(() => {
  cleanup();
  window.history.pushState({}, "", "/");
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const recoveredTemplate = {
  id: "template-recovered",
  name: "復旧した繁殖台帳",
  documentType: "cow-ledger",
  activeVersionId: "version-recovered",
  createdAt: "2026-07-30T00:00:00Z",
  versions: [{
    id: "version-recovered",
    versionNumber: 1,
    mode: "REFERENCE_TEMPLATE",
    status: "ACTIVE",
    sourceJobId: "template-job",
    canonicalWidth: 1400,
    canonicalHeight: 990,
    quality: {},
    createdAt: "2026-07-30T00:00:00Z",
  }],
};

test("requires Google login before loading protected OCR data", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/health")) {
      return jsonResponse({ status: "ok", database: "ok", jobWorker: "running", ocrProvider: "mock" });
    }
    if (path.endsWith("/auth/session")) {
      return jsonResponse({ required: true, configured: true, authenticated: false, user: null });
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Googleアカウントで接続" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Googleでログイン" })).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/templates"))).toBe(false);
});

test("shows OAuth setup guidance when the backend is not configured", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/auth/session")) {
      return jsonResponse({ required: true, configured: false, authenticated: false, user: null });
    }
    return jsonResponse({});
  }));

  render(<App />);

  expect(await screen.findByText("管理者のOAuth設定が必要です")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Googleでログイン" })).not.toBeInTheDocument();
});

test("requires reconnecting when the stored Google authorization is broader than current policy", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/health")) {
      return jsonResponse({ status: "ok", database: "ok", jobWorker: "running", ocrProvider: "mock" });
    }
    if (path.endsWith("/auth/session")) {
      return jsonResponse({
        required: true,
        configured: true,
        authenticated: true,
        authorizationCurrent: false,
        user: {
          subject: "legacy-user",
          email: "owner@example.com",
          displayName: "台帳 管理者",
          pictureUrl: null,
        },
      });
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Google連携の更新が必要です" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Google連携を更新" })).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/templates"))).toBe(false);
});

test("shows the local workbench home", async () => {
  render(<App />);
  expect(await screen.findByText("紙の台帳を、", { exact: false })).toBeInTheDocument();
  expect(await screen.findByText("試運転モード")).toBeInTheDocument();
});

test("reloads templates automatically when backend health recovers", async () => {
  let resolveHealth!: (response: Response) => void;
  const healthResponse = new Promise<Response>((resolve) => {
    resolveHealth = resolve;
  });
  let templateListCalls = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL): Promise<Response> => {
    const path = String(input);
    if (path.endsWith("/health")) return healthResponse;
    if (path.endsWith("/templates")) {
      templateListCalls += 1;
      return Promise.resolve(templateListCalls === 1
        ? jsonResponse({ detail: "起動待ちです。" }, 503)
        : jsonResponse([recoveredTemplate]));
    }
    if (path.endsWith("/templates/template-recovered")) {
      return Promise.resolve(jsonResponse(recoveredTemplate));
    }
    if (path.includes("/jobs?limit=")) return Promise.resolve(jsonResponse([]));
    return Promise.resolve(jsonResponse({}));
  }));
  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent("テンプレートを取得できませんでした。");

  await act(async () => {
    resolveHealth(jsonResponse({
      status: "ok",
      database: "ok",
      jobWorker: "running",
      ocrProvider: "mock",
    }));
  });

  await waitFor(() => expect(templateListCalls).toBe(2));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(screen.getByText("1 件")).toBeInTheDocument();
});

test("offers a manual template reload while the backend is still unavailable", async () => {
  let templateListCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/health")) return jsonResponse({ detail: "未接続" }, 503);
    if (path.endsWith("/templates")) {
      templateListCalls += 1;
      return templateListCalls === 1
        ? jsonResponse({ detail: "一時的に取得できません。" }, 503)
        : jsonResponse([]);
    }
    if (path.includes("/jobs?limit=")) return jsonResponse([]);
    return jsonResponse({});
  }));
  render(<App />);

  const retry = await screen.findByRole("button", { name: "テンプレートを再取得" });
  fireEvent.click(retry);

  await waitFor(() => expect(templateListCalls).toBe(2));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

test("keeps old review-required and interrupted jobs in the action-required list", async () => {
  const normalJobs = Array.from({ length: 21 }, (_, index) => ({
    id: `confirmed-${index}`,
    jobType: "EXTRACT_DOCUMENT",
    status: "CONFIRMED",
    progressStage: "READY_FOR_REVIEW",
    progressPercent: 100,
    attemptNumber: 1,
    error: null,
    createdAt: `2026-07-${String(30 - index).padStart(2, "0")}T00:00:00Z`,
    startedAt: null,
    completedAt: null,
  }));
  const interrupted = {
    ...normalJobs[0],
    id: "older-interrupted",
    jobType: "PREPARE_TEMPLATE",
    status: "INTERRUPTED",
    error: { code: "BACKEND_RESTARTED", message: "再試行できます。" },
    createdAt: "2026-06-01T00:00:00Z",
  };
  const ocrReview = {
    ...normalJobs[0],
    id: "older-ocr-review",
    jobType: "EXTRACT_DOCUMENT",
    status: "REVIEW_REQUIRED",
    createdAt: "2026-05-01T00:00:00Z",
  };
  const templateReview = {
    ...normalJobs[0],
    id: "older-template-review",
    jobType: "PREPARE_TEMPLATE",
    status: "REVIEW_REQUIRED",
    createdAt: "2026-04-01T00:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/health")) {
      return new Response(JSON.stringify({
        status: "ok",
        database: "ok",
        jobWorker: "running",
        ocrProvider: "mock",
      }), { status: 200 });
    }
    if (path.endsWith("/templates")) return new Response("[]", { status: 200 });
    if (path.endsWith("/jobs?limit=100")) {
      return new Response(JSON.stringify(normalJobs), { status: 200 });
    }
    if (path.includes("/jobs?limit=1000&attentionOnly=true")) {
      return new Response(
        JSON.stringify([interrupted, ocrReview, templateReview]),
        { status: 200 },
      );
    }
    return new Response("{}", { status: 200 });
  }));

  render(<App />);

  const heading = await screen.findByRole("heading", { name: "対応が必要な処理" });
  const attentionSection = heading.closest("section");
  expect(attentionSection).not.toBeNull();
  const attentionView = within(attentionSection as HTMLElement);
  expect(attentionView.getByText("再開可能")).toBeInTheDocument();
  expect(attentionView.getAllByText("確認待ち")).toHaveLength(2);
  expect(attentionView.getByText("帳票読み取り")).toBeInTheDocument();
  expect(attentionView.getAllByText("テンプレート準備")).toHaveLength(2);
});

test("keeps the normal recent list when the action-required request fails", async () => {
  const recent = {
    id: "recent-confirmed",
    jobType: "EXTRACT_DOCUMENT",
    status: "CONFIRMED",
    progressStage: "READY_FOR_REVIEW",
    progressPercent: 100,
    attemptNumber: 1,
    error: null,
    createdAt: "2026-07-30T00:00:00Z",
    startedAt: null,
    completedAt: null,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/health")) {
      return jsonResponse({
        status: "ok",
        database: "ok",
        jobWorker: "running",
        ocrProvider: "mock",
      });
    }
    if (path.endsWith("/templates")) return jsonResponse([]);
    if (path.endsWith("/jobs?limit=100")) return jsonResponse([recent]);
    if (path.includes("/jobs?limit=1000&attentionOnly=true")) {
      return jsonResponse({ detail: "一時的に取得できません。" }, 503);
    }
    return jsonResponse({});
  }));

  render(<App />);

  expect(await screen.findByText("確定済み")).toBeInTheDocument();
  expect(screen.getByText("帳票読み取り")).toBeInTheDocument();
});

test("searches and displays confirmed data", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /データ検索/ }));
  fireEvent.change(screen.getByLabelText("書類の見出しから検索"), {
    target: { value: "123-4567-890" },
  });
  fireEvent.click(await screen.findByRole("button", { name: "検索" }));

  expect(await screen.findByText((_, node) => (
    node?.tagName === "P" && node.textContent === "個体識別番号 123-4567-890"
  ))).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "修正花子" })).toBeInTheDocument();
  expect(screen.getByText("1件")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "検索結果" })).toBeInTheDocument();
});

test("does not expose the legacy sire-name management page in the side panel", async () => {
  render(<App />);

  const navigation = await screen.findByRole("navigation", { name: "メインメニュー" });
  expect(within(navigation).getByRole("button", { name: /テンプレート/ })).toBeInTheDocument();
  expect(within(navigation).queryByRole("button", { name: /種雄牛名を追加/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "種雄牛名を追加" })).not.toBeInTheDocument();
});

test("warns before leaving an unconfirmed OCR review", async () => {
  window.history.pushState({}, "", "/?job=job-review");
  const confirm = vi.spyOn(window, "confirm");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "この個体で間違いありませんか？" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /データ検索/ }));
  expect(screen.getByRole("dialog", { name: "未保存の変更があります" })).toBeInTheDocument();
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "この画面に残る" }));
  expect(screen.getByRole("heading", { name: "この個体で間違いありませんか？" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "未保存の変更があります" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /データ検索/ }));
  fireEvent.click(screen.getByRole("button", { name: "変更を破棄して移動" }));
  expect(await screen.findByRole("heading", { name: "登録済みデータを検索" })).toBeInTheDocument();
});

test("opens a Google Sheets correction link directly in edit mode", async () => {
  window.history.pushState(
    {},
    "",
    "/?job=job-review&field=document.cattleName&edit=1",
  );

  render(<App />);

  expect(await screen.findByRole("button", { name: "修正を保存" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", {
    name: "この個体で間違いありませんか？",
  })).not.toBeInTheDocument();
});
