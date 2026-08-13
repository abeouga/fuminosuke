import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { JobPage } from "./JobPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("retries an interrupted job without requiring the original upload again", async () => {
  let retried = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/jobs/job-1/retry") && init?.method === "POST") {
      retried = true;
      return new Response(JSON.stringify(job("QUEUED")), { status: 200 });
    }
    if (path.endsWith("/jobs/job-1")) {
      return new Response(JSON.stringify(job(retried ? "PROCESSING" : "INTERRUPTED")), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(
    <JobPage
      jobId="job-1"
      onClose={() => undefined}
      onOpenReview={() => undefined}
      onOpenTemplates={() => undefined}
      onStartNewReading={() => undefined}
    />,
  );

  expect(await screen.findByText("再起動により処理が中断されました")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "処理を再試行" }));
  expect(await screen.findByText("処理を再開しました。完了までこの画面で確認できます。")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("処理が完了するまでお待ちください")).toBeInTheDocument());
});

test("keeps ordinary failed jobs retryable", async () => {
  let retried = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/jobs/job-1/retry") && init?.method === "POST") {
      retried = true;
      return new Response(JSON.stringify(job("QUEUED")), { status: 200 });
    }
    if (path.endsWith("/jobs/job-1")) {
      return new Response(JSON.stringify(retried
        ? job("PROCESSING")
        : job("FAILED", { errorCode: "PROCESSING_FAILED" })), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(
    <JobPage
      jobId="job-1"
      onClose={() => undefined}
      onOpenReview={() => undefined}
      onOpenTemplates={() => undefined}
      onStartNewReading={() => undefined}
    />,
  );

  expect(await screen.findByRole("heading", {
    name: "処理に失敗しました",
  })).toBeInTheDocument();
  expect(screen.getByText(
    "元画像と設定は保存されています。同じ内容で安全に再試行できます。",
  )).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "処理を再試行" }));
  expect(await screen.findByText(
    "処理を再開しました。完了までこの画面で確認できます。",
  )).toBeInTheDocument();
});

test.each([
  {
    jobType: "PREPARE_TEMPLATE" as const,
    actionLabel: "新しいテンプレートを登録",
    expectedGuidance: "新しいテンプレートとして登録してください",
    callback: "template" as const,
  },
  {
    jobType: "EXTRACT_DOCUMENT" as const,
    actionLabel: "新しい読み取りを開始",
    expectedGuidance: "新しい読み取りを開始してください",
    callback: "ocr" as const,
  },
])("does not retry incomplete $jobType inputs and guides to a new registration", async ({
  jobType,
  actionLabel,
  expectedGuidance,
  callback,
}) => {
  let retryCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/jobs/job-1/retry") && init?.method === "POST") {
      retryCalls += 1;
    }
    if (path.endsWith("/jobs/job-1")) {
      return new Response(JSON.stringify(job("FAILED", {
        jobType,
        errorCode: "INCOMPLETE_JOB_INPUTS",
      })), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));
  const onOpenTemplates = vi.fn();
  const onStartNewReading = vi.fn();

  render(
    <JobPage
      jobId="job-1"
      onClose={() => undefined}
      onOpenReview={() => undefined}
      onOpenTemplates={onOpenTemplates}
      onStartNewReading={onStartNewReading}
    />,
  );

  expect(await screen.findByRole("heading", {
    name: "元画像の保存が完了していません",
  })).toBeInTheDocument();
  expect(screen.getByText(new RegExp(expectedGuidance))).toBeInTheDocument();
  expect(screen.queryByText(
    "元画像と設定は保存されています。同じ内容で安全に再試行できます。",
  )).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "処理を再試行" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: actionLabel }));
  expect(retryCalls).toBe(0);
  expect(callback === "template" ? onOpenTemplates : onStartNewReading).toHaveBeenCalledOnce();
});

test("does not retry an OCR job whose accepted template snapshot is invalid", async () => {
  let retryCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/jobs/job-1/retry") && init?.method === "POST") {
      retryCalls += 1;
    }
    if (path.endsWith("/jobs/job-1")) {
      return new Response(JSON.stringify(job("FAILED", {
        errorCode: "OCR_TEMPLATE_SNAPSHOT_INVALID",
      })), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));
  const onStartNewReading = vi.fn();

  render(
    <JobPage
      jobId="job-1"
      onClose={() => undefined}
      onOpenReview={() => undefined}
      onOpenTemplates={() => undefined}
      onStartNewReading={onStartNewReading}
    />,
  );

  expect(await screen.findByRole("heading", {
    name: "受付時のテンプレートを確認できません",
  })).toBeInTheDocument();
  expect(screen.getByText(/安全に再試行できません/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "処理を再試行" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "新しい読み取りを開始" }));
  expect(retryCalls).toBe(0);
  expect(onStartNewReading).toHaveBeenCalledOnce();
});

function job(
  status: string,
  options: {
    jobType?: "PREPARE_TEMPLATE" | "EXTRACT_DOCUMENT";
    errorCode?: string;
  } = {},
) {
  return {
    id: "job-1",
    jobType: options.jobType ?? "EXTRACT_DOCUMENT",
    status,
    progressStage: status === "PROCESSING" ? "CALLING_OCR" : null,
    progressPercent: status === "PROCESSING" ? 60 : 20,
    attemptNumber: retriedAttempt(status),
    error: options.errorCode
      ? {
        code: options.errorCode,
        message: options.errorCode === "INCOMPLETE_JOB_INPUTS"
          ? "受付中にバックエンドが停止し、元画像の保存を完了できませんでした。"
          : "処理に失敗しました。再試行できます。",
      }
      : status === "INTERRUPTED"
        ? { code: "BACKEND_RESTARTED", message: "バックエンド再起動により中断されました。" }
        : null,
    createdAt: "2026-07-30T00:00:00Z",
    startedAt: null,
    completedAt: null,
  };
}

function retriedAttempt(status: string) {
  return status === "INTERRUPTED" ? 1 : 2;
}
