import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  FiAlertTriangle,
  FiArrowUp,
  FiCheck,
  FiChevronDown,
  FiCopy,
  FiExternalLink,
  FiFileText,
  FiFilter,
  FiImage,
  FiInfo,
  FiMessageSquare,
  FiMic,
  FiMicOff,
  FiPlus,
  FiRefreshCw,
  FiSearch,
  FiX,
} from "react-icons/fi";
import { EditingError } from "../editing/client";
import { askStore, openQuestion, type ScopeItem, type Turn } from "../structured/askStore";
import {
  evidenceHref,
  structuredClient,
  tableCsv,
  type AnswerTable,
  type DocumentSummary,
  type Evidence,
  type FieldName,
  type StructuredStatus,
} from "../structured/client";
import { QuestionVoiceCapture } from "../structured/voiceCapture";
import { VoiceMeter } from "../components/VoiceMeter";

/** Same limit as the questions API (`documentIds` max 20). */
const MAX_SCOPE = 20;
// Last known status and documents, so coming back to the page shows them at once (refreshed on mount).
let cachedStatus: StructuredStatus | null = null;
let cachedDocuments: DocumentSummary[] | null = null;

export function AskPage({ onOpenReview }: { onOpenReview: (jobId: string, field?: string | null) => void }) {
  const state = useSyncExternalStore(askStore.subscribe, askStore.getSnapshot);
  const { turns, scope, filter, templateId, draft, replyMode, evidence } = state;
  const [status, setStatus] = useState<StructuredStatus | null | "unavailable">(cachedStatus);
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(cachedDocuments);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [fieldNames, setFieldNames] = useState<FieldName[]>([]);
  const [suggestionFields, setSuggestionFields] = useState<Record<string, FieldName[]>>({});
  const [scopeOpen, setScopeOpen] = useState(false);
  const pageRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // One capture per page visit; a completed transcript is sent exactly like typed text.
  // Spoken text goes into the input box: the user checks it and sends it with Enter.
  const [voice] = useState(() => new QuestionVoiceCapture((spoken) => {
    askStore.set((current) => ({ draft: current.draft.trim() ? `${current.draft.trimEnd()} ${spoken}` : spoken }));
    inputRef.current?.focus();
  }));
  const voiceState = useSyncExternalStore(voice.subscribe, voice.getSnapshot);
  useEffect(() => () => { void voice.stop(); }, [voice]);

  const enabled = status !== null && status !== "unavailable" && status.enabled;

  const loadDocuments = useCallback(async () => {
    try {
      const page = await structuredClient.documents();
      cachedDocuments = page.items;
      setDocuments(page.items);
      setDocumentsError(null);
    } catch (error) {
      setDocumentsError(error instanceof EditingError ? error.message : "確定済みの書類を取得できませんでした。");
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    void structuredClient.status()
      .then((value) => { cachedStatus = value; if (!stopped) setStatus(value); })
      .catch(() => { if (!stopped) setStatus("unavailable"); });
    return () => { stopped = true; };
  }, []);
  useEffect(() => { if (enabled) void loadDocuments(); }, [enabled, loadDocuments]);

  const allDocuments = useMemo(() => documents ?? [], [documents]);
  const templates = useMemo(() => {
    const byId = new Map<string, string>();
    for (const document of allDocuments) if (!byId.has(document.templateId)) byId.set(document.templateId, document.templateName);
    return [...byId].map(([id, name]) => ({ id, name }));
  }, [allDocuments]);
  const byJob = useMemo(() => new Map(allDocuments.map((document) => [document.jobId, document])), [allDocuments]);
  // A document dropped from the confirmed list (e.g. reopened) silently leaves the scope.
  const scopeItems: ScopeItem[] = scope.flatMap((jobId) => {
    const document = byJob.get(jobId);
    return document ? [{ jobId, label: documentLabel(document) }] : [];
  });
  const visible = useMemo(() => {
    const needle = fold(filter.trim());
    return allDocuments.filter((document) =>
      (!templateId || document.templateId === templateId)
      && (!needle || [document.documentName, document.templateName, ...document.identity.map((item) => item.value)].some((value) => value && fold(value).includes(needle))));
  }, [allDocuments, filter, templateId]);
  // Field names are shown for one template: the chosen one, or the one all selected documents share.
  const fieldTemplate = templateId || singleTemplate(scopeItems.map((item) => byJob.get(item.jobId)!));
  useEffect(() => {
    if (!enabled || !fieldTemplate) { setFieldNames([]); return; }
    let stopped = false;
    void structuredClient.fields(fieldTemplate)
      .then((page) => { if (!stopped) setFieldNames(page.items); })
      .catch(() => { if (!stopped) setFieldNames([]); });
    return () => { stopped = true; };
  }, [enabled, fieldTemplate]);
  // Example questions come from the user's own documents and field names, never a fixed form.
  const exampleDocuments = useMemo(() => (scopeItems.length > 0 ? scopeItems.map((item) => byJob.get(item.jobId)!) : allDocuments).slice(0, 2), [scopeItems, byJob, allDocuments]);
  useEffect(() => {
    if (!enabled || turns.length > 0) return;
    const missing = [...new Set(exampleDocuments.map((document) => document.templateId))].filter((id) => !(id in suggestionFields));
    if (missing.length === 0) return;
    let stopped = false;
    void Promise.all(missing.map((id) => structuredClient.fields(id).then((page) => [id, page.items] as const).catch(() => [id, []] as const)))
      .then((entries) => { if (!stopped) setSuggestionFields((current) => ({ ...current, ...Object.fromEntries(entries) })); });
    return () => { stopped = true; };
  }, [enabled, turns.length, exampleDocuments, suggestionFields]);
  const suggestions = useMemo(() => exampleQuestions(exampleDocuments, suggestionFields), [exampleDocuments, suggestionFields]);

  const busy = turns.some((turn) => turn.pending);
  const open = openQuestion(state);
  const answering = open !== null && replyMode;
  const submit = (raw: string) => {
    const text = raw.trim();
    if (!text || busy || !enabled) return;
    askStore.set({ draft: "" });
    if (answering) askStore.replyText(text);
    else askStore.ask(text, scopeItems);
  };
  const toggleScope = (jobId: string) => {
    askStore.set((current) => ({
      scope: current.scope.includes(jobId)
        ? current.scope.filter((id) => id !== jobId)
        : current.scope.length >= MAX_SCOPE ? current.scope : [...current.scope, jobId],
    }));
  };
  const insertField = (name: FieldName) => {
    const words = name.kind === "column" && name.tableTitle ? `${name.tableTitle}の${name.label}` : name.label;
    askStore.set((current) => ({ draft: current.draft + words }));
    setScopeOpen(false);
    inputRef.current?.focus();
  };

  // Keep the newest message in view inside the conversation (the page itself does not jump).
  const last = turns[turns.length - 1];
  const scrollKey = `${turns.length}:${last?.pending ? 1 : 0}:${last?.result ? 1 : 0}:${last?.error ? 1 : 0}`;
  const scrolledOnce = useRef(false);
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    // Coming back to the page jumps straight to the latest message; new messages glide in.
    // A finished answer is shown from its question down, so a long answer is read from the top.
    const bottom = list.scrollHeight - list.clientHeight;
    const asked = [...list.querySelectorAll<HTMLElement>(".ask-msg.user")].pop();
    const top = asked && !last?.pending ? Math.min(bottom, asked.offsetTop - 12) : bottom;
    list.scrollTo({ top: Math.max(top, 0), behavior: scrolledOnce.current ? "smooth" : "instant" });
    scrolledOnce.current = true;
  }, [scrollKey]);
  // The page fills the window below wherever the app's header ends, so only the conversation scrolls.
  useLayoutEffect(() => {
    const page = pageRef.current;
    if (!page) return;
    const fit = () => {
      const gap = 16;
      const top = page.getBoundingClientRect().top + window.scrollY;
      // The main area's bottom padding would push the page past the window; give it back.
      const bottom = parseFloat(getComputedStyle(page.parentElement ?? page).paddingBottom) || 0;
      page.style.setProperty("--ask-height", `${Math.max(window.innerHeight - top - gap, 0)}px`);
      page.style.marginBottom = `${Math.min(gap - bottom, 0)}px`;
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 168)}px`;
  }, [draft]);
  const openQuestionId = open?.question.questionId;
  useEffect(() => { if (openQuestionId) inputRef.current?.focus({ preventScroll: true }); }, [openQuestionId]);

  const qaConnected = enabled && status.qaConnected;
  const voiceConnected = enabled && status.voiceConnected;
  const listening = voiceState.mode !== "stopped";
  const latestId = last?.id ?? null;
  const placeholder = !enabled ? "質問は利用できません"
    : answering ? "選択肢にない場合は、ここに書いて送ってください"
    : scopeItems.length > 0 ? "選んだ書類について質問を入力"
    : "例：書類名や番号と、知りたい欄の名前を入れて質問";

  return (
    <div className="ask-page" ref={pageRef}>
      <header className="ask-header">
        <div>
          <h1>質問</h1>
          <p>確定した書類の内容を、ふだんの言葉で聞けます。答えには根拠の欄の画像が付きます。</p>
        </div>
        <div className="ask-header-actions">
          <button type="button" className="secondary compact ask-scope-toggle" onClick={() => setScopeOpen(true)}>
            <FiFilter aria-hidden="true" />対象{scopeItems.length > 0 ? `（${scopeItems.length}）` : ""}
          </button>
          {turns.length > 0 && (
            <button type="button" className="secondary compact" disabled={busy} onClick={() => askStore.clear()}>
              <FiPlus aria-hidden="true" />新しい会話
            </button>
          )}
        </div>
      </header>

      {status === "unavailable" && (
        <aside className="notice danger"><strong>接続を確認できません</strong><span>サーバーへ接続できるまで質問は送れません。</span></aside>
      )}
      {status !== null && status !== "unavailable" && !status.enabled && (
        <aside className="notice danger"><strong>この環境では質問が無効です</strong><span>構造化データ機能（STRUCTURED_DATA_ENABLED）を有効にすると使えます。</span></aside>
      )}
      {enabled && !qaConnected && (
        <aside className="notice"><strong>AIが未接続です</strong><span>欄の名前を書いた質問には答えられます。それ以外は欄を選んで答えます。</span></aside>
      )}

      <div className={evidence ? "ask-layout with-evidence" : "ask-layout"}>
        {scopeOpen && <button type="button" className="ask-sheet-backdrop" aria-label="対象の選択を閉じる" onClick={() => setScopeOpen(false)} />}
        <aside className={scopeOpen ? "ask-scope open" : "ask-scope"} aria-label="対象の書類">
          <div className="ask-panel-head">
            <h2>対象の書類</h2>
            <button type="button" className="ask-icon-button ask-scope-close" onClick={() => setScopeOpen(false)} aria-label="対象の選択を閉じる"><FiX aria-hidden="true" /></button>
          </div>
          <p className="ask-panel-lead">{scopeItems.length > 0 ? `${scopeItems.length}件を選択中。選んだ書類の中から答えます。` : "選ばなければ、すべての確定済み書類から探します。"}</p>
          {templates.length > 1 && (
            <label className="ask-template">
              <span>テンプレート</span>
              <select value={templateId} disabled={!enabled} onChange={(event) => askStore.set({ templateId: event.target.value })}>
                <option value="">すべて</option>
                {templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
              </select>
            </label>
          )}
          <label className="ask-filter">
            <FiSearch aria-hidden="true" />
            <input type="search" value={filter} placeholder="書類名・番号・名前で絞り込み" disabled={!enabled} onChange={(event) => askStore.set({ filter: event.target.value })} />
          </label>
          {documentsError && (
            <p className="ask-inline-error" role="alert">{documentsError}<button type="button" className="link-button" onClick={() => void loadDocuments()}>再取得</button></p>
          )}
          {enabled && documents === null && !documentsError && <p className="ask-panel-lead">書類を読み込んでいます…</p>}
          {enabled && documents !== null && visible.length === 0 && (
            <p className="ask-panel-lead">{allDocuments.length === 0 ? "確定済みの書類はまだありません。読み取り結果を確定すると質問できます。" : "条件に合う書類はありません。"}</p>
          )}
          <ul className="ask-document-list">
            {visible.map((document) => {
              const checked = scope.includes(document.jobId);
              const identity = document.identity.filter((item) => item.value).map((item) => `${item.label} ${item.value}`).join("・");
              return (
                <li key={document.jobId} className={checked ? "selected" : undefined}>
                  <label className="ask-document">
                    <input type="checkbox" checked={checked} disabled={!checked && scope.length >= MAX_SCOPE} onChange={() => toggleScope(document.jobId)} />
                    <span>
                      <strong>{document.documentName ?? "（名称なし）"}</strong>
                      {identity && <small>{identity}</small>}
                      <small className="ask-document-meta">{document.templateName} · {new Date(document.confirmedAt).toLocaleDateString("ja-JP")}確定{document.seriesVersion > 1 ? ` · 版${document.seriesVersion}` : ""}</small>
                    </span>
                  </label>
                  <button type="button" className="ask-icon-button" onClick={() => onOpenReview(document.jobId)} aria-label={`${document.documentName ?? "書類"}を開く`} title="書類を開く">
                    <FiExternalLink aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
          {scopeItems.length > 0 && <button type="button" className="ghost compact" onClick={() => askStore.set({ scope: [] })}>選択をすべて外す</button>}
          {fieldNames.length > 0 && (
            <div className="ask-fields">
              <h3>聞ける欄<small>押すと入力欄に入ります</small></h3>
              <div className="ask-chip-row">
                {fieldNames.map((name) => (
                  <button key={`${name.kind}:${name.tableTitle ?? ""}:${name.label}`} type="button" className="ask-chip" onClick={() => insertField(name)}
                    title={name.kind === "column" ? `表「${name.tableTitle}」の列` : "欄"}>
                    {name.label}{name.kind === "column" && <small>表</small>}
                  </button>
                ))}
              </div>
            </div>
          )}
        </aside>

        <section className="ask-chat" aria-label="質問と回答">
          <div className="ask-messages" ref={listRef} role="log" aria-live="polite">
            {turns.length === 0
              ? (
                <div className="ask-welcome">
                  <span className="ask-welcome-icon" aria-hidden="true"><FiMessageSquare /></span>
                  <h2>何を調べますか？</h2>
                  <p>書類の名前や番号と、知りたい欄の名前を入れて聞いてください。合計・件数・最大・最小や、日付から今日までの経過も求められます。</p>
                  {suggestions.length > 0 && (
                    <div className="ask-suggestions">
                      {suggestions.map((suggestion) => (
                        <button key={suggestion} type="button" disabled={!enabled || busy} onClick={() => submit(suggestion)}>{suggestion}</button>
                      ))}
                    </div>
                  )}
                  {documents !== null && <small>確定済みの書類 {allDocuments.length}件{scopeItems.length > 0 ? `のうち、選んだ${scopeItems.length}件` : ""}が対象です</small>}
                </div>
              )
              : turns.map((turn) => (
                <TurnView
                  key={turn.id}
                  turn={turn}
                  latest={turn.id === latestId}
                  busy={busy}
                  evidence={evidence}
                  onChoose={(choice) => askStore.choose(turn.id, choice)}
                  onRetry={() => askStore.retry(turn.id)}
                  onShowEvidence={(item) => askStore.set({ evidence: item })}
                  onOpenReview={onOpenReview}
                />
              ))}
          </div>

          <form className="ask-composer" onSubmit={(event) => { event.preventDefault(); submit(draft); }}>
            {open && (
              <div className={answering ? "ask-reply-banner" : "ask-reply-banner new"}>
                <span>{answering ? <>「{open.question.message}」への回答として送ります</> : "新しい質問として送ります"}</span>
                <button type="button" className="link-button" onClick={() => askStore.set({ replyMode: !replyMode })}>
                  {answering ? "新しい質問にする" : "回答として送る"}
                </button>
              </div>
            )}
            {!answering && scopeItems.length > 0 && (
              <div className="ask-scope-bar" aria-label="選んだ書類">
                {scopeItems.map((item) => (
                  <span key={item.jobId} className="ask-scope-chip">
                    <FiFileText aria-hidden="true" />{item.label}
                    <button type="button" onClick={() => toggleScope(item.jobId)} aria-label={`${item.label}を対象から外す`}><FiX aria-hidden="true" /></button>
                  </span>
                ))}
              </div>
            )}
            <div className="ask-input-row">
              <label className="sr-only" htmlFor="ask-input">質問</label>
              <textarea
                id="ask-input"
                ref={inputRef}
                value={draft}
                rows={1}
                placeholder={placeholder}
                disabled={!enabled}
                onChange={(event) => askStore.set({ draft: event.target.value })}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
                  event.preventDefault();
                  submit(draft);
                }}
              />
              {voiceConnected && <VoiceMeter read={voice.meter.read} active={voiceState.mode === "listening"} speaking={voiceState.message === "聞き取り中…"} />}
              {voiceConnected && (
                <button
                  type="button"
                  className={listening ? "ask-round-button ask-mic listening" : "ask-round-button ask-mic"}
                  aria-pressed={listening}
                  aria-label={listening ? "音声入力を止める" : "音声で質問"}
                  title={listening ? "音声入力を止める" : "音声で質問"}
                  onClick={() => { if (listening) void voice.finish(); else void voice.start(); }}
                >
                  {listening ? <FiMicOff aria-hidden="true" /> : <FiMic aria-hidden="true" />}
                </button>
              )}
              <button type="submit" className="ask-round-button ask-send" disabled={!enabled || busy || !draft.trim()} aria-label="送信" title="送信（Enter）">
                <FiArrowUp aria-hidden="true" />
              </button>
            </div>
            <p className="ask-composer-foot" role="status" aria-live="polite">
              {voiceState.caption
                ? <span className="ask-caption">{voiceState.caption}</span>
                : voiceState.message
                  ? <span>{voiceState.message}</span>
                  : <span>Enterで送信 · Shift+Enterで改行{busy ? " · 回答を待っています" : ""}</span>}
            </p>
          </form>
        </section>

        {evidence && (
          <EvidencePanel evidence={evidence} onClose={() => askStore.set({ evidence: null })} onOpenReview={onOpenReview} />
        )}
      </div>
    </div>
  );
}

function fold(value: string): string {
  return value.normalize("NFKC").toLowerCase();
}

function singleTemplate(documents: DocumentSummary[]): string {
  const ids = new Set(documents.map((document) => document.templateId));
  return ids.size === 1 ? [...ids][0] : "";
}

function documentLabel(document: DocumentSummary): string {
  return document.documentName ?? document.identity.find((item) => item.value)?.value ?? "（名称なし）";
}

/** Up to four example questions built from the documents' own names and fields. */
function exampleQuestions(documents: DocumentSummary[], fields: Record<string, FieldName[]>): string[] {
  const questions: string[] = [];
  for (const document of documents) {
    const names = fields[document.templateId] ?? [];
    const identity = new Set(document.identity.map((item) => item.label));
    const label = documentLabel(document);
    const field = names.find((name) => name.kind === "field" && !identity.has(name.label)) ?? names.find((name) => name.kind === "field");
    const column = names.find((name) => name.kind === "column");
    if (field) questions.push(`${label}の${field.label}は？`);
    if (column) questions.push(`${label}の${column.tableTitle ? `${column.tableTitle}の` : ""}${column.label}を一覧で見せて`);
  }
  return [...new Set(questions)].slice(0, 4);
}

function statusNote(status: string): string | null {
  if (status === "uncertain") return "読み取り未確認";
  if (status === "blank") return "空欄";
  return null;
}

function TurnView({ turn, latest, busy, evidence, onChoose, onRetry, onShowEvidence, onOpenReview }: {
  turn: Turn;
  latest: boolean;
  busy: boolean;
  evidence: Evidence | null;
  onChoose: (choice: { value: string; label: string }) => void;
  onRetry: () => void;
  onShowEvidence: (evidence: Evidence) => void;
  onOpenReview: (jobId: string, field?: string | null) => void;
}) {
  const result = turn.result;
  const question = result?.question ?? null;
  const canChoose = latest && !busy;
  return (
    <>
      <div className={turn.request.replyTo ? "ask-msg user reply" : "ask-msg user"}>
        <div className="ask-bubble">{turn.text}</div>
        {!turn.request.replyTo && turn.scope.length > 0 && <small className="ask-msg-meta">対象：{turn.scope.map((item) => item.label).join("、")}</small>}
      </div>
      <div className={`ask-msg bot${result ? ` ${result.state}` : ""}${turn.error ? " failed" : ""}`}>
        <span className="ask-avatar" aria-hidden="true">AI</span>
        <div className="ask-bubble">
          {turn.pending && <p className="ask-typing" role="status"><i /><i /><i /><span>確定データを調べています</span></p>}
          {turn.error && (
            <div className="ask-failed">
              <p role="alert"><FiAlertTriangle aria-hidden="true" />{turn.error}</p>
              {latest && (
                <button type="button" className="secondary compact" disabled={busy} onClick={onRetry}>
                  <FiRefreshCw aria-hidden="true" />もう一度送る
                </button>
              )}
            </div>
          )}
          {result && (
            <>
              <p className="ask-answer">{result.answer}</p>
              {result.notes.length > 0 && (
                <ul className="ask-notes">{result.notes.map((note, index) => <li key={index}><FiInfo aria-hidden="true" />{note}</li>)}</ul>
              )}
              {question && !question.optional && (
                <div className="ask-choices" role="group" aria-label={question.message}>
                  {question.choices.map((choice) => (
                    <button key={choice.value} type="button" className={turn.picked === choice.value ? "picked" : undefined}
                      disabled={!canChoose} aria-pressed={turn.picked === choice.value} onClick={() => onChoose(choice)}>
                      {turn.picked === choice.value && <FiCheck aria-hidden="true" />}{choice.label}
                    </button>
                  ))}
                  {canChoose && <p className="ask-choices-hint">当てはまるものがなければ、下の入力欄に書いて送ってください。</p>}
                </div>
              )}
              {result.table && result.table.rows.length > 0 && <AnswerTableView table={result.table} />}
              {result.steps.length > 0 && (
                <details className="ask-steps" onToggle={(event) => { if (event.currentTarget.open) event.currentTarget.scrollIntoView({ block: "nearest", behavior: "smooth" }); }}>
                  <summary><FiChevronDown aria-hidden="true" />計算の過程</summary>
                  <dl>{result.steps.map((step, index) => <div key={index}><dt>{step.label}</dt><dd>{step.value}</dd></div>)}</dl>
                </details>
              )}
              {result.caveats.length > 0 && (
                <div className="ask-caveats">
                  <strong><FiAlertTriangle aria-hidden="true" />確認してください</strong>
                  <ul>{result.caveats.slice(0, 3).map((caveat, index) => <li key={index}>{caveat}</li>)}</ul>
                  {result.caveats.length > 3 && (
                    <details>
                      <summary>ほか{result.caveats.length - 3}件</summary>
                      <ul>{result.caveats.slice(3).map((caveat, index) => <li key={index}>{caveat}</li>)}</ul>
                    </details>
                  )}
                </div>
              )}
              {result.evidence.length > 0 && (
                <div className="ask-evidence-chips" aria-label={`根拠（${result.evidence.length}件）`}>
                  <span><FiImage aria-hidden="true" />根拠の欄</span>
                  {result.evidence.map((item, index) => {
                    const note = statusNote(item.status);
                    const active = evidence !== null && evidence.jobId === item.jobId && evidence.fieldId === item.fieldId && evidence.label === item.label;
                    return (
                      <button key={index} type="button" className={`ask-chip${note ? " warn" : ""}${active ? " active" : ""}`} onClick={() => onShowEvidence(item)} aria-pressed={active}>
                        {item.label}{note && <small>{note}</small>}
                      </button>
                    );
                  })}
                </div>
              )}
              {question?.optional && canChoose && (
                <details className="ask-repick" onToggle={(event) => { if (event.currentTarget.open) event.currentTarget.scrollIntoView({ block: "nearest", behavior: "smooth" }); }}>
                  <summary><FiChevronDown aria-hidden="true" />{question.message}</summary>
                  <div className="ask-choices">
                    {question.choices.map((choice) => (
                      <button key={choice.value} type="button" onClick={() => onChoose(choice)}>{choice.label}</button>
                    ))}
                  </div>
                </details>
              )}
              {result.documents.length > 0 && result.state !== "clarifying" && (
                <footer className="ask-doc-links">
                  {result.documents.map((document) => (
                    <button key={document.jobId} type="button" className="link-button" onClick={() => onOpenReview(document.jobId)}>
                      <FiExternalLink aria-hidden="true" />{document.documentName ?? "書類"}を開く
                    </button>
                  ))}
                </footer>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

function AnswerTableView({ table }: { table: AnswerTable }) {
  const [copied, setCopied] = useState<"done" | "failed" | null>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(tableCsv(table));
      setCopied("done");
    } catch {
      setCopied("failed");
    }
    window.setTimeout(() => setCopied(null), 2000);
  };
  return (
    <div className="ask-table-block">
      <div className="ask-table-wrap">
        <table className="ask-table">
          <thead><tr>{table.columns.map((column, index) => <th key={index}>{column}</th>)}</tr></thead>
          <tbody>
            {table.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell ?? <span className="ask-empty">—</span>}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className="ask-copy" onClick={() => void copy()}>
        {copied === "done" ? <><FiCheck aria-hidden="true" />コピーしました</> : <><FiCopy aria-hidden="true" />{copied === "failed" ? "コピーできませんでした" : "表をコピー（CSV）"}</>}
      </button>
    </div>
  );
}

function EvidencePanel({ evidence, onClose, onOpenReview }: { evidence: Evidence; onClose: () => void; onOpenReview: (jobId: string, field?: string | null) => void }) {
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [evidence]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  const note = statusNote(evidence.status);
  return (
    <aside className="ask-evidence-panel" aria-label="根拠の欄">
      <div className="ask-panel-head">
        <h2>根拠の欄</h2>
        <button type="button" className="ask-icon-button" onClick={onClose} aria-label="根拠を閉じる"><FiX aria-hidden="true" /></button>
      </div>
      <p className="ask-evidence-title"><strong>{evidence.label}</strong><small>{evidence.documentName ?? "（名称なし）"}</small></p>
      {evidence.cropUrl && !imageFailed
        ? <figure className="ask-evidence-crop"><img src={evidence.cropUrl} alt={`${evidence.label}の切り出し画像`} onError={() => setImageFailed(true)} /></figure>
        : <p className="ask-panel-lead">{evidence.cropUrl ? "切り出し画像を表示できません。" : "この欄には切り出し画像がありません。"}</p>}
      <dl className="ask-evidence-values">
        <div><dt>確定値</dt><dd>{evidence.value ?? "（空欄）"}</dd></div>
        {evidence.raw !== evidence.value && <div><dt>読み取り時</dt><dd>{evidence.raw ?? "—"}</dd></div>}
        {evidence.interpretedAs && evidence.interpretedAs !== evidence.value && <div><dt>計算での読み方</dt><dd>{evidence.interpretedAs}</dd></div>}
        {note && <div><dt>状態</dt><dd>{note}</dd></div>}
      </dl>
      <button type="button" className="primary compact" onClick={() => onOpenReview(evidence.jobId, evidence.targetPath)}><FiExternalLink aria-hidden="true" />書類でこの欄を確認・修正</button>
      <a className="ask-evidence-link" href={evidenceHref(evidence)} target="_blank" rel="noopener">新しいタブで開く</a>
    </aside>
  );
}
