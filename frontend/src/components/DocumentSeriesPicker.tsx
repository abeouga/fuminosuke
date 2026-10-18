import { useEffect, useState } from "react";
import { FiSearch } from "react-icons/fi";
import { ApiError, api, type SeriesCandidate } from "../api";

export type SeriesSelection = { jobId: string; label: string; seriesVersion: number };

/**
 * Optional choice to read the upload as the next version of a confirmed document.
 * The user picks the document; nothing is matched automatically.
 */
export function DocumentSeriesPicker({ templateVersionId, open, onOpenChange, value, onChange }: {
  templateVersionId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: SeriesSelection | null;
  onChange: (value: SeriesSelection | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<SeriesCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let stopped = false;
    const timer = window.setTimeout(() => {
      void api.seriesCandidates(templateVersionId, query.trim() || undefined)
        .then((page) => { if (!stopped) { setItems(page.items); setError(null); } })
        .catch((reason) => { if (!stopped) setError(reason instanceof ApiError ? reason.message : "確定済みの書類を取得できませんでした。"); });
    }, 200);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [open, query, templateVersionId]);

  return (
    <fieldset className="series-picker">
      <label className="switch-row">
        <span><strong>確定済みの書類の更新として読み込む</strong><small>前に読み取って確定した同じ書類に、記入が増えた場合に使います。前の版は履歴として残ります。</small></span>
        <input type="checkbox" checked={open} onChange={(event) => {
          onOpenChange(event.target.checked);
          if (!event.target.checked) onChange(null);
        }} /><i />
      </label>
      {open && (
        <div className="series-picker-body">
          <label className="series-picker-search">
            <FiSearch aria-hidden="true" />
            <input type="search" value={query} placeholder="書類名・識別情報で絞り込み" onChange={(event) => setQuery(event.target.value)} />
          </label>
          {error && <p className="inline-warning" role="alert">{error}</p>}
          {items !== null && items.length === 0 && !error && <p className="series-picker-empty">このテンプレートの確定済みの書類はありません。</p>}
          <ul className="series-picker-list" role="radiogroup" aria-label="更新する書類">
            {(items ?? []).map((item) => {
              const label = item.documentName ?? "（名称なし）";
              const checked = value?.jobId === item.jobId;
              return (
                <li key={item.jobId}>
                  <label className={`series-picker-item${checked ? " selected" : ""}${item.selectable ? "" : " disabled"}`}>
                    <input type="radio" name="series-predecessor" checked={checked} disabled={!item.selectable}
                      onChange={() => onChange({ jobId: item.jobId, label, seriesVersion: item.seriesVersion })} />
                    <span>
                      <strong>{label}{item.seriesVersion > 1 && <small> 版{item.seriesVersion}</small>}</strong>
                      <small>確定 {new Date(item.confirmedAt).toLocaleDateString("ja-JP")}</small>
                      {item.identity.some((entry) => entry.value) && (
                        <small>{item.identity.filter((entry) => entry.value).map((entry) => `${entry.label}=${entry.value}`).join("、")}</small>
                      )}
                      {item.reason && <small className="series-picker-reason">{item.reason}</small>}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {value === null && items !== null && items.some((item) => item.selectable) && (
            <p className="series-picker-empty">更新する書類を選んでください。</p>
          )}
        </div>
      )}
    </fieldset>
  );
}
