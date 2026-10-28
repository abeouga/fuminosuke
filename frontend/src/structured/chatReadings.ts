import { useSyncExternalStore } from "react";
import { api, ApiError, createIdempotencyKey, type TemplateClassification } from "../api";
import { readingQueue, syncReadingQueue } from "../readingQueue";
import { findConfirmedMatch } from "../confirmedMatch";
import { privateLocalStorage, storageScope } from "../auth/transport";

// Images put into the chat: first attached to the message box, sent with the send button. The server
// then guesses the template, the user picks one only when it is unsure, and the reading is sent to the
// reading queue (the review screen is never opened for them). When a sent but not yet confirmed reading
// of the same template looks like an earlier photo of the same sheet, the user is asked first whether
// the new photo continues it, replaces it or is another document. Otherwise, when it looks like a later
// photo of a confirmed document of the same template, it is read as that document's next version.
// Each image belongs to the conversation it was sent in, and is kept with it (per signed-in user) so
// opening the conversation from the history shows its images again.

export type ChatCandidate = TemplateClassification["candidates"][number];

/** A sent, not yet confirmed reading the new image looks like a later photo of. */
export type EarlierReading = {
  jobId: string;
  fileName: string;
  templateName: string;
  status: string;
  /** The confirmed document the earlier reading itself updates (kept when it is replaced). */
  predecessorJobId: string | null;
};

export type ChatReading = {
  id: string;
  /** The conversation it was sent in. */
  conversationId: string;
  /** The chat turn this image was added after (null: before any turn). */
  afterTurnId: number | null;
  /** The turn sent together with this image (its text): shown in that turn's message and answer. */
  turnId?: number | null;
  /** Images sent together are never asked about one another. */
  batch: string;
  fileName: string;
  state: "classifying" | "choosing" | "comparing" | "continuing" | "waiting" | "starting" | "queued" | "failed";
  candidates: ChatCandidate[];
  /** Whether the server found at least one template the image aligns with. */
  matched: boolean;
  /** The template it is read on, once decided or picked. */
  template: ChatCandidate | null;
  templateName: string | null;
  /** The earlier reading it was compared with (asked about, waited for, or replaced). */
  earlier: EarlierReading | null;
  /** The confirmed document it is a later photo of, found while the earlier reading is asked about. */
  confirmed?: { jobId: string; label: string } | null;
  /** How it was finally sent: as the update of the earlier reading, or replacing it. */
  outcome: "continued" | "replaced" | null;
  jobId: string | null;
  error: string | null;
  key: string;
};

export type ChatAttachment = { id: string; fileName: string; preview: string };

const STORAGE_KEY = "fuminosuke-chat-readings-v3";
/** The tab-wide list of every user before v3 (another user signing in on the tab saw it). */
const SHARED_KEY = "fuminosuke-chat-readings-v2";
const MAX_READINGS = 300;
const MAX_ATTACHMENTS = 10;
const WAIT_POLL_MS = 4000;
const ENDED = new Set(["FAILED", "INTERRUPTED", "CANCELLED"]);
// The files themselves only live in this tab; after a reload a pending choice asks for the image again.
const files = new Map<string, File>();
const previews = new Map<string, string>();
const listeners = new Set<() => void>();
// Read again once the signed-in user's storage is known (this module loads before the session).
let loadedScope = storageScope();
let readings: ChatReading[] = load();
let attachments: ChatAttachment[] = [];

function load(): ChatReading[] {
  try { sessionStorage.removeItem(SHARED_KEY); } catch { /* storage may be off */ }
  try {
    const stored: unknown = JSON.parse(privateLocalStorage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(stored)) return [];
    return (stored as ChatReading[]).filter((reading) => typeof reading?.conversationId === "string").map((reading) =>
      ["classifying", "comparing", "waiting", "starting"].includes(reading.state)
        ? { ...reading, state: "failed", error: "画面を再読み込みしたため中断しました。画像をもう一度送ってください。" }
        : reading);
  } catch {
    return [];
  }
}

function sync(): ChatReading[] {
  const scope = storageScope();
  if (scope !== loadedScope) {
    loadedScope = scope;
    readings.forEach((reading) => release(reading.id));
    readings = load();
  }
  return readings;
}

function notify() {
  listeners.forEach((listener) => listener());
}

function update(next: ChatReading[]) {
  sync();
  const kept = next.slice(-MAX_READINGS);
  next.slice(0, -MAX_READINGS).forEach((reading) => release(reading.id));
  readings = kept;
  try {
    privateLocalStorage.setItem(STORAGE_KEY, JSON.stringify(readings));
  } catch {
    // Storage can be disabled; the list then lasts until the page is reloaded.
  }
  notify();
}

function patch(id: string, change: Partial<ChatReading>) {
  update(readings.map((reading) => reading.id === id ? { ...reading, ...change } : reading));
}

function find(id: string) {
  return readings.find((reading) => reading.id === id);
}

function message(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function release(id: string) {
  const url = previews.get(id);
  if (url) URL.revokeObjectURL(url);
  files.delete(id);
  previews.delete(id);
}

async function start(id: string, candidate: ChatCandidate, predecessorJobId?: string) {
  const reading = find(id);
  const file = files.get(id);
  if (!reading || !file) {
    patch(id, { state: "failed", error: "画像が見つかりません。もう一度送ってください。" });
    return;
  }
  patch(id, { state: "starting", template: candidate, templateName: candidate.templateName, error: null });
  try {
    const created = await api.createOcr(candidate.templateVersionId, file, true, reading.key, predecessorJobId);
    readingQueue.add(created.jobId, file.name, candidate.templateName, {
      templateId: candidate.templateId, predecessorJobId,
    });
    patch(id, { state: "queued", jobId: created.jobId });
  } catch (error) {
    patch(id, { state: "failed", error: message(error, "読み取りを始められませんでした。") });
  }
}

type Match = Awaited<ReturnType<typeof findConfirmedMatch>>;

function confirmedMatch(file: File, candidate: ChatCandidate): Promise<Match> {
  // The comparison is only a convenience: without it the image is read as its own document.
  return findConfirmedMatch(file, candidate.templateVersionId).catch(() => null);
}

/** Starts the reading, as the next version of the confirmed document it matched, if any. */
function startAsUpdate(id: string, candidate: ChatCandidate, match: Match) {
  if (!find(id)) return;
  if (!match) return start(id, candidate);
  patch(id, {
    outcome: "continued",
    earlier: {
      jobId: match.jobId, fileName: match.label, templateName: candidate.templateName,
      status: "CONFIRMED", predecessorJobId: null,
    },
  });
  return start(id, candidate, match.jobId);
}

/**
 * Starts the reading, unless an unconfirmed earlier reading looks like the same sheet: then asks.
 * The confirmed documents are compared at the same time: a later photo of one is read as its next
 * version (also when the earlier reading is replaced or kept apart).
 */
async function proceed(id: string, candidate: ChatCandidate) {
  const reading = find(id);
  const file = files.get(id);
  if (!reading || !file) return start(id, candidate);
  patch(id, { state: "comparing", template: candidate, templateName: candidate.templateName, error: null, confirmed: null });
  const confirmed = confirmedMatch(file, candidate);
  const sameBatch = new Set(readings.filter((item) => item.batch === reading.batch && item.jobId).map((item) => item.jobId));
  const pick = () => readingQueue.unconfirmed(candidate.templateId, candidate.templateName)
    .filter((item) => !sameBatch.has(item.jobId));
  if (pick().length === 0) return startAsUpdate(id, candidate, await confirmed);
  try {
    // A reading confirmed in another tab is no longer "before confirmation".
    await syncReadingQueue(pick().map((item) => item.jobId));
    const queued = pick();
    if (queued.length === 0) return startAsUpdate(id, candidate, await confirmed);
    const result = await api.compareContinuations(file, queued.map((item) => item.jobId));
    const best = result.items
      .filter((item) => item.continuation)
      .sort((a, b) => (b.similarity ?? 0) - (a.similarity ?? 0))[0];
    const earlier = best ? queued.find((item) => item.jobId === best.jobId) : undefined;
    const match = await confirmed;
    if (!find(id)) return;
    if (!earlier) return startAsUpdate(id, candidate, match);
    patch(id, {
      state: "continuing",
      confirmed: match && { jobId: match.jobId, label: match.label },
      earlier: {
        jobId: earlier.jobId, fileName: earlier.fileName, templateName: earlier.templateName,
        status: earlier.status, predecessorJobId: earlier.predecessorJobId ?? null,
      },
    });
  } catch {
    if (find(id)) await startAsUpdate(id, candidate, await confirmed);
  }
}

async function classify(id: string) {
  const file = files.get(id);
  if (!file) return;
  try {
    const result = await api.classifyTemplate(file);
    const matched = result.candidates.some((candidate) => candidate.matched);
    const decided = result.candidates.find((candidate) => candidate.templateVersionId === result.decidedTemplateVersionId);
    patch(id, { candidates: result.candidates, matched });
    if (decided) await proceed(id, decided);
    else patch(id, { state: "choosing" });
  } catch (error) {
    patch(id, { state: "failed", error: message(error, "画像を確認できませんでした。") });
  }
}

// Readings waiting for the earlier one to be confirmed: start as its update once it is.
function settleWaiting(statuses: Map<string, string | null>) {
  for (const reading of readings) {
    if (reading.state !== "waiting" || !reading.earlier || !reading.template) continue;
    if (!statuses.has(reading.earlier.jobId)) continue;
    const status = statuses.get(reading.earlier.jobId) ?? null;
    if (status === "CONFIRMED") {
      patch(reading.id, { outcome: "continued" });
      void start(reading.id, reading.template, reading.earlier.jobId);
    } else if (status === null || ENDED.has(status)) {
      patch(reading.id, {
        state: "failed",
        error: `「${reading.earlier.fileName}」が確定されないまま終わったため、続きとして読み取れませんでした。`,
      });
    } else if (status !== reading.earlier.status) {
      patch(reading.id, { earlier: { ...reading.earlier, status } });
    }
  }
}

readingQueue.subscribe(() => {
  const statuses = new Map(readingQueue.snapshot().map((item) => [item.jobId, item.status] as [string, string | null]));
  settleWaiting(new Map([...statuses].filter(([, status]) => status === "CONFIRMED" || (status !== null && ENDED.has(status)))));
});

let waitTimer: number | null = null;
function watchWaiting() {
  const waiting = () => readings.filter((reading) => reading.state === "waiting" && reading.earlier);
  if (waitTimer !== null || waiting().length === 0) return;
  waitTimer = window.setInterval(() => {
    const ids = [...new Set(waiting().map((reading) => reading.earlier!.jobId))];
    if (ids.length === 0) {
      if (waitTimer !== null) window.clearInterval(waitTimer);
      waitTimer = null;
      return;
    }
    if (document.hidden) return;
    void Promise.all(ids.map((jobId) => api.job(jobId)
      .then((job) => [jobId, job.status] as const)
      .catch((error: unknown) => [jobId, error instanceof ApiError && error.code === "JOB_NOT_FOUND" ? null : undefined] as const)))
      .then((pairs) => settleWaiting(new Map(
        pairs.filter((pair): pair is readonly [string, string | null] => pair[1] !== undefined),
      )));
  }, WAIT_POLL_MS);
}

export const chatReadings = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  snapshot: sync,
  attachments: () => attachments,
  preview: (id: string) => previews.get(id) ?? null,

  /** Puts images into the message box; they are read only when the message is sent. */
  attach(list: File[]) {
    const room = MAX_ATTACHMENTS - attachments.length;
    const images = list.filter((file) => file.type.startsWith("image/")).slice(0, Math.max(room, 0));
    const added = images.map((file): ChatAttachment => {
      const id = createIdempotencyKey("ocr").slice(4);
      const preview = URL.createObjectURL(file);
      files.set(id, file);
      previews.set(id, preview);
      return { id, fileName: file.name || "画像", preview };
    });
    if (added.length > 0) {
      attachments = [...attachments, ...added];
      notify();
    }
    return { added: added.length, skipped: list.filter((file) => file.type.startsWith("image/")).length - added.length };
  },

  detach(id: string) {
    if (!attachments.some((item) => item.id === id)) return;
    release(id);
    attachments = attachments.filter((item) => item.id !== id);
    notify();
  },

  /**
   * Sends the attached images: each is classified and read (or asked about). With `turnId` they belong
   * to the message sent with them, and their reading is shown under that message's answer.
   */
  send(conversationId: string, afterTurnId: number | null, turnId: number | null = null) {
    if (attachments.length === 0) return 0;
    const batch = createIdempotencyKey("ocr").slice(4);
    const sent = attachments.map((item): ChatReading => ({
      id: item.id, conversationId, afterTurnId, turnId, batch, fileName: item.fileName, state: "classifying", candidates: [], matched: false,
      template: null, templateName: null, earlier: null, outcome: null, jobId: null, error: null,
      key: createIdempotencyKey("ocr"),
    }));
    attachments = [];
    update([...readings, ...sent]);
    sent.forEach((reading) => void classify(reading.id));
    return sent.length;
  },

  choose(id: string, candidate: ChatCandidate) {
    const reading = find(id);
    if (reading?.state !== "choosing" && reading?.state !== "failed") return;
    void proceed(id, candidate);
  },

  /** Reads it as the update of the earlier reading, once that one is confirmed. */
  continueEarlier(id: string) {
    const reading = find(id);
    if (reading?.state !== "continuing" || !reading.earlier || !reading.template) return;
    if (reading.earlier.status === "CONFIRMED") {
      patch(id, { outcome: "continued" });
      void start(id, reading.template, reading.earlier.jobId);
      return;
    }
    patch(id, { state: "waiting" });
    watchWaiting();
  },

  /** Discards the earlier reading (and any corrections made to it) and reads this image instead. */
  async replaceEarlier(id: string) {
    const reading = find(id);
    if (reading?.state !== "continuing" || !reading.earlier || !reading.template) return;
    const { earlier, template } = reading;
    patch(id, { state: "starting", error: null });
    try {
      try {
        await api.deleteJob(earlier.jobId);
      } catch (error) {
        // It started reading meanwhile: a reading in progress is cancelled instead.
        if (!(error instanceof ApiError) || error.code !== "INVALID_STATE_TRANSITION") throw error;
        await api.cancel(earlier.jobId);
      }
    } catch (error) {
      if (!(error instanceof ApiError) || error.code !== "JOB_NOT_FOUND") {
        patch(id, { state: "continuing", error: message(error, "前の読み取りを置き換えられませんでした。") });
        return;
      }
    }
    readingQueue.remove(earlier.jobId);
    patch(id, { outcome: "replaced" });
    await start(id, template, earlier.predecessorJobId ?? reading.confirmed?.jobId ?? undefined);
  },

  /** Reads it as a document of its own, leaving the earlier reading as it is. */
  readSeparately(id: string) {
    const reading = find(id);
    if ((reading?.state !== "continuing" && reading?.state !== "waiting") || !reading.template) return;
    const { confirmed } = reading;
    void startAsUpdate(id, reading.template, confirmed ? { ...confirmed, seriesVersion: 0, similarity: 0 } : null);
  },

  /** Retries a failed image: the template guess again, or the start with the template already picked. */
  retry(id: string) {
    const reading = find(id);
    if (!reading || reading.state !== "failed" || !files.has(id)) return;
    patch(id, { state: "classifying", error: null, earlier: null, outcome: null, confirmed: null });
    void classify(id);
  },

  dismiss(id: string) {
    release(id);
    update(readings.filter((reading) => reading.id !== id));
  },

  /** Forgets the images sent with a turn removed from the conversation (their readings go on). */
  dismissTurn(turnId: number) {
    const gone = readings.filter((reading) => reading.turnId === turnId);
    if (gone.length === 0) return;
    gone.forEach((reading) => release(reading.id));
    update(readings.filter((reading) => reading.turnId !== turnId));
  },

  hasFile: (id: string) => files.has(id),

  /** Forgets the images of a conversation removed from the history (their readings go on). */
  dropConversation(conversationId: string) {
    const gone = sync().filter((reading) => reading.conversationId === conversationId);
    if (gone.length === 0) return;
    gone.forEach((reading) => release(reading.id));
    update(readings.filter((reading) => reading.conversationId !== conversationId));
  },

  /** Forgets the images of conversations no longer in the history (pushed out of it, or removed in another tab). */
  keepConversations(conversationIds: Set<string>) {
    const gone = sync().filter((reading) => !conversationIds.has(reading.conversationId));
    if (gone.length === 0) return;
    gone.forEach((reading) => release(reading.id));
    update(readings.filter((reading) => conversationIds.has(reading.conversationId)));
  },
};

export function useChatReadings() {
  return useSyncExternalStore(chatReadings.subscribe, chatReadings.snapshot);
}

export function useChatAttachments() {
  return useSyncExternalStore(chatReadings.subscribe, chatReadings.attachments);
}
