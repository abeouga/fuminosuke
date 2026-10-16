import { authenticatedFetch } from "../auth/transport";
import { editingRequest, type Owner } from "./v2client";
import type { AnalysisCreate, AnalysisEventResult, AnalysisMergeRequest, AnalysisPlanResult, AnalysisResult, AnalysisRevertRequest, MergeResult } from "./template-analysis.generated";
const path = (owner: Owner, id = "") => `/sessions/${owner.sessionId}/template-analyses${id ? `/${encodeURIComponent(id)}` : ""}`;
export const templateAnalysisClient = {
  plan: (owner: Owner, body: AnalysisCreate) => editingRequest<AnalysisPlanResult>(path(owner) + "/plan", owner, body, "POST"),
  create: (owner: Owner, body: AnalysisCreate) => editingRequest<AnalysisResult>(path(owner), owner, body, "POST"),
  get: (owner: Owner, id: string) => editingRequest<AnalysisResult>(path(owner, id), owner),
  cancel: (owner: Owner, id: string) => editingRequest<AnalysisResult>(path(owner, id) + "/cancel", owner, {}, "POST"),
  merge: (owner: Owner, id: string, body: AnalysisMergeRequest) => editingRequest<MergeResult>(path(owner, id) + "/merge", owner, body, "POST"),
  revert: (owner: Owner, id: string, mergeId: string, body: AnalysisRevertRequest) => editingRequest<AnalysisResult>(path(owner, id) + `/merges/${encodeURIComponent(mergeId)}/revert`, owner, body, "POST"),
};
export async function analysisEvents(owner: Owner, id: string, sequence: number, signal: AbortSignal, receive: (event: AnalysisEventResult) => void) {
  const response = await authenticatedFetch("/api/v2/editing" + path(owner, id) + "/events", { credentials: "include", signal,
    headers: { "X-Editing-Session": owner.token, ...(sequence ? { "Last-Event-ID": `${id}:${sequence}` } : {}) } });
  if (!response.ok || !response.body) throw new Error("解析状態へ接続できません。結果を再取得してください。");
  const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
  try {
    while (!signal.aborted) {
      const part = await reader.read(); if (part.done) break;
      buffer += decoder.decode(part.value, { stream: true });
      if (buffer.length > 2_097_152) throw new Error("解析状態の応答が上限を超えました。");
      buffer = buffer.replaceAll("\r\n", "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        const data = block.split("\n").filter(s => s.startsWith("data:")).map(s => s.slice(5).trimStart()).join("\n");
        if (data) receive(JSON.parse(data) as AnalysisEventResult);
      }
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
