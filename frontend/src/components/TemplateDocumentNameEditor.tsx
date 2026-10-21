import { useEffect, useState } from "react";
import { FiX } from "react-icons/fi";
import { api } from "../api";
import type { DocumentNameRule, TemplateDocumentNameSettings } from "../types";
import { showToast } from "./Toast";
import { Spinner } from "./Spinner";

const OPEN_EVENT = "ledger-open-document-name";

/** Opens 「ファイル名の付け方」 for a template over the current screen. */
export function openDocumentNameEditor(templateId: string, templateName: string) {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { templateId, templateName } }));
}

export function DocumentNameEditorHost() {
  const [request, setRequest] = useState<{ templateId: string; templateName: string } | null>(null);
  useEffect(() => {
    const receive = (event: Event) => setRequest((event as CustomEvent<{ templateId: string; templateName: string }>).detail);
    window.addEventListener(OPEN_EVENT, receive);
    return () => window.removeEventListener(OPEN_EVENT, receive);
  }, []);
  if (!request) return null;
  return <TemplateDocumentNameEditor key={request.templateId} {...request} onClose={() => setRequest(null)} />;
}

const EMPTY: DocumentNameRule = { fieldPaths: [], separator: "_", prefix: "", suffix: "" };

/**
 * How the documents of a template are named, changed after the template is made.
 * The name is only proposed on the review screen; no new version is made.
 */
function TemplateDocumentNameEditor({ templateId, templateName, onClose }: {
  templateId: string;
  templateName: string;
  onClose: () => void;
}) {
  const [settings, setSettings] = useState<TemplateDocumentNameSettings | null>(null);
  const [rule, setRule] = useState<DocumentNameRule>(EMPTY);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.templateDocumentName(templateId).then((loaded) => {
      if (!live) return;
      setSettings(loaded);
      setRule(loaded.rule ?? EMPTY);
    }).catch((caught) => { if (live) setError(caught instanceof Error ? caught.message : String(caught)); });
    return () => { live = false; };
  }, [templateId]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [busy, onClose]);

  const labelOf = new Map(settings?.candidates.map((candidate) => [candidate.targetPath, candidate.label]) ?? []);
  const identity = settings?.candidates.filter((candidate) => candidate.identity) ?? [];
  const others = settings?.candidates.filter((candidate) => !candidate.identity) ?? [];
  const listed = showAll || identity.length === 0 ? [...identity, ...others] : identity;
  const toggle = (path: string) => setRule((current) => ({
    ...current,
    fieldPaths: current.fieldPaths.includes(path)
      ? current.fieldPaths.filter((item) => item !== path)
      : [...current.fieldPaths, path],
  }));
  const example = rule.fieldPaths.length
    ? `${rule.prefix}${rule.fieldPaths.map((path) => `〈${labelOf.get(path) ?? "欄"}〉`).join(rule.separator)}${rule.suffix}.xlsx`
    : "ファイル名に入れる欄を選んでください";
  const save = async (next: DocumentNameRule | null) => {
    setBusy(true);
    setError(null);
    try {
      await api.saveTemplateDocumentName(templateId, next);
      showToast({ tone: "success", title: next ? "ファイル名の付け方を保存しました" : "作成時のファイル名の付け方に戻しました", quiet: true });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <section className="modal-card document-name-dialog" role="dialog" aria-modal="true" aria-labelledby="document-name-title">
        <header>
          <h2 id="document-name-title">ファイル名の付け方</h2>
          <button type="button" className="document-name-close" aria-label="閉じる" onClick={onClose} disabled={busy}><FiX aria-hidden="true" /></button>
        </header>
        <p className="document-name-subtitle">{templateName}　·　確認画面の書類名の候補になります。</p>
        {!settings && !error && <p role="status"><Spinner />読み込み中…</p>}
        {settings && <>
          <div className="identity-card-preview" aria-live="polite">
            <span>ファイル名の例</span>
            <strong>{example}</strong>
            <small>〈 〉の部分は、読み取った内容に置き換わります。</small>
          </div>
          <p className="document-name-heading">ファイル名に入れる欄（押した順に並びます）</p>
          <div className="document-name-field-options">
            {listed.map((candidate) => {
              const order = rule.fieldPaths.indexOf(candidate.targetPath);
              return (
                <label key={candidate.targetPath}>
                  <input type="checkbox" checked={order >= 0} onChange={() => toggle(candidate.targetPath)} />
                  {order >= 0 && <b className="document-name-order">{order + 1}</b>}
                  {candidate.label}
                </label>
              );
            })}
          </div>
          {identity.length > 0 && others.length > 0 && (
            <button type="button" className="secondary compact" onClick={() => setShowAll((value) => !value)}>
              {showAll ? "確認する欄だけ表示" : `ほかの欄も表示（${others.length}件）`}
            </button>
          )}
          <div className="document-name-format-grid">
            <label>前に付ける文字<input value={rule.prefix} maxLength={100} onChange={(event) => setRule({ ...rule, prefix: event.target.value })} placeholder="例: 面接カード_" /></label>
            <label>欄と欄の間の文字<input value={rule.separator} maxLength={20} onChange={(event) => setRule({ ...rule, separator: event.target.value })} placeholder="例: _（空欄でも可）" /></label>
            <label>後ろに付ける文字<input value={rule.suffix} maxLength={100} onChange={(event) => setRule({ ...rule, suffix: event.target.value })} placeholder="空欄でも可" /></label>
          </div>
        </>}
        {error && <p className="error-box" role="alert">{error}</p>}
        <div className="page-actions">
          {settings?.custom && <button type="button" className="secondary" disabled={busy} onClick={() => void save(null)}>作成時の設定に戻す</button>}
          <button type="button" className="secondary" disabled={busy} onClick={onClose}>キャンセル</button>
          <button type="button" className="primary" disabled={busy || !settings || rule.fieldPaths.length === 0}
            onClick={() => void save(rule)}>{busy ? <><Spinner />保存中…</> : "保存"}</button>
        </div>
      </section>
    </div>
  );
}
