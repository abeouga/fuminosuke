import { useEffect, useLayoutEffect, useId, useRef, useState } from "react";
import { FiEdit3, FiX } from "react-icons/fi";
import { CATEGORY_LABEL, OPERATION_LABEL, contextKey, resourceKey, type AssistantEditAdapter, type AssistantOperation, type AssistantPreview, type AssistantPreviewProvider, type AssistantReceipt, type AssistantSnapshot, type AssistantRecovery, type AssistantHistoryItem } from "./contracts";
import { ApiError } from "../api";
import { createServerPreviewProvider } from "./serverPreviewProvider";
import { VoiceControls } from "./VoiceControls";

type PanelProps = {
  snapshot: AssistantSnapshot;
  adapter: AssistantEditAdapter;
  provider?: AssistantPreviewProvider;
};
export function AssistantPanel(props: PanelProps) {
  return <AssistantPanelSession key={resourceKey(props.snapshot.context)} {...props} />;
}
function AssistantPanelSession({ snapshot, adapter, provider: suppliedProvider }: PanelProps) {
  const [serverProvider] = useState(() => createServerPreviewProvider(resourceKey(snapshot.context)));
  const provider = suppliedProvider ?? serverProvider;
  const [open, setOpen] = useState(false);
  const [operation, setOperation] = useState<AssistantOperation | "">("");
  const [value, setValue] = useState("");
  const [blank, setBlank] = useState(false);
  const [preview, setPreview] = useState<AssistantPreview | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  const [lastSaved, setLastSaved] = useState<AssistantReceipt | null>(null);
  const [recovery, setRecovery] = useState<AssistantRecovery | null>(null);
  const [history, setHistory] = useState<AssistantHistoryItem[]>([]);
  const [historyEpoch, setHistoryEpoch] = useState(0);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const stopVoice = useRef<() => void>(() => {});
  const application = useRef<{ preview: AssistantPreview; challenge: string; key: string; receipt?: AssistantReceipt; approvalTurnId?: string } | null>(null);
  const previewRef = useRef(preview);
  previewRef.current = preview;
  const controller = useRef<AbortController | null>(null);
  const adapterRef = useRef(adapter);
  adapterRef.current = adapter;
  const latest = useRef(snapshot);
  latest.current = snapshot;
  const panelId = useId();
  const inputId = useId();
  const key = contextKey(snapshot.context);
  const previousKey = useRef(key);
  const requestEpoch = useRef(0);
  const taskLock = useRef(false);
  const openRef = useRef(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const wasOpenRef = useRef(false);
  const mounted = useRef(true);
  function recoveryState() {
    try { return provider.recovery?.(); } catch { return recovery; }
  }
  const currentPreview = preview && contextKey(preview.context) === key && !snapshot.dirty
    && !snapshot.busy && !(preview.undoOfId ? snapshot.resourceBlocker : snapshot.blocker) && !snapshot.saveError && !recovery ? preview : null;

  useLayoutEffect(() => {
    const inspect = () => {
      try {
        const stored = provider.recovery?.();
        const needed = stored && (!stored.completed || (stored.receipt?.revision ?? 0) > (latest.current.context.resourceRevision ?? 0)) ? stored : null;
        setRecovery(needed);
        if (needed) {
          setLastSaved(null);
          if (!openRef.current) { adapterRef.current.begin(); openRef.current = true; setOpen(true); }
          adapterRef.current.setApplying?.(true);
          if (!taskLock.current) { setOutcomeUnknown(true); setStatus(`「${needed.label}」の保存結果を確認してください。`); }
        } else if (!taskLock.current && !application.current) {
          setOutcomeUnknown(false); adapterRef.current.setApplying?.(false);
        }
      } catch (error) { setStatus(error instanceof Error ? error.message : "復旧情報を取得できません。"); }
    };
    inspect();
    return provider.subscribe?.(inspect);
  }, [provider, snapshot.context.resourceRevision]);

  function invalidate() {
    stopVoice.current();
    if (previewRef.current && !application.current && !recovery) void provider.cancel?.(previewRef.current).catch(() => undefined);
    requestEpoch.current += 1;
    controller.current?.abort();
    controller.current = null;
    setPending(false);
    setPreview(null);
    setChallenge(null);
  }
  useEffect(() => {
    if (open && !recovery && !application.current) void provider.sync?.(structuredClone(snapshot.context)).catch(reason => {
      if (openRef.current) setStatus(reason instanceof Error ? reason.message : "選択対象を同期できませんでした。");
    });
  }, [open, key, provider, recovery]);
  useEffect(() => {
    if (!open || recovery || application.current || !provider.history) return;
    let active = true;
    void provider.history(structuredClone(snapshot.context)).then(items => { if (active) setHistory(items); })
      .catch(reason => { if (active) setStatus(reason instanceof Error ? reason.message : "操作履歴を取得できません。"); });
    return () => { active = false; };
  }, [open, key, provider, Boolean(recovery), historyEpoch]);
  useEffect(() => {
    if (!currentPreview || !provider.present) return;
    const abort = new AbortController();
    setChallenge(null);
    setPending(true);
    void provider.present(currentPreview, abort.signal).then(value => {
      if (!abort.signal.aborted) setChallenge(value.challenge);
    }).catch(reason => {
      if (!abort.signal.aborted) setStatus(reason instanceof Error ? reason.message : "承認情報を取得できませんでした。");
    }).finally(() => { if (!abort.signal.aborted) setPending(false); });
    return () => abort.abort();
  }, [currentPreview?.id, key, provider]);
  useEffect(() => {
    if (!preview?.expiresAt) return;
    const timer = window.setTimeout(() => { invalidate(); setStatus("変更案の期限が切れました。作り直してください。"); }, Math.max(0, Date.parse(preview.expiresAt) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [preview?.id]);
  useEffect(() => {
    if (previousKey.current !== key) {
      previousKey.current = key;
      invalidate();
      setValue("");
      setBlank(false);
      setStatus(openRef.current ? "選択または編集状態が変わりました。変更案を作り直してください。" : null);
    }
    setOperation(current => snapshot.operations.includes(current as AssistantOperation) ? current : snapshot.operations[0] ?? "");
  }, [key, snapshot.operations.join("|")]);
  useEffect(() => {
    if (snapshot.busy || snapshot.dirty || (preview?.undoOfId ? snapshot.resourceBlocker : snapshot.blocker) || snapshot.saveError) invalidate();
  }, [snapshot.busy, snapshot.dirty, snapshot.blocker, snapshot.resourceBlocker, snapshot.saveError]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestEpoch.current += 1;
      controller.current?.abort();
      if (openRef.current) adapterRef.current.end();
    };
  }, []);
  useEffect(() => {
    if (open) closeRef.current?.focus({ preventScroll: true });
    else if (wasOpenRef.current) toggleRef.current?.focus({ preventScroll: true });
    wasOpenRef.current = open;
  }, [open]);

  function close() {
    if (application.current || recovery) { setStatus("保存結果を確認してからパネルを閉じてください。"); return; }
    invalidate();
    openRef.current = false;
    setOpen(false);
    adapter.end();
    provider.reset?.();
  }
  function start() {
    adapter.begin();
    openRef.current = true;
    setOpen(true);
    setStatus(null);
    setLastSaved(null);
  }
  async function resolveDirty(action: "save" | "discard") {
    if (taskLock.current) return;
    taskLock.current = true;
    setPending(true);
    setStatus(null);
    try {
      const resolved = action === "save" ? await adapter.save() : adapter.discard();
      if (openRef.current) setStatus(resolved ? "編集状態を更新しました。選択対象を確認してください。" : "変更を解決できませんでした。既存の編集欄を確認してください。");
    } catch (reason) {
      if (openRef.current) setStatus(reason instanceof Error ? reason.message : "編集状態を更新できませんでした。");
    } finally {
      taskLock.current = false;
      if (openRef.current) setPending(false);
    }
  }
  async function createPreview() {
    if (taskLock.current || !operation) return;
    const selected = latest.current;
    if (selected.busy || selected.dirty || selected.blocker || selected.saveError) return;
    invalidate();
    const epoch = requestEpoch.current;
    const capturedKey = contextKey(selected.context);
    const abort = new AbortController();
    controller.current = abort;
    setPending(true);
    setStatus(null);
    setLastSaved(null);
    try {
      const next = await provider.preview({ context: structuredClone(selected.context), operation, value: blank ? null : value }, structuredClone(selected), abort.signal);
      if (!abort.signal.aborted && epoch === requestEpoch.current && openRef.current
        && capturedKey === contextKey(latest.current.context) && !latest.current.dirty && !latest.current.busy && !latest.current.blocker && !latest.current.saveError) setPreview(next);
    } catch (reason) {
      if (!abort.signal.aborted && epoch === requestEpoch.current) setStatus(reason instanceof Error ? reason.message : "変更案を作成できませんでした。");
    } finally {
      if (epoch === requestEpoch.current) setPending(false);
    }
  }
  async function createUndo(item: AssistantHistoryItem) {
    if (taskLock.current || recovery || !provider.undoPreview || snapshot.dirty || snapshot.busy || snapshot.resourceBlocker || snapshot.saveError) return;
    invalidate();
    const epoch = requestEpoch.current, capturedKey = key;
    const abort = new AbortController(); controller.current = abort;
    setPending(true); setStatus(null); setLastSaved(null);
    try {
      const next = await provider.undoPreview(item.operationId, structuredClone(snapshot), abort.signal);
      if (!abort.signal.aborted && epoch === requestEpoch.current && capturedKey === contextKey(latest.current.context)) setPreview(next);
    } catch (reason) { if (!abort.signal.aborted) setStatus(reason instanceof Error ? reason.message : "取り消し案を取得できません。"); }
    finally { if (epoch === requestEpoch.current) setPending(false); }
  }
  async function applyPreview(discardDraft = false, approvalTurnId?: string) {
    if (taskLock.current || !provider.apply || !adapter.saved) return;
    if (!application.current && !recovery) {
      if (!currentPreview || !challenge) return;
      application.current = { preview: currentPreview, challenge, key: crypto.randomUUID(), approvalTurnId };
    }
    const active = application.current;
    taskLock.current = true;
    setApplying(true);
    setOutcomeUnknown(false);
    adapter.setApplying?.(true);
    let settled = false;
    let settledKey: string | null = null;
    try {
      let receipt: AssistantReceipt | undefined;
      let requestKey: string;
      if (recovery && provider.recover) {
        const recovered = await provider.recover();
        if (!recovered) { application.current = null; setRecovery(null); setOutcomeUnknown(false); settled = true; return; }
        setRecovery(recovered); requestKey = recovered.key;
        if (recovered.failure) {
          await provider.acknowledge?.(requestKey);
          settled = true; settledKey = requestKey;
          application.current = null; setRecovery(null); invalidate(); setStatus(recovered.failure); return;
        }
        receipt = recovered.receipt;
      } else if (active) {
        active.receipt ??= await provider.apply(active.preview, active.challenge, active.key, active.approvalTurnId);
        receipt = active.receipt; requestKey = active.key;
      } else return;
      if (!receipt) throw new Error("保存結果を取得できません。");
      if (!mounted.current) return; // The journal owns recovery after navigation.
      if (latest.current.recoveryBusy) throw new Error("既存の保存処理が完了してから保存結果を確認してください。");
      if ((latest.current.recoveryDirty ?? latest.current.dirty) && !discardDraft) {
        setOutcomeUnknown(true); setStatus("保存は完了しています。未保存の入力を保持しています。表示を更新する場合は、入力の破棄を選択してください。"); return;
      }
      await adapterRef.current.saved!(receipt, discardDraft);
      await provider.acknowledge?.(requestKey);
      settled = true;
      settledKey = requestKey;
      application.current = null;
      setRecovery(null);
      invalidate();
      setLastSaved(receipt);
      setHistoryEpoch(current => current + 1);
      setStatus(`「${receipt.target.label}」${receipt.undoOfId ? "の変更を取り消しました。" : "を保存しました。"}`);
    } catch (reason) {
      const stored = recoveryState();
      const relevant = stored?.key === (active?.key ?? recovery?.key) ? stored : null;
      if (active?.receipt || relevant?.receipt) {
        setOutcomeUnknown(true);
        setStatus("保存は完了しています。表示の再取得に失敗しました。保存結果を確認してください。");
      } else if (reason instanceof ApiError && (!relevant || relevant.failure)) {
        if (relevant?.failure) await provider.acknowledge?.(relevant.key);
        application.current = null;
        setRecovery(null);
        invalidate();
        setStatus(reason.message);
      } else {
        setOutcomeUnknown(true);
        setStatus("保存結果が不明です。「保存結果を確認」で再確認してください。");
      }
    } finally {
      taskLock.current = false;
      setApplying(false);
      const stored = recoveryState();
      const journalUnresolved = stored && stored.key !== settledKey && (!stored.completed || (stored.receipt?.revision ?? 0) > (latest.current.context.resourceRevision ?? 0));
      const unresolved = Boolean((!settled && application.current) || journalUnresolved);
      if (journalUnresolved) { setRecovery(stored); setOutcomeUnknown(true); setLastSaved(null); }
      if (mounted.current) adapterRef.current.setApplying?.(unresolved);
      if (!unresolved) setOutcomeUnknown(false);
    }
  }
  const blocked = snapshot.blocker ?? snapshot.saveError;
  return <div className={`assistant-slot${open ? " is-open" : ""}`}>
    <button ref={toggleRef} type="button" className="secondary compact assistant-toggle" aria-expanded={open} aria-controls={panelId} onClick={open ? close : start}>
      <FiEdit3 aria-hidden="true" />操作アシスタント
    </button>
    {open && <aside id={panelId} className="assistant-panel" aria-label="操作アシスタント" onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); close(); }
    }}>
      <header><div><h2>変更案の確認</h2><span>案を確認してから保存します。</span></div><button ref={closeRef} type="button" className="ghost compact" aria-label="操作アシスタントを閉じる" disabled={applying || outcomeUnknown} onClick={close}><FiX aria-hidden="true" /></button></header>
      <div className="assistant-target"><span>選択中の対象</span><strong>{snapshot.target?.label ?? "帳票上の対象を1つ選択してください"}</strong></div>
      {snapshot.target?.image && <figure className="assistant-image">{snapshot.target.image.kind === "crop"
        ? <img src={snapshot.target.image.url} alt={`${snapshot.target.label}の原本`} />
        : <svg viewBox={`${snapshot.target.image.x} ${snapshot.target.image.y} ${snapshot.target.image.width} ${snapshot.target.image.height}`} role="img" aria-label={`${snapshot.target.label}の原本`}><image href={snapshot.target.image.url} width={snapshot.target.image.imageWidth} height={snapshot.target.image.imageHeight} /></svg>}</figure>}
      {snapshot.busy ? <p role="status">{outcomeUnknown ? "保存結果を確認するまで、既存の編集を停止しています。" : "既存の保存・処理の完了を待っています。"}</p>
        : blocked ? <p className="assistant-warning" role="status">{blocked}</p>
        : snapshot.dirty ? <section className="assistant-dirty" aria-label="未保存変更の扱い"><p>未保存の変更があります。続ける前に保存または破棄してください。</p><div className="assistant-actions"><button type="button" className="secondary compact" disabled={pending} onClick={() => void resolveDirty("save")}>保存して続行</button><button type="button" className="secondary compact" disabled={pending} onClick={() => void resolveDirty("discard")}>破棄して続行</button><button type="button" className="ghost compact" onClick={close}>中止</button></div></section>
        : snapshot.operations.length > 0 && <form onSubmit={event => { event.preventDefault(); void createPreview(); }}>
          <label>操作<select aria-label="アシスタントの操作" value={operation} onChange={event => { invalidate(); setStatus(null); setValue(""); setBlank(false); setOperation(event.target.value as AssistantOperation); }}>{snapshot.operations.map(item => <option key={item} value={item}>{OPERATION_LABEL[item]}</option>)}</select></label>
          {operation === "template.change_category" ? <label>変更後の種類<select aria-label="変更後の種類" value={value} onChange={event => { invalidate(); setValue(event.target.value); }}><option value="">種類を選択</option>{Object.entries(CATEGORY_LABEL).map(([category, label]) => <option key={category} value={category}>{label}</option>)}</select></label>
            : <label htmlFor={inputId}>変更後の文字列<input id={inputId} value={value} disabled={blank} maxLength={operation === "ocr.set_value" ? 500 : operation === "template.rename_choice" ? 100 : 300} onChange={event => { invalidate(); setValue(event.target.value); }} /></label>}
          {operation === "ocr.set_value" && <label className="assistant-blank"><input type="checkbox" checked={blank} onChange={event => { invalidate(); setBlank(event.target.checked); }} />空欄にする</label>}
          <div className="assistant-actions"><button type="submit" className="secondary compact" disabled={pending || applying || voiceBusy || outcomeUnknown || !operation}>変更案を表示</button></div>
        </form>}
      {currentPreview && <section className="assistant-proposal" aria-label="変更案"><h3>{currentPreview.undoOfId ? "変更の取り消し" : OPERATION_LABEL[currentPreview.operation]}</h3><p>対象：{currentPreview.target.label}</p><dl><div><dt>変更前</dt><dd>{formatValue(currentPreview.before)}</dd></div><div><dt>変更後</dt><dd>{formatValue(currentPreview.after)}</dd></div>{currentPreview.beforeStoredValue !== undefined && <div><dt>保存される選択値</dt><dd>{formatValue(currentPreview.beforeStoredValue)} → {formatValue(currentPreview.afterStoredValue ?? null)}</dd></div>}{currentPreview.effects?.map((effect, index) => <div key={`${effect.label}-${index}`}><dt>{effect.label}</dt><dd>{formatValue(effect.before)} → {formatValue(effect.after)}</dd></div>)}</dl><p>この案はまだ保存されていません。</p><div className="assistant-actions"><button type="button" className="primary compact" disabled={!challenge || pending || applying || voiceBusy || !adapter.saved} onClick={() => void applyPreview()}>{currentPreview.undoOfId ? "取り消しを適用" : "変更を適用"}</button><button type="button" className="ghost compact" disabled={applying} onClick={() => { invalidate(); setStatus("変更案を取り消しました。"); }}>案を取り消す</button></div></section>}
      {provider.voiceStatus && !recovery && <VoiceControls provider={provider} snapshot={snapshot} preview={currentPreview} challenge={challenge} receipt={lastSaved}
        disabled={pending || applying || outcomeUnknown || snapshot.busy || snapshot.dirty || Boolean(currentPreview?.undoOfId ? snapshot.resourceBlocker : snapshot.blocker) || Boolean(snapshot.saveError)}
        registerStop={stop => { stopVoice.current = stop; }} onBusy={setVoiceBusy}
        onProposal={next => { invalidate(); setStatus(null); setLastSaved(null); setPreview(next); }}
        onApprove={turnId => { void applyPreview(false, turnId); }} />}
      {outcomeUnknown && <button type="button" className="secondary compact" disabled={applying || snapshot.recoveryBusy} onClick={() => void applyPreview()}>保存結果を確認</button>}
      {recovery?.receipt && (snapshot.recoveryDirty ?? snapshot.dirty) && <section className="assistant-dirty" aria-label="復旧時の未保存入力"><p>保存済みの結果とは別に、未保存の入力を保持しています。</p><button type="button" className="secondary compact" disabled={applying || snapshot.recoveryBusy} onClick={() => void applyPreview(true)}>未保存入力を破棄して保存結果を表示</button></section>}
      {history.length > 0 && !recovery && <section className="assistant-history" aria-label="保存した操作"><h3>直前の操作</h3><p>「{history[0].target.label}」{history[0].undoOfId ? "の変更を取り消しました" : `：${formatValue(history[0].after)}`}</p>{history[0].canUndo ? <button type="button" className="secondary compact" disabled={pending || applying || snapshot.busy || snapshot.dirty || Boolean(snapshot.resourceBlocker) || Boolean(snapshot.saveError)} onClick={() => void createUndo(history[0])}>取り消し案を表示</button> : <p>{history[0].reason}</p>}</section>}
      {applying && <p role="status">保存結果を確認しています。</p>}
      {(lastSaved || status) && <p className="assistant-status" role="status">{lastSaved ? `「${lastSaved.target.label}」${lastSaved.undoOfId ? "の変更を取り消しました。" : "を保存しました。"}` : status}</p>}
    </aside>}
  </div>;
}
function formatValue(value: string | null) { return value === null ? "空欄" : value === "" ? "未設定" : value; }
