import { afterEach, expect, test, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

test("paged output uses authenticated download and validates the result revision header", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("xlsx", { headers: { "X-Result-Revision": "7" } }));
  vi.stubGlobal("fetch", fetcher);
  const blob = await api.downloadPageOutput("job/id", 7);
  const content = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
  expect(content).toBe("xlsx");
  expect(fetcher).toHaveBeenCalledWith("/api/v1/ocr-jobs/job%2Fid/page-review/output.xlsx?revision=7", { credentials: "include" });
});

test.each([null, "6"])("rejects a download from an absent or wrong revision: %s", async (header) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("xlsx", { headers: header ? { "X-Result-Revision": header } : {} })));
  await expect(api.downloadPageOutput("job", 7)).rejects.toMatchObject({ code: "PAGE_OUTPUT_REVISION_CHANGED" });
});

test("does not treat an API error document as an XLSX file", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "状態が変わりました", code: "PAGE_OUTPUT_REVISION_CHANGED" }), { status: 409 })));
  await expect(api.downloadPageOutput("job", 7)).rejects.toMatchObject({ message: "状態が変わりました", code: "PAGE_OUTPUT_REVISION_CHANGED" });
});
