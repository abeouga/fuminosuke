import { privateLocalStorage } from "../auth/transport";
import { EditingError } from "../editing/client";
import { structuredClient, type Evidence, type QuestionContext, type QuestionResult } from "./client";

/**
 * The question page's conversation, kept outside the page component.
 *
 * Leaving the page (opening a document, going home) unmounts the component but not this
 * store, so the conversation, the chosen documents, the unsent text and an answer still on
 * its way are all there when the user comes back. The same state is mirrored to
 * sessionStorage so a reload of the tab keeps it too (a request in flight at reload time
 * is shown as interrupted, with a retry).
 *
 * Finished conversations are kept in this browser's history (privateLocalStorage) and can be
 * opened again from the side bar; the current one is written there on every change.
 */

export type ScopeItem = { jobId: string; label: string };
export type ReplyTo = { questionId: string; value?: string; text?: string };
export type Turn = {
  id: number;
  /** What the user sees as their message. */
  text: string;
  /** The question text to send again on retry (for a reply: the reply itself). */
  request: { text: string; replyTo?: ReplyTo; documentIds?: string[]; context?: QuestionContext };
  scope: ScopeItem[];
  result: QuestionResult | null;
  error: string | null;
  errorCode: string | null;
  pending: boolean;
  /** Choice value picked from this turn's question, to mark it as chosen. */
  picked: string | null;
};
export type Conversation = { id: string; title: string; updatedAt: number; turns: Turn[] };
export type AskState = {
  /** The conversation being shown; it is in `history` as soon as it has a turn. */
  conversationId: string;
  turns: Turn[];
  /** Past conversations, newest first. */
  history: Conversation[];
  scope: string[];
  filter: string;
  templateId: string;
  draft: string;
  /** When true the next message answers the open question instead of starting a new one. */
  replyMode: boolean;
  /** The source document shown beside the chat, with the answer's cells highlighted. */
  viewer: AskViewer | null;
  /** The document left open in each conversation (this tab only), restored when it is shown again. */
  viewers: Record<string, AskViewer>;
};

export type AskViewer = {
  jobId: string;
  documentName: string | null;
  /** The answer's cells in this document (each highlighted on the page). */
  evidence: Evidence[];
  /** The cell picked last; it is scrolled into view and shown below the page. */
  focus: string | null;
};

const STORAGE_KEY = "fuminosuke-ask-v1";
const HISTORY_KEY = "fuminosuke-ask-history-v1";
const MAX_TURNS = 60;
const MAX_CONVERSATIONS = 50;
const INTERRUPTED = "画面の再読み込みで回答を受け取れませんでした。もう一度送ってください。";
export const STOPPED = "STOPPED";
/** The running question's request, so the stop button can drop it. */
const running = new Map<number, AbortController>();

function newConversationId() {
  return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/** A request cut off by a reload (or left behind in another conversation) is shown as interrupted. */
function settled(turns: Turn[]): Turn[] {
  if (!turns.some((turn) => turn.pending)) return turns;
  return turns.map((turn) => turn.pending ? { ...turn, pending: false, error: INTERRUPTED, errorCode: "INTERRUPTED" } : turn);
}

function loadHistory(): Conversation[] {
  try {
    const value: unknown = JSON.parse(privateLocalStorage.getItem(HISTORY_KEY) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value
      .filter((item): item is Conversation => typeof item?.id === "string" && Array.isArray(item.turns))
      .map((item) => ({ ...item, turns: settled(item.turns) }));
  } catch {
    return [];
  }
}

function initial(): AskState {
  const history = loadHistory();
  const empty: AskState = { conversationId: newConversationId(), turns: [], history, scope: [], filter: "", templateId: "", draft: "", replyMode: true, viewer: null, viewers: {} };
  try {
    const stored = window.sessionStorage.getItem(STORAGE_KEY);
    if (!stored) return empty;
    const value = JSON.parse(stored) as Partial<AskState>;
    const turns = Array.isArray(value.turns) ? value.turns : [];
    const conversationId = typeof value.conversationId === "string" ? value.conversationId : empty.conversationId;
    const viewers = value.viewers && typeof value.viewers === "object" ? value.viewers : {};
    return {
      ...empty,
      ...value,
      conversationId,
      history,
      viewers,
      viewer: viewers[conversationId] ?? null,
      turns: settled(turns),
    };
  } catch {
    return empty;
  }
}

let state: AskState = initial();
let nextId = [...state.turns, ...state.history.flatMap((item) => item.turns)].reduce((max, turn) => Math.max(max, turn.id), 0) + 1;
const listeners = new Set<() => void>();

function titleOf(turns: Turn[]): string {
  const first = turns.find((turn) => !turn.request.replyTo)?.text ?? turns[0]?.text ?? "";
  const line = first.replace(/\s+/g, " ").trim();
  return line.length > 40 ? `${line.slice(0, 40)}…` : line || "新しい会話";
}

/** The current conversation written into the history (moved to the top when it changed). */
function withCurrent(current: AskState): Conversation[] {
  const others = current.history.filter((item) => item.id !== current.conversationId);
  if (current.turns.length === 0) return others;
  const previous = current.history.find((item) => item.id === current.conversationId);
  const changed = !previous || previous.turns !== current.turns;
  const entry: Conversation = {
    id: current.conversationId,
    title: titleOf(current.turns),
    updatedAt: changed ? Date.now() : previous.updatedAt,
    turns: current.turns,
  };
  return changed ? [entry, ...others].slice(0, MAX_CONVERSATIONS) : current.history.map((item) => item.id === entry.id ? entry : item);
}

function save() {
  try {
    const { history: _history, viewer: _viewer, ...rest } = state;  // viewer lives in viewers
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(rest));
  } catch {
    // Storage full or blocked: the conversation still lives for this page visit.
  }
  try {
    privateLocalStorage.setItem(HISTORY_KEY, JSON.stringify(state.history));
  } catch {
    // The history is a convenience; without storage it lasts for this page visit.
  }
}

function set(patch: Partial<AskState> | ((current: AskState) => Partial<AskState>)) {
  const next = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
  // Remember the shown conversation's document panel, so switching back reopens it.
  const viewers = { ...next.viewers };
  if (next.viewer) viewers[next.conversationId] = next.viewer;
  else delete viewers[next.conversationId];
  state = { ...next, viewers, history: withCurrent(next) };
  save();
  for (const listener of listeners) listener();
}

/**
 * Another tab of this browser changed the history (a conversation removed or added there): take
 * its list, so this tab does not write its older list back and bring removed conversations back.
 * When the conversation shown here was removed there, this tab starts an empty one.
 */
function syncHistory(event: StorageEvent) {
  if (event.storageArea !== window.localStorage || event.newValue === null || !event.key?.endsWith(HISTORY_KEY)) return;
  if (event.newValue === JSON.stringify(state.history)) return;
  const history = loadHistory();
  const removedHere = state.history.some((item) => item.id === state.conversationId)
    && !history.some((item) => item.id === state.conversationId) && !askStore.busy();
  const keep = new Set(history.map((item) => item.id));
  const viewers = Object.fromEntries(Object.entries(state.viewers).filter(([id]) => keep.has(id) || id === state.conversationId));
  if (removedHere) {
    state = { ...state, history, viewers, conversationId: newConversationId(), turns: [], viewer: null, replyMode: true };
  } else {
    // The shown conversation keeps this tab's turns (they may be newer) without moving in the list.
    const mine = history.map((item) => item.id === state.conversationId ? { ...item, turns: state.turns } : item);
    state = { ...state, history: mine, viewers };
    if (!mine.some((item) => item.id === state.conversationId)) state = { ...state, history: withCurrent(state) };
  }
  try {
    const { history: _history, viewer: _viewer, ...rest } = state;
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(rest));
  } catch {
    // The conversation still lives for this page visit.
  }
  for (const listener of listeners) listener();
}

if (typeof window !== "undefined") window.addEventListener("storage", syncHistory);

function updateTurn(id: number, patch: Partial<Turn>) {
  set((current) => ({ turns: current.turns.map((turn) => turn.id === id ? { ...turn, ...patch } : turn) }));
}

/** The latest turn's question that still waits for an answer (a real question, not a re-pick). */
export function openQuestion(current: AskState) {
  const last = current.turns[current.turns.length - 1];
  const question = last?.result?.question;
  return last && !last.pending && question && !question.optional && last.result?.state === "clarifying" ? { turn: last, question } : null;
}

/** The turn whose answer asked the given question. */
function askedBy(questionId: string): Turn | undefined {
  return state.turns.find((item) => item.result?.question?.questionId === questionId);
}

/**
 * The question to send again when the server forgot the open question: the original question,
 * with every answer given to it on the way (in order) written after it, and a picked document
 * kept as the documents to look in. Null when part of the chain was removed from the chat.
 */
function recoveredRequest(turn: Turn): Turn["request"] | null {
  const replies: { turn: Turn; kind: string | undefined }[] = [];
  let current: Turn | undefined = turn;
  while (current?.request.replyTo) {
    const asker = askedBy(current.request.replyTo.questionId);
    if (!asker) return null;
    replies.unshift({ turn: current, kind: asker.result?.question?.kind });
    current = asker;
  }
  if (!current) return null;
  const documentIds = [...replies].reverse().find((item) => item.kind === "document" && item.turn.request.replyTo?.value)?.turn.request.replyTo?.value;
  const answers = replies.filter((item) => item.kind !== "document").map((item) => item.turn.text);
  const text = answers.length > 0 ? `${current.request.text}（${answers.join("、")}）` : current.request.text;
  const scope = documentIds ? [documentIds] : current.request.documentIds;
  const context = current.request.context;
  return { text, ...(scope ? { documentIds: scope } : {}), ...(context ? { context } : {}) };
}

async function run(id: number, recovering = false) {
  const turn = state.turns.find((item) => item.id === id);
  if (!turn) return;
  const controller = new AbortController();
  running.set(id, controller);
  try {
    const result = await structuredClient.ask(turn.request, { conversationId: state.conversationId, turnId: id, recovered: recovering }, controller.signal);
    if (controller.signal.aborted) return;
    updateTurn(id, { result, pending: false, error: null, errorCode: null });
    if (result.open) {
      // 「はじめを出して」: the document itself, beside the chat (no cell picked on it).
      const jobId = result.open;
      const documentName = result.documents.find((item) => item.jobId === jobId)?.documentName ?? null;
      set({ viewer: { jobId, documentName, evidence: [], focus: null } });
    }
  } catch (error) {
    // Stopped (or removed) while waiting: the turn already says so.
    if (controller.signal.aborted) return;
    // The server forgot the open question (restart, or a long pause): ask the original
    // question again with the answer written into it, once, without bothering the user.
    const request = !recovering && error instanceof EditingError && error.code === "QA_QUESTION_EXPIRED" ? recoveredRequest(turn) : null;
    if (request) {
      updateTurn(id, { request });
      await run(id, true);
      return;
    }
    const message = error instanceof EditingError ? error.message : "質問を処理できませんでした。接続を確認してもう一度送ってください。";
    updateTurn(id, { error: message, errorCode: error instanceof EditingError ? error.code : null, pending: false });
  } finally {
    if (running.get(id) === controller) running.delete(id);
  }
}

/** The question a turn belongs to: its own text, or the asked question with the answers given. */
function originText(turn: Turn): string {
  const answers: string[] = [];
  let current: Turn | undefined = turn;
  while (current?.request.replyTo) {
    answers.unshift(current.text);
    current = askedBy(current.request.replyTo.questionId);
  }
  const asked = current?.request.text ?? "";
  return answers.length > 0 && current ? `${asked}（${answers.join("、")}）` : asked || turn.text;
}

/**
 * The last answer still in the chat (removed exchanges are not used): the documents it was about
 * and its question, so a follow-up such as 「母は？」 is read about the same document.
 */
function conversationContext(): QuestionContext | undefined {
  const last = [...state.turns].reverse().find((turn) => !turn.pending && turn.result && turn.result.documents.length > 0);
  if (!last?.result) return undefined;
  return { documentIds: last.result.documents.map((document) => document.jobId).slice(0, 20), previous: originText(last).slice(0, 2000) || null };
}

function push(text: string, base: Turn["request"], scope: ScopeItem[]) {
  const context = conversationContext();
  const request = context ? { ...base, context } : base;
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

  /** Stop the question being answered: its request is dropped and the server cancels the work. */
  stop() {
    const turn = state.turns.find((item) => item.pending);
    if (!turn) return;
    running.get(turn.id)?.abort();
    running.delete(turn.id);
    updateTurn(turn.id, { pending: false, error: "回答を止めました。", errorCode: STOPPED });
  },

  /** Send a failed turn again (an expired follow-up recovers through its original question). */
  retry(turnId: number) {
    const turn = state.turns.find((item) => item.id === turnId);
    if (!turn || askStore.busy()) return;
    updateTurn(turnId, { pending: true, error: null, errorCode: null });
    void run(turnId);
  },

  /**
   * Remove one exchange (the user's message and its answer) from the conversation; nothing else
   * is removed. It is no longer used as context: a later reply sent again recovers only through
   * questions still in the chat, and a removed answer's pick can be chosen again. A turn still
   * waiting is cancelled (its late answer finds no turn and is dropped). A document opened only
   * from the removed answer is closed.
   */
  removeTurn(turnId: number) {
    const target = state.turns.find((item) => item.id === turnId);
    if (!target) return;
    running.get(turnId)?.abort();
    running.delete(turnId);
    const reply = target.request.replyTo;
    const turns = state.turns
      .filter((item) => item.id !== turnId)
      .map((item) => reply && item.result?.question?.questionId === reply.questionId && item.picked === (reply.value ?? null)
        ? { ...item, picked: null }
        : item);
    const shown = (jobId: string, list: Turn[]) => list.some((item) =>
      item.result?.evidence.some((evidence) => evidence.jobId === jobId)
      || item.result?.documents.some((document) => document.jobId === jobId)
      || item.result?.question?.choices.some((choice) => choice.value === jobId));
    const viewer = state.viewer && shown(state.viewer.jobId, [target]) && !shown(state.viewer.jobId, turns) ? null : state.viewer;
    void structuredClient.logRemoved(state.conversationId, [{
      turnId: target.id,
      text: target.text,
      reply: Boolean(reply),
      state: target.result?.state ?? null,
      answer: target.result?.answer?.slice(0, 4000) ?? target.error,
      pending: target.pending,
    }]);
    set({ turns, viewer, replyMode: true });
  },

  /** Start a new conversation; the current one stays in the history. */
  clear() {
    set({ conversationId: newConversationId(), turns: [], viewer: null, replyMode: true, draft: "" });
  },

  /** Show a conversation from the history. */
  open(id: string) {
    const conversation = state.history.find((item) => item.id === id);
    if (!conversation || askStore.busy() || id === state.conversationId) return;
    set({ conversationId: id, turns: settled(conversation.turns), viewer: state.viewers[id] ?? null, replyMode: true, draft: "" });
  },

  /** Remove a conversation from the history (the shown one is replaced by an empty one). */
  remove(id: string) {
    if (id === state.conversationId) {
      if (askStore.busy()) return;
      set((current) => ({ history: current.history.filter((item) => item.id !== id), conversationId: newConversationId(), turns: [], viewer: null, replyMode: true }));
      return;
    }
    set((current) => {
      const { [id]: _removed, ...viewers } = current.viewers;
      return { history: current.history.filter((item) => item.id !== id), viewers };
    });
  },
};
