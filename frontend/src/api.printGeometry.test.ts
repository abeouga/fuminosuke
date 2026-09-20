import { afterEach, expect, test, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

test("explicit print preparation requests the versioned Calc geometry model", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ saved: {} })));
  vi.stubGlobal("fetch", fetcher);
  await api.preparePrintFiles("version", "a".repeat(64));
  const url = new URL(String(fetcher.mock.calls[0][0]), "http://localhost");
  expect(url.pathname).toBe("/api/v1/template-versions/version/print-layout/prepare");
  expect(url.searchParams.get("base_revision")).toBe("a".repeat(64));
  expect(url.searchParams.get("geometry_model")).toBe("wsl-calc-print-grid-v1");
  expect(fetcher.mock.calls[0][1].method).toBe("POST");
});
