import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { FiCheck, FiCopy, FiExternalLink, FiFilter, FiHelpCircle, FiMic, FiMicOff, FiSearch, FiSend, FiX } from "react-icons/fi";
import { EditingError } from "../editing/client";
import {
  evidenceHref,
  structuredClient,
  tableCsv,
  type AnswerTable,
  type DocumentSummary,
  type Evidence,
  type FieldName,
  type QuestionResult,
  type StructuredStatus,
} from "../structured/client";
import { QuestionVoiceCapture } from "../structured/voiceCapture";

type ScopeItem = { jobId: string; label: string };
type Turn = { id: number; text: string; scope: ScopeItem[]; result: QuestionResult | null; error: string | null; pending: boolean };

/** Same limit as the questions API (`documentIds` max 20). */
const MAX_SCOPE = 20;

export function AskPage({ onOpenReview }: { onOpenReview: (jobId: string) => void }) {
  const [status, setStatus] = useState<StructuredStatus | null | "unavailable">(null);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [scope, setScope] = useState<string[]>([]);
  const [fieldNames, setFieldNames] = useState<FieldName[]>([]);
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [shownEvidence, setShownEvidence] = useState<Evidence | null>(null);
  const [scopeOpen, setScopeOpen] = useState(false);
  const nextId = useRef(1);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sendRef = useRef<(question: string) => void>(() => undefined);
  // One capture per page visit; a completed transcript is sent exactly like typed text.
  const [voice] = useState(() => new QuestionVoiceCapture((spoken) => sendRef.current(spoken)));
  const voiceState = useSyncExternalStore(voice.subscribe, voice.getSnapshot);
  useEffect(() => () => { void voice.stop(); }, [voice]);

  const enabled = status !== null && status !== "unavailable" && status.enabled;

  const loadDocuments = useCallback(async () => {
    try {
      const page = await structuredClient.documents();
      setDocuments(page.items);
      setDocumentsError(null);
    } catch (error) {
      setDocumentsError(error instanceof EditingError ? error.message : "確定済みの書類を取得できませんでした。");
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    void structuredClient.status()
      .then((value) => { if (!stopped) setStatus(value); })
      .catch(() => { if (!stopped) setStatus("unavailable"); });
    return () => { stopped = true; };
  }, []);
  useEffect(() => { if (enabled) void loadDocuments(); }, [enabled, loadDocuments]);
  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: "end" });
  }, [turns]);

  const templates = useMemo(() => {
    const byId = new Map<string, string>();
    for (const document of documents) if (!byId.has(document.templateId)) byId.set(document.templateId, document.templateName);
    return [...byId].map(([id, name]) => ({ id, name }));
  }, [documents]);
  const byJob = useMemo(() => new Map(documents.map((document) => [document.jobId, document])), [documents]);
  // A document dropped from the confirmed list (e.g. reopened) silently leaves the scope.
  const scopeItems: ScopeItem[] = scope.flatMap((jobId) => {
    const document = byJob.get(jobId);
    return document ? [{ jobId, label: documentLabel(document) }] : [];
  });
  const visible = useMemo(() => {
    const needle = fold(filter.trim());
    return documents.filter((document) =>
      (!templateId || document.templateId === templateId)
      && (!needle || [document.documentName, ...document.identity.map((item) => item.value)].some((value) => value && fold(value).includes(needle))));
  }, [documents, filter, templateId]);
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

  const busy = turns.some((turn) => turn.pending);
  const send = async (question: string, replyTo?: { questionId: string; value: string }, shownText?: string) => {
    const id = nextId.current++;
    const asked = scopeItems;
    setTurns((current) => [...current, { id, text: shownText ?? question, scope: asked, result: null, error: null, pending: true }]);
    try {
      const documentIds = asked.length > 0 ? asked.map((item) => item.jobId) : undefined;
      const result = await structuredClient.ask({
        text: question,
        ...(replyTo ? { replyTo } : {}),
        ...(documentIds ? { documentIds } : {}),
      });
      setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, result, pending: false } : turn));
    } catch (error) {
      const message = error instanceof EditingError ? error.message : "質問を処理できませんでした。";
      setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, error: message, pending: false } : turn));
    }
  };
  const submit = () => {
    const question = text.trim();
    if (!question || busy) return;
    setText("");
    void send(question);
  };
  sendRef.current = (spoken) => { void send(spoken); };
  const toggleScope = (jobId: string) => {
    setScope((current) => current.includes(jobId)
      ? current.filter((id) => id !== jobId)
      : current.length >= MAX_SCOPE ? current : [...current, jobId]);
  };
  const insertField = (name: FieldName) => {
    setText((current) => current + (name.kind === "column" && name.tableTitle ? `${name.tableTitle}の${name.label}` : name.label));
    inputRef.current?.focus();
  };
  const qaConnected = enabled && status.qaConnected;
  const voiceConnected = enabled && status.voiceConnected;
  const listening = voiceState.mode !== "stopped";
  const latestId = turns.length > 0 ? turns[turns.length - 1].id : null;

  return (
    <div className="page-stack ask-workspace">
      <section className="hero ask-hero">
        <div>
          <span className="eyebrow">確定済みデータへの質問</span>
          <h1>確定した帳票の内容を、<em>そのまま質問</em>できます。</h1>
          <p className="hero-copy">対象の書類を選ぶか、質問に書類名・識別情報（名前・番号）を含めてください。該当する欄や表から値・経過期間・合計などを求め、根拠の欄を画像で確認できます。</p>
        </div>
      </section>

      {status === "unavailable" && (
        <aside className="notice danger"><strong>接続を確認できません</strong><span>バックエンドへ接続できるまで質問は送れません。</span></aside>
      )}
      {status !== null && status !== "unavailable" && !status.enabled && (
        <aside className="notice danger"><strong>この環境では質問応答が無効です</strong><span>STRUCTURED_DATA_ENABLED が有効な環境で利用できます。</span></aside>
      )}
      {enabled && !qaConnected && (
        <aside className="notice"><strong>AIが未接続です</strong><span>書類の一覧は使えますが、欄や処理の選択にはAI接続が必要です。</span></aside>
      )}

      <div className={shownEvidence ? "ask-layout with-evidence" : "ask-layout"}>
        <aside className={scopeOpen ? "ask-scope open" : "ask-scope"} aria-label="対象の書類">
          <div className="section-title">
            <h2>対象の書類</h2>
            <button type="button" className="ask-icon-button ask-scope-close" onClick={() => setScopeOpen(false)} aria-label="対象の選択を閉じる"><FiX aria-hidden="true" /></button>
          </div>
          <label className="ask-template">
            <span>テンプレート</span>
            <select value={templateId} disabled={!enabled} onChange={(event) => setTemplateId(event.target.value)}>
              <option value="">すべて</option>
              {templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
            </select>
          </label>
          <label className="ask-filter">
            <FiSearch aria-hidden="true" />
            <input type="search" value={filter} placeholder="書類名・識別情報で絞り込み" disabled={!enabled} onChange={(event) => setFilter(event.target.value)} />
          </label>
          {documentsError && <p className="ask-error" role="alert">{documentsError}</p>}
          {enabled && !documentsError && visible.length === 0 && <p className="section-lead">該当する確定済みの書類はありません。</p>}
          <ul className="ask-document-list">
            {visible.map((document) => {
              const checked = scope.includes(document.jobId);
              return (
                <li key={document.jobId}>
                  <label className={checked ? "ask-document selected" : "ask-document"}>
                    <input type="checkbox" checked={checked} disabled={!checked && scope.length >= MAX_SCOPE} onChange={() => toggleScope(document.jobId)} />
                    <span>
                      <strong>{document.documentName ?? "（名称なし）"}</strong>
                      <small>{document.templateName} · {new Date(document.confirmedAt).toLocaleDateString("ja-JP")}{document.seriesVersion > 1 ? ` · 版${document.seriesVersion}` : ""}</small>
                      {document.identity.some((item) => item.value) && (
                        <small>{document.identity.filter((item) => item.value).map((item) => `${item.label}=${item.value}`).join("、")}</small>
                      )}
                    </span>
                  </label>
                  <button type="button" className="ask-open" onClick={() => onOpenReview(document.jobId)} aria-label={`${document.documentName ?? "書類"}を開く`}>
                    <FiExternalLink aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
          {fieldNames.length > 0 && (
            <div className="ask-fields">
              <h3>聞ける欄</h3>
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

        <section className="ask-conversation" aria-label="質問と回答">
          <div className="ask-turns" ref={listRef} role="log" aria-live="polite">
            {turns.length === 0 && (
              <p className="section-lead">例：「母牛こよみの子牛は今何か月？」「CHK-001の受付番号は？」「こよみの価格の合計は？」</p>
            )}
            {turns.map((turn) => (
              <TurnView
                key={turn.id}
                turn={turn}
                canReply={turn.id === latestId && !busy}
                shownEvidence={shownEvidence}
                onReply={(question, choice) => void send("", { questionId: question, value: choice.value }, `→ ${choice.label}`)}
                onShowEvidence={setShownEvidence}
                onOpenReview={onOpenReview}
              />
            ))}
          </div>
          <form className="ask-form" onSubmit={(event) => { event.preventDefault(); submit(); }}>
            <div className="ask-scope-bar">
              <button type="button" className="ask-scope-toggle" onClick={() => setScopeOpen(true)}><FiFilter aria-hidden="true" />対象を選ぶ</button>
              {scopeItems.length === 0
                ? <span className="ask-scope-hint">対象: すべての確定済み書類（質問の書類名・識別情報で特定）</span>
                : (
                  <>
                    <span className="ask-scope-hint">対象:</span>
                    {scopeItems.map((item) => (
                      <span key={item.jobId} className="ask-scope-chip">
                        {item.label}
                        <button type="button" onClick={() => toggleScope(item.jobId)} aria-label={`${item.label}を対象から外す`}><FiX aria-hidden="true" /></button>
                      </span>
                    ))}
                    {scopeItems.length > 1 && <button type="button" className="link-button" onClick={() => setScope([])}>すべて外す</button>}
                  </>
                )}
            </div>
            <label className="sr-only" htmlFor="ask-input">質問</label>
            <textarea
              id="ask-input"
              ref={inputRef}
              value={text}
              rows={2}
              placeholder="質問を入力（Ctrl+Enterで送信）"
              disabled={!enabled || busy}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); } }}
            />
            <button type="submit" className="primary" disabled={!enabled || busy || !text.trim()}>
              <FiSend aria-hidden="true" />{busy ? "問い合わせ中…" : "質問する"}
            </button>
            {voiceConnected && (
              <button
                type="button"
                className={listening ? "secondary ask-mic listening" : "secondary ask-mic"}
                aria-pressed={listening}
                onClick={() => { if (listening) void voice.stop(); else void voice.start(); }}
              >
                {listening ? <FiMicOff aria-hidden="true" /> : <FiMic aria-hidden="true" />}
                {listening ? "音声を止める" : "音声で質問"}
              </button>
            )}
          </form>
          {(voiceState.message || voiceState.caption) && (
            <p className="ask-voice-status" role="status" aria-live="polite">
              {voiceState.caption ? <span className="ask-caption">{voiceState.caption}</span> : <span>{voiceState.message}</span>}
            </p>
          )}
        </section>

        {shownEvidence && (
          <EvidencePanel evidence={shownEvidence} onClose={() => setShownEvidence(null)} onOpenReview={onOpenReview} />
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

function statusNote(status: string): string | null {
  if (status === "uncertain") return "読み取り未確認";
  if (status === "blank") return "空欄";
  return null;
}

function TurnView({ turn, canReply, shownEvidence, onReply, onShowEvidence, onOpenReview }: {
  turn: Turn;
  canReply: boolean;
  shownEvidence: Evidence | null;
  onReply: (questionId: string, choice: { value: string; label: string }) => void;
  onShowEvidence: (evidence: Evidence) => void;
  onOpenReview: (jobId: string) => void;
}) {
  const result = turn.result;
  return (
    <article className="ask-turn">
      <p className="ask-question"><FiHelpCircle aria-hidden="true" />{turn.text}</p>
      {turn.scope.length > 0 && <p className="ask-turn-scope">対象: {turn.scope.map((item) => item.label).join("、")}</p>}
      {turn.pending && <p className="ask-pending" role="status">問い合わせ中…</p>}
      {turn.error && <p className="ask-error" role="alert">{turn.error}</p>}
      {result && (
        <div className={`ask-answer ask-${result.state}`}>
          <p>{result.answer}</p>
          {result.question && canReply && (
            <div className="ask-choices" role="group" aria-label={result.question.message}>
              {result.question.choices.map((choice) => (
                <button key={choice.value} type="button" className="secondary compact" onClick={() => onReply(result.question!.questionId, choice)}>
                  {choice.label}
                </button>
              ))}
            </div>
          )}
          {result.table && result.table.rows.length > 0 && <AnswerTableView table={result.table} />}
          {result.steps.length > 0 && (
            <dl className="ask-steps" aria-label="計算の過程">
              {result.steps.map((step, index) => (
                <div key={index}><dt>{step.label}</dt><dd>{step.value}</dd></div>
              ))}
            </dl>
          )}
          {result.caveats.length > 0 && (
            <ul className="ask-caveats">{result.caveats.map((caveat, index) => <li key={index}>{caveat}</li>)}</ul>
          )}
          {result.evidence.length > 0 && (
            <div className="ask-evidence-chips" aria-label={`根拠（${result.evidence.length}件）`}>
              <span>根拠</span>
              {result.evidence.map((item, index) => {
                const note = statusNote(item.status);
                const active = shownEvidence !== null && shownEvidence.jobId === item.jobId && shownEvidence.fieldId === item.fieldId && shownEvidence.label === item.label;
                return (
                  <button key={index} type="button" className={`ask-chip${note ? " warn" : ""}${active ? " active" : ""}`} onClick={() => onShowEvidence(item)} aria-pressed={active}>
                    {item.label}{note && <small>{note}</small>}
                  </button>
                );
              })}
            </div>
          )}
          <footer>
            {result.documents.map((document) => (
              <button key={document.jobId} type="button" className="link-button" onClick={() => onOpenReview(document.jobId)}>
                書類「{document.documentName ?? document.jobId}」を開く
              </button>
            ))}
            <small>AI呼出し {result.modelCalls}回</small>
          </footer>
        </div>
      )}
    </article>
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
      <button type="button" className="ghost compact ask-copy" onClick={() => void copy()}>
        {copied === "done" ? <><FiCheck aria-hidden="true" />コピーしました</> : <><FiCopy aria-hidden="true" />{copied === "failed" ? "コピーできませんでした" : "CSVでコピー"}</>}
      </button>
    </div>
  );
}

function EvidencePanel({ evidence, onClose, onOpenReview }: { evidence: Evidence; onClose: () => void; onOpenReview: (jobId: string) => void }) {
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [evidence]);
  const note = statusNote(evidence.status);
  return (
    <aside className="ask-evidence-panel" aria-label="根拠の欄">
      <div className="section-title">
        <h2>根拠</h2>
        <button type="button" className="ask-icon-button" onClick={onClose} aria-label="根拠を閉じる"><FiX aria-hidden="true" /></button>
      </div>
      <p className="ask-evidence-title"><strong>{evidence.label}</strong><small>{evidence.documentName ?? evidence.jobId}</small></p>
      {evidence.cropUrl && !imageFailed
        ? <figure className="ask-evidence-crop"><img src={evidence.cropUrl} alt={`${evidence.label}の切り出し画像`} onError={() => setImageFailed(true)} /></figure>
        : <p className="section-lead">{evidence.cropUrl ? "切り出し画像を表示できません。" : "この欄には切り出し画像がありません。"}</p>}
      <dl className="ask-steps">
        <div><dt>読み取り</dt><dd>{evidence.raw ?? "—"}</dd></div>
        {evidence.value !== evidence.raw && <div><dt>確定値</dt><dd>{evidence.value ?? "—"}</dd></div>}
        {evidence.interpretedAs && evidence.interpretedAs !== evidence.value && <div><dt>解釈</dt><dd>{evidence.interpretedAs}</dd></div>}
        {note && <div><dt>状態</dt><dd>{note}</dd></div>}
      </dl>
      <button type="button" className="secondary" onClick={() => onOpenReview(evidence.jobId)}><FiExternalLink aria-hidden="true" />書類を開いて確認・修正</button>
      <a className="ask-evidence-link" href={evidenceHref(evidence)} target="_blank" rel="noopener">この欄を新しいタブで開く</a>
    </aside>
  );
}
