import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { FiMic, FiSquare, FiRotateCcw } from "react-icons/fi";
import { v2client, type EditingStatus } from "./v2client";
import { VoiceCapture } from "./voiceCapture";
import type { PageScope } from "./v2.generated";
import type { SavedResource, WorkbenchStore } from "./workbenchStore";
import "./editing.css";

type Props = { store: WorkbenchStore; pageScope: PageScope; selectedEntities: string[]; selectionEpoch: number; manualActive: boolean; holdSelectionContext?: boolean; disabled?: string | null; onSaved: (saved: SavedResource) => void | Promise<void> };
export function EditingControls(props: Props) {
  const latest = useRef(props); latest.current = props;
  const audio = useMemo(() => new VoiceCapture(props.store), [props.store]);
  const state = useSyncExternalStore(props.store.subscribe, props.store.getSnapshot);
  const voice = useSyncExternalStore(audio.subscribe, audio.getSnapshot);
  const [capabilities, setCapabilities] = useState<EditingStatus | null>(null);
  const [error, setError] = useState(""), [text, setText] = useState("");
  const run = (work: () => Promise<unknown>) => { setError(""); void work().catch(e => setError(e instanceof Error ? e.message : "処理を完了できません。")); };
  const scopeKey = JSON.stringify(props.pageScope), selectedKey = JSON.stringify([props.selectedEntities, props.selectionEpoch]);
  useEffect(() => {
    const detach = props.store.connectEvents(), saved = props.store.onSaved(value => latest.current.onSaved(value));
    const stop = props.store.subscribeInteraction(() => { void audio.stop(); });
    let live = true;
    void v2client.status().then(s => { if (live) setCapabilities(s); }).catch(e => { if (live) setError(String(e)); });
    const hide = () => { if (document.hidden) void audio.stop(); };
    const leave = () => { void audio.stop(); };
    document.addEventListener("visibilitychange", hide); window.addEventListener("pagehide", leave);
    window.addEventListener("fuminosuke-auth-required", leave);
    return () => { live = false; detach(); saved(); stop(); void audio.stop(); document.removeEventListener("visibilitychange", hide); window.removeEventListener("pagehide", leave); window.removeEventListener("fuminosuke-auth-required", leave); };
  }, [audio, props.store]);
  useEffect(() => { props.store.setInteraction(props.manualActive); }, [props.store, props.manualActive]);
  const previousScope = useRef(scopeKey);
  useEffect(() => {
    if (previousScope.current !== scopeKey) run(() => props.store.selectionChanged(props.pageScope, props.selectedEntities));
    previousScope.current = scopeKey;
  }, [audio, scopeKey]);
  const previousSelection = useRef(selectedKey);
  useEffect(() => {
    if (!props.holdSelectionContext && selectedKey !== previousSelection.current && (props.store.resourceKind === "template" || voice.mode === "local")) run(() => props.store.selectionChanged(props.pageScope, props.selectedEntities));
    previousSelection.current = selectedKey;
  }, [audio, selectedKey, voice.mode, props.store, props.holdSelectionContext]);
  useEffect(() => { if (props.disabled) void audio.stop(); }, [audio, props.disabled]);
  const tables = new Map<string, string>();
  for (const target of state.context?.catalog.targets ?? []) {
    if (target.recordCell) tables.set(target.recordCell.tableId, target.recordCell.tableName);
    if (target.kind === "record_section") {
      const binding = state.context?.uiBindings.find(b => b.handle === target.handle);
      if (binding) tables.set(binding.entityId, target.name);
    }
  }
  const table = state.context?.scopeContext.selectedTableId ?? null;
  const active = voice.mode !== "stopped", halted = !["ready", "saving"].includes(state.state);
  const blocked = Boolean(props.disabled || props.manualActive || halted || !state.context);
  const configured = capabilities?.enabled && capabilities.voiceConnected;
  const plannerAvailable = capabilities?.enabled && capabilities.plannerConnected;
  async function submit(value: string | { choiceId: string }) {
    if (!state.question) await props.store.prepareInstruction(props.pageScope, props.selectedEntities);
    await props.store.instruction(value); setText("");
  }
  return <section className="editing-controls" aria-label="音声による編集">
    <div className="editing-actions">
      {active ? <button className="secondary" onClick={() => void audio.stop()}><FiSquare />音声入力を停止</button>
        : <button className="secondary" disabled={blocked || !configured} onClick={() => run(() => audio.start("continuous", props.pageScope, props.selectedEntities, table))}><FiMic />音声入力</button>}
      {voice.mode === "local" ? <button className="secondary" onClick={() => audio.finishLocal()}>録音を終了して送信</button>
        : <button className="secondary" disabled={blocked || !configured || active || props.selectedEntities.length !== 1} onClick={() => run(() => audio.start("local", props.pageScope, props.selectedEntities, table))}>選択欄を録音</button>}
      <label className="editing-scope">対象表<select aria-label="音声編集の対象表" value={table ?? ""} disabled={blocked || state.pending > 0} onChange={e => run(() => props.store.changeScope(e.target.value || null))}>
        <option value="">自動（直近の表を優先）</option>{[...tables].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select></label>
      <button className="secondary" disabled={blocked || !state.canUndo || state.pending > 0} onClick={() => run(async () => { const saved = await props.store.undoResource(); await latest.current.onSaved(saved); })}><FiRotateCcw />元に戻す</button>
    </div>
    <p className="editing-status" role="status">{props.disabled || error || (voice.message === "音声を受信・指示を整理中" ? voice.message : state.message || voice.message)}</p>
    {!error && <p className="editing-help">{capabilities === null ? "音声・AI編集の利用状態を確認しています。" : !configured
      ? "音声入力は現在利用できません。手動で編集・保存できます。"
      : "セルを選ばずに話せます。局所修正は欄を選んで録音できます。対象表の選択は任意です。"}</p>}
    {voice.caption && <p className="editing-caption" aria-label="認識中の発話">{voice.caption}</p>}
    {state.question && <div className="editing-question"><p>{state.question.message}</p><div className="editing-actions">{state.question.choices.map(choice => <button key={choice.choiceId} className="secondary" disabled={blocked} onClick={() => run(() => submit({ choiceId: choice.choiceId }))}>{choice.label}</button>)}<button className="secondary" onClick={() => run(() => props.store.cancelPending())}>この指示を中止</button></div></div>}
    {halted && <button className="secondary" onClick={() => run(async () => { const saved = await props.store.recoverResource(); await latest.current.onSaved(saved); })}>保存結果を再取得</button>}
    <details className="editing-text"><summary>文字で指示</summary>
      {capabilities && !plannerAvailable && <p>AIへの指示は現在利用できません。手動編集を続けてください。</p>}
      <form onSubmit={e => { e.preventDefault(); if (plannerAvailable && text.trim()) run(() => submit(text.trim())); }}>
      <textarea aria-label="編集指示" value={text} maxLength={20000} disabled={blocked || active || !plannerAvailable} onChange={e => setText(e.target.value)} rows={2} />
      <button className="secondary" disabled={blocked || active || !plannerAvailable || !text.trim() || state.pending >= 2}>送信</button>
    </form></details>
  </section>;
}
