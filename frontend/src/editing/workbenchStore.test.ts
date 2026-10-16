import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkbenchStore } from "./workbenchStore";
import { readEvents, v2client } from "./v2client";
import { api } from "../api";
import type { CommandResult, ContextResult, Event, Receipt, SessionResult } from "./v2.generated";
import type { OcrDocument, OcrResult } from "../types";
vi.mock("./v2client", async original => ({ ...await original<object>(), readEvents: vi.fn(), v2client: { create: vi.fn(), context: vi.fn(), update: vi.fn(), ocr: vi.fn(), query: vi.fn() } }));
vi.mock("../api", () => ({ api: { result: vi.fn() } }));
let context: ContextResult, receipt: Receipt, result: CommandResult, document: OcrDocument;
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear();
  context = { contractVersion: "editing.v2", scopeContext: { contractVersion: "editing.v2", kind: "scope_snapshot", resource: { kind: "ocr", id: "job" }, resourceRevision: 1, contextRevision: 1, interactionEpoch: 0, mode: "manual", pageScope: { mode: "all", pageId: null }, selectedTableId: null, selectedHandles: [], localTargetHandle: null, recentTableId: null, catalogRevision: "c", scopeDigest: "d", currentFocus: null }, editable: true, capabilities: [], catalog: { complete: true, targets: [] }, draftState: { state: "complete", reasons: [] }, limits: { operations: 50, queuedCommands: 2, instructionCharacters: 20000, utteranceSeconds: 180 }, lastReceipt: null, uiBindings: [] };
  receipt = { receiptId: "receipt", operationId: "op", beforeRevision: 1, afterRevision: 2, viewRevision: 2, notification: "変更しました。", canUndo: true, substitutions: {}, focus: { viewRevision: 2, contextRevision: 2, interactionEpoch: 1, anchors: [], primaryAnchorId: null } };
  result = { contractVersion: "editing.v2", commandId: "cmd", instructionId: "ins", requestId: "request", state: "APPLIED", acceptedEvent: {} as CommandResult["acceptedEvent"], finalEvent: { payload: { type: "applied", receipt } } as CommandResult["acceptedEvent"] };
  document = { schemaVersion: "1", templateId: "template", templateVersion: "1", fieldLabels: {}, processingMetadata: {}, fields: { field: { raw: "00789", normalized: null, status: "uncertain", alternatives: ["00456"], reviewReason: "確認中", sourceRegionId: "region", boundingBox: null } }, reviewItems: [{ code: "review", path: "fields.field", message: "確認中", severity: "warning", sourceRegionId: "region" }] };
  vi.mocked(v2client.create).mockImplementation(async () => ({ ...context, sessionId: "owner", token: "secret" }) as SessionResult);
  vi.mocked(v2client.context).mockImplementation(async () => structuredClone(context));
  vi.mocked(v2client.update).mockImplementation(async (_owner, body) => { context.scopeContext = { ...context.scopeContext, ...body, kind: "scope_snapshot", contextRevision: 2 }; return structuredClone(context); });
  vi.mocked(api.result).mockImplementation(async () => ({ revision: context.scopeContext.resourceRevision, result: document }) as OcrResult);
  vi.mocked(v2client.query).mockResolvedValue(result);
});
afterEach(() => vi.useRealTimers());
function saved() { context.scopeContext.resourceRevision = 2; context.lastReceipt = receipt; }
it("retains a complete hand draft after a lost response and queries the original request", async () => {
  vi.mocked(v2client.ocr).mockImplementation(async (_owner, body) => { result.requestId = body.requestId; saved(); throw new TypeError("lost response"); });
  const store = new WorkbenchStore("job", "ocr");
  await expect(store.saveOcrDraft(document)).rejects.toThrow("lost response");
  expect(store.retainedOcrDraft()).toEqual({ fields: document.fields, reviewItems: document.reviewItems });
  const outcome = await store.recoverResource();
  expect(outcome.result.receiptId).toBe("receipt");
  expect(v2client.ocr).toHaveBeenCalledTimes(1);
  expect(v2client.query).toHaveBeenCalledWith({ sessionId: "owner", token: "secret" }, result.requestId);
});
it("keeps a durable receipt when preview fetching fails, without saving again", async () => {
  vi.mocked(v2client.ocr).mockImplementation(async (_owner, body) => { saved(); return { ...result, requestId: body.requestId }; });
  vi.mocked(api.result).mockRejectedValueOnce(new Error("preview unavailable"));
  const store = new WorkbenchStore("job", "ocr");
  await expect(store.saveOcrDraft(document)).rejects.toThrow("preview unavailable");
  expect(store.getSnapshot().state).toBe("saved_display_pending");
  expect(store.retainedOcrDraft()?.fields.field.alternatives).toEqual(["00456"]);
  await store.recoverResource(); expect(v2client.ocr).toHaveBeenCalledTimes(1);
  expect(store.getSnapshot().state).toBe("ready");
});
it("does not discard a rejected manual draft while the user inspects the latest state", async () => {
  vi.mocked(v2client.ocr).mockImplementation(async (_owner, body) => ({ ...result, requestId: body.requestId, state: "CONFLICT", finalEvent: { payload: { type: "conflict", error: { code: "REVISION_CONFLICT", message: "別の編集があります。" } } } } as CommandResult));
  const store = new WorkbenchStore("job", "ocr");
  await expect(store.saveOcrDraft(document)).rejects.toThrow("別の編集");
  expect(store.retainedOcrDraft()?.fields).toEqual(document.fields);
  expect(store.getSnapshot().state).toBe("conflict");
});
it("allows a new scope after a definitely rejected voice command without replaying it", async () => {
  const store = new WorkbenchStore("job", "ocr"); await store.initialize();
  store.acceptCommand({ ...result, state: "CONFLICT", finalEvent: { payload: { type: "conflict", error: { code: "TABLE_SCOPE_MISMATCH", message: "選択した表の外側は変更できません。" } } } } as CommandResult);
  await vi.waitFor(() => expect(store.getSnapshot().message).toBe("選択した表の外側は変更できません。"));
  expect(store.getSnapshot().state).toBe("ready");
  await store.changeScope(null);
  expect(store.getSnapshot().context?.scopeContext.selectedTableId).toBeNull();
  expect(v2client.ocr).not.toHaveBeenCalled();
});

const pause = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
function accepted(id: string): CommandResult {
  const event: Event = { contractVersion: "editing.v2", eventId: id + "-accepted", sessionId: "owner",
    commandId: id, instructionId: id, requestId: id, sequence: 1, emittedAt: new Date().toISOString(),
    payload: { type: "accepted", queuePosition: 0 } };
  return { contractVersion: "editing.v2", commandId: id, instructionId: id, requestId: id,
    state: "ACCEPTED", acceptedEvent: event, finalEvent: null };
}
it("consumes terminal SSE directly and overlaps coherent preview reads (controlled latency sample)", async () => {
  const samples: Array<{ milliseconds: number; queries: number }> = [];
  for (let index = 0; index < 5; index++) {
    let receive!: (event: Event) => void;
    vi.mocked(readEvents).mockImplementation(async (_owner, _cursor, signal, callback, connected?) => {
      receive = callback; connected?.();
      await new Promise<void>(resolve => signal.addEventListener("abort", () => resolve(), { once: true }));
    });
    vi.mocked(v2client.context).mockImplementation(async () => structuredClone(context));
    const store = new WorkbenchStore("sample" + index, "ocr"); await store.initialize();
    const detach = store.connectEvents(); await vi.waitFor(() => expect(receive).toBeTypeOf("function"));
    const command = accepted("sample" + index), finalEvent: Event = { ...command.acceptedEvent,
      eventId: command.commandId + "-done", sequence: 2, payload: { type: "applied", receipt } };
    let finished = false, displayed = false;
    const beforeQueries = vi.mocked(v2client.query).mock.calls.length;
    vi.mocked(v2client.query).mockImplementation(async () => finished ? { ...command, state: "APPLIED", finalEvent } : command);
    vi.mocked(v2client.context).mockImplementation(async () => { await pause(40); return structuredClone(context); });
    vi.mocked(api.result).mockImplementation(async () => { await pause(40); return { revision: 2, result: document } as OcrResult; });
    store.onSaved(() => { displayed = true; });
    const start = performance.now(); store.acceptCommand(command);
    await pause(20); saved(); finished = true; receive(finalEvent);
    await vi.waitFor(() => expect(displayed).toBe(true), { interval: 5 });
    samples.push({ milliseconds: Math.round(performance.now() - start), queries: vi.mocked(v2client.query).mock.calls.length - beforeQueries });
    detach(); await Promise.resolve();
    context.scopeContext.resourceRevision = 1; context.lastReceipt = null;
  }
  console.info("CONTROLLED_DELIVERY_TIMINGS " + JSON.stringify(samples));
  expect(samples.map(s => s.queries)).toEqual([0, 0, 0, 0, 0]);
});

it("serializes preview publication so a delayed old read cannot overwrite a newer save", async () => {
  const store = new WorkbenchStore("job", "ocr"); await store.initialize();
  const displayed: number[] = []; store.onSaved(value => { displayed.push(value.ocr!.revision); });
  saved();
  vi.mocked(api.result).mockImplementation(async () => {
    const revision = context.scopeContext.resourceRevision;
    if (revision === 2) await pause(80);
    return { revision, result: document } as OcrResult;
  });
  store.acceptCommand({ ...result, requestId: "first" });
  await vi.waitFor(() => expect(api.result).toHaveBeenCalledTimes(1), { interval: 1 });
  context.scopeContext.resourceRevision = 3;
  const newer = { ...receipt, beforeRevision: 2, afterRevision: 3, viewRevision: 3 };
  context.lastReceipt = newer;
  store.acceptCommand({ ...result, requestId: "second", commandId: "cmd2",
    finalEvent: { payload: { type: "applied", receipt: newer } } as Event });
  await vi.waitFor(() => expect(displayed).toHaveLength(2));
  expect(displayed).toEqual([2, 3]);
});

it("does not publish a preview from a different revision during parallel reads", async () => {
  vi.mocked(v2client.ocr).mockImplementation(async (_owner, body) => { saved(); return { ...result, requestId: body.requestId }; });
  vi.mocked(api.result).mockResolvedValue({ revision: 3, result: document } as OcrResult);
  const store = new WorkbenchStore("job", "ocr");
  await expect(store.saveOcrDraft(document)).rejects.toThrow("世代");
  expect(store.getSnapshot().state).toBe("saved_display_pending");
  expect(store.retainedOcrDraft()?.fields).toEqual(document.fields);
});

it("queries a completed command when an open event stream loses its terminal event, without resending", async () => {
  vi.useFakeTimers();
  let connected = false;
  vi.mocked(readEvents).mockImplementation(async (_owner, _cursor, signal, _receive, ready) => {
    ready?.(); connected = true;
    await new Promise<void>(resolve => signal.addEventListener("abort", () => resolve(), { once: true }));
  });
  const store = new WorkbenchStore("job", "ocr"); await store.initialize();
  const detach = store.connectEvents();
  await vi.waitFor(() => expect(connected).toBe(true));
  const command = accepted("lost-terminal");
  saved(); vi.mocked(v2client.query).mockResolvedValue({ ...command, state: "APPLIED", finalEvent: result.finalEvent });
  let displayed = false; store.onSaved(() => { displayed = true; });
  store.acceptCommand(command);
  expect(v2client.query).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1000);
  expect(displayed).toBe(true);
  expect(v2client.query).toHaveBeenCalledExactlyOnceWith({ sessionId: "owner", token: "secret" }, command.requestId);
  expect(v2client.ocr).not.toHaveBeenCalled();
  detach(); await Promise.resolve();
});

it("retains a terminal event across a late accepted response and recovers the preview without querying or saving again", async () => {
  let receive!: (event: Event) => void;
  vi.mocked(readEvents).mockImplementation(async (_owner, _cursor, signal, callback, ready) => {
    receive = callback; ready?.();
    await new Promise<void>(resolve => signal.addEventListener("abort", () => resolve(), { once: true }));
  });
  const store = new WorkbenchStore("job", "ocr"); await store.initialize();
  const detach = store.connectEvents(); await vi.waitFor(() => expect(receive).toBeTypeOf("function"));
  const command = accepted("late-response"); receive(command.acceptedEvent); saved();
  vi.mocked(api.result).mockRejectedValueOnce(new Error("preview unavailable"));
  receive({ ...command.acceptedEvent, eventId: "done", sequence: 2, payload: { type: "applied", receipt } });
  store.acceptCommand(command);
  await vi.waitFor(() => expect(store.getSnapshot().state).toBe("saved_display_pending"));
  // A reload must retain the authoritative terminal event as well as the original owner.
  const restored = new WorkbenchStore("job", "ocr"); await restored.initialize();
  const outcome = await restored.recoverResource();
  expect(outcome.result.receiptId).toBe(receipt.receiptId);
  expect(v2client.query).not.toHaveBeenCalled();
  expect(v2client.ocr).not.toHaveBeenCalled();
  detach(); await Promise.resolve();
});

it("keeps a failed display update blocked when a later queued command displays successfully", async () => {
  const store = new WorkbenchStore("job", "ocr"); await store.initialize(); saved();
  vi.mocked(api.result).mockRejectedValueOnce(new Error("first preview unavailable"));
  store.acceptCommand({ ...result, requestId: "first" });
  await vi.waitFor(() => expect(store.getSnapshot().state).toBe("saved_display_pending"));
  const newer = { ...receipt, beforeRevision: 2, afterRevision: 3, viewRevision: 3 };
  context.scopeContext.resourceRevision = 3; context.lastReceipt = newer;
  store.acceptCommand({ ...result, requestId: "second", commandId: "second",
    finalEvent: { payload: { type: "applied", receipt: newer } } as Event });
  await vi.waitFor(() => expect(store.getSnapshot().pending).toBe(1));
  expect(store.getSnapshot().state).toBe("saved_display_pending");
  await store.recoverResource();
  expect(store.getSnapshot().state).toBe("ready");
  expect(v2client.query).not.toHaveBeenCalled();
  expect(v2client.ocr).not.toHaveBeenCalled();
});

it("uses query recovery immediately after the last event subscriber disconnects", async () => {
  let connected = false;
  vi.mocked(readEvents).mockImplementation(async (_owner, _cursor, signal, _receive, ready) => {
    ready?.(); connected = true;
    await new Promise<void>(resolve => signal.addEventListener("abort", () => resolve(), { once: true }));
  });
  const store = new WorkbenchStore("job", "ocr"); await store.initialize();
  const detach = store.connectEvents(); await vi.waitFor(() => expect(connected).toBe(true));
  detach(); await Promise.resolve(); saved();
  store.acceptCommand(accepted("disconnected"));
  expect(v2client.query).toHaveBeenCalledExactlyOnceWith({ sessionId: "owner", token: "secret" }, "disconnected");
  await vi.waitFor(() => expect(store.getSnapshot().pending).toBe(0));
});
