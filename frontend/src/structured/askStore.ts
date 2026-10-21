import { EditingError } from "../editing/client";
import { structuredClient, type Evidence, type QuestionResult } from "./client";

/**
 * The question page's conversation, kept outside the page component.
 *
 * Leaving the page (opening a document, going home) unmounts the component but not this
 * store, so the conversation, the chosen documents, the unsent text and an answer still on
 * its way are all there when the user comes back. The same state is mirrored to
 * sessionStorage so a reload of the tab keeps it too (a request in flight at reload time
 * is shown as interrupted, with a retry).
 */

export type ScopeItem = { jobId: string; label: string };
export type ReplyTo = { questionId: string; value?: string; text?: string };
export type Turn = {
  id: number;
  /** What the user sees as their message. */
  text: string;
  /** The question text to send again on retry (for a reply: the reply itself). */
  request: { text: string; replyTo?: ReplyTo; documentIds?: string[] };
  scope: ScopeItem[];
  result: QuestionResult | null;
  error: string | null;
  errorCode: string | null;
  pending: boolean;
  /** Choice value picked from this turn's question, to mark it as chosen. */
  picked: string | null;
};
export type AskState = {
  turns: Turn[];
  scope: string[];
  filter: string;
  templateId: string;
  draft: string;
  /** When true the next message answers the open question instead of starting a new one. */
  replyMode: boolean;
  evidence: Evidence | null;
};

const STORAGE_KEY = "fuminosuke-ask-v1";
const MAX_TURNS = 60;
const INTERRUPTED = "画面の再読み込みで回答を受け取れませんでした。もう一度送ってください。";

function initial(): AskState {
  const empty: AskState = { turns: [], scope: [], filter: "", templateId: "", draft: "", replyMode: true, evidence: null };
  try {
    const stored = window.sessionStorage.getItem(STORAGE_KEY);
    if (!stored) return empty;
    const value = JSON.parse(stored) as Partial<AskState>;
    const turns = Array.isArray(value.turns) ? value.turns : [];
    return {
      ...empty,
      ...value,
      evidence: null,
      turns: turns.map((turn) => turn.pending ? { ...turn, pending: false, error: INTERRUPTED, errorCode: "INTERRUPTED" } : turn),
    };
  } catch {
    return empty;
  }
}

let state: AskState = initial();
let nextId = state.turns.reduce((max, turn) => Math.max(max, turn.id), 0) + 1;
const listeners = new Set<() => void>();

function save() {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ ...state, evidence: null }));
  } catch {
    // Storage full or blocked: the conversation still lives for this page visit.
  }
}

function set(patch: Partial<AskState> | ((current: AskState) => Partial<AskState>)) {
  state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
  save();
  for (const listener of listeners) listener();
}

function updateTurn(id: number, patch: Partial<Turn>) {
  set((current) => ({ turns: current.turns.map((turn) => turn.id === id ? { ...turn, ...patch } : turn) }));
}

/** The latest turn's question that still waits for an answer (a real question, not a re-pick). */
export function openQuestion(current: AskState) {
  const last = current.turns[current.turns.length - 1];
  const question = last?.result?.question;
  return last && !last.pending && question && !question.optional && last.result?.state === "clarifying" ? { turn: last, question } : null;
}

/** The question a follow-up belongs to: the nearest earlier turn that was not itself a reply. */
function originOf(turnId: number): Turn | undefined {
  const index = state.turns.findIndex((item) => item.id === turnId);
  return state.turns.slice(0, Math.max(index, 0)).reverse().find((item) => !item.request.replyTo);
}

async function run(id: number, recovering = false) {
  const turn = state.turns.find((item) => item.id === id);
  if (!turn) return;
  try {
    const result = await structuredClient.ask(turn.request);
    updateTurn(id, { result, pending: false, error: null, errorCode: null });
  } catch (error) {
    // The server forgot the open question (restart, or a long pause): ask the original
    // question again with the answer written into it, once, without bothering the user.
    const origin = originOf(id);
    if (!recovering && error instanceof EditingError && error.code === "QA_QUESTION_EXPIRED" && turn.request.replyTo && origin) {
      // Every answer given since the original question, in order, written after it.
      const answers = state.turns.filter((item) => item.id > origin.id && item.id <= id && item.request.replyTo).map((item) => item.text);
      const request = { text: `${origin.request.text}（${answers.join("、")}）`, ...(origin.request.documentIds ? { documentIds: origin.request.documentIds } : {}) };
      updateTurn(id, { request });
      await run(id, true);
      return;
    }
    const message = error instanceof EditingError ? error.message : "質問を処理できませんでした。接続を確認してもう一度送ってください。";
    updateTurn(id, { error: message, errorCode: error instanceof EditingError ? error.code : null, pending: false });
  }
}

function push(text: string, request: Turn["request"], scope: ScopeItem[]) {
  const id = nextId++;
  const turn: Turn = { id, text, request, scope, result: null, error: null, errorCode: null, pending: true, picked: null };
  set((current) => ({ turns: [...current.turns, turn].slice(-MAX_TURNS), replyMode: true }));
  void run(id);
}

export const askStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  getSnapshot: () => state,
  set,
  busy: () => state.turns.some((turn) => turn.pending),

  /** A new question over the given documents (none: every confirmed document). */
  ask(text: string, scope: ScopeItem[]) {
    const question = text.trim();
    if (!question || askStore.busy()) return;
    const documentIds = scope.length > 0 ? scope.map((item) => item.jobId) : undefined;
    push(question, { text: question, ...(documentIds ? { documentIds } : {}) }, scope);
  },

  /** Answer a turn's question with one of its choices. */
  choose(turnId: number, choice: { value: string; label: string }) {
    const turn = state.turns.find((item) => item.id === turnId);
    const question = turn?.result?.question;
    if (!turn || !question || askStore.busy()) return;
    updateTurn(turnId, { picked: choice.value });
    push(choice.label, { text: "", replyTo: { questionId: question.questionId, value: choice.value } }, turn.scope);
  },

  /** Answer the open question in the user's own words. */
  replyText(text: string) {
    const open = openQuestion(state);
    const words = text.trim();
    if (!open || !words || askStore.busy()) return;
    push(words, { text: "", replyTo: { questionId: open.question.questionId, text: words } }, open.turn.scope);
  },

  /** Send a failed turn again (an expired follow-up recovers through its original question). */
  retry(turnId: number) {
    const turn = state.turns.find((item) => item.id === turnId);
    if (!turn || askStore.busy()) return;
    updateTurn(turnId, { pending: true, error: null, errorCode: null });
    void run(turnId);
  },

  clear() {
    set({ turns: [], evidence: null, replyMode: true });
  },
};
