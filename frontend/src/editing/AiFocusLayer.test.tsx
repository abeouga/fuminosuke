import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AiFocusLayer, TemplateAiFocus, type TargetAnchorRegistry } from "./AiFocusLayer";
import type { AiFocusState } from "./focusState";
import type { ContextResult, FocusAnchor } from "./v2.generated";

const anchor = (id: string): FocusAnchor => ({ anchorId: id, kind: "review_field", resourceId: "job", fieldId: id, cellId: null, tableId: "table", pageId: null, rowId: null, columnId: null, label: id });
const context = { scopeContext: { resource: { kind: "ocr", id: "job" }, resourceRevision: 3, contextRevision: 1, interactionEpoch: 0, pageScope: { mode: "all", pageId: null } } } as ContextResult;
function focus(ids: string[]): AiFocusState { return { commandId: ids.join(), phase: "applied", receivedAt: performance.now(), focus: { viewRevision: 3, contextRevision: 1, interactionEpoch: 0, primaryAnchorId: ids[0], anchors: ids.map(anchor) } }; }
let element: HTMLDivElement, registry: TargetAnchorRegistry;
beforeEach(() => {
  vi.useFakeTimers(); element = document.createElement("div"); document.body.append(element);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal("scrollBy", vi.fn());
  registry = { resolve: a => ({ element, box: { left: a.fieldId === "a" ? 100 : 500, top: a.fieldId === "far" ? 1500 : 200, width: 80, height: 35 } }), tables: () => [] };
});
afterEach(() => { cleanup(); element.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const tick = (n = 32) => act(async () => { await vi.advanceTimersByTimeAsync(n); });
it("outlines every changed cell together without selecting it or moving keyboard focus", async () => {
  const input = document.createElement("input"); element.append(input); input.focus();
  render(<AiFocusLayer registry={registry} focus={focus(["a", "b"])} context={context} viewRevision={3} />); await tick();
  expect(document.querySelectorAll(".ai-focus-outline.is-applied")).toHaveLength(2);
  expect(document.activeElement).toBe(input); expect(window.scrollBy).not.toHaveBeenCalled();
});
it("does not paint a saved highlight until its matching preview arrives", async () => {
  const props = { registry, focus: focus(["a"]), context };
  const view = render(<AiFocusLayer {...props} viewRevision={2} />); await tick();
  expect(document.querySelectorAll(".ai-focus-outline")).toHaveLength(0);
  view.rerender(<AiFocusLayer {...props} viewRevision={3} />); await tick();
  expect(document.querySelectorAll(".ai-focus-outline")).toHaveLength(1);
});
it("interpolates one outline between cells and finishes at the target", async () => {
  const view = render(<AiFocusLayer registry={registry} focus={focus(["a"])} context={context} viewRevision={3} />); await tick(300);
  view.rerender(<AiFocusLayer registry={registry} focus={focus(["b"])} context={context} viewRevision={3} />); await tick(64);
  const halfway = parseFloat((document.querySelector(".ai-focus-outline") as HTMLElement).style.left);
  expect(halfway).toBeGreaterThan(92); expect(halfway).toBeLessThan(492);
  await tick(300); expect(parseFloat((document.querySelector(".ai-focus-outline") as HTMLElement).style.left)).toBe(492);
});
it("defers automatic scrolling during a user gesture and reevaluates only the latest focus", async () => {
  const view = render(<AiFocusLayer registry={registry} focus={focus(["a"])} context={context} viewRevision={3} />); await tick();
  act(() => window.dispatchEvent(new WheelEvent("wheel")));
  view.rerender(<AiFocusLayer registry={registry} focus={focus(["far"])} context={context} viewRevision={3} />); await tick(300);
  expect(window.scrollBy).not.toHaveBeenCalled();
  view.rerender(<AiFocusLayer registry={registry} focus={focus(["b"])} context={context} viewRevision={3} />); await tick(1000);
  expect(window.scrollBy).not.toHaveBeenCalled();
});
it("suspends scrolling throughout editor input and resumes on exit without moving keyboard focus", async () => {
  const input = document.createElement("input"); element.append(input); input.focus();
  const view = render(<AiFocusLayer registry={registry} focus={focus(["far"])} context={context} viewRevision={3} suspendScroll />);
  await tick(1100); expect(window.scrollBy).not.toHaveBeenCalled();
  view.rerender(<AiFocusLayer registry={registry} focus={focus(["far"])} context={context} viewRevision={3} suspendScroll={false} />);
  await tick(); expect(window.scrollBy).toHaveBeenCalled(); expect(document.activeElement).toBe(input);
});
it("paints proposed new, changed-before, changed-after and removed frames together without layout cells", async () => {
  const canvas = document.createElement("canvas"); element.append(canvas);
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 100, top: 100, width: 400, height: 300 } as DOMRect);
  const state = focus(["new", "old-before", "old-after", "removed"]);
  state.focus.anchors = state.focus.anchors.map(a => ({ ...a, kind: "template_cell", cellId: a.fieldId, fieldId: null }));
  state.regions = ["added", "before", "changed", "removed"].map((kind, i) => ({ anchorId: state.focus.anchors[i].anchorId, cellId: state.focus.anchors[i].cellId!, x: 10 + 40 * i, y: 20, width: 30, height: 20, kind, label: kind })) as NonNullable<AiFocusState["regions"]>;
  const templateContext = { ...context, scopeContext: { ...context.scopeContext, resource: { kind: "template", id: "job" } } } as ContextResult;
  render(<TemplateAiFocus canvasRef={{ current: canvas }} cells={[]} width={400} height={300} sections={[]} groups={[]} focus={state} context={templateContext} viewRevision={3} />);
  await tick(); expect(document.querySelectorAll("[data-ai-change]")).toHaveLength(4);
  expect(document.querySelector('[data-ai-change="added"]')).toHaveTextContent("added");
  expect(document.querySelector('[data-ai-change="removed"]')).toHaveTextContent("removed");
});
it("interpolates between proposed frames even after the old draft-only reference leaves the registry", async () => {
  const canvas = document.createElement("canvas"); element.append(canvas);
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 100, top: 100, width: 400, height: 300 } as DOMRect);
  const templateContext = { ...context, scopeContext: { ...context.scopeContext, resource: { kind: "template", id: "job" } } } as ContextResult;
  const proposed = (id: string, x: number): AiFocusState => {
    const a = { ...anchor(id), kind: "template_cell" as const, cellId: id, fieldId: null };
    return { ...focus([id]), focus: { ...focus([id]).focus, anchors: [a] }, regions: [{ anchorId: id, cellId: id, x, y: 20, width: 30, height: 20, kind: "added", label: "追加予定" }] };
  };
  const props = { canvasRef: { current: canvas }, cells: [], width: 400, height: 300, sections: [], groups: [], context: templateContext, viewRevision: 3 };
  const view = render(<TemplateAiFocus {...props} focus={proposed("one", 10)} />); await tick(300);
  view.rerender(<TemplateAiFocus {...props} focus={proposed("two", 150)} />); await tick(64);
  const halfway = parseFloat((document.querySelector(".ai-focus-outline") as HTMLElement).style.left);
  expect(halfway).toBeGreaterThan(102); expect(halfway).toBeLessThan(242);
  await tick(300); expect(parseFloat((document.querySelector(".ai-focus-outline") as HTMLElement).style.left)).toBe(242);
});
