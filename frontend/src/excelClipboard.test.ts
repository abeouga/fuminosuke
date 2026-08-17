import { afterEach, expect, test, vi } from "vitest";
import type { ExcelTable } from "./types";
import { copyExcelTable, excelTableToHtml, excelTableToTsv } from "./excelClipboard";

const table: ExcelTable = {
  fileName: "繁殖台帳_001.xlsx",
  sheetName: "読み取り結果",
  maxColumns: 2,
  rows: [
    { kind: "section", cells: [{ value: "基本情報", display: "基本情報", kind: "text" }] },
    { kind: "header", cells: [
      { value: "項目", display: "項目", kind: "text" },
      { value: "値", display: "値", kind: "text" },
    ] },
    { kind: "data", cells: [
      { value: "個体識別番号", display: "個体識別番号", kind: "text" },
      { value: "0012345678", display: "0012345678", kind: "identifier" },
    ] },
    { kind: "data", cells: [
      { value: "備考", display: "備考", kind: "text" },
      { value: "=1+1", display: "=1+1", kind: "text" },
    ] },
  ],
};

afterEach(() => vi.unstubAllGlobals());

test("builds Excel-pasteable TSV and rich HTML while protecting formula-like text", () => {
  expect(excelTableToTsv(table)).toContain("0012345678");
  expect(excelTableToTsv(table)).toContain("'\u003d1+1");
  const html = excelTableToHtml(table);
  expect(html).toContain('colspan="2"');
  expect(html).toContain("mso-number-format:'\\@'");
  expect(html).toContain("&#39;=1+1");
  expect(html).toContain("border-collapse:collapse");
  expect(html).toContain("border:1px solid #D7E0E7");
  expect(html).toContain("background:#5B9BD5");
  expect(html).toContain("background:#D9EAF7");
});

test("writes both formatted HTML and TSV to the rich clipboard", async () => {
  let payload: Record<string, Blob> | null = null;
  class FakeClipboardItem {
    constructor(data: Record<string, Blob>) {
      payload = data;
    }
  }
  const write = vi.fn(async () => undefined);
  vi.stubGlobal("ClipboardItem", FakeClipboardItem);
  vi.stubGlobal("navigator", { clipboard: { write } });

  await copyExcelTable(table);

  expect(write).toHaveBeenCalledTimes(1);
  expect(payload).not.toBeNull();
  expect(Object.keys(payload ?? {})).toEqual(["text/plain", "text/html"]);
});

test("falls back to plain clipboard text when rich clipboard is unavailable", async () => {
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });

  await copyExcelTable(table);

  expect(writeText).toHaveBeenCalledWith(expect.stringContaining("0012345678"));
});
