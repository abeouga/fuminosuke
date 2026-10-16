import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TemplateAnalysisStore } from "./templateAnalysisStore";
import { templateAnalysisClient as client, analysisEvents } from "./templateAnalysisClient";
import { EditingError } from "./v2client";
import { changedFacets, facetTouch, patchedCellFacets } from "./authoringFacets";
import type { WorkbenchStore } from "./workbenchStore";
import type { AnalysisResult, ManualDraft, MergeResult } from "./template-analysis.generated";
import type { LayoutDefinition } from "../types";
vi.mock("./templateAnalysisClient", () => ({ analysisEvents: vi.fn(), templateAnalysisClient: { create: vi.fn(), get: vi.fn(), cancel: vi.fn(), merge: vi.fn(), revert: vi.fn() } }));
const owner = { sessionId: "owner", token: "token" };
let draft: ManualDraft, run: AnalysisResult, result: MergeResult;
let pause = vi.fn(), resume = vi.fn(), apply = vi.fn();
let store: TemplateAnalysisStore, workbench: WorkbenchStore;
const newStore = () => new TemplateAnalysisStore(workbench, { read: () => structuredClone(draft), apply, pause, resume });
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear();
  draft = { layout: { canonicalWidth: 400, canonicalHeight: 300, detector: "test", ocrAvailable: false, warnings: [], allowCrossAxisLabels: false,
    cells: [{ id: "c1", x: 1, y: 1, width: 30, height: 20, confidence: 1, role: "ignored" }], relations: [], labelPool: [], recordSections: [], recordGroups: [] }, documentIdentityFields: [], documentName: null };
  run = { contractVersion: "template-analysis.v3", analysisId: "run", requestId: "", resource: { kind: "template", id: "version" }, baseRevision: 1,
    snapshotDigest: "snapshot", draftDigest: "draft", scopeDigest: "scope", state: "ready", pages: [], plannedCalls: 1, reservedCalls: 1, completedCalls: 1, expiresAt: "future", lastSequence: 3, error: null,
    proposal: { proposalDigest: "proposal", coverage: {}, focus: null, unresolved: [], units: [{ unitId: "u1", pageId: null, kind: "role", cellIds: ["c1"], name: "分類", state: "mergeable", reason: null, dependencies: [] }] } };
  result = { contractVersion: "template-analysis.v3", analysisId: "run", mergeId: "merge", baseRevision: 1, inputDraftDigest: "in", outputDraftDigest: "out", draft: structuredClone(draft), appliedUnitIds: ["u1"], conflicts: [], unresolved: [], focus: null, substitutions: {} };
  result.draft.layout.cells![0].role = "label";
  pause = vi.fn(); resume = vi.fn(); apply = vi.fn().mockImplementation((input, merged) => { if (JSON.stringify(input) !== JSON.stringify(draft)) return false; draft = structuredClone(merged); return true; });
  const context = { scopeContext: { resourceRevision: 1, contextRevision: 2, interactionEpoch: 1, catalogRevision: "catalog", scopeDigest: "scope", selectedTableId: null }, uiBindings: [] };
  workbench = { resourceId: "version", getOwner: () => owner, getContext: () => context, analysisContext: vi.fn().mockResolvedValue(context), refreshContext: vi.fn().mockResolvedValue(context) } as unknown as WorkbenchStore;
  store = newStore();
  vi.mocked(client.create).mockImplementation(async (_o, body) => { run.requestId = body.requestId; return structuredClone(run); });
  vi.mocked(client.get).mockImplementation(async () => structuredClone(run));
  vi.mocked(client.merge).mockResolvedValue(result);
  run.proposal!.units[0].regionId = "r1";
  run.proposal!.units[0].proposalDigest = "d".repeat(64);
  vi.mocked(client.revert).mockResolvedValue(run);
  vi.mocked(analysisEvents).mockImplementation(() => new Promise(() => undefined));
});
const start = () => store.start({ mode: "all", pageId: null }, "full", false);
it("returns to manual editing after a pre-admission configuration refusal", async () => {
  vi.mocked(client.create).mockRejectedValue(new EditingError("AI解析の接続設定がありません。", "PROVIDER_NOT_CONFIGURED", 503));
  const before = structuredClone(draft);
  await start();
  expect(store.getSnapshot()).toMatchObject({ recovery: false, held: true });
  store.finish();
  expect(store.getSnapshot().held).toBe(false); expect(resume).toHaveBeenCalledOnce();
  expect(draft).toEqual(before); expect(apply).not.toHaveBeenCalled();
});
it("retains recovery and the manual draft after an ambiguous server failure", async () => {
  vi.mocked(client.create).mockRejectedValue(new EditingError("処理時間が上限に達しました。", "PROVIDER_TIMEOUT", 504));
  const before = structuredClone(draft);
  await start(); store.finish();
  expect(store.getSnapshot()).toMatchObject({ recovery: true, held: true });
  expect(resume).not.toHaveBeenCalled(); expect(draft).toEqual(before);
});
afterEach(() => vi.restoreAllMocks());
it("retires a v2 analysis journal while retaining the independently held manual draft", () => {
  draft.layout.cells![0].fieldLabel = "保持する手入力";
  const original = structuredClone(draft);
  sessionStorage.setItem("template-analysis-v2:version", JSON.stringify({ create: { contractVersion: "template-analysis.v2" }, held: true, analysisId: "old", mergeIds: ["old-receipt"], touches: [], serial: 0 }));
  const restored = newStore();
  expect(restored.getSnapshot()).toMatchObject({ held: true, recovery: false, applied: [] });
  expect(restored.getSnapshot().message).toContain("解析の定義が変わりました");
  expect(restored.captureSave(draft).analysisMergeIds).toEqual([]);
  expect(draft).toEqual(original);
  expect(client.get).not.toHaveBeenCalled();
});
it("copies server-issued unit digests without requiring a client-side draft hash", async () => {
  await start(); await store.merge(["u1"]);
  const body = vi.mocked(client.merge).mock.calls[0][2];
  expect(body.unitDigests).toEqual({ u1: "d".repeat(64) });
  expect(body.parentMergeId).toBeNull();
  expect(body).not.toHaveProperty("inputDraftDigest");
});
it("preserves hand input and removes the reverted receipt when typing races with undo", async () => {
  await start(); await store.merge(["u1"]);
  let resolve!: (value: AnalysisResult) => void;
  vi.mocked(client.revert).mockImplementation(() => new Promise(done => { resolve = done; }));
  const undoing = store.undoDraft();
  await vi.waitFor(() => expect(client.revert).toHaveBeenCalledOnce());
  draft.layout.cells![0].fieldLabel = "取り消し待ちの手入力";
  store.touch([facetTouch("c1", "meaning")]);
  resolve(run); await undoing;
  expect(draft.layout.cells![0].fieldLabel).toBe("取り消し待ちの手入力");
  expect(store.captureSave(draft).analysisMergeIds).toEqual([]);
  expect(store.getSnapshot().staleUnits).toEqual(["u1"]);
});
it("rejects a late merge after the editing scope changes", async () => {
  await start(); let resolve!: (r: MergeResult) => void;
  vi.mocked(client.merge).mockImplementation(() => new Promise(done => { resolve = done; }));
  const merging = store.merge(["u1"]); await vi.waitFor(() => expect(client.merge).toHaveBeenCalledOnce());
  workbench.getContext().scopeContext.interactionEpoch += 1;
  resolve(result); await merging;
  expect(draft.layout.cells![0].role).toBe("ignored"); expect(store.getSnapshot().applied).toEqual([]);
});
it("keeps a staged draft and its provenance when persisting the applied state fails", async () => {
  await start();
  const originalSet = Storage.prototype.setItem;
  apply.mockImplementation((input, merged) => {
    expect(input).toEqual(draft); draft = structuredClone(merged);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
    return true;
  });
  await store.merge(["u1"]);
  expect(store.getSnapshot()).toMatchObject({ storageBlocked: true, held: true, applied: ["u1"] });
  expect(draft.layout.cells![0].role).toBe("label"); expect(resume).not.toHaveBeenCalled();
  vi.mocked(Storage.prototype.setItem).mockImplementation(originalSet); await store.recover();
  expect(store.captureSave(draft).analysisMergeIds).toEqual(["merge"]); expect(client.merge).toHaveBeenCalledOnce();
});
it("does not report a received business receipt as failed when local storage fills", async () => {
  await start(); await store.merge(["u1"]); const captured = store.captureSave(draft);
  const fault = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
  expect(() => store.saved(captured)).not.toThrow();
  expect(store.getSnapshot()).toMatchObject({ storageBlocked: true, held: true, applied: [], run: null });
  expect(resume).not.toHaveBeenCalled(); fault.mockRestore(); await store.recover();
  expect(store.captureSave(draft).analysisMergeIds).toEqual([]); expect(resume).toHaveBeenCalledOnce();
});
it("keeps a same-value hand instruction made while merge is in flight", async () => {
  await start(); let resolve!: (r: MergeResult) => void;
  vi.mocked(client.merge).mockImplementation(() => new Promise(done => { resolve = done; }));
  const merging = store.merge(["u1"]); await vi.waitFor(() => expect(client.merge).toHaveBeenCalledOnce());
  store.touch([facetTouch("c1", "role")]); resolve(result); await merging;
  expect(draft.layout.cells![0].role).toBe("ignored");
  expect(store.getSnapshot().applied).toEqual([]);
});
it("does not undo a later explicit hand confirmation of the same AI value", async () => {
  await start(); await store.merge(["u1"]);
  store.touch([facetTouch("c1", "role")]); await store.undoDraft();
  expect(draft.layout.cells![0].role).toBe("label");
});
it("accepts a server-coalesced run while keeping the exact original request for retry", async () => {
  vi.mocked(client.create).mockImplementation(async () => ({ ...run, requestId: "other-request", state: "running" }));
  await start(); expect(store.getSnapshot().run?.analysisId).toBe("run");
  await store.recover(); expect(client.create).toHaveBeenCalledOnce(); expect(client.get).toHaveBeenCalledOnce();
});
it("holds editing without an unhandled rejection when recovery storage is full", async () => {
  const fault = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
  await expect(start()).resolves.toBeUndefined();
  expect(client.create).not.toHaveBeenCalled(); expect(store.getSnapshot().held).toBe(true);
  expect(store.getSnapshot().busy).toBe(false); expect(resume).not.toHaveBeenCalled();
  fault.mockRestore(); await store.recover();
  expect(client.create).not.toHaveBeenCalled(); expect(store.getSnapshot().held).toBe(false);
});
it("stages an independent selection and carries merge provenance only to explicit save", async () => {
  await start(); await store.merge(["u1"]);
  expect(pause).toHaveBeenCalled(); expect(resume).not.toHaveBeenCalled();
  expect(draft.layout.cells![0].role).toBe("label");
  expect(store.captureSave(draft).analysisMergeIds).toEqual(["merge"]);
  expect(store.getSnapshot().held).toBe(true);
  store.saved(); expect(resume).toHaveBeenCalledOnce();
  expect(store.getSnapshot().applied).toEqual([]);
});
it("reuses the exact create payload after a lost reply, including across reload", async () => {
  vi.mocked(client.create).mockRejectedValueOnce(new TypeError("lost reply"));
  await start(); const body = vi.mocked(client.create).mock.calls[0][1];
  expect(store.getSnapshot().recovery).toBe(true);
  store = newStore();
  await store.recover(); expect(vi.mocked(client.create).mock.calls[1][1]).toEqual(body);
  await store.recover(); expect(client.create).toHaveBeenCalledTimes(2); expect(client.get).toHaveBeenCalledOnce();
});
it("restores staged provenance and same-value touches after reload without creating another run", async () => {
  await start(); await store.merge(["u1"]); store.touch([facetTouch("c1", "role")]);
  store = newStore(); expect(store.getSnapshot().applied).toEqual(["u1"]);
  await store.recover();
  expect(store.captureSave(draft)).toMatchObject({ analysisMergeIds: ["merge"], manualTouchedFacets: [facetTouch("c1", "role")] });
  expect(client.create).toHaveBeenCalledOnce(); expect(client.merge).toHaveBeenCalledOnce();
});
it("undoes an unsaved AI merge and refuses to undo over later hand edits", async () => {
  await start(); await store.merge(["u1"]); await store.undoDraft();
  expect(draft.layout.cells![0].role).toBe("ignored"); expect(store.captureSave(draft).analysisMergeIds).toEqual([]);
  await store.merge(["u1"]); draft.layout.cells![0].fieldLabel = "保護する入力";
  await store.undoDraft(); expect(draft.layout.cells![0].fieldLabel).toBe("保護する入力");
  expect(store.captureSave(draft).analysisMergeIds).toEqual(["merge"]);
});
it("retains a lost merge reply and retries its same request without another analysis", async () => {
  await start(); vi.mocked(client.merge).mockRejectedValueOnce(new TypeError("lost merge"));
  await store.merge(["u1"]); const body = vi.mocked(client.merge).mock.calls[0][2];
  await store.recover(); expect(vi.mocked(client.merge).mock.calls[1][2]).toEqual(body);
  expect(client.create).toHaveBeenCalledOnce(); expect(draft.layout.cells![0].role).toBe("label");
});
it("does not replace hand input made while a merge is in flight", async () => {
  await start(); let resolve!: (r: MergeResult) => void;
  vi.mocked(client.merge).mockImplementation(() => new Promise(done => { resolve = done; }));
  const merging = store.merge(["u1"]); await vi.waitFor(() => expect(client.merge).toHaveBeenCalledOnce());
  draft.layout.cells![0].fieldLabel = "手入力"; store.touch([facetTouch("c1", "meaning")]); resolve(result); await merging;
  expect(draft.layout.cells![0].fieldLabel).toBe("手入力"); expect(draft.layout.cells![0].role).toBe("ignored");
  expect(store.captureSave(draft).analysisMergeIds).toEqual([]);
  expect(store.getSnapshot().message).toContain("入力が変わりました");
});
it("tracks geometry independently from explicit same-value classification", () => {
  const before = structuredClone(draft.layout) as LayoutDefinition;
  expect(changedFacets(before, { ...before, cells: before.cells.map(c => ({ ...c, x: 50 })) })).toEqual([facetTouch("c1", "geometry")]);
  const touches = patchedCellFacets("c1", { role: "ignored" }); store.touch(touches);
  expect(store.captureSave(draft).manualTouchedFacets).toEqual([facetTouch("c1", "role")]);
});
it("retains deletion touches through save without inventing empty meanings for new frames", () => {
  const before = structuredClone(draft.layout) as LayoutDefinition;
  const added = { ...before, cells: [...before.cells, { ...before.cells[0], id: "new-frame" }] };
  expect(changedFacets(before, added)).toEqual([
    facetTouch("@layout", "geometry"), facetTouch("new-frame", "geometry"), facetTouch("new-frame", "role"),
  ]);
  draft.layout.cells = [];
  store.touch(changedFacets(before, draft.layout as LayoutDefinition));
  expect(store.captureSave(draft).manualTouchedFacets).toEqual([facetTouch("c1", "geometry"), facetTouch("@layout", "geometry")]);
});
it("undoes physical and semantic changes together and invalidates the prior geometry generation", async () => {
  const original = structuredClone(draft);
  result.draft.layout.cells![0].x = 50;
  await start(); await store.merge(["u1"]); await store.undoDraft();
  expect(draft).toEqual(original);
  const captured = store.captureSave(draft);
  expect(captured.analysisMergeIds).toEqual([]);
  expect(captured.manualTouchedFacets).toEqual([facetTouch("@layout", "geometry")]);
  store = newStore(); await store.recover(); await store.merge(["u1"]);
  expect(client.merge).toHaveBeenCalledOnce();
  expect(store.getSnapshot().staleUnits).toEqual(["u1"]);
  expect(store.captureSave(draft).manualTouchedFacets).toEqual([facetTouch("@layout", "geometry")]);
});
it("previews a new frame before it exists without mutating the manual draft", async () => {
  run.proposal!.units[0] = { ...run.proposal!.units[0], kind: "structure", cellIds: ["new"], geometry: {
    before: [], after: [{ id: "new", x: 80, y: 90, width: 100, height: 20 }], addedIds: ["new"], changedIds: [], removedIds: [], referenceMap: { n1: "new" },
  } };
  const original = structuredClone(draft);
  await start(); store.preview(["u1"]);
  expect(draft).toEqual(original); expect(apply).not.toHaveBeenCalled();
  expect(store.getSnapshot().focus?.regions).toMatchObject([{ cellId: "new", kind: "added", x: 80 }]);
  store.preview([]); expect(store.getSnapshot().focus).toBeNull();
});
it("keeps an old geometry proposal stale after a same-value instruction and late refresh", async () => {
  await start(); store.preview(["u1"]);
  store.touch([facetTouch("c1", "geometry")]); await store.refresh(); store.preview(["u1"]); await store.merge(["u1"]);
  expect(store.getSnapshot()).toMatchObject({ staleUnits: ["u1"], focus: null });
  expect(client.merge).not.toHaveBeenCalled();
  await start(); expect(store.getSnapshot().staleUnits).toEqual([]);
});
it("protects captured hand geometry without treating it as a later conflicting edit at merge", async () => {
  store.touch([facetTouch("@layout", "geometry")]); await start();
  expect(vi.mocked(client.create).mock.calls[0][1].manualTouchedFacets).toEqual([facetTouch("@layout", "geometry")]);
  store.touch([facetTouch("c1", "meaning")]); await store.merge(["u1"]);
  expect(vi.mocked(client.merge).mock.calls[0][2].manualTouchedFacets).toEqual([facetTouch("c1", "meaning")]);
  expect(store.captureSave(draft).manualTouchedFacets).toEqual([facetTouch("@layout", "geometry"), facetTouch("c1", "meaning")]);
});
it("retains touches made during save and maps new IDs using the receipt", () => {
  store.touch([facetTouch("c1", "role")]); const captured = store.captureSave(draft);
  store.touch([facetTouch("c1", "meaning")]); store.saved(captured, { c1: "canonical" });
  draft.layout.cells![0].id = "canonical";
  expect(store.captureSave(draft).manualTouchedFacets).toEqual([facetTouch("canonical", "meaning")]);
});
it("keeps a rejected proposal and hand draft available without automatic retry", async () => {
  await start(); vi.mocked(client.merge).mockRejectedValueOnce(new EditingError("手修正と競合", "MANUAL_CONFLICT", 409));
  await store.merge(["u1"]);
  expect(store.getSnapshot().run?.analysisId).toBe("run"); expect(store.getSnapshot().recovery).toBe(false);
  expect(draft.layout.cells![0].role).toBe("ignored"); expect(client.create).toHaveBeenCalledOnce();
});
it("ends cancellation without offering a nonexistent proposal or changing the draft", async () => {
  run.state = "running"; run.proposal = null;
  await start();
  vi.mocked(client.cancel).mockResolvedValue({ ...run, state: "cancelled" });
  await store.cancel();
  expect(store.getSnapshot()).toMatchObject({ held: true, run: { state: "cancelled" }, message: "解析を中止しました。下書きは保持しています。" });
  expect(apply).not.toHaveBeenCalled(); expect(client.create).toHaveBeenCalledOnce();
  store.finish(); expect(resume).toHaveBeenCalledOnce();
});
