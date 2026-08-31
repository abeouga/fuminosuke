import { describe, expect, it } from "vitest";

import {
  eraseMeaningLabelLinksAlongSegment,
  eraseMeaningParentLinksAlongSegment,
  segmentDistance,
} from "./LayoutEditor";


describe("LayoutEditor eraser geometry", () => {
  it("does not intersect every link when the eraser is clicked away from it", () => {
    expect(segmentDistance(
      { x: 10, y: 10 },
      { x: 10, y: 10 },
      { x: 50, y: 0 },
      { x: 50, y: 100 },
    )).toBe(40);
  });

  it("removes a heading-to-label meaning link without removing the field mapping", () => {
    const relations = [{
      id: "birth_year_relation",
      order: 1,
      inputCellId: "birth_year_input",
      labelCellIds: ["birth_year_label"],
      parentLabelCellId: "birth_parent",
      axis: "vertical",
      targetPath: "person.birthYear",
    }] as Parameters<typeof eraseMeaningParentLinksAlongSegment>[0];
    const cellsById = new Map([
      ["birth_parent", {
        id: "birth_parent", role: "parent_label", x: 0, y: 0, width: 80, height: 30,
        confidence: 1, text: "生年月日", textConfidence: 1, source: "manual",
      }],
      ["birth_year_label", {
        id: "birth_year_label", role: "label", x: 0, y: 80, width: 80, height: 30,
        confidence: 1, text: "西暦", textConfidence: 1, source: "manual",
      }],
      ["birth_year_input", {
        id: "birth_year_input", role: "input", x: 100, y: 80, width: 80, height: 30,
        confidence: 1, text: "", textConfidence: null, source: "manual",
      }],
    ]) as Parameters<typeof eraseMeaningParentLinksAlongSegment>[1];

    const result = eraseMeaningParentLinksAlongSegment(
      relations,
      cellsById,
      { x: 20, y: 55 },
      { x: 60, y: 55 },
      8,
    );

    expect(result.changed).toBe(true);
    expect(result.relations).toEqual([expect.objectContaining({
      inputCellId: "birth_year_input",
      labelCellIds: ["birth_year_label"],
      targetPath: "person.birthYear",
      parentLabelCellId: null,
    })]);
  });

  it("removes a meaning relation when adjacent label and input cells leave no visible line", () => {
    const relations = [{
      id: "birth_month_relation",
      order: 1,
      inputCellId: "birth_month_input",
      labelCellIds: ["birth_month_label"],
      parentLabelCellId: null,
      axis: "horizontal",
      targetPath: "person.birthMonth",
    }] as Parameters<typeof eraseMeaningLabelLinksAlongSegment>[0];
    const cellsById = new Map([
      ["birth_month_label", {
        id: "birth_month_label", role: "label", x: 0, y: 0, width: 80, height: 30,
        confidence: 1, text: "月", textConfidence: 1, source: "manual",
      }],
      ["birth_month_input", {
        id: "birth_month_input", role: "input", x: 80, y: 0, width: 80, height: 30,
        confidence: 1, text: "", textConfidence: null, source: "manual",
      }],
    ]) as Parameters<typeof eraseMeaningLabelLinksAlongSegment>[1];

    const result = eraseMeaningLabelLinksAlongSegment(
      relations,
      cellsById,
      { x: 110, y: 15 },
      { x: 110, y: 15 },
      8,
      400,
      300,
    );

    expect(result).toEqual({ relations: [], changed: true });
  });

  it("detects a click placed directly on a link", () => {
    expect(segmentDistance(
      { x: 50, y: 30 },
      { x: 50, y: 30 },
      { x: 50, y: 0 },
      { x: 50, y: 100 },
    )).toBe(0);
  });

  it("detects two crossing segments", () => {
    expect(segmentDistance(
      { x: 0, y: 50 },
      { x: 100, y: 50 },
      { x: 50, y: 0 },
      { x: 50, y: 100 },
    )).toBe(0);
  });

  it("keeps separated collinear segments apart", () => {
    expect(segmentDistance(
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 20, y: 0 },
      { x: 30, y: 0 },
    )).toBe(10);
  });
});
