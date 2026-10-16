import { authenticatedFetch } from "../auth/transport";
import { ApiError } from "../api";
import type { AssistantContext, AssistantHistoryItem, AssistantPreview, AssistantPreviewProvider, AssistantReceipt, AssistantSnapshot, VoiceStatus, VoiceTurnResult } from "./contracts";
import { recoveryJournal, type PendingRequest } from "./recoveryJournal";

type Credentials = { token: string; contextRevision: number };
type Proposal = Pick<AssistantPreview, "id" | "operation" | "contextRevision" | "diffHash" | "before" | "after" | "expiresAt" | "effects" | "undoOfId"> & { target: { label: string } };
type ReceiptState = { state: "PENDING" | "SUCCEEDED" | "FAILED"; result: AssistantReceipt | null; error: { detail: string } | null };
function serverContext(context: AssistantContext) {
  const { snapshotKey: _localOnly, ...selected } = context;
  return selected;
}

/** Proposals are view-owned; only an explicitly submitted request survives the view. */
export function createServerPreviewProvider(resource: string): AssistantPreviewProvider {
  const journal = recoveryJournal(resource);
  let credentials: Credentials | null = null;
  let acceptedContext: string | null = null;
  let tail: Promise<unknown> = Promise.resolve();
  const serial = <T,>(work: () => Promise<T>): Promise<T> => {
    const result = tail.then(work, work);
    tail = result.catch(() => undefined);
    return result;
  };
  async function request<T>(path: string, body?: unknown, init: RequestInit = {}, token = credentials?.token): Promise<T> {
    const response = await authenticatedFetch(`/api/v1/assistant${path}`, {
      ...init, signal: init.signal ?? AbortSignal.timeout(15000), method: init.method ?? (body === undefined ? "GET" : "POST"),
      credentials: "include", headers: { "Content-Type": "application/json", ...(token ? { "X-Assistant-Session": token } : {}), ...init.headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      const problem = await response.json().catch(() => ({})) as { detail?: string; code?: string };
      if (response.status >= 500) throw new Error(problem.detail ?? "操作アシスタントとの通信に失敗しました。");
      throw new ApiError(problem.detail ?? "操作アシスタントとの通信に失敗しました。", problem.code);
    }
    return await response.json() as T;
  }
  function requireSettled() {
    if (journal.read()?.kind === "pending") throw new ApiError("先の操作の保存結果を確認してください。", "ASSISTANT_REQUEST_PENDING");
  }
  async function synchronize(context: AssistantContext) {
    requireSettled();
    const incoming = serverContext(context), key = JSON.stringify(incoming);
    if (!credentials) {
      credentials = await request<Credentials>("/sessions", { context: incoming });
      acceptedContext = key;
    } else if (acceptedContext !== key) {
      try {
        const next = await request<{ contextRevision: number }>("/sessions/context", { expectedContextRevision: credentials.contextRevision, context: incoming }, { method: "PUT" });
        credentials.contextRevision = next.contextRevision;
        acceptedContext = key;
      } catch (error) {
        // A failed exchange never silently rebases an existing proposal.
        credentials = null; acceptedContext = null;
        throw error;
      }
    }
  }
  function preview(proposal: Proposal, snapshot: AssistantSnapshot): AssistantPreview {
    return { ...proposal, origin: "server-proposal", context: structuredClone(snapshot.context),
      target: { label: proposal.target.label, currentValue: proposal.before,
        ...(!proposal.undoOfId && snapshot.target?.image ? { image: snapshot.target.image } : {}) } };
  }
  async function send(entry: PendingRequest) {
    try {
      entry.receipt = await request<AssistantReceipt>(`/proposals/${encodeURIComponent(entry.preview.id)}/apply`, {
        contextRevision: entry.preview.contextRevision, diffHash: entry.preview.diffHash, challenge: entry.challenge,
        ...(entry.approvalTurnId ? { approvalTurnId: entry.approvalTurnId } : {}),
      }, { headers: { "Idempotency-Key": entry.key } }, entry.token);
    } catch (error) {
      if (error instanceof ApiError) { entry.failure = error.message; journal.write(entry); }
      throw error;
    }
    // If this write fails, the original PENDING packet still permits receipt lookup.
    try { journal.write(entry); } catch { throw new Error("保存結果の復旧情報を更新できません。保存結果を再確認してください。"); }
    return entry.receipt;
  }
  function presentation(proposal: AssistantPreview, challenge: string) {
    return { contextRevision: proposal.contextRevision, diffHash: proposal.diffHash, challenge };
  }
  function turnResult(result: { turnId: string; proposal?: Proposal; approved?: boolean; message?: string }, snapshot: AssistantSnapshot): VoiceTurnResult {
    return { ...result, ...(result.proposal ? { proposal: preview(result.proposal, snapshot) } : { proposal: undefined }) };
  }
  return {
    voiceStatus: () => request<VoiceStatus>("/voice/status"),
    voiceText: (text, snapshot, signal) => serial(async () => {
      await synchronize(snapshot.context); signal.throwIfAborted();
      return turnResult(await request("/turns/text", { contextRevision: credentials!.contextRevision, text }, { signal: AbortSignal.any([signal, AbortSignal.timeout(90000)]), headers: { "Idempotency-Key": crypto.randomUUID() } }), snapshot);
    }),
    voiceStart: (snapshot, signal, approval) => serial(async () => {
      await synchronize(snapshot.context); signal.throwIfAborted();
      const result = await request<{ turnId: string }>("/turns", { contextRevision: credentials!.contextRevision, purpose: approval ? "approval" : "command", ...(approval ? { proposalId: approval.preview.id, presentation: presentation(approval.preview, approval.challenge), speechId: approval.speechId } : {}) }, { signal, headers: { "Idempotency-Key": crypto.randomUUID() } });
      return result.turnId;
    }),
    voiceAudio: async (turnId, audio, snapshot, signal) => {
      const response = await authenticatedFetch(`/api/v1/assistant/turns/${encodeURIComponent(turnId)}/audio`, { method: "POST", signal: AbortSignal.any([signal, AbortSignal.timeout(100000)]), credentials: "include", headers: { "Content-Type": audio.type, "X-Assistant-Session": credentials!.token }, body: audio });
      if (!response.ok) {
        const problem = await response.json().catch(() => ({})) as { detail?: string; code?: string };
        throw new ApiError(problem.detail ?? "音声の処理に失敗しました。自動再送はしません。", problem.code);
      }
      return turnResult(await response.json(), snapshot);
    },
    voiceCancel: async turnId => { if (credentials) await request(`/turns/${encodeURIComponent(turnId)}/cancel`, {}); },
    voiceSpeech: (proposal, challenge, signal) => serial(() => request("/speech", { proposalId: proposal.id, presentation: presentation(proposal, challenge) }, { signal: AbortSignal.any([signal, AbortSignal.timeout(60000)]), headers: { "Idempotency-Key": crypto.randomUUID() } })),
    voiceEnded: async (speechId, completed) => { await request(`/speech/${encodeURIComponent(speechId)}/ended`, { completed }); },
    voiceReceiptSpeech: (receipt, signal) => serial(() => request("/speech/receipt", { requestKey: receipt.requestKey }, { signal: AbortSignal.any([signal, AbortSignal.timeout(60000)]), headers: { "Idempotency-Key": crypto.randomUUID() } })),
    sync: context => serial(() => synchronize(structuredClone(context))),
    preview: (input, snapshot, signal) => serial(async () => {
      await synchronize(input.context); signal.throwIfAborted();
      return preview(await request<Proposal>("/proposals", { contextRevision: credentials!.contextRevision, operation: input.operation, value: input.value }, { signal }), snapshot);
    }),
    undoPreview: (id, snapshot, signal) => serial(async () => {
      await synchronize(snapshot.context); signal.throwIfAborted();
      return preview(await request<Proposal>(`/operations/${encodeURIComponent(id)}/undo-proposal`, { contextRevision: credentials!.contextRevision }, { signal }), snapshot);
    }),
    history: context => serial(async () => {
      await synchronize(context);
      return (await request<{ items: AssistantHistoryItem[] }>("/history")).items;
    }),
    present: (proposal, signal) => serial(async () => {
      requireSettled();
      return request<{ challenge: string }>(`/proposals/${proposal.id}/presented`, { contextRevision: proposal.contextRevision, diffHash: proposal.diffHash }, { signal });
    }),
    apply: (proposal, challenge, key, approvalTurnId) => serial(() => journal.lock(async () => {
      const stored = journal.read();
      if (stored?.kind === "pending") {
        if (stored.key !== key) throw new ApiError("別の操作の保存結果を確認してください。", "ASSISTANT_REQUEST_PENDING");
        if (stored.receipt) return stored.receipt;
        if (stored.failure) throw new ApiError(stored.failure);
        return send(stored);
      }
      if (stored?.receipt.requestKey === key) return stored.receipt;
      if (!credentials) throw new ApiError("変更案を作り直してください。", "ASSISTANT_SESSION_INVALID");
      const entry: PendingRequest = { kind: "pending", token: credentials.token, preview: proposal, challenge, key, approvalTurnId };
      journal.write(entry); // Must complete before the first HTTP mutation.
      return send(entry);
    })),
    recovery: journal.summary,
    subscribe: journal.subscribe,
    recover: () => serial(() => journal.lock(async () => {
      const stored = journal.read();
      if (!stored || stored.kind === "completed" || stored.receipt || stored.failure) return journal.summary();
      let state: ReceiptState | null = null;
      try { state = await request<ReceiptState>(`/requests/${encodeURIComponent(stored.key)}`, undefined, {}, stored.token); }
      catch (error) { if (!(error instanceof ApiError) || error.code !== "ASSISTANT_REQUEST_NOT_FOUND") throw error; }
      if (state?.state === "SUCCEEDED") {
        if (!state.result) throw new Error("保存結果の内容を確認できません。");
        stored.receipt = state.result;
      } else if (state?.state === "FAILED") stored.failure = state.error?.detail ?? "この操作は保存されませんでした。";
      else {
        // Not found/PENDING is not proof of failure: replay the exact approved
        // request. The receipt/transaction lock arbitrates a still-running sender.
        try { await send(stored); } catch (error) { if (!stored.failure) throw error; }
      }
      journal.write(stored);
      return journal.summary();
    })),
    acknowledge: key => serial(() => journal.lock(async () => {
      const stored = journal.read();
      if (stored?.kind !== "pending" || stored.key !== key) return;
      if (stored.receipt) journal.write({ kind: "completed", receipt: stored.receipt });
      else if (stored.failure) journal.write(null);
      else throw new Error("保存結果を確認するまで復旧情報を削除できません。");
    })),
    cancel: proposal => serial(async () => { await request(`/proposals/${proposal.id}/cancel`, {}); }),
    reset: () => { void serial(async () => { credentials = null; acceptedContext = null; }); },
  };
}
