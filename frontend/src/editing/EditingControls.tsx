import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { FiMic, FiSquare, FiRotateCcw } from "react-icons/fi";
import { v2client, type EditingStatus } from "./v2client";
import { VoiceCapture } from "./voiceCapture";
import { VoiceMeter } from "../components/VoiceMeter";
import { VoiceOverlay, type BubbleTone } from "./VoiceOverlay";
import type { AiAttachment } from "../components/ReviewAiDrag";
import type { PageScope } from "./v2.generated";
import type { SavedResource, WorkbenchStore } from "./workbenchStore";
import "./editing.css";

type Props = { store: WorkbenchStore; pageScope: PageScope; selectedEntities: string[]; selectionEpoch: number; manualActive: boolean; holdSelectionContext?: boolean; tableSelector?: boolean; voiceOnly?: boolean; textInstructions?: boolean; floatingAnchor?: string; attach?: { key: number; attachment: AiAttachment } | null; disabled?: string | null; onSaved: (saved: SavedResource) => void | Promise<void> };
export function EditingControls(props: Props) {
  const latest = useRef(props); latest.current = props;
  const audio = useMemo(() => new VoiceCapture(props.store), [props.store]);
  const state = useSyncExternalStore(props.store.subscribe, props.store.getSnapshot);
  const voice = useSyncExternalStore(audio.subscribe, audio.getSnapshot);
  const [capabilities, setCapabilities] = useState<EditingStatus | null>(null);
  const [error, setError] = useState(""), [text, setText] = useState("");
  const undoneAt = useRef(0), typedAt = useRef(-Infinity);
  const run = (work: () => Promise<unknown>) => { setError(""); audio.clearError(); void work().catch(e => setError(e instanceof Error ? e.message : "処理を完了できません。")); };
  const scopeKey = JSON.stringify(props.pageScope), selectedKey = JSON.stringify([props.selectedEntities, props.selectionEpoch]);
  useEffect(() => {
    const detach = props.store.connectEvents(), saved = props.store.onSaved(value => latest.current.onSaved(value));
    const stop = props.store.subscribeInteraction(() => { if (audio.getSnapshot().mode !== "stopped") void audio.stop("画面の操作があったため音声入力を止めました。続けるには「音声入力」を押してください。"); });
    // Choosing a table keeps voice on: pause before the scope change, resume after it.
    let resume = false;
    const scope = props.store.subscribeScope(async phase => {
      // "starting" counts too: a second click may land while voice is still restarting.
      if (phase === "before") { const mode = audio.getSnapshot().mode; resume = resume || mode === "continuous" || mode === "starting"; if (mode !== "stopped") await audio.stop(); return; }
      if (!resume) return;
      resume = false;
      const p = latest.current, table = p.store.getSnapshot().context?.scopeContext.selectedTableId ?? null;
      void audio.start("continuous", p.pageScope, p.selectedEntities, table);
    });
    let live = true;
    void v2client.status().then(s => { if (live) setCapabilities(s); }).catch(e => { if (live) setError(String(e)); });
    const hide = () => { if (document.hidden && audio.getSnapshot().mode !== "stopped") void audio.stop("ほかのタブやアプリに切り替えたため音声入力を止めました。"); };
    const leave = () => { void audio.stop(); };
    document.addEventListener("visibilitychange", hide); window.addEventListener("pagehide", leave);
    window.addEventListener("fuminosuke-auth-required", leave);
    return () => { live = false; detach(); saved(); stop(); scope(); void audio.stop(); document.removeEventListener("visibilitychange", hide); window.removeEventListener("pagehide", leave); window.removeEventListener("fuminosuke-auth-required", leave); };
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
  const remaining = capabilities?.voiceSecondsRemaining ?? null;
  const configured = capabilities?.enabled && capabilities.voiceConnected && (remaining === null || remaining >= 300);
  const plannerAvailable = capabilities?.enabled && capabilities.plannerConnected;
  async function recover() {
    const saved = await props.store.recoverResource();
    // A plain re-read (nothing was waiting) must not displace input the editor still holds.
    try { await latest.current.onSaved(saved); } catch (reason) { if (saved.result.operationId !== null) throw reason; }
  }
  async function submit(value: string | { choiceId: string }) {
    if (!state.question) await props.store.prepareInstruction(props.pageScope, props.selectedEntities);
    await props.store.instruction(value); setText("");
  }
  const help = !configured && capabilities !== null
    ? remaining !== null && remaining < 300 && capabilities.voiceConnected ? "本日の音声入力時間を使い切りました。明日になると再び使えます。手動で編集・保存できます。" : "音声入力は現在利用できません。手動で編集・保存できます。"
    : "";
  if (props.floatingAnchor) {
    const message = state.question ? "" : state.message || voice.message;
    // Spin only while a spoken instruction is being handled. Manual autosaves run quietly,
    // and a refused or unconfirmed save (kept pending until recovery) shows its error instead.
    const thinking = !halted && !state.question && (
      voice.message === "音声を受信・指示を整理中"
      || (state.pending > 0 && state.message === "指示を整理しています。")
    );
    const failure = error || voice.error || (halted ? state.message : "");
    const bubble: { key: string; tone: BubbleTone; text: string; sticky: boolean } | null = state.question
      ? { key: "q" + state.question.questionId, tone: "question", text: state.question.message, sticky: true }
      : failure ? { key: "e" + failure, tone: "error", text: failure, sticky: true }
        // Manual autosaves report on the page; the bubble speaks only while voice is in use.
        : message && !TRANSIENT.has(message) && (voice.mode !== "stopped" || performance.now() - undoneAt.current < 15000 || performance.now() - typedAt.current < 60000) ? { key: `m${state.commandId ?? ""}${message}`, tone: message.endsWith("しました。") ? "success" : "info", text: message, sticky: false }
          : null;
    return <VoiceOverlay anchorSelector={props.floatingAnchor}
      listening={voice.mode === "continuous" || voice.mode === "local"} starting={voice.mode === "starting"}
      speaking={voice.utterance} thinking={thinking && !state.question}
      canStart={!blocked && Boolean(configured)} canUndo={!blocked && state.canUndo && state.pending === 0}
      startHint={props.disabled || help || (blocked ? "手動の編集・保存が終わると使えます。" : "音声入力は準備中です。")}
      meter={audio.meter.read} caption={voice.caption} bubble={bubble}
      onStart={() => run(async () => { await audio.start("continuous", props.pageScope, props.selectedEntities, table); void v2client.status().then(setCapabilities).catch(() => undefined); })}
      onStop={() => void audio.stop()}
      onUndo={() => run(async () => { undoneAt.current = performance.now(); const saved = await props.store.undoResource(); await latest.current.onSaved(saved); })}
      attach={props.attach}
      textInput={props.textInstructions ? {
        enabled: !blocked && !active && Boolean(plannerAvailable) && state.pending < 2,
        hint: props.disabled || (capabilities && !plannerAvailable ? "AIへの指示は現在利用できません。" : active ? "音声入力を止めると文字で指示できます。" : blocked ? "手動の編集・保存が終わると使えます。" : "準備中です。"),
        onSubmit: async value => {
          setError(""); audio.clearError(); typedAt.current = performance.now();
          try { await submit(value); } catch (e) { setError(e instanceof Error ? e.message : "処理を完了できません。"); throw e; }
        },
      } : undefined}
      extra={state.question ? <div className="voice-bubble-choices">{state.question.choices.map(choice => <button key={choice.choiceId} type="button" className="secondary editing-choice" disabled={blocked} title={choice.label}
        onMouseEnter={() => props.store.previewTarget(choice.handle ?? null)} onMouseLeave={() => props.store.previewTarget(null)}
        onFocus={() => props.store.previewTarget(choice.handle ?? null)} onBlur={() => props.store.previewTarget(null)}
        onClick={() => { props.store.previewTarget(null); run(() => submit({ choiceId: choice.choiceId })); }}>{choiceLabel(choice.label)}</button>)}
        <button type="button" className="secondary" onClick={() => run(() => props.store.cancelPending())}>この指示を中止</button></div>
        : halted ? <div className="voice-bubble-choices"><button type="button" className="secondary" onClick={() => run(recover)}>保存結果を再取得</button></div>
          : undefined} />;
  }
  return <section className="editing-controls" aria-label="音声による編集">
    <div className="editing-actions">
      {active ? <><button className="secondary" onClick={() => void audio.stop()}><FiSquare />音声入力を停止</button><VoiceMeter read={audio.meter.read} active={voice.mode === "continuous" || voice.mode === "local"} speaking={voice.utterance} /></>
        : <button className="secondary" disabled={blocked || !configured} onClick={() => run(async () => { await audio.start("continuous", props.pageScope, props.selectedEntities, table); void v2client.status().then(setCapabilities).catch(() => undefined); })}><FiMic />音声入力</button>}
      {props.voiceOnly ? null : voice.mode === "local" ? <button className="secondary" onClick={() => audio.finishLocal()}>録音を終了して送信</button>
        : <button className="secondary" disabled={blocked || !configured || active || props.selectedEntities.length !== 1} onClick={() => run(() => audio.start("local", props.pageScope, props.selectedEntities, table))}>選択欄を録音</button>}
      {props.tableSelector !== false && <label className="editing-scope">対象表<select aria-label="音声編集の対象表" value={table ?? ""} disabled={blocked || state.pending > 0} onChange={e => run(() => props.store.changeScope(e.target.value || null))}>
        <option value="">自動（直近の表を優先）</option>{[...tables].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select></label>}
      <button className="secondary" disabled={blocked || !state.canUndo || state.pending > 0} onClick={() => run(async () => { const saved = await props.store.undoResource(); await latest.current.onSaved(saved); })}><FiRotateCcw />元に戻す</button>
    </div>
    <p className={voice.error && !error && !props.disabled ? "editing-status is-error" : "editing-status"} role="status">{props.disabled || error || voice.error || (state.question ? "" : (voice.utterance || (active && voice.message !== "音声入力中です。") ? voice.message : state.message || voice.message))}</p>
    {!error && capabilities !== null && (!configured || (remaining !== null && remaining < 1800)) && <p className="editing-help">{!configured
      ? remaining !== null && remaining < 300 && capabilities.voiceConnected ? "本日の音声入力時間を使い切りました。明日になると再び使えます。手動で編集・保存できます。" : "音声入力は現在利用できません。手動で編集・保存できます。"
      : `本日の音声入力の残りは約${Math.floor((remaining ?? 0) / 60)}分です。`}</p>}
    {voice.caption && <p className="editing-caption" aria-label="認識中の発話">{voice.caption}</p>}
    {state.question && <div className="editing-question"><p>{state.question.message}</p><div className="editing-actions">{state.question.choices.map(choice => <button key={choice.choiceId} className="secondary editing-choice" disabled={blocked} title={choice.label}
      onMouseEnter={() => props.store.previewTarget(choice.handle ?? null)} onMouseLeave={() => props.store.previewTarget(null)}
      onFocus={() => props.store.previewTarget(choice.handle ?? null)} onBlur={() => props.store.previewTarget(null)}
      onClick={() => { props.store.previewTarget(null); run(() => submit({ choiceId: choice.choiceId })); }}>{choiceLabel(choice.label)}</button>)}<button className="secondary" onClick={() => run(() => props.store.cancelPending())}>この指示を中止</button></div></div>}
    {halted && <button className="secondary" onClick={() => run(recover)}>保存結果を再取得</button>}
    {!props.voiceOnly && <details className="editing-text"><summary>文字で指示</summary>
      {capabilities && !plannerAvailable && <p>AIへの指示は現在利用できません。手動編集を続けてください。</p>}
      <form onSubmit={e => { e.preventDefault(); if (plannerAvailable && text.trim()) run(() => submit(text.trim())); }}>
      <textarea aria-label="編集指示" value={text} maxLength={20000} disabled={blocked || active || !plannerAvailable} onChange={e => setText(e.target.value)} rows={2} />
      <button className="secondary" disabled={blocked || active || !plannerAvailable || !text.trim() || state.pending >= 2}>送信</button>
    </form></details>}
  </section>;
}

/** Progress messages are shown as the listening/thinking animation, not as a result bubble. */
const TRANSIENT = new Set(["音声入力中です。", "聞き取り中…", "音声接続を準備しています。", "指示を整理しています。", "変更を保存しています。", "音声を受信・指示を整理中"]);

/** 「売却子牛一覧・1行目・3列目 体重」 → 「1行目の体重」 with the table as a small note. */
function choiceLabel(label: string) {
  const cell = /^(.+)・(\d+)行目・\d+列目 (.+)$/.exec(label);
  return cell ? <>{cell[2]}行目の{cell[3]}<small className="editing-choice-table">{cell[1]}</small></> : label;
}
