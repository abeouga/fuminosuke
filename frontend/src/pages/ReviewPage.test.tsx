import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ReviewPage } from "./ReviewPage";

const reading = {
  raw: "sample",
  normalized: null,
  status: "uncertain" as const,
  alternatives: [],
  reviewReason: "check",
  sourceRegionId: "header_identity",
  boundingBox: null,
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/ocr-jobs/job-1/result")) {
      return new Response(JSON.stringify({
        jobId: "job-1",
        revision: 1,
        result: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "version-1",
          document: { cattleName: reading },
          saleCalves: [],
          pedigree: {},
          calvings: [],
          reviewItems: [],
          processingMetadata: {},
        },
        validation: { valid: false, issues: [] },
        provider: "openai",
        modelId: "gpt-5-mini",
        promptVersion: "test",
        apiCallCount: 1,
        inputTokens: 1,
        outputTokens: 1,
        estimatedCost: 0.001,
        confirmed: false,
        individualId: null,
      }), { status: 200 });
    }
    if (path.endsWith("/jobs/job-1/artifacts")) {
      return new Response(JSON.stringify({ items: [
        { name: "alignedSource", mediaType: "image/jpeg", url: "/full.jpg" },
        { name: "fieldCrop.header_identity.alignedColor", mediaType: "image/jpeg", url: "/crop.jpg" },
      ] }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("shows the full sheet until a field is selected", async () => {
  render(<ReviewPage jobId="job-1" onClose={() => undefined} />);

  expect(await screen.findByAltText("位置合わせ済み帳票")).toHaveAttribute("src", "/full.jpg");

  fireEvent.focus(screen.getAllByRole("textbox")[0]);
  await waitFor(() => {
    expect(screen.getByAltText("header_identity の切り出し")).toHaveAttribute("src", "/crop.jpg");
  });

  fireEvent.click(screen.getByRole("button", { name: "帳票全体" }));
  expect(screen.getByAltText("位置合わせ済み帳票")).toHaveAttribute("src", "/full.jpg");
});
