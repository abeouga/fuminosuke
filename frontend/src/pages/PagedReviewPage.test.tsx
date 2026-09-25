import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api, ApiError } from "../api";
import type { PagedReviewResult } from "../pagedReview";
import { PagedReviewPage } from "./PagedReviewPage";
import { ReviewPage } from "./ReviewPage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function result(): PagedReviewResult {
  return { jobId: "job", revision: 1, confirmed: false, confirmAvailable: false, outputAvailable: false,
    pages: ["a", "b"].map((pageId, index) => ({
      pageId, pageNumber: index + 1, pageRange: `range-${pageId}`, inputId: `input-${pageId}`,
      inputRevision: 1, inputSha256: pageId.repeat(64), referenceImageSha256: "f".repeat(64),
      pageImageUrl: `/page-${pageId}-full.png`,
      result: { schemaVersion: "1.0", templateId: "fixture", templateVersion: "1",
        document: {}, saleCalves: [], pedigree: {}, calvings: [], customFields: {}, reviewItems: [],
        processingMetadata: { modelId: "fixture", promptVersion: "fixture", sourceRegions: [], notes: [] } },
      validation: { valid: true, issues: [] },
      fields: [{ mappingId: `mapping-${pageId}`, targetPath: `customFields.${pageId}`,
        label: "同名項目", cropUrl: `/page-${pageId}.png`, reading: {
          raw: `元値-${pageId}`, normalized: `元値-${pageId}`, status: "readable", alternatives: [],
          reviewReason: null, sourceRegionId: `mapping-${pageId}`,
          boundingBox: { x: 10, y: 20, width: 100, height: 50 },
        } }],
    })) };
}

function outputResult(jobId = "job"): PagedReviewResult {
  return { ...result(), jobId, revision: 7, confirmed: true, outputAvailable: true,
    documentName: `保存名-${jobId}` };
}

function metadataResult(proposedName = "候補名"): PagedReviewResult {
  return { ...result(), documentMetadata: { proposedName, identityFields: [
    { pageId: "b", mappingId: "mapping-b", targetPath: "customFields.b", label: "同名項目" },
  ] } };
}

function fieldButton(path = "customFields.a") {
  return screen.getByRole("button", { name: `同名項目 ${path} を編集` });
}

async function changeField(path: string, value: string) {
  const button = await screen.findByRole("button", { name: `同名項目 ${path} を編集` });
  fireEvent.doubleClick(button);
  const input = screen.getByLabelText(`同名項目 ${path} を直接編集`);
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: "Enter" });
}

function nextPage() {
  fireEvent.click(screen.getByRole("button", { name: /次のページ/ }));
}

function previousPage() {
  fireEvent.click(screen.getByRole("button", { name: /前のページ/ }));
}

test("prefills the whole-document name proposal without adding paged-only field badges", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(metadataResult());
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await waitFor(() => expect(screen.getByLabelText("保存する書類名")).toHaveValue("候補名"));
  expect(screen.getByText(/OCR結果は候補です/)).toHaveTextContent("完全に自動検証した結果ではありません");
  expect(screen.queryByText("書類の識別項目")).not.toBeInTheDocument();
  nextPage();
  expect(screen.queryByText("書類の識別項目")).not.toBeInTheDocument();
  expect(fieldButton("customFields.b")).toHaveTextContent("元値-b");
});

test("never overwrites a typed document name when a correction returns a new proposal", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(metadataResult());
  let finish!: (value: PagedReviewResult) => void;
  vi.spyOn(api, "correctPage").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await changeField("customFields.a", "補正");
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  fireEvent.change(screen.getByLabelText("保存する書類名"), { target: { value: "手入力名" } });
  await act(async () => { finish({ ...metadataResult("新しい候補"), revision: 2 }); });
  expect(screen.getByLabelText("保存する書類名")).toHaveValue("手入力名");
  fireEvent.click(screen.getByRole("button", { name: "設定した項目から書類名を反映" }));
  expect(screen.getByLabelText("保存する書類名")).toHaveValue("新しい候補");
});

test("confirmed document name remains authoritative over a different proposal", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue({ ...metadataResult(), ...outputResult(),
    documentMetadata: metadataResult().documentMetadata });
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await waitFor(() => expect(screen.getByText("保存名-job")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "設定した項目から書類名を反映" })).not.toBeInTheDocument();
});

test("downloads the explicitly confirmed revision without blocking page or crop viewing", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(outputResult());
  let finish!: (blob: Blob) => void;
  const download = vi.spyOn(api, "downloadPageOutput").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  URL.createObjectURL = vi.fn(() => "blob:paged-output"); URL.revokeObjectURL = vi.fn();
  const links: { href: string; name: string }[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    links.push({ href: this.href, name: this.download });
  });
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  const button = await screen.findByRole("button", { name: "全ページの記入済みXLSXを取得" });
  fireEvent.click(button); fireEvent.click(button);
  expect(download).toHaveBeenCalledExactlyOnceWith("job", 7);
  expect(screen.getByRole("button", { name: "再編集する" })).toBeDisabled();
  nextPage();
  fireEvent.click(fieldButton("customFields.b"));
  expect(screen.getByRole("img", { name: "同名項目の読み取り領域" })).toHaveAttribute("src", "/page-b.png");
  await act(async () => { finish(new Blob(["xlsx"])); });
  expect(links).toEqual([{ href: "blob:paged-output", name: "保存名-job.xlsx" }]);
  expect(screen.getByRole("button", { name: "再編集する" })).toBeEnabled();
  expect(screen.getByText("全ページ確定済み")).toBeInTheDocument();
});

test("output failures retain confirmation and values and can be retried after refreshing", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(outputResult());
  const download = vi.spyOn(api, "downloadPageOutput").mockRejectedValue(new Error("原本を取得できません"));
  const reopen = vi.spyOn(api, "reopenPages");
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "全ページの記入済みXLSXを取得" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("原本を取得できません");
  expect(screen.getByText("全ページ確定済み")).toBeInTheDocument();
  expect(fieldButton()).toHaveTextContent("元値-a");
  expect(reopen).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "全ページの記入済みXLSXを取得" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "全ページの記入済みXLSXを取得" }));
  await waitFor(() => expect(download).toHaveBeenCalledTimes(2));
});

test("does not download an old job response after the displayed document changes", async () => {
  vi.spyOn(api, "pageReview").mockImplementation(async (id) => outputResult(id));
  let finish!: (blob: Blob) => void;
  vi.spyOn(api, "downloadPageOutput").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  URL.createObjectURL = vi.fn(); URL.revokeObjectURL = vi.fn();
  const view = render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "全ページの記入済みXLSXを取得" }));
  view.rerender(<PagedReviewPage jobId="new-job" onClose={() => undefined} />);
  await waitFor(() => expect(screen.getByText("保存名-new-job")).toBeInTheDocument());
  await act(async () => { finish(new Blob(["old XLSX"])); });
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});

test("uses page identity for same-named fields and retains edits while changing pages", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  const correct = vi.spyOn(api, "correctPage").mockResolvedValue({ ...result(), revision: 2 });
  const guard = vi.fn();
  render(<PagedReviewPage jobId="job" onClose={() => undefined} onNavigationGuardChange={guard} />);
  await changeField("customFields.a", "ページA補正");
  nextPage();
  expect(fieldButton("customFields.b")).toHaveTextContent("元値-b");
  await changeField("customFields.b", "ページB補正");
  fireEvent.click(fieldButton("customFields.b"));
  expect(screen.getByRole("img", { name: "同名項目の読み取り領域" })).toHaveAttribute("src", "/page-b.png");
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  await waitFor(() => expect(correct).toHaveBeenCalledWith("job", "b", 1, [expect.objectContaining({ mappingId: "mapping-b", raw: "ページB補正" })]));
  expect(correct.mock.calls[0][3][0]).not.toHaveProperty("sourceRegionId");
  expect(correct.mock.calls[0][3][0]).not.toHaveProperty("boundingBox");
  previousPage();
  expect(fieldButton()).toHaveTextContent("ページA補正");
  expect(guard).toHaveBeenLastCalledWith(true);
});

test("shows the owned full-page artifact, single-page-style table selection, and overlaid previous/next navigation", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  const fullPage = await screen.findByRole("img", { name: "ページ 1の位置合わせ済み帳票" });
  expect(fullPage).toHaveAttribute("src", "/page-a-full.png");
  expect(screen.getByRole("region", { name: "独立項目" })).toHaveTextContent("同名項目");
  expect(screen.getByText("問題なし")).toBeInTheDocument();
  expect(screen.getByText("要確認")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "問題なし" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "要確認" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "空欄" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "結果編集モード" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: /前のページ/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /次のページ/ })).toBeEnabled();
  expect(fieldButton()).toHaveTextContent("元値-a");
  expect(fieldButton().closest("td")).toHaveClass("ocr-cell-ok");
  fireEvent.click(fieldButton());
  expect(screen.getByRole("img", { name: "同名項目の読み取り領域" })).toHaveAttribute("src", "/page-a.png");
  fireEvent.click(screen.getByRole("button", { name: /次のページ/ }));
  expect(screen.getByRole("img", { name: "ページ 2の位置合わせ済み帳票" })).toHaveAttribute("src", "/page-b-full.png");
  expect(screen.getByText("ページ 2 / 2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /前のページ/ })).toBeEnabled();
  expect(screen.getByRole("button", { name: /次のページ/ })).toBeDisabled();
});

test("uses the accepted review headings instead of flattening every page into one generic table", async () => {
  const structured = result();
  structured.reviewStructure = {
    schemaVersion: 1,
    fields: [
      { id: "field-a", mappingId: "mapping-a", targetPath: "customFields.a", label: "同名項目", inputCellId: null, relationId: null },
      { id: "field-b", mappingId: "mapping-b", targetPath: "customFields.b", label: "同名項目", inputCellId: null, relationId: null },
    ],
    recordSections: [],
    parentSections: [
      { id: "section-a", title: "ページAの見出し", fieldIds: ["field-a"] },
      { id: "section-b", title: "ページBの見出し", fieldIds: ["field-b"] },
    ],
    independentFieldIds: [],
  };
  vi.spyOn(api, "pageReview").mockResolvedValue(structured);
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  expect(await screen.findByRole("region", { name: "ページAの見出し" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "ページBの見出し" })).not.toBeInTheDocument();
  nextPage();
  expect(screen.getByRole("region", { name: "ページBの見出し" })).toBeInTheDocument();
});

test("moves record rows with the shared controls while keeping fixed page mappings", async () => {
  const structured = result();
  structured.pages[0].fields = [
    ["r1a", "records.0.a", "列A", "一行目A", "readable"],
    ["r1b", "records.0.b", "列B", "一行目B", "uncertain"],
    ["r2a", "records.1.a", "列A", "二行目A", "readable"],
    ["r2b", "records.1.b", "列B", "二行目B", "readable"],
  ].map(([mappingId, targetPath, label, raw, status]) => ({
    mappingId, targetPath, label, cropUrl: `/${mappingId}.png`,
    reading: { raw, normalized: raw, status: status as "readable" | "uncertain",
      alternatives: [], reviewReason: status === "uncertain" ? "画像を確認してください" : null,
      sourceRegionId: mappingId, boundingBox: { x: 1, y: 2, width: 3, height: 4 } },
  }));
  structured.reviewStructure = {
    schemaVersion: 1,
    fields: structured.pages[0].fields.map((field) => ({ id: field.mappingId,
      mappingId: field.mappingId, targetPath: field.targetPath, label: field.label,
      inputCellId: null, relationId: null })),
    recordSections: [{ id: "records", title: "履歴", collectionPath: "records",
      columns: [{ id: "a", label: "列A" }, { id: "b", label: "列B" }],
      rows: [
        { id: "row-1", order: 1, cells: [{ columnId: "a", fieldId: "r1a" }, { columnId: "b", fieldId: "r1b" }] },
        { id: "row-2", order: 2, cells: [{ columnId: "a", fieldId: "r2a" }, { columnId: "b", fieldId: "r2b" }] },
      ] }],
    parentSections: [], independentFieldIds: [],
  };
  vi.spyOn(api, "pageReview").mockResolvedValue(structured);
  const correct = vi.spyOn(api, "correctPage").mockResolvedValue({ ...structured, revision: 2 });
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  const section = await screen.findByRole("region", { name: "履歴" });
  fireEvent.click(screen.getByRole("button", { name: "行移動モード" }));
  fireEvent.click(screen.getByRole("button", { name: "履歴 2行目を上へ移動" }));
  expect(screen.getByRole("button", { name: "列A records.0.a を編集" })).toHaveTextContent("二行目A");
  expect(screen.getByRole("button", { name: "列B records.1.b を編集" })).toHaveTextContent("一行目B");
  expect(section).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  await waitFor(() => expect(correct).toHaveBeenCalledTimes(1));
  const sent = correct.mock.calls[0][3];
  expect(sent).toEqual(expect.arrayContaining([
    expect.objectContaining({ mappingId: "r1a", raw: "二行目A" }),
    expect.objectContaining({ mappingId: "r2b", raw: "一行目B", status: "uncertain" }),
  ]));
  for (const patch of sent) {
    expect(patch).not.toHaveProperty("sourceRegionId");
    expect(patch).not.toHaveProperty("boundingBox");
  }
});

test("does not freeze editing or overwrite changes made during a save", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  let finish!: (value: PagedReviewResult) => void;
  vi.spyOn(api, "correctPage").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await changeField("customFields.a", "送信値");
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  await changeField("customFields.a", "送信後の追加編集");
  await act(async () => { finish({ ...result(), revision: 2 }); });
  expect(fieldButton()).toHaveTextContent("送信後の追加編集");
  expect(screen.getByRole("button", { name: "修正を保存" })).toBeEnabled();
});

test("keeps drafts after an uncertain save and requires refresh before retry", async () => {
  const get = vi.spyOn(api, "pageReview").mockResolvedValue(result());
  const correct = vi.spyOn(api, "correctPage").mockRejectedValue(new ApiError("競合", "REVISION_CONFLICT"));
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await changeField("customFields.a", "保持する補正");
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("競合");
  expect(fieldButton()).toHaveTextContent("保持する補正");
  expect(screen.getByRole("button", { name: "修正を保存" })).toBeDisabled();
  get.mockResolvedValue({ ...result(), revision: 3 });
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "修正を保存" })).toBeEnabled());
  expect(fieldButton()).toHaveTextContent("保持する補正");
  correct.mockResolvedValue({ ...result(), revision: 4 });
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  await waitFor(() => expect(correct).toHaveBeenLastCalledWith("job", "a", 3, expect.any(Array)));
});

test("routes the paged result rejection to the page review UI, not legacy Page 1", async () => {
  vi.spyOn(api, "result").mockRejectedValue(new ApiError("paged", "MULTI_PAGE_REVIEW_NOT_AVAILABLE"));
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  render(<ReviewPage jobId="job" onClose={() => undefined} />);
  expect(await screen.findByRole("heading", { name: "読み取り結果を確認" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "同名項目 customFields.a を編集" })).toHaveTextContent("元値-a");
});

test("ignores a previous job's late save response", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValueOnce(result()).mockResolvedValueOnce({ ...result(), jobId: "next", revision: 7 });
  let finish!: (value: PagedReviewResult) => void;
  vi.spyOn(api, "correctPage").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const view = render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await changeField("customFields.a", "旧ジョブの編集");
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  view.rerender(<PagedReviewPage jobId="next" onClose={() => undefined} />);
  await screen.findByText(/REVISION 7/);
  await act(async () => { finish({ ...result(), revision: 2 }); });
  expect(screen.getByText(/REVISION 7/)).toBeInTheDocument();
  expect(fieldButton()).toHaveTextContent("元値-a");
});

test("recovers an initial load failure with an explicit retry", async () => {
  vi.spyOn(api, "pageReview").mockRejectedValueOnce(new ApiError("offline")).mockResolvedValueOnce(result());
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  expect(await screen.findByRole("button", { name: "同名項目 customFields.a を編集" })).toHaveTextContent("元値-a");
});

test("requires explicit review of every page and preserves crop browsing after confirmation and reopen", async () => {
  const ready = { ...result(), confirmAvailable: true };
  vi.spyOn(api, "pageReview").mockResolvedValue(ready);
  let finish!: (value: PagedReviewResult) => void;
  const confirm = vi.spyOn(api, "confirmPages").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const reopen = vi.spyOn(api, "reopenPages").mockResolvedValue({ ...ready, revision: 3, documentName: "全ページの書類" });
  const guard = vi.fn();
  render(<PagedReviewPage jobId="job" onClose={() => undefined} onNavigationGuardChange={guard} />);
  await screen.findByRole("button", { name: "同名項目 customFields.a を編集" });
  fireEvent.change(screen.getByLabelText("保存する書類名"), { target: { value: "全ページの書類" } });
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  expect(screen.getByRole("button", { name: "全ページを確定" })).toBeDisabled();
  nextPage();
  expect(screen.getByLabelText("このページの内容を確認しました")).not.toBeChecked();
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "全ページを確定" }));
  expect(confirm).toHaveBeenCalledWith("job", 1, ["a", "b"], "全ページの書類");
  expect(screen.getByRole("button", { name: "結果編集モード" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /前のページ/ })).toBeEnabled();
  await act(async () => { finish({ ...ready, revision: 2, confirmed: true, confirmAvailable: false, documentName: "全ページの書類" }); });
  expect(screen.getByRole("status")).toHaveTextContent("全ページ確定済み");
  expect(guard).toHaveBeenLastCalledWith(false);
  fireEvent.click(fieldButton("customFields.b"));
  expect(screen.getByRole("img", { name: "同名項目の読み取り領域" })).toHaveAttribute("src", "/page-b.png");
  fireEvent.click(screen.getByRole("button", { name: "再編集する" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "結果編集モード" })).toBeEnabled());
  expect(reopen).toHaveBeenCalledWith("job", 2);
  expect(screen.getByLabelText("このページの内容を確認しました")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "全ページを確定" })).toBeDisabled();
});

test("recovers a lost confirmation response from server truth, without automatically reopening", async () => {
  const ready = { ...result(), confirmAvailable: true };
  const get = vi.spyOn(api, "pageReview").mockResolvedValue(ready);
  vi.spyOn(api, "confirmPages").mockRejectedValue(new ApiError("応答を受信できませんでした"));
  const reopen = vi.spyOn(api, "reopenPages");
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await screen.findByRole("button", { name: "同名項目 customFields.a を編集" });
  fireEvent.change(screen.getByLabelText("保存する書類名"), { target: { value: "保存名" } });
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  nextPage();
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  fireEvent.click(screen.getByRole("button", { name: "全ページを確定" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("応答を受信できませんでした");
  expect(screen.getByRole("button", { name: "結果編集モード" })).toBeDisabled();
  expect(screen.getByLabelText("保存する書類名")).toHaveValue("保存名");
  expect(screen.getByRole("button", { name: "全ページを確定" })).toBeDisabled();
  get.mockResolvedValue({ ...ready, revision: 2, confirmed: true, confirmAvailable: false, documentName: "保存名" });
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  expect(await screen.findByRole("status")).toHaveTextContent("全ページ確定済み");
  expect(fieldButton("customFields.b")).toHaveTextContent("元値-b");
  expect(reopen).not.toHaveBeenCalled();
});

test("editing invalidates that page's acknowledgement and does not auto-confirm", async () => {
  const ready = { ...result(), confirmAvailable: true };
  vi.spyOn(api, "pageReview").mockResolvedValue(ready);
  vi.spyOn(api, "correctPage").mockResolvedValue({ ...ready, revision: 2 });
  const confirm = vi.spyOn(api, "confirmPages");
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await screen.findByRole("button", { name: "同名項目 customFields.a を編集" });
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  await changeField("customFields.a", "追加補正");
  expect(screen.getByLabelText("このページの内容を確認しました")).not.toBeChecked();
  expect(screen.getByLabelText("このページの内容を確認しました")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  await waitFor(() => expect(screen.getByLabelText("このページの内容を確認しました")).toBeEnabled());
  expect(screen.getByLabelText("このページの内容を確認しました")).not.toBeChecked();
  expect(confirm).not.toHaveBeenCalled();
});

test("keeps conflicting drafts separate from a confirmed server result until explicit reopen", async () => {
  const ready = { ...result(), confirmAvailable: true };
  const get = vi.spyOn(api, "pageReview").mockResolvedValue(ready);
  vi.spyOn(api, "correctPage").mockRejectedValue(new ApiError("他の画面で確定済み", "PAGE_RESULT_LOCKED"));
  vi.spyOn(api, "reopenPages").mockResolvedValue({ ...ready, revision: 3, documentName: "書類" });
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await changeField("customFields.a", "失ってはいけない補正");
  fireEvent.click(screen.getByRole("button", { name: "修正を保存" }));
  await screen.findByRole("alert");
  get.mockResolvedValue({ ...ready, revision: 2, confirmed: true, confirmAvailable: false, documentName: "書類" });
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  expect(await screen.findByRole("status")).toHaveTextContent("全ページ確定済み");
  expect(fieldButton()).toHaveTextContent("元値-a");
  expect(screen.getByText("未保存の補正を保持しています。保存するには再編集してください。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "再編集する" }));
  await waitFor(() => expect(fieldButton()).toHaveTextContent("失ってはいけない補正"));
});

test("uses the same compact status editor as the single-page review", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  const button = await screen.findByRole("button", { name: "同名項目 customFields.a を編集" });
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("同名項目 customFields.a を直接編集")).not.toBeInTheDocument();
  fireEvent.click(button);
  expect(screen.getByRole("group", { name: "同名項目の判定" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "不確実" }));
  expect(fieldButton().closest("td")).toHaveClass("ocr-cell-warning");
  expect(fieldButton()).toHaveTextContent("要確認");
});

test("finds warnings across pages without Excel ranges and keeps page drafts and crop ownership", async () => {
  const pages = result();
  pages.pages.forEach((page) => { page.pageRange = ""; });
  pages.pages[1].fields[0].reading.status = "uncertain";
  pages.pages[1].fields[0].reading.normalized = null;
  vi.spyOn(api, "pageReview").mockResolvedValue(pages);
  const correct = vi.spyOn(api, "correctPage");
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await changeField("customFields.a", "保持する編集");
  fireEvent.click(screen.getByRole("button", { name: "次の要確認（1件）" }));
  expect(screen.getByText("ページ 2 / 2 · 同名項目")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "同名項目の読み取り領域" })).toHaveAttribute("src", "/page-b.png");
  previousPage();
  expect(fieldButton()).toHaveTextContent("保持する編集");
  expect(correct).not.toHaveBeenCalled();
});

test("shows actual API usage totals for the current OCR job", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  vi.spyOn(api, "openaiUsage").mockResolvedValue({ records: [
    { id: "1", jobId: "job", modelId: "gpt-5", apiCallCount: 2, inputTokens: 100,
      outputTokens: 25, totalTokens: 125, estimatedCost: "0.0125" } as never,
    { id: "2", jobId: "other", modelId: "gpt-5", apiCallCount: 99, inputTokens: 999,
      outputTokens: 999, totalTokens: 1998, estimatedCost: "9" } as never,
  ] } as never);
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  expect(await screen.findByText(/API 2回/)).toHaveTextContent("入力 100 / 出力 25 tokens · 合計 125 · API推定 $0.012500");
});
