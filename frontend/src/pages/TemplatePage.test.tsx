import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { Job, LayoutDefinition, Template, TemplateVersion } from "../types";
import { TemplatePage } from "./TemplatePage";

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
  cells: [],
  relations: [],
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

function reviewFetch(actionPath?: string, actionResponse?: () => Promise<Response>) {
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
        mappings: [],
        targets: [],
        validForOcr: true,
        warnings: [],
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

  selectTemplateImage(container);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));

  const lockedButton = await screen.findByRole("button", { name: "テンプレート準備を開始しました" });
  expect(lockedButton).toBeDisabled();
  fireEvent.click(lockedButton);
  expect(createCalls).toBe(1);
  expect(rememberJob).toHaveBeenCalledTimes(1);
  expect(rememberJob).toHaveBeenCalledWith("template-job-1");
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

  const resumeButtons = screen.getAllByRole("button", { name: /確認を再開: 繁殖台帳A/ });
  expect(resumeButtons).toHaveLength(2);
  expect(resumeButtons[0]).toHaveAccessibleName(
    `確認を再開: 繁殖台帳A v2 · 白紙帳票から · ${expectedDateTime(createdAt)} · ID first-dr`,
  );
  expect(resumeButtons[1]).toHaveAccessibleName(
    `確認を再開: 繁殖台帳A v2 · 白紙帳票から · ${expectedDateTime(laterDraft.createdAt)} · ID second-d`,
  );
  expect(resumeButtons[0]).not.toHaveAccessibleName(resumeButtons[1].getAttribute("aria-label")!);
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
