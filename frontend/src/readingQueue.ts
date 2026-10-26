import { useEffect, useSyncExternalStore } from "react";
import { api, ApiError } from "./api";
import { privateLocalStorage, storageScope } from "./auth/transport";
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
/**
 * Listed at once when 「読み取りを開始」 is pressed while the image is still being compared with the
 * confirmed documents; replaced by the real reading when the server accepts it. Its id is local.
 */
export const SENDING = "SENDING";
const PENDING_PREFIX = "pending:";
const RUNNING = new Set(["CREATED", "QUEUED", "PROCESSING"]);
// Sent but not confirmed yet: a later photo of the same sheet may continue or replace it.
const UNCONFIRMED = new Set([...RUNNING, "REVIEW_REQUIRED"]);
const POLL_MS = 2500;

// Read again whenever the signed-in user's storage changes: this module loads before the session is
// known, and in the cloud the list is kept per user (reading it then would show another, stale list).
let loadedScope = storageScope();
let items: QueuedReading[] = read();
const listeners = new Set<() => void>();

function current(): QueuedReading[] {
  const scope = storageScope();
  if (scope !== loadedScope) {
    loadedScope = scope;
    items = read();
  }
  return items;
}

function read(): QueuedReading[] {
  try {
    const stored: unknown = JSON.parse(privateLocalStorage.getItem(STORAGE_KEY) ?? "[]");
    // A reading still being sent when the page was left can no longer be sent: it is dropped.
    return Array.isArray(stored) ? stored.filter((item): item is QueuedReading =>
      typeof item === "object" && item !== null && typeof (item as QueuedReading).jobId === "string"
      && (item as QueuedReading).status !== SENDING) : [];
  } catch {
    return [];
  }
}

function update(next: QueuedReading[]) {
  loadedScope = storageScope();
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
  snapshot: current,
  add(jobId: string, fileName: string, templateName: string, origin: QueuedReadingOrigin = {}) {
    update([...current().filter((item) => item.jobId !== jobId), {
      jobId, fileName, templateName, status: "QUEUED", progressPercent: 0, errorMessage: null, addedAt: Date.now(),
      ...origin,
    }]);
  },
  /** Lists a reading that is still being sent; returns its local id for `sent` or `remove`. */
  sending(fileName: string, templateName: string, origin: QueuedReadingOrigin = {}) {
    const id = `${PENDING_PREFIX}${crypto.randomUUID()}`;
    update([...current(), {
      jobId: id, fileName, templateName, status: SENDING, progressPercent: 0, errorMessage: null, addedAt: Date.now(),
      ...origin,
    }]);
    return id;
  },
  /** The server accepted the reading listed as `localId`: it takes the real job's place in the list. */
  sent(localId: string, jobId: string, origin: QueuedReadingOrigin = {}) {
    update(current().map((item) => item.jobId === localId
      ? { ...item, ...origin, jobId, status: "QUEUED" } : item));
  },
  remove(jobId: string) {
    update(current().filter((item) => item.jobId !== jobId));
  },
  /** Drops the finished ones (confirmed, cancelled) the user no longer needs to see. */
  clearDone() {
    update(current().filter((item) => !["CONFIRMED", "CANCELLED"].includes(item.status)));
  },
  setStatus(jobId: string, status: string) {
    update(current().map((item) => item.jobId === jobId ? { ...item, status } : item));
  },
  /** The next document waiting for review, in the order it was sent (not `exceptJobId`). */
  nextReview(exceptJobId?: string) {
    return current().find((item) => item.status === "REVIEW_REQUIRED" && item.jobId !== exceptJobId) ?? null;
  },
  running: () => current().filter((item) => RUNNING.has(item.status)),
  /** The sent readings of this template that are not confirmed yet (newest first). */
  unconfirmed(templateId: string, templateName: string) {
    return current()
      .filter((item) => UNCONFIRMED.has(item.status)
        && (item.templateId ? item.templateId === templateId : item.templateName === templateName))
      .sort((a, b) => b.addedAt - a.addedAt);
  },
};

export function useReadingQueue() {
  return useSyncExternalStore(readingQueue.subscribe, readingQueue.snapshot);
}

/** Reads the current state of the given readings from the server and announces finished ones. */
let syncing = false;

export async function syncReadingQueue(ids = current().map((item) => item.jobId)) {
  ids = ids.filter((id) => !id.startsWith(PENDING_PREFIX));
  // One sync at a time: a slow reply must not land after a newer one.
  if (ids.length === 0 || syncing) return;
  syncing = true;
  const gone = new Set<string>();
  let jobs: (Awaited<ReturnType<typeof api.job>> | null)[];
  try {
    jobs = await Promise.all(ids.map((id) => api.job(id).catch((error: unknown) => {
      // Deleted elsewhere (another tab or device): nothing is left to wait for.
      if (error instanceof ApiError && error.code === "JOB_NOT_FOUND") gone.add(id);
      return null;
    })));
  } finally {
    syncing = false;
  }
  let changed = gone.size > 0;
  const next = current().filter((item) => !gone.has(item.jobId)).map((item) => {
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

export type ReadingState = Pick<QueuedReading, "status" | "progressPercent" | "errorMessage">;

// The state of readings no longer in the list (removed with ×, sent from another browser or listed in a
// list this browser forgot): read from the server, so a chat message never waits on a list entry.
const outside = new Map<string, ReadingState>();
const outsideListeners = new Set<() => void>();
const fetching = new Set<string>();
const FINAL = new Set(["CONFIRMED", "FAILED", "CANCELLED", "INTERRUPTED"]);

async function fetchOutside(jobId: string) {
  if (fetching.has(jobId)) return;
  fetching.add(jobId);
  try {
    const job = await api.job(jobId);
    outside.set(jobId, { status: job.status, progressPercent: job.progressPercent, errorMessage: job.error?.message ?? null });
  } catch (error) {
    if (!(error instanceof ApiError && error.code === "JOB_NOT_FOUND")) return;
    outside.set(jobId, { status: "CANCELLED", progressPercent: 0, errorMessage: "この読み取りは削除されています。" });
  } finally {
    fetching.delete(jobId);
  }
  outsideListeners.forEach((listener) => listener());
}

/** The state of one sent reading: from the list while it is listed, otherwise from the server. */
export function useReadingState(jobId: string | null): ReadingState | null {
  const queue = useReadingQueue();
  const listed = jobId ? queue.find((item) => item.jobId === jobId) ?? null : null;
  const fetched = useSyncExternalStore(
    (listener) => { outsideListeners.add(listener); return () => { outsideListeners.delete(listener); }; },
    () => (jobId ? outside.get(jobId) ?? null : null),
  );
  const ask = Boolean(jobId) && !listed;
  const running = !fetched || RUNNING.has(fetched.status);
  const final = fetched !== null && FINAL.has(fetched.status);
  useEffect(() => {
    // Read again whenever the message is shown (a review may have been confirmed meanwhile), and keep
    // polling while it is still being read.
    if (!ask || !jobId || final) return;
    const poll = () => { if (!document.hidden) void fetchOutside(jobId); };
    poll();
    if (!running) return;
    const timer = window.setInterval(poll, POLL_MS * 2);
    return () => window.clearInterval(timer);
  }, [ask, jobId, running, final]);
  return listed ?? fetched;
}
