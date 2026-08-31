import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ExcelExportActions } from "./ExcelExportActions";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("copies the confirmed table and offers a per-document xlsx download", async () => {
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    fileName: "繁殖台帳.xlsx",
    sheetName: "読み取り結果",
    maxColumns: 1,
    rows: [{
      kind: "data",
      cells: [{ value: "よつば", display: "よつば", kind: "text" }],
    }],
  }), { status: 200 })));

  render(<ExcelExportActions jobId="job-1" />);
  expect(screen.getByRole("link", { name: ".xlsxを保存" })).toHaveAttribute(
    "href",
    "/api/v1/ocr-jobs/job-1/export?format=xlsx",
  );
  fireEvent.click(screen.getByRole("button", { name: "Excelへコピー" }));

  expect(await screen.findByText("罫線付きでコピーしました。Excelで貼り付けできます。")).toBeInTheDocument();
  expect(writeText).toHaveBeenCalledWith("よつば");
});

test("renames the Drive spreadsheet after the output name is edited", async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({
    enabled: true,
    status: "synced",
    spreadsheetUrl: "https://docs.google.com/spreadsheets/d/sheet-1/edit",
    fileName: "面接カード_阿部桜雅",
    lastError: null,
  }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  render(
    <ExcelExportActions
      jobId="job-1"
      driveFileName="面接カード_入力済み"
    />,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "書類名" }), {
    target: { value: "面接カード_阿部桜雅.xlsx" },
  });

  expect(await screen.findByText("Drive上のファイル名へ反映しました。", {}, { timeout: 2000 }))
    .toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/ocr-jobs/job-1/spreadsheet-name",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ fileName: "面接カード_阿部桜雅.xlsx" }),
    }),
  );
  expect(screen.getByRole("textbox", { name: "書類名" }))
    .toHaveValue("面接カード_阿部桜雅");
});
