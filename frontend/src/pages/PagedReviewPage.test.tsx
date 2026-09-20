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

test("prefills the whole-document name proposal and identifies only its owning page", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(metadataResult());
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await waitFor(() => expect(screen.getByLabelText("保存する書類名")).toHaveValue("候補名"));
  expect(screen.queryByText("書類の識別項目")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "ページ 2" }));
  expect(screen.getByText("書類の識別項目")).toBeInTheDocument();
  expect(screen.getByLabelText("同名項目")).toHaveValue("元値-b");
});

test("never overwrites a typed document name when a correction returns a new proposal", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(metadataResult());
  let finish!: (value: PagedReviewResult) => void;
  vi.spyOn(api, "correctPage").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  const field = await screen.findByLabelText("同名項目");
  fireEvent.change(field, { target: { value: "補正" } });
  fireEvent.click(screen.getByRole("button", { name: "このページの変更を保存" }));
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
  await waitFor(() => expect(screen.getByLabelText("保存する書類名")).toHaveValue("保存名-job"));
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
  fireEvent.click(screen.getByRole("button", { name: "ページ 2" }));
  fireEvent.click(screen.getByRole("button", { name: "同名項目の領域を表示" }));
  expect(screen.getByRole("img")).toHaveAttribute("src", "/page-b.png");
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
  expect(screen.getByLabelText("同名項目")).toHaveValue("元値-a");
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
  await waitFor(() => expect(screen.getByLabelText("保存する書類名")).toHaveValue("保存名-new-job"));
  await act(async () => { finish(new Blob(["old XLSX"])); });
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});

test("uses page identity for same-named fields and retains edits while changing pages", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  const correct = vi.spyOn(api, "correctPage").mockResolvedValue({ ...result(), revision: 2 });
  const guard = vi.fn();
  render(<PagedReviewPage jobId="job" onClose={() => undefined} onNavigationGuardChange={guard} />);
  const input = await screen.findByLabelText("同名項目");
  fireEvent.change(input, { target: { value: "ページA補正" } });
  fireEvent.click(screen.getByRole("button", { name: "ページ 2" }));
  expect(screen.getByLabelText("同名項目")).toHaveValue("元値-b");
  fireEvent.change(screen.getByLabelText("同名項目"), { target: { value: "ページB補正" } });
  fireEvent.click(screen.getByRole("button", { name: "同名項目の領域を表示" }));
  expect(screen.getByRole("img")).toHaveAttribute("src", "/page-b.png");
  fireEvent.click(screen.getByRole("button", { name: "このページの変更を保存" }));
  await waitFor(() => expect(correct).toHaveBeenCalledWith("job", "b", 1, [expect.objectContaining({ mappingId: "mapping-b", raw: "ページB補正" })]));
  expect(correct.mock.calls[0][3][0]).not.toHaveProperty("sourceRegionId");
  expect(correct.mock.calls[0][3][0]).not.toHaveProperty("boundingBox");
  fireEvent.click(screen.getByRole("button", { name: "ページ 1" }));
  expect(screen.getByLabelText("同名項目")).toHaveValue("ページA補正");
  expect(guard).toHaveBeenLastCalledWith(true);
});

test("does not freeze editing or overwrite changes made during a save", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  let finish!: (value: PagedReviewResult) => void;
  vi.spyOn(api, "correctPage").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  const input = await screen.findByLabelText("同名項目");
  fireEvent.change(input, { target: { value: "送信値" } });
  fireEvent.click(screen.getByRole("button", { name: "このページの変更を保存" }));
  expect(input).toBeEnabled();
  fireEvent.change(input, { target: { value: "送信後の追加編集" } });
  await act(async () => { finish({ ...result(), revision: 2 }); });
  expect(screen.getByLabelText("同名項目")).toHaveValue("送信後の追加編集");
  expect(screen.getByRole("button", { name: "このページの変更を保存" })).toBeEnabled();
});

test("keeps drafts after an uncertain save and requires refresh before retry", async () => {
  const get = vi.spyOn(api, "pageReview").mockResolvedValue(result());
  const correct = vi.spyOn(api, "correctPage").mockRejectedValue(new ApiError("競合", "REVISION_CONFLICT"));
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("同名項目"), { target: { value: "保持する補正" } });
  fireEvent.click(screen.getByRole("button", { name: "このページの変更を保存" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("競合");
  expect(screen.getByLabelText("同名項目")).toHaveValue("保持する補正");
  expect(screen.getByRole("button", { name: "このページの変更を保存" })).toBeDisabled();
  get.mockResolvedValue({ ...result(), revision: 3 });
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "このページの変更を保存" })).toBeEnabled());
  expect(screen.getByLabelText("同名項目")).toHaveValue("保持する補正");
  correct.mockResolvedValue({ ...result(), revision: 4 });
  fireEvent.click(screen.getByRole("button", { name: "このページの変更を保存" }));
  await waitFor(() => expect(correct).toHaveBeenLastCalledWith("job", "a", 3, expect.any(Array)));
});

test("routes the paged result rejection to the page review UI, not legacy Page 1", async () => {
  vi.spyOn(api, "result").mockRejectedValue(new ApiError("paged", "MULTI_PAGE_REVIEW_NOT_AVAILABLE"));
  vi.spyOn(api, "pageReview").mockResolvedValue(result());
  render(<ReviewPage jobId="job" onClose={() => undefined} />);
  expect(await screen.findByRole("heading", { name: "複数ページの読み取り確認" })).toBeInTheDocument();
  expect(await screen.findByLabelText("同名項目")).toHaveValue("元値-a");
});

test("ignores a previous job's late save response", async () => {
  vi.spyOn(api, "pageReview").mockResolvedValueOnce(result()).mockResolvedValueOnce({ ...result(), jobId: "next", revision: 7 });
  let finish!: (value: PagedReviewResult) => void;
  vi.spyOn(api, "correctPage").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const view = render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("同名項目"), { target: { value: "旧ジョブの編集" } });
  fireEvent.click(screen.getByRole("button", { name: "このページの変更を保存" }));
  view.rerender(<PagedReviewPage jobId="next" onClose={() => undefined} />);
  await screen.findByText(/保存revision 7/);
  await act(async () => { finish({ ...result(), revision: 2 }); });
  expect(screen.getByText(/保存revision 7/)).toBeInTheDocument();
  expect(screen.getByLabelText("同名項目")).toHaveValue("元値-a");
});

test("recovers an initial load failure with an explicit retry", async () => {
  vi.spyOn(api, "pageReview").mockRejectedValueOnce(new ApiError("offline")).mockResolvedValueOnce(result());
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  expect(await screen.findByLabelText("同名項目")).toHaveValue("元値-a");
});

test("requires explicit review of every page and preserves crop browsing after confirmation and reopen", async () => {
  const ready = { ...result(), confirmAvailable: true };
  vi.spyOn(api, "pageReview").mockResolvedValue(ready);
  let finish!: (value: PagedReviewResult) => void;
  const confirm = vi.spyOn(api, "confirmPages").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const reopen = vi.spyOn(api, "reopenPages").mockResolvedValue({ ...ready, revision: 3, documentName: "全ページの書類" });
  const guard = vi.fn();
  render(<PagedReviewPage jobId="job" onClose={() => undefined} onNavigationGuardChange={guard} />);
  await screen.findByLabelText("同名項目");
  fireEvent.change(screen.getByLabelText("保存する書類名"), { target: { value: "全ページの書類" } });
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  expect(screen.getByRole("button", { name: "全ページを確定" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "ページ 2" }));
  expect(screen.getByLabelText("このページの内容を確認しました")).not.toBeChecked();
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "全ページを確定" }));
  expect(confirm).toHaveBeenCalledWith("job", 1, ["a", "b"], "全ページの書類");
  expect(screen.getByLabelText("同名項目")).toBeDisabled();
  expect(screen.getByRole("button", { name: "ページ 1" })).toBeEnabled();
  await act(async () => { finish({ ...ready, revision: 2, confirmed: true, confirmAvailable: false, documentName: "全ページの書類" }); });
  expect(screen.getByRole("status")).toHaveTextContent("全ページ確定済み");
  expect(guard).toHaveBeenLastCalledWith(false);
  fireEvent.click(screen.getByRole("button", { name: "同名項目の領域を表示" }));
  expect(screen.getByRole("img")).toHaveAttribute("src", "/page-b.png");
  fireEvent.click(screen.getByRole("button", { name: "再編集する" }));
  await waitFor(() => expect(screen.getByLabelText("同名項目")).toBeEnabled());
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
  await screen.findByLabelText("同名項目");
  fireEvent.change(screen.getByLabelText("保存する書類名"), { target: { value: "保存名" } });
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  fireEvent.click(screen.getByRole("button", { name: "ページ 2" }));
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  fireEvent.click(screen.getByRole("button", { name: "全ページを確定" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("応答を受信できませんでした");
  expect(screen.getByLabelText("同名項目")).toBeDisabled();
  expect(screen.getByLabelText("保存する書類名")).toHaveValue("保存名");
  expect(screen.getByRole("button", { name: "全ページを確定" })).toBeDisabled();
  get.mockResolvedValue({ ...ready, revision: 2, confirmed: true, confirmAvailable: false, documentName: "保存名" });
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  expect(await screen.findByRole("status")).toHaveTextContent("全ページ確定済み");
  expect(screen.getByLabelText("同名項目")).toHaveValue("元値-b");
  expect(reopen).not.toHaveBeenCalled();
});

test("editing invalidates that page's acknowledgement and does not auto-confirm", async () => {
  const ready = { ...result(), confirmAvailable: true };
  vi.spyOn(api, "pageReview").mockResolvedValue(ready);
  vi.spyOn(api, "correctPage").mockResolvedValue({ ...ready, revision: 2 });
  const confirm = vi.spyOn(api, "confirmPages");
  render(<PagedReviewPage jobId="job" onClose={() => undefined} />);
  await screen.findByLabelText("同名項目");
  fireEvent.click(screen.getByLabelText("このページの内容を確認しました"));
  fireEvent.change(screen.getByLabelText("同名項目"), { target: { value: "追加補正" } });
  expect(screen.getByLabelText("このページの内容を確認しました")).not.toBeChecked();
  expect(screen.getByLabelText("このページの内容を確認しました")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "このページの変更を保存" }));
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
  fireEvent.change(await screen.findByLabelText("同名項目"), { target: { value: "失ってはいけない補正" } });
  fireEvent.click(screen.getByRole("button", { name: "このページの変更を保存" }));
  await screen.findByRole("alert");
  get.mockResolvedValue({ ...ready, revision: 2, confirmed: true, confirmAvailable: false, documentName: "書類" });
  fireEvent.click(screen.getByRole("button", { name: "編集内容を保持して最新結果を取得" }));
  expect(await screen.findByRole("status")).toHaveTextContent("全ページ確定済み");
  expect(screen.getByLabelText("同名項目")).toHaveValue("元値-a");
  expect(screen.getByText("未保存の補正を保持しています。保存するには再編集してください。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "再編集する" }));
  await waitFor(() => expect(screen.getByLabelText("同名項目")).toHaveValue("失ってはいけない補正"));
});
