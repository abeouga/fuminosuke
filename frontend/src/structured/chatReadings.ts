import { useSyncExternalStore } from "react";
import { api, ApiError, createIdempotencyKey, type TemplateClassification } from "../api";
import { readingQueue } from "../readingQueue";

// Images dropped into the chat: the server guesses the template, the user picks one only when it is
// unsure, and the reading is then sent to the reading queue (the review screen is never opened for them).

export type ChatCandidate = TemplateClassification["candidates"][number];

export type ChatReading = {
  id: string;
  /** The chat turn this image was added after (null: before any turn). */
  afterTurnId: number | null;
  fileName: string;
  state: "classifying" | "choosing" | "starting" | "queued" | "failed";
  candidates: ChatCandidate[];
  /** Whether the server found at least one template the image aligns with. */
  matched: boolean;
  templateName: string | null;
  jobId: string | null;
  error: string | null;
  key: string;
};

const STORAGE_KEY = "fuminosuke-chat-readings-v1";
const MAX_READINGS = 40;
// The files themselves only live in this tab; after a reload a pending choice asks for the image again.
const files = new Map<string, File>();
const previews = new Map<string, string>();
const listeners = new Set<() => void>();
let readings: ChatReading[] = load();

function load(): ChatReading[] {
  try {
    const stored: unknown = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(stored)) return [];
    return (stored as ChatReading[]).map((reading) =>
      reading.state === "classifying" || reading.state === "starting"
        ? { ...reading, state: "failed", error: "画面を再読み込みしたため中断しました。画像をもう一度送ってください。" }
        : reading);
  } catch {
    return [];
  }
}

function update(next: ChatReading[]) {
  readings = next.slice(-MAX_READINGS);
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(readings));
  } catch {
    // Storage can be disabled; the list then lasts until the page is reloaded.
  }
  listeners.forEach((listener) => listener());
}

function patch(id: string, change: Partial<ChatReading>) {
  update(readings.map((reading) => reading.id === id ? { ...reading, ...change } : reading));
}

function message(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

async function start(id: string, candidate: ChatCandidate) {
  const reading = readings.find((item) => item.id === id);
  const file = files.get(id);
  if (!reading || !file) {
    patch(id, { state: "failed", error: "画像が見つかりません。もう一度送ってください。" });
    return;
  }
  patch(id, { state: "starting", templateName: candidate.templateName, error: null });
  try {
    const created = await api.createOcr(candidate.templateVersionId, file, true, reading.key);
    readingQueue.add(created.jobId, file.name, candidate.templateName);
    patch(id, { state: "queued", jobId: created.jobId });
  } catch (error) {
    patch(id, { state: "failed", error: message(error, "読み取りを始められませんでした。") });
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
    if (decided) await start(id, decided);
    else patch(id, { state: "choosing" });
  } catch (error) {
    patch(id, { state: "failed", error: message(error, "画像を確認できませんでした。") });
  }
}

export const chatReadings = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  snapshot: () => readings,
  preview: (id: string) => previews.get(id) ?? null,

  add(list: File[], afterTurnId: number | null) {
    const images = list.filter((file) => file.type.startsWith("image/"));
    const added = images.map((file): ChatReading => {
      const id = createIdempotencyKey("ocr").slice(4);
      files.set(id, file);
      previews.set(id, URL.createObjectURL(file));
      return {
        id, afterTurnId, fileName: file.name || "画像", state: "classifying", candidates: [], matched: false,
        templateName: null, jobId: null, error: null, key: createIdempotencyKey("ocr"),
      };
    });
    if (added.length === 0) return 0;
    update([...readings, ...added]);
    added.forEach((reading) => void classify(reading.id));
    return added.length;
  },

  choose(id: string, candidate: ChatCandidate) {
    const reading = readings.find((item) => item.id === id);
    if (reading?.state !== "choosing" && reading?.state !== "failed") return;
    void start(id, candidate);
  },

  /** Retries a failed image: the template guess again, or the start with the template already picked. */
  retry(id: string) {
    const reading = readings.find((item) => item.id === id);
    if (!reading || reading.state !== "failed" || !files.has(id)) return;
    patch(id, { state: "classifying", error: null });
    void classify(id);
  },

  dismiss(id: string) {
    const url = previews.get(id);
    if (url) URL.revokeObjectURL(url);
    files.delete(id);
    previews.delete(id);
    update(readings.filter((reading) => reading.id !== id));
  },

  hasFile: (id: string) => files.has(id),

  clear() {
    previews.forEach((url) => URL.revokeObjectURL(url));
    files.clear();
    previews.clear();
    update([]);
  },
};

export function useChatReadings() {
  return useSyncExternalStore(chatReadings.subscribe, chatReadings.snapshot);
}
