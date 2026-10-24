import { authenticatedFetch } from "../auth/transport";
import type { ReviewBlock } from "../types";
import { EditingError } from "../editing/client";

export type StructuredStatus = { contractVersion: "structured.v1"; enabled: boolean; qaConnected: boolean; voiceConnected: boolean };
export type IdentityValue = { label: string; fieldId: string | null; targetPath: string; value: string | null; status: string | null; groupLabel: string | null };
export type DocumentSummary = {
  jobId: string; kind: "single" | "paged"; templateId: string; templateVersionId: string | null;
  templateName: string; documentName: string | null; confirmedAt: string; confirmedRevision: number; identity: IdentityValue[];
  seriesId: string | null; seriesVersion: number;
  /** Small image of the (first) page; absent from servers older than the shelf thumbnails. */
  thumbnailUrl?: string | null;
};
export type Box = { x: number; y: number; width: number; height: number };
export type DocumentPage = { pageId: string | null; pageNumber: number; imageUrl: string };
export type ReadingStatus = "readable" | "uncertain" | "blank";
export type StructuredReading = {
  fieldId: string; logicalFieldId: string | null; key: string; targetPath: string; label: string;
  section: string | null; pageId: string | null; tableId: string | null; rowNumber: number | null; columnId: string | null;
  raw: string | null; normalized: string | null; value: string | null; status: ReadingStatus; reviewReason: string | null;
  /** Aligned crop of the cell; absent from servers older than the document shelf. */
  cropUrl?: string | null;
  /** A selection box (選択欄); older servers leave it out. */
  choice?: boolean;
  /** The cell's rectangle on its page image (aligned-image pixels). */
  box?: Box | null;
};
export type StructuredTable = {
  id: string; title: string; collectionPath: string | null; columns: { id: string; label: string }[];
  rows: { id: string; rowNumber: number; cells: { columnId: string; fieldId: string | null }[] }[];
};
export type ExtraReading = { key: string; label: string; raw: string | null; normalized: string | null; value: string | null; status: ReadingStatus };
/** A printed heading, table title, column name or item name, and what it names. */
export type HeadingCell = {
  cellId: string; kind: "section" | "table" | "column" | "item"; box: Box; pageId: string | null;
  section: string | null; tableIds: string[]; columnId: string | null; fieldIds: string[];
};
export type StructuredDocument = DocumentSummary & {
  fields: StructuredReading[]; tables: StructuredTable[]; extraFields: ExtraReading[]; pages?: DocumentPage[];
  /** Tables, headings and items under no heading from the top of the form (older servers: none). */
  blocks?: ReviewBlock[] | null;
  /** Absent from servers older than heading highlighting. */
  headings?: HeadingCell[];
};
/** A document choice also carries its form and identity (`detail`) and a picture. */
export type QuestionChoice = { value: string; label: string; detail?: string | null; thumbnailUrl?: string | null };
/** `optional`: the answer is already given; the choices only offer a different reading. */
export type Clarification = { questionId: string; kind: string; message: string; choices: QuestionChoice[]; optional: boolean };
export type AnswerTable = {
  columns: string[];
  rows: (string | null)[][];
  /** The cell each row was read from (same order as rows); absent in answers saved before it existed. */
  sources?: { jobId: string; fieldId: string | null }[];
};
export type Evidence = {
  jobId: string; documentName: string | null; fieldId: string | null; targetPath: string | null;
  label: string; raw: string | null; value: string | null; status: string; interpretedAs: string | null;
  cropUrl: string | null;
};
export type AnswerStep = { label: string; value: string };
export type FieldName = { label: string; kind: "field" | "column"; tableTitle: string | null };
export type QuestionResult = {
  contractVersion: "structured.v1"; state: "answered" | "clarifying" | "unanswerable"; answer: string;
  table: AnswerTable | null; steps: AnswerStep[]; evidence: Evidence[]; caveats: string[]; notes: string[]; question: Clarification | null;
  modelCalls: number; documents: { jobId: string; documentName: string | null }[];
  /** A general answer to a question the documents cannot answer (only values put in from them). */
  general?: boolean;
  /** The document the user asked to see (「はじめを出して」): opened beside the chat. */
  open?: string | null;
};
/** A reply carries either one of the offered choices (`value`) or the user's own words (`text`). */
export type QuestionRequest = {
  text: string; replyTo?: { questionId: string; value?: string; text?: string }; documentIds?: string[];
  /** The conversation's last answer (its documents and question), to fill in a follow-up. */
  context?: QuestionContext;
};
export type QuestionContext = { documentIds: string[]; previous: string | null };
/** Where a question was sent from in the chat; only written to the local chat log. */
export type ChatClient = { conversationId: string; turnId: number; recovered?: boolean };
export type ChatTurnRecord = { turnId: number; text: string; reply: boolean; state: string | null; answer: string | null; pending: boolean };

const base = "/api/v2/structured";

async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await authenticatedFetch(base + path, {
    method: body === undefined ? "GET" : "POST",
    credentials: "include",
    ...(signal ? { signal } : {}),
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok) {
    throw new EditingError(value.message ?? value.detail ?? "質問を処理できませんでした。", value.code ?? "STRUCTURED_FAILED", response.status);
  }
  return value as T;
}

export const structuredClient = {
  status: () => request<StructuredStatus>("/status"),
  documents: (q?: string) => request<{ items: DocumentSummary[] }>("/documents" + (q ? `?q=${encodeURIComponent(q)}` : "")),
  /** Every listed (newest confirmed) document, for the document shelf. */
  allDocuments: () => request<{ items: DocumentSummary[] }>("/documents?limit=1000"),
  document: (jobId: string) => request<StructuredDocument>(`/documents/${encodeURIComponent(jobId)}`),
  fields: (templateId: string) => request<{ items: FieldName[] }>(`/fields?templateId=${encodeURIComponent(templateId)}`),
  // Fields added later (notes, optional) default to empty so an older server still renders.
  /** `signal` stops the question (the server cancels the work still running). */
  ask: (body: QuestionRequest, client?: ChatClient, signal?: AbortSignal) => request<QuestionResult>("/questions", client ? { ...body, client } : body, signal).then((result) => ({
    ...result,
    notes: result.notes ?? [],
    question: result.question ? { ...result.question, optional: result.question.optional ?? false } : null,
  })),
  /** Tell the local chat log about removed exchanges (best effort; never blocks the chat). */
  logRemoved: (conversationId: string, turns: ChatTurnRecord[]) =>
    request<unknown>("/events", { type: "remove", conversationId, turns }).catch(() => undefined),
};
