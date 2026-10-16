import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { FiMic, FiSquare } from "react-icons/fi";
import { editingRequest } from "../editing/client";
import type { PageScope } from "../editing/generated";
import type { EditingStore, SavedResource } from "../editing/store";
import { ConversationAudio } from "./ConversationAudio";
import "./assistant.css";

type Props = { store: EditingStore; pageScope: PageScope; selectedEntities: string[]; selectionEpoch: number;
  manualActive: boolean; disabled?: string | null; onSaved: (value: SavedResource) => void | Promise<void>;
  onTargets?: (targets: Array<{ entityId: string; kind: string; pageId: string | null }>, cue: number) => void };
type VoiceStatus = { configured: boolean; localAvailable: boolean; calls: { remaining: number }; secondsRemaining: number };
const LABEL = { LISTENING: "", RESOLVING: "指示を解析しています。", COMMITTING: "変更を保存しています。", NOTIFYING: "", CLARIFYING: "", RECOVERING: "保存結果を確認してください。", STOPPED: "" };

export function ConversationControls(props: Props) {
  const latest = useRef(props); latest.current = props;
  const [cue, setCue] = useState(0);
  const guidanceGeneration = useRef(0), mounted = useRef(false);
  const guidanceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const audio = useMemo(() => new ConversationAudio(props.store, async saved => {
    await latest.current.onSaved(saved);
    const handles = saved.result.changedTargets.map(target => target.handle).filter((handle): handle is string => Boolean(handle));
    if (handles.length && !["STOPPED", "RECOVERING"].includes(audio.conversation.getSnapshot().state)) {
      // Visual guidance must not delay the durable save notification.
      void highlight(handles).catch(() => undefined);
    }
  }), [props.store]);
  const state = useSyncExternalStore(audio.subscribe, audio.getSnapshot);
  const conversation = useSyncExternalStore(audio.conversation.subscribe, audio.conversation.getSnapshot);
  const [status, setStatus] = useState<VoiceStatus | null>(null), [error, setError] = useState("");
  const scope = JSON.stringify(props.pageScope), selection = JSON.stringify([props.selectedEntities, props.selectionEpoch]);
  const previousSelection = useRef(selection);
  const refresh = () => { void editingRequest<VoiceStatus>("/voice/status").then(setStatus).catch(reason => setError(reason instanceof Error ? reason.message : "音声設定を取得できません。")); };
  useEffect(() => {
    mounted.current = true;
    refresh();
    const visibility = () => { if (document.visibilityState !== "visible") void audio.stop(); };
    const pagehide = () => { void audio.stop(); };
    document.addEventListener("visibilitychange", visibility); window.addEventListener("pagehide", pagehide);
    return () => {
      mounted.current = false; guidanceGeneration.current += 1;
      if (guidanceTimer.current) clearTimeout(guidanceTimer.current);
      latest.current.onTargets?.([], 0);
      document.removeEventListener("visibilitychange", visibility); window.removeEventListener("pagehide", pagehide); void audio.stop();
    };
  }, [audio, scope]);
  useEffect(() => { audio.mount(); return () => audio.unmount(); }, [audio]);
  useEffect(() => { props.store.setInteraction(props.manualActive); }, [props.store, props.manualActive]);
  useEffect(() => {
    if (selection === previousSelection.current || props.manualActive || state.mode !== "continuous") return;
    previousSelection.current = selection;
    void audio.selected(props.selectedEntities, props.selectionEpoch).catch(reason => setError(reason instanceof Error ? reason.message : "選択を取得できません。"));
  }, [audio, props.manualActive, selection, state.mode]);
  useEffect(() => { if (props.disabled) void audio.stop(); }, [audio, props.disabled]);
  const active = state.mode !== "stopped", disabled = Boolean(props.disabled || !status?.configured || status.calls.remaining < 1);
  function clearGuidance() {
    guidanceGeneration.current += 1;
    if (guidanceTimer.current) clearTimeout(guidanceTimer.current);
    guidanceTimer.current = null;
    setCue(0); latest.current.onTargets?.([], 0);
  }
  async function highlight(handles: string[]) {
    // A replacement lookup may fail or become stale. Remove the previous cue
    // before cancelling its expiry, otherwise it can remain highlighted forever.
    clearGuidance();
    const generation = ++guidanceGeneration.current;
    const binding = JSON.stringify([latest.current.pageScope, latest.current.selectedEntities, latest.current.selectionEpoch]);
    const owner = audio.conversation.getOwner(), context = audio.conversation.getContext();
    const result = await editingRequest<{ targets: Array<{ entityId: string; kind: string; pageId: string | null }> }>(`/sessions/${owner.sessionId}/ui-targets`, owner,
      { contextRevision: context.contextRevision, resourceRevision: context.resourceRevision, entityIds: [], handles }, "POST");
    if (!mounted.current || generation !== guidanceGeneration.current || latest.current.manualActive ||
      binding !== JSON.stringify([latest.current.pageScope, latest.current.selectedEntities, latest.current.selectionEpoch])) return;
    const currentOwner = audio.conversation.getOwner(), current = audio.conversation.getContext();
    if (currentOwner.sessionId !== owner.sessionId || current.contextRevision !== context.contextRevision ||
      current.resourceRevision !== context.resourceRevision || current.localEditEpoch !== context.localEditEpoch) return;
    setCue(generation); latest.current.onTargets?.(result.targets, generation);
    guidanceTimer.current = setTimeout(() => { if (mounted.current && generation === guidanceGeneration.current) clearGuidance(); }, 3000);
  }
  useEffect(() => {
    if (conversation.candidates.length) void highlight(conversation.candidates.map(target => target.handle)).catch(() => undefined);
    else if (["RESOLVING", "STOPPED", "RECOVERING"].includes(conversation.state)) clearGuidance();
  }, [audio, conversation.candidates, conversation.state]);
  useEffect(() => { clearGuidance(); }, [audio, scope, selection, props.manualActive, props.disabled]);
  return <section className="conversation-controls" aria-label="音声による編集">
    <div className="conversation-actions">
      <span className="conversation-guide" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 3v3M9 3h6M4 11H2v5h2m16-5h2v5h-2" />
          <rect x="4" y="6" width="16" height="14" rx="3" />
          <path d="M8 11v3m8-3v3M9 17h6" />
        </svg>
        <span key={cue} className={`conversation-guide-indicator${cue ? " is-active" : ""}`} />
      </span>
      {active ? <button className="secondary" onClick={() => void audio.stop().finally(refresh)}><FiSquare />音声入力を停止</button>
        : <button className="secondary" disabled={disabled || props.manualActive || !status || status.secondsRemaining < 1} onClick={() => { setError(""); previousSelection.current = selection; void audio.startContinuous(props.pageScope, props.selectedEntities, props.selectionEpoch); }}><FiMic />音声入力</button>}
      {state.mode === "local" ? <button className="secondary" onClick={() => void audio.finishLocal().finally(refresh)}>録音を終了して送信</button>
        : props.selectedEntities.length > 0 && <button className="secondary" disabled={active || disabled || props.manualActive || !status?.localAvailable} onClick={() => { setError(""); void audio.startLocal(props.pageScope, props.selectedEntities, props.selectionEpoch); }}><FiMic />選択欄を録音</button>}
      {conversation.state === "RECOVERING" && <button className="secondary" onClick={() => void audio.conversation.recover().catch(reason => setError(reason instanceof Error ? reason.message : "保存結果を取得できません。"))}>保存結果を再取得</button>}
    </div>
    <p className="conversation-status" role="status">{props.disabled ?? (error || conversation.message || state.message || LABEL[conversation.state])}</p>
    {state.caption && <p className="conversation-caption" aria-label="音声字幕">{state.caption}</p>}
    {conversation.candidates.length > 0 && <p key={cue} className={`conversation-candidates${cue ? " is-highlighted" : ""}`}>{conversation.candidates.map(target => target.recordCell
      ? `${target.recordCell.tableName}・${target.recordCell.rowNumber}行目・${target.recordCell.columnNumber}列目（${target.recordCell.columnName}）`
      : target.name).join(" / ")}</p>}
  </section>;
}
