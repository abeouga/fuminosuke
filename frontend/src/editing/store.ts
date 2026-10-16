import { privateSessionStorage } from "../auth/transport";
import { editingClient, EditingError, type EditingOwner, type ManualEdit, type ManualOcrEdit, type ManualResult, type ManualView } from "./client";
import type { Batch, BatchCommand, BatchResult, ContextResult, PageScope, Undo, UndoCommand } from "./generated";
import type { OcrDocument, OcrResult } from "../types";
import type { TemplateAuthoringUpdate, TemplateAuthoringView } from "../assistant/generatedAuthoring";
import { api } from "../api";
import { substituteOwnReferences } from "./reconcile";

export type SaveState = "ready" | "saving" | "recovering" | "saved_display_pending" | "conflict" | "failed";
type Journal = { owner: EditingOwner; viewOwner?: EditingOwner; result: ManualResult | null; sent: boolean } & (
  { kind: "manual"; body: ManualEdit } | { kind: "ocr_manual"; body: ManualOcrEdit } | { kind: "undo"; body: Undo } | { kind: "batch"; body: Batch }
);
export type LoadedManualView = ManualView & { authoring: TemplateAuthoringView };
export type SavedResource = { result: BatchResult; context: ContextResult; template: LoadedManualView | null; ocr: OcrResult | null };
export type StoreStatus = { state: SaveState; message: string; canUndo: boolean };
const clone = <T,>(value: T): T => structuredClone(value);

/** One serial resource queue. Factories run against only this client's own prior receipt. */
export class EditingStore {
  private owner: EditingOwner | null = null;
  private context: ContextResult | null = null;
  private journal: Journal | null = null;
  private tail: Promise<unknown> = Promise.resolve();
  private halted = false;
  private listeners = new Set<() => void>();
  private substitutions: Record<string, string> = {};
  private pending = 0;
  private interactionActive = false;
  private interactionEpoch = 0;
  private interactionListeners = new Set<() => void>();
  private initialization: Promise<void> | null = null;
  private status: StoreStatus = { state: "ready", message: "", canUndo: false };
  private readonly key: string;

  constructor(readonly resourceId: string, readonly resourceKind: "template" | "ocr" = "template") {
    this.key = `editing-journal-v1:${resourceKind}:${resourceId}`;
    const raw = privateSessionStorage.getItem(this.key);
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as Journal;
        if ((parsed.kind === "undo" || parsed.kind === "batch" || ((parsed.kind === "manual" || parsed.kind === "ocr_manual") && parsed.body.draft)) && parsed.owner.sessionId && parsed.body.requestId) {
          this.journal = parsed; this.owner = parsed.viewOwner ?? parsed.owner; this.halted = true;
          this.status = { state: parsed.result ? "saved_display_pending" : "recovering", message: "前の保存結果を確認してください。入力は保持しています。", canUndo: false };
        }
      } catch { this.status = { state: "failed", message: "保存記録を読み取れません。下書きは保持しています。", canUndo: false }; this.halted = true; }
    }
  }
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.status;
  private notify(state: SaveState, message: string, canUndo = this.status.canUndo) {
    this.status = { state, message, canUndo }; this.listeners.forEach(fn => fn());
  }
  private remember() {
    // Fail before sending if the journal cannot be persisted. Request identity
    // and input must survive a reload; the bearer token stays in this tab only.
    if (this.journal) privateSessionStorage.setItem(this.key, JSON.stringify(this.journal));
    else privateSessionStorage.removeItem(this.key);
  }
  setInteraction(active: boolean) {
    if (this.interactionActive === active) return;
    this.interactionActive = active;
    if (active) this.invalidateConversation();
  }
  private invalidateConversation() {
    this.interactionEpoch += 1; this.interactionListeners.forEach(fn => fn());
  }
  subscribeInteraction(fn: () => void) { this.interactionListeners.add(fn); return () => this.interactionListeners.delete(fn); }
  async captureBoundary(): Promise<{ context: ContextResult; epoch: number; manualActive: boolean }> {
    await this.initialize();
    if (this.halted) throw new EditingError("先に保存結果を確認してください。", "QUEUE_HALTED", 409);
    this.requireOwner();
    return { context: clone(this.context!), epoch: this.interactionEpoch, manualActive: this.interactionActive };
  }
  async conversationBoundary(): Promise<{ context: ContextResult; epoch: number }> {
    await this.initialize(); await this.tail;
    if (this.halted || this.journal) throw new EditingError("先に保存結果を確認してください。", "QUEUE_HALTED", 409);
    if (this.interactionActive) throw new EditingError("手動操作の終了を待っています。", "MANUAL_EDIT_ACTIVE", 409);
    this.requireOwner();
    return { context: clone(this.context!), epoch: this.interactionEpoch };
  }
  async acceptOwnRevision(revision: number): Promise<void> {
    this.invalidateConversation();
    return this.serial(async () => {
      if (this.journal) throw new EditingError("先に保存結果を確認してください。", "RECEIPT_PENDING", 409);
      const context = await editingClient.context(this.requireOwner());
      if (context.resourceRevision !== revision) throw new EditingError("操作後に別の編集が入りました。", "REVISION_CONFLICT", 409);
      this.context = context;
      this.notify("ready", "", context.history.some(item => item.canUndo));
    });
  }
  async initialize(): Promise<void> {
    if (this.initialization) return this.initialization;
    this.initialization = this.initializeOnce();
    try { await this.initialization; }
    catch (error) { this.initialization = null; throw error; }
  }
  private async initializeOnce(): Promise<void> {
    if (this.journal || this.halted || this.owner) return;
    const current = await editingClient.create({
      resource: { kind: this.resourceKind, id: this.resourceId }, viewInstanceId: crypto.randomUUID(), mode: "manual",
      pageScope: { mode: "all", pageId: null }, localEditEpoch: 0, selectionEpoch: 0,
    });
    this.owner = { sessionId: current.sessionId, token: current.token }; this.context = current;
    if (this.resourceKind === "ocr") {
      this.notify("ready", "", current.history.some(item => item.canUndo)); return;
    }
    let view: ManualView;
    try { view = await editingClient.manualView(this.owner); }
    catch (error) { this.owner = null; this.context = null; throw error; }
    this.context = view.context;
    this.notify("ready", "", this.context.history.some(item => item.canUndo));
  }
  private serial<T>(work: () => Promise<T>): Promise<T> {
    this.pending += 1;
    const result = this.tail.then(async () => {
      if (this.halted) throw new EditingError(this.status.message || "保存を停止しています。", "QUEUE_HALTED", 409);
      try { return await work(); }
      catch (error) {
        if (error instanceof EditingError && error.code === "VOICE_CONTEXT_STALE") throw error;
        this.halted = true;
        if (this.status.state === "saving" || this.status.state === "ready") {
          const conflict = error instanceof EditingError && ["REVISION_CONFLICT", "CONTEXT_STALE", "TARGET_STALE"].includes(error.code);
          this.notify(conflict ? "conflict" : "failed", error instanceof Error ? error.message : "入力を保持して保存を停止しました。", false);
        }
        throw error;
      }
    });
    this.tail = result.catch(() => undefined).finally(() => { this.pending -= 1; });
    return result;
  }
  private requireOwner(): EditingOwner {
    if (!this.owner || !this.context) throw new EditingError("編集状態を取得できていません。", "EDITING_NOT_READY", 409);
    return this.owner;
  }
  async save(draft: TemplateAuthoringUpdate, scope: PageScope): Promise<{ view: LoadedManualView; result: ManualResult }> {
    this.invalidateConversation();
    const captured = clone(draft);
    return this.serial(async () => {
      const owner = this.requireOwner(), context = this.context!;
      this.notify("saving", "保存中です。", false);
      const body: ManualEdit = {
        contractVersion: "editing.v1", requestId: crypto.randomUUID(), expectedRevision: context.resourceRevision,
        contextRevision: context.contextRevision, localEditEpoch: context.localEditEpoch,
        origin: "manual", turnId: null, draft: substituteOwnReferences(captured, this.substitutions),
      };
      this.journal = { kind: "manual", owner, body, result: null, sent: false }; this.remember();
      // A stale external revision is rejected here. Do not fetch/rebase/retry
      // against another writer's revision.
      this.context = await editingClient.update(owner, {
        expectedContextRevision: context.contextRevision, resourceRevision: context.resourceRevision,
        pageScope: scope, localEditEpoch: context.localEditEpoch + 1,
        selectionEpoch: context.selectionEpoch, selectedHandles: [],
      });
      body.contextRevision = this.context.contextRevision; body.localEditEpoch = this.context.localEditEpoch;
      this.journal.sent = true; this.remember();
      let result: ManualResult;
      try { result = await editingClient.manual(owner, body); }
      catch (error) {
        if (error instanceof EditingError && error.status < 500) {
          // A definite rejection has no successful save. Keep the rejected intent
          // until explicit review rather than submitting its successors.
          throw error;
        }
        this.notify("recovering", "保存結果を確認しています。入力は保持しています。", false);
        result = await this.resolveReceipt();
      }
      return this.finish(result);
    });
  }
  private async resolveReceipt(): Promise<ManualResult> {
    if (!this.journal) throw new EditingError("保存記録がありません。", "JOURNAL_MISSING", 409);
    if (!this.journal.sent) throw new EditingError("送信前に停止した入力です。最新の保存版を確認してください。", "UNSENT_DRAFT", 409);
    const { owner, body } = this.journal;
    const receipt = this.journal.kind === "manual" ? await editingClient.manualReceipt(owner, body.requestId) : await editingClient.receipt(owner, body.requestId);
    if (receipt.state === "SUCCEEDED" && receipt.result) return { substitutions: {}, ...receipt.result };
    if (receipt.state === "FAILED") throw new EditingError(receipt.error?.message ?? "保存要求は拒否されました。", receipt.error?.code ?? "EDITING_FAILED", 409);
    if (receipt.state === "NOT_FOUND") {
      if (this.journal.kind === "manual") return editingClient.manual(owner, this.journal.body);
      if (this.journal.kind === "ocr_manual") return { ...await editingClient.manualOcr(owner, this.journal.body), substitutions: {} };
      if (this.journal.kind === "batch") return { ...await editingClient.batch(owner, this.journal.body), substitutions: {} };
      return { ...await editingClient.undo(owner, this.journal.body), substitutions: {} };
    } // Exact original key/payload only.
    throw new EditingError("保存結果がまだ確定していません。再取得してください。", "RECEIPT_PENDING", 409);
  }
  private async loadView(owner: EditingOwner): Promise<LoadedManualView> {
    let view: ManualView;
    try { view = await editingClient.manualView(owner); }
    catch (error) {
      if (!(error instanceof EditingError) || error.code !== "EDITING_SESSION_EXPIRED") throw error;
      // A new read owner never resubmits an old write. The original receipt and
      // its revision are still checked by finish before accepting the view.
      const current = await editingClient.create({ resource: { kind: "template", id: this.resourceId },
        viewInstanceId: crypto.randomUUID(), mode: "manual", pageScope: { mode: "all", pageId: null } });
      this.owner = { sessionId: current.sessionId, token: current.token };
      view = await editingClient.manualView(this.owner);
    }
    const authoring = await api.authoring(this.resourceId);
    if (authoring.revision !== view.context.resourceRevision) throw new EditingError("表示取得中に別の編集が入りました。", "REVISION_CONFLICT", 409);
    return { ...view, authoring };
  }
  private async finish(result: ManualResult): Promise<{ view: LoadedManualView; result: ManualResult }> {
    if (!this.journal) throw new EditingError("保存記録がありません。", "JOURNAL_MISSING", 409);
    this.journal.result = clone(result); this.remember();
    Object.assign(this.substitutions, result.substitutions);
    try {
      const view = await this.loadView(this.journal.viewOwner ?? this.journal.owner);
      if (view.context.resourceRevision !== result.afterRevision) {
        this.notify("conflict", "保存後に別の編集が入りました。入力と保存結果を保持しています。", false);
        throw new EditingError(this.status.message, "REVISION_CONFLICT", 409);
      }
      this.context = view.context;
      this.journal = null; this.remember(); this.halted = false;
      this.notify("ready", result.notification, view.context.history.some(item => item.canUndo));
      return { view, result };
    } catch (error) {
      if (this.status.state !== "conflict") this.notify("saved_display_pending", "保存済み・表示更新待ちです。保存内容を再取得してください。", false);
      throw error;
    }
  }
  async recover(): Promise<{ view: LoadedManualView; result: ManualResult }> {
    await this.tail;
    if (!this.journal) throw new EditingError("再取得する保存記録がありません。", "JOURNAL_MISSING", 409);
    return this.finish(this.journal.result ?? await this.resolveReceipt());
  }
  retainedDraft(): TemplateAuthoringUpdate | null { return this.journal?.kind === "manual" ? clone(this.journal.body.draft) : null; }
  retainedOcrDraft(): ManualOcrEdit["draft"] | null { return this.journal?.kind === "ocr_manual" ? clone(this.journal.body.draft) : null; }
  async inspectLatest(): Promise<LoadedManualView> {
    await this.tail;
    if (!this.owner) throw new EditingError("編集sessionがありません。", "EDITING_NOT_READY", 409);
    return this.loadView(this.owner); // Read-only. Never overwrite the retained input.
  }
  async resumeAfterReview(view: ManualView): Promise<void> {
    this.invalidateConversation();
    await this.tail;
    if (this.journal && !this.journal.result && ["recovering", "saved_display_pending"].includes(this.status.state)) throw new EditingError("先に保存結果を確認してください。", "RECEIPT_PENDING", 409);
    this.context = view.context; this.journal = null; this.remember();
    this.halted = false; this.notify("ready", "確認した保存版から編集を再開しました。保持した入力は次の操作で保存します。", view.context.history.some(item => item.canUndo));
  }
  async resumeOcrAfterReview(reviewed: OcrResult): Promise<void> {
    if (this.resourceKind !== "ocr") throw new EditingError("OCRの編集対象ではありません。", "CAPABILITY_UNAVAILABLE", 409);
    this.invalidateConversation(); await this.tail;
    if (["recovering", "saved_display_pending"].includes(this.status.state)) throw new EditingError("先に保存結果を確認してください。", "RECEIPT_PENDING", 409);
    const context = await editingClient.context(this.requireOwner()), current = await api.result(this.resourceId);
    if (context.resourceRevision !== reviewed.revision || JSON.stringify(current) !== JSON.stringify(reviewed)) throw new EditingError("確認後に別の編集が入りました。保存版を再確認してください。", "REVISION_CONFLICT", 409);
    this.context = context; this.journal = null; this.remember(); this.halted = false;
    this.notify("ready", "確認した保存版から編集を再開しました。", context.history.some(item => item.canUndo));
  }
  /** The controller's fixed envelope is journalled without rebasing or re-keying. */
  async commitConversation(owner: EditingOwner, command: BatchCommand | UndoCommand, epoch: number): Promise<SavedResource> {
    return this.serial(async () => {
      if (this.interactionActive || epoch !== this.interactionEpoch || command.body.expectedRevision !== this.context?.resourceRevision) {
        throw new EditingError("発話後に手動編集が入りました。再指示してください。", "VOICE_CONTEXT_STALE", 409);
      }
      return this.commitResource(owner, command);
    });
  }
  async saveOcr(operations: Batch["operations"]): Promise<SavedResource> {
    if (this.resourceKind !== "ocr") throw new EditingError("OCRの編集対象ではありません。", "CAPABILITY_UNAVAILABLE", 409);
    const captured = clone(operations); this.invalidateConversation();
    return this.serial(async () => {
      const context = this.context!;
      return this.commitResource(this.requireOwner(), { kind: "batch", body: {
        contractVersion: "editing.v1", requestId: crypto.randomUUID(), expectedRevision: context.resourceRevision,
        contextRevision: context.contextRevision, localEditEpoch: context.localEditEpoch,
        origin: "manual", turnId: null, correction: null, operations: captured,
      } });
    });
  }
  async saveOcrDraft(draft: OcrDocument): Promise<SavedResource> {
    await this.initialize(); this.invalidateConversation();
    const captured = clone({ fields: draft.fields, reviewItems: draft.reviewItems });
    return this.serial(async () => {
      const context = this.context!;
      return this.commitResource(this.requireOwner(), { kind: "ocr_manual", body: {
        contractVersion: "editing.v1", requestId: crypto.randomUUID(), expectedRevision: context.resourceRevision,
        contextRevision: context.contextRevision, localEditEpoch: context.localEditEpoch, origin: "manual", turnId: null, draft: captured,
      } });
    });
  }
  private async commitResource(owner: EditingOwner, command: BatchCommand | UndoCommand | { kind: "ocr_manual"; body: ManualOcrEdit }): Promise<SavedResource> {
    this.notify("saving", "変更を保存しています。", false);
    this.journal = { ...clone(command), owner: clone(owner), viewOwner: this.requireOwner(), result: null, sent: true };
    this.remember();
    let result: ManualResult;
    try {
      result = { substitutions: {}, ...await (command.kind === "batch" ? editingClient.batch(owner, command.body) : command.kind === "ocr_manual" ? editingClient.manualOcr(owner, command.body) : editingClient.undo(owner, command.body)) };
    } catch (error) {
      if (error instanceof EditingError && error.status < 500) throw error;
      this.notify("recovering", "保存結果を確認しています。", false);
      result = await this.resolveReceipt();
    }
    return this.finishResource(result);
  }
  private async finishResource(result: ManualResult): Promise<SavedResource> {
    if (this.resourceKind === "template") {
      const saved = await this.finish(result);
      return { result, context: saved.view.context, template: saved.view, ocr: null };
    }
    if (!this.journal) throw new EditingError("保存記録がありません。", "JOURNAL_MISSING", 409);
    this.journal.result = clone(result); this.remember();
    try {
      let context: ContextResult;
      try { context = await editingClient.context(this.owner!); }
      catch (error) {
        if (!(error instanceof EditingError) || error.code !== "EDITING_SESSION_EXPIRED") throw error;
        const session = await editingClient.create({ resource: { kind: "ocr", id: this.resourceId }, viewInstanceId: crypto.randomUUID(), mode: "manual" });
        this.owner = { sessionId: session.sessionId, token: session.token }; context = session;
      }
      const ocr = await api.result(this.resourceId);
      if (context.resourceRevision !== result.afterRevision || ocr.revision !== result.afterRevision) {
        this.notify("conflict", "保存後に別の編集が入りました。保存結果を保持しています。", false);
        throw new EditingError(this.status.message, "REVISION_CONFLICT", 409);
      }
      this.context = context; this.journal = null; this.remember(); this.halted = false;
      this.notify("ready", result.notification, context.history.some(item => item.canUndo));
      return { result, context, template: null, ocr };
    } catch (error) {
      if (this.status.state !== "conflict") this.notify("saved_display_pending", "保存済み・表示更新待ちです。", false);
      throw error;
    }
  }
  async recoverResource(): Promise<SavedResource> {
    await this.tail;
    if (!this.journal) throw new EditingError("再取得する保存記録がありません。", "JOURNAL_MISSING", 409);
    return this.finishResource(this.journal.result ?? await this.resolveReceipt());
  }
  async refreshSaved(saved: SavedResource): Promise<SavedResource> {
    const boundary = await this.conversationBoundary();
    if (boundary.context.resourceRevision !== saved.result.afterRevision) throw new EditingError("保存後に別の編集が入りました。保持した入力と最新表示を確認してください。", "REVISION_CONFLICT", 409);
    if (this.resourceKind === "template") {
      const view = await this.loadView(this.requireOwner());
      if (view.context.resourceRevision !== saved.result.afterRevision) throw new EditingError("保存後に別の編集が入りました。", "REVISION_CONFLICT", 409);
      return { ...saved, template: view, context: view.context };
    }
    const ocr = await api.result(this.resourceId);
    if (ocr.revision !== saved.result.afterRevision) throw new EditingError("保存後に別の編集が入りました。", "REVISION_CONFLICT", 409);
    return { ...saved, ocr, context: boundary.context };
  }
  async undo(): Promise<LoadedManualView> {
    if (this.resourceKind !== "template") throw new EditingError("テンプレートの編集対象ではありません。", "CAPABILITY_UNAVAILABLE", 409);
    return (await this.undoResource()).template!;
  }
  async undoResource(): Promise<SavedResource> {
    this.invalidateConversation();
    return this.serial(async () => {
      if (this.interactionActive || this.pending > 1) throw new EditingError("未保存の操作を先に終了してください。", "MANUAL_EDIT_ACTIVE", 409);
      const owner = this.requireOwner();
      const context = await editingClient.context(owner);
      if (context.resourceRevision !== this.context!.resourceRevision) throw new EditingError("別の編集が保存されています。", "REVISION_CONFLICT", 409);
      const previous = context.history.find(item => item.canUndo);
      if (!previous) throw new EditingError("現在の直前操作を取り消せません。", "UNDO_UNAVAILABLE", 409);
      const body: Undo = {
        contractVersion: "editing.v1", requestId: crypto.randomUUID(), expectedRevision: context.resourceRevision,
        contextRevision: context.contextRevision, localEditEpoch: context.localEditEpoch,
        origin: "manual", turnId: null, operationId: previous.operationId,
      };
      return this.commitResource(owner, { kind: "undo", body });
    });
  }
}
const stores = new Map<string, EditingStore>();
export function templateEditingStore(id: string): EditingStore {
  return resourceEditingStore("template", id);
}
export function resourceEditingStore(kind: "template" | "ocr", id: string): EditingStore {
  const key = `${kind}:${id}`;
  let store = stores.get(key); if (!store) { store = new EditingStore(id, kind); stores.set(key, store); }
  return store;
}
