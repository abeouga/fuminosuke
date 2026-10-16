import { authenticatedFetch } from "../auth/transport";
import { editingClient, EditingError, type EditingOwner } from "../editing/client";
import type { ContextResult, ContextUpdate, PageScope, StartedTurn, TargetInfo, TurnStatus, TurnResolution } from "../editing/generated";
import { EditingStore, type SavedResource } from "../editing/store";

export type ConversationState = "LISTENING" | "RESOLVING" | "COMMITTING" | "NOTIFYING" | "CLARIFYING" | "RECOVERING" | "STOPPED";
export type ConversationStatus = { state: ConversationState; message: string; candidates: TargetInfo[] };
export type CapturedTurn = { owner: EditingOwner; started: StartedTurn; epoch: number; generation: number };
export type AudioCapture = { epoch: number; generation: number; revision: number };

/** Text/completed-STT adapter. Microphone and playback lifecycles are separate. */
export class EditingConversation {
  private owner: EditingOwner | null = null;
  private context: ContextResult | null = null;
  private generation = 0;
  private pendingQuestion: string | null = null;
  private turns = new Map<string, CapturedTurn>();
  private listeners = new Set<() => void>();
  private readonly unsubscribe: () => boolean;
  private status: ConversationStatus = { state: "STOPPED", message: "", candidates: [] };
  private resolving = false;
  private current: CapturedTurn | null = null;
  private ownAdvances = new Map<number, number>();
  private pendingDisplay: SavedResource | null = null;

  constructor(readonly store: EditingStore, private readonly saved: (value: SavedResource) => void | Promise<void>, private readonly beforeCommit: () => Promise<void> = async () => undefined) {
    this.unsubscribe = store.subscribeInteraction(() => { void this.invalidate(); });
  }
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.status;
  private set(state: ConversationState, message = "", candidates: TargetInfo[] = []) {
    this.status = { state, message, candidates }; this.listeners.forEach(fn => fn());
  }
  private requireOwner(): EditingOwner {
    if (!this.owner) throw new EditingError("会話は停止しています。", "CONVERSATION_STOPPED", 409);
    return this.owner;
  }
  getOwner(): EditingOwner { return this.requireOwner(); }
  getContext(): ContextResult { if (!this.context) throw new Error("会話は停止しています。"); return this.context; }
  async capture(): Promise<AudioCapture> {
    const boundary = await this.store.captureBoundary();
    const generation = this.generation;
    const context = await editingClient.context(this.requireOwner());
    const latest = await this.store.captureBoundary();
    if (generation !== this.generation || boundary.epoch !== latest.epoch || context.resourceRevision !== latest.context.resourceRevision) throw new EditingError("発話開始中に編集状態が変わりました。再指示してください。", "VOICE_CONTEXT_STALE", 409);
    if (this.context?.contextRevision !== context.contextRevision) this.pendingQuestion = null;
    this.context = context;
    return { epoch: latest.epoch, generation, revision: latest.context.resourceRevision };
  }
  async cancelUncommitted(): Promise<boolean> {
    if (!this.current) return false;
    return (await this.cancel(this.current)).state !== "CONSUMED";
  }
  async open(mode: "continuous" | "local", pageScope: PageScope): Promise<ContextResult> {
    const stopped = this.stop(), generation = this.generation;
    await stopped;
    if (generation !== this.generation) throw new EditingError("会話開始を中止しました。", "VOICE_CONTEXT_STALE", 409);
    const boundary = await this.store.conversationBoundary();
    if (generation !== this.generation) throw new EditingError("会話開始中に編集状態が変わりました。", "VOICE_CONTEXT_STALE", 409);
    const session = await editingClient.create({
      resource: { kind: this.store.resourceKind, id: this.store.resourceId },
      viewInstanceId: boundary.context.viewInstanceId, mode, pageScope,
      localEditEpoch: boundary.context.localEditEpoch, selectionEpoch: 0,
    });
    const owner = { sessionId: session.sessionId, token: session.token };
    if (generation !== this.generation || session.resourceRevision !== boundary.context.resourceRevision) {
      await editingClient.close(owner);
      throw new EditingError("会話開始中に編集状態が変わりました。", "VOICE_CONTEXT_STALE", 409);
    }
    this.owner = owner; this.context = session; this.set("LISTENING");
    return session;
  }
  async updateContext(update: Omit<ContextUpdate, "expectedContextRevision" | "resourceRevision">): Promise<ContextResult> {
    await this.invalidate();
    const owner = this.requireOwner();
    const boundary = await this.store.conversationBoundary();
    const current = await editingClient.context(owner);
    if (current.resourceRevision !== boundary.context.resourceRevision) throw new EditingError("別の編集が保存されています。", "REVISION_CONFLICT", 409);
    this.context = await editingClient.update(owner, { ...update,
      expectedContextRevision: current.contextRevision, resourceRevision: current.resourceRevision });
    return this.context;
  }
  async begin(audioItemId: string | null = null, capture?: AudioCapture): Promise<CapturedTurn> {
    const owner = this.requireOwner();
    const generation = this.generation;
    const boundary = await this.store.conversationBoundary();
    if (capture) {
      let revision = capture.revision;
      while (this.ownAdvances.has(revision)) revision = this.ownAdvances.get(revision)!;
      if (capture.epoch !== boundary.epoch || capture.generation !== generation || revision !== boundary.context.resourceRevision) {
        throw new EditingError("発話中に手動編集が入りました。再指示してください。", "VOICE_CONTEXT_STALE", 409);
      }
    }
    const context = await editingClient.context(owner);
    if (context.resourceRevision !== boundary.context.resourceRevision || generation !== this.generation) {
      throw new EditingError("発話開始前に編集状態が変わりました。", "VOICE_CONTEXT_STALE", 409);
    }
    if (this.context && context.contextRevision !== this.context.contextRevision) this.pendingQuestion = null;
    this.context = context;
    const started = await editingClient.beginTurn(owner, {
      contextRevision: context.contextRevision, resourceRevision: context.resourceRevision,
      localEditEpoch: context.localEditEpoch, selectionEpoch: context.selectionEpoch, audioItemId,
    });
    const captured = { owner, started, epoch: boundary.epoch, generation };
    if (generation !== this.generation) {
      await editingClient.cancelTurn(owner, started.turnId);
      throw new EditingError("発話開始中に手動操作が入りました。", "VOICE_CONTEXT_STALE", 409);
    }
    this.turns.set(started.turnId, captured);
    return captured;
  }
  async resolve(captured: CapturedTurn, text: string): Promise<SavedResource | null> {
    return this.resolveWith(captured, replyTo => editingClient.resolveTurn(captured.owner, captured.started.turnId, { text, replyTo }));
  }
  async resolveAudio(captured: CapturedTurn, audio: Blob): Promise<SavedResource | null> {
    return this.resolveWith(captured, async replyTo => {
      const response = await authenticatedFetch(`/api/v1/editing/sessions/${captured.owner.sessionId}/conversation/turns/${captured.started.turnId}/audio${replyTo ? `?reply_to=${replyTo}` : ""}`, {
        method: "POST", headers: { "X-Editing-Session": captured.owner.token, "Content-Type": audio.type }, body: audio,
      });
      const value = await response.json();
      if (!response.ok) throw new EditingError(value.message ?? value.detail, value.code, response.status);
      return value as TurnResolution;
    });
  }
  getQuestionId(): string | null { return this.pendingQuestion; }
  private async resolveWith(captured: CapturedTurn, resolve: (replyTo: string | null) => Promise<TurnResolution>): Promise<SavedResource | null> {
    if (this.resolving) throw new EditingError("前の発話を処理しています。", "TURN_RESOLVING", 409);
    this.resolving = true;
    this.current = captured;
    let committing = false;
    try {
      const boundary = await this.store.conversationBoundary();
      if (captured.generation !== this.generation || captured.epoch !== boundary.epoch || captured.started.context.resourceRevision !== boundary.context.resourceRevision) {
        throw new EditingError("発話後に編集状態が変わりました。再指示してください。", "VOICE_CONTEXT_STALE", 409);
      }
      this.set("RESOLVING");
      const previousQuestion = this.pendingQuestion;
      const result = await resolve(previousQuestion);
      if (previousQuestion) this.turns.delete(previousQuestion);
      if (captured.generation !== this.generation || !this.turns.has(captured.started.turnId)) throw new EditingError("発話の解析を中止しました。", "VOICE_CONTEXT_STALE", 409);
      if (result.state !== "READY") {
        this.pendingQuestion = result.state === "CLARIFYING" ? result.turnId : null;
        if (result.state !== "CLARIFYING") this.turns.delete(captured.started.turnId);
        this.set(result.state === "CLARIFYING" ? "CLARIFYING" : "LISTENING", result.message, result.candidates ?? []);
        return null;
      }
      if (!result.command) throw new EditingError("変更の解析結果が不正です。", "VOICE_RESPONSE_INVALID", 422);
      await this.beforeCommit();
      if (captured.generation !== this.generation || !this.turns.has(captured.started.turnId)) throw new EditingError("発話の処理を中止しました。", "VOICE_CONTEXT_STALE", 409);
      this.pendingQuestion = null; committing = true; this.set("COMMITTING");
      const saved = await this.store.commitConversation(captured.owner, result.command, captured.epoch);
      this.turns.delete(captured.started.turnId);
      if (saved.result.afterRevision > saved.result.beforeRevision) this.ownAdvances.set(saved.result.beforeRevision, saved.result.afterRevision);
      this.pendingDisplay = saved;
      this.context = await editingClient.context(captured.owner);
      if (this.context.resourceRevision !== saved.result.afterRevision) throw new EditingError("保存後に別の編集が入りました。", "REVISION_CONFLICT", 409);
      await this.saved(saved);
      this.pendingDisplay = null;
      if (captured.generation === this.generation) this.set("NOTIFYING", saved.result.notification, []);
      return saved;
    } catch (error) {
      if (!committing || (error instanceof EditingError && error.code === "VOICE_CONTEXT_STALE")) {
        await this.cancel(captured).catch(() => undefined);
      }
      // A stopped/replaced conversation must not publish a late resolution
      // error. Once saving began, retain the receipt recovery path instead.
      if (!committing && captured.generation !== this.generation) throw error;
      const state = this.store.getSnapshot().state;
      this.set(this.pendingDisplay || ["recovering", "saved_display_pending"].includes(state) ? "RECOVERING" : this.owner ? "LISTENING" : "STOPPED", this.pendingDisplay ? "保存済み・表示更新待ちです。" : error instanceof Error ? error.message : "処理を完了できませんでした。");
      throw error;
    } finally { this.resolving = false; this.current = null; }
  }
  async cancel(captured: CapturedTurn): Promise<TurnStatus> {
    const status = await editingClient.cancelTurn(captured.owner, captured.started.turnId);
    this.turns.delete(captured.started.turnId);
    if (this.pendingQuestion === captured.started.turnId) this.pendingQuestion = null;
    // A consumed turn stays saved. The original receipt establishes whether a
    // later undo is needed; cancellation never asserts rollback.
    return status;
  }
  async recover(): Promise<SavedResource> {
    this.set("RECOVERING");
    const saved = this.pendingDisplay ? await this.store.refreshSaved(this.pendingDisplay) : await this.store.recoverResource();
    await this.saved(saved); this.pendingDisplay = null; this.set(this.owner ? "NOTIFYING" : "STOPPED", saved.result.notification);
    return saved;
  }
  notificationFinished() { if (this.status.state === "NOTIFYING") this.set(this.owner ? "LISTENING" : "STOPPED", this.status.message); }
  private async invalidate(): Promise<void> {
    this.generation += 1; this.pendingQuestion = null;
    this.ownAdvances.clear();
    const turns = [...this.turns.values()]; this.turns.clear();
    await Promise.allSettled(turns.map(turn => editingClient.cancelTurn(turn.owner, turn.started.turnId)));
  }
  async stop(): Promise<void> {
    const owner = this.owner; this.owner = null; this.context = null;
    const invalidated = this.invalidate(), generation = this.generation;
    await invalidated;
    try { if (owner) await editingClient.close(owner); }
    finally {
      const pending = this.pendingDisplay || ["recovering", "saved_display_pending"].includes(this.store.getSnapshot().state);
      if (generation === this.generation) this.set(pending ? "RECOVERING" : "STOPPED", pending ? "保存結果または保存済みの表示を確認してください。" : "");
    }
  }
  async dispose(): Promise<void> { this.unsubscribe(); await this.stop(); this.listeners.clear(); }
}
