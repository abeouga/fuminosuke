import { useState, useSyncExternalStore } from "react";
import { analysisRunning, type TemplateAnalysisStore } from "./templateAnalysisStore";
import "./TemplateAnalysisStatus.css";

/** The AI draft runs in the background: an overlay while it works, and a notice only when the user must act. */
export function TemplateAnalysisStatus({ store, blocked, saving, onSave }: { store: TemplateAnalysisStore; blocked: boolean; saving: boolean; onSave(): void }) {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const running = analysisRunning(state.run);
  const [dismissed, setDismissed] = useState("");
  // Busy without applied units covers admission and the automatic merge right after the run.
  if (running || (state.busy && !state.recovery && !state.storageBlocked && !state.applied.length)) {
    return <div className="template-analysis-overlay" role="status" aria-live="polite">
      <div className="template-analysis-overlay-box">
        <span className="template-analysis-spinner" aria-hidden="true" />
        <strong>AIが帳票を解析しています…</strong>
        {running && <button className="secondary compact" disabled={state.busy} onClick={() => void store.cancel()}>解析を中止</button>}
      </div>
    </div>;
  }
  const needsAction = state.held || state.recovery || state.storageBlocked;
  if (!needsAction) return state.message && state.message !== dismissed
    ? <div className="template-analysis-notice is-info" role="status"><p>{state.message}</p><button className="secondary compact" onClick={() => setDismissed(state.message)}>閉じる</button></div>
    : null;
  const locked = blocked || state.busy || saving;
  return <div className="template-analysis-notice" role="status">
    <p>{state.message || "AIの下書きを確認してください。"}</p>
    <div className="template-analysis-notice-actions">
      {state.applied.length > 0 && <button className="primary compact" disabled={locked || state.storageBlocked} onClick={onSave}>保存する</button>}
      {state.applied.length > 0 && <button className="secondary compact" disabled={locked} onClick={() => store.undoDraft()}>AIの下書きを戻す</button>}
      {(state.recovery || state.storageBlocked) && <button className="secondary compact" disabled={state.busy} onClick={() => void store.recover()}>結果を再取得</button>}
      {!state.applied.length && !state.recovery && !state.storageBlocked && <button className="secondary compact" disabled={locked} onClick={() => store.finish()}>手動編集に戻る</button>}
    </div>
  </div>;
}
