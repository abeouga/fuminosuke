import { useEffect, useMemo, useState } from "react";
import { ApiError, api, exportUrl } from "../api";
import type { Artifact, FieldReading, OcrDocument, OcrFieldPreview, OcrResult } from "../types";

type ReadingEntry = { path: string; label: string; value: FieldReading };
type ReviewColumn = { key: string; label: string };
type RevisionConflictBackup = {
  draft: OcrDocument;
  baseRevision: number;
  latestLoaded: boolean;
};
type JapaneseEra = "" | "heisei" | "reiwa";

const SALE_NOTE_SEX_SYMBOLS = ["♂", "♀"];

function sanitizeBirthDateInput(value: string) {
  return value
    .normalize("NFKC")
    .replace(/[^\d]+/g, " ")
    .replace(/^\s+/, "")
    .split(" ")
    .slice(0, 3)
    .join(" ");
}

function birthDateParts(value: string | null) {
  if (!value) return null;
  const groups = sanitizeBirthDateInput(value).trim().split(/\s+/);
  if (groups.length !== 3 || groups.some((group) => !/^\d+$/.test(group))) return null;
  const [year, month, day] = groups.map(Number);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return null;
  return [year, month, day] as const;
}

function formatJapaneseEraDate(value: string | null, era: Exclude<JapaneseEra, "">) {
  const parts = birthDateParts(value);
  if (!parts) return null;
  const [eraYear, month, day] = parts;
  const westernYear = eraYear + (era === "heisei" ? 1988 : 2018);
  const converted = new Date(Date.UTC(westernYear, month - 1, day));
  if (
    converted.getUTCFullYear() !== westernYear
    || converted.getUTCMonth() !== month - 1
    || converted.getUTCDate() !== day
  ) return null;
  const stamp = westernYear * 10000 + month * 100 + day;
  if (era === "heisei" && (stamp < 19890108 || stamp > 20190430)) return null;
  if (era === "reiwa" && stamp < 20190501) return null;
  return `${westernYear}年${month}月${day}日`;
}

function inferJapaneseEra(raw: string | null, normalized: string | null): JapaneseEra {
  if (!normalized) return "";
  if (formatJapaneseEraDate(raw, "heisei") === normalized) return "heisei";
  if (formatJapaneseEraDate(raw, "reiwa") === normalized) return "reiwa";
  return "";
}

function saleNoteNeedsSymbol(entry: ReadingEntry) {
  if (
    !entry.path.startsWith("saleCalves.")
    || !entry.path.endsWith(".notes")
    || entry.value.status === "blank"
  ) return false;
  const value = entry.value.normalized ?? entry.value.raw;
  return Boolean(value && !SALE_NOTE_SEX_SYMBOLS.includes(value[0]));
}

const DOCUMENT_ORDER = ["unifiedEarTag", "cattleName", "birthDate", "registrationNumber", "coatColor", "breeder", "owner"];
const PEDIGREE_ORDER = ["father", "mother", "paternalGrandfather", "paternalGrandmother", "maternalGrandfather", "maternalGrandmother"];
const SALE_COLUMNS: ReviewColumn[] = [
  { key: "parity", label: "産次" },
  { key: "ageDays", label: "日齢" },
  { key: "weightKg", label: "体重" },
  { key: "priceYen", label: "価格" },
  { key: "sireName", label: "種雄牛名" },
  { key: "saleDate", label: "売却日" },
  { key: "notes", label: "備考" },
];
const CALVING_COLUMNS: ReviewColumn[] = [
  { key: "parity", label: "産次" },
  { key: "calvingDate", label: "分娩日" },
  { key: "sex", label: "性別" },
  { key: "outcome", label: "結果" },
  { key: "notes", label: "備考" },
];

export function ReviewPage({
  jobId,
  onClose,
  onNavigationGuardChange,
}: {
  jobId: string;
  onClose: () => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const [result, setResult] = useState<OcrResult | null>(null);
  const [draft, setDraft] = useState<OcrDocument | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [selectedMappingId, setSelectedMappingId] = useState<string | null>(null);
  const [previewRatio, setPreviewRatio] = useState(38);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [previewWarning, setPreviewWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revisionConflict, setRevisionConflict] = useState<RevisionConflictBackup | null>(null);
  const [sireCandidates, setSireCandidates] = useState<string[]>([]);
  const load = async () => {
    setSelectedPath(null);
    setSelectedMappingId(null);
    setPreviewWarning(null);
    const next = await api.result(jobId);
    const [artifactList, sirePool] = await Promise.all([
      api.artifacts(jobId).catch(() => null),
      api.sireNames().catch(() => null),
    ]);
    setResult(next);
    setDraft(next.result);
    setArtifacts(artifactList?.items ?? []);
    if (!artifactList) {
      setPreviewWarning("画像プレビューを取得できませんでした。文字の修正と保存は続けられます。");
    }
    if (sirePool && Array.isArray(sirePool.items)) {
      setSireCandidates(
        sirePool.items.flatMap((item) => [item.name, `${item.name}ET`]),
      );
    }
  };
  useEffect(() => {
    setResult(null);
    setDraft(null);
    setError(null);
    setLoadError(null);
    setRevisionConflict(null);
    void load().catch((reason) => {
      setLoadError(reason instanceof Error ? reason.message : "結果を取得できませんでした。");
    });
  }, [jobId]);
  useEffect(() => {
    if (!result && !revisionConflict) return;
    onNavigationGuardChange?.(Boolean((result && !result.confirmed) || revisionConflict));
    return () => onNavigationGuardChange?.(false);
  }, [onNavigationGuardChange, result?.confirmed, revisionConflict]);
  const previews = result?.fieldPreviews ?? [];
  const mappedPaths = useMemo(
    () => previews.length > 0
      ? new Set(previews.map((preview) => preview.targetPath))
      : null,
    [previews],
  );
  const reviewPaths = useMemo(() => {
    if (!mappedPaths || !result?.individualId) return mappedPaths;
    return new Set([...mappedPaths, "document.unifiedEarTag"]);
  }, [mappedPaths, result?.individualId]);
  const entries = useMemo(
    () => draft ? collectReadings(draft, reviewPaths) : [],
    [draft, reviewPaths],
  );
  const relevantReviewItems = draft?.reviewItems.filter(
    (item) => !reviewPaths
      || reviewPaths.has(item.path)
      || item.path === "processingMetadata",
  ) ?? [];
  const entryByPath = useMemo(
    () => new Map(entries.map((entry) => [entry.path, entry])),
    [entries],
  );
  const warningPaths = useMemo(
    () => new Set([
      ...entries
        .filter((entry) => (
          entry.value.status === "uncertain"
          || Boolean(entry.value.reviewReason)
          || saleNoteNeedsSymbol(entry)
        ))
        .map((entry) => entry.path),
      ...relevantReviewItems
        .filter((item) => item.path !== "processingMetadata")
        .map((item) => item.path),
    ]),
    [entries, relevantReviewItems],
  );
  const warningEntries = useMemo(
    () => entries.filter((entry) => warningPaths.has(entry.path)),
    [entries, warningPaths],
  );
  const byName = useMemo(() => new Map(artifacts.map((item) => [item.name, item.url])), [artifacts]);
  const previewByPath = useMemo(
    () => new Map(previews.map((preview) => [preview.targetPath, preview])),
    [previews],
  );
  const previewByMappingId = useMemo(
    () => new Map(previews.map((preview) => [preview.mappingId, preview])),
    [previews],
  );
  const selectedEntry = entries.find((entry) => entry.path === selectedPath);
  const selectedPreview = selectedMappingId
    ? previewByMappingId.get(selectedMappingId)
    : selectedPath
      ? previewByPath.get(selectedPath)
      : undefined;
  const legacyRegionId = previews.length === 0 ? selectedEntry?.value.sourceRegionId : undefined;
  const regionImage = selectedPreview
    ? byName.get(`fieldCrop.${selectedPreview.mappingId}.alignedColor`)
      ?? selectedPreview.artifactUrl
    : (legacyRegionId ? byName.get(`fieldCrop.${legacyRegionId}.alignedColor`) : undefined);
  const suggestedPath = entries[0]?.path ?? null;
  const documentEntries = orderedEntries(entryByPath, "document", DOCUMENT_ORDER);
  const pedigreeEntries = orderedEntries(entryByPath, "pedigree", PEDIGREE_ORDER);
  const customEntries = entries.filter((entry) => entry.path.startsWith("customFields."));
  const recordKeyByRow = useMemo(
    () => collectRecordKeys(result?.recordStructure),
    [result?.recordStructure],
  );
  const selectEntry = (entry: ReadingEntry) => {
    const preview = resolveFieldPreview(entry, previewByPath, previewByMappingId);
    setSelectedPath(entry.path);
    setSelectedMappingId(preview?.mappingId ?? null);
  };
  const selectNextWarning = () => {
    if (warningEntries.length === 0) return;
    const currentIndex = warningEntries.findIndex((entry) => entry.path === selectedPath);
    const next = warningEntries[(currentIndex + 1) % warningEntries.length];
    selectEntry(next);
    window.setTimeout(() => {
      const cell = Array.from(
        document.querySelectorAll<HTMLElement>("[data-review-path]"),
      ).find((element) => element.dataset.reviewPath === next.path);
      cell?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    }, 0);
  };
  const editingLocked = Boolean(result?.confirmed || revisionConflict);
  const replaceEntryText = (entry: ReadingEntry, text: string) => {
    if (!draft || editingLocked) return;
    const value = text.length > 0 ? text : null;
    if (entry.path === "document.birthDate") {
      const raw = sanitizeBirthDateInput(text) || null;
      setDraft(updateReading(
        draft,
        entry.path,
        raw
          ? {
            raw,
            normalized: null,
            status: "uncertain",
            reviewReason: birthDateParts(raw)
              ? "BIRTH_DATE_ERA_REQUIRED"
              : "BIRTH_DATE_NUMBERS_INVALID",
          }
          : { raw: null, normalized: null, status: "blank", reviewReason: null },
      ));
      return;
    }
    if (entry.path.startsWith("saleCalves.") && entry.path.endsWith(".notes")) {
      const valid = Boolean(value && SALE_NOTE_SEX_SYMBOLS.includes(value[0]));
      setDraft(updateReading(
        draft,
        entry.path,
        value
          ? {
            raw: value,
            normalized: valid ? value : null,
            status: valid ? "readable" : "uncertain",
            reviewReason: valid ? null : "SALE_NOTE_SEX_SYMBOL_UNRESOLVED",
          }
          : { raw: null, normalized: null, status: "blank", reviewReason: null },
      ));
      return;
    }
    setDraft(updateReading(
      draft,
      entry.path,
      value
        ? {
          raw: value,
          normalized: value,
          status: "readable",
          reviewReason: null,
        }
        : {
          raw: null,
          normalized: null,
          status: "blank",
          reviewReason: null,
        },
    ));
  };
  const handleMutationFailure = (
    reason: unknown,
    fallback: string,
    conflictResult: OcrResult | null = result,
    conflictDraft: OcrDocument | null = draft,
  ) => {
    if (
      reason instanceof ApiError
      && reason.code === "REVISION_CONFLICT"
      && conflictResult
      && conflictDraft
    ) {
      setRevisionConflict({
        draft: structuredClone(conflictDraft),
        baseRevision: conflictResult.revision,
        latestLoaded: false,
      });
      setError(null);
      setMessage(null);
      return;
    }
    setError(reason instanceof ApiError ? reason.message : fallback);
  };
  const reloadLatestAfterConflict = async () => {
    if (!revisionConflict) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const latest = await api.result(jobId);
      setResult(latest);
      setDraft(latest.result);
      setSelectedPath(null);
      setSelectedMappingId(null);
      setRevisionConflict((current) => current
        ? { ...current, latestLoaded: true }
        : current);
      setMessage(
        latest.confirmed
          ? "最新結果は別の画面で確定済みです。退避した入力は下で確認できます。"
          : `最新の revision ${latest.revision} を表示しました。退避した入力を復元して再編集できます。`,
      );
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "最新結果を取得できませんでした。");
    } finally {
      setBusy(false);
    }
  };
  const restoreConflictBackup = () => {
    if (!revisionConflict || !result || result.confirmed) return;
    setDraft(structuredClone(revisionConflict.draft));
    setRevisionConflict(null);
    setError(null);
    setMessage(
      `退避した入力を revision ${result.revision} 上へ復元しました。内容を確認して保存してください。`,
    );
  };
  const adoptLatestResult = () => {
    if (!revisionConflict) return;
    if (!window.confirm("退避した入力を破棄し、現在表示中の最新結果を採用します。よろしいですか？")) {
      return;
    }
    setRevisionConflict(null);
    setError(null);
    setMessage("最新結果を採用しました。");
  };
  const identityReady = hasConfirmableIndividualId(draft, result?.individualId);
  if (loadError) return <div className="empty-state large"><h2>結果を開けません</h2><p>{loadError}</p><button className="secondary" onClick={onClose}>戻る</button></div>;
  if (!result || !draft) return <div className="empty-state large">結果を読み込んでいます…</div>;
  const unresolved = new Set([
    ...entries
      .filter((entry) => (
        entry.value.status === "uncertain"
        || saleNoteNeedsSymbol(entry)
      ))
      .map((entry) => entry.path),
    ...relevantReviewItems
      .filter((item) => item.severity === "error")
      .map((item) => item.path),
  ]).size;
  return (
    <div className="page-stack review-page">
      <header className="page-header split"><div><span className="eyebrow">OCR REVIEW · REVISION {result.revision}</span><h1>読み取り結果を確認</h1><p>画像と項目を見比べ、原文と整形値を必要に応じて修正してください。</p></div><button className="ghost" onClick={onClose}>← ホームへ</button></header>
      <aside className="notice compact"><strong>使用テンプレート</strong><span>{result.result.templateId} · v{result.result.templateVersion}</span></aside>
      {result.provider === "mock" && <aside className="notice compact"><strong>試運転データ</strong><span>現在の候補は固定fixtureから作られています。</span></aside>}
      {result.provider === "openai" && <aside className="notice compact"><strong>{result.modelId ?? "OpenAI OCR"}</strong><span>入力 {result.inputTokens?.toLocaleString() ?? "-"} / 出力 {result.outputTokens?.toLocaleString() ?? "-"} tokens · API推定 {result.estimatedCost == null ? "計算不可" : `$${result.estimatedCost.toFixed(6)}`} · prompt {result.promptVersion ?? "-"}</span></aside>}
      {mappedPaths && <aside className="notice compact"><strong>意味づけ済み項目のみ表示</strong><span>テンプレートで設定された {mappedPaths.size} 項目{result.individualId ? "と、読み取り開始時に入力した個体識別番号" : ""}を表示しています。</span></aside>}
      {!identityReady && <aside className="notice external compact"><strong>個体識別番号を確認してください</strong><span>確定保存には、読み取り開始時に入力した10桁の個体識別番号が必要です。</span></aside>}
      {result.confirmed && result.individualId && <aside className="notice external compact"><strong>個体JSONへ保存済み</strong><span>個体識別番号 {result.individualId} で検索できます。</span></aside>}
      {previewWarning && <aside className="notice compact"><strong>画像表示のみ利用できません</strong><span>{previewWarning}</span></aside>}
      <div
        className="ocr-review-layout"
        style={{ "--preview-ratio": `${previewRatio}%` } as React.CSSProperties}
      >
        <aside className="source-pane">
          <div className="pane-title">
            <span>対応する画像</span>
            <div className="pane-title-controls">
              <small>{selectedPath ?? "帳票全体"}</small>
              <label>
                プレビュー幅
                <input
                  type="range"
                  min="30"
                  max="55"
                  value={previewRatio}
                  aria-label="プレビュー表示幅"
                  onChange={(event) => setPreviewRatio(Number(event.target.value))}
                />
              </label>
            </div>
          </div>
          <div className="source-image">
            {!selectedPath ? (
              <img src={byName.get("alignedSource")} alt="位置合わせ済み帳票" />
            ) : regionImage ? (
              <img key={`${selectedPath}:${selectedPreview?.mappingId ?? legacyRegionId}`} src={regionImage} alt={`${selectedPath} の切り出し`} />
            ) : (
              <div className="preview-missing">この項目に対応する矩形画像がありません。</div>
            )}
          </div>
          {selectedPreview?.boundingBox && <p className="preview-bounds">矩形: x {selectedPreview.boundingBox.x} / y {selectedPreview.boundingBox.y} / 幅 {selectedPreview.boundingBox.width} / 高さ {selectedPreview.boundingBox.height} · {selectedPreview.mappingId}</p>}
          <div className="source-switch"><button className={!selectedPath ? "active" : ""} onClick={() => { setSelectedPath(null); setSelectedMappingId(null); }}>帳票全体</button><button className={selectedPath ? "active" : ""} disabled={!suggestedPath} onClick={() => { const entry = suggestedPath ? entryByPath.get(suggestedPath) : undefined; if (entry) selectEntry(entry); }}>選択領域</button><button onClick={() => { const image = byName.get("handwritingOnly"); if (image) window.open(image, "_blank", "noopener,noreferrer"); }}>手書きのみ ↗</button></div>
          <div className="review-summary"><strong>{unresolved}</strong><span>要確認項目</span><p>「不確実」は空欄とは異なります。画像を見て判断してください。</p></div>
        </aside>
        <main className="field-pane">
          <div className="field-toolbar">
            <div className="ocr-cell-legend">
              <span><i className="legend-ok" />問題なし</span>
              <span><i className="legend-warning" />要確認</span>
            </div>
            <div className="field-toolbar-actions">
              {!result.confirmed && (
                <button
                  type="button"
                  className="secondary compact"
                  disabled={warningEntries.length === 0}
                  onClick={selectNextWarning}
                >
                  次の要確認（{warningEntries.length}件）
                </button>
              )}
              <span>{result.confirmed ? "確定済み" : `${entries.length}セルを編集中`}</span>
            </div>
          </div>
          {selectedEntry && (
            <CellEditor
              entry={selectedEntry}
              confirmed={editingLocked}
              warning={warningPaths.has(selectedEntry.path)}
              suggestedValues={
                selectedEntry.path.endsWith(".sireName")
                  ? sireCandidates
                  : selectedEntry.path.startsWith("saleCalves.")
                    && selectedEntry.path.endsWith(".notes")
                    ? SALE_NOTE_SEX_SYMBOLS
                    : []
              }
              onChange={(patch) => setDraft(updateReading(draft, selectedEntry.path, patch))}
              onClose={() => { setSelectedPath(null); setSelectedMappingId(null); }}
            />
          )}
          {entries.length === 0 ? (
            <div className="empty-state">テンプレートで意味づけされた読み取りセルがありません。</div>
          ) : (
            <div className="ocr-edit-tables">
              {documentEntries.length > 0 && <ReviewTableSection title="基本情報">
                <KeyValueReviewTable
                  entries={documentEntries}
                  selectedPath={selectedPath}
                  warningPaths={warningPaths}
                  confirmed={editingLocked}
                  onSelect={selectEntry}
                  onEdit={replaceEntryText}
                />
              </ReviewTableSection>}
              {pedigreeEntries.length > 0 && <ReviewTableSection title="血統">
                <KeyValueReviewTable
                  entries={pedigreeEntries}
                  selectedPath={selectedPath}
                  warningPaths={warningPaths}
                  confirmed={editingLocked}
                  onSelect={selectEntry}
                  onEdit={replaceEntryText}
                />
              </ReviewTableSection>}
              <CollectionReviewTable
                title="売却子牛一覧"
                root="saleCalves"
                rowCount={draft.saleCalves.length}
                columns={SALE_COLUMNS}
                entryByPath={entryByPath}
                selectedPath={selectedPath}
                warningPaths={warningPaths}
                recordKeyByRow={recordKeyByRow}
                confirmed={editingLocked}
                onSelect={selectEntry}
                onEdit={replaceEntryText}
              />
              <CollectionReviewTable
                title="繁殖履歴"
                root="calvings"
                rowCount={draft.calvings.length}
                columns={CALVING_COLUMNS}
                entryByPath={entryByPath}
                selectedPath={selectedPath}
                warningPaths={warningPaths}
                recordKeyByRow={recordKeyByRow}
                confirmed={editingLocked}
                onSelect={selectEntry}
                onEdit={replaceEntryText}
              />
              {customEntries.length > 0 && <ReviewTableSection title="追加項目">
                <KeyValueReviewTable
                  entries={customEntries}
                  selectedPath={selectedPath}
                  warningPaths={warningPaths}
                  confirmed={editingLocked}
                  onSelect={selectEntry}
                  onEdit={replaceEntryText}
                />
              </ReviewTableSection>}
            </div>
          )}
        </main>
      </div>
      {revisionConflict && (
        <section className="form-card revision-conflict-recovery" aria-live="assertive">
          <div>
            <span className="eyebrow">入力を退避しました</span>
            <h2>別の画面で修正が保存されました</h2>
            <p>
              revision {revisionConflict.baseRevision} を基にした入力内容は、この画面に退避しています。
              勝手に破棄しません。
            </p>
          </div>
          <details>
            <summary>退避した入力内容を表示</summary>
            <pre className="json-viewer">{JSON.stringify(revisionConflict.draft, null, 2)}</pre>
          </details>
          {!revisionConflict.latestLoaded ? (
            <div className="job-action-buttons">
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => { void reloadLatestAfterConflict(); }}
              >
                最新結果を再取得
              </button>
            </div>
          ) : (
            <>
              {result.confirmed && (
                <p className="error-box">
                  最新結果は確定済みのため、この画面から退避内容を再保存できません。
                </p>
              )}
              <div className="job-action-buttons">
                <button
                  type="button"
                  className="primary"
                  disabled={busy || result.confirmed}
                  onClick={restoreConflictBackup}
                >
                  退避した入力を復元して再編集
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={adoptLatestResult}
                >
                  最新結果を採用
                </button>
              </div>
            </>
          )}
        </section>
      )}
      {error && <p className="error-box">{error}</p>}{message && <p className="success-box">{message}</p>}
      <div className="decision-bar sticky"><div><strong>{result.confirmed ? "確定済みの結果です" : revisionConflict ? "競合の復旧方法を選んでください" : !identityReady ? "個体識別番号を確認してください" : `${unresolved}件の要確認項目があります`}</strong><span>{result.confirmed ? "JSONまたはCSVを保存できます。" : revisionConflict ? "上の復旧欄で最新結果を取得し、退避内容を復元するか選択してください。" : !identityReady ? "10桁の個体識別番号を入力すると確定保存できます。" : "修正を保存してから確定してください。"}</span></div>{result.confirmed ? <><a className="secondary button-link" href={exportUrl(jobId, "json")}>JSON出力</a><a className="primary button-link" href={exportUrl(jobId, "csv")}>CSV出力</a></> : <><button className="secondary" disabled={busy || Boolean(revisionConflict)} onClick={async () => { setBusy(true); setError(null); setMessage(null); try { const saved = await api.correct(jobId, result.revision, draft); setResult(saved); setDraft(saved.result); setRevisionConflict(null); setMessage("修正を保存しました。"); } catch (reason) { handleMutationFailure(reason, "保存できませんでした。"); } finally { setBusy(false); } }}>修正を保存</button><button className="primary" disabled={busy || Boolean(revisionConflict) || unresolved > 0 || !identityReady} onClick={async () => { if (!window.confirm("この内容を確定します。確定後は修正できません。よろしいですか？")) return; setBusy(true); setError(null); setMessage(null); let latest = result; try { if (JSON.stringify(draft) !== JSON.stringify(result.result)) { latest = await api.correct(jobId, result.revision, draft); setResult(latest); setDraft(latest.result); } const confirmed = await api.confirm(jobId, latest.revision); setResult(confirmed); setDraft(confirmed.result); setRevisionConflict(null); setMessage("結果を確定しました。"); } catch (reason) { handleMutationFailure(reason, "確定できませんでした。", latest, draft); } finally { setBusy(false); } }}>内容を確定</button></>}</div>
    </div>
  );
}

function CellEditor({
  entry,
  confirmed,
  warning,
  suggestedValues,
  onChange,
  onClose,
}: {
  entry: ReadingEntry;
  confirmed: boolean;
  warning: boolean;
  suggestedValues: string[];
  onChange: (patch: Partial<FieldReading>) => void;
  onClose: () => void;
}) {
  const isBirthDate = entry.path === "document.birthDate";
  const isSaleNote = entry.path.startsWith("saleCalves.")
    && entry.path.endsWith(".notes");
  const selectedEra = inferJapaneseEra(
    entry.value.raw,
    entry.value.normalized,
  );
  return (
    <section className={`ocr-cell-editor ${warning ? "editor-warning" : "editor-ok"}`} aria-label="選択セルの編集">
      <header>
        <div>
          <strong>{entry.label}</strong>
          <small>{entry.path} · cell {entry.value.sourceRegionId}</small>
        </div>
        <span>{warning ? "要確認" : "問題なし"}</span>
        <button className="ghost compact" type="button" onClick={onClose}>閉じる</button>
      </header>
      {isBirthDate ? (
        <div className="ocr-cell-editor-fields birth-date-editor-fields">
          <label>
            元号年・月・日（数字のみ）
            <input
              autoFocus
              disabled={confirmed}
              inputMode="numeric"
              value={sanitizeBirthDateInput(entry.value.raw ?? "")}
              placeholder="例: 26 9 27"
              onChange={(event) => {
                const raw = sanitizeBirthDateInput(event.target.value) || null;
                onChange(raw
                  ? {
                    raw,
                    normalized: null,
                    status: "uncertain",
                    reviewReason: birthDateParts(raw)
                      ? "BIRTH_DATE_ERA_REQUIRED"
                      : "BIRTH_DATE_NUMBERS_INVALID",
                  }
                  : {
                    raw: null,
                    normalized: null,
                    status: "blank",
                    reviewReason: null,
                  });
              }}
            />
          </label>
          <label>
            元号
            <select
              disabled={confirmed || !birthDateParts(entry.value.raw)}
              value={selectedEra}
              onChange={(event) => {
                const era = event.target.value as JapaneseEra;
                if (!era) {
                  onChange({
                    normalized: null,
                    status: "uncertain",
                    reviewReason: "BIRTH_DATE_ERA_REQUIRED",
                  });
                  return;
                }
                const normalized = formatJapaneseEraDate(entry.value.raw, era);
                onChange(normalized
                  ? {
                    raw: sanitizeBirthDateInput(entry.value.raw ?? ""),
                    normalized,
                    status: "readable",
                    reviewReason: null,
                  }
                  : {
                    normalized: null,
                    status: "uncertain",
                    reviewReason: "BIRTH_DATE_NUMBERS_INVALID",
                  });
              }}
            >
              <option value="">選択してください</option>
              <option value="heisei">平成</option>
              <option value="reiwa">令和</option>
            </select>
          </label>
          <label>
            西暦表記
            <input
              readOnly
              value={entry.value.normalized ?? ""}
              placeholder="元号を選択してください"
            />
          </label>
        </div>
      ) : (
      <div className="ocr-cell-editor-fields">
        <label>
          原文
          <input
            autoFocus
            disabled={confirmed}
            value={entry.value.raw ?? ""}
            placeholder={entry.value.status === "blank" ? "空欄" : "画像どおり入力"}
            onChange={(event) => {
              const raw = event.target.value || null;
              if (isSaleNote) {
                const valid = Boolean(raw && SALE_NOTE_SEX_SYMBOLS.includes(raw[0]));
                onChange(raw
                  ? {
                    raw,
                    normalized: valid ? raw : null,
                    status: valid ? "readable" : "uncertain",
                    reviewReason: valid
                      ? null
                      : "SALE_NOTE_SEX_SYMBOL_UNRESOLVED",
                  }
                  : { raw: null, normalized: null, status: "blank", reviewReason: null });
                return;
              }
              const normalizedTracksRaw = (
                entry.value.normalized === null
                || entry.value.normalized === entry.value.raw
              );
              onChange(raw
                ? {
                  raw,
                  ...(normalizedTracksRaw ? { normalized: raw } : {}),
                  status: entry.value.status === "blank" ? "readable" : entry.value.status,
                }
                : { raw: null, normalized: null, status: "blank" });
            }}
          />
        </label>
        <label>
          整形値
          <input
            disabled={confirmed || entry.value.status !== "readable"}
            value={entry.value.normalized ?? ""}
            placeholder="確定保存する値"
            onChange={(event) => onChange({ normalized: event.target.value || null })}
          />
        </label>
        <label>
          判定
          <select
            disabled={confirmed}
            value={entry.value.status}
            onChange={(event) => {
              const status = event.target.value as FieldReading["status"];
              onChange(status === "blank"
                ? { status, raw: null, normalized: null, reviewReason: null }
                : status === "uncertain"
                  ? { status, normalized: null, raw: entry.value.raw ?? "" }
                  : { status, raw: entry.value.raw ?? "", reviewReason: null });
            }}
          >
            <option value="readable">読めた</option>
            <option value="uncertain">不確実</option>
            <option value="blank">空欄</option>
          </select>
        </label>
      </div>
      )}
      {Array.from(new Set([...entry.value.alternatives, ...suggestedValues])).length > 0 && (
        <div className="ocr-cell-alternatives">
          <span>{suggestedValues.length > 0 ? "登録候補:" : "候補:"}</span>
          {Array.from(new Set([...entry.value.alternatives, ...suggestedValues])).map((value) => (
            <button
              disabled={confirmed}
              key={value}
              onClick={() => {
                const selected = isSaleNote && SALE_NOTE_SEX_SYMBOLS.includes(value)
                  ? `${value}${(entry.value.raw ?? "").slice(1)}`
                  : value;
                onChange({
                  raw: selected,
                  normalized: selected,
                  status: "readable",
                  reviewReason: null,
                });
              }}
            >
              {value}
            </button>
          ))}
        </div>
      )}
      {entry.value.reviewReason && <p className="review-reason">確認理由: {reviewReasonLabel(entry.value.reviewReason)}</p>}
    </section>
  );
}

function ReviewTableSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  if (!children) return null;
  return (
    <section className="ocr-edit-section">
      <header><h3>{title}</h3></header>
      {children}
    </section>
  );
}

function KeyValueReviewTable({
  entries,
  selectedPath,
  warningPaths,
  confirmed,
  onSelect,
  onEdit,
}: {
  entries: ReadingEntry[];
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  if (entries.length === 0) return null;
  const rows = chunk(entries, 2);
  return (
    <div className="ocr-edit-table-wrap">
      <table className="ocr-edit-table ocr-key-value-table">
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((entry) => (
                <ReviewLabelAndCell
                  key={entry.path}
                  entry={entry}
                  selected={selectedPath === entry.path}
                  warning={warningPaths.has(entry.path)}
                  confirmed={confirmed}
                  onSelect={onSelect}
                  onEdit={onEdit}
                />
              ))}
              {row.length === 1 && <><th aria-hidden="true" /><td className="ocr-empty-cell" /></>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewLabelAndCell({
  entry,
  selected,
  warning,
  confirmed,
  onSelect,
  onEdit,
}: {
  entry: ReadingEntry;
  selected: boolean;
  warning: boolean;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  return (
    <>
      <th scope="row">{entry.label}</th>
      <ReviewValueCell
        entry={entry}
        selected={selected}
        warning={warning}
        confirmed={confirmed}
        onSelect={onSelect}
        onEdit={onEdit}
      />
    </>
  );
}

function CollectionReviewTable({
  title,
  root,
  rowCount,
  columns,
  entryByPath,
  selectedPath,
  warningPaths,
  recordKeyByRow,
  confirmed,
  onSelect,
  onEdit,
}: {
  title: string;
  root: string;
  rowCount: number;
  columns: ReviewColumn[];
  entryByPath: ReadonlyMap<string, ReadingEntry>;
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  recordKeyByRow: ReadonlyMap<string, string>;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  const visibleColumns = columns.filter((column) => (
    Array.from({ length: rowCount }, (_, index) => `${root}.${index}.${column.key}`)
      .some((path) => entryByPath.has(path))
  ));
  const visibleRows = Array.from({ length: rowCount }, (_, index) => index)
    .filter((index) => visibleColumns.some((column) => entryByPath.has(`${root}.${index}.${column.key}`)));
  if (visibleColumns.length === 0 || visibleRows.length === 0) return null;
  return (
    <ReviewTableSection title={title}>
      <div className="ocr-edit-table-wrap">
        <table className="ocr-edit-table ocr-collection-table">
          <thead>
            <tr><th>レコード</th>{visibleColumns.map((column) => <th key={column.key}>{column.label}</th>)}</tr>
          </thead>
          <tbody>
            {visibleRows.map((index) => (
              <tr key={index}>
                <th scope="row">{recordKeyByRow.get(`${root}.${index}`) ?? index + 1}産次</th>
                {visibleColumns.map((column) => {
                  const entry = entryByPath.get(`${root}.${index}.${column.key}`);
                  return entry ? (
                    <ReviewValueCell
                      key={column.key}
                      entry={entry}
                      selected={selectedPath === entry.path}
                      warning={warningPaths.has(entry.path)}
                      confirmed={confirmed}
                      onSelect={onSelect}
                      onEdit={onEdit}
                    />
                  ) : <td className="ocr-unmapped-cell" key={column.key}>—</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ReviewTableSection>
  );
}

function collectRecordKeys(
  structure: OcrResult["recordStructure"],
): Map<string, string> {
  const keys = new Map<string, string>();
  if (!structure) return keys;
  const collectionBySection = new Map(
    structure.sections.map((section) => [section.id, section.collectionPath]),
  );
  structure.groups.forEach((group) => {
    const collection = collectionBySection.get(group.sectionId);
    const value = group.recordKeyValue?.trim();
    if (collection && value) {
      keys.set(`${collection}.${group.order - 1}`, value);
    }
  });
  return keys;
}

function ReviewValueCell({
  entry,
  selected,
  warning,
  confirmed,
  onSelect,
  onEdit,
}: {
  entry: ReadingEntry;
  selected: boolean;
  warning: boolean;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  const value = entry.value.normalized ?? entry.value.raw;
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(value ?? "");
  useEffect(() => {
    if (!editing) setEditValue(value ?? "");
  }, [editing, value]);
  const commit = () => {
    if (!editing) return;
    setEditing(false);
    onEdit(entry, editValue);
  };
  return (
    <td
      className={`${warning ? "ocr-cell-warning" : "ocr-cell-ok"} ${selected ? "ocr-cell-selected" : ""}`}
      data-review-path={entry.path}
    >
      {editing ? (
        <input
          className="ocr-inline-cell-input"
          autoFocus
          aria-label={`${entry.label} ${entry.path} を直接編集`}
          value={editValue}
          onChange={(event) => setEditValue(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commit();
            }
          }}
        />
      ) : (
        <button
          type="button"
          aria-label={`${entry.label} ${entry.path} を編集`}
          title={confirmed ? undefined : "ダブルクリックで直接編集"}
          onClick={() => onSelect(entry)}
          onDoubleClick={(event) => {
            if (confirmed) return;
            event.preventDefault();
            onSelect(entry);
            setEditValue(value ?? "");
            setEditing(true);
          }}
        >
          <span>{value || (entry.value.status === "blank" ? "空欄" : "未入力")}</span>
          <small>{warning ? "要確認" : "OK"}</small>
        </button>
      )}
    </td>
  );
}

function orderedEntries(
  entryByPath: ReadonlyMap<string, ReadingEntry>,
  root: string,
  keys: string[],
) {
  return keys
    .map((key) => entryByPath.get(`${root}.${key}`))
    .filter((entry): entry is ReadingEntry => Boolean(entry));
}

function chunk<T>(items: T[], size: number): T[][] {
  const rows: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    rows.push(items.slice(index, index + size));
  }
  return rows;
}

function resolveFieldPreview(
  entry: ReadingEntry,
  previewByPath: ReadonlyMap<string, OcrFieldPreview>,
  previewByMappingId: ReadonlyMap<string, OcrFieldPreview>,
): OcrFieldPreview | undefined {
  const pathPreview = previewByPath.get(entry.path);
  const sourcePreview = previewByMappingId.get(entry.value.sourceRegionId);
  // sourceRegionId is written from the exact crop used for this reading.
  // A JSON path can change when record rows are regrouped, so it must only be
  // a fallback for legacy results that do not carry a usable source cell ID.
  return sourcePreview ?? pathPreview;
}

function hasConfirmableIndividualId(
  document: OcrDocument | null,
  fallbackIndividualId: string | null | undefined,
): boolean {
  const reading = document?.document.unifiedEarTag;
  if (reading) {
    if (reading.status !== "readable") return false;
    return isIndividualId(reading.normalized ?? reading.raw);
  }
  return isIndividualId(fallbackIndividualId);
}

function isIndividualId(value: string | null | undefined): boolean {
  if (!value) return false;
  return /^\d{10}$/.test(value.replace(/[\s‐‑‒–—―ー－-]/g, ""));
}

function collectReadings(
  document: OcrDocument,
  mappedPaths: ReadonlySet<string> | null,
): ReadingEntry[] {
  const roots: Array<[string, unknown]> = [["document", document.document], ["saleCalves", document.saleCalves], ["pedigree", document.pedigree], ["calvings", document.calvings], ["customFields", document.customFields ?? {}]];
  const entries: ReadingEntry[] = [];
  const visit = (value: unknown, path: string) => {
    if (!value || typeof value !== "object") return;
    if ("status" in value && "sourceRegionId" in value) {
      if (!mappedPaths || mappedPaths.has(path)) {
        entries.push({ path, label: fieldLabel(path), value: value as FieldReading });
      }
      return;
    }
    Object.entries(value).forEach(([key, child]) => visit(child, `${path}.${key}`));
  };
  roots.forEach(([path, value]) => visit(value, path));
  return entries;
}

function updateReading(document: OcrDocument, path: string, patch: Partial<FieldReading>): OcrDocument {
  const next = structuredClone(document);
  const keys = path.split(".");
  let target: unknown = next;
  for (const key of keys) target = Array.isArray(target) ? target[Number(key)] : (target as Record<string, unknown>)[key];
  Object.assign(target as FieldReading, patch);
  next.reviewItems = next.reviewItems.filter((item) => item.path !== path);
  return next;
}

function fieldLabel(path: string) {
  const key = path.split(".").at(-1) ?? path;
  return ({ unifiedEarTag: "統一耳標番号", cattleName: "牛名", birthDate: "生年月日", registrationNumber: "登録番号", coatColor: "毛色", breeder: "繁殖者", owner: "所有者", father: "父", mother: "母", paternalGrandfather: "父方祖父", paternalGrandmother: "父方祖母", maternalGrandfather: "母方祖父", maternalGrandmother: "母方祖母", parity: "産次", calvingDate: "分娩日", sex: "性別", outcome: "結果", notes: "備考", ageDays: "日齢", weightKg: "体重", priceYen: "価格", sireName: "種雄牛名", saleDate: "売却日" } as Record<string, string>)[key] ?? key;
}

function reviewReasonLabel(reason: string) {
  return ({
    BIRTH_DATE_ERA_REQUIRED: "元号（平成または令和）を選択してください。",
    BIRTH_DATE_NUMBERS_INVALID: "元号年・月・日の3つの数字を確認してください。",
    SALE_NOTE_SEX_SYMBOL_UNRESOLVED: "備考の先頭を♂または♀から選択してください。",
    SIRE_NAME_CANDIDATE_UNRESOLVED: "登録済みの種雄牛名と一致しません。画像と候補を確認してください。",
    DITTO_SOURCE_UNAVAILABLE: "同上マークの上段に確定した値がありません。",
    MISSING_TRANSCRIPTION: "この欄の読み取り結果が返されませんでした。",
    DITTO_PENDING: "同上マークの参照先を確認しています。",
  } as Record<string, string>)[reason] ?? reason;
}
