import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { SalePriceChart } from "./SalePriceChart";

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

afterEach(cleanup);

test("plots complete sale records chronologically and reports excluded rows", () => {
  const { container } = render(<SalePriceChart saleCalves={[
    {
      parity: readable("3"),
      priceYen: readable("770000"),
      saleDate: readable("2019年11月6日"),
      sireName: readable("光晴重"),
    },
    {
      parity: readable("2"),
      priceYen: readable("540.000"),
      saleDate: readable("30 8-10"),
      sireName: readable("光晴重"),
    },
    {
      parity: readable("4"),
      priceYen: readable("800000"),
      saleDate: blank,
      sireName: readable("百合茂"),
    },
  ]} />);

  expect(screen.getByRole("img", {
    name: "売却日と売却価格のグラフ。2件を表示。",
  })).toBeInTheDocument();
  expect(screen.getByText("540,000円")).toBeInTheDocument();
  expect(screen.getByText("770,000円")).toBeInTheDocument();
  expect(screen.getByText("2018/8/10")).toBeInTheDocument();
  expect(screen.getByText("2019/11/6")).toBeInTheDocument();
  expect(screen.getByText("1件は日付または価格が不足しているため非表示です。")).toBeInTheDocument();
  expect(container.querySelectorAll(".sale-chart-point")).toHaveLength(2);
  expect(container.querySelector(".sale-chart-line")).toBeInTheDocument();
});

test("shows an explanation when no row has both date and price", () => {
  render(<SalePriceChart saleCalves={[
    { parity: readable("1"), priceYen: readable("500000"), saleDate: blank },
  ]} />);

  expect(screen.getByRole("status")).toHaveTextContent("グラフに表示できる記録がありません。");
  expect(screen.getByText("1件は日付または価格が不足しています。")).toBeInTheDocument();
});
