import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { FiArrowLeft, FiArrowRight, FiCheck, FiEdit2, FiEdit3, FiFileText, FiSave } from "react-icons/fi";
import { ApiError, api } from "../api";
import { AssistantPanel } from "../assistant/AssistantPanel";
import { EditingControls } from "../editing/EditingControls";
import { ReviewAiFocus } from "../editing/AiFocusLayer";
import { v2client as editingClient, EditingError } from "../editing/v2client";
import { resourceEditingStore } from "../editing/workbenchStore";
import { useInteractionContext } from "../assistant/useInteractionContext";
import type { AssistantSnapshot } from "../assistant/contracts";
import { PagedReviewPage } from "./PagedReviewPage";
import { ExcelExportActions } from "../components/ExcelExportActions";
import { AnchoredReviewPopover, ReviewChecklists, ReviewJoins, ReviewRenaming, checklistOptionsByPath, ReviewSeriesMarks, buildReviewDisplayModel, CellStatusPopover, MarkStatusPopover, markPopoverIsWide, reviewEntryLabel, GenericRecordReviewTable, IndependentReviewGroups, KeyValueReviewTable, ReviewResultsToolbar, ReviewDraftBoundary, ReviewTableSection, VoiceTableTarget, reviewEntryContext, sectionItemPaths, type ReadingEntry, type ReviewDisplayRecordSection, type ReviewInteractionMode } from "../components/OcrReviewResults";
import type { Artifact, DocumentIdentity, FieldReading, OcrDocument, OcrResult } from "../types";
import { displayName } from "../userMessages";
import { displayOrders, joinByPath, type ItemJoin } from "../itemJoins";
import { onItemJoinsSaved, openItemJoinEditor } from "../components/TemplateItemJoinEditor";
import { showToast } from "../components/Toast";
export { buildReviewDisplayModel, positionReviewPopover } from "../components/OcrReviewResults";
export function ReviewPage({ jobId, onClose, onConfirmed, onNavigationGuardChange, requireIdentityConfirmation = false, initialSelectedPath = null, autoReopenConfirmed = false, templateNames = {}, closeLabel = "ホームへ", }: {
    jobId: string;
    onClose: () => void;
    /** After 「内容を確定する」 succeeds (the app then shows the document shelf). */
    onConfirmed?: (confirmed: { templateId: string; documentName: string }) => void;
    /** Label of the back button (the page it returns to). */
    closeLabel?: string;
    onNavigationGuardChange?: (warn: boolean) => void;
    requireIdentityConfirmation?: boolean;
    initialSelectedPath?: string | null;
    autoReopenConfirmed?: boolean;
    templateNames?: Record<string, string>;
}) {
    const [result, setResult] = useState<OcrResult | null>(null), [draft, setDraft] = useState<OcrDocument | null>(null);
    const [artifacts, setArtifacts] = useState<Artifact[]>([]), [identities, setIdentities] = useState<DocumentIdentity[]>([]);
    const [identityChecked, setIdentityChecked] = useState(!requireIdentityConfirmation), [contextError, setContextError] = useState(false);
    const [name, setName] = useState(""), [selected, setSelected] = useState<string | null>(initialSelectedPath);
    const [mode, setMode] = useState<ReviewInteractionMode>("edit"), [inlineDirty, setInlineDirty] = useState(false);
    const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [message, setMessage] = useState<string | null>(null);
    // Update of a confirmed document: the user may continue despite a different identity.
    const [acceptMismatch, setAcceptMismatch] = useState(false), [addedOnly, setAddedOnly] = useState(false);
    const [confirmDialog, setConfirmDialog] = useState(false), [paged, setPaged] = useState(false);
    const [loadAttempt, setLoadAttempt] = useState(0);
    // An automatic save runs while the person keeps typing; only other work locks the inputs.
    const [saving, setSaving] = useState(false);
    // 「この読み取りはあっていますか？」: 「次へ」 waits for the check.
    const [identityAgreed, setIdentityAgreed] = useState(false);
    // The whole form over the screen (opened by pressing the preview).
    const [zoomed, setZoomed] = useState(false);
    // Pressing 「内容を確定する」 without a name: the count restarts the name field's glow.
    const [nameWanted, setNameWanted] = useState(0);
    const nameInput = useRef<HTMLInputElement>(null);
    useEffect(() => { if (nameWanted) nameInput.current?.focus(); }, [nameWanted]);
    useEffect(() => {
        if (!zoomed) return;
        const key = (event: KeyboardEvent) => { if (event.key === "Escape") setZoomed(false); };
        window.addEventListener("keydown", key);
        return () => window.removeEventListener("keydown", key);
    }, [zoomed]);
    // Names given to items on this screen (display only; per template).
    const [itemNames, setItemNames] = useState<Record<string, string>>({});
    const [conflict, setConflict] = useState<{
        draft: OcrDocument;
        latest: OcrResult | null;
    } | null>(null);
    const [assistantSaveError, setAssistantSaveError] = useState<string | null>(null);
    const [assistantApplying, setAssistantApplying] = useState(false);
    const [recoveryDiscardEpoch, setRecoveryDiscardEpoch] = useState(0);
    const editingStore = useMemo(() => resourceEditingStore("ocr", jobId), [jobId]);
    const editingStatus = useSyncExternalStore(editingStore.subscribe, editingStore.getSnapshot);
    const [editingEnabled, setEditingEnabled] = useState<boolean | null>(null);
    const voiceTableId = editingStatus.context?.scopeContext.selectedTableId ?? null;
    // Voice instructions can be narrowed to one table by clicking its name (no dropdown).
    const voiceTableTarget = useMemo(() => editingEnabled && editingStatus.context && editingStatus.state === "ready" && editingStatus.pending === 0
      ? { selected: voiceTableId, select: (tableId: string | null) => { void editingStore.changeScope(tableId).catch(e => setError(e instanceof Error ? e.message : "対象の表を切り替えられませんでした。")); } }
      : null, [editingEnabled, editingStatus.context, editingStatus.state, editingStatus.pending, voiceTableId, editingStore]);
    const reviewRoot = useRef<HTMLDivElement>(null);
    // Saved / failed notes are small corner notifications; they never push the page down.
    useEffect(() => {
        if (!message) return;
        showToast({ tone: "success", quiet: true, title: message });
        setMessage(null);
    }, [message]);
    const showsPage = Boolean(result && draft);
    useEffect(() => {
        if (!error || !showsPage) return;
        showToast({ tone: "error", quiet: true, title: error });
        setError(null);
    }, [error, showsPage]);
    useEffect(() => {
        if (!result || paged) return;
        let active = true;
        void editingClient.status().then(async status => {
            if (status.enabled && !result.confirmed) await editingStore.initialize();
            if (active) setEditingEnabled(status.enabled);
        }).catch(reason => {
            if (active && reason instanceof EditingError && reason.status === 404) setEditingEnabled(false);
            else if (active) setError(reason instanceof Error ? reason.message : "編集状態を取得できません。");
        });
        return () => { active = false; };
    }, [editingStore, Boolean(result), result?.confirmed, paged]);
    const draftRef = useRef(draft);
    draftRef.current = draft;
    const reopenRequest = useRef<Promise<OcrResult> | null>(null);
    const dirty = Boolean(result && draft && !result.confirmed && (JSON.stringify(draft) !== JSON.stringify(result.result) || name !== result.documentName));
    const locked = Boolean(result?.confirmed || busy || assistantApplying || conflict);
    useEffect(() => {
        if (!editingEnabled || !result || !draft || result.confirmed || locked || saving || inlineDirty || assistantSaveError || JSON.stringify(draft) === JSON.stringify(result.result)) return;
        // A short pause after the last key, so a save does not run for every letter.
        const timer = setTimeout(() => { void save(); }, 500);
        return () => clearTimeout(timer);
    }, [editingEnabled, result, draft, locked, saving, inlineDirty, assistantSaveError]);
    const accept = (next: OcrResult) => { setResult(next); setDraft(next.result); setName(next.documentName); setAssistantSaveError(null); };
    useEffect(() => {
        let active = true;
        setError(null);
        async function load() {
            let next = await api.result(jobId);
            if (autoReopenConfirmed && next.confirmed) {
                reopenRequest.current ??= api.reopenResult(jobId, next.revision);
                next = await reopenRequest.current;
            }
            const [images, context] = await Promise.all([api.artifacts(jobId).catch(() => ({ items: [] })), api.reviewContext(jobId).catch(() => null)]);
            if (!active)
                return;
            accept(next);
            setArtifacts(images.items);
            setIdentities(context?.identityFields ?? []);
            setContextError(!context);
        }
        void load().catch(reason => {
            if (!active)
                return;
            if (reason instanceof ApiError && reason.code === "MULTI_PAGE_REVIEW_REQUIRED")
                setPaged(true);
            else
                setError(reason instanceof Error ? reason.message : "結果を取得できませんでした。");
        });
        return () => { active = false; };
    }, [jobId, autoReopenConfirmed, loadAttempt]);
    useEffect(() => {
        const guarded = dirty || inlineDirty || busy || saving || assistantApplying || Boolean(conflict);
        onNavigationGuardChange?.(guarded);
        const guard = (event: BeforeUnloadEvent) => {
            if (guarded) {
                event.preventDefault();
                event.returnValue = "";
            }
        };
        window.addEventListener("beforeunload", guard);
        return () => { onNavigationGuardChange?.(false); window.removeEventListener("beforeunload", guard); };
    }, [dirty, inlineDirty, busy, saving, assistantApplying, conflict, onNavigationGuardChange]);
    const entries = useMemo<ReadingEntry[]>(() => draft ? Object.entries(draft.fields).map(([key, value]) => ({ path: `fields.${key}`, value,
        label: displayName(result?.reviewStructure?.fields.find(field => field.targetPath === `fields.${key}`)?.label ?? draft.fieldLabels[key] ?? key) })) : [], [draft, result?.reviewStructure]);
    const previews = result?.fieldPreviews ?? [];
    const series = result?.series ?? null;
    const seriesMarks = useMemo(() => {
        if (!series) return null;
        const carried = new Set(series.carriedPaths), identityPaths = new Set(series.predecessorIdentity.map(item => item.targetPath));
        // Only a blank_only update knows which fields were newly read; a full re-read marks nothing as added.
        const added = new Set(series.mode === "blank_only"
            ? entries.filter(entry => !carried.has(entry.path) && !identityPaths.has(entry.path) && entry.value.status !== "blank").map(entry => entry.path)
            : []);
        return { added, carried };
    }, [series, entries]);
    const identityMismatches = series ? series.predecessorIdentity.filter(item => {
        const reading = entries.find(entry => entry.path === item.targetPath)?.value;
        const current = foldIdentity(reading?.normalized ?? reading?.raw);
        return current !== foldIdentity(item.value) && !(item.raw && current === foldIdentity(item.raw));
    }) : [];
    const mismatchBlocked = identityMismatches.length > 0 && !acceptMismatch;
    const templateId = result?.result.templateId ?? null;
    useEffect(() => {
        if (!templateId) return;
        let active = true;
        void api.displayNames(templateId).then(value => { if (active) setItemNames(value.names); }).catch(() => undefined);
        return () => { active = false; };
    }, [templateId]);
    const [itemJoins, setItemJoins] = useState<ItemJoin[]>([]);
    useEffect(() => {
        if (!templateId) return;
        let active = true;
        void api.itemJoins(templateId).then(value => { if (active) setItemJoins(value.items); }).catch(() => undefined);
        const stop = onItemJoinsSaved((savedTemplateId, items) => { if (active && savedTemplateId === templateId) setItemJoins(items); });
        return () => { active = false; stop(); };
    }, [templateId]);
    const templateVersion = result?.result.templateVersion ?? null;
    const joinContext = useMemo(() => ({
        byPath: joinByPath(itemJoins),
        orders: displayOrders(itemJoins),
        configure: templateId ? (paths: string[], anchor: HTMLElement) => openItemJoinEditor(templateId, templateVersion, { paths, anchor }) : undefined,
    }), [itemJoins, templateId, templateVersion]);
    const renameItem = async (entry: ReadingEntry, name: string) => {
        if (!templateId) return;
        try { setItemNames((await api.renameItem(templateId, entry.path, name || null)).names); setMessage(name ? `項目名を「${name}」にしました。` : "項目名を元に戻しました。"); }
        catch (reason) { setError(reason instanceof Error ? reason.message : "項目名を変更できませんでした。"); }
    };
    const display = useMemo(() => buildReviewDisplayModel(entries, previews, result?.reviewStructure, itemNames), [entries, previews, result?.reviewStructure, itemNames]);
    const byName = new Map(artifacts.map(item => [item.name, item.url])), fullImage = byName.get("alignedSource") ?? byName.get("normalizedSource");
    const selectedEntry = entries.find(entry => entry.path === selected), selectedPreview = previews.find(preview => preview.targetPath === selected);
    const warningPaths = new Set(entries.filter(entry => entry.value.status === "uncertain" || entry.value.reviewReason).map(entry => entry.path));
    for (const item of draft?.reviewItems ?? [])
        if (item.path.startsWith("fields."))
            warningPaths.add(item.path);
    const shownWarnings = result?.confirmed ? new Set<string>() : warningPaths;
    const choices = new Set(previews.filter(preview => preview.mappingType === "mark").map(preview => preview.targetPath));
    const readingFor = (field: DocumentIdentity) => entries.find(entry => entry.path === field.targetPath)?.value;
    const identityInvalid = (field: DocumentIdentity) => {
        const reading = readingFor(field), value = reading?.normalized ?? reading?.raw ?? "";
        return !reading || reading.status !== "readable" || !value.trim() || (field.valueType === "digits" && (!/^[0-9]+$/.test(value) || (field.exactLength != null && value.length !== field.exactLength)));
    };
    const update = (path: string, patch: Partial<FieldReading>) => {
        if (locked)
            return;
        setDraft(current => current ? updateReading(current, path, patch) : current);
        if (editingStatus.state === "ready") setAssistantSaveError(null);
        setError(null);
        setMessage(null);
    };
    const editText = (entry: ReadingEntry, text: string) => update(entry.path, { raw: text || null, normalized: text || null, status: text ? "readable" : "blank", reviewReason: null });
    const failure = async (reason: unknown, sent: OcrDocument) => {
        if ((reason instanceof ApiError || reason instanceof EditingError) && ["REVISION_CONFLICT", "CONTEXT_STALE"].includes(reason.code ?? "")) {
            setConflict({ draft: structuredClone(sent), latest: await api.result(jobId).catch(() => null) });
            setError("別の画面で更新されています。入力は保持されています。");
        }
        else
            setError(reason instanceof Error ? reason.message : "保存できませんでした。");
    };
    async function save() {
        if (!result || !draft || locked || saving || inlineDirty)
            return false;
        const sent = draft;
        setSaving(true);
        setError(null);
        setAssistantSaveError(null);
        try {
            if (editingEnabled === null) throw new Error("編集機能の状態を取得してから保存してください。");
            const saved = editingEnabled ? (await editingStore.saveOcrDraft(sent)).ocr! : await api.correct(jobId, result.revision, sent);
            setResult(saved);
            setName(current => current === result.documentName ? saved.documentName : current);
            if (draftRef.current === sent)
                setDraft(saved.result);
            setMessage("保存しました");
            return true;
        }
        catch (reason) {
            await failure(reason, draftRef.current ?? sent);
            setAssistantSaveError(reason instanceof Error ? reason.message : "保存状態を確認してください。");
            return false;
        }
        finally {
            setSaving(false);
        }
    }
    async function confirm() {
        if (!result || !draft || locked || inlineDirty)
            return;
        const sent = draft;
        setConfirmDialog(false);
        setBusy(true);
        setError(null);
        let latest = result, requested = false;
        try {
            if (JSON.stringify(draft) !== JSON.stringify(result.result)) {
                if (editingEnabled === null) throw new Error("編集機能の状態を取得してから確定してください。");
                latest = editingEnabled ? (await editingStore.saveOcrDraft(draft)).ocr! : await api.correct(jobId, result.revision, draft);
                setResult(latest);
                setDraft(latest.result);
            }
            requested = true;
            const confirmed = await api.confirm(jobId, latest.revision, name.trim(), acceptMismatch);
            if (editingEnabled) await editingStore.acceptOwnRevision(confirmed.revision);
            accept(confirmed);
            if (onConfirmed) onConfirmed({ templateId: confirmed.result.templateId, documentName: confirmed.documentName });
            else setMessage("結果と書類名を確定しました。");
        }
        catch (reason) {
            const server = requested ? await api.result(jobId).catch(() => null) : null;
            if (server?.confirmed && server.revision === latest.revision && JSON.stringify(server.result) === JSON.stringify(latest.result) && server.documentName === name.trim()) {
                accept(server);
                if (onConfirmed) onConfirmed({ templateId: server.result.templateId, documentName: server.documentName });
                else setMessage("保存状態を確認しました。確定済みです。");
            }
            else
                await failure(reason, sent);
        }
        finally {
            setBusy(false);
        }
    }
    async function recoverSaved() {
        if (busy || inlineDirty) return;
        const retained = editingStore.retainedOcrDraft(), input = draftRef.current;
        const matches = Boolean(retained && input && JSON.stringify(retained) === JSON.stringify({ fields: input.fields, reviewItems: input.reviewItems }));
        setBusy(true);
        try {
            const saved = await editingStore.recoverResource();
            if (!saved.ocr) throw new Error("保存済みの読み取り結果を取得できません。");
            setResult(saved.ocr);
            // Receipt recovery never resubmits a later edit or replaces it.
            if (draftRef.current === input && (matches || !dirty)) setDraft(saved.ocr.result);
            setName(current => current === result?.documentName ? saved.ocr!.documentName : current);
            const laterInput = draftRef.current !== input || (!matches && dirty);
            setAssistantSaveError(laterInput ? "保持した未保存の入力を確認してください。" : null);
            setError(null); setMessage(laterInput ? `${saved.result.notification} 後続の入力を保持しています。次の操作または保存で反映します。` : saved.result.notification);
        } catch (reason) { setError(reason instanceof Error ? reason.message : "保存結果を取得できません。"); }
        finally { setBusy(false); }
    }
    async function reopen() {
        setBusy(true);
        setError(null);
        try {
            const reopened = await api.reopenResult(jobId, result!.revision);
            if (editingEnabled) await editingStore.acceptOwnRevision(reopened.revision);
            accept(reopened);
            setIdentityChecked(true);
        }
        catch (reason) {
            setError(reason instanceof Error ? reason.message : "再編集できませんでした。");
        }
        finally {
            setBusy(false);
        }
    }
    const assistantInteraction = useInteractionContext(
        JSON.stringify([jobId, selected, mode, identityChecked]),
        JSON.stringify([draft, name, inlineDirty, result?.confirmed, conflict]),
    );
    const assistantSnapshot: AssistantSnapshot = {
        context: { ...assistantInteraction, resourceKind: "ocr", jobId, resourceRevision: result?.revision ?? null,
            snapshotKey: JSON.stringify([result?.revision, result?.confirmed]), mappingId: selectedPreview?.mappingId ?? null, targetPath: selected },
        target: selectedEntry ? { label: reviewEntryContext(display, selectedEntry), currentValue: selectedEntry.value.normalized ?? selectedEntry.value.raw,
            ...(selectedPreview?.artifactUrl ? { image: { kind: "crop", url: selectedPreview.artifactUrl } as const } : {}) } : null,
        operations: selectedEntry && selectedPreview?.mappingId && selectedPreview.mappingType !== "mark" ? ["ocr.set_value"] : [],
        dirty, busy: busy || assistantApplying,
        recoveryDirty: dirty || inlineDirty || Boolean(conflict), recoveryBusy: busy,
        resourceBlocker: result?.confirmed ? "確定済みです。" : conflict || inlineDirty || contextError ? "既存の入力・競合を先に解決してください。" : null,
        blocker: result?.confirmed ? "確定済みです。既存の再編集操作で下書きに戻してください。"
            : conflict ? "競合中の入力を保持しています。最新結果との照合を先に完了してください。"
            : inlineDirty ? "表の入力途中です。入力を確定または取り消してから続けてください。"
            : result && name !== result.documentName ? "書類名の変更を確定するか、元の名前に戻してから続けてください。"
            : mode !== "edit" ? "編集モードで対象を選択してください。"
            : !selectedEntry ? "結果の項目を1つ選択してください。"
            : !selectedPreview?.mappingId ? "選択欄の対応情報を取得できません。別の欄へ代替実行はしません。"
            : selectedPreview.mappingType === "mark" ? "選択式の読み取り値は、既存の選択画面で修正してください。" : null,
        saveError: assistantSaveError,
    };
    if (paged)
        return <PagedReviewPage jobId={jobId} templateNames={templateNames} onClose={onClose} onConfirmed={onConfirmed} closeLabel={closeLabel} onNavigationGuardChange={onNavigationGuardChange}/>;
    if (!result || !draft)
        return <section className="empty-state large"><p role={error ? "alert" : "status"}>{error ?? "結果を読み込んでいます…"}</p>
            {error && <button className="secondary" onClick={() => setLoadAttempt(value => value + 1)}>結果を再取得</button>}
            <button className="ghost" onClick={onClose}><FiArrowLeft />{closeLabel}</button></section>;
    if (!identityChecked && !result.confirmed)
        return <div className="page-stack identity-confirm-page">
    <header className="page-header split has-back"><button className="ghost page-back" onClick={onClose}><FiArrowLeft />{closeLabel}</button><div className="page-title-end"><span className="eyebrow">書類の確認</span><h1>この読み取りはあっていますか？</h1></div></header>
    <aside className="identity-template-card">
      <span className="identity-template-thumb">{fullImage ? <img src={fullImage.replace(/(\/artifacts\/[^/?]+)/, "$1/thumbnail")} alt="" decoding="async" onError={event => { event.currentTarget.hidden = true; }}/> : null}<FiFileText aria-hidden="true"/></span>
      <span className="identity-template-text"><small>使用テンプレート</small><strong>{templateNames[result.result.templateId] ?? "読み取り時のテンプレート"}</strong></span>
    </aside>
    {contextError ? <aside className="error-box">識別設定を取得できませんでした。<button className="secondary" onClick={async () => {
                    try {
                        const context = await api.reviewContext(jobId);
                        setIdentities(context.identityFields);
                        setContextError(false);
                    }
                    catch {
                        setContextError(true);
                    }
                }}>再取得</button></aside> : <section className="identity-confirm-grid">
      {identities.map(field => <article className={`identity-confirm-card ${identityInvalid(field) ? "invalid" : ""}`} key={field.mappingId}><header><h2>「{field.label}」はこの内容ですか？</h2></header>
        <div className="identity-confirm-preview"><img src={previews.find(preview => preview.mappingId === field.mappingId)?.artifactUrl} alt={`${field.label}の読み取り範囲`}/></div>
        {series && (() => {
            const previous = series.predecessorIdentity.find(item => item.targetPath === field.targetPath);
            const differs = identityMismatches.some(item => item.targetPath === field.targetPath);
            const entry = entries.find(item => item.path === field.targetPath);
            return <p className={`identity-previous${differs ? " differs" : ""}`}>前の版「{series.predecessorDocumentName ?? "名称なし"}」: <strong>{previous?.value ?? "（なし）"}</strong>
              {previous?.raw && previous.raw !== previous.value && <small>（読み取り「{previous.raw}」）</small>}{differs ? " · 一致しません" : " · 一致"}
              {previous?.value && entry && !locked && (entry.value.normalized ?? entry.value.raw) !== previous.value
                && <button type="button" className="ghost compact" onClick={() => editText(entry, previous.value!)}>前の版の値にする</button>}</p>;
        })()}
        <label className="field-label"><span className="identity-result-label"><FiEdit2 aria-hidden="true"/>検出結果</span><input aria-label={field.label} value={readingFor(field)?.normalized ?? readingFor(field)?.raw ?? ""} disabled={locked || !readingFor(field)} inputMode={field.valueType === "digits" ? "numeric" : "text"} aria-invalid={identityInvalid(field)} onChange={event => editText(entries.find(entry => entry.path === field.targetPath)!, event.target.value)}/><small>{field.valueType === "digits" ? `数字${field.exactLength ? `${field.exactLength}桁` : ""}で確認してください。` : "書類に記載された内容を確認してください。"}</small></label></article>)}
      {!identities.length && <article className="identity-confirm-card"><h2>読み取り対象の書類</h2><p>識別項目は設定されていません。帳票全体を確認してください。</p>{fullImage && <img className="identity-full-image" src={fullImage} alt="読み取った帳票全体"/>}</article>}
    </section>}
    {series && <aside className={`notice compact series-identity-notice${identityMismatches.length ? " warn" : ""}`}>
      <strong>「{series.predecessorDocumentName ?? "名称なし"}」の更新（版{series.seriesVersion}）</strong>
      <span>{identityMismatches.length
          ? `前の版と${identityMismatches.map(item => item.label).join("・")}が一致しません。同じ書類の更新か確認してください。`
          : "書類を見分ける情報が前の版と一致しています。"}</span>
      {identityMismatches.length > 0 && (acceptMismatch
          ? <span>この書類の更新として続けます。</span>
          : <span className="series-identity-actions">
              <button className="secondary" disabled={busy} onClick={() => setAcceptMismatch(true)}>この書類の更新として続ける</button>
              <button className="ghost" disabled={busy} onClick={async () => {
                  setBusy(true);
                  try { await api.cancel(jobId); onClose(); }
                  catch (reason) { setError(reason instanceof ApiError ? reason.message : "読み取りを取り消せませんでした。"); }
                  finally { setBusy(false); }
              }}>更新をやめる（この読み取りを取り消す）</button>
            </span>)}
    </aside>}
    {error && <p className="error-box" role="alert">{error}</p>}
    <div className="page-actions identity-next">
      <label className="identity-agree"><input type="checkbox" checked={identityAgreed} onChange={event => setIdentityAgreed(event.target.checked)}/><FiCheck aria-hidden="true"/>確認しました</label>
      <button className="primary" disabled={!identityAgreed || busy || saving || contextError || identities.some(identityInvalid) || mismatchBlocked} onClick={() => setIdentityChecked(true)}>次へ<FiArrowRight /></button>
    </div>
  </div>;
    return <ReviewDraftBoundary key={recoveryDiscardEpoch} onDirtyChange={setInlineDirty}><div ref={reviewRoot} className="page-stack review-page">
    <header className="page-header split has-back"><button className="ghost page-back" disabled={assistantApplying} onClick={onClose}><FiArrowLeft />{closeLabel}</button><div><h1>{result.confirmed ? "確定済みの読み取り結果" : "読み取り結果の確認・修正"}</h1></div><div className="page-actions">
      {result.confirmed ? <><button className="secondary" disabled={busy} onClick={() => void reopen()}><FiEdit3 />再編集</button><ExcelExportActions jobId={jobId} documentName={result.documentName}/></> : <>
      <button className="secondary" disabled={locked || inlineDirty || !dirty} onClick={() => void save()}><FiSave />修正を保存</button><button className="primary" disabled={locked || inlineDirty || contextError || identities.some(identityInvalid) || mismatchBlocked} onClick={() => {
        // Without a name the name field glows and takes the focus instead of opening the dialog.
        if (!name.trim()) { setNameWanted(value => value + 1); return; }
        setConfirmDialog(true);
      }}><FiCheck />内容を確定する</button></>}</div></header>
    {conflict && <aside className="error-box"><strong>入力を保持しています</strong><p>最新結果を確認してから、退避した入力を復元できます。</p>
      <button className="secondary" onClick={async () => { const latest = await api.result(jobId); setConflict(current => current ? { ...current, latest } : current); accept(latest); }}>最新結果を表示</button>
      <button className="secondary" disabled={!conflict.latest || conflict.latest.confirmed || busy} onClick={async () => {
                const reviewed = conflict.latest!;
                setBusy(true);
                try {
                if (editingEnabled) await editingStore.resumeOcrAfterReview(reviewed);
                const restored = structuredClone(conflict.latest!.result);
                for (const [key, value] of Object.entries(conflict.draft.fields))
                    if (restored.fields[key])
                        restored.fields[key] = { ...value, sourceRegionId: restored.fields[key].sourceRegionId, boundingBox: restored.fields[key].boundingBox };
                setDraft(restored);
                setConflict(null);
                setError(null);
                setAssistantSaveError("復元した入力は次の操作または保存で反映します。");
                } catch (reason) { setError(reason instanceof Error ? reason.message : "編集を再開できません。"); }
                finally { setBusy(false); }
            }}>退避した入力を復元</button>
      <button className="ghost" disabled={!conflict.latest || busy} onClick={async () => {
        setBusy(true);
        try { if (editingEnabled) await editingStore.resumeOcrAfterReview(conflict.latest!); accept(conflict.latest!); setConflict(null); setError(null); }
        catch (reason) { setError(reason instanceof Error ? reason.message : "編集を再開できません。"); }
        finally { setBusy(false); }
      }}>最新結果を採用</button></aside>}
    {series && seriesMarks && <aside className="notice compact series-review-notice">
      <strong>「{series.predecessorDocumentName ?? "名称なし"}」の更新（版{series.seriesVersion}）</strong>
      <span>{series.mode === "blank_only"
          ? `追加 ${seriesMarks.added.size}欄 · 前の版から引き継ぎ ${seriesMarks.carried.size}欄（前回空欄だった欄と識別情報だけを読み取りました）`
          : "全欄を読み直した更新です。"}</span>
      {mismatchBlocked && <span className="series-review-warning">前の版と{identityMismatches.map(item => item.label).join("・")}が一致しません。識別情報を確認してください。</span>}
      {series.mode === "blank_only" && seriesMarks.added.size > 0 && <span className="series-identity-actions">
        <label className="series-added-toggle"><input type="checkbox" checked={addedOnly} onChange={event => setAddedOnly(event.target.checked)}/>追加した欄を目立たせる</label>
        <button className="secondary compact" onClick={() => {
            const added = entries.filter(entry => seriesMarks.added.has(entry.path)), index = added.findIndex(entry => entry.path === selected);
            setSelected(added[(index + 1) % added.length]?.path ?? null);
        }}>次の追加欄へ</button>
      </span>}
    </aside>}
    <div className="review-assistant-workspace"><div className="generic-review-workspace"><section className="generic-review-source">
      <label className={`field-label document-name-field${nameWanted && !name.trim() ? " is-wanted" : ""}`}>書類名<input key={nameWanted} ref={nameInput} value={name} maxLength={180} disabled={locked} placeholder="書類名を入力してください" onChange={event => setName(event.target.value)}/></label>
      {/* The selected cell's crop floats over the page image, so the image itself never moves. */}
      <div className="ocr-page-preview-frame">{fullImage && <div className="ocr-page-preview"><button type="button" className="ocr-page-preview-open" title="押すと画面いっぱいに表示" onClick={() => setZoomed(true)}><img src={fullImage} alt="読み取った帳票全体"/></button></div>}
        {selectedPreview && <figure className="ocr-region-preview"><img src={selectedPreview.artifactUrl} alt={`${selectedEntry?.label ?? "選択欄"}の切り出し画像`}/><figcaption>{reviewEntryContext(display, selectedEntry)}</figcaption></figure>}</div>
    </section><section className="generic-review-results"><ReviewResultsToolbar interactionMode={mode} editingLocked={locked} confirmed={result.confirmed} warningCount={warningPaths.size} cellCount={entries.length} onInteractionModeChange={setMode} onNextWarning={() => { const warnings = entries.filter(entry => warningPaths.has(entry.path)), index = warnings.findIndex(entry => entry.path === selected); setSelected(warnings[(index + 1) % warnings.length]?.path ?? null); }}/>
      <VoiceTableTarget.Provider value={voiceTableTarget}><ReviewSeriesMarks.Provider value={seriesMarks}><ReviewChecklists.Provider value={checklistOptionsByPath(previews)}><ReviewRenaming.Provider value={mode === "rename" ? renameItem : null}><ReviewJoins.Provider value={joinContext}><div className={`ocr-edit-tables${addedOnly ? " series-added-only" : ""}`}>{display.recordSections.map(section => <GenericRecordReviewTable key={section.id} section={section} selectedPath={selected} warningPaths={shownWarnings} choicePaths={choices} confirmed={locked} interactionMode={mode} onSelect={entry => setSelected(entry.path)} onEdit={editText} onMoveRow={(section, from, to) => {
                if (!locked)
                    setDraft(current => current ? swapGenericRecordRowsAtFixedRegions(current, section, from, to) : current);
            }}/>)}
        {display.parentSections.map(section => <ReviewTableSection key={section.id} title={section.title} joinPaths={sectionItemPaths(section.entries)}><KeyValueReviewTable entries={section.entries} selectedPath={selected} warningPaths={shownWarnings} choicePaths={choices} confirmed={locked || mode !== "edit"} onSelect={entry => setSelected(entry.path)} onEdit={editText}/></ReviewTableSection>)}
        {display.independentEntries.length > 0 && <ReviewTableSection title="独立項目"><IndependentReviewGroups groups={display.independentGroups} selectedPath={selected} warningPaths={shownWarnings} choicePaths={choices} confirmed={locked || mode !== "edit"} onSelect={entry => setSelected(entry.path)} onEdit={editText}/></ReviewTableSection>}
      </div></ReviewJoins.Provider></ReviewRenaming.Provider></ReviewChecklists.Provider></ReviewSeriesMarks.Provider></VoiceTableTarget.Provider>{!result.confirmed && selectedEntry && mode === "edit" && selectedPreview?.markRecognitionMode !== "checklist" && <AnchoredReviewPopover path={selectedEntry.path} wide={selectedPreview?.mappingType === "mark" && markPopoverIsWide(selectedPreview)} onClose={() => setSelected(null)}>
        {selectedPreview?.mappingType === "mark"
          ? <MarkStatusPopover entry={{ ...selectedEntry, label: reviewEntryLabel(display, selectedEntry) }} preview={selectedPreview} confirmed={locked} onChange={patch => update(selectedEntry.path, patch)} onClose={() => setSelected(null)}/>
          : <CellStatusPopover entry={{ ...selectedEntry, label: reviewEntryLabel(display, selectedEntry) }} confirmed={locked} onChange={patch => update(selectedEntry.path, patch)} onClose={() => setSelected(null)}/>}</AnchoredReviewPopover>}
    </section></div>
    {editingEnabled && <ReviewAiFocus rootRef={reviewRoot} focus={editingStatus.focus} context={editingStatus.context} viewRevision={result.revision} />}
    {/* Voice input and undo float over the form image like on the template screen; results speak in its bubble. */}
    {editingEnabled ? <EditingControls store={editingStore} pageScope={{ mode: "all", pageId: null }} tableSelector={false}
      voiceOnly floatingAnchor=".generic-review-source .ocr-page-preview"
      selectedEntities={selected?.startsWith("fields.") ? [selected.slice(7)] : []} selectionEpoch={assistantInteraction.selectionEpoch}
      manualActive={dirty || inlineDirty || busy || Boolean(conflict)}
      disabled={result.confirmed ? "確定済みです。再編集で音声入力を使用できます。" : contextError ? "帳票との対応情報を取得できません。" : null}
      onSaved={saved => {
        if (!saved.ocr) throw new Error("保存済みの読み取り結果を取得できません。");
        if (dirty || inlineDirty || busy || conflict) throw new Error("保存済み・表示更新待ちです。未保存の入力を保持しています。");
        accept(saved.ocr);
      }} /> : <AssistantPanel snapshot={assistantSnapshot} adapter={{ begin: () => undefined, end: () => undefined,
      setApplying: setAssistantApplying,
      saved: async (receipt, discardDraft = false) => {
        try {
          const next = await api.result(jobId);
          if (next.revision < receipt.revision) throw new Error("保存した内容をまだ読み込めません。少し待ってから画面を再読み込みしてください。");
          if ((dirty || conflict || inlineDirty) && !discardDraft) throw new Error("未保存の入力を保持しています。");
          if (busy) throw new Error("保存中の処理を先に完了してください。");
          if (discardDraft && inlineDirty) { setRecoveryDiscardEpoch(current => current + 1); setInlineDirty(false); }
          accept(next);
          setConflict(null);
          setMessage(`「${receipt.target.label}」${receipt.undoOfId ? "の変更を取り消しました。" : "の変更を保存しました。"}`);
        } catch (reason) {
          setAssistantSaveError("保存は完了しています。表示の再取得に失敗しました。");
          throw reason;
        }
      },
      save,
      discard: () => {
        if (!result || busy || conflict || inlineDirty || assistantSaveError) return false;
        setDraft(result.result); setName(result.documentName); setError(null); setMessage("未保存の修正を破棄しました。");
        return true;
      },
    }} />}</div>
    {editingEnabled && ["recovering", "saved_display_pending"].includes(editingStatus.state) && <section className="inline-warning" aria-label="修正保存の復旧">
      <p role="status">{editingStatus.message}</p>
      <button className="secondary" disabled={busy || inlineDirty} onClick={() => void recoverSaved()}>保存結果を再取得</button>
    </section>}
    {!result.confirmed && draft.reviewItems.length > 0 && <details className="review-notes"><summary>確認メモ {draft.reviewItems.length}件</summary>{draft.reviewItems.map((item, index) => <p key={index}>{entries.find(entry => entry.path === item.path)?.label ?? "帳票全体"}：{item.message}</p>)}</details>}
    {zoomed && fullImage && <div className="ocr-page-lightbox" role="dialog" aria-modal="true" aria-label="帳票全体の拡大表示" onClick={() => setZoomed(false)}>
      <img src={fullImage} alt="読み取った帳票全体（拡大）"/>
      <button type="button" className="ocr-page-lightbox-close" aria-label="閉じる" onClick={() => setZoomed(false)}>×</button>
    </div>}
    {confirmDialog && <div className="modal-backdrop"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="confirm-title"><h2 id="confirm-title">結果を確定しますか？</h2><p>確定することで、この内容がチャットで使えます。</p><div className="page-actions"><button className="secondary" onClick={() => setConfirmDialog(false)}>確認を続ける</button><button className="primary" onClick={() => void confirm()}><FiCheck />確定する</button></div></section></div>}
    {busy && <div className="review-confirm-loading-overlay" role="status"><div className="review-confirm-loading-card"><span className="review-confirm-loading-spinner"/><strong>保存状態を更新しています</strong></div></div>}
  </div></ReviewDraftBoundary>;
}
type ReadingPathPair = {
    firstPath: string;
    secondPath: string;
};
function readingAtPath(document: OcrDocument, path: string): FieldReading | undefined { const [root, key, ...rest] = path.split("."); return root === "fields" && !rest.length ? document.fields[key] : undefined; }
function setReadingAtPath(document: OcrDocument, path: string, value: FieldReading) {
    const [root, key, ...rest] = path.split(".");
    if (root !== "fields" || rest.length || !document.fields[key])
        return false;
    document.fields[key] = value;
    return true;
}
function updateReading(document: OcrDocument, path: string, patch: Partial<FieldReading>): OcrDocument {
    const next = structuredClone(document), reading = readingAtPath(next, path);
    if (!reading)
        return document;
    setReadingAtPath(next, path, { ...reading, ...patch });
    next.reviewItems = next.reviewItems.filter(item => item.path !== path);
    return next;
}
const CALVING_OUTCOME_MARKS = ["双", "早", "流", "死"] as const;
const CALVING_SEX_MARKS = ["♂", "♀"] as const;
function selectedMarkLabels(value: FieldReading | undefined, allowed: readonly string[]) {
    const text = `${value?.raw ?? ""} ${value?.normalized ?? ""}`;
    return allowed.filter((label) => text.includes(label));
}
function swapReadingsAtFixedRegions(document: OcrDocument, pairs: readonly ReadingPathPair[]): OcrDocument {
    if (pairs.length === 0)
        return document;
    const paths = pairs.flatMap(({ firstPath, secondPath }) => [firstPath, secondPath]);
    if (new Set(paths).size !== paths.length)
        return document;
    const snapshots = new Map<string, FieldReading>();
    for (const path of paths) {
        const reading = readingAtPath(document, path);
        if (!reading)
            return document;
        snapshots.set(path, structuredClone(reading));
    }
    const next = structuredClone(document);
    const destinationBySource = new Map<string, string>();
    for (const { firstPath, secondPath } of pairs) {
        const first = snapshots.get(firstPath);
        const second = snapshots.get(secondPath);
        if (!first || !second)
            return document;
        setReadingAtPath(next, firstPath, readingValueAtFixedRegion(second, first));
        setReadingAtPath(next, secondPath, readingValueAtFixedRegion(first, second));
        destinationBySource.set(firstPath, secondPath);
        destinationBySource.set(secondPath, firstPath);
    }
    next.reviewItems = next.reviewItems.map((item) => {
        const destinationPath = destinationBySource.get(item.path);
        if (!destinationPath)
            return item;
        return {
            ...item,
            path: destinationPath,
            sourceRegionId: readingAtPath(next, destinationPath)?.sourceRegionId ?? null,
        };
    });
    return next;
}
function genericRecordRowPairs(section: ReviewDisplayRecordSection, fromIndex: number, toIndex: number): ReadingPathPair[] | null {
    if (Math.abs(fromIndex - toIndex) !== 1
        || fromIndex < 0
        || toIndex < 0
        || fromIndex >= section.rows.length
        || toIndex >= section.rows.length)
        return null;
    const fromRow = section.rows[fromIndex];
    const toRow = section.rows[toIndex];
    const pairs: ReadingPathPair[] = [];
    for (const column of section.columns) {
        const firstPath = fromRow.cells.find((cell) => cell.columnId === column.id)?.entry?.path;
        const secondPath = toRow.cells.find((cell) => cell.columnId === column.id)?.entry?.path;
        if (!firstPath || !secondPath)
            return null;
        pairs.push({ firstPath, secondPath });
    }
    const paths = pairs.flatMap(({ firstPath, secondPath }) => [firstPath, secondPath]);
    return new Set(paths).size === paths.length ? pairs : null;
}
export function canSwapGenericRecordRows(section: ReviewDisplayRecordSection, fromIndex: number, toIndex: number): boolean {
    return genericRecordRowPairs(section, fromIndex, toIndex) !== null;
}
export function swapGenericRecordRowsAtFixedRegions(document: OcrDocument, section: ReviewDisplayRecordSection, fromIndex: number, toIndex: number): OcrDocument {
    const pairs = genericRecordRowPairs(section, fromIndex, toIndex);
    return pairs ? swapReadingsAtFixedRegions(document, pairs) : document;
}
function readingValueAtFixedRegion(value: FieldReading, fixedRegion: FieldReading): FieldReading {
    return {
        ...structuredClone(value),
        sourceRegionId: fixedRegion.sourceRegionId,
        boundingBox: fixedRegion.boundingBox
            ? { ...fixedRegion.boundingBox }
            : null,
    };
}

/** Identity comparison with the previous version: width, spacing and case are ignored (same as the server). */
function foldIdentity(value: string | null | undefined): string {
    return (value ?? "").normalize("NFKC").replace(/\s+/g, "").toLowerCase();
}
