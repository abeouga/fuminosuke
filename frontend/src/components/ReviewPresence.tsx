import { useCallback, useEffect, useRef, useState } from "react";
import { FiArrowLeft, FiEdit3 } from "react-icons/fi";
import { api } from "../api";
import { Spinner } from "./Spinner";

/**
 * 2026-10-25: tells a review screen when the same reading is open on another screen (another
 * tab, the phone…), before anything is changed there. The other screen can be taken over; the
 * one that had it then learns so. A notice only: saving is still guarded by the result revision.
 */

/** One id per loaded page: a reload releases the old one and opens again. */
const viewerId = typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID() : `v${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
const RENEW_MS = 20_000;

/** held: this screen edits; other: another screen had it when this one opened; lost: taken over. */
export type ReviewPresenceState = "unknown" | "held" | "other" | "lost";

export function useReviewPresence(jobId: string, active: boolean, onTakenOver: () => void) {
  const [state, setState] = useState<ReviewPresenceState>("unknown");
  const [otherSince, setOtherSince] = useState<string | null>(null);
  const [taking, setTaking] = useState(false);
  const stateRef = useRef(state);
  stateRef.current = state;
  const takenOver = useRef(onTakenOver);
  takenOver.current = onTakenOver;

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const check = async () => {
      // A screen that lost it keeps saying so until the person decides.
      if (stateRef.current === "lost") return;
      try {
        const presence = await api.claimReviewPresence(jobId, viewerId);
        if (!alive) return;
        setOtherSince(presence.otherSince);
        setState(current => presence.held ? (current === "lost" ? current : "held")
          : current === "held" ? "lost" : current === "lost" ? current : "other");
      } catch {
        // A failed check never blocks editing.
      }
    };
    void check();
    const timer = window.setInterval(() => void check(), RENEW_MS);
    const visible = () => { if (document.visibilityState === "visible") void check(); };
    document.addEventListener("visibilitychange", visible);
    const release = () => { if (stateRef.current === "held") void api.releaseReviewPresence(jobId, viewerId).catch(() => undefined); };
    window.addEventListener("pagehide", release);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("pagehide", release);
      release();
    };
  }, [jobId, active]);

  const takeOver = useCallback(async () => {
    setTaking(true);
    try {
      const presence = await api.claimReviewPresence(jobId, viewerId, true);
      if (presence.held) {
        setState("held");
        // The other screen may have saved meanwhile: show what is stored now.
        takenOver.current();
      }
    } catch {
      // Left as it is; the person can try again or close.
    } finally {
      setTaking(false);
    }
  }, [jobId]);

  return { state: active ? state : "unknown" as ReviewPresenceState, otherSince, taking, takeOver };
}

function since(iso: string | null): string {
  if (!iso) return "";
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  return minutes < 1 ? "たった今" : minutes < 60 ? `${minutes}分前` : `${Math.floor(minutes / 60)}時間前`;
}

export function ReviewPresenceDialog({ presence, closeLabel, onClose }: {
  presence: ReturnType<typeof useReviewPresence>; closeLabel: string; onClose: () => void;
}) {
  if (presence.state !== "other" && presence.state !== "lost") return null;
  const lost = presence.state === "lost";
  return <div className="modal-backdrop review-presence-backdrop">
    <section className="modal-card review-presence-card" role="alertdialog" aria-modal="true" aria-labelledby="review-presence-title" aria-describedby="review-presence-text">
      <h2 id="review-presence-title">{lost ? "別の画面で編集が始まりました" : "この書類は別の画面で開かれています"}</h2>
      <p id="review-presence-text">{lost
        ? "この画面での修正は止めています。続ける場合は、別の画面で保存された内容を読み込み直してから編集します。"
        : `${since(presence.otherSince)}から別の画面（別のタブや端末）で開かれています。こちらで編集すると、別の画面では編集できなくなります。`}</p>
      <div className="page-actions">
        <button className="secondary" disabled={presence.taking} onClick={onClose}><FiArrowLeft />{closeLabel}</button>
        <button className="primary" disabled={presence.taking} onClick={() => void presence.takeOver()}>
          {presence.taking ? <Spinner /> : <FiEdit3 />}こちらで編集する</button>
      </div>
    </section>
  </div>;
}
