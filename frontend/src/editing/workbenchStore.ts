import { privateSessionStorage } from "../auth/transport";
import { api } from "../api";
import type { OcrDocument, OcrResult } from "../types";
import type { TemplateAuthoringUpdate, TemplateAuthoringView } from "../assistant/generatedAuthoring";
import { liveSubstitutions, substituteOwnReferences } from "./reconcile";
import { EditingError, readEvents, v2client, type Owner } from "./v2client";
import { validFocus, type AiFocusState } from "./focusState";
import type { Binding, CommandRequest, CommandResult, ContextResult, ContextUpdate, Event, ManualAuthoring, ManualOcr, PageScope, Question, Receipt } from "./v2.generated";

export type LoadedManualView = { context: ContextResult; authoring: TemplateAuthoringView };
export type SavedResource = { result: Receipt; context: ContextResult; template: LoadedManualView | null; ocr: OcrResult | null };
type SaveState = "ready" | "saving" | "recovering" | "saved_display_pending" | "conflict" | "failed";
export type WorkbenchState = { state: SaveState; message: string; canUndo: boolean; context: ContextResult | null; focus: AiFocusState | null; question: Question | null; commandId: string | null; pending: number };
type Body = CommandRequest | ManualAuthoring | ManualOcr;
type Journal = { requestId: string; owner: Owner; origin: "manual" | "voice"; endpoint: "command" | "authoring" | "ocr"; body?: Body; result?: CommandResult; finalEvent?: Event; displayPending?: boolean };
const displayPendingMessage = "保存済み・表示更新待ちです。入力を保持しています。";
const clone = <T,>(value: T): T => structuredClone(value);
const delay = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
export const bindingOf = (context: ContextResult): Binding => {
  const s = context.scopeContext;
  return { expectedRevision: s.resourceRevision, contextRevision: s.contextRevision, interactionEpoch: s.interactionEpoch, catalogRevision: s.catalogRevision, scopeDigest: s.scopeDigest };
};

/** One resource owner and command queue; microphone, SSE and preview have separate lifetimes. */
export class WorkbenchStore {
  private owner: Owner | null = null;
  private cursor: string | null = null;
  private sequence = 0;
  private journals = new Map<string, Journal>();
  private completed = new Map<string, Promise<SavedResource | null>>();
  private listeners = new Set<() => void>();
  private savedListeners = new Set<(saved: SavedResource) => Promise<void> | void>();
  private interactionListeners = new Set<() => void>();
  private scopeListeners = new Set<(phase: "before" | "after") => void | Promise<void>>();
  private initialization: Promise<void> | null = null;
  private tail: Promise<unknown> = Promise.resolve();
  private previewTail: Promise<unknown> = Promise.resolve();
  private terminalWaiters = new Map<string, () => void>();
  private stream: AbortController | null = null;
  private streamSessionId: string | null = null;
  private streamUsers = 0;
  private focusTimer: ReturnType<typeof setTimeout> | null = null;
  private manualActive = false;
  private localEpoch = 0;
  private substitutions: Record<string, string> = {};
  private state: WorkbenchState = { state: "ready", message: "", canUndo: false, context: null, focus: null, question: null, commandId: null, pending: 0 };
  private readonly key: string;
  constructor(readonly resourceId: string, readonly resourceKind: "template" | "ocr") {
    this.key = `editing-v2:${resourceKind}:${resourceId}`;
    const raw = privateSessionStorage.getItem(this.key);
    if (raw) try {
      const saved = JSON.parse(raw) as { owner: Owner; cursor: string | null; sequence: number; journals: Journal[]; open?: { question: Question; commandId: string | null } };
      if (!saved.owner?.sessionId || !saved.owner.token) throw new Error();
      this.owner = saved.owner; this.cursor = saved.cursor; this.sequence = saved.sequence;
      // A template save the server refused for its content is not resent or waited on;
      // the editor keeps the unsaved input in its own draft.
      this.journals = new Map(saved.journals.filter(j => !refusedManualSave(j)).map(j => [j.requestId, j]));
      // An open question survives a reload; otherwise the server waits for an answer the page cannot show.
      if (saved.open && Date.parse(saved.open.question.expiresAt) > Date.now()) this.state = { ...this.state, question: saved.open.question, commandId: saved.open.commandId, message: saved.open.question.message };
      if (this.journals.size) this.state = { ...this.state, state: "recovering", message: "前の保存結果を確認してください。入力は保持しています。" };
    } catch { this.state = { ...this.state, state: "failed", message: "保存記録を読み取れません。入力は保持されています。" }; }
  }
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.state;
  private publish(patch: Partial<WorkbenchState>) { const asked = "question" in patch && patch.question !== this.state.question; this.state = { ...this.state, ...patch }; if (asked) this.remember(); this.listeners.forEach(fn => fn()); }
  private remember() { if (this.owner) privateSessionStorage.setItem(this.key, JSON.stringify({ owner: this.owner, cursor: this.cursor, sequence: this.sequence, journals: [...this.journals.values()], open: this.state.question ? { question: this.state.question, commandId: this.state.commandId } : undefined })); }
  private publishCompletion(patch: Partial<WorkbenchState>) {
    const displayPending = [...this.journals.values()].some(journal => journal.displayPending);
    this.publish({ state: "ready", pending: this.journals.size, ...patch,
      ...(displayPending ? { state: "saved_display_pending", message: displayPendingMessage } : {}) });
  }
  private setContext(context: ContextResult) { this.localEpoch = Math.max(this.localEpoch, context.scopeContext.interactionEpoch); this.publish({ context, canUndo: Boolean(context.lastReceipt?.canUndo) }); }
  getOwner(): Owner { if (!this.owner) throw new Error("編集状態を取得してください。"); return this.owner; }
  getContext(): ContextResult { if (!this.state.context) throw new Error("編集状態を取得してください。"); return this.state.context; }
  onSaved(fn: (saved: SavedResource) => Promise<void> | void) { this.savedListeners.add(fn); return () => { this.savedListeners.delete(fn); }; }
  /** Table focus changes keep voice input running: it is paused before and resumed after. */
  subscribeScope(fn: (phase: "before" | "after") => void | Promise<void>) { this.scopeListeners.add(fn); return () => { this.scopeListeners.delete(fn); }; }
  subscribeInteraction(fn: () => void) { this.interactionListeners.add(fn); return () => { this.interactionListeners.delete(fn); }; }
  setInteraction(active: boolean) {
    if (active === this.manualActive) return;
    this.manualActive = active;
    if (active) {
      this.localEpoch++; this.clearFocus(); this.interactionListeners.forEach(fn => fn());
      // Fence server planning as soon as a hand gesture starts, before its save.
      // Only the screen state is sent; a refusal re-reads it instead of halting later saves.
      if (this.state.context && this.state.context.scopeContext.mode !== "manual") void this.enqueue(() => this.updateContext({ mode: "manual", selectedHandles: [], localTargetHandle: null }, true))
        .catch(() => this.refreshContext().catch(error => this.failure(error)));
    }
  }
  private enqueue<T>(work: () => Promise<T>): Promise<T> { const result = this.tail.then(work); this.tail = result.catch(() => undefined); return result; }
  async initialize() {
    if (!this.initialization) this.initialization = this.initializeOnce().catch(error => { this.initialization = null; throw error; });
    await this.initialization;
  }
  private async initializeOnce() {
    if (this.owner) {
      try { this.setContext(await v2client.context(this.owner)); return; }
      catch (error) { if (!(error instanceof EditingError) || error.code !== "EDITING_SESSION_EXPIRED") throw error; }
    }
    const created = await v2client.create({ contractVersion: "editing.v2", resource: { kind: this.resourceKind, id: this.resourceId }, viewInstanceId: crypto.randomUUID(), mode: "manual", pageScope: { mode: "all", pageId: null } });
    this.owner = { sessionId: created.sessionId, token: created.token }; this.sequence = 0; this.cursor = null; this.setContext(created); this.remember();
  }
  private failure(error: unknown) { this.publish({ state: error instanceof EditingError && error.status === 409 ? "conflict" : "failed", message: error instanceof Error ? error.message : "編集処理を完了できません。" }); }
  private requireReady() { if (["recovering", "saved_display_pending", "conflict", "failed"].includes(this.state.state)) throw new EditingError("保存結果と保持した入力を確認してください。", "QUEUE_HALTED", 409); }
  async refreshContext() {
    try { const context = await v2client.context(this.getOwner()); this.setContext(context); return context; }
    catch (error) {
      if (!(error instanceof EditingError) || error.code !== "EDITING_SESSION_EXPIRED") throw error;
      // The receipt journal retains its original owner. A fresh read session cannot resend it.
      this.initialization = null; this.interactionListeners.forEach(fn => fn());
      await this.initialize(); return this.getContext();
    }
  }
  private contextBody(patch: Partial<ContextUpdate>, manual = false): ContextUpdate {
    const s = this.getContext().scopeContext;
    return { contractVersion: "editing.v2", kind: "context_update", resource: s.resource, mode: s.mode, pageScope: s.pageScope, selectedTableId: s.selectedTableId, selectedHandles: s.selectedHandles, localTargetHandle: s.localTargetHandle,
      expectedRevision: s.resourceRevision, expectedContextRevision: s.contextRevision, interactionEpoch: manual ? Math.max(this.localEpoch, s.interactionEpoch + 1) : s.interactionEpoch, ...patch };
  }
  private async updateContext(patch: Partial<ContextUpdate>, manual = false) {
    let updated;
    try { updated = await v2client.update(this.getOwner(), this.contextBody(patch, manual)); }
    catch (error) {
      if (!(error instanceof EditingError) || error.code !== "EDITING_SESSION_EXPIRED") throw error;
      // A screen left open past the session's idle limit (30 min) starts a new session and sends
      // the same screen state again. Only the screen state is sent here, never an edit, so this
      // cannot apply anything twice; the following save uses the new session's binding.
      this.initialization = null; this.interactionListeners.forEach(fn => fn());
      await this.initialize();
      updated = await v2client.update(this.getOwner(), this.contextBody(patch, manual));
    }
    this.clearFocus(); this.publish({ question: null, commandId: null }); this.setContext(updated); return updated;
  }
  handles(entities: string[]) { const kind = this.resourceKind === "ocr" ? "field" : "cell"; return this.getContext().uiBindings.filter(b => b.kind === kind && entities.includes(b.entityId)).map(b => b.handle); }
  async audioContext(mode: "continuous" | "local", pageScope: PageScope, entities: string[], table: string | null) {
    await this.initialize(); await this.tail; this.requireReady();
    if (this.manualActive) throw new Error("手動操作を終了してください。");
    await this.refreshContext(); const handles = this.handles(entities);
    if (mode === "local" && handles.length !== 1) throw new Error("局所録音では欄を一つ選択してください。");
    return this.updateContext({ mode, pageScope, selectedTableId: table, selectedHandles: mode === "local" || this.resourceKind === "template" ? handles : [], localTargetHandle: mode === "local" ? handles[0] : null });
  }
  async changeScope(table: string | null) {
    return this.enqueue(async () => {
      this.requireReady(); await Promise.all([...this.scopeListeners].map(fn => fn("before")));
      try { await this.changeScopeNow(table); } finally { this.scopeListeners.forEach(fn => void fn("after")); }
    });
  }
  private async changeScopeNow(table: string | null) {
    const question = this.state.question;
    if (question?.missingKind === "scope") {
      const requestId = crypto.randomUUID();
      const result = await v2client.scopeAnswer(this.getOwner(), { contractVersion: "editing.v2", requestId, replyTo: { questionId: question.questionId, version: question.version }, context: this.contextBody({ selectedTableId: table }) });
      await this.refreshContext(); this.acceptCommand(result); return;
    }
    await this.updateContext({ selectedTableId: table });
  }
  connectEvents() {
    this.streamUsers++; if (!this.stream) { this.stream = new AbortController(); void this.eventLoop(this.stream); }
    return () => { this.streamUsers--; queueMicrotask(() => { if (this.streamUsers === 0) { this.stream?.abort(); this.stream = null; this.streamSessionId = null; } }); };
  }
  private async eventLoop(controller: AbortController) {
    try { await this.initialize(); } catch (error) { if (!controller.signal.aborted) this.failure(error); return; }
    for (let attempt = 0; !controller.signal.aborted && attempt < 3; attempt++) {
      const owner = this.getOwner();
      try { await readEvents(owner, this.cursor, controller.signal, event => this.event(event), () => {
        if (!controller.signal.aborted && this.stream === controller) this.streamSessionId = owner.sessionId;
      }); }
      catch { if (controller.signal.aborted) return; }
      finally { if (this.stream === controller) this.streamSessionId = null; }
      if (!controller.signal.aborted) await delay(1000);
    }
    if (!controller.signal.aborted) this.publish({ message: "状態の接続が切れました。保存結果は再取得できます。" });
  }
  private event(event: Event) {
    if (event.sessionId !== this.owner?.sessionId || event.sequence <= this.sequence) return;
    this.sequence = event.sequence; this.cursor = event.eventId;
    const payload = event.payload;
    if (payload.type === "accepted" && !this.journals.has(event.requestId) && !this.completed.has(event.requestId)) {
      this.journals.set(event.requestId, { requestId: event.requestId, owner: this.getOwner(), origin: "voice", endpoint: "command" });
      this.publish({ pending: this.journals.size, commandId: event.commandId, message: "指示を整理しています。" });
    }
    if (payload.type === "target_focus") this.showFocus({ commandId: event.commandId, phase: payload.phase, focus: payload.focus, receivedAt: performance.now() });
    const journal = this.journals.get(event.requestId);
    if (!["accepted", "target_focus"].includes(payload.type) && journal) {
      journal.finalEvent = event;
      this.terminalWaiters.get(event.requestId)?.();
      void this.final(event.requestId).catch(error => this.failureUnlessSaved(error));
    }
    this.remember();
  }
  acceptCommand(result: CommandResult) {
    if (this.completed.has(result.requestId) && !this.journals.has(result.requestId)) return;
    if (!this.journals.has(result.requestId) && !this.completed.has(result.requestId)) this.journals.set(result.requestId, { requestId: result.requestId, owner: this.getOwner(), origin: "voice", endpoint: "command" });
    const journal = this.journals.get(result.requestId);
    if (journal) { journal.result = result; if (result.finalEvent) journal.finalEvent = result.finalEvent; }
    this.remember(); this.publish({ pending: this.journals.size, commandId: result.commandId, question: null, message: "指示を整理しています。" });
    void this.final(result.requestId).catch(error => this.failureUnlessSaved(error));
  }
  private failureUnlessSaved(error: unknown) {
    if (["saved_display_pending", "recovering"].includes(this.state.state)) return;
    // A refused change (not a stale version) leaves nothing waiting, so later saves continue.
    if (error instanceof EditingError && this.journals.size === 0 && (error.status === 422 || contentRefusal(error.code) || ["TABLE_SCOPE_MISMATCH", "LOCAL_SCOPE_MISMATCH"].includes(error.code))) this.publish({ state: "ready", message: error.message });
    else this.failure(error);
  }
  private final(id: string): Promise<SavedResource | null> {
    const existing = this.completed.get(id); if (existing) return existing;
    const work = this.finishCommand(id); this.completed.set(id, work);
    void work.catch(() => this.completed.delete(id));
    if (this.completed.size > 100) this.completed.delete(this.completed.keys().next().value!);
    return work;
  }
  private waitForTerminal(journal: Journal, milliseconds: number) {
    if (journal.finalEvent || journal.result?.finalEvent) return Promise.resolve();
    return new Promise<void>(resolve => {
      const done = () => { clearTimeout(timer); this.terminalWaiters.delete(journal.requestId); resolve(); };
      const timer = setTimeout(done, milliseconds);
      this.terminalWaiters.set(journal.requestId, done);
    });
  }
  private async finishCommand(id: string): Promise<SavedResource | null> {
    const journal = this.journals.get(id); if (!journal) return null;
    // Prefer the authenticated durable event; querying remains the recovery path.
    const deadline = performance.now() + 60_000;
    while (!journal.finalEvent && !journal.result?.finalEvent && performance.now() < deadline) {
      if (this.streamSessionId === journal.owner.sessionId) await this.waitForTerminal(journal, 1000);
      if (journal.finalEvent || journal.result?.finalEvent) break;
      journal.result = await v2client.query(journal.owner, id);
      if (!journal.result.finalEvent) await this.waitForTerminal(journal, 250);
    }
    const finalEvent = journal.finalEvent ?? journal.result?.finalEvent;
    if (!finalEvent) { this.publish({ state: "recovering", message: "保存結果を確認してください。指示は再送しません。" }); throw new Error("編集処理の結果を確認できません。"); }
    journal.finalEvent = finalEvent; this.remember();
    const commandId = finalEvent.commandId ?? journal.result?.commandId ?? id;
    const payload = finalEvent.payload;
    if (payload.type === "applied") {
      // Only publication is serialized: later command completion can arrive while reads run.
      const previous = this.previewTail;
      let release!: () => void;
      this.previewTail = new Promise<void>(resolve => { release = resolve; });
      await previous;
      try {
        const saved = await this.loadSaved(payload.receipt, journal.origin === "voice");
        if (journal.origin === "voice") for (const fn of this.savedListeners) await fn(saved);
        Object.assign(this.substitutions, payload.receipt.substitutions ?? {});
        this.journals.delete(id); this.remember();
        this.publishCompletion({ message: payload.receipt.notification, question: null });
        if (journal.origin === "voice") this.showFocus({ commandId, phase: "applied", focus: payload.receipt.focus, receivedAt: performance.now() });
        return saved;
      } catch (error) { journal.displayPending = true; this.remember(); this.publish({ state: "saved_display_pending", message: displayPendingMessage }); throw error; }
      finally { release(); }
    }
    if (!(journal.origin === "manual" && (payload.type === "failed" || payload.type === "conflict")) || refusedManualSave(journal)) this.journals.delete(id);
    this.remember();
    this.publish({ pending: this.journals.size, commandId });
    if (payload.type === "clarification_required") this.publishCompletion({ question: payload.question, message: payload.question.message });
    else if (payload.type === "failed" || payload.type === "conflict") { this.clearFocus(commandId); throw new EditingError(payload.error.message, payload.error.code, payload.type === "conflict" ? 409 : 422); }
    else if (payload.type === "no_action" || payload.type === "cancelled" || payload.type === "expired") { this.clearFocus(commandId); this.publishCompletion({ question: null, message: payload.message }); }
    return null;
  }
  private async loadSaved(receipt: Receipt, allowLaterOwnSave = false): Promise<SavedResource> {
    const [context, preview] = await Promise.all([this.refreshContext(),
      this.resourceKind === "template" ? api.authoring(this.resourceId) : api.result(this.resourceId)]);
    const revision = context.scopeContext.resourceRevision;
    if (revision !== receipt.afterRevision && !(allowLaterOwnSave && revision > receipt.afterRevision && context.lastReceipt?.afterRevision === revision)) throw new EditingError("保存後に別の編集が入りました。最新表示を確認してください。", "REVISION_CONFLICT", 409);
    if (this.resourceKind === "template") {
      const authoring = preview as TemplateAuthoringView;
      if (authoring.revision !== revision) throw new Error("保存した内容をまだ読み込めません。少し待ってから画面を再読み込みしてください。");
      return { result: receipt, context, template: { context, authoring }, ocr: null };
    }
    const ocr = preview as OcrResult;
    if (ocr.revision !== revision) throw new Error("保存した内容をまだ読み込めません。少し待ってから画面を再読み込みしてください。");
    return { result: receipt, context, ocr, template: null };
  }
  private unchangedReceipt(receiptId: string, context: ContextResult, notification: string): Receipt {
    const s = context.scopeContext, revision = s.resourceRevision;
    return { receiptId, operationId: null, beforeRevision: revision, afterRevision: revision, viewRevision: revision, notification, canUndo: Boolean(context.lastReceipt?.canUndo), substitutions: {},
      focus: { viewRevision: revision, contextRevision: s.contextRevision, interactionEpoch: s.interactionEpoch, anchors: [], primaryAnchorId: null } };
  }
  private async sendManual(body: Body, endpoint: Journal["endpoint"]): Promise<SavedResource> {
    const journal: Journal = { requestId: body.requestId, owner: this.getOwner(), origin: "manual", body: clone(body), endpoint };
    this.journals.set(body.requestId, journal); this.remember(); this.publish({ state: "saving", message: "変更を保存しています。" });
    try {
      const result = endpoint === "authoring" ? await v2client.authoring(journal.owner, body as ManualAuthoring) : endpoint === "ocr" ? await v2client.ocr(journal.owner, body as ManualOcr) : await v2client.command(journal.owner, body as CommandRequest);
      journal.result = result; this.remember();
      const saved = await this.final(body.requestId);
      if (saved) return saved;
      if ((journal.finalEvent ?? journal.result?.finalEvent)?.payload.type === "no_action") {
        return this.loadSaved(this.unchangedReceipt(body.requestId, await this.refreshContext(), "保存内容に変更はありません。"));
      }
      throw new Error("手動編集を完了できません。");
    } catch (error) {
      // A refused template save does not stop later saves; only a stale version or an
      // unknown outcome holds the queue until the saved state is checked.
      // A refused template save or undo changed nothing: the editor keeps the unsaved input and
      // saves it again on the next edit, so later saves continue on the current version.
      if (error instanceof EditingError && error.status < 500 && droppableManual(journal)) {
        this.journals.delete(body.requestId); this.remember();
        if (!contentRefusal(error.code)) await this.refreshContext().catch(() => undefined);
        this.publish({ state: "ready", pending: this.journals.size, message: error.message });
      } else if (this.state.state !== "saved_display_pending") this.publish({ state: error instanceof EditingError && error.status < 500 ? "conflict" : "recovering", message: error instanceof Error ? error.message : "保存結果を確認してください。" });
      throw error;
    }
  }
  async analysisContext(pageScope: PageScope) {
    return this.enqueue(async () => {
      await this.initialize(); this.requireReady();
      this.interactionListeners.forEach(fn => fn());
      await this.refreshContext();
      return this.updateContext({ mode: "manual", pageScope }, true);
    });
  }
  async save(draft: TemplateAuthoringUpdate, scope: PageScope, provenance: Pick<ManualAuthoring, "analysisMergeIds" | "manualTouchedFacets"> = {}) {
    const captured = clone(draft);
    const capturedProvenance = clone(provenance);
    return this.enqueue(async () => { await this.initialize(); this.requireReady(); await this.updateContext({ mode: "manual", pageScope: scope, selectedTableId: null, selectedHandles: [], localTargetHandle: null }, true);
      const substitutions = liveSubstitutions(captured, this.substitutions);
      const saved = await this.sendManual({ contractVersion: "editing.v2", requestId: crypto.randomUUID(), binding: bindingOf(this.getContext()), afterCommandId: null, draft: substituteOwnReferences(captured, substitutions), ...substituteOwnReferences(capturedProvenance, substitutions) }, "authoring");
      return { view: saved.template!, result: saved.result };
    });
  }
  async saveOcrDraft(draft: OcrDocument): Promise<SavedResource> {
    const captured = clone({ fields: draft.fields, reviewItems: draft.reviewItems });
    return this.enqueue(async () => { await this.initialize(); this.requireReady(); await this.updateContext({ mode: "manual", selectedTableId: null, selectedHandles: [], localTargetHandle: null }, true);
      return this.sendManual({ contractVersion: "editing.v2", kind: "manual_ocr", requestId: crypto.randomUUID(), binding: bindingOf(this.getContext()), afterCommandId: null, draft: captured }, "ocr"); });
  }
  async instruction(text: string | { choiceId: string }) {
    await this.initialize(); this.requireReady(); if (this.manualActive) throw new Error("手動操作を終了してください。");
    const question = this.state.question, requestId = crypto.randomUUID(), owner = this.getOwner();
    this.journals.set(requestId, { requestId, owner, origin: "voice", endpoint: "command" }); this.remember();
    try {
      const result = await v2client.command(owner, { contractVersion: "editing.v2", requestId, binding: bindingOf(this.getContext()), afterCommandId: null, command: { kind: "instruction", input: typeof text === "string" ? { kind: "text", text } : { kind: "choice", choiceId: text.choiceId }, replyTo: question ? { questionId: question.questionId, version: question.version } : null } });
      this.acceptCommand(result);
    } catch (error) {
      if (error instanceof EditingError && error.status < 500) { this.journals.delete(requestId); this.remember(); }
      else this.publish({ state: "recovering", message: "受付結果を確認してください。指示は再送しません。" });
      throw error;
    }
  }
  async prepareInstruction(pageScope: PageScope, entities: string[]) {
    await this.initialize(); await this.tail; this.requireReady(); await this.refreshContext();
    const s = this.getContext().scopeContext, handles = this.resourceKind === "template" ? this.handles(entities) : [];
    if (s.mode !== "continuous" || JSON.stringify(s.pageScope) !== JSON.stringify(pageScope) || JSON.stringify(s.selectedHandles) !== JSON.stringify(handles))
      await this.updateContext({ mode: "continuous", pageScope, selectedHandles: handles, localTargetHandle: null });
  }
  async selectionChanged(pageScope: PageScope, entities: string[]) {
    this.clearFocus(); this.interactionListeners.forEach(fn => fn());
    return this.enqueue(async () => {
      await this.initialize(); this.requireReady(); await this.refreshContext();
      return this.updateContext({ mode: "manual", pageScope, selectedHandles: this.handles(entities), localTargetHandle: null }, true);
    });
  }
  async cancelPending() { const id = this.state.commandId; if (!id) return;
    await v2client.command(this.getOwner(), { contractVersion: "editing.v2", requestId: crypto.randomUUID(), binding: bindingOf(this.getContext()), afterCommandId: null, command: { kind: "cancel", commandId: id } });
    this.publish({ question: null, message: "未保存の指示を中止しました。" }); this.clearFocus(id);
  }
  async undoResource() { return this.enqueue(async () => { this.requireReady(); const receipt = this.getContext().lastReceipt, operationId = receipt?.operationId; if (!operationId || !receipt.canUndo) throw new Error("取り消せる変更がありません。");
    this.interactionListeners.forEach(fn => fn()); await this.updateContext({ mode: "manual", selectedHandles: [], localTargetHandle: null }, true);
    return this.sendManual({ contractVersion: "editing.v2", requestId: crypto.randomUUID(), binding: bindingOf(this.getContext()), afterCommandId: null, command: { kind: "undo", operationId } }, "command"); }); }
  async undo() { return (await this.undoResource()).template!; }
  async recoverResource(): Promise<SavedResource> {
    await this.tail; const journal = [...this.journals.values()][0];
    if (!journal) {
      // Nothing is waiting: the halt came from reading or sending the screen state. Re-read it.
      const context = await this.refreshContext();
      const saved = await this.loadSaved(this.unchangedReceipt(crypto.randomUUID(), context, "最新の保存内容を取得しました。"));
      this.publish({ state: "ready", pending: 0, message: saved.result.notification }); return saved;
    }
    if (!journal.finalEvent && !journal.result?.finalEvent) {
      try { journal.result = await v2client.query(journal.owner, journal.requestId); }
      catch (error) {
        if (!(error instanceof EditingError) || error.code !== "COMMAND_NOT_FOUND" || !journal.body) throw error;
        const b = journal.body; journal.result = journal.endpoint === "authoring" ? await v2client.authoring(journal.owner, b as ManualAuthoring) : journal.endpoint === "ocr" ? await v2client.ocr(journal.owner, b as ManualOcr) : await v2client.command(journal.owner, b as CommandRequest);
      }
    }
    if (refusedManualSave(journal)) {
      const payload = (journal.finalEvent ?? journal.result?.finalEvent)!.payload as { error: { message: string } };
      this.journals.delete(journal.requestId); this.remember();
      await this.refreshContext().catch(() => undefined);
      this.publish({ state: "ready", pending: this.journals.size, message: payload.error.message });
      throw new EditingError(`${payload.error.message} この変更は保存されていません。編集を続けると保存し直します。`, "SAVE_REFUSED", 422);
    }
    this.completed.delete(journal.requestId); const result = await this.final(journal.requestId); if (!result) throw new Error("変更の保存は行われていません。保持した入力を確認してください。"); return result;
  }
  async recover() { const saved = await this.recoverResource(); return { view: saved.template!, result: saved.result }; }
  retainedDraft(): TemplateAuthoringUpdate | null { const j = [...this.journals.values()].find(v => v.endpoint === "authoring"); return j?.body && "draft" in j.body ? j.body.draft as TemplateAuthoringUpdate : null; }
  retainedOcrDraft(): Pick<OcrDocument, "fields" | "reviewItems"> | null { const j = [...this.journals.values()].find(v => v.endpoint === "ocr"); return j?.body && "draft" in j.body ? j.body.draft as Pick<OcrDocument, "fields" | "reviewItems"> : null; }
  async inspectLatest(): Promise<LoadedManualView> { const context = await this.refreshContext(), authoring = await api.authoring(this.resourceId); if (authoring.revision !== context.scopeContext.resourceRevision) throw new Error("表示取得中に別の編集が入りました。"); return { context, authoring }; }
  async resumeAfterReview(view: LoadedManualView) { if (["recovering", "saved_display_pending"].includes(this.state.state)) throw new Error("先に保存結果を確認してください。"); this.setContext(view.context); this.journals.clear(); this.remember(); this.publish({ state: "ready", message: "確認した保存内容から再開しました。", question: null }); }
  async resumeOcrAfterReview(reviewed: OcrResult) { const current = await api.result(this.resourceId); if (JSON.stringify(current) !== JSON.stringify(reviewed)) throw new Error("確認後に別の編集が入りました。"); await this.acceptOwnRevision(reviewed.revision); this.journals.clear(); this.remember(); this.publish({ state: "ready", message: "確認した保存内容から再開しました。" }); }
  async acceptOwnRevision(revision: number) { const context = await this.refreshContext(); if (context.scopeContext.resourceRevision !== revision) throw new EditingError("別の編集が入りました。", "REVISION_CONFLICT", 409); this.clearFocus(); }
  private showFocus(value: AiFocusState) {
    const context = this.state.context; if (!context || this.manualActive || !validFocus(value.focus, context, context.scopeContext.resourceRevision)) return;
    if (this.focusTimer) clearTimeout(this.focusTimer);
    const previous = this.state.focus, wait = previous?.phase === "applied" && value.commandId !== previous.commandId ? Math.max(0, 1500 - (performance.now() - previous.receivedAt)) : 0;
    if (wait) this.focusTimer = setTimeout(() => this.showFocus(value), wait);
    else this.publish({ focus: value });
  }
  private beforePreview: AiFocusState | null = null;
  /** Hovering a question choice outlines its target; leaving restores the previous outline. */
  previewTarget(handle: string | null) {
    const context = this.state.context, previewing = this.state.focus?.commandId === "choice-preview";
    const binding = handle && context ? context.uiBindings.find(b => b.handle === handle && (b.kind === "field" || b.kind === "cell")) : null;
    if (!binding || !context) { if (previewing) this.publish({ focus: this.beforePreview }); this.beforePreview = null; return; }
    if (!previewing) this.beforePreview = this.state.focus;
    const scope = context.scopeContext, field = this.resourceKind === "ocr";
    const anchor = { anchorId: `preview_${binding.entityId}`, kind: field ? "review_field" as const : "template_cell" as const, resourceId: scope.resource.id, pageId: binding.pageId,
      tableId: null, fieldId: field ? binding.entityId : null, cellId: field ? null : binding.entityId, rowId: null, columnId: null, label: "" };
    this.publish({ focus: { commandId: "choice-preview", phase: "preview", receivedAt: performance.now(),
      focus: { viewRevision: scope.resourceRevision, contextRevision: scope.contextRevision, interactionEpoch: scope.interactionEpoch, anchors: [anchor], primaryAnchorId: anchor.anchorId } } });
  }
  clearFocus(commandId?: string) { if (this.focusTimer) clearTimeout(this.focusTimer); this.focusTimer = null; if (!commandId || this.state.focus?.commandId === commandId) this.publish({ focus: null }); }
}
const stores = new Map<string, WorkbenchStore>();
export function resourceEditingStore(kind: "template" | "ocr", id: string) { const key = `${kind}:${id}`; if (!stores.has(key)) stores.set(key, new WorkbenchStore(id, kind)); return stores.get(key)!; }
export const templateEditingStore = (id: string) => resourceEditingStore("template", id);

/** Codes meaning the save was built on an outdated version, not that its content was refused. */
function contentRefusal(code: string) { return !/REVISION|STALE|SESSION|CONTEXT|BINDING_CHANGED/.test(code); }
/** Template saves and undos that the editor can redo from its own state when refused. */
function droppableManual(journal: Journal) {
  return journal.origin === "manual" && (journal.endpoint === "authoring" || (journal.body as CommandRequest | undefined)?.command?.kind === "undo");
}
function refusedManualSave(journal: Journal) {
  const payload = (journal.finalEvent ?? journal.result?.finalEvent)?.payload;
  return droppableManual(journal) && Boolean(payload) && (payload!.type === "failed" || payload!.type === "conflict");
}
