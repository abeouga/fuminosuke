import { useEffect, useRef, useState } from "react";
import { FiAlertCircle, FiCheck, FiChevronDown, FiFileText, FiX } from "react-icons/fi";
import { readingQueue, syncReadingQueue, useReadingQueue, type QueuedReading } from "../readingQueue";

const STATUS_LABELS: Record<string, string> = {
  CREATED: "受付中",
  QUEUED: "順番待ち",
  PROCESSING: "読み取り中",
  REVIEW_REQUIRED: "確認待ち",
  CONFIRMED: "確定済み",
  FAILED: "失敗",
  CANCELLED: "中止",
  INTERRUPTED: "中断",
};
const RUNNING = new Set(["CREATED", "QUEUED", "PROCESSING"]);
const PROBLEM = new Set(["FAILED", "INTERRUPTED"]);

/**
 * The readings the user sent from this browser, shown in the menu bar (left of the connection
 * status) while any are listed: they run one after another on the server while the user keeps
 * adding forms or reviews the ones already read. Opens downward as a list.
 */
export function ReadingQueueMenu({ onOpen }: {
  onOpen: (item: QueuedReading) => void;
}) {
  const queue = useReadingQueue();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const running = queue.filter((item) => RUNNING.has(item.status));
  const waiting = queue.filter((item) => item.status === "REVIEW_REQUIRED");
  const problems = queue.filter((item) => PROBLEM.has(item.status)).length;
  const done = queue.filter((item) => ["CONFIRMED", "CANCELLED"].includes(item.status)).length;
  // Overall progress of the readings still running, for the ring on the button.
  const progress = running.length === 0 ? 1
    : running.reduce((sum, item) => sum + (item.status === "PROCESSING" ? item.progressPercent : 0), 0) / (running.length * 100);
  const [pulse, setPulse] = useState(false);
  const signature = `${queue.length}:${waiting.length}`;
  const firstSignature = useRef(signature);
  useEffect(() => {
    if (signature === firstSignature.current) return;
    firstSignature.current = signature;
    setPulse(true);
    const timer = window.setTimeout(() => setPulse(false), 900);
    return () => window.clearTimeout(timer);
  }, [signature]);
  useEffect(() => { if (open) void syncReadingQueue(); }, [open]);
  useEffect(() => { if (queue.length === 0) setOpen(false); }, [queue.length]);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  if (queue.length === 0) return null;
  const choose = (item: QueuedReading) => { setOpen(false); onOpen(item); };
  const summary = [
    running.length > 0 ? `読み取り中 ${running.length}` : "",
    waiting.length > 0 ? `確認待ち ${waiting.length}` : "",
    problems > 0 ? `失敗 ${problems}` : "",
  ].filter(Boolean).join(" · ") || "すべて確認済み";
  return (
    <div className={`reading-float${open ? " open" : ""}`} ref={rootRef}>
      <button type="button" className={`reading-float-pill${pulse ? " pulse" : ""}`}
        aria-expanded={open} aria-controls="reading-float-panel"
        onClick={() => setOpen((current) => !current)}>
        <ProgressRing value={progress} active={running.length > 0} done={waiting.length} />
        <span className="reading-float-summary">
          <strong>読み取り状況</strong>
          <small>{summary}</small>
        </span>
        <FiChevronDown className="reading-float-chevron" aria-hidden="true" />
      </button>
      {open && (
        <section id="reading-float-panel" className="reading-float-panel" aria-label="読み取り状況の一覧">
          <ol className="reading-float-list">
            {[...queue].reverse().map((item) => (
              <QueueRow key={item.jobId} item={item} onOpen={choose} />
            ))}
          </ol>
          {(done > 0 || waiting.length > 0) && (
            <footer className="reading-float-actions">
              {done > 0 && <button type="button" className="reading-float-link" onClick={() => readingQueue.clearDone()}>確定済みを消す</button>}
              {waiting.length > 0 && <button type="button" className="reading-float-primary" onClick={() => choose(waiting[0])}>
                順に確認する（{waiting.length}件）
              </button>}
            </footer>
          )}
        </section>
      )}
    </div>
  );
}

function QueueRow({ item, onOpen }: { item: QueuedReading; onOpen: (item: QueuedReading) => void }) {
  const status = item.status.toLowerCase();
  const runningNow = RUNNING.has(item.status);
  const openable = item.status === "REVIEW_REQUIRED" || PROBLEM.has(item.status);
  const [thumbFailed, setThumbFailed] = useState(false);
  const thumb = !runningNow && !PROBLEM.has(item.status) && !thumbFailed
    ? `/api/v1/jobs/${encodeURIComponent(item.jobId)}/artifacts/alignedSource/thumbnail`
    : null;
  const body = (
    <>
      <span className={`reading-float-thumb is-${status}`}>
        {thumb ? <img src={thumb} alt="" decoding="async" onError={() => setThumbFailed(true)} />
          : PROBLEM.has(item.status) ? <FiAlertCircle aria-hidden="true" />
            : <FiFileText aria-hidden="true" />}
        {item.status === "CONFIRMED" && <span className="reading-float-check"><FiCheck aria-hidden="true" /></span>}
      </span>
      <span className="reading-float-text">
        <strong title={item.fileName}>{item.fileName}</strong>
        <small>
          <i className={`reading-float-dot is-${status}`} aria-hidden="true" />
          {STATUS_LABELS[item.status] ?? item.status}
          {item.status === "PROCESSING" && item.progressPercent > 0 ? ` ${item.progressPercent}%` : ""}
          <span className="reading-float-template"> · {item.templateName}</span>
        </small>
        {PROBLEM.has(item.status) && item.errorMessage && <small className="reading-float-error">{item.errorMessage}</small>}
        {runningNow && <span className="reading-float-bar"><span style={{ width: `${item.status === "PROCESSING" ? Math.max(4, item.progressPercent) : 0}%` }} /></span>}
      </span>
    </>
  );
  return (
    <li className={`reading-float-row is-${status}`}>
      {openable
        ? <button type="button" className="reading-float-open" onClick={() => onOpen(item)}
            title={item.status === "REVIEW_REQUIRED" ? "確認画面を開く" : "詳細を開く"}>{body}
            <span className="reading-float-go">{item.status === "REVIEW_REQUIRED" ? "確認する" : "詳細"}</span>
          </button>
        : <div className="reading-float-open">{body}</div>}
      {!runningNow && <button type="button" className="reading-float-remove" aria-label={`「${item.fileName}」を一覧から外す`}
        title="一覧から外す（書類は消えません）" onClick={() => readingQueue.remove(item.jobId)}><FiX aria-hidden="true" /></button>}
    </li>
  );
}

function ProgressRing({ value, active, done }: { value: number; active: boolean; done: number }) {
  const radius = 13;
  const length = 2 * Math.PI * radius;
  return (
    <span className={`reading-float-ring${active ? " active" : ""}`} aria-hidden="true">
      <svg viewBox="0 0 32 32">
        <circle cx="16" cy="16" r={radius} className="track" />
        <circle cx="16" cy="16" r={radius} className="value"
          strokeDasharray={length} strokeDashoffset={active ? length * (1 - Math.max(0.08, value)) : 0} />
      </svg>
      <b>{active ? "" : done > 0 ? done : <FiCheck />}</b>
    </span>
  );
}
