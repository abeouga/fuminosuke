import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api, ApiError } from "../api";
import { ToastViewport } from "../components/Toast";
import type { Job, Template } from "../types";
import type { PagedReviewResult } from "../pagedReview";
import { OcrPage } from "./OcrPage";

const hook = vi.hoisted(() => ({ terminal: null as null | ((job: Job) => void) }));
vi.mock("../hooks/useJobPoller", () => ({ useJobPoller: (_id: string | null, callback: (job: Job) => void) => {
  hook.terminal = callback;
  return { job: null, connectionLost: false };
} }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const template: Template = { id: "t", name: "複数ページ帳票", documentType: "form",
  activeVersionId: "v", createdAt: "2026-09-13", versions: [{ id: "v", versionNumber: 1,
    mode: "SPREADSHEET_TEMPLATE", status: "ACTIVE", sourceJobId: "source",
    canonicalWidth: 100, canonicalHeight: 100, createdAt: "2026-09-13",
    quality: { layoutVerified: true, printLayout: { pages: [{}] } } }] };
const terminal: Job = { id: "job", jobType: "EXTRACT_DOCUMENT", status: "REVIEW_REQUIRED",
  progressStage: "COMPLETED", progressPercent: 100, attemptNumber: 1, error: null,
  createdAt: "2026-09-13", startedAt: null, completedAt: null };
const ready: PagedReviewResult = { jobId: "job", revision: 1, confirmed: false,
  confirmAvailable: false, outputAvailable: false, pages: [] };

test.each([true, false])("paged terminal opens only the dedicated ready review (ready=%s)", async (available) => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], revision: "r" }))));
  const paged = vi.spyOn(api, "pageReview");
  if (available) paged.mockResolvedValue(ready);
  else paged.mockRejectedValue(new ApiError("ページ結果が未準備です", "PAGE_RESULT_NOT_READY"));
  const legacy = vi.spyOn(api, "result");
  const open = vi.fn();
  render(<><OcrPage templates={[template]} rememberJob={() => undefined} openReview={open}
    openTemplates={() => undefined} /><ToastViewport /></>);
  await act(async () => { hook.terminal?.(terminal); });
  await waitFor(() => expect(paged).toHaveBeenCalledWith("job"));
  expect(legacy).not.toHaveBeenCalled();
  if (available) expect(open).toHaveBeenCalledWith("job");
  else {
    expect(open).not.toHaveBeenCalled();
    expect(await screen.findByText("ページ結果が未準備です")).toBeInTheDocument();
  }
});
