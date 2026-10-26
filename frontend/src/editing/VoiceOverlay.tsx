import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { FiMessageSquare, FiMic, FiMove, FiPaperclip, FiRotateCcw, FiSend, FiSquare, FiX } from "react-icons/fi";
import { InstructionField, type InstructionFieldHandle } from "./InstructionField";
import { VoiceMeter } from "../components/VoiceMeter";
import { useCornerPin } from "../components/useCornerPin";
import { useAiDropTarget, type AiAttachment } from "../components/ReviewAiDrag";

export type { Corner } from "../components/useCornerPin";

const STORAGE_KEY = "fuminosuke.voiceOverlayCorner";

export type BubbleTone = "info" | "success" | "error" | "question";
export type VoiceOverlayProps = {
  anchorSelector: string;
  listening: boolean; starting: boolean; speaking: boolean; thinking: boolean;
  canStart: boolean; canUndo: boolean; startHint: string;
  meter: () => number;
  onStart: () => void; onStop: () => void; onUndo: () => void;
  bubble: { key: string; tone: BubbleTone; text: string; sticky: boolean } | null;
  caption: string;
  extra?: ReactNode;
  /** Typed instructions to the same AI as voice. The text is kept until the send succeeds. */
  textInput?: { enabled: boolean; hint: string; onSubmit: (text: string) => Promise<void> };
  /** A result cell dropped on the toolbar, attached to the typed instruction; a new key adds it once. */
  attach?: { key: number; attachment: AiAttachment } | null;
};

/**
 * Voice toolbar pinned to a corner of the template preview. It follows scrolling,
 * shows results as a speech bubble and can be dragged to any of the four corners.
 */
export function VoiceOverlay(props: VoiceOverlayProps) {
  // The panel lives in the preview frame (the anchor's parent), not the scrolling image area.
  const anchor = useCallback(
    () => (document.querySelector(props.anchorSelector)?.parentElement as HTMLElement | null) ?? null,
    [props.anchorSelector],
  );
  const { corner, style, dragging, grip, pin, container } = useCornerPin(anchor, STORAGE_KEY, "bottom-right", 1);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [hovered, setHovered] = useState(false);
  // A reply that never arrives must not leave the spinner running forever.
  const [stale, setStale] = useState(false);
  useEffect(() => {
    setStale(false);
    if (!props.thinking) return;
    const timer = window.setTimeout(() => setStale(true), 60000);
    return () => window.clearTimeout(timer);
  }, [props.thinking]);
  const thinking = props.thinking && !stale;

  // Results fade after a while; questions and errors stay until answered or closed.
  const bubble = props.bubble && props.bubble.key !== dismissed ? props.bubble : null;
  useEffect(() => {
    if (!bubble || bubble.sticky || hovered) return;
    const timer = window.setTimeout(() => setDismissed(bubble.key), 7000);
    return () => window.clearTimeout(timer);
  }, [bubble?.key, bubble?.sticky, hovered]);

  const [typing, setTyping] = useState(false), [draft, setDraft] = useState({ message: "", hasText: false }), [sending, setSending] = useState(false);
  const field = useRef<InstructionFieldHandle>(null);
  useEffect(() => { if (typing) field.current?.focus(); }, [typing]);
  const text = props.textInput;
  // A cell dropped on the toolbar goes into the sentence as a chip, where the caret is.
  const drop = useAiDropTarget();
  // Only drops made while the toolbar is shown: one from before it (re)appeared is not added again.
  const seenAttach = useRef(props.attach?.key);
  useEffect(() => {
    if (!props.attach || !text || props.attach.key === seenAttach.current) return;
    seenAttach.current = props.attach.key;
    pendingChip.current = props.attach.attachment;
    setTyping(true);
  }, [props.attach?.key]);
  // The field may be mounting on this very render; the chip goes in once it is on the page.
  const pendingChip = useRef<AiAttachment | null>(null);
  useEffect(() => {
    if (!typing || !pendingChip.current || !field.current) return;
    field.current.insert(pendingChip.current); pendingChip.current = null;
  }, [typing, props.attach?.key]);
  // Hovering a chip shows the cell it stands for in the result table.
  const [hoveredPath, setHoveredPath] = useState<string | null>(null);
  useEffect(() => {
    if (!hoveredPath) return;
    const cell = document.querySelector<HTMLElement>(`td[data-review-path="${CSS.escape(hoveredPath)}"]`);
    if (!cell) return;
    cell.classList.add("is-ai-chip-source"); cell.scrollIntoView({ block: "nearest", inline: "nearest" });
    return () => cell.classList.remove("is-ai-chip-source");
  }, [hoveredPath]);
  const canSend = Boolean(text?.enabled && draft.hasText && !sending);
  function send() {
    if (!text || !canSend) return;
    setSending(true);
    text.onSubmit(draft.message.trim().slice(0, 2000)).then(() => field.current?.clear(), () => undefined).finally(() => { setSending(false); field.current?.focus(); });
  }

  const busy = thinking || props.starting;
  const live = props.speaking && props.caption;
  return createPortal(
    <div ref={drop.ref} className={`voice-overlay is-${corner}${dragging ? " is-dragging" : ""}${text && drop.armed ? " is-drop-armed" : ""}${text && drop.over ? " is-drop-over" : ""}`} style={style} {...pin}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      {(bubble || live || thinking || props.extra) && !dragging && <div
        className={`voice-bubble is-${live || thinking ? "info" : bubble?.tone ?? "info"}`} role="status" aria-live="polite">
        {live ? <p className="voice-bubble-caption">{props.caption}</p>
          : thinking ? <p className="voice-thinking" aria-label="指示を処理しています"><span className="voice-dots"><i /><i /><i /></span></p>
            : bubble ? <p>{bubble.text}</p> : null}
        {props.extra}
        {bubble && !live && !thinking && <button type="button" className="voice-bubble-close" aria-label="閉じる" onClick={() => setDismissed(bubble.key)}><FiX /></button>}
      </div>}
      {text && drop.armed && !typing && <p className="voice-drop-hint"><FiPaperclip aria-hidden />ここへドロップして文に入れる</p>}
      {text && typing && !dragging && <form className="voice-text" onSubmit={e => { e.preventDefault(); send(); }}>
        <InstructionField ref={field} enabled={text.enabled}
          placeholder={drop.armed ? "ここへドロップ" : text.enabled ? "例：3行目の体重を500に（欄をドロップして指せます）" : text.hint}
          onChange={(message, hasText) => setDraft({ message, hasText })} onSend={send} onEscape={() => setTyping(false)} onHoverChip={setHoveredPath} />
        <button type="submit" aria-label="送信" title={text.enabled ? "送信（Enter）" : text.hint} disabled={!canSend}><FiSend /></button>
      </form>}
      <div className="voice-pill">
        <button type="button" className="voice-grip" aria-label="音声入力の位置を移動（ドラッグ又は矢印キー）" title="ドラッグで四隅へ移動"
          {...grip}><FiMove /></button>
        {props.starting && !props.listening
          ? <button type="button" className="voice-mic is-starting is-busy" aria-label="マイクを準備中（押すと中止）" title="マイクを準備中（押すと中止）" onClick={props.onStop}><FiMic /></button>
          : props.listening
          ? <button type="button" className={`voice-mic is-on${busy ? " is-busy" : ""}${props.speaking ? " is-speaking" : ""}`} aria-label="音声入力を停止" title="音声入力を停止" onClick={props.onStop}><FiSquare /></button>
          : <button type="button" className={`voice-mic${thinking ? " is-busy" : ""}`} aria-label="音声入力" title={props.canStart ? "音声入力" : props.startHint} disabled={!props.canStart} onClick={props.onStart}><FiMic /></button>}
        {props.listening && <VoiceMeter read={props.meter} active speaking={props.speaking} />}
        {text && <button type="button" className={`voice-text-toggle${typing ? " is-on" : ""}`} aria-label="文字で指示" aria-pressed={typing} title="文字で指示"
          onClick={() => setTyping(open => !open)}><FiMessageSquare /></button>}
        <button type="button" className="voice-undo" aria-label="元に戻す" title="元に戻す" disabled={!props.canUndo} onClick={props.onUndo}><FiRotateCcw /></button>
      </div>
    </div>,
    container,
  );
}
