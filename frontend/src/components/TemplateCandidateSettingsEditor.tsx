import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api";
import type { Artifact, FieldMapping, Template, TemplateCandidateSettings, TemplateVersion } from "../types";

type CandidateDraft = {
  id: string;
  name: string;
  values: string[];
  allowedSuffixes: string[];
  targetPatterns: string[];
};

export function TemplateCandidateSettingsEditor({
  template,
  version,
  onClose,
  onNavigationGuardChange,
}: {
  template: Template;
  version: TemplateVersion;
  onClose: () => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const [revision, setRevision] = useState(version.revision);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [mappings, setMappings] = useState<FieldMapping[]>([]);
  const [canonicalSize, setCanonicalSize] = useState({ width: version.canonicalWidth, height: version.canonicalHeight });
  const [sets, setSets] = useState<CandidateDraft[]>([]);
  const [activeSetId, setActiveSetId] = useState<string | null>(null);
  const [savedSignature, setSavedSignature] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [newName, setNewName] = useState("");
  const [newValues, setNewValues] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadFailed(false);
    setFeedback(null);
    if (!version.sourceJobId) {
      setLoadFailed(true);
      setLoading(false);
      setFeedback({ kind: "error", text: "帳票プレビューの元画像がありません。" });
      return () => { cancelled = true; };
    }
    void Promise.all([
      api.artifacts(version.sourceJobId),
      api.authoring(version.id),
      api.templateCandidateSettings(template.id, version.id),
    ]).then(([artifactResponse, authoring, settings]) => {
      if (cancelled) return;
      if (authoring.revision !== settings.resourceRevision) throw new Error("読み込み中に変更されました。再取得してください。");
      const mappingResponse = authoring.mapping;
      setRevision(settings.resourceRevision);
      const next = draftsFromSettings(settings);
      setArtifacts(artifactResponse.items);
      setMappings(mappingResponse.mappings.filter((mapping) => mapping.enabled && mapping.mappingType !== "mark"));
      setCanonicalSize({ width: mappingResponse.canonicalWidth, height: mappingResponse.canonicalHeight });
      setSets(next);
      setActiveSetId(next[0]?.id ?? null);
      setSavedSignature(settingsSignature(next));
    }).catch((error) => {
      if (cancelled) return;
      setLoadFailed(true);
      setFeedback({ kind: "error", text: candidateError(error, "読み取り候補の設定を読み込めませんでした。") });
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [loadAttempt, template.id, version.id, version.sourceJobId]);

  const currentSignature = settingsSignature(sets);
  const dirty = !loading && currentSignature !== savedSignature;
  useEffect(() => {
    onNavigationGuardChange?.(dirty || saving);
  }, [dirty, onNavigationGuardChange, saving]);
  useEffect(() => () => onNavigationGuardChange?.(false), [onNavigationGuardChange]);

  const activeSet = sets.find((item) => item.id === activeSetId) ?? null;
  const artifactByName = useMemo(
    () => new Map(artifacts.map((artifact) => [artifact.name, artifact.url])),
    [artifacts],
  );
  const preview = artifactByName.get("cleanPreview")
    ?? artifactByName.get("cleanTemplate")
    ?? artifactByName.get("normalized")
    ?? artifactByName.get("alignedSource.1")
    ?? "";
  const boundSetByTarget = useMemo(() => {
    const result = new Map<string, CandidateDraft>();
    for (const candidateSet of sets) {
      for (const target of candidateSet.targetPatterns) result.set(target, candidateSet);
    }
    return result;
  }, [sets]);

  const close = () => {
    if (saving) return;
    if (dirty && !window.confirm("保存していない読み取り候補の変更があります。破棄して戻りますか？")) return;
    onClose();
  };
  const updateActive = (update: Partial<CandidateDraft>) => {
    if (!activeSetId) return;
    setSets((current) => current.map((item) => (
      item.id === activeSetId ? { ...item, ...update } : item
    )));
    setFeedback(null);
  };
  const addSet = () => {
    const name = newName.trim();
    const values = linesToValues(newValues);
    if (!name || values.length === 0) {
      setFeedback({ kind: "error", text: "候補の種類名と、1件以上の読み取り候補を入力してください。" });
      return;
    }
    const candidate: CandidateDraft = {
      id: `new-${Date.now()}-${sets.length}`,
      name,
      values,
      allowedSuffixes: [],
      targetPatterns: [],
    };
    setSets((current) => [...current, candidate]);
    setActiveSetId(candidate.id);
    setNewName("");
    setNewValues("");
    setFeedback(null);
  };
  const removeSet = (id: string) => {
    setSets((current) => current.filter((item) => item.id !== id));
    if (activeSetId === id) {
      const remaining = sets.filter((item) => item.id !== id);
      setActiveSetId(remaining[0]?.id ?? null);
    }
    setFeedback(null);
  };
  const toggleMapping = (mapping: FieldMapping) => {
    if (!activeSet) {
      setFeedback({ kind: "error", text: "先に右側で読み取り候補を選択してください。" });
      return;
    }
    const targetPattern = normalizeTargetPattern(mapping.targetPath);
    const isCurrent = activeSet.targetPatterns.includes(targetPattern);
    setSets((current) => current.map((candidateSet) => ({
      ...candidateSet,
      targetPatterns: candidateSet.id === activeSet.id
        ? isCurrent
          ? candidateSet.targetPatterns.filter((target) => target !== targetPattern)
          : [...candidateSet.targetPatterns.filter((target) => target !== targetPattern), targetPattern]
        : candidateSet.targetPatterns.filter((target) => target !== targetPattern),
    })));
    setFeedback(null);
  };
  const save = async () => {
    const invalid = sets.find((item) => !item.name.trim() || item.values.length === 0);
    if (invalid) {
      setActiveSetId(invalid.id);
      setFeedback({ kind: "error", text: "候補の種類名と、1件以上の読み取り候補を入力してください。" });
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const saved = await api.saveTemplateCandidateSettings(
        template.id,
        version.id,
        revision,
        sets.map((item) => ({
          name: item.name.trim(),
          values: uniqueValues(item.values),
          allowedSuffixes: uniqueValues(item.allowedSuffixes),
          targetPatterns: [...new Set(item.targetPatterns)],
        })),
      );
      setRevision(saved.resourceRevision);
      const next = draftsFromSettings(saved);
      setSets(next);
      setActiveSetId((current) => next.some((item) => item.id === current) ? current : next[0]?.id ?? null);
      setSavedSignature(settingsSignature(next));
      setFeedback({ kind: "success", text: "読み取り候補と適用する欄を保存しました。次回の読み取りから使用されます。" });
    } catch (error) {
      setFeedback({ kind: "error", text: candidateError(error, "読み取り候補を保存できませんでした。") });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page-stack candidate-settings-page">
      <header className="page-header split">
        <div>
          <span className="eyebrow">READING CANDIDATES · {template.name} · V{version.versionNumber}</span>
          <h1>読み取り候補を設定</h1>
          <p>この書類で使われる文字を登録し、適用する記入欄を帳票上で指定します。候補外の結果は自動確定せず、確認対象になります。</p>
        </div>
        <button type="button" className="ghost" disabled={saving} onClick={close}>← テンプレート一覧へ</button>
      </header>

      {feedback && <div className={feedback.kind === "error" ? "error-box" : "success-box"} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.text}</div>}
      {loadFailed && <button type="button" className="secondary compact candidate-retry" onClick={() => setLoadAttempt((current) => current + 1)}>読み込みを再試行</button>}
      {loading ? <div className="empty-state">読み取り候補と帳票を読み込んでいます…</div> : (
        <div className="candidate-settings-workspace">
          <section className="candidate-preview-panel" aria-label="読み取り候補を適用する帳票プレビュー">
            <div className="candidate-panel-heading">
              <div><span className="eyebrow">DOCUMENT PREVIEW</span><h2>適用する記入欄を選択</h2></div>
              <p>{activeSet ? `「${activeSet.name}」を適用する欄をクリックしてください。繰り返し行は同じ項目の全行へ適用されます。` : "右側で読み取り候補を選択してください。"}</p>
            </div>
            {preview ? (
              <div
                className="candidate-preview-stage"
                style={{ aspectRatio: `${canonicalSize.width} / ${canonicalSize.height}` }}
              >
                <img src={preview} alt={`${template.name}の帳票`} />
                {mappings.map((mapping) => {
                  const targetPattern = normalizeTargetPattern(mapping.targetPath);
                  const boundSet = boundSetByTarget.get(targetPattern);
                  const active = boundSet?.id === activeSetId;
                  return (
                    <button
                      type="button"
                      key={mapping.mappingId}
                      className={`candidate-mapping-rectangle ${boundSet ? "bound" : ""} ${active ? "active" : ""}`}
                      style={{
                        left: `${mapping.x / canonicalSize.width * 100}%`,
                        top: `${mapping.y / canonicalSize.height * 100}%`,
                        width: `${mapping.width / canonicalSize.width * 100}%`,
                        height: `${mapping.height / canonicalSize.height * 100}%`,
                      }}
                      aria-label={`${mapping.label}に読み取り候補を${active ? "適用解除" : "適用"}`}
                      title={`${mapping.label}${boundSet ? ` · ${boundSet.name}` : " · 未設定"}`}
                      onClick={() => toggleMapping(mapping)}
                    >
                      {boundSet && <span>{boundSet.name}</span>}
                    </button>
                  );
                })}
              </div>
            ) : <div className="empty-state">帳票プレビューがありません。</div>}
            <div className="candidate-preview-legend">
              <span><i className="active" />選択中の候補</span>
              <span><i className="bound" />ほかの候補を設定済み</span>
              <span><i />未設定の記入欄</span>
            </div>
          </section>

          <aside className="candidate-control-panel">
            <section className="candidate-add-card">
              <span className="eyebrow">ADD CANDIDATES</span>
              <h2>新しい読み取り候補を追加</h2>
              <label className="field-label">候補の種類名<input value={newName} placeholder="例：担当者名、商品名、等級" maxLength={100} onChange={(event) => setNewName(event.target.value)} /></label>
              <label className="field-label">使われる文字<textarea value={newValues} placeholder={'1行に1件入力\n例：山田太郎\n佐藤花子'} rows={5} onChange={(event) => setNewValues(event.target.value)} /></label>
              <button type="button" className="secondary full" onClick={addSet}>候補を追加して欄を選ぶ</button>
            </section>

            <section className="candidate-set-list" aria-label="登録する読み取り候補">
              <div className="candidate-panel-heading compact">
                <div><span className="eyebrow">REGISTERED FOR TEMPLATE</span><h2>このテンプレートの候補</h2></div>
                <strong>{sets.length}種類</strong>
              </div>
              {sets.map((candidateSet) => (
                <button
                  type="button"
                  key={candidateSet.id}
                  className={candidateSet.id === activeSetId ? "candidate-set-card active" : "candidate-set-card"}
                  onClick={() => { setActiveSetId(candidateSet.id); setFeedback(null); }}
                >
                  <strong>{candidateSet.name || "名称未入力"}</strong>
                  <span>{candidateSet.values.length}候補 · {candidateSet.targetPatterns.length}項目に適用</span>
                </button>
              ))}
              {sets.length === 0 && <div className="empty-state compact">まだ読み取り候補はありません。</div>}
            </section>

            {activeSet && (
              <section className="candidate-edit-card">
                <div className="candidate-edit-heading"><h2>選択中の候補を編集</h2><button type="button" className="danger-button compact" onClick={() => removeSet(activeSet.id)}>削除</button></div>
                <label className="field-label">候補の種類名<input value={activeSet.name} maxLength={100} onChange={(event) => updateActive({ name: event.target.value })} /></label>
                <label className="field-label">使われる文字<CandidateValuesTextarea key={activeSet.id} values={activeSet.values} onChange={(values) => updateActive({ values })} /></label>
                <label className="field-label">候補の後ろに付く文字（任意）<input value={activeSet.allowedSuffixes.join("、")} placeholder="例：ET" onChange={(event) => updateActive({ allowedSuffixes: splitSuffixes(event.target.value) })} /></label>
                <div className="candidate-binding-summary">
                  <strong>適用する項目</strong>
                  {activeSet.targetPatterns.length > 0
                    ? <ul>{activeSet.targetPatterns.map((target) => <li key={target}>{fieldLabelForTarget(target, mappings)}</li>)}</ul>
                    : <p>帳票プレビュー上の記入欄をクリックしてください。</p>}
                </div>
              </section>
            )}
          </aside>
        </div>
      )}

      {!loading && (
        <div className="decision-bar sticky candidate-save-bar">
          <div><strong>{dirty ? "未保存の変更があります" : "保存済みです"}</strong><p>保存後に開始したOCRジョブへ、候補と適用先を固定して記録します。</p></div>
          <button type="button" className="primary" disabled={!dirty || saving || loadFailed} onClick={() => void save()}>{saving ? "保存中…" : "読み取り候補を保存"}</button>
        </div>
      )}
    </div>
  );
}

function draftsFromSettings(settings: TemplateCandidateSettings): CandidateDraft[] {
  return settings.sets.map((item) => ({
    id: item.id,
    name: item.name,
    values: [...item.values],
    allowedSuffixes: [...item.allowedSuffixes],
    targetPatterns: item.bindings.map((binding) => binding.targetPattern),
  }));
}

function settingsSignature(sets: CandidateDraft[]) {
  return JSON.stringify(sets.map((item) => ({
    name: item.name.trim(),
    values: uniqueValues(item.values),
    allowedSuffixes: uniqueValues(item.allowedSuffixes),
    targetPatterns: [...new Set(item.targetPatterns)].sort(),
  })));
}

function uniqueValues(values: string[]) {
  const found = new Set<string>();
  return values.map((value) => value.trim()).filter((value) => {
    if (!value) return false;
    const normalized = value.normalize("NFKC").replace(/\s+/g, "").toLocaleLowerCase();
    if (found.has(normalized)) return false;
    found.add(normalized);
    return true;
  });
}

function linesToValues(value: string) {
  return uniqueValues(value.split(/\r?\n/));
}

function splitSuffixes(value: string) {
  return uniqueValues(value.split(/[、,\r\n]+/));
}

function normalizeTargetPattern(targetPath: string) {
  return targetPath.replace(/\.\d+(?=\.|$)/g, "[]");
}

function fieldLabelForTarget(targetPattern: string, mappings: FieldMapping[]) {
  return mappings.find((mapping) => normalizeTargetPattern(mapping.targetPath) === targetPattern)?.label
    ?? targetPattern;
}

function candidateError(error: unknown, fallback: string) {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

function CandidateValuesTextarea({ values, onChange }: { values: string[]; onChange: (values: string[]) => void }) {
  const [text, setText] = useState(values.join("\n"));
  return <textarea value={text} rows={8} onChange={(event) => {
    setText(event.target.value);
    onChange(linesToValues(event.target.value));
  }} />;
}
