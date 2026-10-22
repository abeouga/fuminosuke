import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  FiAlertTriangle,
  FiArrowUp,
  FiCheck,
  FiChevronDown,
  FiCopy,
  FiExternalLink,
  FiImage,
  FiInfo,
  FiMessageSquare,
  FiMic,
  FiMicOff,
  FiPlus,
  FiRefreshCw,
  FiX,
} from "react-icons/fi";
import { askStore, openQuestion, type Turn } from "../structured/askStore";
import { chatReadings, useChatReadings, type ChatReading } from "../structured/chatReadings";
import { useReadingQueue } from "../readingQueue";
import { Spinner } from "../components/Spinner";
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

// Last known status and documents, so coming back to the page shows them at once (refreshed on mount).
let cachedStatus: StructuredStatus | null = null;
let cachedDocuments: DocumentSummary[] | null = null;

export function AskPage({ onOpenReview }: { onOpenReview: (jobId: string, field?: string | null) => void }) {
  const state = useSyncExternalStore(askStore.subscribe, askStore.getSnapshot);
  const { turns, draft, replyMode, evidence } = state;
  const [status, setStatus] = useState<StructuredStatus | null | "unavailable">(cachedStatus);
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(cachedDocuments);
  const [suggestionFields, setSuggestionFields] = useState<Record<string, FieldName[]>>({});
  const pageRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const readings = useChatReadings();
  const [dragging, setDragging] = useState(false);
  // One capture per page visit; a completed transcript is sent exactly like typed text.
  // Spoken text goes into the input box: the user checks it and sends it with Enter.
  const [voice] = useState(() => new QuestionVoiceCapture((spoken) => {
    askStore.set((current) => ({ draft: current.draft.trim() ? `${current.draft.trimEnd()} ${spoken}` : spoken }));
    inputRef.current?.focus();
  }));
  const voiceState = useSyncExternalStore(voice.subscribe, voice.getSnapshot);
  useEffect(() => () => { void voice.stop(); }, [voice]);

  const enabled = status !== null && status !== "unavailable" && status.enabled;

  useEffect(() => {
    let stopped = false;
    void structuredClient.status()
      .then((value) => { cachedStatus = value; if (!stopped) setStatus(value); })
      .catch(() => { if (!stopped) setStatus("unavailable"); });
    return () => { stopped = true; };
  }, []);
  // Documents are only used for the example questions; the chat always asks across all confirmed documents.
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    void structuredClient.documents()
      .then((page) => { cachedDocuments = page.items; if (!stopped) setDocuments(page.items); })
      .catch(() => undefined);
    return () => { stopped = true; };
  }, [enabled]);

  // Example questions come from the user's own documents and field names, never a fixed form.
  const exampleDocuments = useMemo(() => (documents ?? []).slice(0, 2), [documents]);
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
    else askStore.ask(text, []);
  };
  // Images go to the template guess and then the reading queue; the review screen is never opened for them.
  const addImages = (list: FileList | File[] | null | undefined) => {
    if (!list) return false;
    return chatReadings.add([...list], turns[turns.length - 1]?.id ?? null) > 0;
  };
  // Keep the newest message in view inside the conversation (the page itself does not jump).
  const last = turns[turns.length - 1];
  const scrollKey = `${turns.length}:${last?.pending ? 1 : 0}:${last?.result ? 1 : 0}:${last?.error ? 1 : 0}:${readings.map((reading) => reading.state[0]).join("")}`;
  const scrolledOnce = useRef(false);
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    // Coming back to the page jumps straight to the latest message; new messages glide in.
    // A finished answer is shown from its question down, so a long answer is read from the top.
    const bottom = list.scrollHeight - list.clientHeight;
    const asked = [...list.querySelectorAll<HTMLElement>(".ask-msg.user:not(.image)")].pop();
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
  const placeholder = !enabled ? "チャットは利用できません"
    : answering ? "選択肢にない場合は、ここに書いて送ってください"
    : "メッセージを入力";
  const turnIds = new Set(turns.map((turn) => turn.id));
  const readingViews = (turnId: number | null) => readings
    .filter((reading) => turnId === null
      ? reading.afterTurnId === null || !turnIds.has(reading.afterTurnId)
      : reading.afterTurnId === turnId)
    .map((reading) => <ReadingView key={reading.id} reading={reading} onOpenReview={onOpenReview} />);

  return (
    <div className="ask-page" ref={pageRef}>
      <header className="ask-header">
        <h1>チャット</h1>
        {(turns.length > 0 || readings.length > 0) && (
          <div className="ask-header-actions">
            <button type="button" className="secondary compact" disabled={busy} onClick={() => { askStore.clear(); chatReadings.clear(); }}>
              <FiPlus aria-hidden="true" />新しい会話
            </button>
          </div>
        )}
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
        <section
          className={dragging ? "ask-chat dropping" : "ask-chat"}
          aria-label="チャット"
          onDragOver={(event) => {
            if (![...event.dataTransfer.types].includes("Files")) return;
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
          onDrop={(event) => {
            if (![...event.dataTransfer.types].includes("Files")) return;
            event.preventDefault();
            setDragging(false);
            addImages(event.dataTransfer.files);
          }}
        >
          {dragging && <div className="ask-drop-hint" aria-hidden="true"><FiImage />画像を置くと読み取ります</div>}
          <div className="ask-messages" ref={listRef} role="log" aria-live="polite">
            {turns.length === 0 && readings.length === 0
              ? (
                <div className="ask-welcome">
                  <span className="ask-welcome-icon" aria-hidden="true"><FiMessageSquare /></span>
                  <h2>何を調べますか？</h2>
                  <p className="ask-welcome-hint">記入した書類の画像を置くと、テンプレートを見分けて読み取ります。</p>
                  {suggestions.length > 0 && (
                    <div className="ask-suggestions">
                      {suggestions.map((suggestion) => (
                        <button key={suggestion} type="button" disabled={!enabled || busy} onClick={() => submit(suggestion)}>{suggestion}</button>
                      ))}
                    </div>
                  )}
                </div>
              )
              : [readingViews(null), ...turns.map((turn) => [
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
                />,
                ...readingViews(turn.id),
              ])]}
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
                onPaste={(event) => {
                  if (addImages(event.clipboardData.files)) event.preventDefault();
                }}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
                  event.preventDefault();
                  submit(draft);
                }}
              />
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                multiple
                hidden
                onChange={(event) => { addImages(event.target.files); event.target.value = ""; }}
              />
              <button
                type="button"
                className="ask-round-button ask-attach"
                aria-label="書類の画像を読み取る"
                title="書類の画像を読み取る"
                onClick={() => fileRef.current?.click()}
              >
                <FiImage aria-hidden="true" />
              </button>
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
            {(voiceState.caption || voiceState.message) && (
              <p className="ask-composer-foot" role="status" aria-live="polite">
                {voiceState.caption ? <span className="ask-caption">{voiceState.caption}</span> : <span>{voiceState.message}</span>}
              </p>
            )}
          </form>
        </section>

        {evidence && (
          <EvidencePanel evidence={evidence} onClose={() => askStore.set({ evidence: null })} onOpenReview={onOpenReview} />
        )}
      </div>
    </div>
  );
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

function ReadingView({ reading, onOpenReview }: { reading: ChatReading; onOpenReview: (jobId: string) => void }) {
  const queue = useReadingQueue();
  const queued = reading.jobId ? queue.find((item) => item.jobId === reading.jobId) ?? null : null;
  const preview = chatReadings.preview(reading.id);
  const hasFile = chatReadings.hasFile(reading.id);
  const failed = reading.state === "failed" || (queued !== null && ["FAILED", "INTERRUPTED"].includes(queued.status));
  return (
    <>
      <div className="ask-msg user image">
        {preview
          ? <img className="ask-image" src={preview} alt={reading.fileName} />
          : <div className="ask-bubble"><FiImage aria-hidden="true" /> {reading.fileName}</div>}
      </div>
      <div className={`ask-msg bot reading${reading.state === "choosing" ? " clarifying" : ""}${failed ? " failed" : ""}`}>
        <span className="ask-avatar" aria-hidden="true">AI</span>
        <div className="ask-bubble">
          {reading.state === "classifying" && <p className="ask-reading-status" role="status"><Spinner />どのテンプレートの書類か確かめています…</p>}
          {reading.state === "choosing" && (
            <>
              <p className="ask-answer">
                {reading.candidates.length === 0
                  ? "使用中のテンプレートと一致しませんでした。この書類のテンプレートを作成してから、もう一度送ってください。"
                  : reading.matched ? "どのテンプレートで読み取りますか？"
                  : "はっきり一致するテンプレートがありませんでした。近いものを選ぶと読み取ります。"}
              </p>
              {!hasFile && reading.candidates.length > 0 && <p className="ask-reading-note">画面を再読み込みしたため、画像をもう一度送ってください。</p>}
              {reading.candidates.length > 0 && (
                <div className="ask-template-cards" role="group" aria-label="テンプレートを選ぶ">
                  {reading.candidates.map((candidate) => (
                    <button
                      key={candidate.templateVersionId}
                      type="button"
                      className="ask-template-card"
                      disabled={!hasFile}
                      onClick={() => chatReadings.choose(reading.id, candidate)}
                    >
                      <img src={`${candidate.thumbnailUrl}?width=240`} alt="" loading="lazy" />
                      <span>{candidate.templateName}</span>
                    </button>
                  ))}
                </div>
              )}
              <div><button type="button" className="link-button" onClick={() => chatReadings.dismiss(reading.id)}>{reading.candidates.length === 0 ? "閉じる" : "読み取らない"}</button></div>
            </>
          )}
          {reading.state === "starting" && <p className="ask-reading-status" role="status"><Spinner />「{reading.templateName}」で読み取りを始めています…</p>}
          {reading.state === "queued" && (
            <QueuedStatus
              reading={reading}
              status={queued?.status ?? "QUEUED"}
              progress={queued?.progressPercent ?? 0}
              error={queued?.errorMessage ?? null}
              onOpenReview={onOpenReview}
            />
          )}
          {reading.state === "failed" && (
            <div className="ask-failed">
              <p role="alert"><FiAlertTriangle aria-hidden="true" />{reading.error}</p>
              <div className="ask-reading-actions">
                {hasFile && (
                  <button type="button" className="secondary compact" onClick={() => chatReadings.retry(reading.id)}>
                    <FiRefreshCw aria-hidden="true" />もう一度試す
                  </button>
                )}
                <button type="button" className="link-button" onClick={() => chatReadings.dismiss(reading.id)}>閉じる</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/** A reading sent to the queue: its progress, then a link to review it (never opened automatically). */
function QueuedStatus({ reading, status, progress, error, onOpenReview }: {
  reading: ChatReading;
  status: string;
  progress: number;
  error: string | null;
  onOpenReview: (jobId: string) => void;
}) {
  const name = `「${reading.templateName}」`;
  if (status === "REVIEW_REQUIRED" || status === "CONFIRMED") {
    return (
      <div className="ask-reading-done">
        <p className="ask-answer">
          <FiCheck aria-hidden="true" />
          {status === "CONFIRMED" ? `${name}で読み取り、確定済みです。` : `${name}で読み取りました。右上の「読み取り状況」から確認できます。`}
        </p>
        {reading.jobId && (
          <button type="button" className="secondary compact" onClick={() => onOpenReview(reading.jobId!)}>
            <FiExternalLink aria-hidden="true" />{status === "CONFIRMED" ? "結果を開く" : "確認する"}
          </button>
        )}
      </div>
    );
  }
  if (status === "FAILED" || status === "INTERRUPTED" || status === "CANCELLED") {
    return <p role="alert" className="ask-reading-error"><FiAlertTriangle aria-hidden="true" />{error ?? `${name}で読み取れませんでした。`}</p>;
  }
  return (
    <p className="ask-reading-status" role="status">
      <Spinner />{name}で読み取っています{progress > 0 ? `（${progress}%）` : "（順番待ち）"}。終わると右上の「読み取り状況」に入ります。
    </p>
  );
}
