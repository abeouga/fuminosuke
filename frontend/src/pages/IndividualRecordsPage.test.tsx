import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { IndividualRecordsPage } from "./IndividualRecordsPage";

const readable = (value: string) => ({
  raw: value,
  normalized: value,
  status: "readable" as const,
  alternatives: [],
  reviewReason: null,
  sourceRegionId: "test",
  boundingBox: null,
});

const blank = {
  raw: null,
  normalized: null,
  status: "blank" as const,
  alternatives: [],
  reviewReason: null,
  sourceRegionId: "test",
  boundingBox: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("renders an individual record as human-readable sections instead of JSON code", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/individual-records/catalog")) {
      return new Response(JSON.stringify({
        count: 1,
        items: [{
          individualId: "0000000001",
          cattleName: "よみ",
          recordCount: 1,
          lastConfirmedAt: "2026-07-27T12:00:00Z",
        }],
      }), { status: 200 });
    }
    if (path.includes("/individual-records?individualId=")) {
      return new Response(JSON.stringify({
        query: "0000000001",
        individualId: "0000000001",
        count: 1,
        records: [{
          recordId: "record-1",
          individualId: "0000000001",
          sourceJobId: "job-1",
          ocrResultRevision: 2,
          confirmedAt: "2026-07-27T12:00:00Z",
          data: {
            schemaVersion: "1.0",
            templateId: "template-1",
            templateVersion: "1",
            document: {
              unifiedEarTag: readable("0000000001"),
              cattleName: readable("よみ"),
              birthDate: readable("平成26年9月27日"),
              registrationNumber: readable("27093"),
              coatColor: readable("黒"),
              breeder: readable("阿蘇牧場"),
              owner: readable("花子"),
            },
            saleCalves: [
              {
                parity: readable("2"),
                ageDays: readable("277"),
                weightKg: readable("323"),
                priceYen: readable("762.300"),
                sireName: readable("光晴重"),
                saleDate: readable("令和1年11月6日"),
                notes: blank,
              },
              {
                parity: blank,
                ageDays: blank,
                weightKg: blank,
                priceYen: blank,
                sireName: blank,
                saleDate: blank,
                notes: blank,
              },
            ],
            pedigree: {
              father: readable("幸紀雄"),
              mother: readable("みつえい"),
              paternalGrandfather: readable("百合茂"),
              paternalGrandmother: blank,
              maternalGrandfather: readable("安福久"),
              maternalGrandmother: blank,
            },
            calvings: [{
              parity: readable("1"),
              calvingDate: readable("平成28年7月25日"),
              sex: readable("雌"),
              outcome: readable("正常"),
              notes: blank,
            }],
            customFields: {
              "document.farmCode": readable("A-12"),
            },
            reviewItems: [],
            processingMetadata: {},
          },
        }],
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  const { container } = render(<IndividualRecordsPage />);
  fireEvent.change(screen.getByLabelText("個体識別番号"), {
    target: { value: "000-0000-001" },
  });
  fireEvent.click(screen.getByRole("button", { name: "個体を検索" }));

  expect(await screen.findByRole("heading", { name: "基本情報" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "血統" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "売却子牛一覧" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "繁殖履歴" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "追加項目" })).toBeInTheDocument();
  expect(screen.getAllByText("よみ").length).toBeGreaterThan(0);
  expect(screen.getByText("幸紀雄")).toBeInTheDocument();

  const saleSection = screen.getByRole("heading", { name: "売却子牛一覧" }).closest("section");
  expect(saleSection).not.toBeNull();
  expect(within(saleSection as HTMLElement).getByText("762,300円")).toBeInTheDocument();
  expect(within(saleSection as HTMLElement).getAllByRole("row")).toHaveLength(2);

  expect(container.querySelector("pre")).not.toBeInTheDocument();
  expect(screen.queryByText("保存されているJSON全体")).not.toBeInTheDocument();
  expect(screen.queryByText("APIのJSONを開く")).not.toBeInTheDocument();
});

test("selects an individual from the confirmed record catalog", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/individual-records/catalog")) {
      return new Response(JSON.stringify({
        count: 1,
        items: [{
          individualId: "0000000001",
          cattleName: "よみ",
          recordCount: 1,
          lastConfirmedAt: "2026-07-27T12:00:00Z",
        }],
      }), { status: 200 });
    }
    if (path.includes("/individual-records?individualId=")) {
      return new Response(JSON.stringify({
        query: "0000000001",
        individualId: "0000000001",
        count: 0,
        records: [],
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(<IndividualRecordsPage />);
  fireEvent.click(await screen.findByRole("button", { name: /よみ.*表示する/ }));

  expect(screen.getByLabelText("個体識別番号")).toHaveValue("0000000001");
  expect(await screen.findByText("0件の確定済み帳票が見つかりました。")).toBeInTheDocument();
});

test("marks legacy confirmed data that needs rechecking", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/individual-records/catalog")) {
      return new Response(JSON.stringify({
        count: 1,
        items: [{
          individualId: "0000000001",
          cattleName: "要確認牛",
          recordCount: 1,
          lastConfirmedAt: "2026-07-27T12:00:00Z",
          needsReview: true,
        }],
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(<IndividualRecordsPage />);

  expect(await screen.findByText("要再確認")).toBeInTheDocument();
});
