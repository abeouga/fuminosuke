import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TemplateAnalysisPanel } from "./TemplateAnalysisPanel";
import type { AnalysisView, TemplateAnalysisStore } from "./templateAnalysisStore";
import type { AnalysisResult } from "./template-analysis.generated";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); delete (document as unknown as { elementFromPoint?: unknown }).elementFromPoint; });
function visibleLayout(inputY = 200) {
  let y = inputY;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return this.matches("input[data-analysis-unit]") ? new DOMRect(100, y, 13, 13)
      : this.matches("button[data-analysis-merge]") ? new DOMRect(100, 140, 100, 40)
      : this.matches("[data-analysis-preview]") ? new DOMRect(100, 220, 300, 20)
      : new DOMRect(0, 0, 1024, 768);
  });
  const hit = vi.fn(() => document.querySelector("input[data-analysis-unit]"));
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: hit });
  hit.mockImplementation((_x?: number, pointY?: number) => document.querySelector(pointY === 160 ? "button[data-analysis-merge]" : pointY === 230 ? "[data-analysis-preview]" : "input[data-analysis-unit]"));
  return (nextY: number) => { y = nextY; };
}
function frames() {
  let id = 0;
  const callbacks = new Map<number, FrameRequestCallback>();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation(cb => { callbacks.set(++id, cb); return id; });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(key => { callbacks.delete(key); });
  return () => { for (let i = 0; i < 2; i++) act(() => { const pending = [...callbacks.values()]; callbacks.clear(); pending.forEach(cb => cb(performance.now())); }); };
}
function setup() {
  const run: AnalysisResult = { contractVersion: "template-analysis.v3", analysisId: "run", requestId: "req", resource: { kind: "template", id: "v" },
    baseRevision: 1, snapshotDigest: "s", draftDigest: "d", scopeDigest: "scope", state: "ready", pages: [], plannedCalls: 1, reservedCalls: 1, completedCalls: 1, expiresAt: "later", lastSequence: 2, error: null,
    proposal: { proposalDigest: "proposal", coverage: {}, focus: null, unresolved: [{ cellId: "new", pageId: null, reason: "ambiguous_binding" }], units: [{
      unitId: "unit", pageId: null, kind: "structure", cellIds: ["old", "new"], name: "枠と項目", state: "mergeable", reason: null, dependencies: [],
      geometry: { before: [{ id: "old", x: 10, y: 20, width: 30, height: 40 }], after: [{ id: "new", x: 20, y: 50, width: 60, height: 70 }], addedIds: ["new"], removedIds: ["old"], changedIds: [], referenceMap: { n1: "new" } },
    }] } };
  let state: AnalysisView = { run, busy: false, held: true, message: "", focus: null, applied: [], conflicts: [], staleUnits: [], recovery: false, storageBlocked: false, pendingFacets: 0, manualSerial: 0 };
  const methods = { getSnapshot: () => state, subscribe: () => () => {}, preview: vi.fn(), previewCell: vi.fn(), previewUnresolved: vi.fn(), merge: vi.fn(), start: vi.fn(), painted: vi.fn(), finish: vi.fn() };
  const store = methods as unknown as TemplateAnalysisStore;
  const props = { store, pageScope: { mode: "all" as const, pageId: null }, multiplePages: false, blocked: false, saving: false, onSave: vi.fn(), structureAvailable: true };
  const view = render(<TemplateAnalysisPanel {...props} />);
  return { methods, view, props, setState: (next: Partial<AnalysisView>) => { state = { ...state, ...next }; view.rerender(<TemplateAnalysisPanel {...props} />); } };
}
it("offers manual editing after an empty or failed proposal without saving anything", () => {
  const { methods, props, setState } = setup();
  const run = methods.getSnapshot().run!;
  setState({ run: { ...run, state: "failed", error: { code: "AI_API_BUDGET_EXHAUSTED", message: "利用上限に達しました。" }, proposal: null }, message: "利用上限に達しました。" });
  expect(screen.getByText("利用上限に達しました。")).toBeInTheDocument();
  expect(screen.getByText(/反映できるAI案はありません/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "手動編集に戻る" }));
  expect(methods.finish).toHaveBeenCalledOnce();
  expect(methods.merge).not.toHaveBeenCalled(); expect(props.onSave).not.toHaveBeenCalled();
  setState({ recovery: true });
  expect(screen.getByRole("button", { name: "手動編集に戻る" })).toBeDisabled();
  expect(screen.getByText(/処理結果が未確認/)).toBeInTheDocument();
});
it("shows add/remove coordinates and previews the selected proposal without saving", () => {
  const { methods, props } = setup();
  fireEvent.click(screen.getByText("変更内容を確認"));
  expect(screen.getByText("枠の差分：追加1・変更0・除去1")).toBeInTheDocument();
  fireEvent.click(screen.getByText("枠の差分：追加1・変更0・除去1"));
  expect(screen.getByText("位置 10, 20 / 大きさ 30 × 40")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "この枠を表示" })[1]); expect(methods.previewCell).toHaveBeenCalledWith("new");
  fireEvent.click(screen.getByRole("checkbox", { name: /枠と項目/ }));
  expect(methods.preview).toHaveBeenCalledWith(["unit"]); expect(props.onSave).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "案を反映" })); expect(methods.merge).toHaveBeenCalledWith(["unit"]);
});
it("blocks a previously selected stale unit and keeps a manual repair location accessible", () => {
  const { methods, setState } = setup();
  fireEvent.click(screen.getByRole("button", { name: "位置を表示" })); expect(methods.previewUnresolved).toHaveBeenCalledWith("new");
  fireEvent.click(screen.getByRole("checkbox", { name: /枠と項目/ })); setState({ staleUnits: ["unit"] });
  expect(screen.getByRole("button", { name: "案を反映" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /枠と項目/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "位置を表示" })).toBeDisabled();
  expect(methods.merge).not.toHaveBeenCalled();
});
it("allows a validated region to be applied while other regions are still running", () => {
  const { methods, setState } = setup();
  const base = methods.getSnapshot().run!;
  setState({ run: { ...base, state: "running" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /枠と項目/ }));
  expect(screen.getByRole("button", { name: "案を反映" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "案を反映" }));
  expect(methods.merge).toHaveBeenCalledWith(["unit"]);
});
it("records system readiness before selection and excludes unavailable or failed proposals", () => {
  visibleLayout();
  let frame = 0;
  const callbacks = new Map<number, FrameRequestCallback>();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation(cb => { callbacks.set(++frame, cb); return frame; });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(id => { callbacks.delete(id); });
  const flush = () => { for (let i = 0; i < 2; i++) act(() => { const pending = [...callbacks.values()]; callbacks.clear(); pending.forEach(cb => cb(performance.now())); }); };
  const { methods, setState, view, props } = setup();
  flush();
  expect(screen.getByRole("checkbox", { name: /枠と項目/ })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "案を反映" })).toBeDisabled();
  expect(methods.painted).toHaveBeenCalledWith(["unit"], true, expect.objectContaining({
    analysisId: "run", selectionControls: [expect.objectContaining({ unitId: "unit", enabled: true })],
    mergeControl: expect.objectContaining({ present: true, enabled: false, requiresSelection: true }),
  }));
  for (const unavailable of [{ busy: true }, { recovery: true }, { storageBlocked: true }, { staleUnits: ["unit"] }, { conflicts: ["unit"] }]) {
    methods.painted.mockClear();
    setState({ busy: false, recovery: false, storageBlocked: false, staleUnits: [], conflicts: [], ...unavailable }); flush();
    expect(methods.painted.mock.calls.filter(([, actionable]) => actionable).flatMap(([ids]) => ids)).toEqual([]);
  }
  methods.painted.mockClear();
  setState({ conflicts: [], busy: false });
  view.rerender(<TemplateAnalysisPanel {...props} saving />); flush();
  expect(methods.painted.mock.calls.filter(([, actionable]) => actionable).flatMap(([ids]) => ids)).toEqual([]);
  methods.painted.mockClear();
  const base = methods.getSnapshot().run!;
  setState({ run: { ...base, state: "failed" } }); flush();
  expect(methods.painted.mock.calls.filter(([, actionable]) => actionable).flatMap(([ids]) => ids)).toEqual([]);
});
it("does not count an offscreen proposal and rechecks when scrolling reveals it", () => {
  const setY = visibleLayout(1000), flush = frames();
  const { methods } = setup(); flush();
  expect(methods.painted.mock.calls.filter(([, actionable]) => actionable).flatMap(([ids]) => ids)).toEqual([]);
  methods.painted.mockClear(); setY(200);
  fireEvent.scroll(document); flush();
  expect(methods.painted.mock.calls.filter(([, actionable]) => actionable).flatMap(([ids]) => ids)).toContain("unit");
});
it("places a late prioritized region before regions that finished earlier", () => {
  const { methods, setState } = setup();
  const base = methods.getSnapshot().run!, unit = base.proposal!.units[0];
  setState({ run: { ...base, proposal: { ...base.proposal!, units: [
    { ...unit, unitId: "finished-first", displayOrder: 1 }, { ...unit, unitId: "prioritized", displayOrder: 0 },
  ] } } });
  expect([...document.querySelectorAll("input[data-analysis-unit]")].map(e => (e as HTMLElement).dataset.analysisUnit)).toEqual(["prioritized", "finished-first"]);
});
