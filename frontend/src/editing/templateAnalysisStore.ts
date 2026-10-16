import { privateSessionStorage } from "../auth/transport";
import type { LayoutDefinition } from "../types";
import { analysisPreview } from "./analysisPreview";
import { authoringFacets, changedFacets, facetKey, facetTouch } from "./authoringFacets";
import { bindingOf, type WorkbenchStore } from "./workbenchStore";
import { EditingError, type Owner } from "./v2client";
import { analysisEvents, templateAnalysisClient as client } from "./templateAnalysisClient";
import type { AiFocusState } from "./focusState";
import type { AnalysisCreate, AnalysisMergeRequest, AnalysisResult, FacetTouch, GeometryCell, ManualDraft, MergeResult, PageScope } from "./template-analysis.generated";

export const analysisRunning = (run: AnalysisResult | null) => !!run && ["queued", "running", "validating"].includes(run.state);
export const analysisMergeAvailable = (run: AnalysisResult | null) => !!run?.proposal && !["cancelled", "failed", "expired", "stale"].includes(run.state);
export type AnalysisReadiness = { analysisId: string; selectionControls: { unitId: string; enabled: boolean; visible: boolean; hitTest: boolean; previewVisible: boolean }[];
  mergeControl: { present: boolean; enabled: boolean; requiresSelection: boolean; visible: boolean; hitTest: boolean } };
type PendingMerge = { owner: Owner; analysisId: string; body: AnalysisMergeRequest; manualSerial?: number; result?: MergeResult };
type SaveCapture = { serial: number; analysisMergeIds: string[]; manualTouchedFacets: FacetTouch[] };
type Journal = { owner: Owner | null; create: AnalysisCreate | null; analysisId: string | null; sequence: number;
  touches: [string, { touch: FacetTouch; serial: number }][]; serial: number; mergeIds: string[];
  pendingMerge: PendingMerge | null; held: boolean; saveCapture?: SaveCapture; applied?: string[]; analysisSerial?: number;
  staged?: { input: ManualDraft; output: ManualDraft; mergeId: string; unitIds: string[]; manualSerial?: number } };
export type AnalysisView = { run: AnalysisResult | null; busy: boolean; held: boolean; message: string;
  focus: AiFocusState | null; applied: string[]; conflicts: string[]; staleUnits: string[]; recovery: boolean; storageBlocked: boolean; pendingFacets: number; manualSerial: number };
type Editor = { read(): ManualDraft | null; matches?(input: ManualDraft): boolean; priorityBounds?(): number[] | null; apply(input: ManualDraft, merged: ManualDraft, action?: "apply" | "undo"): boolean; pause(): void; resume(): void };
const emptyJournal = (): Journal => ({ owner: null, create: null, analysisId: null, sequence: 0, touches: [], serial: 0, mergeIds: [], pendingMerge: null, held: false });
const messageOf = (e: unknown) => e instanceof Error ? e.message : "解析処理を完了できませんでした。";
const runMessage = (run: AnalysisResult) => run.error?.message ?? (analysisRunning(run)
  ? "AIで帳票を解析中です。手動編集は続けられます。"
  : run.state === "cancelled" ? "解析を中止しました。下書きは保持しています。"
  : ["failed", "expired", "stale"].includes(run.state) ? "解析案を反映できません。下書きは保持しています。"
  : "解析案を確認してください。まだ保存していません。");

/** Analysis never saves business data. Writes and lost replies retain their exact request IDs. */
export class TemplateAnalysisStore {
  private journal = emptyJournal();
  private state: AnalysisView = { run: null, busy: false, held: false, message: "", focus: null, applied: [], conflicts: [], staleUnits: [], recovery: false, storageBlocked: false, pendingFacets: 0, manualSerial: 0 };
  private listeners = new Set<() => void>();
  private stream: AbortController | null = null;
  private paintedUnits = new Set<string>();
  private mark(stage: string, requestId = this.journal.create?.requestId, detail?: unknown) {
    if (requestId) performance.mark?.(`template-analysis:${requestId}:${stage}`, { detail });
  }
  painted(unitIds: string[], actionable: boolean, readiness?: AnalysisReadiness) {
    const run = this.state.run;
    if (!run || (readiness && readiness.analysisId !== run.analysisId)) return;
    if (actionable && (!analysisMergeAvailable(run) || this.state.busy || this.state.recovery || this.state.storageBlocked ||
      !readiness?.mergeControl.present || !readiness.mergeControl.visible || !readiness.mergeControl.hitTest ||
      !(readiness.mergeControl.enabled || readiness.mergeControl.requiresSelection))) return;
    const units = run.proposal?.units ?? run.pages.flatMap(p => p.proposal?.units ?? []) ?? [];
    for (const id of unitIds) {
      if (!units.some(u => u.unitId === id && u.state === "mergeable") || this.state.applied.includes(id) || this.state.staleUnits.includes(id)) continue;
      if (actionable && (this.state.conflicts.includes(id) || !readiness?.selectionControls.some(c => c.unitId === id && c.enabled && c.visible && c.hitTest && c.previewVisible))) continue;
      const key = `${actionable ? "actionable" : "rendered"}:${id}`;
      if (!this.paintedUnits.has(key)) { this.paintedUnits.add(key); this.mark(key, undefined, { ...readiness, unitId: id }); }
    }
  }
  private storageKey: string;
  constructor(private workbench: WorkbenchStore, private editor: Editor) {
    this.storageKey = `template-analysis-v2:${workbench.resourceId}`;
    try {
      const raw = privateSessionStorage.getItem(this.storageKey);
      if (raw) this.journal = JSON.parse(raw) as Journal;
      const retired = this.journal.create && this.journal.create.contractVersion !== "template-analysis.v3";
      if (retired) {
        this.journal = { ...emptyJournal(), held: true };
        this.state.message = "解析の定義が変わりました。手動の下書きを確認してから再解析してください。";
      }
      this.state = { ...this.state, held: this.journal.held, recovery: !!(this.journal.create || this.journal.pendingMerge),
        applied: this.journal.applied ?? [],
        pendingFacets: this.journal.touches.length, manualSerial: this.journal.serial,
        message: retired ? "解析の定義が変わりました。手動の下書きを保持しています。確認してから再解析してください。" : this.journal.held ? "保持したAI案と下書きを確認してください。自動保存は保留中です。" : "" };
    } catch { this.state.message = "解析の復旧情報を読めません。手動下書きを確認してください。"; }
  }
  getSnapshot = () => this.state;
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  private publish(patch: Partial<AnalysisView>) {
    const next = { ...this.state, ...patch };
    const changedGeometry = this.journal.touches.some(([, v]) => v.touch.facet === "geometry" && v.serial > (this.journal.analysisSerial ?? 0));
    const units = next.run?.proposal?.units ?? next.run?.pages.flatMap(p => p.proposal?.units ?? []) ?? [];
    this.state = { ...next, staleUnits: changedGeometry ? units.filter(u => !next.applied.includes(u.unitId)).map(u => u.unitId) : [] };
    this.listeners.forEach(fn => fn());
  }
  private remember() {
    try { privateSessionStorage.setItem(this.storageKey, JSON.stringify(this.journal)); }
    catch {
      this.editor.pause(); this.journal.held = true;
      const message = "復旧情報を保存できません。入力を保持して自動保存を止めました。保存領域を確保してから再取得してください。";
      this.publish({ held: true, storageBlocked: true, message });
      throw new Error(message);
    }
    this.publish({ storageBlocked: false });
  }
  private tryRemember() { try { this.remember(); return true; } catch { return false; } }
  private hold() { this.editor.pause(); this.journal.held = true; this.publish({ held: true }); this.remember(); }
  reconnect() {
    if (this.journal.held) this.editor.pause();
    if (this.journal.analysisId) void this.refresh().catch(e => this.publish({ message: messageOf(e), recovery: true }));
    return () => this.disconnect();
  }
  disconnect() { this.stream?.abort(); this.stream = null; }
  touch = (touches: FacetTouch[]) => {
    if (!touches.length) return;
    const map = new Map(this.journal.touches);
    for (const touch of touches) {
      const id = touch.target.draftEntityId;
      if (id) map.set(facetKey(id, touch.facet), { touch, serial: ++this.journal.serial });
    }
    this.journal.touches = [...map];
    this.tryRemember();
    this.publish({ focus: null, pendingFacets: map.size, manualSerial: this.journal.serial });
  };
  private touches(draft: ManualDraft, afterSerial = -1): FacetTouch[] {
    const available = authoringFacets(draft.layout as LayoutDefinition);
    return this.journal.touches.filter(([key, value]) => value.serial > afterSerial && (available.has(key) || value.touch.facet === "geometry")).map(([, value]) => value.touch);
  }
  captureSave(draft: ManualDraft) {
    const captured = { serial: this.journal.serial, analysisMergeIds: [...this.journal.mergeIds], manualTouchedFacets: this.touches(draft) };
    this.journal.saveCapture = captured; this.remember(); return captured;
  }
  saved(captured: SaveCapture | undefined = this.journal.saveCapture, substitutions: Record<string, string> = {}) {
    if (!captured) return;
    this.mark("saved");
    const wasHeld = this.journal.held;
    this.journal.touches = this.journal.touches.filter(([, v]) => v.serial > captured.serial).map(([, value]) => {
      const id = value.touch.target.draftEntityId!;
      const mapped = substitutions[id] ?? id;
      return [facetKey(mapped, value.touch.facet), { ...value, touch: { ...value.touch, target: { draftEntityId: mapped } } }];
    });
    this.journal.mergeIds = this.journal.mergeIds.filter(id => !captured.analysisMergeIds.includes(id));
    this.journal.pendingMerge = null;
    this.journal.applied = []; delete this.journal.staged;
    delete this.journal.saveCapture;
    this.disconnect(); this.journal.create = null; this.journal.analysisId = null;
    this.journal.held = false;
    const remembered = this.tryRemember();
    if (remembered) this.editor.resume();
    // A local journal failure cannot turn a received business receipt into a failed save.
    this.publish({ held: !remembered, recovery: false, focus: null, run: null, applied: [], conflicts: [], pendingFacets: this.journal.touches.length,
      message: !remembered ? this.state.message : wasHeld ? "保存しました。AI案の再反映には再解析してください。" : "" });
  }
  async start(scope: PageScope, task: "full" | "roles", releaseRoles: boolean) {
    if (this.state.busy || this.state.storageBlocked || analysisRunning(this.state.run) || this.state.recovery || this.journal.mergeIds.length) return;
    const requestId = crypto.randomUUID(); this.mark("clicked", requestId); this.paintedUnits.clear();
    this.publish({ run: null, busy: true, message: "解析を受け付けています。", focus: null, applied: [], conflicts: [] });
    try {
      this.hold();
      const context = await this.workbench.analysisContext(scope);
      const draft = this.editor.read(); if (!draft) throw new Error("入力を終了してから解析してください。");
      const owner = this.workbench.getOwner();
      this.disconnect(); this.journal = { ...this.journal, owner, analysisId: null, sequence: 0, pendingMerge: null, analysisSerial: this.journal.serial,
          create: { contractVersion: "template-analysis.v3", requestId, binding: bindingOf(context),
          draft, task, pageScope: scope, selectedTableId: context.scopeContext.selectedTableId,
          priorityBounds: this.editor.priorityBounds?.() ?? null,
          releaseRoleProtection: releaseRoles ? context.uiBindings.filter(b => b.kind === "cell" &&
            (scope.mode === "all" || b.pageId === scope.pageId)).map(b => b.handle) : [], manualTouchedFacets: this.touches(draft) } };
      this.remember();
      this.accept(await client.create(owner, this.journal.create!), true);
      this.mark("admitted");
    } catch (e) { this.failed(e); }
    finally { this.publish({ busy: false }); }
  }
  private failed(e: unknown) {
    // These preflight refusals happen before AnalysisRun admission. A timeout or
    // unknown 5xx remains ambiguous and must still be recovered before reuse.
    const notAdmitted = new Set(["PROVIDER_NOT_CONFIGURED", "TEMPLATE_ANALYSIS_DISABLED",
      "TEMPLATE_ANALYSIS_SCHEMA_REQUIRED", "EDITING_API_DISABLED", "EDITING_SCHEMA_REQUIRED"]);
    const definitive = e instanceof EditingError && (e.status < 500 || notAdmitted.has(e.code));
    if (definitive) { if (!this.journal.analysisId) this.journal.create = null; this.journal.pendingMerge = null; }
    const remembered = this.tryRemember();
    this.publish({ message: remembered ? messageOf(e) : this.state.message, recovery: !definitive && !!(this.journal.create || this.journal.pendingMerge) });
  }
  private accept(run: AnalysisResult, createReply = false) {
    // The server may coalesce a new request onto an existing run with its original requestId.
    if (!this.journal.owner || run.resource.id !== this.workbench.resourceId ||
      (!createReply && run.analysisId !== this.journal.analysisId)) return;
    this.journal.analysisId = run.analysisId;
    this.journal.sequence = Math.max(this.journal.sequence, run.lastSequence); this.remember();
    this.publish({ run, recovery: false, message: runMessage(run) });
    if (analysisRunning(run)) this.connect(run);
  }
  private connect(run: AnalysisResult) {
    if (this.stream || !this.journal.owner) return;
    const controller = this.stream = new AbortController();
    void analysisEvents(this.journal.owner, run.analysisId, this.journal.sequence, controller.signal, event => {
      if (controller.signal.aborted || this.journal.analysisId !== run.analysisId || event.analysisId !== run.analysisId || event.snapshotDigest !== run.snapshotDigest || event.sequence <= this.journal.sequence) return;
      this.journal.sequence = event.sequence;
      if (event.type === "page_started" && this.state.run?.analysisId === run.analysisId) {
        this.publish({ run: { ...this.state.run, state: "running", pages: this.state.run.pages.map(page => page.pageId === event.pageId ? { ...page, state: "running" } : page) } });
      }
      // GET fetches only validated page proposals, never model JSON in progress events.
      if (["region_validated", "page_validated", "ready", "partial", "failed", "cancelled", "expired", "stale"].includes(event.type)) void this.refresh().catch(e => this.publish({ message: messageOf(e), recovery: true }));
    }).then(async () => {
      if (!controller.signal.aborted && this.journal.analysisId === run.analysisId) await this.refresh();
    }).catch(e => {
      if (!controller.signal.aborted) this.publish({ recovery: true, message: messageOf(e) + " 結果の再取得で再接続できます。" });
    }).finally(() => { if (this.stream === controller) this.stream = null; });
  }
  async refresh() {
    if (!this.journal.owner || !this.journal.analysisId) return;
    const id = this.journal.analysisId, run = await client.get(this.journal.owner, id);
    if (id === this.journal.analysisId && run.lastSequence >= (this.state.run?.lastSequence ?? 0)) this.accept(run);
  }
  async recover() {
    if (this.state.busy) return;
    this.publish({ busy: true });
    try {
      this.remember();
      if (this.journal.pendingMerge) await this.sendMerge(this.journal.pendingMerge);
      else if (this.journal.analysisId) { this.disconnect(); await this.refresh(); }
      else if (this.journal.owner && this.journal.create) this.accept(await client.create(this.journal.owner, this.journal.create), true);
      else if (!this.journal.mergeIds.length) {
        this.journal.held = false; this.remember(); this.editor.resume();
        this.publish({ held: false, recovery: false, run: null, message: "復旧情報を保存しました。手動編集へ戻りました。" });
      }
    } catch (e) { this.failed(e); } finally { this.publish({ busy: false }); }
  }
  async cancel() {
    if (this.state.busy || !this.journal.owner || !this.journal.analysisId) return;
    this.publish({ busy: true });
    try { this.accept(await client.cancel(this.journal.owner, this.journal.analysisId)); this.disconnect(); }
    catch (e) { this.failed(e); } finally { this.publish({ busy: false }); }
  }
  preview(unitIds: string[]) {
    const run = this.state.run, draft = this.editor.read();
    if (!run || !draft) { this.publish({ focus: null }); return; }
    const units = (run.proposal?.units ?? run.pages.flatMap(p => p.proposal?.units ?? []))
      .filter(u => unitIds.includes(u.unitId) && !this.state.staleUnits.includes(u.unitId));
    this.mark("selected", undefined, { unitIds: units.map(u => u.unitId) });
    this.publish({ focus: analysisPreview(run, units, draft.layout as LayoutDefinition, this.journal.create?.binding, "proposal") });
  }
  previewUnresolved(cellId: string, bounds?: GeometryCell) {
    const run = this.state.run, draft = this.editor.read();
    if (!run || !draft || this.state.staleUnits.length) return;
    const units = run.proposal?.units ?? run.pages.flatMap(p => p.proposal?.units ?? []);
    const displayId = bounds ? `unresolved:${bounds.x}:${bounds.y}:${bounds.width}:${bounds.height}` : cellId;
    const displayUnits = bounds ? [{ unitId: "unresolved-preview", pageId: null, kind: "structure" as const, cellIds: [displayId], name: "手動設定が必要", state: "protected" as const, reason: null, dependencies: [], geometry: { before: [], after: [{ ...bounds, id: displayId }], addedIds: [], removedIds: [], changedIds: [], referenceMap: {} } }] : units;
    const focus = analysisPreview(run, displayUnits, draft.layout as LayoutDefinition, this.journal.create?.binding, "proposal", displayId, true);
    this.publish({ focus, ...(!focus ? { message: "この欄の位置を表示できません。原画像を確認し、手動で枠を設定してください。" } : {}) });
  }
  previewCell(cellId: string) {
    const run = this.state.run, draft = this.editor.read();
    if (!run || !draft) return;
    const units = (run.proposal?.units ?? run.pages.flatMap(p => p.proposal?.units ?? []))
      .filter(u => !this.state.staleUnits.includes(u.unitId) && !this.state.applied.includes(u.unitId));
    this.publish({ focus: analysisPreview(run, units, draft.layout as LayoutDefinition, this.journal.create?.binding, "proposal", cellId) });
  }
  async merge(unitIds: string[]) {
    const run = this.state.run;
    if (this.state.busy || this.state.storageBlocked || this.state.recovery || !run?.proposal || !this.journal.owner || !unitIds.length) return;
    if (unitIds.some(id => this.state.staleUnits.includes(id))) {
      this.publish({ focus: null, message: "枠の変更後は再解析してください。手動の下書きを保持しています。" }); return;
    }
    this.mark("merge_requested", undefined, { unitIds });
    this.publish({ busy: true, focus: null });
    try {
      const owner = this.workbench.getOwner();
      if (owner.sessionId !== this.journal.owner.sessionId) throw new Error("編集セッションが変わりました。旧案は反映せず、再解析してください。");
      const context = await this.workbench.refreshContext();
      const draft = this.editor.read(); if (!draft) throw new Error("入力を終了してから案を反映してください。");
      const pending: PendingMerge = { owner, analysisId: run.analysisId, manualSerial: this.journal.serial, body: { contractVersion: "template-analysis.v3",
        requestId: crypto.randomUUID(), binding: bindingOf(context),
        draft, parentMergeId: this.journal.mergeIds.at(-1) ?? null,
        unitDigests: Object.fromEntries(run.proposal.units.filter(u => unitIds.includes(u.unitId)).map(u => [u.unitId, u.proposalDigest!])),
        selectedUnitIds: unitIds, manualTouchedFacets: this.touches(draft, this.journal.analysisSerial ?? -1) } };
      this.journal.pendingMerge = pending; this.remember(); await this.sendMerge(pending);
    } catch (e) { this.failed(e); } finally { this.publish({ busy: false }); }
  }
  private async sendMerge(pending: PendingMerge) {
    const result = pending.result ?? await client.merge(pending.owner, pending.analysisId, pending.body);
    pending.result = result; this.remember();
    const owner = this.workbench.getOwner();
    if (owner.sessionId !== pending.owner.sessionId || owner.token !== pending.owner.token ||
      JSON.stringify(bindingOf(this.workbench.getContext())) !== JSON.stringify(pending.body.binding)) {
      this.journal.pendingMerge = null; this.remember();
      this.publish({ recovery: false, message: "編集対象が変わりました。旧対象の案は反映せず、下書きを保持しています。" }); return;
    }
    if (pending.manualSerial !== this.journal.serial || !this.editor.apply(pending.body.draft, result.draft)) {
      // A late reply cannot replace typing that occurred while merge was in flight.
      await client.revert(pending.owner, pending.analysisId, result.mergeId, { contractVersion: "template-analysis.v3", requestId: `discard_${pending.body.requestId}`, binding: pending.body.binding });
      this.journal.pendingMerge = null; this.remember();
      this.publish({ recovery: false, message: "反映待ちの間に入力が変わりました。手動の下書きを保持し、旧反映記録を取り消しました。" }); return;
    }
    this.journal.mergeIds.push(result.mergeId); this.journal.pendingMerge = null;
    this.mark("applied", undefined, { unitIds: result.appliedUnitIds });
    this.journal.applied = [...this.state.applied, ...result.appliedUnitIds];
    this.journal.staged = { input: pending.body.draft, output: result.draft, mergeId: result.mergeId, unitIds: result.appliedUnitIds, manualSerial: this.journal.serial };
    const remembered = this.tryRemember();
    this.publish({ applied: this.journal.applied, conflicts: result.conflicts.map(c => c.unitId), recovery: false,
      message: !remembered ? this.state.message : `下書きへ${result.appliedUnitIds.length}件反映しました。${result.conflicts.length ? `競合${result.conflicts.length}件は保留しました。` : ""}確認して保存してください。`,
      focus: this.state.run ? analysisPreview(this.state.run,
        this.state.run.proposal?.units.filter(u => result.appliedUnitIds.includes(u.unitId)) ?? [],
        result.draft.layout as LayoutDefinition, this.journal.create?.binding, "draft") : null });
  }
  async undoDraft() {
    const staged = this.journal.staged;
    if (!staged || this.state.busy || this.state.storageBlocked) return;
    const matches = this.editor.matches?.(staged.output) ?? (JSON.stringify(this.editor.read()) === JSON.stringify(staged.output));
    if (staged.manualSerial !== this.journal.serial || !matches) {
      this.publish({ message: "反映後の手修正を保持しています。AI反映を戻す前に現在の下書きを確認してください。" }); return;
    }
    if (!this.journal.owner || !this.journal.analysisId || !this.journal.create) return;
    this.publish({ busy: true });
    try {
      await client.revert(this.journal.owner, this.journal.analysisId, staged.mergeId, { contractVersion: "template-analysis.v3", requestId: `undo_${staged.mergeId}`, binding: this.journal.create.binding });
      this.journal.mergeIds = this.journal.mergeIds.filter(id => id !== staged.mergeId);
      this.journal.applied = this.state.applied.filter(id => !staged.unitIds.includes(id));
      delete this.journal.staged;
      if (staged.manualSerial !== this.journal.serial || !this.editor.apply(staged.output, staged.input, "undo")) {
        this.touch([facetTouch("@layout", "geometry")]);
        this.publish({ applied: this.journal.applied, recovery: false, message: "取り消し中の手修正を保持しました。旧AI案を失効させたため、現在の下書きを確認して保存してください。" }); return;
      }
    if (changedFacets(staged.output.layout as LayoutDefinition, staged.input.layout as LayoutDefinition).some(t => t.facet === "geometry")) {
      this.touch([facetTouch("@layout", "geometry")]);
    }
    const remembered = this.tryRemember();
    this.publish({ applied: this.journal.applied, focus: null, conflicts: [], message: remembered ? "直前のAI反映を戻しました。まだ保存していません。" : this.state.message });
    } catch (e) { this.publish({ recovery: true, message: messageOf(e) }); }
    finally { this.publish({ busy: false }); }
  }
  finish() {
    if (this.state.busy || this.state.storageBlocked || analysisRunning(this.state.run) || this.state.recovery || this.journal.mergeIds.length) return;
    this.disconnect(); this.journal.create = null; this.journal.analysisId = null; this.journal.held = false;
    if (!this.tryRemember()) return;
    this.editor.resume(); this.publish({ held: false, focus: null, run: null, message: "解析を終了しました。手動編集へ戻りました。" });
  }
}
