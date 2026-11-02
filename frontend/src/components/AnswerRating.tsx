import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { FiMic, FiMicOff, FiSend, FiThumbsDown, FiThumbsUp, FiX } from "react-icons/fi";
import type { AnswerRating, RatingReason } from "../structured/client";
import { QuestionVoiceCapture } from "../structured/voiceCapture";

const REASONS: { value: Exclude<RatingReason, "other">; label: string }[] = [
  { value: "wrong_value", label: "数値が違う" },
  { value: "not_wanted", label: "探していたものと違う" },
  { value: "unclear", label: "分かりにくい" },
];
const REASON_LABEL: Record<RatingReason, string> = {
  wrong_value: "数値が違う", not_wanted: "探していたものと違う", unclear: "分かりにくい", other: "その他",
};

/**
 * 👍/👎 under an answer. 👎 opens a small box asking why; closing it without choosing keeps the 👎.
 * Pressing the chosen button again takes the rating back.
 */
export function AnswerRatingBar({ rating, onRate, voiceAvailable, onVoiceStart }: {
  rating: AnswerRating | null | undefined;
  onRate: (rating: AnswerRating | null) => void;
  /** Voice can be used here (the server has it and the page may use the microphone). */
  voiceAvailable: boolean;
  /** Called before this box starts listening, so the chat's own microphone is stopped first. */
  onVoiceStart: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const good = rating?.value === "good";
  const bad = rating?.value === "bad";
  const badTitle = bad && rating?.reason ? `良くない（${REASON_LABEL[rating.reason]}）` : "良くない";
  return (
    <div className="ask-rating" role="group" aria-label="この回答の評価">
      <button type="button" className={good ? "ask-rate good on" : "ask-rate good"} aria-pressed={good}
        aria-label="良い" title={good ? "良い（押すと取り消し）" : "良い"}
        onClick={() => { setAsking(false); onRate(good ? null : { value: "good" }); }}>
        <FiThumbsUp aria-hidden="true" />
      </button>
      <button type="button" className={bad ? "ask-rate bad on" : "ask-rate bad"} aria-pressed={bad}
        aria-label="良くない" title={bad ? `${badTitle}（押すと取り消し）` : badTitle}
        onClick={() => {
          if (bad) { setAsking(false); onRate(null); return; }
          onRate({ value: "bad" });
          setAsking(true);
        }}>
        <FiThumbsDown aria-hidden="true" />
      </button>
      {asking && (
        <ReasonBox voiceAvailable={voiceAvailable} onVoiceStart={onVoiceStart}
          onClose={() => setAsking(false)}
          onPick={(reason, comment, commentBy) => {
            onRate({ value: "bad", reason, ...(comment ? { comment, commentBy } : {}) });
            setAsking(false);
          }} />
      )}
    </div>
  );
}

function ReasonBox({ voiceAvailable, onVoiceStart, onPick, onClose }: {
  voiceAvailable: boolean;
  onVoiceStart: () => void;
  onPick: (reason: RatingReason, comment?: string, commentBy?: "voice" | "text") => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const [other, setOther] = useState(false);
  const [comment, setComment] = useState("");
  const [spoken, setSpoken] = useState(false);
  const [voice] = useState(() => new QuestionVoiceCapture((text) => {
    setSpoken(true);
    setComment((current) => current.trim() ? `${current.trimEnd()} ${text}` : text);
  }));
  const voiceState = useSyncExternalStore(voice.subscribe, voice.getSnapshot);
  const listening = voiceState.mode !== "stopped";
  useEffect(() => () => { void voice.stop(); }, [voice]);
  // It opens under the answer, which may be at the bottom of the conversation: bring it into view.
  useEffect(() => { box.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [other]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (box.current && !box.current.contains(event.target as Node)
        && !(event.target as Element | null)?.closest?.(".ask-rating")) onClose();
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [onClose]);
  const startOther = () => {
    setOther(true);
    if (voiceAvailable) { onVoiceStart(); void voice.start(); }
    else window.setTimeout(() => field.current?.focus(), 0);
  };
  const send = () => {
    const text = comment.trim();
    if (!text) return;
    void voice.stop();
    onPick("other", text.slice(0, 1000), spoken ? "voice" : "text");
  };
  return (
    <div ref={box} className="ask-rating-box" role="dialog" aria-label="良くなかった理由">
      <div className="ask-rating-box-head">
        <strong>どこが良くなかったですか？</strong>
        <button type="button" className="ask-rating-close" aria-label="閉じる" onClick={onClose}><FiX aria-hidden="true" /></button>
      </div>
      {!other ? (
        <div className="ask-rating-reasons">
          {REASONS.map((reason) => (
            <button key={reason.value} type="button" onClick={() => onPick(reason.value)}>{reason.label}</button>
          ))}
          <button type="button" className="ask-rating-other" onClick={startOther}>
            {voiceAvailable && <FiMic aria-hidden="true" />}その他（{voiceAvailable ? "声で伝える" : "書いて伝える"}）
          </button>
        </div>
      ) : (
        <div className="ask-rating-comment">
          <textarea ref={field} rows={3} maxLength={1000} value={comment}
            placeholder={listening ? "話してください…" : "良くなかったところを書いてください"}
            onChange={(event) => setComment(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
          {voiceState.caption && <p className="ask-rating-caption" aria-live="polite">{voiceState.caption}</p>}
          {voiceState.message && <p className="ask-rating-error" role="alert">{voiceState.message}</p>}
          <div className="ask-rating-comment-actions">
            {voiceAvailable && (
              <button type="button" className={listening ? "ask-rating-mic listening" : "ask-rating-mic"} aria-pressed={listening}
                aria-label={listening ? "音声入力を止める" : "音声で伝える"} title={listening ? "音声入力を止める" : "音声で伝える"}
                onClick={() => { if (listening) void voice.finish(); else { onVoiceStart(); void voice.start(); } }}>
                {listening ? <FiMicOff aria-hidden="true" /> : <FiMic aria-hidden="true" />}
              </button>
            )}
            <button type="button" className="primary compact" disabled={!comment.trim()} onClick={send}>
              <FiSend aria-hidden="true" />送る
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
