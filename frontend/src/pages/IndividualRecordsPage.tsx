import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api";
import {
  navigateToSpreadsheet,
  type SpreadsheetNavigator,
} from "../spreadsheetNavigation";
import type {
  DataSearchCatalog,
  DataSearchField,
  DataSearchFilter,
  DataSearchItem,
  DataSearchResponse,
} from "../types";

const PAGE_SIZE = 20;

export function IndividualRecordsPage({
  onEditResult,
  onOpenDocument,
  onSpreadsheetNavigate = navigateToSpreadsheet,
}: {
  onEditResult?: (jobId: string) => void;
  onOpenDocument?: (documentId: string) => void;
  onSpreadsheetNavigate?: SpreadsheetNavigator;
}) {
  const [catalog, setCatalog] = useState<DataSearchCatalog | null>(null);
  const [result, setResult] = useState<DataSearchResponse | null>(null);
  const [query, setQuery] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [filters, setFilters] = useState<DataSearchFilter[]>([]);
  const [busy, setBusy] = useState(true);
  const [actionJobId, setActionJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    void Promise.all([
      api.dataSearchCatalog(),
      api.dataSearch({ pageSize: PAGE_SIZE }),
    ]).then(([nextCatalog, nextResult]) => {
      if (cancelled) return;
      setCatalog(nextCatalog);
      setResult(nextResult);
      setError(null);
    }).catch((reason) => {
      if (!cancelled) {
        setError(searchError(reason, "登録済みデータを取得できませんでした。"));
      }
    }).finally(() => {
      if (!cancelled) setBusy(false);
    });
    return () => { cancelled = true; };
  }, []);

  const selectedTemplate = useMemo(
    () => catalog?.templates.find((item) => item.templateId === templateId) ?? null,
    [catalog, templateId],
  );

  const executeSearch = async (
    page = 1,
    next: { query?: string; templateId?: string; filters?: DataSearchFilter[] } = {},
  ) => {
    setBusy(true);
    setError(null);
    try {
      const selectedTemplateId = next.templateId ?? templateId;
      setResult(await api.dataSearch({
        query: next.query ?? query,
        templateId: selectedTemplateId || null,
        filters: next.filters ?? filters,
        page,
        pageSize: PAGE_SIZE,
      }));
    } catch (reason) {
      setError(searchError(reason, "登録済みデータを検索できませんでした。"));
    } finally {
      setBusy(false);
    }
  };

  const addFilter = () => {
    const first = selectedTemplate?.fields[0];
    if (!first) return;
    setFilters((current) => [...current, emptyFilter(first)]);
  };

  const clearSearch = () => {
    setQuery("");
    setTemplateId("");
    setFilters([]);
    void executeSearch(1, { query: "", templateId: "", filters: [] });
  };

  const editResult = async (jobId: string) => {
    setActionJobId(jobId);
    setError(null);
    try {
      await api.reopenResult(jobId);
      onEditResult?.(jobId);
    } catch (reason) {
      setError(searchError(reason, "読み取り結果の編集を開始できませんでした。"));
    } finally {
      setActionJobId(null);
    }
  };

  const openSpreadsheet = async (item: DataSearchItem) => {
    setActionJobId(item.sourceJobId);
    setError(null);
    try {
      if (item.spreadsheetUrl) {
        onSpreadsheetNavigate(item.spreadsheetUrl);
        return;
      }
      const status = await api.spreadsheetStatus(item.sourceJobId);
      if (!status.spreadsheetUrl) {
        throw new Error("この書類のスプレッドシートはまだ作成されていません。");
      }
      onSpreadsheetNavigate(status.spreadsheetUrl);
    } catch (reason) {
      setError(searchError(reason, "スプレッドシートを開けませんでした。"));
    } finally {
      setActionJobId(null);
    }
  };

  return (
    <div className="page-stack records-page data-search-page">
      <header className="page-header">
        <span className="eyebrow">REGISTERED DATA SEARCH</span>
        <h1>登録済みデータを検索</h1>
        <p>確定した読み取り結果を、書類の見出しや意味づけ済みの項目から検索します。</p>
      </header>

      <form
        className="data-search-panel"
        onSubmit={(event) => {
          event.preventDefault();
          void executeSearch();
        }}
      >
        <div className="data-search-primary">
          <label>
            <span>書類の見出しから検索</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="例：個体識別番号、牛名、帳票名"
              autoComplete="off"
            />
          </label>
          <label>
            <span>書類の種類</span>
            <select
              value={templateId}
              onChange={(event) => {
                setTemplateId(event.target.value);
                setFilters([]);
              }}
            >
              <option value="">すべての書類</option>
              {catalog?.templates.map((item) => (
                <option key={item.templateId} value={item.templateId}>
                  {item.templateName}（{item.documentCount}件）
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="primary" disabled={busy}>
            {busy ? "検索中…" : "検索"}
          </button>
        </div>

        <section className="data-search-conditions" aria-labelledby="search-condition-title">
          <div>
            <div>
              <span className="eyebrow">FILTERS</span>
              <h2 id="search-condition-title">項目を指定して絞り込む</h2>
            </div>
            <button
              type="button"
              className="secondary compact"
              disabled={!selectedTemplate || selectedTemplate.fields.length === 0}
              onClick={addFilter}
            >
              ＋ 条件を追加
            </button>
          </div>
          {!selectedTemplate ? (
            <p>詳細条件を使用する場合は、先に書類の種類を一つ選択してください。</p>
          ) : selectedTemplate.fields.length === 0 ? (
            <p>この書類には検索できる確定済み項目がありません。</p>
          ) : filters.length === 0 ? (
            <p>必要な場合だけ条件を追加してください。書類を見分ける項目は上の検索欄から検索できます。</p>
          ) : (
            <div className="data-search-filter-list">
              {filters.map((filter, index) => (
                <SearchFilterRow
                  key={`${index}-${filter.fieldKey}`}
                  index={index}
                  filter={filter}
                  fields={selectedTemplate.fields}
                  onChange={(next) => setFilters((current) => current.map((item, itemIndex) => (
                    itemIndex === index ? next : item
                  )))}
                  onRemove={() => setFilters((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                />
              ))}
            </div>
          )}
        </section>

        <div className="data-search-footer">
          <button type="button" className="ghost" disabled={busy} onClick={clearSearch}>
            条件をクリア
          </button>
          <button type="submit" className="primary" disabled={busy}>
            {busy ? "検索中…" : "この条件で検索"}
          </button>
        </div>
      </form>

      {error && <p className="error-box" role="alert">{error}</p>}

      <section className="data-search-results" aria-live="polite">
        <header>
          <div>
            <span className="eyebrow">SEARCH RESULTS</span>
            <h2>検索結果</h2>
          </div>
          {result && <strong>{result.count}件</strong>}
        </header>
        {busy && !result ? (
          <div className="empty-state large">登録済みデータを読み込んでいます…</div>
        ) : result?.items.length === 0 ? (
          <div className="empty-state large">
            <span>検</span>
            <h2>条件に一致する書類がありません</h2>
            <p>書類の種類または検索条件を変更してください。</p>
          </div>
        ) : (
          <div className="data-search-result-list">
            {result?.items.map((item) => (
              <SearchResultCard
                key={item.documentId}
                item={item}
                busy={actionJobId === item.sourceJobId}
                onOpenDocument={onOpenDocument ? () => onOpenDocument(item.documentId) : undefined}
                onEdit={() => void editResult(item.sourceJobId)}
                onOpenSpreadsheet={() => void openSpreadsheet(item)}
              />
            ))}
          </div>
        )}
        {result && result.count > result.pageSize && (
          <nav className="data-search-pagination" aria-label="検索結果のページ">
            <button
              type="button"
              className="secondary compact"
              disabled={busy || result.page <= 1}
              onClick={() => void executeSearch(result.page - 1)}
            >
              前へ
            </button>
            <span>{result.page} / {Math.ceil(result.count / result.pageSize)}ページ</span>
            <button
              type="button"
              className="secondary compact"
              disabled={busy || result.page >= Math.ceil(result.count / result.pageSize)}
              onClick={() => void executeSearch(result.page + 1)}
            >
              次へ
            </button>
          </nav>
        )}
      </section>
    </div>
  );
}

function SearchFilterRow({
  index,
  filter,
  fields,
  onChange,
  onRemove,
}: {
  index: number;
  filter: DataSearchFilter;
  fields: DataSearchField[];
  onChange: (filter: DataSearchFilter) => void;
  onRemove: () => void;
}) {
  const field = fields.find((item) => item.fieldKey === filter.fieldKey) ?? fields[0];
  const operators = operatorsFor(field);
  const listId = `data-search-suggestions-${index}`;
  return (
    <div className="data-search-filter-row">
      <label>
        <span>項目</span>
        <select
          aria-label={`検索項目 ${index + 1}`}
          value={filter.fieldKey}
          onChange={(event) => {
            const nextField = fields.find((item) => item.fieldKey === event.target.value) ?? fields[0];
            onChange(emptyFilter(nextField));
          }}
        >
          {fields.map((item) => (
            <option key={item.fieldKey} value={item.fieldKey}>
              {item.sectionTitle} / {item.label}{item.repeated ? "（明細）" : ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>条件</span>
        <select
          aria-label={`検索条件 ${index + 1}`}
          value={filter.operator}
          onChange={(event) => onChange({
            ...filter,
            operator: event.target.value as DataSearchFilter["operator"],
            valueTo: event.target.value === "between" ? filter.valueTo ?? "" : null,
          })}
        >
          {operators.map((operator) => (
            <option key={operator.value} value={operator.value}>{operator.label}</option>
          ))}
        </select>
      </label>
      <label>
        <span>値</span>
        <input
          aria-label={`検索値 ${index + 1}`}
          type={field.valueType === "date" ? "date" : field.valueType === "number" ? "number" : "text"}
          list={field.suggestions.length ? listId : undefined}
          value={filter.value}
          onChange={(event) => onChange({ ...filter, value: event.target.value })}
          autoComplete="off"
        />
        {field.suggestions.length > 0 && (
          <datalist id={listId}>
            {field.suggestions.map((suggestion) => <option key={suggestion} value={suggestion} />)}
          </datalist>
        )}
      </label>
      {filter.operator === "between" && (
        <label>
          <span>終了値</span>
          <input
            aria-label={`検索終了値 ${index + 1}`}
            type={field.valueType === "number" ? "number" : field.valueType === "date" ? "date" : "text"}
            value={filter.valueTo ?? ""}
            onChange={(event) => onChange({ ...filter, valueTo: event.target.value })}
          />
        </label>
      )}
      <button type="button" className="ghost compact" onClick={onRemove}>解除</button>
    </div>
  );
}

function SearchResultCard({
  item,
  busy,
  onOpenDocument,
  onEdit,
  onOpenSpreadsheet,
}: {
  item: DataSearchItem;
  busy: boolean;
  onOpenDocument?: () => void;
  onEdit: () => void;
  onOpenSpreadsheet: () => void;
}) {
  return (
    <article className="data-search-result-card">
      <header>
        <div>
          <span className="eyebrow">{item.templateName}</span>
          <small>{item.displayTitleLabel}</small>
          <h3>{item.displayTitle}</h3>
          {item.displaySubtitle && (
            <p>{item.displaySubtitleLabel ? `${item.displaySubtitleLabel} ` : ""}{item.displaySubtitle}</p>
          )}
        </div>
        <div className="data-search-result-meta">
          <strong>{item.pageCount}ページ</strong>
          <span>更新 {new Date(item.confirmedAt).toLocaleString("ja-JP")}</span>
        </div>
      </header>
      {item.matches.length > 0 && (
        <dl className="data-search-match-list">
          {item.matches.map((match, index) => (
            <div key={`${match.fieldKey}-${match.recordOrder ?? 0}-${index}`}>
              <dt>
                {match.sectionTitle}
                {match.recordOrder ? ` ${match.recordKey || `${match.recordOrder}行目`}` : ""}
                <span>{match.label}</span>
              </dt>
              <dd>{match.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <footer>
        {onOpenDocument && <button type="button" className="primary compact" disabled={busy} onClick={onOpenDocument}>書類を開く</button>}
        <button type="button" className="secondary compact" disabled={busy} onClick={onEdit}>読み取り結果を確認・修正</button>
        <button type="button" className="secondary compact" disabled={busy} onClick={onOpenSpreadsheet}>スプレッドシートを開く ↗</button>
      </footer>
    </article>
  );
}

function emptyFilter(field: DataSearchField): DataSearchFilter {
  return {
    fieldKey: field.fieldKey,
    operator: field.valueType === "number" ? "exact" : field.valueType === "date" ? "on" : "contains",
    value: "",
  };
}

function operatorsFor(field: DataSearchField) {
  if (field.valueType === "number") {
    return [
      { value: "exact", label: "一致" },
      { value: "gte", label: "以上" },
      { value: "lte", label: "以下" },
      { value: "between", label: "範囲" },
    ] as const;
  }
  if (field.valueType === "date") {
    return [
      { value: "on", label: "当日" },
      { value: "before", label: "以前" },
      { value: "after", label: "以後" },
      { value: "between", label: "期間" },
    ] as const;
  }
  return [
    { value: "exact", label: "一致" },
    { value: "contains", label: "含む" },
    { value: "startsWith", label: "前方一致" },
  ] as const;
}

function searchError(reason: unknown, fallback: string) {
  return reason instanceof ApiError || reason instanceof Error ? reason.message : fallback;
}
