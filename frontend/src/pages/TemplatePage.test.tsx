import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { DocumentIdentity, FieldMappingsResponse, Job, LayoutDefinition, Template, TemplateVersion } from "../types";
import {
  mappingWarningsForIdentity,
  mappingWarningWorkflowStep,
  markOptionsTargetFromFieldErrors,
  savedPrintPageForCell,
  TemplatePage,
} from "./TemplatePage";
import type { PrintState, ReferenceQualification } from "../printLayout";

const createdAt = "2026-07-30T12:00:00Z";
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
let scrollIntoViewMock = vi.fn();

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
  scrollIntoViewMock = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollIntoViewMock,
  });
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
  if (originalScrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: originalScrollIntoView,
    });
  } else {
    delete (HTMLElement.prototype as { scrollIntoView?: HTMLElement["scrollIntoView"] }).scrollIntoView;
  }
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

const pagedRevision = "a".repeat(64);
const pagedState: PrintState = {
  revision: pagedRevision,
  editable: true,
  source: {
    sheetName: "sheet", startRow: 1, endRow: 10, width: 100, height: 100,
    rowBoundaries: [], validBreakRows: [], cells: [], unsupported: [], warnings: [],
    importedSettings: { pageSetup: null, margins: null, printOptions: null },
  },
  saved: {
    paperSize: "A4", orientation: "portrait",
    margins: { left: 10, right: 10, top: 10, bottom: 10 },
    targetPagesWide: 1, targetPagesTall: null,
    horizontalCentering: false, verticalCentering: false,
    commonScale: 1, ocrReady: false, warnings: [], pages: [{
      page: 1, range: "A1:D10", startRow: 1, endRow: 10, width: 600, height: 800,
      transform: { scale: 2, offsetX: 0, offsetY: 0, originX: 0, originY: 0 }, cells: [],
      fields: [{ cellId: "identity-input", sourceRange: "A1", templatePage: 1,
        rectangle: { x: 100, y: 100, width: 200, height: 60 } }],
      referenceImage: { sha256: "b".repeat(64) },
    }],
  },
};

const passQualification: ReferenceQualification = {
  status: "PASS", qualified: true, qualificationIssued: true,
  environmentQualified: true, geometryQualified: true,
  productionBlockers: [], unresolvedBlockers: [], codes: [],
  expiresAt: 4_102_444_800,
  templateVersionRevision: pagedRevision,
  safetyStages: {
    policy: "paged-two-stage-safety-v1",
    activation: { status: "QUALIFIED", codes: [], requiresSubmittedScan: false, certificateIssued: true },
    execution: { status: "NOT_EVALUATED", requiredBefore: "PROVIDER_REQUEST",
      checks: ["INK_CLEARANCE"], requiresSubmittedScan: true },
  },
};

function pagedReviewFetch(evidence: ReferenceQualification, options: {
  approveResponse?: () => Promise<Response>;
  refreshedState?: PrintState;
  refreshedEvidence?: ReferenceQualification;
  authoritativeTemplate?: Template;
} = {}) {
  const fallback = reviewFetch();
  let printLoads = 0;
  let qualificationLoads = 0;
  return vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(input);
    if (path.endsWith("/template-versions/version-draft/print-layout")) {
      printLoads += 1;
      return Promise.resolve(jsonResponse(printLoads > 1 && options.refreshedState
        ? options.refreshedState : pagedState));
    }
    if (path.endsWith("/template-versions/version-draft/print-layout/qualification") && !init?.method) {
      qualificationLoads += 1;
      return Promise.resolve(jsonResponse(qualificationLoads > 1 && options.refreshedEvidence
        ? options.refreshedEvidence : evidence));
    }
    if (path.includes("/template-versions/version-draft/approve") && init?.method === "POST"
      && options.approveResponse) return options.approveResponse();
    if (path.endsWith("/templates") && !init?.method && options.authoritativeTemplate) {
      return Promise.resolve(jsonResponse([options.authoritativeTemplate]));
    }
    if (path.endsWith("/templates/template-draft") && options.authoritativeTemplate) {
      return Promise.resolve(jsonResponse(options.authoritativeTemplate));
    }
    return fallback(input, init);
  });
}

test.each([false, true])("steps switch immediately during background persistence without losing edits (paged=%s)", async (paged) => {
  const key = "template-layout-draft:version-draft";
  localStorage.removeItem(key);
  const fallback = reviewFetch(undefined, undefined, {
    documentName: { fieldPaths: ["document.unifiedEarTag"], separator: "_", prefix: "", suffix: "" },
  });
  const writes: Array<{ layout: LayoutDefinition; resolve: () => void }> = [];
  const mappingWrites: Array<{ documentName: { prefix: string }; documentIdentityFields: DocumentIdentity[] }> = [];
  let revision = "initial-revision";
  const submittedRevisions: string[] = [];
  const editableLayout: LayoutDefinition = { ...layout,
    cells: [...layout.cells, { ...layout.cells[0], id: "edit-input", x: 150 }] };
  const pageState: PrintState = {
    revision, editable: true,
    source: { sheetName: "sheet", startRow: 1, endRow: 1, width: 300, height: 100,
      rowBoundaries: [], validBreakRows: [], cells: [], unsupported: [], warnings: [],
      importedSettings: { pageSetup: null, margins: null, printOptions: null } },
    saved: { paperSize: "A4", orientation: "portrait", margins: { left: 10, right: 10, top: 10, bottom: 10 },
      targetPagesWide: 1, targetPagesTall: null, horizontalCentering: false, verticalCentering: false,
      commonScale: 1, ocrReady: false, warnings: [], pages: [
        { page: 1, range: "A1:C1", startRow: 1, endRow: 1, width: 300, height: 100,
          transform: { scale: 1, offsetX: 0, offsetY: 0, originX: 0, originY: 0 }, cells: [],
          fields: [{ cellId: "identity-input", sourceRange: "A1", templatePage: 1,
            rectangle: { x: 0, y: 0, width: 100, height: 30 } },
            { cellId: "edit-input", sourceRange: "B1", templatePage: 1,
              rectangle: { x: 150, y: 0, width: 100, height: 30 } }], referenceImage: { sha256: "b".repeat(64) } },
      ] },
  };
  let refreshCount = 0;
  let releaseRefresh!: () => void;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/layout") && !init?.method) return Promise.resolve(jsonResponse(editableLayout));
    if (path.endsWith("/print-layout")) return Promise.resolve(jsonResponse({ ...pageState, revision }));
    if ((path.endsWith("/layout") || path.includes("/print-layout/authoring?")) && init?.method === "PUT") {
      const submitted = JSON.parse(String(init.body)) as LayoutDefinition;
      if (paged) submittedRevisions.push(decodeURIComponent(path.split("base_revision=")[1]));
      return new Promise<Response>(resolve => writes.push({ layout: submitted, resolve: () => {
        revision = `layout-${writes.length}`;
        resolve(jsonResponse(paged ? { layout: submitted, state: { ...pageState, revision } } : submitted));
      } }));
    }
    if (path.endsWith("/field-mappings") && init?.method === "PUT") {
      revision = `mapping-${writes.length}`;
      const body = JSON.parse(String(init.body));
      mappingWrites.push(body);
      return fallback(input, init).then(async response => jsonResponse({ ...await response.json(),
        documentName: body.documentName, documentIdentityFields: body.documentIdentityFields }));
    }
    if (String(input).endsWith("/field-mappings") && !init?.method && ++refreshCount === 2) {
      return new Promise<Response>(resolve => { releaseRefresh = () => { void fallback(input, init).then(resolve); }; });
    }
    return fallback(input, init);
  }));
  try {
    const version = paged ? { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
      quality: { layoutVerified: false, printLayout: pageState.saved } } : draftVersion;
    render(<TemplatePage templates={[{ ...draftTemplate, versions: [version] }]} refresh={vi.fn()} rememberJob={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
    await screen.findByLabelText("帳票レイアウト編集キャンバス");
    await waitFor(() => expect(screen.getByRole("button", { name: "区分変更" })).toBeEnabled());
    vi.useFakeTimers();
    fireEvent.change(screen.getByLabelText("セル一覧から選択"), { target: { value: "edit-input" } });
    fireEvent.click(screen.getByRole("button", { name: "読み取らない" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(writes).toHaveLength(1);
    const next = screen.getByRole("button", { name: "次の工程 →" });
    expect(next).toBeEnabled();
    fireEvent.click(next);
    expect(screen.getByRole("button", { name: "意味付け" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /3.*繰り返し/ }));
    expect(screen.getByRole("button", { name: "繰り返し部分をまとめる" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /4.*照合/ }));
    fireEvent.change(screen.getByLabelText("個体識別番号の照合グループ"), { target: { value: "identity_group_1" } });
    fireEvent.change(screen.getByLabelText("選択中のグループ名"), { target: { value: "変更後の条件" } });
    fireEvent.change(screen.getByLabelText("先頭の固定文字"), { target: { value: "new-prefix_" } });
    fireEvent.click(screen.getByRole("button", { name: /5.*確認/ }));
    expect(screen.getByRole("button", { name: /5.*確認/ })).toHaveAttribute("aria-current", "step");
    fireEvent.click(screen.getByRole("button", { name: /1.*欄の種類/ }));
    fireEvent.change(screen.getByLabelText("セル一覧から選択"), { target: { value: "edit-input" } });
    expect(screen.getByRole("button", { name: "見出し" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "見出し" }));
    await act(async () => writes[0].resolve());
    expect(screen.getByRole("button", { name: "記入欄" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "記入欄" }));
    await act(async () => releaseRefresh());
    fireEvent.click(screen.getByRole("button", { name: /4.*照合/ }));
    expect(screen.getByLabelText("先頭の固定文字")).toHaveValue("new-prefix_");
    expect(screen.getByLabelText("選択中のグループ名")).toHaveValue("変更後の条件");
    // Pending layout edits still auto-save after moving to identity/review.
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(writes).toHaveLength(2);
    expect(writes[1].layout.cells[1]).toMatchObject({ id: "edit-input", role: "input" });
    fireEvent.click(screen.getByRole("button", { name: /1.*欄の種類/ }));
    if (paged) {
      expect(submittedRevisions).toEqual(["initial-revision", "mapping-1"]);
      expect(screen.getByRole("button", { name: "矩形を移動" })).toBeDisabled(); // Existing geometry gate.
    } else expect(screen.getByRole("button", { name: "矩形を移動" })).toBeEnabled();
    await act(async () => writes[1].resolve());
    expect(mappingWrites).toHaveLength(2);
    expect(mappingWrites[0].documentName.prefix).toBe("");
    expect(mappingWrites[1].documentName.prefix).toBe("new-prefix_");
    expect(mappingWrites[1].documentIdentityFields[0]).toMatchObject({
      targetPath: "document.unifiedEarTag", matchGroupLabel: "変更後の条件" });
    fireEvent.click(screen.getByRole("button", { name: /2.*読み取る/ }));
    expect(screen.getByRole("button", { name: "意味付け" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "親項目を接続" })).toBeEnabled();
    expect(localStorage.getItem(key)).toBeNull();
    expect(screen.queryByText(/画面移動を中止/)).not.toBeInTheDocument();
  } finally {
    cleanup(); vi.useRealTimers(); localStorage.removeItem(key);
  }
});

test("failed close retains draft and specific save error without blaming connectivity", async () => {
  const key = "template-layout-draft:version-draft";
  const draft = structuredClone(layout);
  draft.cells[0].text = "unsaved value";
  localStorage.setItem(key, JSON.stringify(draft));
  const detail = "要求merge B3:C3 の内部値 C3 を安全に削除できません。";
  const fallback = reviewFetch();
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/layout") && init?.method === "PUT") {
      return Promise.resolve(jsonResponse({ detail, code: "SPREADSHEET_OUTPUT_MERGE_CONFLICT" }, 409));
    }
    return fallback(input, init);
  }));
  try {
    render(<TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
    const next = await screen.findByRole("button", { name: /一覧へ戻る/ });
    // The navigation renders before the asynchronously loaded editor/draft.
    await screen.findByLabelText("帳票レイアウト編集キャンバス");
    expect(JSON.parse(localStorage.getItem(key)!).cells[0].text).toBe("unsaved value");
    await waitFor(() => expect(next).toBeEnabled());
    fireEvent.click(next);
    expect(await screen.findByText(/編集欄に表示されたエラー内容を確認/)).toBeInTheDocument();
    expect(screen.getByText(detail)).toBeInTheDocument();
    expect(screen.queryByText(/接続状態を確認/)).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(key)!).cells[0].text).toBe("unsaved value");
    expect(screen.getByRole("button", { name: /1.*欄の種類/ })).toHaveAttribute("aria-current", "step");
  } finally {
    cleanup();
    localStorage.removeItem(key);
  }
});

test("failed background save does not block or reverse step changes and retains the draft", async () => {
  const key = "template-layout-draft:version-draft";
  const draft = structuredClone(layout);
  draft.cells[0].text = "recoverable edit";
  localStorage.setItem(key, JSON.stringify(draft));
  const fallback = reviewFetch();
  let writes = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/layout") && init?.method === "PUT") {
      writes++;
      return Promise.resolve(jsonResponse({ detail: "保存競合・下書きを保持" }, 409));
    }
    return fallback(input, init);
  }));
  try {
    render(<TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
    await screen.findByLabelText("帳票レイアウト編集キャンバス");
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "次の工程 →" }));
    expect(screen.getByRole("button", { name: "意味付け" })).toBeEnabled();
    expect(writes).toBe(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(screen.getByText("保存競合・下書きを保持")).toBeInTheDocument();
    expect(screen.queryByText(/画面移動を中止/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /3.*繰り返し/ }));
    expect(screen.getByRole("button", { name: "繰り返し部分をまとめる" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /1.*欄の種類/ }));
    expect(screen.getByRole("button", { name: /1.*欄の種類/ })).toHaveAttribute("aria-current", "step");
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(writes).toBe(1);
    expect(JSON.parse(localStorage.getItem(key)!).cells[0].text).toBe("recoverable edit");
    expect(screen.getByRole("button", { name: "承認して有効化" })).toBeDisabled();
  } finally {
    cleanup(); vi.useRealTimers(); localStorage.removeItem(key);
  }
});

test("saved pages enable manual authoring while formal qualification is unverified", async () => {
  const state: PrintState = {
    revision: "a".repeat(64), editable: true,
    source: { sheetName: "sheet", startRow: 1, endRow: 20, width: 100, height: 100,
      rowBoundaries: [], validBreakRows: [], cells: [], unsupported: [], warnings: [],
      importedSettings: { pageSetup: null, margins: null, printOptions: null } },
    saved: { paperSize: "A4", orientation: "portrait", margins: { left: 10, right: 10, top: 10, bottom: 10 },
      targetPagesWide: 1, targetPagesTall: null, horizontalCentering: false, verticalCentering: false,
      commonScale: 1, ocrReady: false, warnings: [], pages: [
        { page: 1, range: "A1:D10", startRow: 1, endRow: 10, width: 600, height: 800,
          transform: { scale: 2, offsetX: 0, offsetY: 0, originX: 0, originY: 0 }, cells: [],
          fields: [{ cellId: "identity-input", sourceRange: "A1", templatePage: 1,
            rectangle: { x: 100, y: 100, width: 200, height: 60 } }], referenceImage: { sha256: "b".repeat(64) } },
        { page: 2, range: "A11:D20", startRow: 11, endRow: 20, width: 620, height: 820,
          transform: { scale: 2, offsetX: 0, offsetY: 0, originX: 0, originY: 0 }, cells: [],
          fields: [], referenceImage: { sha256: "c".repeat(64) } },
      ] },
  };
  const fallback = reviewFetch(undefined, undefined, {
    documentName: { fieldPaths: ["document.unifiedEarTag"], separator: "_", prefix: "", suffix: "" },
  });
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/print-layout")) return Promise.resolve(jsonResponse(state));
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: false, printLayout: state.saved } };
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [version] }]}
    refresh={vi.fn()} rememberJob={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  const previous = await screen.findByRole("button", { name: "前の編集ページ" });
  const next = screen.getByRole("button", { name: "次の編集ページ" });
  expect(screen.queryByRole("combobox", { name: "編集ページ" })).not.toBeInTheDocument();
  expect(previous).toBeDisabled();
  await waitFor(() => expect(next).toBeEnabled());
  expect(screen.getByRole("button", { name: "区分変更" })).toBeEnabled();
  expect(screen.getByLabelText("帳票レイアウト編集キャンバス")).toHaveAttribute("width", "600");
  fireEvent.click(next);
  await waitFor(() => expect(screen.getByLabelText("帳票レイアウト編集キャンバス")).toHaveAttribute("width", "620"));
  expect(previous).toBeEnabled();
  expect(next).toBeDisabled();
  expect(screen.queryByRole("heading", { name: "XLSXのセル構造を確定" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "承認して有効化" })).toBeDisabled();
  expect(screen.getByLabelText("テンプレート設定の状態")).not.toHaveTextContent("有効化は未対応");
});

test.each([
  {
    label: "UNVERIFIED",
    evidence: { status: "UNVERIFIED", qualified: false } satisfies ReferenceQualification,
    message: "現在の版は資格確認が必要です",
  },
  {
    label: "FAIL",
    evidence: { status: "FAIL", qualified: false, codes: ["GEOMETRY_TOLERANCE_UNQUALIFIED"] } satisfies ReferenceQualification,
    message: "正式資格が成立していません",
  },
  {
    label: "EXPIRED",
    evidence: { ...passQualification, expiresAt: 1 } satisfies ReferenceQualification,
    message: "正式資格の期限が切れています",
  },
])("paged $label keeps approval closed and shows the backend qualification state", async ({ evidence, message }) => {
  vi.stubGlobal("fetch", pagedReviewFetch(evidence));
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: false, printLayout: pagedState.saved } };
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [version] }]}
    refresh={vi.fn()} rememberJob={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  await screen.findByText(message);
  expect(screen.getByRole("button", { name: "承認して有効化" })).toBeDisabled();
  expect(screen.getByLabelText("テンプレート設定の状態")).not.toHaveTextContent("有効化は未対応");
  if (evidence.codes?.length) {
    expect(screen.getByLabelText("正式資格の未解決条件")).toHaveTextContent(evidence.codes[0]);
  }
});

test("paged PASS uses the current print revision and confirms authoritative ACTIVE state", async () => {
  let approveCalls = 0;
  const activeVersion = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const, status: "ACTIVE" as const,
    quality: { printLayout: pagedState.saved } };
  const oldVersion = { ...draftVersion, id: "version-old", versionNumber: 1, status: "ARCHIVED" as const };
  const authoritativeTemplate = { ...draftTemplate, activeVersionId: activeVersion.id,
    versions: [activeVersion, oldVersion] };
  const fetch = pagedReviewFetch(passQualification, {
    approveResponse: () => {
      approveCalls += 1;
      return Promise.resolve(jsonResponse(activeVersion));
    },
    authoritativeTemplate,
  });
  vi.stubGlobal("fetch", fetch);
  const refresh = vi.fn(async () => undefined);
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: false, printLayout: pagedState.saved } };
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [version] }]}
    refresh={refresh} rememberJob={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  await screen.findByText("この版の正式資格は有効です。");
  const approve = screen.getByRole("button", { name: "承認して有効化" });
  expect(approve).toBeEnabled();
  fireEvent.click(approve);
  fireEvent.click(approve);
  expect(await screen.findByText("このテンプレートは有効です")).toBeInTheDocument();
  expect(approveCalls).toBe(1);
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls.some(([url, init]) => (
    String(url).endsWith(`/template-versions/version-draft/approve?base_revision=${pagedRevision}`)
    && init?.method === "POST"
  ))).toBe(true);
  expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/templates/template-draft"))).toBe(true);
  expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/templates"))).toBe(false);
  expect(screen.queryByRole("button", { name: "承認して有効化" })).not.toBeInTheDocument();
});

test("paged qualification loading blocks duplicate actions and explicit recheck can enable approval", async () => {
  const fallback = reviewFetch();
  let resolveStatus!: (response: Response) => void;
  const pendingStatus = new Promise<Response>((resolve) => { resolveStatus = resolve; });
  let qualificationPosts = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/print-layout")) return Promise.resolve(jsonResponse(pagedState));
    if (path.endsWith("/print-layout/qualification") && !init?.method) return pendingStatus;
    if (path.includes("/print-layout/qualification?base_revision=") && init?.method === "POST") {
      qualificationPosts += 1;
      return Promise.resolve(jsonResponse(passQualification));
    }
    return fallback(input, init);
  }));
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: false, printLayout: pagedState.saved } };
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [version] }]}
    refresh={vi.fn()} rememberJob={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  expect(await screen.findByText("LOADING")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "資格を再確認" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "承認して有効化" })).toBeDisabled();
  resolveStatus(jsonResponse({ status: "UNVERIFIED", qualified: false }));
  const recheck = await screen.findByRole("button", { name: "資格を再確認" });
  await waitFor(() => expect(recheck).toBeEnabled());
  fireEvent.click(recheck);
  fireEvent.click(recheck);
  await screen.findByText("この版の正式資格は有効です。");
  expect(qualificationPosts).toBe(1);
  expect(screen.getByRole("button", { name: "承認して有効化" })).toBeEnabled();
});

test("paged stale revision refreshes revision and qualification without retrying approval", async () => {
  const nextRevision = "c".repeat(64);
  const fetch = pagedReviewFetch(passQualification, {
    approveResponse: () => Promise.resolve(jsonResponse({
      code: "PRINT_LAYOUT_CONFLICT", detail: "設定が更新されています。",
    }, 409)),
    refreshedState: { ...pagedState, revision: nextRevision },
    refreshedEvidence: { status: "UNVERIFIED", qualified: false, codes: ["PRINT_LAYOUT_CHANGED"] },
  });
  vi.stubGlobal("fetch", fetch);
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: false, printLayout: pagedState.saved } };
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [version] }]}
    refresh={vi.fn()} rememberJob={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  await screen.findByText("この版の正式資格は有効です。");
  fireEvent.click(screen.getByRole("button", { name: "承認して有効化" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("承認は自動再送していません");
  expect(fetch.mock.calls.filter(([url]) => String(url).includes("/approve?"))).toHaveLength(1);
  await screen.findByText("現在の版は資格確認が必要です。");
  expect(screen.getByRole("button", { name: "承認して有効化" })).toBeDisabled();
});

test("saved print layout exposes version-bound downloads in template management", async () => {
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { printLayout: { pages: [{ startRow: 1, endRow: 10 }] } } };
  const fetch = vi.fn((input: RequestInfo | URL) => {
    if (String(input).endsWith("/print-layout")) return Promise.resolve(jsonResponse({ revision: "r1" }));
    if (String(input).endsWith("/files/pdf?base_revision=r1")) {
      return Promise.resolve(jsonResponse({ detail: "隔離環境でPDF変換できません。" }, 409));
    }
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  vi.stubGlobal("fetch", fetch);
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [version] }]}
    refresh={vi.fn(async () => undefined)} rememberJob={vi.fn()} />);
  expect(screen.getByRole("button", { name: "印刷用XLSX" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "印刷用PDF" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("隔離環境");
  expect(fetch.mock.calls.some(([url]) => String(url).includes(
    "/template-versions/version-draft/print-layout/files/pdf?base_revision=r1"))).toBe(true);
});

async function openDraftReview() {
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: "承認して有効化" })).toBeEnabled());
}

test("print controls are exclusive to XLSX cards and explain missing settings", () => {
  const spreadsheet = { ...draftTemplate, id: "xlsx-template", name: "未設定XLSX", versions: [
    { ...draftVersion, id: "xlsx-version", mode: "SPREADSHEET_TEMPLATE" as const },
  ] };
  render(<TemplatePage templates={[draftTemplate, spreadsheet]} refresh={vi.fn()} rememberJob={vi.fn()} />);
  expect(screen.getAllByRole("button", { name: "印刷用XLSX" })).toHaveLength(1);
  expect(screen.getByRole("button", { name: "印刷用XLSX" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "印刷用PDF" })).toBeDisabled();
  expect(screen.getByText("印刷ページ設定が保存されていません。")).toBeInTheDocument();
});

test("each template card downloads its displayed version without mixing cards or versions", async () => {
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const, quality: { printLayout: { pages: [{}] } } };
  const templates = ["a", "b"].map(id => ({ ...draftTemplate, id, name: `帳票${id}`, versions: [
    { ...version, id: `${id}-displayed` },
    { ...version, id: `${id}-older`, versionNumber: 1, status: "ARCHIVED" as const },
  ] }));
  const fetch = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/print-layout")) return Promise.resolve(jsonResponse({ revision: url.includes("a-displayed") ? "ra" : "rb" }));
    if (url.includes("/files/")) return Promise.resolve(new Response(new Blob(["fixture"])));
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<TemplatePage templates={templates} refresh={vi.fn()} rememberJob={vi.fn()} />);
  fireEvent.click(screen.getAllByRole("button", { name: "印刷用XLSX" })[0]);
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).includes("/a-displayed/print-layout/files/xlsx?base_revision=ra"))).toBe(true));
  fireEvent.click(screen.getAllByRole("button", { name: "印刷用PDF" })[1]);
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).includes("/b-displayed/print-layout/files/pdf?base_revision=rb"))).toBe(true));
  expect(fetch.mock.calls.some(([url]) => String(url).includes("-older"))).toBe(false);
  await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(2));
});

test("card print downloads keep the active version while a newer draft is edited", async () => {
  const active = { ...draftVersion, id: "published-xlsx", versionNumber: 1, status: "ACTIVE" as const,
    mode: "SPREADSHEET_TEMPLATE" as const, quality: { printLayout: { pages: [{}] } } };
  const draft = { ...active, id: "editing-xlsx", versionNumber: 2, status: "DRAFT" as const };
  const fetch = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/print-layout")) return Promise.resolve(jsonResponse({ revision: "published-revision" }));
    if (url.includes("/files/")) return Promise.resolve(new Response(new Blob(["fixture"])));
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<TemplatePage templates={[{ ...draftTemplate, activeVersionId: active.id, versions: [draft, active] }]}
    refresh={vi.fn()} rememberJob={vi.fn()} />);
  expect(screen.getByText("使用中 v1 の印刷用帳票")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /確認を再開/ })).toBeEnabled();
  for (const format of ["XLSX", "PDF"]) {
    fireEvent.click(screen.getByRole("button", { name: `印刷用${format}` }));
    await screen.findByText(`印刷用${format}を取得し、ブラウザーへダウンロードを渡しました。`);
    expect(fetch.mock.calls.some(([url]) => String(url).includes(
      `/published-xlsx/print-layout/files/${format.toLowerCase()}?base_revision=published-revision`))).toBe(true);
  }
  expect(fetch.mock.calls.some(([url]) => String(url).includes("editing-xlsx"))).toBe(false);
});

test("active editing opens and reuses the server draft without saving the active version", async () => {
  const active = { ...draftVersion, id: "version-active", status: "ACTIVE" as const,
    versionNumber: 1, sourceJobId: "job-active" };
  const draft = { ...draftVersion, quality: { layoutVerified: true, forkParentVersionId: active.id } };
  const fallback = reviewFetch();
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/version-active/edit")) return Promise.resolve(jsonResponse(draft));
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  const props = { templates: [{ ...draftTemplate, versions: [active], activeVersionId: active.id }],
    refresh: vi.fn(async () => undefined), rememberJob: vi.fn(), initialEditVersionId: active.id };
  const view = render(<TemplatePage {...props} />);
  await screen.findByRole("button", { name: "承認して有効化" });
  expect(screen.queryByRole("button", { name: "選別マスクから再生成" })).not.toBeInTheDocument();
  await waitFor(() => expect(fetch.mock.calls.some(
    ([url]) => String(url).endsWith("/version-draft/layout"),
  )).toBe(true));
  expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/version-active/layout"))).toBe(false);
  view.unmount();
  render(<TemplatePage {...props} />);
  await screen.findByRole("button", { name: "承認して有効化" });
  expect(fetch.mock.calls.filter(([url]) => String(url).endsWith("/version-active/edit"))).toHaveLength(2);
});

test("paged active editing sends its current revision and resumes the existing draft", async () => {
  const active = { ...draftVersion, id: "version-active", status: "ACTIVE" as const,
    versionNumber: 1, sourceJobId: "job-active", mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { printLayout: pagedState.saved } };
  const draft = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { printLayout: pagedState.saved, forkParentVersionId: active.id } };
  const fallback = reviewFetch();
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/version-active/print-layout")) return Promise.resolve(jsonResponse(pagedState));
    if (path.endsWith(`/version-active/edit?base_revision=${pagedRevision}`)) {
      return Promise.resolve(jsonResponse(draft));
    }
    if (path.endsWith("/version-draft/print-layout")) return Promise.resolve(jsonResponse(pagedState));
    if (path.endsWith("/version-draft/print-layout/qualification")) {
      return Promise.resolve(jsonResponse({ status: "UNVERIFIED", qualified: false }));
    }
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [active], activeVersionId: active.id }]}
    refresh={vi.fn(async () => undefined)} rememberJob={vi.fn()} initialEditVersionId={active.id} />);
  await screen.findByText("現在の版は資格確認が必要です。");
  expect(fetch.mock.calls.filter(([url]) => String(url).includes("/version-active/edit"))).toHaveLength(1);
  expect(fetch.mock.calls.some(([url, init]) => (
    String(url).endsWith(`/version-active/edit?base_revision=${pagedRevision}`)
    && init?.method === "POST"
  ))).toBe(true);
  expect(screen.getByText("TEMPLATE REVIEW · V2")).toBeInTheDocument();
});

test("failed draft creation leaves the active version unopened and can be retried", async () => {
  const active = { ...draftVersion, id: "version-active", status: "ACTIVE" as const };
  const fallback = reviewFetch();
  let fail = true;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/version-active/edit")) {
      return Promise.resolve(fail ? jsonResponse({ detail: "作成失敗" }, 409) : jsonResponse(draftVersion));
    }
    return fallback(input, init);
  }));
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [active] }]}
    refresh={vi.fn(async () => undefined)} rememberJob={vi.fn()} initialEditVersionId={active.id} />);
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "承認して有効化" })).not.toBeInTheDocument();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "再試行" }));
  await screen.findByRole("button", { name: "承認して有効化" });
});

test("does not show the mapping warning panel when there are no visible warnings", async () => {
  vi.stubGlobal("fetch", reviewFetch());
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));

  await screen.findByLabelText("テンプレート設定の状態");
  expect(screen.queryByRole("complementary", { name: "テンプレート設定の要対応事項" }))
    .not.toBeInTheDocument();
});

test("shows each mapping warning once in the fixed panel and toggles its list", async () => {
  const warnings = [
    "保存先が未設定の読み取り欄があります。",
    "複数の読み取り欄が同じ保存先を使用しています。",
  ];
  vi.stubGlobal("fetch", reviewFetch(undefined, undefined, { warnings }));
  const { container } = render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));

  const panel = await screen.findByRole("complementary", { name: "テンプレート設定の要対応事項" });
  expect(within(panel).getByRole("button", { name: /要対応 2件 折りたたむ/ }))
    .toHaveAttribute("aria-expanded", "true");
  for (const warning of warnings) {
    expect(screen.getAllByText(warning)).toHaveLength(1);
  }
  expect(container.querySelectorAll(".page-stack > .error-box")).toHaveLength(0);

  fireEvent.click(within(panel).getByRole("button", { name: /折りたたむ/ }));
  expect(within(panel).getByRole("button", { name: /要対応 2件 展開する/ }))
    .toHaveAttribute("aria-expanded", "false");
  for (const warning of warnings) {
    expect(within(panel).queryByText(warning)).not.toBeInTheDocument();
  }

  fireEvent.click(within(panel).getByRole("button", { name: /展開する/ }));
  expect(within(panel).getByRole("button", { name: /要対応 2件 折りたたむ/ }))
    .toHaveAttribute("aria-expanded", "true");
  for (const warning of warnings) {
    expect(screen.getAllByText(warning)).toHaveLength(1);
  }
});

test.each([
  {
    name: "missing mapped cells",
    warning: "読み取りに使用できる意味づけ済みセルがありません。",
    expectedHeading: "各欄で読み取る内容を決める",
    overrides: {},
  },
  {
    name: "missing identity settings",
    warning: "書類を見分ける項目が未設定です。",
    expectedHeading: "同じ書類を探す条件と書類名を決める",
    overrides: {
      documentIdentity: null,
      documentIdentityFields: [],
      documentName: null,
      validForOcr: false,
    },
  },
  {
    name: "missing mark options",
    warning: "[MARK_OPTIONS_REQUIRED] 選択欄: 選択肢を1件以上設定してください。",
    expectedHeading: "各欄で読み取る内容を決める",
    overrides: {},
  },
])("moves from a $name warning to its workflow step and focuses the heading", async ({
  warning,
  expectedHeading,
  overrides,
}) => {
  vi.stubGlobal("fetch", reviewFetch(undefined, undefined, { warnings: [warning], ...overrides }));
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));

  const panel = await screen.findByRole("complementary", { name: "テンプレート設定の要対応事項" });
  const action = within(panel).getByText(warning).closest("button");
  expect(action).not.toBeNull();
  fireEvent.click(action!);

  const heading = await screen.findByRole("heading", { name: expectedHeading });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(scrollIntoViewMock).toHaveBeenLastCalledWith({ behavior: "smooth", block: "center" });
  expect(within(panel).getByText(warning)).toBeInTheDocument();
});

test("classifies all existing identity and document-name setting warnings as Step 4", () => {
  expect([
    "書類を見分ける項目が未設定です。",
    "書類を見分ける項目を、使用中の文字欄から選び直してください。",
    "書類を見分ける基準を1つ指定してください。",
    "一覧で大きく表示する項目を1つ指定してください。",
    "従来の照合条件と照合グループは混在できません。",
    "照合グループ名を入力してください。",
    "ライブラリと出力で使う書類名の作り方が未設定です。",
  ].map(mappingWarningWorkflowStep)).toEqual([
    "identity", "identity", "identity", "identity", "identity", "identity", "identity",
  ]);
});

test("parses only the exact structured mark-options field path", () => {
  expect(markOptionsTargetFromFieldErrors([{
    field: "cells.mark-cell.markSets.choice.options",
    message: "required",
  }])).toEqual({ cellId: "mark-cell", markSetId: "choice" });
  expect(markOptionsTargetFromFieldErrors([{
    field: "cells.mark-cell.markSets.choice",
    message: "malformed",
  }])).toBeNull();
  expect(markOptionsTargetFromFieldErrors([{
    field: "cells.mark-cell.markSets.choice.options.extra",
    message: "unknown",
  }])).toBeNull();
  expect(markOptionsTargetFromFieldErrors([
    { field: "cells.first.markSets.choice.options", message: "first" },
    { field: "cells.second.markSets.choice.options", message: "second" },
  ])).toBeNull();
});

test("resolves a saved XLSX page only from a unique field or source-part cell binding", () => {
  const first = pagedState.saved!.pages[0];
  const second = {
    ...first,
    page: 2,
    range: "A11:D20",
    startRow: 11,
    endRow: 20,
    fields: [{
      cellId: "merged-cell",
      sourceRange: "B12:C12",
      templatePage: 2,
      sourceParts: [{ cellId: "mark-cell", sourceRange: "C12" }],
      rectangle: { x: 100, y: 200, width: 240, height: 60 },
    }],
  };
  expect(savedPrintPageForCell([first, second], "mark-cell")).toBe(second);
  expect(savedPrintPageForCell([first, second], "missing")).toBeNull();
  expect(savedPrintPageForCell([second, { ...second, page: 3 }], "mark-cell")).toBeNull();
});

test("keeps an unknown warning as text without adding a workflow action", async () => {
  const warning = "未分類の警告です。";
  vi.stubGlobal("fetch", reviewFetch(undefined, undefined, { warnings: [warning] }));
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));

  const panel = await screen.findByRole("complementary", { name: "テンプレート設定の要対応事項" });
  const message = within(panel).getByText(warning);
  expect(mappingWarningWorkflowStep(warning)).toBeNull();
  expect(message).toHaveClass("mapping-warning-message");
  expect(message.closest("button")).toBeNull();
});

test("disables warning navigation while workflow navigation is busy without making a request", async () => {
  const warning = "読み取りに使用できる意味づけ済みセルがありません。";
  const fallback = reviewFetch(undefined, undefined, { warnings: [warning] });
  const printLayout = new Promise<Response>(() => undefined);
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(input);
    if (path.endsWith("/template-versions/version-draft/print-layout")) {
      return printLayout;
    }
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: true, printLayout: pagedState.saved } };
  render(
    <TemplatePage templates={[{ ...draftTemplate, versions: [version] }]}
      refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));

  const panel = await screen.findByRole("complementary", { name: "テンプレート設定の要対応事項" });
  const action = within(panel).getByText(warning).closest("button");
  expect(action).not.toBeNull();
  await waitFor(() => expect(fetch.mock.calls.some(([input]) =>
    String(input).endsWith("/template-versions/version-draft/print-layout"))).toBe(true));
  await waitFor(() => expect(action).toBeDisabled());
  expect(within(action!).getByText("現在の処理が完了するまで移動できません。")).toBeInTheDocument();
  const requestsBeforeClick = fetch.mock.calls.length;

  fireEvent.click(action!);
  await act(async () => { await Promise.resolve(); });

  expect(fetch).toHaveBeenCalledTimes(requestsBeforeClick);
  expect(screen.getByRole("heading", { name: "検出された枠の種類を確認する" })).not.toHaveFocus();
});

test("opens the saved XLSX page identified by sourceParts before focusing the mark cell", async () => {
  const warning = "[MARK_OPTIONS_REQUIRED] 別ページの選択欄: 選択肢を設定してください。";
  const markLayout: LayoutDefinition = {
    ...layout,
    cells: [...layout.cells, {
      id: "mark-page-2", role: "mark", x: 120, y: 40, width: 80, height: 30,
      confidence: 1, text: "別ページの選択欄", textConfidence: null, source: "manual",
      markSets: [{ id: "choice", targetPath: "customFields.choice", recognitionMode: "manual",
        selectionMode: "single", options: [] }],
    }],
  };
  const pageTwo = {
    ...pagedState.saved!.pages[0],
    page: 2,
    range: "A11:D20",
    startRow: 11,
    endRow: 20,
    cells: [{
      sourceRange: "C12", startRow: 12, endRow: 12, x: 120, y: 40, width: 80, height: 30,
      text: "", fill: "#ffffff", fontSize: 12, bold: false, align: "left", borders: {},
      rectangle: { x: 140, y: 180, width: 160, height: 60 },
    }],
    fields: [{
      cellId: "merged-page-2", sourceRange: "B12:C12", templatePage: 2,
      sourceParts: [{ cellId: "mark-page-2", sourceRange: "C12" }],
      rectangle: { x: 100, y: 180, width: 200, height: 60 },
    }],
    referenceImage: { sha256: "c".repeat(64) },
  };
  const pageState: PrintState = {
    ...pagedState,
    saved: { ...pagedState.saved!, pages: [pagedState.saved!.pages[0], pageTwo] },
  };
  const fallback = reviewFetch(undefined, undefined, {
    warnings: [warning],
    fieldErrors: [{
      field: "cells.mark-page-2.markSets.choice.options",
      message: "選択肢を設定してください。",
    }],
    validForOcr: false,
  });
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(input);
    if (path.endsWith("/template-versions/version-draft/layout")) {
      return Promise.resolve(jsonResponse(markLayout));
    }
    if (path.endsWith("/template-versions/version-draft/print-layout")) {
      return Promise.resolve(jsonResponse(pageState));
    }
    if (path.endsWith("/template-versions/version-draft/print-layout/qualification")) {
      return Promise.resolve(jsonResponse(passQualification));
    }
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  const version = { ...draftVersion, mode: "SPREADSHEET_TEMPLATE" as const,
    quality: { layoutVerified: false, printLayout: pageState.saved } };
  render(<TemplatePage templates={[{ ...draftTemplate, versions: [version] }]}
    refresh={vi.fn()} rememberJob={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  const nextPage = await screen.findByRole("button", { name: "次の編集ページ" });
  await waitFor(() => expect(nextPage).toBeEnabled());
  const panel = await screen.findByRole("complementary", { name: "テンプレート設定の要対応事項" });
  const action = within(panel).getByText(warning).closest("button")!;
  const fieldMappingRequests = () => fetch.mock.calls.filter(([input]) =>
    String(input).endsWith("/field-mappings")).length;
  const beforeFieldMappings = fieldMappingRequests();

  fireEvent.click(action);

  await waitFor(() => expect(screen.getByLabelText("セル一覧から選択")).toHaveValue("mark-page-2"));
  expect(screen.getByRole("button", { name: "前の編集ページ" })).toHaveAttribute("title", "2 / 2ページ");
  expect(screen.getByLabelText("対象セルの選択肢エディタ")).toHaveFocus();
  expect(fieldMappingRequests()).toBe(beforeFieldMappings);
  expect(fetch.mock.calls.some(([, init]) => ["POST", "PUT", "PATCH", "DELETE"].includes(init?.method ?? "")))
    .toBe(false);
  expect(fetch.mock.calls.some(([input]) => /\/ocr(?:\/|$)/.test(String(input)))).toBe(false);
});

test("keeps identity-filtered mapping warnings out of the fixed panel", async () => {
  const visibleWarning = "読み取りに使用できる意味づけ済みセルがありません。";
  const excludedWarning = "書類を見分ける項目が未設定です。";
  vi.stubGlobal("fetch", reviewFetch(undefined, undefined, {
    warnings: [visibleWarning, excludedWarning],
  }));
  render(
    <TemplatePage templates={[draftTemplate]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));

  const panel = await screen.findByRole("complementary", { name: "テンプレート設定の要対応事項" });
  expect(within(panel).getByRole("button", { name: /要対応 1件/ })).toBeInTheDocument();
  expect(within(panel).getByText(visibleWarning)).toBeInTheDocument();
  expect(screen.queryByText(excludedWarning)).not.toBeInTheDocument();
});

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

test("shows the supported image and spreadsheet creation modes in order", () => {
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  const modeGroup = screen.getByRole("group", { name: "準備方法" });
  const modeButtons = within(modeGroup).getAllByRole("button");
  expect(modeButtons).toHaveLength(3);
  expect(modeButtons[0]).toHaveTextContent("記入済みの一枚から");
  expect(modeButtons[1]).toHaveTextContent("白紙帳票から");
  expect(modeButtons[2]).toHaveTextContent("元のExcelから");

  const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
  expect(fileInput).not.toHaveAttribute("multiple");
});

test("prefills an editable template name from the selected XLSX filename", () => {
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /元のExcelから/ }));
  const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
  fireEvent.change(fileInput!, {
    target: {
      files: [new File(["xlsx"], "繁殖台帳.XLSX", {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      })],
    },
  });

  const nameInput = screen.getByLabelText("テンプレート名");
  expect(nameInput).toHaveValue("繁殖台帳");
  fireEvent.change(nameInput, { target: { value: "繁殖台帳 2026年版" } });
  expect(nameInput).toHaveValue("繁殖台帳 2026年版");
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

test("honors the nested reading entry point and returns to reading", () => {
  const onClose = vi.fn();
  render(
    <TemplatePage
      templates={[draftTemplate]}
      refresh={vi.fn()}
      rememberJob={vi.fn()}
      initialWorkspace="create"
      onClose={onClose}
    />,
  );

  expect(screen.getByRole("heading", { name: "テンプレート作成・編集" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "新規作成" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("button", { name: "← 読み取りへ戻る" }));
  expect(onClose).toHaveBeenCalledOnce();
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

test("does not ask for field names before template preparation", async () => {
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

  expect(screen.queryByRole("heading", { name: "この書類から保存する情報" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("項目名")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "項目を追加" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "バイク領収書" },
  });
  selectTemplateImage(container);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));

  await waitFor(() => expect(submittedFieldNames).toEqual([]));
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

test("clears the completed preparation progress after rejecting its draft", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const preparedVersion = { ...draftVersion, sourceJobId: "template-job-1" };
  const preparedTemplate = { ...draftTemplate, versions: [preparedVersion] };
  const fallback = reviewFetch(
    "/template-versions/version-draft/reject",
    () => Promise.resolve(jsonResponse({})),
  );
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/template-jobs") && init?.method === "POST") {
      return Promise.resolve(jsonResponse({ jobId: "template-job-1" }, 202));
    }
    if (path.endsWith("/jobs/template-job-1")) {
      return Promise.resolve(jsonResponse(job("REVIEW_REQUIRED")));
    }
    if (path.endsWith("/templates") && init?.method !== "POST") {
      return Promise.resolve(jsonResponse([preparedTemplate]));
    }
    if (path.endsWith("/templates/template-draft")) {
      return Promise.resolve(jsonResponse(preparedTemplate));
    }
    if (path.endsWith("/jobs/template-job-1/artifacts")) {
      return Promise.resolve(jsonResponse({
        items: [{ name: "normalized", mediaType: "image/png", url: "/source.png" }],
      }));
    }
    return fallback(input, init);
  }));
  const { container } = render(
    <TemplatePage templates={[]} refresh={vi.fn().mockResolvedValue(undefined)} rememberJob={vi.fn()} />,
  );

  fireEvent.change(screen.getByLabelText("テンプレート名"), {
    target: { value: "繁殖台帳A" },
  });
  selectTemplateImage(container);
  fireEvent.click(screen.getByRole("button", { name: "テンプレート準備を開始" }));
  await screen.findByRole("button", { name: "承認して有効化" });
  fireEvent.click(screen.getByRole("button", { name: "却下" }));

  await screen.findByRole("heading", { name: "新しいテンプレートを作成" });
  await waitFor(() => {
    expect(screen.queryByText("処理を準備しています")).not.toBeInTheDocument();
    expect(container.querySelector(".progress-card")).not.toBeInTheDocument();
  });
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

  fireEvent.click(within(workflowNavigation).getByRole("button", { name: /4.*照合と書類名/ }));

  expect(screen.getByLabelText("書類を見分ける欄を選択する帳票プレビュー")).toBeInTheDocument();
  expect(screen.getByText("4-A 同じ書類を探す条件")).toBeInTheDocument();
  expect(screen.getByText("4-B ライブラリ・出力で使う書類名")).toBeInTheDocument();
  expect(screen.getAllByText("個体識別番号").length).toBeGreaterThan(0);

  fireEvent.click(within(workflowNavigation).getByRole("button", { name: /5.*確認・保存/ }));
  expect(screen.getByText("照合条件")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "レイアウトは保存済み" })).toBeDisabled();
});

test.each(["photo", "xlsx", "paged"] as const)("persists the selected document rectangle and gives the correct next step (%s)", async variant => {
  const pageState: PrintState = {
    revision: "a".repeat(64), editable: true,
    source: { sheetName: "sheet", startRow: 1, endRow: 2, width: 300, height: 200,
      rowBoundaries: [], validBreakRows: [], cells: [], unsupported: [], warnings: [],
      importedSettings: { pageSetup: null, margins: null, printOptions: null } },
    saved: variant === "paged" ? { paperSize: "A4", orientation: "portrait",
      margins: { left: 10, right: 10, top: 10, bottom: 10 }, targetPagesWide: 1, targetPagesTall: 2,
      horizontalCentering: false, verticalCentering: false, commonScale: 1, ocrReady: false, warnings: [],
      pages: [1, 2].map(page => ({ page, range: `A${page}:C${page}`, startRow: page, endRow: page,
        width: 300, height: 100, cells: [], transform: { scale: 1, offsetX: 0, offsetY: 0, originX: 0, originY: 0 },
        fields: page === 1 ? [{ cellId: "identity-input", sourceRange: "A1", templatePage: 1,
          rectangle: { x: 0, y: 0, width: 100, height: 30 } }] : [],
        referenceImage: { sha256: "b".repeat(64) } })),
    } : null,
  };
  let savedIdentity: unknown = null;
  let savedIdentityFields: unknown = null;
  let savedDocumentName: unknown = null;
  const mappingResponse = (
    documentIdentity: unknown,
    validForOcr: boolean,
    documentIdentityFields: unknown[] = [],
    documentName: unknown = null,
  ) => ({
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
    documentName,
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
    if (path.endsWith("/print-layout")) return jsonResponse(pageState);
    if (path.includes("/print-layout/authoring?") && init?.method === "PUT") {
      return jsonResponse({ layout: JSON.parse(String(init.body)), state: pageState });
    }
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
        savedDocumentName = body.documentName;
        return jsonResponse(mappingResponse(
          body.documentIdentity,
          true,
          body.documentIdentityFields,
          body.documentName,
        ));
      }
      return jsonResponse(mappingResponse(null, false));
    }
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  }));
  render(
    <TemplatePage templates={[{ ...draftTemplate, versions: [{ ...draftVersion,
      mode: variant === "photo" ? "REFERENCE_TEMPLATE" : "SPREADSHEET_TEMPLATE",
      quality: { layoutVerified: true, ...(variant === "paged" ? { printLayout: pageState.saved } : {}) },
    }] }]} refresh={vi.fn()} rememberJob={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: /確認を再開: 繁殖台帳A/ }));
  const workflowNavigation = await screen.findByRole("navigation", { name: "テンプレート設定の工程" });
  const identityStep = within(workflowNavigation).getByRole("button", { name: /4.*照合と書類名/ });
  // The navigation can mount before the saved page's initial revision has loaded.
  // This case tests persistence after initial load, not clicking a disabled control.
  await waitFor(() => expect(identityStep).toBeEnabled());
  fireEvent.click(identityStep);
  const canvas = screen.getByLabelText("書類を見分ける欄を選択する帳票プレビュー");
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 300, bottom: 100,
    width: 300, height: 100, toJSON: () => ({}),
  });
  fireEvent.click(canvas, { clientX: 50, clientY: 15 });
  fireEvent.click(screen.getByRole("checkbox", { name: "個体識別番号" }));
  fireEvent.click(within(workflowNavigation).getByRole("button", { name: /5.*確認・保存/ }));
  fireEvent.click(screen.getByRole("button", { name: "テンプレート設定をDBへ保存" }));

  await waitFor(() => expect(savedIdentity).toBeNull());
  expect(savedIdentityFields).toEqual([{
    mappingId: "layout_identity-input",
    targetPath: "document.unifiedEarTag",
    label: "個体識別番号",
    valueType: "digits",
    exactLength: 10,
    matchRole: "confirmation",
    displayRole: "none",
    matchGroupId: "identity_group_1",
    matchGroupLabel: "照合条件1",
    componentOrder: 0,
  }]);
  expect(savedDocumentName).toEqual({
    fieldPaths: ["document.unifiedEarTag"],
    separator: "",
    prefix: "",
    suffix: "",
  });
  expect(await screen.findByRole("button", { name: "レイアウトは保存済み" })).toBeDisabled();
  if (variant === "photo") {
    expect(screen.getByText(/保存しました。次に記入欄を再検出してください。/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "記入欄を再検出" })).toBeEnabled());
  } else {
    expect(screen.queryByText(/次に記入欄を再検出してください/)).not.toBeInTheDocument();
    if (variant === "xlsx") {
      expect(screen.getByText(/保存しました。次に「XLSX構造を確定」を押してください。/)).toBeInTheDocument();
      await waitFor(() => expect(screen.getByRole("button", { name: "XLSX構造を確定" })).toBeEnabled());
    } else {
      expect(screen.getByText(/下書きとして保存しました。現在の版について正式資格を再確認してください。/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "XLSX構造を確定" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "承認して有効化" })).toBeDisabled();
      expect(screen.getByLabelText("テンプレート設定の状態")).toHaveTextContent("UNVERIFIED");
      expect(screen.getByLabelText("テンプレート設定の状態")).not.toHaveTextContent("有効化は未対応");
      expect(screen.queryByText("設定を保存し、読み取り確認を完了してから承認してください。")).not.toBeInTheDocument();
    }
  }
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
