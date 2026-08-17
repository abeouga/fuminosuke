import { expect, test } from "vitest";
import {
  automaticJapaneseEraDatePatch,
  normalizeJapaneseDateDisplay,
} from "./japaneseDates";

test.each([
  ["30 8-10", "2018年8月10日"],
  ["30,8,10", "2018年8月10日"],
  ["5.11.16", "2023年11月16日"],
  ["5-11-16", "2023年11月16日"],
  ["2001-11-6", "2001年11月6日"],
])("normalizes %s to a Japanese western-date display", (raw, expected) => {
  expect(normalizeJapaneseDateDisplay(raw, raw)).toBe(expected);
  expect(automaticJapaneseEraDatePatch(raw).normalized).toBe(expected);
});
