import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { IndividualRecordsPage } from "./IndividualRecordsPage";

const catalog = {
  count: 1,
  templates: [{
    templateId: "template-1",
    templateName: "繁殖台帳",
    documentCount: 1,
    fields: [
      {
        fieldKey: "document.cattleName",
        label: "名号",
        sectionTitle: "基本情報",
        valueType: "text",
        repeated: false,
        identity: true,
        suggestions: [],
      },
      {
        fieldKey: "saleCalves[].sireName",
        label: "種雄牛名",
        sectionTitle: "売却子牛一覧",
        valueType: "text",
        repeated: true,
        identity: false,
        suggestions: ["光晴重", "福之姫"],
      },
      {
        fieldKey: "saleCalves[].priceYen",
        label: "価格",
        sectionTitle: "売却子牛一覧",
        valueType: "number",
        repeated: true,
        identity: false,
        suggestions: [],
      },
      {
        fieldKey: "document.birthDate",
        label: "生年月日",
        sectionTitle: "基本情報",
        valueType: "date",
        repeated: false,
        identity: false,
        suggestions: [],
      },
    ],
  }],
};

const result = {
  query: "",
  count: 1,
  page: 1,
  pageSize: 20,
  items: [{
    documentId: "document-1",
    templateId: "template-1",
    templateName: "繁殖台帳",
    sourceJobId: "job-1",
    confirmedAt: "2026-08-23T01:02:00Z",
    pageCount: 2,
    spreadsheetUrl: "https://docs.google.com/spreadsheets/d/test/edit",
    displayTitle: "こよみ",
    displayTitleLabel: "名号",
    displaySubtitle: "123-4567-890",
    displaySubtitleLabel: "個体識別番号",
    matches: [{
      fieldKey: "saleCalves[].sireName",
      label: "種雄牛名",
      sectionTitle: "売却子牛一覧",
      recordOrder: 2,
      recordKey: "3",
      value: "福之姫",
    }],
  }],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("searches confirmed data with template-driven fields", async () => {
  const requests: Array<Record<string, unknown>> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/data-search/catalog")) {
      return new Response(JSON.stringify(catalog), { status: 200 });
    }
    if (path.endsWith("/data-search")) {
      requests.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
      return new Response(JSON.stringify(result), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(<IndividualRecordsPage />);

  expect(await screen.findByRole("heading", { name: "登録済みデータを検索" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "こよみ" })).toBeInTheDocument();
  expect(screen.getByText("個体識別番号 123-4567-890")).toBeInTheDocument();
  expect(screen.getByText("売却子牛一覧 3")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("書類の種類"), { target: { value: "template-1" } });
  fireEvent.click(screen.getByRole("button", { name: "＋ 条件を追加" }));
  fireEvent.change(screen.getByLabelText("検索項目 1"), {
    target: { value: "saleCalves[].sireName" },
  });
  fireEvent.change(screen.getByLabelText("検索値 1"), { target: { value: "福之姫" } });
  fireEvent.click(screen.getByRole("button", { name: "＋ 条件を追加" }));
  fireEvent.change(screen.getByLabelText("検索項目 2"), {
    target: { value: "saleCalves[].priceYen" },
  });
  fireEvent.change(screen.getByLabelText("検索条件 2"), { target: { value: "gte" } });
  fireEvent.change(screen.getByLabelText("検索値 2"), { target: { value: "500000" } });
  fireEvent.click(screen.getByRole("button", { name: "この条件で検索" }));

  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toMatchObject({
    templateId: "template-1",
    filters: [
      { fieldKey: "saleCalves[].sireName", operator: "contains", value: "福之姫" },
      { fieldKey: "saleCalves[].priceYen", operator: "gte", value: "500000" },
    ],
  });
});

test("opens the document, spreadsheet, and confirmed result from a search card", async () => {
  const onOpenDocument = vi.fn();
  const onSpreadsheetNavigate = vi.fn();
  const onEditResult = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/data-search/catalog")) {
      return new Response(JSON.stringify(catalog), { status: 200 });
    }
    if (path.endsWith("/data-search")) {
      return new Response(JSON.stringify(result), { status: 200 });
    }
    if (path.endsWith("/ocr-jobs/job-1/reopen")) {
      return new Response("{}", { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(
    <IndividualRecordsPage
      onOpenDocument={onOpenDocument}
      onEditResult={onEditResult}
      onSpreadsheetNavigate={onSpreadsheetNavigate}
    />,
  );
  await screen.findByRole("heading", { name: "こよみ" });

  fireEvent.click(screen.getByRole("button", { name: "書類を開く" }));
  expect(onOpenDocument).toHaveBeenCalledWith("document-1");
  fireEvent.click(screen.getByRole("button", { name: "スプレッドシートを開く ↗" }));
  expect(onSpreadsheetNavigate).toHaveBeenCalledWith(
    "https://docs.google.com/spreadsheets/d/test/edit",
  );
  fireEvent.click(screen.getByRole("button", { name: "読み取り結果を確認・修正" }));
  await waitFor(() => expect(onEditResult).toHaveBeenCalledWith("job-1"));
});

test("offers an inclusive period for date fields", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/data-search/catalog")) {
      return new Response(JSON.stringify(catalog), { status: 200 });
    }
    if (path.endsWith("/data-search")) {
      return new Response(JSON.stringify(result), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(<IndividualRecordsPage />);
  await screen.findByRole("heading", { name: "こよみ" });
  fireEvent.change(screen.getByLabelText("書類の種類"), { target: { value: "template-1" } });
  fireEvent.click(screen.getByRole("button", { name: "＋ 条件を追加" }));
  fireEvent.change(screen.getByLabelText("検索項目 1"), {
    target: { value: "document.birthDate" },
  });
  fireEvent.change(screen.getByLabelText("検索条件 1"), { target: { value: "between" } });

  expect(screen.getByLabelText("検索値 1")).toHaveAttribute("type", "date");
  expect(screen.getByLabelText("検索終了値 1")).toHaveAttribute("type", "date");
});
