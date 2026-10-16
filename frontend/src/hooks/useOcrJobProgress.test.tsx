import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api } from "../api";
import { useOcrJobProgress } from "./useOcrJobProgress";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test.each(["reset", "unmount"])("does not reopen an old review when its response fails after %s", async (departure) => {
  vi.spyOn(api, "job").mockResolvedValue({ id: "job-old", jobType: "EXTRACT_DOCUMENT",
    status: "REVIEW_REQUIRED", progressStage: null, progressPercent: 100, attemptNumber: 1,
    error: null, createdAt: "2026-09-30T00:00:00Z", startedAt: null, completedAt: null });
  let fail!: (reason: Error) => void;
  vi.spyOn(api, "result").mockImplementation(() => new Promise((_resolve, reject) => { fail = reject; }));
  const openReview = vi.fn();
  const view = renderHook(({ id }: { id: string | null }) => useOcrJobProgress(id, false, openReview),
    { initialProps: { id: "job-old" as string | null } });
  await waitFor(() => expect(api.result).toHaveBeenCalledWith("job-old"));
  if (departure === "reset") view.rerender({ id: null });
  else view.unmount();
  await act(async () => { fail(new Error("old response failed")); });
  expect(openReview).not.toHaveBeenCalled();
});
