import { useEffect, useSyncExternalStore } from "react";
import { api } from "./api";
import { privateLocalStorage } from "./auth/transport";
import { showToast } from "./components/Toast";

// The readings started from the reading page, kept outside the page so they keep running and stay
// listed while the user reviews another document (and across a reload). The server's job queue does
// the work; this only remembers which jobs the user sent and polls their state.

export type QueuedReading = {
  jobId: string;
  fileName: string;
  templateName: string;
  status: string;
  progressPercent: number;
  errorMessage: string | null;
  addedAt: number;
  /** The template it is read on (missing on readings listed before this was kept). */
  templateId?: string;
  /** The confirmed document this reading updates, when it was sent as an update. */
  predecessorJobId?: string;
};

/** Optional details of a reading, used to recognise a later photo of the same sheet. */
export type QueuedReadingOrigin = { templateId?: string; predecessorJobId?: string };

const STORAGE_KEY = "fuminosuke.readingQueue";
const RUNNING = new Set(["CREATED", "QUEUED", "PROCESSING"]);
// Sent but not confirmed yet: a later photo of the same sheet may continue or replace it.
const UNCONFIRMED = new Set([...RUNNING, "REVIEW_REQUIRED"]);
const POLL_MS = 2500;

let items: QueuedReading[] = read();
const listeners = new Set<() => void>();

function read(): QueuedReading[] {
  try {
    const stored: unknown = JSON.parse(privateLocalStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(stored) ? stored.filter((item): item is QueuedReading =>
      typeof item === "object" && item !== null && typeof (item as QueuedReading).jobId === "string") : [];
  } catch {
    return [];
  }
}

function update(next: QueuedReading[]) {
  items = next;
  try {
    privateLocalStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage can be disabled; the list then lasts until the page is reloaded.
  }
  listeners.forEach((listener) => listener());
}

export const readingQueue = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  snapshot: () => items,
  add(jobId: string, fileName: string, templateName: string, origin: QueuedReadingOrigin = {}) {
    update([...items.filter((item) => item.jobId !== jobId), {
      jobId, fileName, templateName, status: "QUEUED", progressPercent: 0, errorMessage: null, addedAt: Date.now(),
      ...origin,
    }]);
  },
  remove(jobId: string) {
    update(items.filter((item) => item.jobId !== jobId));
  },
  /** Drops the finished ones (confirmed, cancelled) the user no longer needs to see. */
  clearDone() {
    update(items.filter((item) => !["CONFIRMED", "CANCELLED"].includes(item.status)));
  },
  setStatus(jobId: string, status: string) {
    update(items.map((item) => item.jobId === jobId ? { ...item, status } : item));
  },
  /** The next document waiting for review, in the order it was sent (not `exceptJobId`). */
  nextReview(exceptJobId?: string) {
    return items.find((item) => item.status === "REVIEW_REQUIRED" && item.jobId !== exceptJobId) ?? null;
  },
  running: () => items.filter((item) => RUNNING.has(item.status)),
  /** The sent readings of this template that are not confirmed yet (newest first). */
  unconfirmed(templateId: string, templateName: string) {
    return items
      .filter((item) => UNCONFIRMED.has(item.status)
        && (item.templateId ? item.templateId === templateId : item.templateName === templateName))
      .sort((a, b) => b.addedAt - a.addedAt);
  },
};

export function useReadingQueue() {
  return useSyncExternalStore(readingQueue.subscribe, readingQueue.snapshot);
}

/** Reads the current state of the given readings from the server and announces finished ones. */
export async function syncReadingQueue(ids = items.map((item) => item.jobId)) {
  if (ids.length === 0) return;
  const jobs = await Promise.all(ids.map((id) => api.job(id).catch(() => null)));
  let changed = false;
  const next = items.map((item) => {
    const job = jobs.find((candidate) => candidate?.id === item.jobId);
    if (!job) return item;
    const errorMessage = job.error?.message ?? null;
    if (job.status === item.status && job.progressPercent === item.progressPercent
      && errorMessage === item.errorMessage) return item;
    changed = true;
    if (RUNNING.has(item.status) && job.status === "REVIEW_REQUIRED") {
      showToast({ tone: "success", quiet: true, title: `「${item.fileName}」の読み取りが終わりました` });
    } else if (RUNNING.has(item.status) && ["FAILED", "INTERRUPTED"].includes(job.status)) {
      showToast({ tone: "error", quiet: true, title: `「${item.fileName}」を読み取れませんでした` });
    }
    return { ...item, status: job.status, progressPercent: job.progressPercent, errorMessage };
  });
  if (changed) update(next);
}

/** Polls the running readings while any remain; mounted once by the app shell. */
export function useReadingQueuePolling() {
  const queue = useReadingQueue();
  const running = queue.filter((item) => RUNNING.has(item.status)).map((item) => item.jobId).join(",");
  useEffect(() => {
    if (!running) return;
    const poll = () => { if (!document.hidden) void syncReadingQueue(running.split(",")); };
    poll();
    const timer = window.setInterval(poll, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running]);
}
