import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ExcelExportActions } from "./ExcelExportActions";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test.each([undefined, "確定した書類名"])("keeps download failures in the review and retries with document name %s", async (documentName) => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "XLSXを生成できませんでした。" }), { status: 500 }))
    .mockResolvedValueOnce(new Response(new Blob(["xlsx"]), { headers: {
      "Content-Disposition": "attachment; filename*=utf-8''%E7%A2%BA%E8%AA%8D%E6%9B%B8%E9%A1%9E.xlsx",
    } }));
  vi.stubGlobal("fetch", fetchMock);
  const createObjectURL = vi.fn(() => "blob:xlsx");
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = createObjectURL;
    static revokeObjectURL = vi.fn();
  });
  const downloads: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download); });
  render(<ExcelExportActions jobId="job-1" documentName={documentName} />);
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: ".xlsxを保存" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("XLSXを生成できませんでした。");
  expect(createObjectURL).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: ".xlsxを保存" }));
  await waitFor(() => expect(downloads).toEqual([documentName ? `${documentName}.xlsx` : "確認書類.xlsx"]));
  expect(fetchMock).toHaveBeenCalledTimes(2);
  const [url, options] = fetchMock.mock.calls[1];
  expect(url).toBe("/api/v1/ocr-jobs/job-1/export?format=xlsx");
  const headers: Record<string, string> = {};
  new Headers(options.headers).forEach((value, key) => { headers[key] = value; });
  expect({ ...options, headers }).toEqual({ credentials: "include", headers: {} });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Excelへコピー" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(screen.queryByText(/Drive|Google|ライブラリ/)).not.toBeInTheDocument();
});
