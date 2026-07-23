import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/health")) return new Response(JSON.stringify({ status: "ok", database: "ok", jobWorker: "running", ocrProvider: "mock" }), { status: 200 });
    if (path.endsWith("/templates")) return new Response("[]", { status: 200 });
    if (path.includes("/individual-records?individualId=")) return new Response(JSON.stringify({
      query: "123-4567-890",
      individualId: "1234567890",
      count: 1,
      records: [{
        recordId: "record-1",
        individualId: "1234567890",
        sourceJobId: "job-1",
        ocrResultRevision: 2,
        confirmedAt: "2026-07-22T12:00:00Z",
        data: {
          schemaVersion: "1.0",
          templateId: "template-1",
          templateVersion: "version-1",
          document: {
            cattleName: { raw: "修正花子", normalized: "修正花子", status: "readable", alternatives: [], reviewReason: null, sourceRegionId: "identity", boundingBox: null },
          },
          saleCalves: [],
          pedigree: {},
          calvings: [],
          reviewItems: [],
          processingMetadata: {},
        },
      }],
    }), { status: 200 });
    return new Response("{}", { status: 200 });
  }));
});

afterEach(() => cleanup());

test("shows the local workbench home", async () => {
  render(<App />);
  expect(screen.getByText("紙の台帳を、", { exact: false })).toBeInTheDocument();
  expect(await screen.findByText("試運転モード")).toBeInTheDocument();
});

test("searches confirmed JSON by individual identification number", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /個体検索/ }));
  fireEvent.change(screen.getByLabelText("個体識別番号"), {
    target: { value: "123-4567-890" },
  });
  fireEvent.click(screen.getByRole("button", { name: "JSONを検索" }));

  expect((await screen.findAllByText("123-4567-890")).length).toBe(2);
  expect(screen.getByText("修正花子")).toBeInTheDocument();
  expect(screen.getByText("1件の確定済み帳票が見つかりました。")).toBeInTheDocument();
});
