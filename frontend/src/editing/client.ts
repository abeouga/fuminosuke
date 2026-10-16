import { authenticatedFetch } from "../auth/transport";
import type { Batch, BatchResult, CatalogResult, ContextResult, ContextUpdate, ConversationStart, ReceiptResult, ResolveTurn, SessionCreate, SessionResult, StartedTurn, TargetSearch, TurnResolution, TurnStatus, Undo } from "./generated";
import type { TemplateAuthoringUpdate } from "../assistant/generatedAuthoring";
import type { OcrDocument } from "../types";

export type ManualEdit = Pick<Batch, "contractVersion" | "requestId" | "expectedRevision" | "contextRevision" | "localEditEpoch"> & {
  origin: "manual"; turnId: null; draft: TemplateAuthoringUpdate;
};
export type ManualResult = BatchResult & { substitutions: Record<string, string> };
export type ManualOcrEdit = Pick<ManualEdit, "contractVersion" | "requestId" | "expectedRevision" | "contextRevision" | "localEditEpoch" | "origin" | "turnId"> & {
  draft: Pick<OcrDocument, "fields" | "reviewItems">;
};
export type ManualView = { context: ContextResult; draft: TemplateAuthoringUpdate };
export type ManualReceipt = Omit<ReceiptResult, "result"> & { result: ManualResult | null };
export type EditingOwner = { sessionId: string; token: string };

export class EditingError extends Error {
  constructor(message: string, public readonly code: string, public readonly status: number) { super(message); }
}
export async function editingRequest<T>(path: string, owner?: EditingOwner, body?: unknown, method = "GET"): Promise<T> {
  const response = await authenticatedFetch(`/api/v1/editing${path}`, {
    method, credentials: "include", headers: {
      ...(owner ? { "X-Editing-Session": owner.token } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok) throw new EditingError(value.message ?? value.detail ?? "編集処理を完了できませんでした。", value.code ?? "EDITING_FAILED", response.status);
  return value as T;
}
const request = editingRequest;
const sessionPath = (owner: EditingOwner) => `/sessions/${owner.sessionId}`;
export const editingClient = {
  status: () => request<{ contractVersion: "editing.v1"; enabled: boolean }>("/status"),
  create: (body: SessionCreate) => request<SessionResult>("/sessions", undefined, body, "POST"),
  context: (owner: EditingOwner) => request<ContextResult>(`${sessionPath(owner)}/context`, owner),
  update: (owner: EditingOwner, body: ContextUpdate) => request<ContextResult>(`${sessionPath(owner)}/context`, owner, body, "PUT"),
  manualView: (owner: EditingOwner) => request<ManualView>(`${sessionPath(owner)}/manual-view`, owner),
  manual: (owner: EditingOwner, body: ManualEdit) => request<ManualResult>(`${sessionPath(owner)}/manual-edits`, owner, body, "POST"),
  manualOcr: (owner: EditingOwner, body: ManualOcrEdit) => request<BatchResult>(`${sessionPath(owner)}/manual-ocr-edits`, owner, body, "POST"),
  batch: (owner: EditingOwner, body: Batch) => request<BatchResult>(`${sessionPath(owner)}/batches`, owner, body, "POST"),
  undo: (owner: EditingOwner, body: Undo) => request<BatchResult>(`${sessionPath(owner)}/undo`, owner, body, "POST"),
  receipt: (owner: EditingOwner, id: string) => request<ReceiptResult>(`${sessionPath(owner)}/receipts/${id}`, owner),
  manualReceipt: (owner: EditingOwner, id: string) => request<ManualReceipt>(`${sessionPath(owner)}/manual-receipts/${id}`, owner),
  find: (owner: EditingOwner, body: TargetSearch) => request<CatalogResult>(`${sessionPath(owner)}/targets:find`, owner, body, "POST"),
  close: (owner: EditingOwner) => request<{ state: "CLOSED" }>(sessionPath(owner), owner, undefined, "DELETE"),
  beginTurn: (owner: EditingOwner, body: ConversationStart) => request<StartedTurn>(`${sessionPath(owner)}/conversation/turns`, owner, body, "POST"),
  resolveTurn: (owner: EditingOwner, id: string, body: ResolveTurn) => request<TurnResolution>(`${sessionPath(owner)}/conversation/turns/${id}/resolve`, owner, body, "POST"),
  turnStatus: (owner: EditingOwner, id: string) => request<TurnStatus>(`${sessionPath(owner)}/conversation/turns/${id}`, owner),
  cancelTurn: (owner: EditingOwner, id: string) => request<TurnStatus>(`${sessionPath(owner)}/conversation/turns/${id}`, owner, undefined, "DELETE"),
};
