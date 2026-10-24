import { useEffect, useMemo, useState } from "react";
import { FiCheck, FiFileText, FiInfo, FiRotateCcw, FiTrash2, FiX } from "react-icons/fi";
import { api } from "../api";
import { useTemplatePreviews, templatePreviewKey } from "../hooks/useTemplatePreviews";
import type { Template, TemplateDeleteImpact, TemplateTrashItem } from "../types";
import { userText } from "../userMessages";
import { Spinner } from "./Spinner";
import { showToast } from "./Toast";

type PreviewSource = { id: string; sourceJobId: string | null };

function Thumb({ url }: { url?: string }) {
  const [failed, setFailed] = useState(false);
  return <span className="trash-thumb">
    {url && !failed ? <img src={url} alt="" loading="lazy" onError={() => setFailed(true)} /> : <FiFileText aria-hidden="true" />}
  </span>;
}

/** A small "i": the note shows on hover, focus or a press. */
function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return <span className={`trash-info${open ? " is-open" : ""}`}>
    <button type="button" aria-label="説明" aria-expanded={open}
      onClick={() => setOpen((value) => !value)} onBlur={() => setOpen(false)}><FiInfo aria-hidden="true" /></button>
    <span role="tooltip">{text}</span>
  </span>;
}

function errorText(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? userText(error.message) : fallback;
}

function useEscape(onClose: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, active]);
}

/** Asks before a template goes to the trash, and where its documents should go. */
export function TemplateDeleteDialog({ template, templates, onClose, onDeleted }: {
  template: Template;
  templates: Template[];
  onClose: () => void;
  onDeleted: () => void | Promise<void>;
}) {
  const [impact, setImpact] = useState<TemplateDeleteImpact | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [destination, setDestination] = useState<string>("trash");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEscape(onClose, !busy);

  useEffect(() => {
    let active = true;
    api.templateDeleteImpact(template.id)
      .then((value) => { if (active) setImpact(value); })
      .catch((reason) => { if (active) setLoadError(errorText(reason, "書類の数を確認できませんでした。")); });
    return () => { active = false; };
  }, [template.id]);

  // Templates that can be read with now; the documents move to one of these.
  const targets = useMemo(() => templates
    .filter((item) => item.id !== template.id && item.activeVersionId)
    .map((item) => ({ template: item, version: item.versions.find((v) => v.id === item.activeVersionId) ?? null })),
  [templates, template.id]);
  const previewSources = useMemo<PreviewSource[]>(() => targets
    .flatMap(({ version }) => version ? [{ id: version.id, sourceJobId: version.sourceJobId }] : []), [targets]);
  const previewFor = useTemplatePreviews(previewSources, true);
  const documents = impact?.documents ?? 0;

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      if (documents > 0 && destination === "move") return;
      const moveTo = documents > 0 && destination !== "trash" ? destination : undefined;
      await api.deleteTemplate(template.id, moveTo);
      const target = moveTo ? targets.find((item) => item.template.id === moveTo)?.template.name : null;
      showToast({
        tone: "success", quiet: true, title: "ごみ箱に移動しました",
        message: target ? `書類${documents}件は「${target}」に移しました。` : `「${template.name}」はごみ箱から元に戻せます。`,
      });
      await onDeleted();
    } catch (reason) {
      setError(errorText(reason, "削除できませんでした。"));
      setBusy(false);
    }
  };

  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="modal-card trash-dialog" role="dialog" aria-modal="true" aria-labelledby="template-delete-title">
      <h2 id="template-delete-title">「{template.name}」を削除しますか？</h2>
      {!impact && !loadError && <p className="trash-note"><Spinner />このテンプレートで読み取った書類を確認しています…</p>}
      {loadError && <p className="trash-error" role="alert">{loadError}</p>}
      {impact && documents > 0 && <fieldset className="trash-destination">
        <legend>このテンプレートで読み取った書類が<strong className="trash-count">{documents}件</strong>あります。</legend>
        <label className={`trash-option${destination === "trash" ? " is-selected" : ""}`}>
          <input type="radio" name="trash-destination" checked={destination === "trash"} onChange={() => setDestination("trash")} />
          <span><b>まとめて削除</b><small>ライブラリとチャットに出なくなります</small></span>
        </label>
        {targets.length > 0 && <div className={`trash-option-wrap${destination !== "trash" ? " is-selected" : ""}`}>
          <label className={`trash-option${destination !== "trash" ? " is-selected" : ""}`}>
            <input type="radio" name="trash-destination" checked={destination !== "trash"}
              onChange={() => setDestination(targets.length === 1 ? targets[0].template.id : "move")} />
            <span><b>別のテンプレートに移す</b></span>
          </label>
          <InfoTip text="書類は移した先のテンプレートの書類としてライブラリ・チャットに出ます。" />
        </div>}
        {destination !== "trash" && <ul className="trash-targets" aria-label="移動先のテンプレート">
          {targets.map(({ template: item, version }) => {
            const preview = version ? previewFor({ id: version.id, sourceJobId: version.sourceJobId }) : undefined;
            const selected = destination === item.id;
            return <li key={item.id}>
              <label className={`trash-option is-target${selected ? " is-selected" : ""}`}>
                <input type="radio" name="trash-target" checked={selected} onChange={() => setDestination(item.id)} />
                <Thumb key={version ? templatePreviewKey({ id: version.id, sourceJobId: version.sourceJobId }) : item.id} url={preview?.url} />
                <span><b>{item.name}</b></span>
                {selected && <FiCheck className="trash-check" aria-hidden="true" />}
              </label>
            </li>;
          })}
        </ul>}
      </fieldset>}
      {error && <p className="trash-error" role="alert">{error}</p>}
      <div className="page-actions">
        <button type="button" className="secondary" disabled={busy} onClick={onClose}>キャンセル</button>
        <button type="button" className="danger-button" disabled={busy || (!impact && !loadError) || (documents > 0 && destination === "move")} onClick={() => void submit()}>
          {busy ? <><Spinner />削除中…</> : <><FiTrash2 aria-hidden="true" />削除する</>}
        </button>
      </div>
    </section>
  </div>;
}

/** Deleted templates: put back, or delete for good. */
export function TemplateTrashPanel({ onClose, onRestored }: {
  onClose: () => void;
  onRestored: () => void | Promise<void>;
}) {
  const [items, setItems] = useState<TemplateTrashItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [purging, setPurging] = useState<TemplateTrashItem | null>(null);
  useEscape(() => setPurging(null), Boolean(purging) && !working);

  const load = async () => {
    try { setItems((await api.templateTrash()).items); setError(null); }
    catch (reason) { setError(errorText(reason, "ごみ箱を読み込めませんでした。")); }
  };
  useEffect(() => { void load(); }, []);

  const previewFor = useTemplatePreviews(
    useMemo(() => (items ?? []).map((item) => ({ id: item.templateId, sourceJobId: item.sourceJobId })), [items]),
    true,
  );

  const restore = async (item: TemplateTrashItem) => {
    setWorking(item.templateId);
    try {
      await api.restoreTemplate(item.templateId);
      showToast({ tone: "success", quiet: true, title: `「${item.name}」を元に戻しました` });
      await Promise.all([load(), onRestored()]);
    } catch (reason) {
      showToast({ tone: "error", quiet: true, title: "元に戻せませんでした", message: errorText(reason, "") });
    } finally { setWorking(null); }
  };
  const purge = async (item: TemplateTrashItem) => {
    setWorking(item.templateId);
    try {
      await api.purgeTemplate(item.templateId);
      showToast({ tone: "success", quiet: true, title: `「${item.name}」を完全に削除しました` });
      setPurging(null);
      await load();
    } catch (reason) {
      showToast({ tone: "error", quiet: true, title: "削除できませんでした", message: errorText(reason, "") });
    } finally { setWorking(null); }
  };

  return <div className="trash-panel">
    <div className="trash-panel-head">
      <h3><FiTrash2 aria-hidden="true" />ごみ箱</h3>
      <button type="button" className="ghost compact" onClick={onClose}><FiX aria-hidden="true" />一覧に戻る</button>
    </div>
    {error && <p className="trash-error" role="alert">{error}</p>}
    {!items && !error && <p className="trash-note"><Spinner />読み込んでいます…</p>}
    {items?.length === 0 && <div className="empty-state"><h2>ごみ箱は空です</h2></div>}
    {items && items.length > 0 && <ul className="trash-list">
      {items.map((item) => {
        const busy = working === item.templateId;
        return <li key={item.templateId} className="trash-item">
          <Thumb url={previewFor({ id: item.templateId, sourceJobId: item.sourceJobId })?.url} />
          <div className="trash-item-main">
            <b title={item.name}>{item.name}</b>
            <small>{new Date(item.trashedAt).toLocaleString("ja-JP", { dateStyle: "medium", timeStyle: "short" })}に削除
              {item.documents > 0 ? ` · 書類${item.documents}件` : ""}</small>
          </div>
          <div className="trash-item-actions">
            <button type="button" className="secondary compact" disabled={Boolean(working)} onClick={() => void restore(item)}>
              {busy && !purging ? <Spinner /> : <FiRotateCcw aria-hidden="true" />}元に戻す
            </button>
            <button type="button" className="danger-button compact" disabled={Boolean(working)} onClick={() => setPurging(item)}>完全に削除</button>
          </div>
        </li>;
      })}
    </ul>}
    {purging && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !working) setPurging(null); }}>
      <section className="modal-card trash-dialog" role="dialog" aria-modal="true" aria-labelledby="template-purge-title">
        <h2 id="template-purge-title">「{purging.name}」を完全に削除しますか？</h2>
        <p>{purging.documents > 0
          ? `このテンプレートの書類${purging.documents}件（元画像・読み取り結果・XLSX）もいっしょに削除されます。`
          : "テンプレートと保存した設定が削除されます。"}元に戻すことはできません。</p>
        <div className="page-actions">
          <button type="button" className="secondary" disabled={Boolean(working)} onClick={() => setPurging(null)}>キャンセル</button>
          <button type="button" className="danger-button" disabled={Boolean(working)} onClick={() => void purge(purging)}>
            {working ? <><Spinner />削除中…</> : "完全に削除する"}
          </button>
        </div>
      </section>
    </div>}
  </div>;
}
