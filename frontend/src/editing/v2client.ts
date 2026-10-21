import { authenticatedFetch } from "../auth/transport";
import type { CommandRequest, CommandResult, ContextResult, ContextUpdate, Event, ManualAuthoring, ManualOcr, ScopeAnswer, SessionCreate, SessionResult } from "./v2.generated";
export { EditingError } from "./client";
import { EditingError } from "./client";
export type Owner = { sessionId: string; token: string };
export type EditingStatus = { enabled: boolean; plannerConnected: boolean; voiceConnected: boolean; templateAnalysisEnabled?: boolean; voiceSecondsRemaining?: number | null };
const base = "/api/v2/editing";
export async function editingRequest<T>(path: string, owner?: Owner, body?: unknown, method = "GET"): Promise<T> {
  const response = await authenticatedFetch(base + path, { method, credentials: "include", headers: {
    ...(owner ? { "X-Editing-Session": owner.token } : {}), ...(body === undefined ? {} : { "Content-Type": "application/json" }),
  }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value = await response.json();
  if (!response.ok) throw new EditingError(value.message ?? value.detail ?? "編集処理を完了できませんでした。", value.code ?? "EDITING_FAILED", response.status);
  return value as T;
}
const request = editingRequest;
const path = (owner: Owner) => `/sessions/${owner.sessionId}`;
export const v2client = {
  status: () => request<EditingStatus>("/status"),
  create: (body: SessionCreate) => request<SessionResult>("/sessions", undefined, body, "POST"),
  context: (owner: Owner) => request<ContextResult>(path(owner) + "/context", owner),
  update: (owner: Owner, body: ContextUpdate) => request<ContextResult>(path(owner) + "/context", owner, body, "PUT"),
  command: (owner: Owner, body: CommandRequest) => request<CommandResult>(path(owner) + "/commands", owner, body, "POST"),
  authoring: (owner: Owner, body: ManualAuthoring) => request<CommandResult>(path(owner) + "/manual-authoring", owner, body, "POST"),
  ocr: (owner: Owner, body: ManualOcr) => request<CommandResult>(path(owner) + "/manual-ocr", owner, body, "POST"),
  scopeAnswer: (owner: Owner, body: ScopeAnswer) => request<CommandResult>(path(owner) + "/scope-answer", owner, body, "POST"),
  query: (owner: Owner, id: string) => request<CommandResult>(path(owner) + "/commands/" + encodeURIComponent(id), owner),
  close: (owner: Owner) => request(path(owner), owner, undefined, "DELETE"),
};

/** Authenticated SSE with an explicit cursor; event text is never executable. */
export async function readEvents(owner: Owner, cursor: string | null, signal: AbortSignal, receive: (event: Event) => void, connected?: () => void) {
  const response = await authenticatedFetch(base + path(owner) + "/events", { credentials: "include", signal,
    headers: { "X-Editing-Session": owner.token, ...(cursor ? { "Last-Event-ID": cursor } : {}) } });
  if (!response.ok || !response.body) throw new Error("編集状態の接続を取得できません。");
  connected?.();
  const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
  try {
    while (true) {
      const item = await reader.read(); if (item.done) break;
      buffer += decoder.decode(item.value, { stream: true }).replaceAll("\r\n", "\n");
      if (buffer.length > 2_097_152) throw new Error("編集状態の応答が上限を超えています。");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const data = block.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (data) receive(JSON.parse(data) as Event);
      }
    }
  } finally { reader.releaseLock(); }
}
