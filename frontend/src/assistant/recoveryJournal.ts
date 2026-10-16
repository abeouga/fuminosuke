import { privateLocalStorage } from "../auth/transport";
import { ApiError } from "../api";
import type { AssistantPreview, AssistantReceipt, AssistantRecovery } from "./contracts";

export type PendingRequest = {
  kind: "pending"; key: string; token: string; preview: AssistantPreview; challenge: string;
  receipt?: AssistantReceipt; failure?: string; approvalTurnId?: string;
};
type Entry = PendingRequest | { kind: "completed"; receipt: AssistantReceipt };
const EVENT = "assistant-recovery-changed";

/** A write-ahead journal for one resource. Web Locks serializes participating tabs. */
export function recoveryJournal(resource: string) {
  const storageKey = `fuminosuke.assistant.request.v1:${resource}`;
  function read(): Entry | null {
    try {
      const text = privateLocalStorage.getItem(storageKey);
      if (!text) return null;
      const data = JSON.parse(text) as Entry;
      if ((data.kind === "completed" && data.receipt?.saved === true)
        || (data.kind === "pending" && typeof data.key === "string" && typeof data.token === "string" && data.preview?.id && typeof data.challenge === "string")) return data;
      throw new Error();
    } catch { throw new ApiError("操作の復旧情報を読み取れません。ブラウザーの保存機能を確認してください。", "ASSISTANT_JOURNAL_UNAVAILABLE"); }
  }
  function write(data: Entry | null) {
    try {
      if (data) privateLocalStorage.setItem(storageKey, JSON.stringify(data));
      else privateLocalStorage.removeItem(storageKey);
    } catch { throw new ApiError("復旧情報を保存できません。ブラウザーの保存機能を確認してください。", "ASSISTANT_JOURNAL_UNAVAILABLE"); }
    window.dispatchEvent(new CustomEvent(EVENT, { detail: storageKey }));
  }
  function summary(): AssistantRecovery | null {
    const data = read();
    if (!data) return null;
    if (data.kind === "completed") return { key: data.receipt.requestKey, label: data.receipt.target.label, receipt: data.receipt, completed: true };
    return { key: data.key, label: data.preview.target.label, receipt: data.receipt, failure: data.failure };
  }
  return {
    read, write, summary,
    lock: async <T,>(work: () => Promise<T>): Promise<T> => {
      if (!navigator.locks) throw new ApiError("このブラウザーでは操作の競合を保護できません。対応ブラウザーを使用してください。", "ASSISTANT_JOURNAL_UNAVAILABLE");
      return navigator.locks.request(storageKey, work);
    },
    subscribe: (listener: () => void) => {
      const changed = (event: Event) => {
        if ((event instanceof StorageEvent && event.key === storageKey)
          || (event instanceof CustomEvent && event.detail === storageKey)) listener();
      };
      window.addEventListener("storage", changed);
      window.addEventListener(EVENT, changed);
      return () => { window.removeEventListener("storage", changed); window.removeEventListener(EVENT, changed); };
    },
  };
}
