import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import {
  FiAlertTriangle,
  FiArrowUp,
  FiCheck,
  FiExternalLink,
  FiFileText,
  FiImage,
  FiInfo,
  FiMessageSquare,
  FiMic,
  FiMicOff,
  FiPlus,
  FiClock,
  FiGlobe,
  FiRefreshCw,
  FiSidebar,
  FiTrash2,
  FiX,
} from "react-icons/fi";
import { askStore, openQuestion, STOPPED, type AskViewer, type Conversation, type Turn } from "../structured/askStore";
import { privateLocalStorage } from "../auth/transport";
import { PageViewer } from "./ShelfPage";
import { chatReadings, useChatAttachments, useChatReadings, type ChatReading } from "../structured/chatReadings";
import { showToast } from "../components/Toast";
import { useReadingState } from "../readingQueue";
import { Spinner } from "../components/Spinner";
import { Markdown } from "../components/Markdown";
import { captureAttachments, flyIntoConversation, flyToReadingQueue } from "../components/chatFlights";
import {
  structuredClient,
  type AnswerTable,
  type Evidence,
  type StructuredDocument,
  type StructuredReading,
  type StructuredStatus,
} from "../structured/client";
import { QuestionVoiceCapture } from "../structured/voiceCapture";
import { VoiceMeter } from "../components/VoiceMeter";
import { isPhoneDevice } from "../hooks/usePhoneDevice";
import { visibleHeight } from "../phone/phoneKeyboard";

// Last known status, so coming back to the page shows it at once (refreshed on mount).
let cachedStatus: StructuredStatus | null = null;
const SIDEBAR_KEY = "fuminosuke.askSidebar";
const SPLIT_KEY = "fuminosuke.askSplit";
const SPLIT_MIN = 0.25;
const SPLIT_MAX = 0.75;

function storedSplit(): number {
  try {
    const value = Number(privateLocalStorage.getItem(SPLIT_KEY));
    return value >= SPLIT_MIN && value <= SPLIT_MAX ? value : 0.5;
  } catch {
    return 0.5;
  }
}

/** The line between the chat and the document: drag it (or use ←/→) to share the width. */
function Splitter({ onResize }: { onResize: (ratio: number) => void }) {
  const ratioAt = (handle: HTMLElement, x: number) => {
    const layout = handle.closest(".ask-layout");
    const chat = layout?.querySelector<HTMLElement>(".ask-chat");
    const panel = handle.closest<HTMLElement>(".ask-document-panel");
    if (!chat || !panel) return null;
    const left = chat.getBoundingClientRect().left;
    const right = panel.getBoundingClientRect().right;
    return Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, (x - left) / Math.max(right - left, 1)));
  };
  const current = (handle: HTMLElement) => {
    const panel = handle.closest<HTMLElement>(".ask-document-panel");
    return panel ? ratioAt(handle, panel.getBoundingClientRect().left) : null;
  };
  return (
    <div
      className="ask-splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label="チャットと書類の幅を変える"
      tabIndex={0}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        const handle = event.currentTarget;
        handle.setPointerCapture(event.pointerId);
        handle.classList.add("dragging");
        document.body.classList.add("ask-resizing");
        const move = (next: PointerEvent) => { const ratio = ratioAt(handle, next.clientX); if (ratio !== null) onResize(ratio); };
        const end = () => {
          handle.classList.remove("dragging");
          document.body.classList.remove("ask-resizing");
          handle.removeEventListener("pointermove", move);
          handle.removeEventListener("pointerup", end);
          handle.removeEventListener("pointercancel", end);
        };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", end);
        handle.addEventListener("pointercancel", end);
      }}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const ratio = current(event.currentTarget);
        if (ratio === null) return;
        onResize(Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, ratio + (event.key === "ArrowLeft" ? -0.05 : 0.05))));
      }}
    />
  );
}

function storedSidebar(): boolean {
  try {
    const value = privateLocalStorage.getItem(SIDEBAR_KEY);
    return value === null ? window.innerWidth > 1100 : value === "open";
  } catch {
    return window.innerWidth > 1100;
  }
}

export function AskPage({ onOpenReview }: { onOpenReview: (jobId: string, field?: string | null) => void }) {
  const state = useSyncExternalStore(askStore.subscribe, askStore.getSnapshot);
  const { turns, draft, replyMode, viewer, history, conversationId } = state;
  const [status, setStatus] = useState<StructuredStatus | null | "unavailable">(cachedStatus);
  // A phone shows the history as a drawer on request; it starts closed and is not remembered.
  const [phone] = useState(isPhoneDevice);
  const [sidebar, setSidebarState] = useState(() => !phone && storedSidebar());
  const [split, setSplitState] = useState(storedSplit);
  // The answer-table row under the pointer: its cell is highlighted on the open document.
  const [hovered, setHovered] = useState<RowSource | null>(null);
  const setSplit = (ratio: number) => {
    setSplitState(ratio);
    try { privateLocalStorage.setItem(SPLIT_KEY, String(ratio)); } catch { /* per-viewer convenience only */ }
  };
  const setSidebar = (open: boolean) => {
    setSidebarState(open);
    if (phone) return;
    try { privateLocalStorage.setItem(SIDEBAR_KEY, open ? "open" : "closed"); } catch { /* per-viewer convenience only */ }
  };
  const pageRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const readings = useChatReadings();
  const attachments = useChatAttachments();
  const [dragging, setDragging] = useState(false);
  // Where the images were in the message box when sent; they fly from there into the conversation.
  const flights = useRef(new Map<string, { rect: DOMRect; src: string }>());
  useLayoutEffect(() => {
    if (flights.current.size === 0) return;
    const sent = flights.current;
    flights.current = new Map();
    flyIntoConversation(sent);
  }, [readings]);
  // 画面の中から始まったドラッグ（画像・文字など）は添付にしない。添付はPCのファイルを持ち込んだときだけ。
  const innerDrag = useRef(false);
  useEffect(() => {
    const start = () => { innerDrag.current = true; };
    const end = () => { innerDrag.current = false; };
    window.addEventListener("dragstart", start);
    window.addEventListener("dragend", end);
    window.addEventListener("drop", end);
    return () => {
      window.removeEventListener("dragstart", start);
      window.removeEventListener("dragend", end);
      window.removeEventListener("drop", end);
    };
  }, []);
  // One capture per page visit; a completed transcript is sent exactly like typed text.
  // Spoken text goes into the input box: the user checks it and sends it with Enter.
  const [voice] = useState(() => new QuestionVoiceCapture((spoken) => {
    askStore.set((current) => ({ draft: current.draft.trim() ? `${current.draft.trimEnd()} ${spoken}` : spoken }));
    inputRef.current?.focus();
  }));
  const voiceState = useSyncExternalStore(voice.subscribe, voice.getSnapshot);
  useEffect(() => () => { void voice.stop(); }, [voice]);

  const enabled = status !== null && status !== "unavailable" && status.enabled;

  useEffect(() => {
    let stopped = false;
    void structuredClient.status()
      .then((value) => { cachedStatus = value; if (!stopped) setStatus(value); })
      .catch(() => { if (!stopped) setStatus("unavailable"); });
    return () => { stopped = true; };
  }, []);
  const busy = turns.some((turn) => turn.pending);
  const open = openQuestion(state);
  const answering = open !== null && replyMode;
  const canSend = !busy && ((enabled && draft.trim() !== "") || attachments.length > 0);
  const submit = (raw: string) => {
    const text = raw.trim();
    if (!canSend) return;
    // Images and text sent together are one message with one answer: the text is answered and the
    // images' readings (template guess, then the reading queue) are shown under that answer.
    // Text that only asks for the images to be read gets a short reply here, without the AI.
    flights.current = captureAttachments();
    const images = attachments.length;
    const afterTurnId = turns[turns.length - 1]?.id ?? null;
    let turnId: number | null = null;
    if (text && images > 0 && (!enabled || asksToRead(text))) {
      turnId = askStore.note(text, images > 1 ? `${images}枚の画像を読み取ります。` : "画像を読み取ります。");
    } else if (text && enabled) {
      turnId = answering ? askStore.replyText(text) : askStore.ask(text, []);
    }
    if (turnId !== null) askStore.set({ draft: "" });
    chatReadings.send(afterTurnId, turnId);
  };
  // Dropped, pasted or picked images only go into the message box; the send button reads them.
  const addImages = (list: FileList | File[] | null | undefined) => {
    if (!list) return false;
    const { added, skipped } = chatReadings.attach([...list]);
    if (skipped > 0) showToast({ tone: "error", quiet: true, title: `画像は一度に${added > 0 ? "あと" : ""}10枚まで添付できます` });
    if (added > 0) inputRef.current?.focus();
    return added > 0;
  };
  // Keep the newest message in view inside the conversation (the page itself does not jump).
  const last = turns[turns.length - 1];
  const scrollKey = `${turns.length}:${last?.pending ? 1 : 0}:${last?.result ? 1 : 0}:${last?.error ? 1 : 0}:${readings.map((reading) => reading.state[0]).join("")}`;
  const scrolledOnce = useRef(false);
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    // Coming back to the page jumps straight to the latest message; new messages glide in.
    // A finished answer is shown from its question down, so a long answer is read from the top.
    const bottom = list.scrollHeight - list.clientHeight;
    const asked = [...list.querySelectorAll<HTMLElement>(".ask-msg.user:not(.image)")].pop();
    const top = asked && !last?.pending ? Math.min(bottom, asked.offsetTop - 12) : bottom;
    list.scrollTo({ top: Math.max(top, 0), behavior: scrolledOnce.current ? "smooth" : "instant" });
    scrolledOnce.current = true;
  }, [scrollKey]);
  // The page fills the window below wherever the app's header ends, so only the conversation scrolls.
  useLayoutEffect(() => {
    const page = pageRef.current;
    if (!page) return;
    const fit = () => {
      const gap = 16;
      const top = page.getBoundingClientRect().top + window.scrollY;
      // The main area's bottom padding would push the page past the window; give it back.
      const bottom = parseFloat(getComputedStyle(page.parentElement ?? page).paddingBottom) || 0;
      // A phone's bottom tabs cover the end of the window (they hide while the keyboard is up).
      const tabs = document.querySelector<HTMLElement>(".phone-tabs");
      const covered = tabs && getComputedStyle(tabs).display !== "none" ? tabs.getBoundingClientRect().height : 0;
      page.style.setProperty("--ask-height", `${Math.max(visibleHeight() - top - gap - covered, 0)}px`);
      page.style.marginBottom = `${Math.min(gap + covered - bottom, 0)}px`;
    };
    fit();
    window.addEventListener("resize", fit);
    // iOS shows the keyboard over the page and only shrinks the visual viewport.
    window.visualViewport?.addEventListener("resize", fit);
    // Showing or hiding the tabs (keyboard up or down) changes the room without resizing the window.
    const shell = page.closest(".app-shell");
    const watch = shell ? new MutationObserver(fit) : null;
    if (shell) watch?.observe(shell, { attributes: true, attributeFilter: ["class"] });
    return () => {
      window.removeEventListener("resize", fit);
      window.visualViewport?.removeEventListener("resize", fit);
      watch?.disconnect();
    };
  }, []);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 168)}px`;
  }, [draft]);
  const openQuestionId = open?.question.questionId;
  useEffect(() => { if (openQuestionId) inputRef.current?.focus({ preventScroll: true }); }, [openQuestionId]);

  const qaConnected = enabled && status.qaConnected;
  const voiceConnected = enabled && status.voiceConnected;
  const listening = voiceState.mode !== "stopped";
  const latestId = last?.id ?? null;
  const placeholder = !enabled ? "チャットは利用できません"
    : answering ? "選択肢にない場合は、ここに書いて送ってください"
    : attachments.length > 0 ? "送信すると画像を読み取ります（メッセージも書けます）"
    : "メッセージを入力";
  const turnIds = new Set(turns.map((turn) => turn.id));
  // Readings whose job a later image replaced (the job itself is gone).
  const replaced = new Set(readings.flatMap((reading) =>
    reading.outcome === "replaced" && reading.earlier ? [reading.earlier.jobId] : []));
  // Images sent with a message are shown inside that message and its answer.
  const inTurn = (reading: ChatReading) => reading.turnId != null && turnIds.has(reading.turnId);
  const attachedTo = (turnId: number) => readings.filter((reading) => reading.turnId === turnId);
  const readingViews = (turnId: number | null) => readings
    .filter((reading) => !inTurn(reading))
    .filter((reading) => turnId === null
      ? reading.afterTurnId === null || !turnIds.has(reading.afterTurnId)
      : reading.afterTurnId === turnId)
    .map((reading) => (
      <ReadingView key={reading.id} reading={reading} onOpenReview={onOpenReview}
        replaced={reading.jobId !== null && replaced.has(reading.jobId)} />
    ));

  // A conversation switch shows its document panel rising with the messages, not sliding in.
  const shownConversation = useRef(conversationId);
  const switched = shownConversation.current !== conversationId;
  useEffect(() => { shownConversation.current = conversationId; }, [conversationId]);
  const startNew = () => { askStore.clear(); chatReadings.clear(); inputRef.current?.focus(); };
  const narrow = () => phone || window.matchMedia("(max-width: 900px)").matches;
  const topTools = phone ? document.getElementById("phone-top-tools") : null;

  return (
    <div className="ask-page" ref={pageRef}>
      {status === "unavailable" && (
        <aside className="notice danger"><strong>接続を確認できません</strong><span>サーバーへ接続できるまで質問は送れません。</span></aside>
      )}
      {status !== null && status !== "unavailable" && !status.enabled && (
        <aside className="notice danger"><strong>この環境では質問が無効です</strong><span>構造化データ機能（STRUCTURED_DATA_ENABLED）を有効にすると使えます。</span></aside>
      )}
      {enabled && !qaConnected && (
        <aside className="notice"><strong>AIが未接続です</strong><span>欄の名前を書いた質問には答えられます。それ以外は欄を選んで答えます。</span></aside>
      )}

      {/* A phone keeps the history and the new conversation as icons in the top bar. */}
      {phone && topTools && createPortal(
        <>
          <button type="button" className="phone-top-button" aria-label="履歴" title="履歴" onClick={() => setSidebar(true)}>
            <FiClock aria-hidden="true" />
          </button>
          <button type="button" className="phone-top-button" aria-label="新しい会話" title="新しい会話"
            disabled={busy} onClick={startNew}>
            <FiPlus aria-hidden="true" />
          </button>
        </>,
        topTools,
      )}
      <div className={`ask-layout${viewer ? " with-document" : ""}${sidebar ? " with-history" : " with-rail"}`}
        style={viewer ? { gridTemplateColumns: `${sidebar ? 284 : 72}px minmax(300px, ${split}fr) minmax(280px, ${1 - split}fr)` } : undefined}>
        {/* One panel whose width slides between the rail of icons (closed) and the history (open);
            both stay mounted so opening and closing can animate. */}
        <div className={sidebar ? "ask-side open" : "ask-side"}>
          <nav className="ask-rail" aria-label="会話の履歴" inert={sidebar}>
            <button type="button" className="ask-icon-button" aria-label="履歴を開く" title="履歴を開く" onClick={() => setSidebar(true)}>
              <FiSidebar aria-hidden="true" />
            </button>
            <button type="button" className="ask-icon-button ask-rail-new" disabled={busy}
              aria-label="新しい会話" title="新しい会話" onClick={startNew}>
              <FiPlus aria-hidden="true" />
            </button>
            <button type="button" className="ask-icon-button" aria-label="履歴を見る" title="履歴を見る" onClick={() => setSidebar(true)}>
              <FiClock aria-hidden="true" />
            </button>
          </nav>
          <HistorySidebar
            open={sidebar}
            history={history}
            currentId={conversationId}
            busy={busy}
            onClose={() => setSidebar(false)}
            onNew={() => { startNew(); if (narrow()) setSidebar(false); }}
            onOpen={(id) => { chatReadings.clear(); askStore.open(id); if (narrow()) setSidebar(false); }}
            onRemove={(id) => askStore.remove(id)}
          />
        </div>
        <button type="button" className={sidebar ? "ask-history-backdrop show" : "ask-history-backdrop"} tabIndex={-1}
          aria-hidden={!sidebar} aria-label="履歴を閉じる" onClick={() => setSidebar(false)} />
        <section
          className={dragging ? "ask-chat dropping" : "ask-chat"}
          aria-label="チャット"
          onDragOver={(event) => {
            if (innerDrag.current || ![...event.dataTransfer.types].includes("Files")) return;
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
          onDrop={(event) => {
            if (innerDrag.current || ![...event.dataTransfer.types].includes("Files")) return;
            event.preventDefault();
            setDragging(false);
            addImages(event.dataTransfer.files);
          }}
        >
          {dragging && <div className="ask-drop-hint" aria-hidden="true"><FiImage />画像を置くと添付します</div>}
          <div className="ask-messages" ref={listRef} role="log" aria-live="polite">
            {turns.length === 0 && readings.length === 0
              ? (
                <div className="ask-welcome">
                  <h2><span className="ask-welcome-hello">{greeting(new Date())}</span><span className="ask-welcome-ask">何を調べますか？</span></h2>
                </div>
              )
              : [readingViews(null), ...turns.map((turn) => [
                <TurnView
                  key={turn.id}
                  turn={turn}
                  latest={turn.id === latestId}
                  busy={busy}
                  viewer={viewer}
                  onChoose={(choice) => askStore.choose(turn.id, choice)}
                  onRetry={() => askStore.retry(turn.id)}
                  onRemove={() => { askStore.removeTurn(turn.id); chatReadings.dismissTurn(turn.id); }}
                  attached={attachedTo(turn.id)}
                  replacedJobs={replaced}
                  onOpenReview={onOpenReview}
                  onShowDocument={(next) => askStore.set({ viewer: next })}
                  onHoverRow={setHovered}
                />,
                ...readingViews(turn.id),
              ])]}
          </div>

          <form className="ask-composer" onSubmit={(event) => { event.preventDefault(); submit(draft); }}>
            {attachments.length > 0 && (
              <ul className="ask-attachments" aria-label="添付した画像">
                {attachments.map((item) => (
                  <li key={item.id} className="ask-attachment" data-attachment-id={item.id}>
                    <img src={item.preview} alt={item.fileName} draggable={false} />
                    <button type="button" className="ask-attachment-remove" aria-label={`「${item.fileName}」を外す`} title="外す"
                      onClick={() => chatReadings.detach(item.id)}>
                      <FiX aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="ask-input-row">
              <label className="sr-only" htmlFor="ask-input">質問</label>
              <textarea
                id="ask-input"
                ref={inputRef}
                value={draft}
                rows={1}
                placeholder={placeholder}
                disabled={!enabled}
                onChange={(event) => askStore.set({ draft: event.target.value })}
                onPaste={(event) => {
                  if (addImages(event.clipboardData.files)) event.preventDefault();
                }}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
                  event.preventDefault();
                  submit(draft);
                }}
              />
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                multiple
                hidden
                onChange={(event) => { addImages(event.target.files); event.target.value = ""; }}
              />
              <button
                type="button"
                className="ask-round-button ask-attach"
                aria-label="書類の画像を添付"
                title="書類の画像を添付"
                onClick={() => fileRef.current?.click()}
              >
                <FiImage aria-hidden="true" />
              </button>
              {voiceConnected && <VoiceMeter read={voice.meter.read} active={voiceState.mode === "listening"} speaking={voiceState.message === "聞き取り中…"} />}
              {voiceConnected && (
                <button
                  type="button"
                  className={listening ? "ask-round-button ask-mic listening" : "ask-round-button ask-mic"}
                  aria-pressed={listening}
                  aria-label={listening ? "音声入力を止める" : "音声で質問"}
                  title={listening ? "音声入力を止める" : "音声で質問"}
                  onClick={() => { if (listening) void voice.finish(); else void voice.start(); }}
                >
                  {listening ? <FiMicOff aria-hidden="true" /> : <FiMic aria-hidden="true" />}
                </button>
              )}
              {busy ? (
                // While an answer is being worked out, the send button is the only way to stop it.
                <button type="button" className="ask-round-button ask-send ask-stop" aria-label="回答を止める" title="回答を止める"
                  onClick={() => askStore.stop()}>
                  <span className="ask-stop-mark" aria-hidden="true" />
                </button>
              ) : (
                <button type="submit" className="ask-round-button ask-send" disabled={!canSend} aria-label="送信" title="送信（Enter）">
                  <FiArrowUp aria-hidden="true" />
                </button>
              )}
            </div>
            {(voiceState.caption || voiceState.message) && (
              <p className="ask-composer-foot" role="status" aria-live="polite">
                {voiceState.caption ? <span className="ask-caption">{voiceState.caption}</span> : <span>{voiceState.message}</span>}
              </p>
            )}
          </form>
        </section>

        {viewer && (
          <DocumentPanel
            key={conversationId}
            entrance={switched ? "switch" : "open"}
            viewer={viewer}
            hovered={hovered?.jobId === viewer.jobId ? hovered.fieldId : null}
            onFocus={(focus) => askStore.set((current) => ({ viewer: current.viewer && { ...current.viewer, focus } }))}
            onClose={() => askStore.set({ viewer: null })}
            onResize={setSplit}
          />
        )}
      </div>
    </div>
  );
}

/** How the answer was read (「合計」で計算しています 等): an "i" that shows the notes floating on hover. */
function AnswerNotes({ notes }: { notes: string[] }) {
  return (
    <span className="ask-info">
      <button type="button" className="ask-info-button" aria-label={notes.join(" ")}><FiInfo aria-hidden="true" /></button>
      <span className="ask-info-pop" role="tooltip">
        {notes.map((note, index) => <span key={index}>{note}</span>)}
      </span>
    </span>
  );
}

/** A row's cell on the document; no cell for a row that is a document itself (the list of documents). */
type RowSource = { jobId: string; fieldId: string | null };

function TurnView({ turn, latest, busy, viewer, onChoose, onRetry, onRemove, onShowDocument, onHoverRow, attached, replacedJobs, onOpenReview }: {
  turn: Turn;
  /** Images sent with this message: shown in it, their readings under the answer. */
  attached: ChatReading[];
  replacedJobs: Set<string>;
  onOpenReview: (jobId: string) => void;
  latest: boolean;
  busy: boolean;
  viewer: AskViewer | null;
  onChoose: (choice: { value: string; label: string }) => void;
  onRetry: () => void;
  onRemove: () => void;
  onShowDocument: (viewer: AskViewer) => void;
  onHoverRow: (source: RowSource | null) => void;
}) {
  const result = turn.result;
  const question = result?.question ?? null;
  const canChoose = latest && !busy;
  // The document opens beside the chat with all of this answer's cells in it highlighted.
  const show = (jobId: string, documentName: string | null, focus: string | null) => onShowDocument({
    jobId,
    documentName,
    evidence: (result?.evidence ?? []).filter((item) => item.jobId === jobId && item.fieldId),
    focus,
  });
  const isReplaced = (reading: ChatReading) => reading.jobId !== null && replacedJobs.has(reading.jobId);
  const readingBusy = attached.some((reading) => ["classifying", "comparing", "starting"].includes(reading.state));
  const documentNameOf = (jobId: string) => result?.evidence.find((item) => item.jobId === jobId)?.documentName
    ?? result?.documents.find((item) => item.jobId === jobId)?.documentName ?? null;
  return (
    <>
      <div className={turn.request.replyTo ? "ask-msg user reply" : "ask-msg user"}>
        {attached.length > 0 && (
          <div className="ask-turn-images">
            {attached.map((reading) => (
              <span key={reading.id} className="ask-turn-image">
                <ReadingImage reading={reading} replaced={isReplaced(reading)} />
              </span>
            ))}
          </div>
        )}
        <div className="ask-bubble">{turn.text}</div>
        {!turn.request.replyTo && turn.scope.length > 0 && <small className="ask-msg-meta">対象：{turn.scope.map((item) => item.label).join("、")}</small>}
      </div>
      <div className={`ask-msg bot${result ? ` ${result.state}` : ""}${turn.error ? (turn.errorCode === STOPPED ? " stopped" : " failed") : ""}${turn.pending ? " pending" : ""}`}>
        <AiAvatar thinking={turn.pending || readingBusy} />
        <div className="ask-bubble">
          {turn.local && (
            <div className="ask-answer-row"><Markdown className="ask-answer" text={turn.local} /></div>
          )}
          {turn.pending && (
            <div className="ask-thinking" role="status" aria-label="回答を準備しています"><i /><i /><i /></div>
          )}
          {turn.error && (
            <div className="ask-failed">
              {turn.errorCode === STOPPED
                ? <p>{turn.error}</p>
                : <p role="alert"><FiAlertTriangle aria-hidden="true" />{turn.error}</p>}
              {latest && (
                <button type="button" className="secondary compact" disabled={busy} onClick={onRetry}>
                  <FiRefreshCw aria-hidden="true" />もう一度送る
                </button>
              )}
            </div>
          )}
          {result && (
            <>
              {result.general && (
                <p className="ask-general-badge"><FiGlobe aria-hidden="true" />一般的な情報（書類の内容ではありません）</p>
              )}
              <div className="ask-answer-row">
                <Markdown className="ask-answer" text={result.answer}
                  links={result.state === "clarifying" ? [] : result.documents.flatMap((document) => document.documentName
                    ? [{
                      text: document.documentName,
                      active: viewer?.jobId === document.jobId,
                      title: `「${document.documentName}」を右に表示`,
                      onClick: () => show(document.jobId, document.documentName, null),
                    }]
                    : [])} />
                {result.notes.length > 0 && <AnswerNotes notes={result.notes} />}
              </div>
              {question && !question.optional && question.kind === "document" && (
                <div className="ask-doc-choices" role="group" aria-label={question.message}>
                  {question.choices.map((choice) => {
                    const viewing = !turn.picked && viewer?.jobId === choice.value;
                    return (
                      // One click opens the document on the right; the button growing out of
                      // the card underneath is what chooses it.
                      <div key={choice.value} className={viewing && canChoose ? "ask-doc-option open" : "ask-doc-option"}>
                        <button type="button"
                          className={`ask-doc-choice${turn.picked === choice.value ? " picked" : ""}${viewing ? " viewing" : ""}`}
                          disabled={!canChoose && turn.picked !== choice.value} aria-pressed={viewer?.jobId === choice.value}
                          onClick={() => show(choice.value, choice.label, null)}>
                          {choice.thumbnailUrl
                            ? <img src={choice.thumbnailUrl} alt="" loading="lazy" draggable={false} />
                            : <span className="ask-doc-choice-blank" aria-hidden="true"><FiFileText /></span>}
                          <span className="ask-doc-choice-text">
                            <strong>{turn.picked === choice.value && <FiCheck aria-hidden="true" />}{choice.label}</strong>
                            {choice.detail && <small>{choice.detail}</small>}
                          </span>
                        </button>
                        {viewing && canChoose && (
                          <button type="button" className="ask-doc-confirm-button" onClick={() => onChoose(choice)}>
                            <FiCheck aria-hidden="true" />この書類にする
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              {question && !question.optional && question.kind !== "document" && (
                <div className="ask-choices" role="group" aria-label={question.message}>
                  {question.choices.map((choice) => (
                    <button key={choice.value} type="button" className={turn.picked === choice.value ? "picked" : undefined}
                      disabled={!canChoose} aria-pressed={turn.picked === choice.value} onClick={() => onChoose(choice)}>
                      {turn.picked === choice.value && <FiCheck aria-hidden="true" />}{choice.label}
                    </button>
                  ))}
                  {canChoose && <p className="ask-choices-hint">当てはまるものがなければ、下の入力欄に書いて送ってください。</p>}
                </div>
              )}
              {result.table && result.table.rows.length > 0 && (
                <AnswerTableView table={result.table} sources={rowSources(result.table, result.evidence)} viewer={viewer}
                  // Hovering only highlights the cell on the document already open beside the chat:
                  // it never opens the panel or switches it to another document.
                  onHover={(source) => onHoverRow(source && viewer?.jobId === source.jobId ? source : null)}
                  onOpen={(source) => show(source.jobId, documentNameOf(source.jobId), source.fieldId)} />
              )}
              {result.caveats.length > 0 && (
                <div className="ask-caveats">
                  <strong><FiAlertTriangle aria-hidden="true" />確認してください</strong>
                  <ul>{result.caveats.slice(0, 3).map((caveat, index) => <li key={index}>{caveat}</li>)}</ul>
                  {result.caveats.length > 3 && (
                    <details>
                      <summary>ほか{result.caveats.length - 3}件</summary>
                      <ul>{result.caveats.slice(3).map((caveat, index) => <li key={index}>{caveat}</li>)}</ul>
                    </details>
                  )}
                </div>
              )}
            </>
          )}
          {attached.length > 0 && (
            <div className="ask-turn-readings">
              {attached.map((reading) => (
                <div key={reading.id} className="ask-turn-reading">
                  <ReadingBody reading={reading} onOpenReview={onOpenReview} replaced={isReplaced(reading)} />
                </div>
              ))}
            </div>
          )}
        </div>
        {/* このやり取りを会話から消す。消したものは以後の文脈に使わない。回答を待っている間は出さない（止めるのは送信ボタン）。 */}
        {!turn.pending && (
          <button type="button" className="ask-remove-turn" onClick={onRemove} aria-label="このやり取りを削除" title="このやり取りを削除">
            <FiTrash2 aria-hidden="true" />
          </button>
        )}
      </div>
    </>
  );
}

/** The answer's table. Hovering a row highlights its cell on the open document; clicking opens it there. */
/**
 * The cell each answer-table row was read from. Answers saved before the server sent them are
 * matched the way the server builds them: the rows with a value, in order, are the evidence.
 */
function rowSources(table: AnswerTable, evidence: Evidence[]): (RowSource | null)[] {
  if (table.sources) return table.sources.map((item) => ({ jobId: item.jobId, fieldId: item.fieldId ?? null }));
  const asked = table.columns.length - (table.columns[table.columns.length - 1] === "経過" ? 2 : 1);
  const filled = table.rows.filter((row) => row[asked] !== null).length;
  if (filled !== evidence.length) return table.rows.map(() => null);
  let next = 0;
  return table.rows.map((row) => {
    if (row[asked] === null) return null;
    const item = evidence[next++];
    return item.fieldId ? { jobId: item.jobId, fieldId: item.fieldId } : null;
  });
}

/** The answer's table. Hovering a row highlights its cell on the document beside the chat; clicking picks it. */
function AnswerTableView({ table, sources, viewer, onHover, onOpen }: {
  table: AnswerTable;
  sources: (RowSource | null)[];
  viewer: AskViewer | null;
  onHover: (source: RowSource | null) => void;
  onOpen: (source: RowSource) => void;
}) {
  // A hovered row must not stay highlighted after the table leaves the screen.
  const hover = useRef(onHover);
  hover.current = onHover;
  useEffect(() => () => hover.current(null), []);
  return (
    <div className="ask-table-block">
      <div className="ask-table-wrap">
        <table className="ask-table">
          <thead><tr>{table.columns.map((column, index) => <th key={index}>{column}</th>)}</tr></thead>
          <tbody onMouseLeave={() => onHover(null)}>
            {table.rows.map((row, rowIndex) => {
              const source = sources[rowIndex] ?? null;
              const active = source !== null && viewer?.jobId === source.jobId && viewer.focus === source.fieldId;
              return (
                <tr key={rowIndex} className={source ? `has-source${active ? " active" : ""}` : undefined}
                  title={source ? "書類で見る" : undefined}
                  onMouseEnter={() => onHover(source)}
                  onClick={source ? () => onOpen(source) : undefined}>
                  {row.map((cell, cellIndex) => <td key={cellIndex}>{cell ?? <span className="ask-empty">—</span>}</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Documents fetched for the side panel, so switching between answers does not reload them.
const documentCache = new Map<string, StructuredDocument>();

/** The source document in the right half of the page, the answer's cells highlighted on it. */
function DocumentPanel({ entrance, viewer, hovered, onFocus, onClose, onResize }: {
  /** How the panel appears: slid in when opened, risen like the messages after a conversation switch. */
  entrance: "open" | "switch";
  viewer: AskViewer;
  /** The cell of the answer-table row under the pointer, shown instead of the focused one. */
  hovered: string | null;
  onFocus: (fieldId: string) => void;
  onClose: () => void;
  /** The chat's share of the chat + document width, while the divider is dragged. */
  onResize: (ratio: number) => void;
}) {
  const [loaded, setDocument] = useState<StructuredDocument | null>(documentCache.get(viewer.jobId) ?? null);
  // Never the previous document for the moment after a switch: the new one's preview starts fresh.
  const fetched = loaded?.jobId === viewer.jobId ? loaded : documentCache.get(viewer.jobId) ?? null;
  // A reading not confirmed yet shows its aligned sheet only.
  const imageOnly = useMemo(() => viewer.image ? {
    fields: [] as StructuredReading[],
    pages: [{ pageId: null, pageNumber: 1, imageUrl: `/api/v1/jobs/${encodeURIComponent(viewer.jobId)}/artifacts/alignedSource` }],
  } : null, [viewer.image, viewer.jobId]);
  const document = imageOnly ?? fetched;
  const [failed, setFailed] = useState(false);
  const [appear] = useState(entrance);
  useEffect(() => {
    const cached = documentCache.get(viewer.jobId);
    setDocument(cached ?? null);
    setFailed(false);
    if (cached || viewer.image) return;
    let stopped = false;
    void structuredClient.document(viewer.jobId)
      .then((value) => { documentCache.set(viewer.jobId, value); if (!stopped) setDocument(value); })
      .catch(() => { if (!stopped) setFailed(true); });
    return () => { stopped = true; };
  }, [viewer.image, viewer.jobId]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  const readings = useMemo(() => {
    const ids = new Set(viewer.evidence.map((item) => item.fieldId));
    if (hovered) ids.add(hovered);
    return document?.fields.filter((reading) => ids.has(reading.fieldId)) ?? [];
  }, [document, viewer.evidence, hovered]);
  const focusId = hovered ?? viewer.focus ?? viewer.evidence[0]?.fieldId ?? null;
  const picked = readings.find((reading) => reading.fieldId === focusId) ?? null;
  const title = <h2 className="ask-document-title"><FiFileText aria-hidden="true" /><span>{viewer.documentName ?? "（名称なし）"}</span></h2>;
  const close = <button type="button" className="ask-icon-button ask-document-close" onClick={onClose} aria-label="書類を閉じる" title="閉じる"><FiX aria-hidden="true" /></button>;
  return (
    <aside className={appear === "switch" ? "ask-document-panel switched" : "ask-document-panel"} aria-label="書類">
      <Splitter onResize={onResize} />
      {document && !failed
        // Name, zoom and close share the viewer's one tool row.
        // Keyed by document, so switching to another one replays the preview's fade (only the preview, not the panel).
        ? <PageViewer key={viewer.jobId} pages={document.pages ?? []} readings={readings} picked={picked} headings={[]} pickedHeading={null} marked
          onPick={(reading) => onFocus(reading.fieldId)} onPickHeading={() => undefined}
          title={title} actions={close} />
        : (
          <>
            <div className="ask-panel-head">{title}{close}</div>
            {failed ? <p className="ask-panel-lead">書類を読み込めませんでした。</p>
              : <p className="ask-panel-lead"><Spinner />書類を読み込んでいます…</p>}
          </>
        )}
    </aside>
  );
}

/** An image sent alone: its own message, and its own answer with the reading's progress. */
function ReadingView({ reading, onOpenReview, replaced }: {
  reading: ChatReading;
  onOpenReview: (jobId: string) => void;
  /** A later image replaced this reading: its job was deleted. */
  replaced: boolean;
}) {
  const queued = useReadingState(reading.state === "queued" ? reading.jobId : null);
  const failed = reading.state === "failed" || (queued !== null && ["FAILED", "INTERRUPTED"].includes(queued.status));
  return (
    <>
      <div className="ask-msg user image"><ReadingImage reading={reading} replaced={replaced} /></div>
      <div className={`ask-msg bot reading${reading.state === "choosing" || reading.state === "continuing" ? " clarifying" : ""}${failed ? " failed" : ""}`}>
        <AiAvatar thinking={["classifying", "comparing", "starting"].includes(reading.state)} />
        <div className="ask-bubble"><ReadingBody reading={reading} onOpenReview={onOpenReview} replaced={replaced} /></div>
      </div>
    </>
  );
}

/** The sent image; once its reading is in the queue (not restored as queued), a copy is thrown into 「読み取り状況」. */
function ReadingImage({ reading, replaced }: { reading: ChatReading; replaced: boolean }) {
  const preview = chatReadings.preview(reading.id);
  const imageRef = useRef<HTMLImageElement>(null);
  const shownState = useRef(reading.state);
  useEffect(() => {
    if (shownState.current !== "queued" && reading.state === "queued" && !replaced && imageRef.current) {
      flyToReadingQueue(imageRef.current);
    }
    shownState.current = reading.state;
  }, [reading.state, replaced]);
  return preview
    ? <img ref={imageRef} className="ask-image" src={preview} alt={reading.fileName} draggable={false} data-reading-image={reading.id} />
    : <div className="ask-bubble"><FiImage aria-hidden="true" /> {reading.fileName}</div>;
}

/** What the reading of a sent image is doing: the template guess, a question, its progress or its result. */
function ReadingBody({ reading, onOpenReview, replaced }: {
  reading: ChatReading;
  onOpenReview: (jobId: string) => void;
  replaced: boolean;
}) {
  const queued = useReadingState(reading.state === "queued" ? reading.jobId : null);
  const hasFile = chatReadings.hasFile(reading.id);
  return (
        <>
          {reading.state === "classifying" && <p className="ask-reading-status" role="status"><Spinner />どのテンプレートの書類か確かめています…</p>}
          {reading.state === "comparing" && <p className="ask-reading-status" role="status"><Spinner />前に読み取った書類の続きかどうか確かめています…</p>}
          {reading.state === "continuing" && reading.earlier && <ContinuationQuestion reading={reading} earlier={reading.earlier} />}
          {reading.state === "waiting" && reading.earlier && (
            <div className="ask-reading-waiting">
              <p className="ask-reading-status" role="status">
                <Spinner />「{reading.earlier.fileName}」の確定を待っています。確定すると、その続きとして読み取ります。
              </p>
              <div className="ask-reading-actions">
                {reading.earlier.status === "REVIEW_REQUIRED" && (
                  <button type="button" className="secondary compact" onClick={() => onOpenReview(reading.earlier!.jobId)}>
                    <FiExternalLink aria-hidden="true" />前の書類を確認する
                  </button>
                )}
                <button type="button" className="link-button" onClick={() => chatReadings.readSeparately(reading.id)}>待たずに別の書類として読み取る</button>
                <button type="button" className="link-button" onClick={() => chatReadings.dismiss(reading.id)}>やめる</button>
              </div>
            </div>
          )}
          {reading.state === "choosing" && (
            <>
              <p className="ask-answer">
                {reading.candidates.length === 0
                  ? "使用中のテンプレートと一致しませんでした。この書類のテンプレートを作成してから、もう一度送ってください。"
                  : reading.matched ? "どのテンプレートで読み取りますか？"
                  : "はっきり一致するテンプレートがありませんでした。近いものを選ぶと読み取ります。"}
              </p>
              {!hasFile && reading.candidates.length > 0 && <p className="ask-reading-note">画面を再読み込みしたため、画像をもう一度送ってください。</p>}
              {reading.candidates.length > 0 && (
                <div className="ask-template-cards" role="group" aria-label="テンプレートを選ぶ">
                  {reading.candidates.map((candidate) => (
                    <button
                      key={candidate.templateVersionId}
                      type="button"
                      className="ask-template-card"
                      disabled={!hasFile}
                      onClick={() => chatReadings.choose(reading.id, candidate)}
                    >
                      <img src={`${candidate.thumbnailUrl}?width=240`} alt="" loading="lazy" draggable={false} />
                      <span>{candidate.templateName}</span>
                    </button>
                  ))}
                </div>
              )}
              <div><button type="button" className="link-button" onClick={() => chatReadings.dismiss(reading.id)}>{reading.candidates.length === 0 ? "閉じる" : "読み取らない"}</button></div>
            </>
          )}
          {reading.state === "starting" && <p className="ask-reading-status" role="status"><Spinner />「{reading.templateName}」で読み取りを始めています…</p>}
          {reading.state === "queued" && replaced && (
            <p className="ask-answer ask-reading-replaced">この読み取りは、後から送った画像に置き換えました。</p>
          )}
          {reading.state === "queued" && !replaced && reading.outcome && reading.earlier && (
            <p className="ask-reading-origin">
              {reading.outcome === "continued"
                ? `「${reading.earlier.fileName}」の続きとして読み取ります。`
                : `「${reading.earlier.fileName}」の読み取りを置き換えました。`}
            </p>
          )}
          {reading.state === "queued" && !replaced && (
            <QueuedStatus
              reading={reading}
              status={queued?.status ?? "QUEUED"}
              progress={queued?.progressPercent ?? 0}
              error={queued?.errorMessage ?? null}
              onOpenReview={onOpenReview}
            />
          )}
          {reading.state === "failed" && (
            <div className="ask-failed">
              <p role="alert"><FiAlertTriangle aria-hidden="true" />{reading.error}</p>
              <div className="ask-reading-actions">
                {hasFile && (
                  <button type="button" className="secondary compact" onClick={() => chatReadings.retry(reading.id)}>
                    <FiRefreshCw aria-hidden="true" />もう一度試す
                  </button>
                )}
                <button type="button" className="link-button" onClick={() => chatReadings.dismiss(reading.id)}>閉じる</button>
              </div>
            </div>
          )}
        </>
  );
}

const EARLIER_STATUS: Record<string, string> = {
  CREATED: "受付中", QUEUED: "順番待ち", PROCESSING: "読み取り中", REVIEW_REQUIRED: "確認待ち",
};

// Readings whose earlier sheet was already put in the side panel by itself (once each, not after it is closed).
const shownEarlier = new Set<string>();

const CONTINUATION_OPTIONS = [
  { key: "continue", title: "続きとして読み取る", recommended: true,
    note: "前の書類を確定したあと、その次の版として読み取ります。前に確定した欄は引き継ぎ、空欄だった欄だけを読みます。" },
  { key: "replace", title: "前の読み取りを置き換える", recommended: false,
    note: "前の読み取り（確定前に直した内容も）を削除し、この画像を読み取ります。" },
  { key: "separate", title: "別の書類として読み取る", recommended: false,
    note: "前の読み取りはそのまま残します。" },
] as const;

/** Asks what to do with an image that looks like a later photo of a sheet sent but not confirmed yet. */
function ContinuationQuestion({ reading, earlier }: { reading: ChatReading; earlier: NonNullable<ChatReading["earlier"]> }) {
  const [thumbFailed, setThumbFailed] = useState(false);
  const viewable = earlier.status === "REVIEW_REQUIRED";
  const thumb = viewable && !thumbFailed
    ? `/api/v1/jobs/${encodeURIComponent(earlier.jobId)}/artifacts/alignedSource/thumbnail?width=240`
    : null;
  const showing = useSyncExternalStore(askStore.subscribe, () => askStore.getSnapshot().viewer?.jobId === earlier.jobId);
  const show = () => askStore.set({ viewer: { jobId: earlier.jobId, documentName: earlier.fileName, evidence: [], focus: null, image: true } });
  useEffect(() => {
    // The earlier sheet opens beside the question by itself, so the two can be compared (not over the chat on a narrow screen).
    if (!viewable || shownEarlier.has(reading.id) || window.matchMedia("(max-width: 900px)").matches) return;
    shownEarlier.add(reading.id);
    show();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reading.id, viewable]);
  const choose = (key: typeof CONTINUATION_OPTIONS[number]["key"]) => {
    if (key === "continue") chatReadings.continueEarlier(reading.id);
    else if (key === "replace") void chatReadings.replaceEarlier(reading.id);
    else chatReadings.readSeparately(reading.id);
  };
  const card = (
    <>
      {thumb
        ? <img src={thumb} alt="" loading="lazy" draggable={false} onError={() => setThumbFailed(true)} />
        : <span className="ask-continuation-thumb" aria-hidden="true"><FiFileText /></span>}
      <span><b>{earlier.fileName}</b><small>{earlier.templateName}・{EARLIER_STATUS[earlier.status] ?? "確定前"}</small></span>
    </>
  );
  return (
    <div className="ask-continuation">
      <p className="ask-answer">この画像は前に読み取ったものと同じ書類のようです。</p>
      {viewable
        ? <button type="button" className={`ask-continuation-earlier is-viewable${showing ? " is-showing" : ""}`}
            onClick={show} title="右に表示">{card}</button>
        : <div className="ask-continuation-earlier">{card}</div>}
      <div className="ask-continuation-options" role="group" aria-label="この画像の扱い">
        {CONTINUATION_OPTIONS.map(({ note: plain, ...option }) => {
          // A confirmed document matched too: a reading made instead of the earlier one updates it.
          const note = reading.confirmed && option.key !== "continue"
            ? `${plain}確定済みの「${reading.confirmed.label}」の次の版として読み取ります。`
            : plain;
          return (
          <button key={option.key} type="button" aria-label={`${option.title}。${note}`}
            className={`ask-continuation-option${option.recommended ? " is-recommended" : ""}`}
            onClick={() => choose(option.key)}>
            <b>{option.title}</b>
            <span className="ask-info ask-option-info" aria-hidden="true">
              <span className="ask-info-button"><FiInfo /></span>
              <span className="ask-info-pop">{note}</span>
            </span>
          </button>
          );
        })}
      </div>
      {reading.error && <p role="alert" className="ask-reading-error"><FiAlertTriangle aria-hidden="true" />{reading.error}</p>}
      <div className="ask-continuation-cancel"><button type="button" className="link-button" onClick={() => chatReadings.dismiss(reading.id)}>キャンセル（読み取らない）</button></div>
    </div>
  );
}

/** A reading sent to the queue: its progress, then a link to review it (never opened automatically). */
function QueuedStatus({ reading, status, progress, error, onOpenReview }: {
  reading: ChatReading;
  status: string;
  progress: number;
  error: string | null;
  onOpenReview: (jobId: string) => void;
}) {
  const name = `「${reading.templateName}」`;
  if (status === "REVIEW_REQUIRED" || status === "CONFIRMED") {
    return (
      <div className="ask-reading-done">
        <p className="ask-answer">
          {status !== "CONFIRMED" && <FiCheck aria-hidden="true" />}
          {status === "CONFIRMED" ? `${name}で読み取り、確定済みです。` : `${name}で読み取りました。`}
        </p>
        {reading.jobId && (
          <button type="button" className="secondary compact" onClick={() => onOpenReview(reading.jobId!)}>
            <FiExternalLink aria-hidden="true" />{status === "CONFIRMED" ? "結果を開く" : "確認する"}
          </button>
        )}
        {status === "CONFIRMED" && <span className="ask-confirmed-mark" title="確定済み" aria-label="確定済み"><FiCheck aria-hidden="true" /></span>}
      </div>
    );
  }
  if (status === "FAILED" || status === "INTERRUPTED" || status === "CANCELLED") {
    return <p role="alert" className="ask-reading-error"><FiAlertTriangle aria-hidden="true" />{error ?? `${name}で読み取れませんでした。`}</p>;
  }
  return (
    <p className="ask-reading-status" role="status">
      <Spinner />{name}で読み取っています{progress > 0 ? `（${progress}%）` : "（順番待ち）"}
    </p>
  );
}

/** The assistant's mark: the app's "文" on an ink-blue tile, ringed while it is working. */
function AiAvatar({ thinking }: { thinking: boolean }) {
  return <span className={thinking ? "ask-avatar thinking" : "ask-avatar"} aria-hidden="true"><b>文</b></span>;
}

/** Text sent with images that only asks for them to be read (no question about the documents). */
function asksToRead(text: string): boolean {
  return /読み?取|よみと|読んで|よんで|読み?込|取り?込|とりこ|ＯＣＲ|OCR|スキャン/i.test(text)
    && !/[?？]|何|なに|いくら|いくつ|どれ|どこ|だれ|誰|いつ|教えて|合計|平均/.test(text);
}

/** The welcome line follows the time of day. */
function greeting(now: Date): string {
  const hour = now.getHours();
  if (hour >= 4 && hour < 11) return "おはようございます";
  if (hour >= 11 && hour < 18) return "こんにちは";
  return "こんばんは";
}

const DAY = 24 * 60 * 60 * 1000;

function historyGroup(updatedAt: number, now: Date): string {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (updatedAt >= today) return "今日";
  if (updatedAt >= today - DAY) return "昨日";
  if (updatedAt >= today - 7 * DAY) return "過去7日間";
  if (updatedAt >= today - 30 * DAY) return "過去30日間";
  return "それ以前";
}

function HistorySidebar({ open, history, currentId, busy, onClose, onNew, onOpen, onRemove }: {
  open: boolean;
  onClose: () => void;
  history: Conversation[];
  currentId: string;
  busy: boolean;
  onNew: () => void;
  onOpen: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const now = new Date();
  const groups: { name: string; items: Conversation[] }[] = [];
  for (const item of history) {
    const name = historyGroup(item.updatedAt, now);
    const group = groups.find((entry) => entry.name === name);
    if (group) group.items.push(item);
    else groups.push({ name, items: [item] });
  }
  return (
    <nav className="ask-history" aria-label="会話の履歴" inert={!open}>
      <button type="button" className="ask-icon-button ask-history-close" aria-label="履歴を閉じる" title="履歴を閉じる" onClick={onClose}>
        <FiSidebar aria-hidden="true" />
      </button>
      <button type="button" className="ask-history-new" disabled={busy} onClick={onNew}>
        <FiPlus aria-hidden="true" />新しい会話
      </button>
      <div className="ask-history-scroll">
        {history.length === 0 && <p className="ask-history-empty">まだ会話はありません</p>}
        {groups.map((group) => (
          <section key={group.name} className="ask-history-group">
            <h2>{group.name}</h2>
            <ul>
              {group.items.map((item) => (
                <li key={item.id} className={item.id === currentId ? "active" : undefined}>
                  <button type="button" className="ask-history-item" disabled={busy && item.id !== currentId}
                    aria-current={item.id === currentId ? "true" : undefined} title={item.title} onClick={() => onOpen(item.id)}>
                    <FiMessageSquare aria-hidden="true" /><span>{item.title}</span>
                  </button>
                  <button type="button" className="ask-history-remove" disabled={busy && item.id === currentId}
                    aria-label={`「${item.title}」を履歴から削除`} title="履歴から削除" onClick={() => onRemove(item.id)}>
                    <FiTrash2 aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </nav>
  );
}
