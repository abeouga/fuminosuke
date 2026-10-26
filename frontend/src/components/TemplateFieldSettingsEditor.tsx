import { useEffect, useMemo, useRef, useState } from "react";
import { InfoTip } from "./InfoTip";
import { FiChevronRight, FiCpu, FiList, FiTrash2 } from "react-icons/fi";
import { ApiError, api } from "../api";
import type { Artifact, FieldMapping, LayoutDefinition, Template, TemplateFieldSetting, TemplateMemory, TemplateVersion } from "../types";
import { Spinner } from "./Spinner";

type FieldDraft = {
  keywordsText: string;
  keywordsEnabled: boolean;
  keywordsForced: boolean;
};

/** One settable item: a single field, or every row cell of one table column. */
type FieldItem = { key: string; label: string; patterns: string[]; mappings: FieldMapping[] };

const EMPTY_DRAFT: FieldDraft = { keywordsText: "", keywordsEnabled: true, keywordsForced: false };

type MemoryRule = TemplateMemory["items"][number];

/**
 * 「メモリ」: per-item keywords set by hand, and below them the writing rules learned from
 * corrections made before confirming (those can be deleted, not written here).
 */
export function TemplateFieldSettingsEditor({
  template,
  version,
  onClose,
  onNavigationGuardChange,
  closeLabel = "← 戻る",
}: {
  closeLabel?: string;
  template: Template;
  version: TemplateVersion;
  onClose: () => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const [revision, setRevision] = useState(version.revision);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [mappings, setMappings] = useState<FieldMapping[]>([]);
  const [layout, setLayout] = useState<LayoutDefinition | null>(null);
  const [canonicalSize, setCanonicalSize] = useState({ width: version.canonicalWidth, height: version.canonicalHeight });
  const [drafts, setDrafts] = useState<Record<string, FieldDraft>>({});
  const [rules, setRules] = useState<MemoryRule[]>([]);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [savedSignature, setSavedSignature] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadFailed(false);
    setFeedback(null);
    if (!version.sourceJobId) {
      setLoadFailed(true);
      setLoading(false);
      setFeedback({ kind: "error", text: "帳票の元画像がありません。" });
      return () => { cancelled = true; };
    }
    void Promise.all([
      api.artifacts(version.sourceJobId),
      api.authoring(version.id),
      api.templateFieldSettings(template.id, version.id),
      api.templateMemory(template.id, version.id),
    ]).then(([artifactResponse, authoring, settings, memory]) => {
      if (cancelled) return;
      if (authoring.revision !== settings.resourceRevision) throw new Error("読み込み中に変更されました。もう一度読み込んでください。");
      const textMappings = authoring.mapping.mappings.filter((mapping) => mapping.enabled && mapping.mappingType === "text");
      setRevision(settings.resourceRevision);
      setArtifacts(artifactResponse.items);
      setMappings(textMappings);
      setLayout(authoring.layout);
      setRules(memory.items);
      setCanonicalSize({ width: authoring.mapping.canonicalWidth, height: authoring.mapping.canonicalHeight });
      const next = draftsFromSettings(settings.items, groupItems(textMappings, authoring.layout).keyByPattern);
      setDrafts(next);
      setSavedSignature(signature(next));
    }).catch((error) => {
      if (cancelled) return;
      setLoadFailed(true);
      setFeedback({ kind: "error", text: errorText(error, "メモリを読み込めませんでした。") });
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [loadAttempt, template.id, version.id, version.sourceJobId]);

  const dirty = !loading && signature(drafts) !== savedSignature;
  useEffect(() => {
    onNavigationGuardChange?.(dirty || saving);
  }, [dirty, onNavigationGuardChange, saving]);
  useEffect(() => () => onNavigationGuardChange?.(false), [onNavigationGuardChange]);

  const { items, keyByPattern } = useMemo(() => groupItems(mappings, layout), [mappings, layout]);
  const preview = useMemo(() => {
    const byName = new Map(artifacts.map((artifact) => [artifact.name, artifact.url]));
    return byName.get("cleanPreview") ?? byName.get("cleanTemplate") ?? byName.get("normalized") ?? byName.get("alignedSource.1") ?? "";
  }, [artifacts]);
  // Each learned rule belongs to the item whose cells it covers.
  const rulesByKey = useMemo(() => {
    const byKey = new Map<string, MemoryRule[]>();
    for (const rule of rules) {
      const key = rule.targetPatterns.map((pattern) => keyByPattern.get(pattern)).find(Boolean);
      if (key) byKey.set(key, [...(byKey.get(key) ?? []), rule]);
    }
    return byKey;
  }, [rules, keyByPattern]);
  const configured = [...items.values()].filter((item) => hasContent(drafts[item.key]) || rulesByKey.has(item.key));
  const currentRules = (selected && rulesByKey.get(selected)) || [];
  const removeRule = async (id: string) => {
    setDeleting(id);
    setFeedback(null);
    try {
      await api.deleteTemplateMemory(template.id, id);
      setRules((all) => all.filter((rule) => rule.id !== id));
    } catch (error) {
      setFeedback({ kind: "error", text: errorText(error, "削除できませんでした。") });
    } finally {
      setDeleting(null);
    }
  };
  const current = selected ? items.get(selected) ?? null : null;
  const draft = (selected && drafts[selected]) || EMPTY_DRAFT;
  const keywordCount = lines(draft.keywordsText).length;

  const select = (key: string) => {
    setSelected(key);
    setFeedback(null);
    // In the one-column layout the settings sit below the form: bring them into view.
    requestAnimationFrame(() => {
      const box = panel.current?.getBoundingClientRect();
      if (box && (box.top > window.innerHeight || box.bottom < 0)) panel.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };
  const update = (change: Partial<FieldDraft>) => {
    if (!selected) return;
    setDrafts((all) => ({ ...all, [selected]: { ...(all[selected] ?? EMPTY_DRAFT), ...change } }));
    setFeedback(null);
  };
  const close = () => {
    if (saving) return;
    if (dirty && !window.confirm("保存していない変更があります。破棄して戻りますか？")) return;
    onClose();
  };
  const save = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      const payload = [...items.values()]
        .filter((item) => hasContent(drafts[item.key]))
        .map((item) => {
          const value = drafts[item.key];
          return {
            targetPatterns: item.patterns,
            keywords: lines(value.keywordsText),
            keywordsEnabled: value.keywordsEnabled,
            keywordsForced: value.keywordsForced,
          };
        });
      const saved = await api.saveTemplateFieldSettings(template.id, version.id, revision, payload);
      setRevision(saved.resourceRevision);
      const next = draftsFromSettings(saved.items, keyByPattern);
      setDrafts(next);
      setSavedSignature(signature(next));
      setFeedback({ kind: "success", text: "保存しました。次に読み取る書類から使います。" });
    } catch (error) {
      setFeedback({ kind: "error", text: errorText(error, "保存できませんでした。") });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page-stack candidate-settings-page">
      <header className="page-header split has-back title-end">
        <button type="button" className="ghost page-back" disabled={saving} onClick={close}>{closeLabel}</button>
        <div>
          <span className="eyebrow">{template.name}</span>
          <h1>メモリ<InfoTip align="end" text="欄を押して、よく出る言葉を登録します。覚えた書き方もここに出ます。" /></h1>
        </div>
      </header>

      {feedback && <div className={feedback.kind === "error" ? "error-box" : "success-box"} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.text}</div>}
      {loadFailed && <button type="button" className="secondary compact candidate-retry" onClick={() => setLoadAttempt((n) => n + 1)}>もう一度読み込む</button>}
      {loading ? <div className="empty-state">読み込んでいます…</div> : !loadFailed && (
        <div className="candidate-settings-workspace">
          <section className="candidate-preview-panel" aria-label="帳票">
            {preview ? (
              <div className="candidate-preview-stage" style={{ aspectRatio: `${canonicalSize.width} / ${canonicalSize.height}` }}>
                <img src={preview} alt={`${template.name}の帳票`} />
                {mappings.map((mapping) => {
                  const key = keyByPattern.get(targetPattern(mapping.targetPath)) ?? "";
                  return (
                    <button
                      type="button"
                      key={mapping.mappingId}
                      className={`candidate-mapping-rectangle ${hasContent(drafts[key]) || rulesByKey.has(key) ? "bound" : ""} ${key === selected ? "active" : ""}`}
                      style={{
                        left: `${mapping.x / canonicalSize.width * 100}%`,
                        top: `${mapping.y / canonicalSize.height * 100}%`,
                        width: `${mapping.width / canonicalSize.width * 100}%`,
                        height: `${mapping.height / canonicalSize.height * 100}%`,
                      }}
                      aria-label={`${mapping.label}を選ぶ`}
                      title={mapping.label}
                      onClick={() => select(key)}
                    />
                  );
                })}
              </div>
            ) : <div className="empty-state">帳票の画像がありません。</div>}
          </section>

          <aside className="candidate-control-panel" ref={panel}>
            {current ? (
              <section className="candidate-edit-card field-setting-card">
                <div className="candidate-edit-heading">
                  <h2>{current.label}</h2>
                  {current.mappings.length > 1 && <span className="field-setting-scope">表の列（{current.mappings.length}行すべて）</span>}
                </div>
                <label className="field-label">候補語（1行に1つ）
                  <textarea
                    value={draft.keywordsText}
                    rows={7}
                    placeholder={"例：\n安福久\n勝早桜5"}
                    onChange={(event) => update({ keywordsText: event.target.value })}
                  />
                </label>
                {keywordCount > 0 && <div className="field-setting-switches">
                  <label className="field-setting-switch">
                    <input type="checkbox" checked={draft.keywordsEnabled} onChange={(event) => update({ keywordsEnabled: event.target.checked })} />
                    <span>候補語を使う（{keywordCount}語）</span>
                  </label>
                  <label className={`field-setting-switch ${draft.keywordsEnabled ? "" : "disabled"}`}>
                    <input type="checkbox" checked={draft.keywordsForced} disabled={!draft.keywordsEnabled} onChange={(event) => update({ keywordsForced: event.target.checked })} />
                    <span>候補語に寄せる<small>{draft.keywordsForced
                      ? "読み取った文字を一番近い候補語にします。寄せた欄と、近い語が決まらない欄には確認の印が付きます。"
                      : "オフのときは参考だけ。候補と同じ語なら表記をそろえ、ほかは読み取ったまま残します。"}</small></span>
                  </label>
                </div>}
                {hasContent(draft) && <button type="button" className="ghost compact field-setting-clear" onClick={() => update({ ...EMPTY_DRAFT })}>候補語を消す</button>}
                <div className="field-memory-rules">
                  <h3>覚えた書き方</h3>
                  {currentRules.length === 0
                    ? <p className="field-memory-empty">まだありません。確定した書類で直した書き方を、ここで覚えます。</p>
                    : <ul>{currentRules.map((rule) => (
                        <li key={rule.id}>
                          <div>
                            <strong>{rule.rule}</strong>
                            {rule.exampleBefore && rule.exampleAfter && <small>例：{rule.exampleBefore} → {rule.exampleAfter}</small>}
                          </div>
                          <button type="button" className="ghost compact" aria-label={`「${rule.rule}」を削除`} title="削除"
                            disabled={deleting !== null} onClick={() => void removeRule(rule.id)}>
                            {deleting === rule.id ? <Spinner /> : <FiTrash2 aria-hidden="true" />}
                          </button>
                        </li>
                      ))}</ul>}
                </div>
              </section>
            ) : (
              <section className="candidate-edit-card">
                <p className="field-setting-empty">帳票の欄を押して選んでください。</p>
              </section>
            )}
            {configured.length > 0 && (
              <section className="memory-set-list" aria-label="設定済みの欄">
                <header className="memory-set-head">
                  <h2>設定済み</h2>
                  <span className="memory-set-count">{configured.length}</span>
                </header>
                <ul>
                  {configured.map((item) => {
                    const value = drafts[item.key];
                    const count = value ? lines(value.keywordsText).length : 0;
                    const learned = rulesByKey.get(item.key)?.length ?? 0;
                    const active = item.key === selected;
                    return (
                      <li key={item.key}>
                        <button type="button" className={`memory-set-item${active ? " active" : ""}`} aria-pressed={active} onClick={() => select(item.key)}>
                          <span className="memory-set-name">
                            <strong>{item.label}</strong>
                            {item.mappings.length > 1 && <small>表の列</small>}
                          </span>
                          <span className="memory-set-tags">
                            {count > 0 && <span className={`memory-tag is-keywords${value.keywordsEnabled ? "" : " is-off"}`}
                              title={value.keywordsEnabled ? value.keywordsForced ? "候補語に寄せる" : "候補語を参考にする" : "候補語を使わない"}>
                              <FiList aria-hidden="true" />{count}語{value.keywordsEnabled ? value.keywordsForced ? "・寄せる" : "" : "・オフ"}
                            </span>}
                            {learned > 0 && <span className="memory-tag is-rules" title="覚えた書き方">
                              <FiCpu aria-hidden="true" />{learned}件
                            </span>}
                          </span>
                          <FiChevronRight className="memory-set-arrow" aria-hidden="true" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}
          </aside>
        </div>
      )}

      {!loading && !loadFailed && (
        <div className="decision-bar sticky candidate-save-bar">
          <div><strong>{dirty ? "保存していない変更があります" : "保存済みです"}<InfoTip side="above" text="次の読み取りから使います。" /></strong></div>
          <button type="button" className="primary" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <><Spinner />保存中…</> : "保存"}</button>
        </div>
      )}
    </div>
  );
}

/** Fields and table columns. A column is the row cells of one table under one heading. */
function groupItems(mappings: FieldMapping[], layout: LayoutDefinition | null) {
  const relationByTarget = new Map((layout?.relations ?? []).map((relation) => [relation.targetPath, relation]));
  const sectionByCell = new Map<string, string>();
  for (const group of layout?.recordGroups ?? []) {
    for (const cellId of group.inputCellIds) sectionByCell.set(cellId, group.sectionId);
  }
  const items = new Map<string, FieldItem>();
  const keyByPattern = new Map<string, string>();
  for (const mapping of mappings) {
    const pattern = targetPattern(mapping.targetPath);
    const relation = relationByTarget.get(mapping.targetPath);
    const section = relation ? sectionByCell.get(relation.inputCellId) : undefined;
    const heading = relation?.labelCellIds.length ? [...relation.labelCellIds].sort().join(",") : mapping.label;
    const key = section ? `column:${section}:${heading}` : `field:${pattern}`;
    const item = items.get(key);
    if (item) {
      if (!item.patterns.includes(pattern)) item.patterns.push(pattern);
      item.mappings.push(mapping);
    } else {
      items.set(key, { key, label: mapping.label, patterns: [pattern], mappings: [mapping] });
    }
    keyByPattern.set(pattern, key);
  }
  return { items, keyByPattern };
}

function draftsFromSettings(saved: TemplateFieldSetting[], keyByPattern: Map<string, string>): Record<string, FieldDraft> {
  return Object.fromEntries(saved.map((item) => [keyByPattern.get(item.targetPatterns[0]) ?? `field:${item.targetPatterns[0]}`, {
    keywordsText: item.keywords.join("\n"),
    keywordsEnabled: item.keywordsEnabled,
    keywordsForced: item.keywordsForced,
  }]));
}

function hasContent(draft: FieldDraft | undefined) {
  return Boolean(draft && (lines(draft.keywordsText).length > 0));
}

function signature(drafts: Record<string, FieldDraft>) {
  return JSON.stringify(Object.entries(drafts)
    .filter(([, item]) => hasContent(item))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, item]) => [key, lines(item.keywordsText), item.keywordsEnabled, item.keywordsForced]));
}

function lines(text: string) {
  const seen = new Set<string>();
  return text.split(/\r?\n/).map((line) => line.trim()).filter((line) => {
    const key = line.normalize("NFKC").replace(/\s+/g, "").toLocaleLowerCase();
    if (!line || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function targetPattern(targetPath: string) {
  return targetPath.replace(/\.\d+(?=\.|$)/g, "[]");
}

function errorText(error: unknown, fallback: string) {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}
