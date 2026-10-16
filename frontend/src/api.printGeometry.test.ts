import { afterEach, expect, test, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

test("print preparation leaves backend geometry selection to the configured renderer", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ saved: {} }), { headers: { "X-Template-Revision": "2" } }));
  vi.stubGlobal("fetch", fetcher);
  await api.preparePrintFiles("version", 1, "a".repeat(64));
  const url = new URL(String(fetcher.mock.calls[0][0]), "http://localhost");
  expect(url.pathname).toBe("/api/v1/template-versions/version/print-layout/prepare");
  expect(url.searchParams.get("expectedRevision")).toBe("1");
  expect(url.searchParams.get("base_revision")).toBe("a".repeat(64));
  expect(url.searchParams.has("geometry_model")).toBe(false);
  expect(fetcher.mock.calls[0][1].method).toBe("POST");
});
