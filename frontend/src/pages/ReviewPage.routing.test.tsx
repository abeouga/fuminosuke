import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import { v2client } from "../editing/v2client";
import { ReviewPage } from "./ReviewPage";

vi.mock("./PagedReviewPage", () => ({ PagedReviewPage: ({ jobId }: { jobId: string }) => <h1>ページ別結果 {jobId}</h1> }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("routes a paged job before opening a single-result editing session", async () => {
  vi.spyOn(api, "result").mockRejectedValue(new ApiError("ページ別レビューを使用してください。", "MULTI_PAGE_REVIEW_REQUIRED"));
  const status = vi.spyOn(v2client, "status");
  render(<ReviewPage jobId="paged" onClose={() => undefined} />);
  expect(await screen.findByRole("heading", { name: "ページ別結果 paged" })).toBeInTheDocument();
  expect(status).not.toHaveBeenCalled();
});
it("allows a failed initial result request to be retried without opening the wrong editor", async () => {
  const result = vi.spyOn(api, "result").mockRejectedValueOnce(new Error("接続が切れました。"))
    .mockRejectedValue(new ApiError("ページ別レビューを使用してください。", "MULTI_PAGE_REVIEW_REQUIRED"));
  render(<ReviewPage jobId="paged" onClose={() => undefined} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("接続が切れました。");
  fireEvent.click(screen.getByRole("button", { name: "結果を再取得" }));
  expect(await screen.findByRole("heading", { name: "ページ別結果 paged" })).toBeInTheDocument();
  expect(result).toHaveBeenCalledTimes(2);
});
