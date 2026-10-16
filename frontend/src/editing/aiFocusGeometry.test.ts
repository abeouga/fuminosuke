import { describe, expect, it } from "vitest";
import { scrollDelta, scrollTarget, stickyTitle } from "./aiFocusGeometry";
import { validFocus } from "./focusState";
import type { ContextResult, Focus } from "./v2.generated";

const view = { left: 100, top: 100, width: 400, height: 300 };
describe("preview movement policy", () => {
  it("does not recenter a visible cell; moves a partial cell only enough", () => {
    expect(scrollDelta({ left: 120, top: 120, width: 80, height: 30 }, view)).toEqual({ left: 0, top: 0 });
    expect(scrollDelta({ left: 120, top: 380, width: 80, height: 30 }, view)).toEqual({ left: 0, top: 10 });
  });
  it("centers an offscreen cell and exposes the label edge of a tall cell", () => {
    expect(scrollDelta({ left: 120, top: 600, width: 80, height: 40 }, view).top).toBe(370);
    expect(scrollDelta({ left: 120, top: 600, width: 80, height: 500 }, view).top).toBe(500);
  });
  it("fits one multi-cell union and retains a visible cell when the union is too large", () => {
    const a = { left: 120, top: 120, width: 80, height: 30 }, b = { ...a, top: 200 }, far = { ...a, top: 1500 };
    expect(scrollTarget([a, b], view)).toEqual({ left: 120, top: 120, width: 80, height: 110 });
    expect(scrollTarget([far, a], view)).toEqual(a);
    expect(scrollTarget([far, { ...far, top: 3000 }], view)).toEqual(far);
  });
  it("shows a title only while its header is above and body is still visible", () => {
    const header = { left: 100, top: 50, width: 400, height: 25 }, body = { left: 100, top: 80, width: 400, height: 301 };
    expect(stickyTitle(header, body, view)).toBe(true);
    expect(stickyTitle({ ...header, top: 110 }, body, view)).toBe(false);
    expect(stickyTitle(header, { ...body, top: -300 }, view)).toBe(false);
    expect(stickyTitle({ ...header, left: -900, top: 110 }, body, view)).toBe(false);
  });
  it("does not duplicate a visible header outside the table's inner scroller", () => {
    const header = { left: 100, top: 50, width: 400, height: 25 }, body = { left: 100, top: 100, width: 400, height: 600 };
    const pageView = { left: 0, top: 8, width: 1000, height: 800 };
    expect(stickyTitle(header, body, view, pageView)).toBe(false);
    expect(stickyTitle({ ...header, top: -25 }, body, view, pageView)).toBe(true);
  });
});
it("rejects an old focus, another resource, and a receipt whose preview has not arrived", () => {
  const context = { scopeContext: { resource: { kind: "ocr", id: "owned" }, resourceRevision: 5, contextRevision: 2, interactionEpoch: 3, pageScope: { mode: "all", pageId: null } } } as ContextResult;
  const focus: Focus = { viewRevision: 5, contextRevision: 2, interactionEpoch: 3, primaryAnchorId: "a", anchors: [{ anchorId: "a", kind: "review_field", resourceId: "owned", pageId: null, tableId: null, fieldId: "f", cellId: null, rowId: null, columnId: null, label: "欄" }] };
  expect(validFocus(focus, context, 5)).toBe(true);
  expect(validFocus(focus, context, 4)).toBe(false);
  expect(validFocus({ ...focus, interactionEpoch: 2 }, context, 5)).toBe(false);
  expect(validFocus({ ...focus, anchors: [{ ...focus.anchors[0], resourceId: "foreign" }] }, context, 5)).toBe(false);
});
