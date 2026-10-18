import { authenticatedFetch } from "../auth/transport";
import { EditingError } from "../editing/client";

export type StructuredStatus = { contractVersion: "structured.v1"; enabled: boolean; qaConnected: boolean; voiceConnected: boolean };
export type IdentityValue = { label: string; fieldId: string | null; targetPath: string; value: string | null; status: string | null; groupLabel: string | null };
export type DocumentSummary = {
  jobId: string; kind: "single" | "paged"; templateId: string; templateVersionId: string | null;
  templateName: string; documentName: string | null; confirmedAt: string; confirmedRevision: number; identity: IdentityValue[];
  seriesId: string | null; seriesVersion: number;
};
export type QuestionChoice = { value: string; label: string };
export type Clarification = { questionId: string; kind: string; message: string; choices: QuestionChoice[] };
export type AnswerTable = { columns: string[]; rows: (string | null)[][] };
export type Evidence = {
  jobId: string; documentName: string | null; fieldId: string | null; targetPath: string | null;
  label: string; raw: string | null; value: string | null; status: string; interpretedAs: string | null;
  cropUrl: string | null;
};
export type AnswerStep = { label: string; value: string };
export type FieldName = { label: string; kind: "field" | "column"; tableTitle: string | null };
export type QuestionResult = {
  contractVersion: "structured.v1"; state: "answered" | "clarifying" | "unanswerable"; answer: string;
  table: AnswerTable | null; steps: AnswerStep[]; evidence: Evidence[]; caveats: string[]; question: Clarification | null;
  modelCalls: number; documents: { jobId: string; documentName: string | null }[];
};
export type QuestionRequest = { text: string; replyTo?: { questionId: string; value: string }; documentIds?: string[] };

const base = "/api/v2/structured";

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await authenticatedFetch(base + path, {
    method: body === undefined ? "GET" : "POST",
    credentials: "include",
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
  fields: (templateId: string) => request<{ items: FieldName[] }>(`/fields?templateId=${encodeURIComponent(templateId)}`),
  ask: (body: QuestionRequest) => request<QuestionResult>("/questions", body),
};

/** Review link for one evidence cell: the review page selects the field by its target path. */
export function evidenceHref(evidence: Evidence): string {
  const params = new URLSearchParams({ job: evidence.jobId });
  if (evidence.targetPath) params.set("field", evidence.targetPath);
  return `?${params.toString()}`;
}

/** Answer table as CSV text (RFC 4180 quoting) for the clipboard. */
export function tableCsv(table: AnswerTable): string {
  const cell = (value: string | null) => {
    const text = value ?? "";
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [table.columns, ...table.rows].map((row) => row.map(cell).join(",")).join("\r\n");
}
