import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import fixture from "../../../tests/fixtures/mark-mapping-validation.json";
import { incompleteMeaningCellIds, unassignedMeaningCellIds } from "./LayoutEditor";
import { TemplatePage } from "../pages/TemplatePage";
import type { LayoutDefinition, Template } from "../types";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test.each(fixture.cases)("shared mark validation: $state", (scenario) => {
  const layout = structuredClone(fixture.layout) as LayoutDefinition;
  const mark = layout.cells[2].markSets![0];
  mark.targetPath = scenario.targetPath;
  mark.options = scenario.hasOptions ? [fixture.option] : [];
  expect(unassignedMeaningCellIds(layout).size).toBe(scenario.unassigned);
  expect(incompleteMeaningCellIds(layout).size).toBe(scenario.incomplete);
});

test("mixed mark sets cannot hide an incomplete set behind a valid one", () => {
  const layout = structuredClone(fixture.layout) as LayoutDefinition;
  layout.cells[1].markSets!.push({ ...layout.cells[2].markSets![0], id: "other", targetPath: "" });
  expect(unassignedMeaningCellIds(layout).size).toBe(0);
  expect([...incompleteMeaningCellIds(layout)]).toEqual(["mark_valid", "mark_incomplete"]);
});

test("draft save keeps identity and shows the incomplete mark instead of a secondary text error", async () => {
  const createdAt = "2026-09-11T00:00:00Z";
  const template: Template = {
    id: "minimal-template", name: "最小再現", documentType: "minimal", activeVersionId: null, createdAt,
    versions: [{ id: "minimal-version", versionNumber: 1, mode: "REFERENCE_TEMPLATE", status: "DRAFT",
      sourceJobId: "minimal-job", canonicalWidth: 100, canonicalHeight: 30, quality: {}, createdAt }],
  };
  const identity = { mappingId: "layout_text_input", targetPath: "customFields.textValue", label: "正常テキスト",
    valueType: "text", exactLength: null, matchRole: "primary", displayRole: "title" };
  const text = { ...identity, x: 0, y: 0, width: 20, height: 20, padding: 4,
    enabled: true, modelDetail: "high", mappingType: "text" };
  const before = {
    canonicalWidth: 100, canonicalHeight: 30, mappings: [text], documentIdentity: identity,
    documentIdentityFields: [identity], documentName: { fieldPaths: [text.targetPath], separator: "", prefix: "", suffix: "" },
    targets: [{ path: text.targetPath, label: text.label, section: "test" }], validForOcr: false, warnings: [],
  };
  const warning = "[MARK_OPTIONS_REQUIRED] 不完全選択 (mark_incomplete / choice / customFields.markIncomplete): 選択肢を1件以上設定してください。";
  let layoutPuts = 0;
  let mappingPuts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    let body: unknown;
    if (path.endsWith("/artifacts")) body = { items: [{ name: "normalized", mediaType: "image/png", url: "/source.png" }] };
    else if (path.endsWith("/layout")) {
      if (init?.method === "PUT") layoutPuts++;
      body = fixture.layout;
    } else if (path.endsWith("/field-mappings")) {
      if (init?.method === "PUT") mappingPuts++;
      body = layoutPuts ? { ...before, warnings: [warning], fieldErrors: [{
        field: "cells.mark_incomplete.markSets.choice.options", message: "選択肢を設定してください。",
      }] } : before;
    } else throw new Error(`Unexpected ${path}`);
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  render(<TemplatePage templates={[template]} refresh={vi.fn()} rememberJob={vi.fn()}
    initialEditVersionId="minimal-version" />);
  const navigation = await screen.findByRole("navigation", { name: "テンプレート設定の工程" });
  fireEvent.click(within(navigation).getByRole("button", { name: /5.*確認・保存/ }));
  expect(screen.getByText("未割当").parentElement).toHaveTextContent("0件");
  expect(screen.getByText("設定不完全").parentElement).toHaveTextContent("1件");
  expect(screen.getByText("内容設定済み").parentElement).toHaveTextContent("2");
  expect(screen.getByText("保存判定").parentElement).toHaveTextContent("要修正");
  expect(screen.queryByText("保存可能")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "レイアウトを途中保存" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(warning));
  expect(layoutPuts).toBe(1);
  expect(mappingPuts).toBe(0);
  expect(screen.queryByText(/保存結果にありません/)).not.toBeInTheDocument();
  expect(screen.getByText("照合条件").parentElement).toHaveTextContent("正常テキスト");
});

const markOptionsWarning = "[MARK_OPTIONS_REQUIRED] 不完全選択: 選択肢を1件以上設定してください。";

function renderMarkOptionsWarning(fieldErrors: Array<{ field: string; message: string }>, versionId: string) {
  const createdAt = "2026-09-11T00:00:00Z";
  const template: Template = {
    id: `template-${versionId}`, name: "選択欄テスト", documentType: "minimal", activeVersionId: null, createdAt,
    versions: [{ id: versionId, versionNumber: 1, mode: "REFERENCE_TEMPLATE", status: "DRAFT",
      sourceJobId: `job-${versionId}`, canonicalWidth: 100, canonicalHeight: 30, quality: {}, createdAt }],
  };
  const identity = { mappingId: "layout_text_input", targetPath: "customFields.textValue", label: "正常テキスト",
    valueType: "text", exactLength: null, matchRole: "primary", displayRole: "title" } as const;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/artifacts")) {
      return new Response(JSON.stringify({
        items: [{ name: "normalized", mediaType: "image/png", url: "/source.png" }],
      }), { status: 200 });
    }
    if (path.endsWith("/layout")) {
      return new Response(JSON.stringify(fixture.layout), { status: 200 });
    }
    if (path.endsWith("/field-mappings")) {
      return new Response(JSON.stringify({
        canonicalWidth: 100, canonicalHeight: 30,
        mappings: [{ ...identity, x: 0, y: 0, width: 20, height: 20, padding: 4,
          enabled: true, modelDetail: "high", mappingType: "text" }],
        documentIdentity: identity,
        documentIdentityFields: [identity],
        documentName: { fieldPaths: [identity.targetPath], separator: "", prefix: "", suffix: "" },
        targets: [{ path: identity.targetPath, label: identity.label, section: "test" }],
        validForOcr: false,
        warnings: [markOptionsWarning],
        fieldErrors,
      }), { status: 200 });
    }
    throw new Error(`Unexpected ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<TemplatePage templates={[template]} refresh={vi.fn()} rememberJob={vi.fn()}
    initialEditVersionId={versionId} />);
  return fetchMock;
}

test("MARK_OPTIONS_REQUIRED uses the structured field path to open step 2 and focus the mark editor", async () => {
  const fetchMock = renderMarkOptionsWarning([{
    field: "cells.mark_incomplete.markSets.choice.options",
    message: "選択肢を設定してください。",
  }], "mark-focus-success");
  const warning = await screen.findByText(markOptionsWarning);
  const requestCount = fetchMock.mock.calls.length;
  fireEvent.click(warning.closest("button")!);
  await waitFor(() => expect(screen.getByLabelText("セル一覧から選択")).toHaveValue("mark_incomplete"));
  expect(within(screen.getByRole("navigation", { name: "テンプレート設定の工程" }))
    .getByRole("button", { name: /2.*読み取る内容/ })).toHaveAttribute("aria-current", "step");
  expect(screen.getByLabelText("対象セルの選択肢エディタ")).toHaveFocus();
  expect(document.querySelector('.layout-warning-cell-highlight[data-cell-id="mark_incomplete"]')).not.toBeNull();
  expect(screen.getByText(markOptionsWarning)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(requestCount);
  expect(fetchMock.mock.calls.some(([, init]) => ["POST", "PUT"].includes(init?.method ?? ""))).toBe(false);
});

test.each([
  ["unknown", [{ field: "relations.mark_incomplete.options", message: "unknown" }]],
  ["malformed", [{ field: "cells.mark_incomplete.markSets.choice", message: "malformed" }]],
  ["missing mark set", [{ field: "cells.mark_incomplete.markSets", message: "選択肢を設定してください。" }]],
  ["missing cell", [{ field: "cells.missing.markSets.choice.options", message: "missing" }]],
] as const)("MARK_OPTIONS_REQUIRED falls back to step 2 without selecting for %s field path", async (_state, fieldErrors) => {
  const fetchMock = renderMarkOptionsWarning([...fieldErrors], `mark-focus-${_state.replace(" ", "-")}`);
  const warning = await screen.findByText(markOptionsWarning);
  const requestCount = fetchMock.mock.calls.length;
  fireEvent.click(warning.closest("button")!);
  await waitFor(() => expect(within(screen.getByRole("navigation", { name: "テンプレート設定の工程" }))
    .getByRole("button", { name: /2.*読み取る内容/ })).toHaveAttribute("aria-current", "step"));
  expect(screen.getByLabelText("セル一覧から選択")).toHaveValue("");
  expect(screen.queryByLabelText("対象セルの選択肢エディタ")).not.toBeInTheDocument();
  expect(screen.getByText("対象セルを特定できないため工程2を開いた")).toBeInTheDocument();
  expect(screen.getByText("帳票プレビューから対象の選択欄を選んでください。")).toBeInTheDocument();
  expect(screen.getByText(markOptionsWarning)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(requestCount);
});
