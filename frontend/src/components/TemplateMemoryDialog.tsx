import { useEffect, useMemo, useState } from "react";
import { FiTrash2, FiX } from "react-icons/fi";
import { api } from "../api";
import type { TemplateMemory, TemplateVersion } from "../types";
import { Spinner } from "./Spinner";

const OPEN_EVENT = "ledger-open-template-memory";

type MemoryRequest = { templateId: string; templateName: string; version: TemplateVersion };

/** Opens 「メモリ」 (writing rules learned at confirmation) for a template. */
export function openTemplateMemory(templateId: string, templateName: string, version: TemplateVersion) {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { templateId, templateName, version } }));
}

export function TemplateMemoryHost() {
  const [request, setRequest] = useState<MemoryRequest | null>(null);
  useEffect(() => {
    const receive = (event: Event) => setRequest((event as CustomEvent<MemoryRequest>).detail);
    window.addEventListener(OPEN_EVENT, receive);
    return () => window.removeEventListener(OPEN_EVENT, receive);
  }, []);
  if (!request) return null;
  return <TemplateMemoryDialog key={request.templateId} {...request} onClose={() => setRequest(null)} />;
}

/**
 * The rules the memory learned from corrections made before confirming, and the cells
 * each applies to. The rules are the reading instruction of new readings; a rule can be
 * deleted, not written here.
 */
function TemplateMemoryDialog({ templateId, templateName, version, onClose }: MemoryRequest & { onClose: () => void }) {
  const [memory, setMemory] = useState<TemplateMemory | null>(null);
  const [preview, setPreview] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void Promise.all([
      api.templateMemory(templateId, version.id),
      version.sourceJobId ? api.artifacts(version.sourceJobId) : Promise.resolve({ items: [] }),
    ]).then(([loaded, artifacts]) => {
      if (!live) return;
      setMemory(loaded);
      setSelected(loaded.items[0]?.id ?? null);
      const byName = new Map(artifacts.items.map((artifact) => [artifact.name, artifact.url]));
      setPreview(byName.get("cleanPreview") ?? byName.get("cleanTemplate") ?? byName.get("normalized") ?? byName.get("alignedSource.1") ?? "");
    }).catch((caught) => { if (live) setError(caught instanceof Error ? caught.message : String(caught)); });
    return () => { live = false; };
  }, [templateId, version.id, version.sourceJobId]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);

  // Items in form order: the same cell's rules together.
  const groups = useMemo(() => {
    const byLabel = new Map<string, TemplateMemory["items"]>();
    for (const item of memory?.items ?? []) byLabel.set(item.label, [...(byLabel.get(item.label) ?? []), item]);
    return [...byLabel.entries()];
  }, [memory]);
  const current = memory?.items.find((item) => item.id === selected) ?? null;
  const width = version.canonicalWidth || 1;
  const height = version.canonicalHeight || 1;

  const remove = async (id: string) => {
    setDeleting(id);
    setError(null);
    try {
      await api.deleteTemplateMemory(templateId, id);
      setMemory((value) => value && { ...value, items: value.items.filter((item) => item.id !== id) });
      if (selected === id) setSelected(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="modal-card template-memory-dialog" role="dialog" aria-modal="true" aria-labelledby="template-memory-title">
        <header>
          <h2 id="template-memory-title">メモリ</h2>
          <button type="button" className="document-name-close" aria-label="閉じる" onClick={onClose}><FiX aria-hidden="true" /></button>
        </header>
        <p className="document-name-subtitle">{templateName}</p>
        {!memory && !error && <p role="status"><Spinner />読み込み中…</p>}
        {memory && memory.items.length === 0 && <p className="template-memory-empty">まだメモリはありません。</p>}
        {memory && memory.items.length > 0 && (
          <div className="template-memory-body">
            <div className="template-memory-preview" style={{ aspectRatio: `${width} / ${height}` }}>
              {preview && <img src={preview} alt={`${templateName}の帳票`} />}
              {current?.boxes.map((box, index) => (
                <span
                  key={index}
                  className="template-memory-box"
                  style={{
                    left: `${box.x / width * 100}%`,
                    top: `${box.y / height * 100}%`,
                    width: `${box.width / width * 100}%`,
                    height: `${box.height / height * 100}%`,
                  }}
                />
              ))}
            </div>
            <div className="template-memory-list">
              {groups.map(([label, items]) => (
                <section key={label}>
                  <h3>{label || "名称未設定の欄"}</h3>
                  {items.map((item) => (
                    <div
                      key={item.id}
                      role="button"
                      tabIndex={0}
                      className={`template-memory-rule${item.id === selected ? " active" : ""}`}
                      onClick={() => setSelected(item.id)}
                      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(item.id); } }}
                    >
                      <div>
                        <strong>{item.rule}</strong>
                        {item.exampleBefore && item.exampleAfter && (
                          <small>例：{item.exampleBefore} → {item.exampleAfter}</small>
                        )}
                      </div>
                      <button
                        type="button"
                        className="ghost compact"
                        aria-label={`「${item.rule}」を削除`}
                        disabled={deleting !== null}
                        onClick={(event) => { event.stopPropagation(); void remove(item.id); }}
                      >
                        {deleting === item.id ? <Spinner /> : <FiTrash2 aria-hidden="true" />}
                      </button>
                    </div>
                  ))}
                </section>
              ))}
            </div>
          </div>
        )}
        {error && <p className="error-box" role="alert">{error}</p>}
      </section>
    </div>
  );
}
