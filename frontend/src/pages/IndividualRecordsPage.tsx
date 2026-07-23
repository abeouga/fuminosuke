import { useState } from "react";
import { ApiError, api, individualRecordSearchUrl } from "../api";
import type {
  FieldReading,
  IndividualRecord,
  IndividualRecordSearch,
} from "../types";

export function IndividualRecordsPage() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<IndividualRecordSearch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setCopied(null);
    try {
      setResult(await api.searchIndividualRecords(query));
    } catch (reason) {
      setResult(null);
      setError(
        reason instanceof ApiError ? reason.message : "個体データを検索できませんでした。",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-stack records-page">
      <header className="page-header">
        <span className="eyebrow">INDIVIDUAL JSON RECORDS</span>
        <h1>個体識別番号からJSONを検索</h1>
        <p>
          OCR後に確認・確定した帳票を個体識別番号で検索し、紐づくデータ全体を取得します。
        </p>
      </header>

      <form className="record-search" onSubmit={search}>
        <label htmlFor="individual-id-search">個体識別番号</label>
        <div>
          <input
            id="individual-id-search"
            inputMode="numeric"
            autoComplete="off"
            maxLength={30}
            placeholder="例: 123-4567-890"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="primary" disabled={busy || !query.trim()} type="submit">
            {busy ? "検索中…" : "JSONを検索"}
          </button>
        </div>
        <small>ハイフンや空白は自動的に除き、10桁の番号として検索します。</small>
      </form>

      {error && <p className="error-box">{error}</p>}

      {result && (
        <section className="record-results" aria-live="polite">
          <div className="record-result-header">
            <div>
              <span className="eyebrow">SEARCH RESULT</span>
              <h2>{formatIndividualId(result.individualId)}</h2>
              <p>{result.count}件の確定済み帳票が見つかりました。</p>
            </div>
            <a
              className="secondary button-link"
              href={individualRecordSearchUrl(result.individualId)}
              target="_blank"
              rel="noreferrer"
            >
              APIのJSONを開く
            </a>
          </div>

          {result.records.length === 0 ? (
            <div className="empty-state large">
              <span>個</span>
              <h2>登録されたJSONがありません</h2>
              <p>OCR結果を確認して「内容を確定」すると、ここから検索できます。</p>
            </div>
          ) : (
            <div className="record-list">
              {result.records.map((record, index) => (
                <RecordCard
                  key={record.recordId}
                  record={record}
                  defaultOpen={index === 0}
                  copied={copied === record.recordId}
                  onCopy={async () => {
                    try {
                      await navigator.clipboard.writeText(
                        JSON.stringify(record.data, null, 2),
                      );
                      setCopied(record.recordId);
                    } catch {
                      setError(
                        "JSONをコピーできませんでした。表示されたJSONを選択してコピーしてください。",
                      );
                    }
                  }}
                />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function RecordCard({
  record,
  defaultOpen,
  copied,
  onCopy,
}: {
  record: IndividualRecord;
  defaultOpen: boolean;
  copied: boolean;
  onCopy: () => Promise<void>;
}) {
  const identity = record.data.document;
  return (
    <article className="record-card">
      <div className="record-card-head">
        <div>
          <strong>{readingValue(identity.cattleName) || "牛名未入力"}</strong>
          <span>{new Date(record.confirmedAt).toLocaleString("ja-JP")} に確定</span>
        </div>
        <span className="status-pill status-confirmed">CONFIRMED</span>
      </div>
      <dl className="record-summary-grid">
        <div><dt>個体識別番号</dt><dd>{formatIndividualId(record.individualId)}</dd></div>
        <div><dt>生年月日</dt><dd>{readingValue(identity.birthDate) || "—"}</dd></div>
        <div><dt>登録番号</dt><dd>{readingValue(identity.registrationNumber) || "—"}</dd></div>
        <div><dt>売却子牛</dt><dd>{record.data.saleCalves.length}件</dd></div>
        <div><dt>分娩履歴</dt><dd>{record.data.calvings.length}件</dd></div>
        <div><dt>元ジョブ</dt><dd className="mono">{record.sourceJobId}</dd></div>
      </dl>
      <details open={defaultOpen}>
        <summary>保存されているJSON全体</summary>
        <div className="json-toolbar">
          <span>OCR revision {record.ocrResultRevision}</span>
          <button className="ghost compact" type="button" onClick={() => void onCopy()}>
            {copied ? "コピーしました" : "JSONをコピー"}
          </button>
        </div>
        <pre className="json-viewer">{JSON.stringify(record.data, null, 2)}</pre>
      </details>
    </article>
  );
}

function readingValue(reading?: FieldReading) {
  return reading?.normalized ?? reading?.raw ?? "";
}

function formatIndividualId(value: string) {
  return value.length === 10
    ? `${value.slice(0, 3)}-${value.slice(3, 7)}-${value.slice(7)}`
    : value;
}
