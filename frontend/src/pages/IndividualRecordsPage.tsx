import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type {
  FieldReading,
  IndividualRecordCatalog,
  IndividualRecord,
  IndividualRecordSearch,
  SaleCalfSireSearch,
} from "../types";

export function IndividualRecordsPage() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<IndividualRecordSearch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<IndividualRecordCatalog | null>(null);
  const [catalogBusy, setCatalogBusy] = useState(true);
  const [sireQuery, setSireQuery] = useState("");
  const [sireResult, setSireResult] = useState<SaleCalfSireSearch | null>(null);
  const [sireBusy, setSireBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void api.individualRecordCatalog()
      .then((next) => {
        if (!cancelled) setCatalog(next);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(
            reason instanceof ApiError ? reason.message : "個体一覧を取得できませんでした。",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setCatalogBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const showIndividual = async (individualId: string) => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.searchIndividualRecords(individualId));
    } catch (reason) {
      setResult(null);
      setError(
        reason instanceof ApiError ? reason.message : "個体データを検索できませんでした。",
      );
    } finally {
      setBusy(false);
    }
  };
  const search = (event: React.FormEvent) => {
    event.preventDefault();
    void showIndividual(query);
  };

  const searchSire = async (event: React.FormEvent) => {
    event.preventDefault();
    setSireBusy(true);
    setError(null);
    try {
      setSireResult(await api.searchSaleCalvesBySire(sireQuery));
    } catch (reason) {
      setSireResult(null);
      setError(
        reason instanceof ApiError ? reason.message : "種雄牛名から検索できませんでした。",
      );
    } finally {
      setSireBusy(false);
    }
  };

  return (
    <div className="page-stack records-page">
      <header className="page-header">
        <span className="eyebrow">INDIVIDUAL RECORDS</span>
        <h1>確定済みレコードを検索</h1>
        <p>
          個体識別番号から帳票全体を、種雄牛名から売却子牛の産次・価格を検索できます。
        </p>
      </header>

      <section className="record-catalog" aria-labelledby="individual-catalog-title">
        <div className="record-catalog-head">
          <div>
            <span className="eyebrow">REGISTERED INDIVIDUALS</span>
            <h2 id="individual-catalog-title">登録済み個体から選ぶ</h2>
            <p>確定保存された個体を選ぶと、その台帳を表示します。</p>
          </div>
          {catalog && <strong>{catalog.count}頭</strong>}
        </div>
        {catalogBusy ? (
          <p className="record-catalog-message">個体一覧を読み込んでいます…</p>
        ) : !catalog || catalog.items.length === 0 ? (
          <p className="record-catalog-message">確定保存された個体はまだありません。</p>
        ) : (
          <div className="individual-catalog-list">
            {catalog.items.map((item) => (
              <button
                type="button"
                className="individual-catalog-item"
                key={item.individualId}
                disabled={busy}
                onClick={() => {
                  setQuery(item.individualId);
                  void showIndividual(item.individualId);
                }}
              >
                <span className="individual-catalog-name">{item.cattleName || "牛名未入力"}</span>
                <span className="individual-catalog-id">{formatIndividualId(item.individualId)}</span>
                <span className="individual-catalog-meta">
                  台帳 {item.recordCount}件 · {new Date(item.lastConfirmedAt).toLocaleDateString("ja-JP")}
                </span>
                {item.needsReview && <span className="status-pill status-needs-review">要再確認</span>}
                <strong>表示する →</strong>
              </button>
            ))}
          </div>
        )}
      </section>

      <form className="record-search" onSubmit={search}>
        <label htmlFor="individual-id-search">個体識別番号</label>
        <div>
          <input
            id="individual-id-search"
            inputMode="numeric"
            autoComplete="off"
            maxLength={10}
            pattern="[0-9]{10}"
            placeholder="例: 1234567890"
            value={query}
            onChange={(event) => setQuery(limitIndividualId(event.target.value))}
          />
          <button className="primary" disabled={busy || query.length !== 10} type="submit">
            {busy ? "検索中…" : "個体を検索"}
          </button>
        </div>
        <small>数字10桁で入力してください。11桁目以降は入力されません。</small>
      </form>

      <form className="record-search" onSubmit={searchSire}>
        <label htmlFor="sire-name-search">種雄牛名</label>
        <div>
          <input
            id="sire-name-search"
            autoComplete="off"
            maxLength={200}
            placeholder="例: 福之姫"
            value={sireQuery}
            onChange={(event) => setSireQuery(event.target.value)}
          />
          <button className="primary" disabled={sireBusy || !sireQuery.trim()} type="submit">
            {sireBusy ? "検索中…" : "産次・価格を検索"}
          </button>
        </div>
        <small>同じ行レコードに保存された産次、価格、体重などをまとめて返します。</small>
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
          </div>

          {result.records.length === 0 ? (
            <div className="empty-state large">
              <span>個</span>
              <h2>登録された個体データがありません</h2>
              <p>OCR結果を確認して「内容を確定」すると、ここから検索できます。</p>
            </div>
          ) : (
            <div className="record-list">
              {result.records.map((record, index) => (
                <RecordCard
                  key={record.recordId}
                  record={record}
                  defaultOpen={index === 0}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {sireResult && (
        <section className="record-results" aria-live="polite">
          <div className="record-result-header">
            <div>
              <span className="eyebrow">SALE CALF RECORD SEARCH</span>
              <h2>{sireResult.query}</h2>
              <p>{sireResult.count}件の売却子牛レコードが見つかりました。</p>
            </div>
          </div>
          {sireResult.matches.length === 0 ? (
            <div className="empty-state large">
              <span>牛</span>
              <h2>一致する売却子牛レコードがありません</h2>
            </div>
          ) : (
            <div className="sale-calf-match-list">
              {sireResult.matches.map((match) => (
                <article className="record-card sale-calf-match" key={match.recordFieldId}>
                  <div className="record-card-head">
                    <div>
                      <strong>{match.sectionTitle} / {readingValue(match.data.parity) || match.recordKey || match.recordOrder}産次</strong>
                      <span>個体識別番号 {match.individualId ? formatIndividualId(match.individualId) : "未設定"}</span>
                    </div>
                    <span className={`status-pill ${match.reviewWarnings?.length ? "status-needs-review" : "status-confirmed"}`}>
                      {match.reviewWarnings?.length ? "要再確認" : "MATCH"}
                    </span>
                  </div>
                  {match.reviewWarnings?.map((warning) => (
                    <p className="inline-warning" key={warning}>{warning}</p>
                  ))}
                  <dl className="record-summary-grid">
                    <div><dt>種雄牛名</dt><dd>{readingValue(match.data.sireName) || match.matchedValue || "—"}</dd></div>
                    <div><dt>産次</dt><dd>{readingValue(match.data.parity) || match.recordKey || match.recordOrder}</dd></div>
                    <div><dt>価格</dt><dd>{formatPrice(readingValue(match.data.priceYen))}</dd></div>
                    <div><dt>体重</dt><dd>{readingValue(match.data.weightKg) || "—"}</dd></div>
                    <div><dt>レコード順</dt><dd>{match.recordOrder}</dd></div>
                    <div><dt>元ジョブ</dt><dd className="mono">{match.sourceJobId}</dd></div>
                  </dl>
                </article>
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
}: {
  record: IndividualRecord;
  defaultOpen: boolean;
}) {
  const identity = record.data.document;
  const saleCalves = record.data.saleCalves.filter(hasRecordedValue);
  const calvings = record.data.calvings.filter(hasRecordedValue);
  const customFields = Object.entries(record.data.customFields ?? {})
    .filter(([, reading]) => Boolean(readingValue(reading)));
  return (
    <article className="record-card">
      <div className="record-card-head">
        <div>
          <strong>{readingValue(identity.cattleName) || "牛名未入力"}</strong>
          <span>個体識別番号 {formatIndividualId(record.individualId)}</span>
        </div>
        <span className={`status-pill ${record.needsReview ? "status-needs-review" : "status-confirmed"}`}>
          {record.needsReview ? "要再確認" : "CONFIRMED"}
        </span>
      </div>
      {record.reviewWarnings?.map((warning) => (
        <p className="inline-warning" key={warning}>{warning}</p>
      ))}
      <div className="record-overview">
        <div><strong>{saleCalves.length}</strong><span>売却子牛</span></div>
        <div><strong>{calvings.length}</strong><span>繁殖履歴</span></div>
        <div><strong>{customFields.length}</strong><span>追加項目</span></div>
      </div>

      <details className="record-gui-details" open={defaultOpen}>
        <summary>台帳の内容を表示</summary>
        <div className="record-gui-body">
          <RecordSection title="基本情報" subtitle="この繁殖台帳の個体情報">
            <dl className="record-field-grid">
              <RecordField label="個体識別番号" reading={identity.unifiedEarTag} fallback={record.individualId} format={formatIndividualId} />
              <RecordField label="牛名" reading={identity.cattleName} />
              <RecordField label="生年月日" reading={identity.birthDate} />
              <RecordField label="登録番号" reading={identity.registrationNumber} />
              <RecordField label="毛色" reading={identity.coatColor} />
              <RecordField label="繁殖者" reading={identity.breeder} />
              <RecordField label="所有者" reading={identity.owner} />
            </dl>
          </RecordSection>

          <RecordSection title="血統" subtitle="父母・祖父母">
            <div className="pedigree-board">
              <div className="pedigree-person pedigree-father">
                <span>父</span><strong>{readingValue(record.data.pedigree.father) || "—"}</strong>
              </div>
              <div className="pedigree-person">
                <span>父方祖父</span><strong>{readingValue(record.data.pedigree.paternalGrandfather) || "—"}</strong>
              </div>
              <div className="pedigree-person">
                <span>父方祖母</span><strong>{readingValue(record.data.pedigree.paternalGrandmother) || "—"}</strong>
              </div>
              <div className="pedigree-person pedigree-mother">
                <span>母</span><strong>{readingValue(record.data.pedigree.mother) || "—"}</strong>
              </div>
              <div className="pedigree-person">
                <span>母方祖父</span><strong>{readingValue(record.data.pedigree.maternalGrandfather) || "—"}</strong>
              </div>
              <div className="pedigree-person">
                <span>母方祖母</span><strong>{readingValue(record.data.pedigree.maternalGrandmother) || "—"}</strong>
              </div>
            </div>
          </RecordSection>

          <RecordSection title="売却子牛一覧" subtitle={`${saleCalves.length}件の記録`}>
            {saleCalves.length === 0 ? (
              <p className="record-section-empty">記録はありません。</p>
            ) : (
              <div className="record-table-wrap">
                <table className="record-data-table">
                  <thead><tr><th>産次</th><th>日齢</th><th>体重</th><th>価格</th><th>種雄牛名</th><th>売却日</th><th>備考</th></tr></thead>
                  <tbody>{saleCalves.map((calf, index) => (
                    <tr key={`${readingValue(calf.parity)}-${index}`}>
                      <td>{displayReading(calf.parity, String(index + 1))}</td>
                      <td>{formatUnit(readingValue(calf.ageDays), "日")}</td>
                      <td>{formatUnit(readingValue(calf.weightKg), "kg")}</td>
                      <td>{formatPrice(readingValue(calf.priceYen))}</td>
                      <td>{displayReading(calf.sireName)}</td>
                      <td>{displayReading(calf.saleDate)}</td>
                      <td>{displayReading(calf.notes)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </RecordSection>

          <RecordSection title="繁殖履歴" subtitle={`${calvings.length}件の記録`}>
            {calvings.length === 0 ? (
              <p className="record-section-empty">記録はありません。</p>
            ) : (
              <div className="record-table-wrap">
                <table className="record-data-table">
                  <thead><tr><th>産次</th><th>分娩年月日</th><th>性別</th><th>分娩状況・結果</th><th>備考</th></tr></thead>
                  <tbody>{calvings.map((calving, index) => (
                    <tr key={`${readingValue(calving.parity)}-${index}`}>
                      <td>{displayReading(calving.parity, String(index + 1))}</td>
                      <td>{displayReading(calving.calvingDate)}</td>
                      <td>{displayReading(calving.sex)}</td>
                      <td>{displayReading(calving.outcome)}</td>
                      <td>{displayReading(calving.notes)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </RecordSection>

          {customFields.length > 0 && (
            <RecordSection title="追加項目" subtitle="テンプレートで追加された項目">
              <dl className="record-field-grid custom-record-fields">
                {customFields.map(([key, reading]) => (
                  <RecordField key={key} label={humanizeFieldKey(key)} reading={reading} />
                ))}
              </dl>
            </RecordSection>
          )}

          {record.data.reviewItems.length > 0 && (
            <RecordSection title="確認メモ" subtitle="読み取り時に記録された注意事項">
              <ul className="record-review-notes">
                {record.data.reviewItems.map((item, index) => (
                  <li key={`${item.code}-${item.path}-${index}`}>
                    <strong>{item.severity === "error" ? "要確認" : "注意"}</strong>
                    <span>{item.message}</span>
                  </li>
                ))}
              </ul>
            </RecordSection>
          )}

          <footer className="record-provenance">
            <span>{new Date(record.confirmedAt).toLocaleString("ja-JP")} に確定</span>
            <span>OCR revision {record.ocrResultRevision}</span>
            <span>元ジョブ <code>{record.sourceJobId}</code></span>
          </footer>
        </div>
      </details>
    </article>
  );
}

function RecordSection({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <section className="record-gui-section">
      <div className="record-gui-section-head">
        <h3>{title}</h3>
        <span>{subtitle}</span>
      </div>
      {children}
    </section>
  );
}

function RecordField({
  label,
  reading,
  fallback = "",
  format,
}: {
  label: string;
  reading?: FieldReading;
  fallback?: string;
  format?: (value: string) => string;
}) {
  const rawValue = readingValue(reading) || fallback;
  const value = rawValue && format ? format(rawValue) : rawValue;
  return (
    <div className={`record-field-value ${reading?.status === "uncertain" ? "is-uncertain" : ""}`}>
      <dt>{label}</dt>
      <dd>
        <span>{value || "—"}</span>
        {reading?.status === "uncertain" && <em>要確認</em>}
      </dd>
    </div>
  );
}

function readingValue(reading?: FieldReading) {
  return reading?.normalized ?? reading?.raw ?? "";
}

function hasRecordedValue(record: Record<string, FieldReading>) {
  return Object.values(record).some((reading) => Boolean(readingValue(reading)));
}

function displayReading(reading?: FieldReading, fallback = "—") {
  return readingValue(reading) || fallback;
}

function formatUnit(value: string, unit: string) {
  return value ? `${value}${unit}` : "—";
}

function humanizeFieldKey(key: string) {
  const lastSegment = key.split(".").at(-1) ?? key;
  return lastSegment
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim() || key;
}

function formatIndividualId(value: string) {
  return value.length === 10
    ? `${value.slice(0, 3)}-${value.slice(3, 7)}-${value.slice(7)}`
    : value;
}

function limitIndividualId(value: string) {
  return value.normalize("NFKC").replace(/\D/g, "").slice(0, 10);
}

function formatPrice(value: string) {
  const compact = value.normalize("NFKC").replace(/\s+/g, "").replace(/円$/, "");
  const grouped = /^\d{1,3}(?:,\d{3})+$/.test(compact)
    || /^\d{1,3}(?:\.\d{3})+$/.test(compact);
  const canonical = grouped ? compact.replace(/[,.]/g, "") : compact.replace(/,/g, "");
  const numeric = Number(canonical);
  return value && Number.isFinite(numeric)
    ? `${numeric.toLocaleString("ja-JP")}円`
    : value || "—";
}
