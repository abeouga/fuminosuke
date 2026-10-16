import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { analysisMergeAvailable, analysisRunning, type TemplateAnalysisStore } from "./templateAnalysisStore";
import type { PageScope } from "./template-analysis.generated";
import { analysisControlVisibility } from "./analysisControlVisibility";
import "./TemplateAnalysisPanel.css";

const roles: Record<string, string> = { ignored: "除外", input: "記入欄", label: "項目名", parent_label: "見出し", mark: "選択欄", record_key_cell: "行の識別欄", column_key_cell: "列の識別欄" };
const kinds = { role: "分類", field: "単体欄", table: "表", heading: "見出し", structure: "矩形と意味付け" };
const states = { queued: "受付済み", running: "解析中", validating: "検証中", ready: "案の確認", partial: "一部の案あり", failed: "解析失敗", cancelled: "中止済み", expired: "有効期限切れ", stale: "対象変更で失効" };
const reasons: Record<string, string> = { protected_existing: "既存設定を保護", ambiguous_role: "分類が曖昧", ambiguous_binding: "結合先が曖昧", ambiguous_parent: "見出しが曖昧", missing_evidence: "判断材料不足", missing_binding: "結合先未指定", unsupported_mark_definition: "選択欄の定義が必要", unsupported_structure: "手動設定が必要" };
const pageState = (runState: string, state: string) => state === "ready" ? "検証済み" : state === "partial" ? "一部の案あり" : state === "failed" ? "失敗"
  : runState === "cancelled" ? "中止" : ["failed", "expired", "stale"].includes(runState) ? "終了"
  : state === "running" ? "解析中" : "待機";
const rectangle = (r: { x: number; y: number; width: number; height: number }) => `位置 ${r.x}, ${r.y} / 大きさ ${r.width} × ${r.height}`;

export function TemplateAnalysisPanel({ store, pageScope, multiplePages, blocked, onSave, saving, structureAvailable = false }: {
  store: TemplateAnalysisStore; pageScope: PageScope; multiplePages: boolean; blocked: boolean; onSave(): void; saving: boolean; structureAvailable?: boolean;
}) {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const [allPages, setAllPages] = useState(false), [release, setRelease] = useState(false), [task, setTask] = useState<"full" | "roles">("full");
  const [selected, setSelected] = useState<string[]>([]);
  const panel = useRef<HTMLElement>(null);
  const reveal = useRef({ attempted: "", userMoved: true });
  const run = state.run, proposal = run?.proposal, running = analysisRunning(run);
  const units = [...(proposal?.units ?? run?.pages.flatMap(p => p.proposal?.units ?? []) ?? [])].sort((a, b) =>
    (run?.pages.findIndex(p => p.pageId === a.pageId) ?? 0) - (run?.pages.findIndex(p => p.pageId === b.pageId) ?? 0) ||
    (a.displayOrder ?? Number.MAX_SAFE_INTEGER) - (b.displayOrder ?? Number.MAX_SAFE_INTEGER));
  const unresolved = proposal?.unresolved ?? run?.pages.flatMap(p => p.proposal?.unresolved ?? []) ?? [];
  const eligible = units.filter(u => u.state === "mergeable" && !state.applied.includes(u.unitId) && !state.staleUnits.includes(u.unitId)).map(u => u.unitId);
  useEffect(() => { setSelected([]); }, [run?.analysisId]);
  const locked = blocked || state.busy || state.storageBlocked || saving;
  const mergeAvailable = !locked && !state.recovery && analysisMergeAvailable(run);
  const select = (ids: string[]) => { setSelected(ids); store.preview(ids); };
  useEffect(() => {
    const userMoved = () => { reveal.current.userMoved = true; };
    document.addEventListener("pointerdown", userMoved, true);
    document.addEventListener("keydown", userMoved, true);
    document.addEventListener("wheel", userMoved, true);
    document.addEventListener("touchstart", userMoved, true);
    return () => {
      document.removeEventListener("pointerdown", userMoved, true);
      document.removeEventListener("keydown", userMoved, true);
      document.removeEventListener("wheel", userMoved, true);
      document.removeEventListener("touchstart", userMoved, true);
    };
  }, []);
  useEffect(() => {
    let first = 0, second = 0;
    const measure = () => {
      const inputs = [...panel.current?.querySelectorAll<HTMLInputElement>("input[data-analysis-unit]") ?? []]
        .filter(input => eligible.includes(input.dataset.analysisUnit!) && !state.conflicts.includes(input.dataset.analysisUnit!));
      const preview = (input: HTMLInputElement) => analysisControlVisibility(input.closest("li")?.querySelector<HTMLElement>("[data-analysis-preview]") ?? null);
      const selectionControls = inputs.map(input => { const content = preview(input); return { unitId: input.dataset.analysisUnit!, enabled: input.isConnected && !input.disabled,
        ...analysisControlVisibility(input), previewVisible: content.visible && content.hitTest }; });
      store.painted?.(selectionControls.filter(c => c.visible && c.hitTest).map(c => c.unitId), false);
      if (!mergeAvailable || !run || !panel.current?.isConnected) return;
      const merge = panel.current.querySelector<HTMLButtonElement>("button[data-analysis-merge]");
      const mergeVisibility = analysisControlVisibility(merge);
      const target = inputs.find(input => !input.disabled), targetVisibility = analysisControlVisibility(target ?? null);
      const key = `${run.analysisId}:${target?.dataset.analysisUnit}`;
      const editing = document.activeElement?.matches("input:not([type=checkbox]):not([type=button]),textarea,select,[contenteditable=true]");
      if (target && !reveal.current.userMoved && !editing && !state.applied.length && reveal.current.attempted !== key &&
        (!targetVisibility.visible || !targetVisibility.hitTest || !preview(target).visible || !preview(target).hitTest || !mergeVisibility.visible || !mergeVisibility.hitTest)) {
        reveal.current.attempted = key;
        target.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" });
        schedule(); return;
      }
      store.painted?.(selectionControls.filter(c => c.enabled && c.visible && c.hitTest && c.previewVisible).map(c => c.unitId), true, {
        analysisId: run.analysisId, selectionControls,
        mergeControl: { present: !!merge?.isConnected, enabled: !!merge && !merge.disabled,
          requiresSelection: !!merge?.disabled && !selected.some(id => eligible.includes(id)), ...mergeVisibility },
      });
    };
    const schedule = () => { cancelAnimationFrame(first); cancelAnimationFrame(second); first = requestAnimationFrame(() => { second = requestAnimationFrame(measure); }); };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    if (panel.current) observer?.observe(panel.current);
    observer?.observe(document.body);
    document.addEventListener("scroll", schedule, true);
    document.addEventListener("toggle", schedule, true);
    document.addEventListener("transitionend", schedule, true);
    document.addEventListener("visibilitychange", schedule);
    document.addEventListener("focusin", schedule);
    document.addEventListener("focusout", schedule);
    window.addEventListener("resize", schedule);
    schedule();
    return () => {
      cancelAnimationFrame(first); cancelAnimationFrame(second); observer?.disconnect();
      document.removeEventListener("scroll", schedule, true); document.removeEventListener("toggle", schedule, true);
      document.removeEventListener("transitionend", schedule, true); document.removeEventListener("visibilitychange", schedule);
      document.removeEventListener("focusin", schedule); document.removeEventListener("focusout", schedule); window.removeEventListener("resize", schedule);
    };
  }, [store, run, mergeAvailable, state.conflicts, selected, eligible.join(","), state.applied.length]);
  return <section ref={panel} className="template-analysis" aria-label="AIによる帳票の編集案">
    <div className="template-analysis-actions">
      <button className="secondary" disabled={locked || running || state.recovery || state.applied.length > 0} onClick={() => { reveal.current = { attempted: "", userMoved: false }; setSelected([]); void store.start(allPages ? { mode: "all", pageId: null } : pageScope, task, release); }}>AI解析</button>
      {running && <button className="secondary" disabled={state.busy} onClick={() => void store.cancel()}>解析を中止</button>}
      {state.held && <button className="secondary" disabled={locked || running || state.recovery || state.applied.length > 0} onClick={() => store.finish()}>手動編集に戻る</button>}
      <details><summary>解析の設定</summary><div className="template-analysis-options">
        <label>解析内容<select value={task} disabled={locked || running} onChange={e => setTask(e.target.value as "full" | "roles")}><option value="full">{structureAvailable ? "枠・分類・意味・表" : "分類・意味・表"}</option><option value="roles">分類のみ</option></select></label>
        {multiplePages && <label><input type="checkbox" checked={allPages} disabled={locked || running} onChange={e => setAllPages(e.target.checked)} />全ページを解析</label>}
        <label><input type="checkbox" checked={release} disabled={locked || running} onChange={e => setRelease(e.target.checked)} />既存の分類も見直す</label>
        <small>既存の意味・表・読取設定は保護します。解析後の手修正は上書きしません。</small>
      </div></details>
    </div>
    <p role="status">{run ? `${states[run.state]} · ${run.pages.length}ページ` : pageScope.mode === "current" ? "対象: 表示中のページ" : "対象: テンプレート全体"}{state.held && " · 自動保存を保留中"}</p>
    {state.message && <p className="template-analysis-message" aria-live="polite">{state.message}</p>}
    {run && !running && !eligible.length && !state.applied.length && !state.recovery && <p className="notice compact">反映できるAI案はありません。「手動編集に戻る」から欄を編集・保存できます。</p>}
    {state.recovery && <p className="notice compact">処理結果が未確認です。入力を保持しています。「結果を再取得」で保存状態を確認してください。</p>}
    {!!state.staleUnits.length && <p className="template-analysis-stale" role="status">枠が変更されたため、旧案は反映できません。現在の下書きで再解析してください。</p>}
    {(state.storageBlocked || state.recovery || run) && <button className="secondary compact" disabled={state.busy} onClick={() => void store.recover()}>結果を再取得</button>}
    {!!run && <div className="template-analysis-pages">{run.pages.map((page, i) => <span key={page.pageId ?? "single"}>ページ{i + 1}: {pageState(run.state, page.state)}{page.error && `（${page.error.message}）`}</span>)}</div>}
    {run?.regionScope && <p role="status">指定領域のみを解析しています。ページ全体の完了ではありません。</p>}
    {!!units.length && <>
      <div className="template-analysis-actions">
        <button className="secondary" data-analysis-merge disabled={!mergeAvailable || !selected.some(id => eligible.includes(id))} onClick={() => void store.merge(selected.filter(id => eligible.includes(id)))}>案を反映</button>
        {state.applied.length > 0 && <button className="primary" disabled={locked} onClick={onSave}>確認して保存</button>}
        {state.applied.length > 0 && <button className="secondary" disabled={locked} onClick={() => store.undoDraft()}>直前のAI反映を戻す</button>}
        <button className="secondary compact" disabled={locked || !eligible.length} onClick={() => select(eligible)}>反映可能な案をすべて選択</button>
        <button className="secondary compact" disabled={!selected.length} onClick={() => select([])}>選択を解除</button>
        <span>未解決 {unresolved.length}件 / 保護 {units.filter(u => u.state === "protected").length}件</span>
      </div>
      {units.some(u => u.geometry) && <p className="template-analysis-legend"><span>緑の枠: 追加</span><span>青の枠: 変更後</span><span>灰色の破線: 変更前</span><span>茶色の破線: 除去</span>案を選択すると原画像に重ねて表示します。まだ保存しません。</p>}
      <ul className="template-analysis-units">{units.map(unit => {
        const applied = state.applied.includes(unit.unitId), conflict = state.conflicts.includes(unit.unitId), stale = state.staleUnits.includes(unit.unitId);
        return <li key={unit.unitId}>
          <label><input type="checkbox" data-analysis-unit={unit.unitId} checked={selected.includes(unit.unitId)} disabled={locked || applied || stale || unit.state !== "mergeable"} onChange={e => select(e.target.checked ? [...selected, unit.unitId] : selected.filter(id => id !== unit.unitId))} /><strong>{unit.name}</strong><span>{kinds[unit.kind]} · {unit.cellIds.length}セル{unit.pageId && ` · ${unit.pageId}`}</span></label>
          <span>{applied ? "下書きへ反映済み" : stale ? "枠変更で失効・再解析が必要" : conflict ? "手修正と競合・保留" : unit.state === "protected" ? unit.reason === "dependent_semantics_unresolved" ? "枠と意味の依存関係が未解決" : reasons[unit.reason ?? ""] ?? "既存設定を保護" : "反映可能"}</span>
          <small data-analysis-preview className="template-analysis-summary">{unit.details?.slice(0, 2).join(" / ") ||
            unit.roleChanges?.slice(0, 2).map(c => `${c.cellId}: ${roles[c.before] ?? c.before} → ${roles[c.after] ?? c.after}`).join(" / ") ||
            (unit.geometry ? `枠の追加${unit.geometry.addedIds.length}件・変更${unit.geometry.changedIds.length}件・除去${unit.geometry.removedIds.length}件` : unit.name)}</small>
          <details className="template-analysis-detail"><summary>変更内容を確認</summary>
          {unit.geometry && <details className="template-analysis-geometry"><summary>枠の差分：追加{unit.geometry.addedIds.length}・変更{unit.geometry.changedIds.length}・除去{unit.geometry.removedIds.length}</summary>
            <ul>{unit.geometry.before.map(before => {
              const after = unit.geometry!.after.find(c => c.id === before.id);
              return <li key={before.id}>{after ? "変更" : "除去"}: {before.id}<small>{rectangle(before)}{after && ` → ${rectangle(after)}`}</small><button className="secondary compact" disabled={locked || stale || applied} onClick={() => store.previewCell(before.id)}>この枠を表示</button></li>;
            })}{unit.geometry.after.filter(c => unit.geometry!.addedIds.includes(c.id)).map(c => <li key={c.id}>追加: {c.id}<small>{rectangle(c)}</small><button className="secondary compact" disabled={locked || stale || applied} onClick={() => store.previewCell(c.id)}>この枠を表示</button></li>)}</ul>
          </details>}
          {(unit.roleChanges ?? []).map(change => <small key={`${change.cellId}:${change.after}`}>{change.cellId}: {roles[change.before] ?? change.before} → {roles[change.after] ?? change.after}</small>)}
          {(unit.details ?? []).map((detail, i) => <small key={i}>{detail}</small>)}
          </details>
        </li>;
      })}</ul>
    </>}
    {!!unresolved.length && <details><summary>未解決の欄（{unresolved.length}件）</summary><ul>{unresolved.map((u, i) => <li key={i}>{u.cellId || "未検出の領域"}: {u.reason === "cross_region" ? "範囲をまたぐため再指定が必要" : u.reason === "manual_required" ? "手動設定が必要" : reasons[u.reason] ?? "解析できませんでした。手動で設定してください。"} <button className="secondary compact" disabled={locked || !!state.staleUnits.length} onClick={() => u.bounds ? store.previewUnresolved(u.cellId, u.bounds) : store.previewUnresolved(u.cellId)}>位置を表示</button></li>)}</ul></details>}
  </section>;
}
