import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api";
import { PagedReviewPage } from "./PagedReviewPage";
import { ExcelExportActions } from "../components/ExcelExportActions";
import {
  AnchoredReviewPopover,
  buildReviewDisplayModel,
  CellStatusPopover,
  GenericRecordReviewTable,
  IndependentReviewGroups,
  KeyValueReviewTable,
  ReviewResultsToolbar,
  ReviewTableSection,
  ReviewValueCell,
  type ReadingEntry,
  type ReviewDisplayRecordSection,
  type ReviewInteractionMode,
} from "../components/OcrReviewResults";
import {
  automaticJapaneseEraDatePatch,
  formatJapaneseEraDate,
  inferJapaneseEra,
  japaneseEraDateParts,
  normalizeJapaneseDateDisplay,
  sanitizeJapaneseEraDateInput,
} from "../japaneseDates";
import type { JapaneseEra } from "../japaneseDates";
import {
  navigateToSpreadsheet,
  type SpreadsheetNavigator,
} from "../spreadsheetNavigation";
import type {
  Artifact,
  FieldReading,
  OcrDocument,
  OcrFieldPreview,
  OcrResult,
} from "../types";

export { buildReviewDisplayModel, positionReviewPopover } from "../components/OcrReviewResults";

const LEGACY_DOCUMENT_NAME = "__legacy_document_name__";

type ReviewColumn = { key: string; label: string };
type CollectionRoot = "saleCalves" | "calvings";
type RevisionConflictBackup = {
  draft: OcrDocument;
  baseRevision: number;
  latestLoaded: boolean;
};
const SALE_NOTE_SEX_SYMBOLS = ["♂", "♀"];

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

function isJapaneseEraDatePath(path: string) {
  return path === "document.birthDate"
    || /^calvings\.\d+\.calvingDate$/.test(path)
    || /^saleCalves\.\d+\.saleDate$/.test(path);
}

function normalizeReviewDateReadings(document: OcrDocument) {
  let updated = document;
  for (const entry of collectReadings(document, null)) {
    if (
      !isJapaneseEraDatePath(entry.path)
      || entry.value.status === "blank"
    ) continue;
    const normalized = normalizeJapaneseDateDisplay(
      entry.value.raw,
      entry.value.normalized,
    );
    if (!normalized || entry.value.normalized === normalized) continue;
    updated = updateReading(updated, entry.path, {
      raw: sanitizeJapaneseEraDateInput(
        entry.value.raw ?? entry.value.normalized ?? "",
      ),
      normalized,
      status: "readable",
      reviewReason: null,
    });
  }
  return updated;
}

function normalizeIndividualIdInput(value: string) {
  return value
    .normalize("NFKC")
    .replace(/\D/g, "")
    .slice(0, 10);
}

function readingText(reading: FieldReading | undefined) {
  return reading?.normalized ?? reading?.raw ?? "";
}

function IdentityConfirmField({
  label,
  prompt,
  value,
  imageUrl,
  imageAlt,
  help,
  inputMode,
  maxLength,
  invalid = false,
  onChange,
}: {
  label: string;
  prompt: string;
  value: string;
  imageUrl?: string;
  imageAlt: string;
  help: string;
  inputMode?: "text" | "numeric";
  maxLength?: number;
  invalid?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <article className={`identity-confirm-card ${invalid ? "invalid" : ""}`}>
      <header>
        <span>{label}</span>
        <h2>{prompt}</h2>
      </header>
      <div className="identity-confirm-preview">
        {imageUrl
          ? <img src={imageUrl} alt={imageAlt} />
          : <span>この項目の切り出し画像がありません</span>}
      </div>
      <label className="field-label">
        検出結果
        <input
          aria-label={label}
          value={value}
          inputMode={inputMode}
          maxLength={maxLength}
          autoComplete="off"
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
        <small>{help}</small>
      </label>
    </article>
  );
}

export function ReviewPage({
  jobId,
  onClose,
  onNavigationGuardChange,
  requireIdentityConfirmation = false,
  initialSelectedPath = null,
  autoReopenConfirmed = false,
  onSpreadsheetNavigate = navigateToSpreadsheet,
}: {
  jobId: string;
  onClose: () => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
  requireIdentityConfirmation?: boolean;
  initialSelectedPath?: string | null;
  autoReopenConfirmed?: boolean;
  onSpreadsheetNavigate?: SpreadsheetNavigator;
}) {
  const [result, setResult] = useState<OcrResult | null>(null);
  const [pagedJobId, setPagedJobId] = useState<string | null>(null);
  const [draft, setDraft] = useState<OcrDocument | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [lastSelectedPath, setLastSelectedPath] = useState<string | null>(null);
  const [selectedMappingId, setSelectedMappingId] = useState<string | null>(null);
  const [previewRatio, setPreviewRatio] = useState(38);
  const [interactionMode, setInteractionMode] = useState<ReviewInteractionMode>("edit");
  const [markEditorRow, setMarkEditorRow] = useState<number | null>(null);
  const [choiceEditorPath, setChoiceEditorPath] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [previewWarning, setPreviewWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revisionConflict, setRevisionConflict] = useState<RevisionConflictBackup | null>(null);
  const [sireCandidates, setSireCandidates] = useState<string[]>([]);
  const [identityStepComplete, setIdentityStepComplete] = useState(false);
  const [comparisonPageByDocument, setComparisonPageByDocument] = useState<Record<string, string>>({});
  const [documentName, setDocumentName] = useState("");
  const load = async () => {
    setSelectedPath(null);
    setLastSelectedPath(null);
    setSelectedMappingId(null);
    setMarkEditorRow(null);
    setChoiceEditorPath(null);
    setPreviewWarning(null);
    let next = await api.result(jobId);
    if (autoReopenConfirmed && next.confirmed) {
      next = await api.reopenResult(jobId);
    }
    const [artifactList, sirePool] = await Promise.all([
      api.artifacts(jobId).catch(() => null),
      api.sireNames().catch(() => null),
    ]);
    const hydrated = normalizeReviewDateReadings(
      hydrateMappedReadings(next.result, next.fieldPreviews),
    );
    setResult(next);
    setDraft(hydrated);
    setDocumentName(next.documentName ?? LEGACY_DOCUMENT_NAME);
    const initialEntry = initialSelectedPath
      ? collectReadings(hydrated, null).find((entry) => entry.path === initialSelectedPath)
      : undefined;
    if (initialEntry) {
      const preview = next.fieldPreviews.find(
        (item) => item.targetPath === initialEntry.path
          || item.mappingId === initialEntry.value.sourceRegionId,
      );
      setSelectedPath(initialEntry.path);
      setLastSelectedPath(initialEntry.path);
      setSelectedMappingId(preview?.mappingId ?? null);
    }
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
    setPagedJobId(null);
    setDraft(null);
    setError(null);
    setLoadError(null);
    setRevisionConflict(null);
    setInteractionMode("edit");
    setMarkEditorRow(null);
    setIdentityStepComplete(false);
    setComparisonPageByDocument({});
    setConfirming(false);
    setDocumentName("");
    void load().catch((reason) => {
      if (reason instanceof ApiError && reason.code === "MULTI_PAGE_REVIEW_NOT_AVAILABLE") {
        setPagedJobId(jobId);
        return;
      }
      setLoadError(reason instanceof Error ? reason.message : "結果を取得できませんでした。");
    });
  }, [autoReopenConfirmed, jobId, initialSelectedPath]);
  const previews = result?.fieldPreviews ?? [];
  const isCattleReview =
    previews.length === 0 ||
    previews.some(
      ({ targetPath }) =>
        targetPath.startsWith("document.") ||
        targetPath.startsWith("pedigree.") ||
        targetPath.startsWith("saleCalves.") ||
        targetPath.startsWith("calvings."),
    );
  useEffect(() => {
    if (
      !selectedPath ||
      (isCattleReview && requireIdentityConfirmation && !identityStepComplete && !result?.confirmed)
    ) {
      return;
    }
    window.setTimeout(() => {
      const cell = Array.from(
        document.querySelectorAll<HTMLElement>("[data-review-path]"),
      ).find((element) => element.dataset.reviewPath === selectedPath);
      cell?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    }, 0);
  }, [identityStepComplete, isCattleReview, requireIdentityConfirmation, result?.confirmed, selectedPath]);
  useEffect(() => {
    if (!result && !revisionConflict) return;
    onNavigationGuardChange?.(Boolean((result && !result.confirmed) || revisionConflict));
    return () => onNavigationGuardChange?.(false);
  }, [onNavigationGuardChange, result?.confirmed, revisionConflict]);
  const mappedPaths = useMemo(
    () => previews.length > 0
      ? new Set(previews.map((preview) => preview.targetPath))
      : null,
    [previews],
  );
  const reviewPaths = useMemo(() => {
    if (!mappedPaths || !result?.individualId || !isCattleReview) return mappedPaths;
    return new Set([...mappedPaths, "document.unifiedEarTag"]);
  }, [isCattleReview, mappedPaths, result?.individualId]);
  const choicePaths = useMemo(
    () => new Set(
      previews
        .filter((preview) => preview.mappingType === "mark")
        .map((preview) => preview.targetPath),
    ),
    [previews],
  );
  const entries = useMemo(
    () => draft ? collectReadings(draft, reviewPaths) : [],
    [draft, reviewPaths],
  );
  const reviewCompleted = Boolean(result?.confirmed);
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
  const displayedWarningPaths = useMemo<ReadonlySet<string>>(
    () => reviewCompleted ? new Set<string>() : warningPaths,
    [reviewCompleted, warningPaths],
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
  const suggestedPath = lastSelectedPath && entryByPath.has(lastSelectedPath)
    ? lastSelectedPath
    : entries[0]?.path ?? null;
  const handlePreviewLoadError = () => {
    setPreviewWarning(
      "保存された画像ファイルが見つかりません。DBを別PCから移した旧データは、元画像の再登録が必要です。",
    );
  };
  const documentEntries = orderedEntries(entryByPath, "document", DOCUMENT_ORDER);
  const pedigreeEntries = orderedEntries(entryByPath, "pedigree", PEDIGREE_ORDER);
  const reviewDisplay = useMemo(
    () => buildReviewDisplayModel(entries, previews, result?.reviewStructure),
    [entries, previews, result?.reviewStructure],
  );
  const isStructuredEntry = (entry: ReadingEntry) => (
    !isCattleReview || entry.path.startsWith("customFields.")
  );
  const structuredRecordSections = reviewDisplay.recordSections
    .filter((section) => section.collectionPath.startsWith("genericRecords."))
    .map((section) => ({
      ...section,
      rows: section.rows.map((row) => ({
        ...row,
        cells: row.cells.map((cell) => ({
          ...cell,
          entry: cell.entry && isStructuredEntry(cell.entry) ? cell.entry : null,
        })),
      })),
    }))
    .filter((section) => section.rows.some(
      (row) => row.cells.some((cell) => cell.entry !== null),
    ));
  const parentSections = reviewDisplay.parentSections
    .map((section) => ({
      ...section,
      entries: section.entries.filter(isStructuredEntry),
    }))
    .filter((section) => section.entries.length > 0);
  const independentEntries = reviewDisplay.independentEntries.filter(isStructuredEntry);
  const independentGroups = reviewDisplay.independentGroups
    .map((group) => ({
      ...group,
      entries: group.entries.filter(isStructuredEntry),
    }))
    .filter((group) => group.entries.length > 0);
  const recordKeyByRow = useMemo(
    () => collectRecordKeys(result?.recordStructure),
    [result?.recordStructure],
  );
  const selectEntry = (entry: ReadingEntry) => {
    const preview = resolveFieldPreview(entry, previewByPath, previewByMappingId);
    setSelectedPath(entry.path);
    setLastSelectedPath(entry.path);
    setSelectedMappingId(preview?.mappingId ?? null);
    const markMatch = preview?.mappingType === "mark"
      && (preview.markRecognitionMode ?? "automatic") === "automatic"
      ? /^calvings\.(\d+)\.(?:outcome|sex)$/.exec(entry.path)
      : null;
    setMarkEditorRow(markMatch ? Number(markMatch[1]) : null);
    setChoiceEditorPath(
      preview?.mappingType === "mark" && !markMatch ? entry.path : null,
    );
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
  const closeSelectedCell = () => {
    setSelectedPath(null);
    setSelectedMappingId(null);
    setMarkEditorRow(null);
    setChoiceEditorPath(null);
  };
  const canMoveCollectionRow = (
    root: CollectionRoot,
    fromIndex: number,
    toIndex: number,
  ) => Boolean(draft && canSwapCollectionRows(draft, root, fromIndex, toIndex));
  const moveCollectionRow = (
    root: CollectionRoot,
    fromIndex: number,
    toIndex: number,
  ) => {
    if (!draft || editingLocked || interactionMode !== "reorder") return;
    const next = swapCollectionRowsAtFixedRegions(draft, root, fromIndex, toIndex);
    if (next === draft) return;
    setDraft(next);
    closeSelectedCell();
    setError(null);
    setMessage(`${fromIndex + 1}行目を${toIndex + 1}行目と交換しました。`);
  };
  const moveGenericRow = (
    section: ReviewDisplayRecordSection,
    fromIndex: number,
    toIndex: number,
  ) => {
    if (!draft || editingLocked || interactionMode !== "reorder" || fromIndex === toIndex) return;
    const next = swapGenericRecordRowsAtFixedRegions(draft, section, fromIndex, toIndex);
    if (next === draft) return;
    setDraft(next);
    closeSelectedCell();
    setError(null);
    setMessage(`${section.title}の${fromIndex + 1}行目を${toIndex + 1}行目と交換しました。`);
  };
  const replaceEntryText = (entry: ReadingEntry, text: string) => {
    if (!draft || editingLocked) return;
    const value = text.length > 0 ? text : null;
    if (isJapaneseEraDatePath(entry.path)) {
      const raw = sanitizeJapaneseEraDateInput(text) || null;
      setDraft(updateReading(
        draft,
        entry.path,
        raw
          ? automaticJapaneseEraDatePatch(raw)
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
      setDraft(hydrateMappedReadings(latest.result, latest.fieldPreviews));
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
  const identityReady =
    !isCattleReview || hasConfirmableIndividualId(draft, result?.individualId);
  if (pagedJobId === jobId) return <PagedReviewPage key={jobId} jobId={jobId} onClose={onClose} onNavigationGuardChange={onNavigationGuardChange} />;
  if (loadError) return <div className="empty-state large"><h2>結果を開けません</h2><p>{loadError}</p><button className="secondary" onClick={onClose}>戻る</button></div>;
  if (!result || !draft) return <div className="empty-state large">結果を読み込んでいます…</div>;
  const identityReading = draft.document.unifiedEarTag;
  const cattleNameReading = draft.document.cattleName;
  const identityValue = readingText(identityReading);
  const cattleNameValue = readingText(cattleNameReading);
  const identityPreview = previewByPath.get("document.unifiedEarTag");
  const cattleNamePreview = previewByPath.get("document.cattleName");
  const previewImage = (preview: OcrFieldPreview | undefined) => preview
    ? byName.get(`fieldCrop.${preview.mappingId}.alignedColor`) ?? preview.artifactUrl
    : undefined;
  const replaceIdentityField = (path: "document.unifiedEarTag" | "document.cattleName", value: string) => {
    const normalizedValue = path === "document.unifiedEarTag"
      ? normalizeIndividualIdInput(value)
      : value;
    setDraft((current) => current
      ? updateReading(
        current,
        path,
        normalizedValue
          ? {
            raw: normalizedValue,
            normalized: normalizedValue,
            status: path === "document.unifiedEarTag" && !isIndividualId(normalizedValue)
              ? "uncertain"
              : "readable",
            reviewReason: path === "document.unifiedEarTag" && !isIndividualId(normalizedValue)
              ? "INDIVIDUAL_ID_INVALID"
              : null,
          }
          : { raw: null, normalized: null, status: "blank", reviewReason: null },
      )
      : current);
    setError(null);
    setMessage(null);
  };
  if (
    isCattleReview &&
    requireIdentityConfirmation &&
    !identityStepComplete &&
    !result.confirmed
  ) {
    return (
      <div className="page-stack identity-confirm-page">
        <header className="page-header split">
          <div>
            <span className="eyebrow">IDENTITY CHECK · REVISION {result.revision}</span>
            <h1>この個体で間違いありませんか？</h1>
            <p>帳票から読み取った個体識別番号と牛名を確認してください。間違っている場合は、その場で修正できます。</p>
          </div>
          <button className="ghost" onClick={onClose}>← ホームへ</button>
        </header>
        <aside className="notice compact">
          <strong>使用テンプレート</strong>
          <span>{result.result.templateId} · v{result.result.templateVersion}</span>
        </aside>
        <section className="identity-confirm-grid" aria-label="個体情報の確認">
          <IdentityConfirmField
            label="個体識別番号（統一耳標）"
            prompt="この個体識別番号ですね？"
            value={identityValue}
            imageUrl={previewImage(identityPreview)}
            imageAlt="個体識別番号の読み取り範囲"
            inputMode="numeric"
            maxLength={10}
            invalid={!isIndividualId(identityValue)}
            help="数字10桁で確認してください。ハイフンや空白は自動で除きます。"
            onChange={(value) => replaceIdentityField("document.unifiedEarTag", value)}
          />
          <IdentityConfirmField
            label="牛名"
            prompt="そして、名前はこれですね？"
            value={cattleNameValue}
            imageUrl={previewImage(cattleNamePreview)}
            imageAlt="牛名の読み取り範囲"
            help="牛名が記載されていない帳票は空欄のまま進めます。"
            onChange={(value) => replaceIdentityField("document.cattleName", value)}
          />
        </section>
        {!isIndividualId(identityValue) && (
          <p className="error-box">個体識別番号を10桁の数字で確認・修正してください。</p>
        )}
        {previewWarning && <aside className="notice compact"><strong>画像表示のみ利用できません</strong><span>{previewWarning}</span></aside>}
        {error && <p className="error-box">{error}</p>}
        <div className="identity-confirm-actions">
          <div>
            <strong>この情報を保存して結果表へ進みます</strong>
            <span>ここでは個体情報だけを確認します。各表の検出結果は次の画面で編集できます。</span>
          </div>
          <button
            type="button"
            className="primary"
            disabled={busy || !isIndividualId(identityValue)}
            onClick={async () => {
              setBusy(true);
              setError(null);
              setMessage(null);
              try {
                if (JSON.stringify(draft) !== JSON.stringify(result.result)) {
                  const saved = await api.correct(jobId, result.revision, draft);
                  setResult(saved);
                  setDraft(hydrateMappedReadings(saved.result, saved.fieldPreviews));
                }
                setIdentityStepComplete(true);
              } catch (reason) {
                handleMutationFailure(reason, "個体情報を保存できませんでした。");
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "保存中…" : "この個体で結果表へ進む"}
          </button>
        </div>
      </div>
    );
  }
  if (!result.confirmed && result.documentResolution?.required) {
    const resolve = async (
      action: "ADD_PAGE" | "RESCAN_PAGE" | "NEW_DOCUMENT",
      candidate?: (typeof result.documentResolution.candidates)[number],
    ) => {
      setBusy(true);
      setError(null);
      try {
        const candidateKey = candidate?.documentId ?? candidate?.sourceJobId ?? "";
        const selectedPageId = comparisonPageByDocument[candidateKey];
        const activePage = candidate?.pages.find((page) => (
          selectedPageId
            ? (page.pageId ?? page.sourceJobId) === selectedPageId
            : page.active
        )) ?? candidate?.pages[0];
        const resolved = await api.resolveDocument(jobId, action, {
          documentId: candidate?.documentId,
          jobId: activePage?.sourceJobId ?? candidate?.sourceJobId,
          pageId: activePage?.pageId,
        });
        setResult(resolved);
        setDraft(hydrateMappedReadings(resolved.result, resolved.fieldPreviews));
        setDocumentName(resolved.documentName ?? "");
        setMessage(action === "ADD_PAGE" ? "続きのページとしてまとめました。内容を確認して確定してください。" : action === "RESCAN_PAGE" ? "以前のページと比較しました。変更箇所を確認して確定してください。" : "別の台帳として扱います。内容を確認して確定してください。");
      } catch (reason) {
        setError(reason instanceof ApiError ? reason.message : "保存方法を設定できませんでした。");
      } finally {
        setBusy(false);
      }
    };
    return (
      <div className="page-stack document-resolution-page">
        <header className="page-header split">
          <div>
            <span className="eyebrow">DOCUMENT CHECK · REVISION {result.revision}</span>
            <h1>同じ個体の台帳が見つかりました</h1>
            <p>今回の画像を、以前の台帳へどう反映するか選択してください。この選択が終わるまでスプレッドシートは更新されません。</p>
          </div>
          <button className="ghost" onClick={onClose}>← ホームへ</button>
        </header>
        <section className="document-candidate-list" aria-label="以前の台帳">
          {result.documentResolution.candidates.map((candidate, index) => (
            <article className="document-candidate-card" key={candidate.documentId ?? candidate.sourceJobId ?? index}>
              <div>
                <span className="eyebrow">以前の台帳 {index + 1}</span>
                <h2>{candidate.documentName || candidate.cattleName || "書類名なし"}</h2>
                <p>個体識別番号 {candidate.individualId} · {candidate.pageCount}ページ · 最終確認 {new Date(candidate.lastConfirmedAt).toLocaleString("ja-JP")}</p>
              </div>
              <div className="document-resolution-actions">
                <button className="primary" disabled={busy} onClick={() => void resolve("ADD_PAGE", candidate)}>続きのページとして追加</button>
                {candidate.pages.length > 1 && (
                  <label className="document-page-choice">
                    比較するページ
                    <select
                      value={comparisonPageByDocument[candidate.documentId ?? candidate.sourceJobId ?? ""] ?? (candidate.pages.find((page) => page.active)?.pageId ?? candidate.pages.find((page) => page.active)?.sourceJobId ?? "")}
                      onChange={(event) => setComparisonPageByDocument((current) => ({ ...current, [candidate.documentId ?? candidate.sourceJobId ?? ""]: event.target.value }))}
                    >
                      {candidate.pages.filter((page) => page.active).map((page) => <option key={page.pageId ?? page.sourceJobId ?? page.pageNumber} value={page.pageId ?? page.sourceJobId ?? ""}>{page.pageNumber}ページ目</option>)}
                    </select>
                  </label>
                )}
                <button className="secondary" disabled={busy} onClick={() => void resolve("RESCAN_PAGE", candidate)}>前に読み取ったページと比較</button>
              </div>
            </article>
          ))}
        </section>
        <aside className="notice compact">
          <strong>以前の台帳とは関係がない場合</strong>
          <span>同じ個体でも、年度や用途が異なる別の台帳として保存できます。</span>
          <button className="secondary" disabled={busy} onClick={() => void resolve("NEW_DOCUMENT")}>別の台帳として保存</button>
        </aside>
        {error && <p className="error-box">{error}</p>}
      </div>
    );
  }
  const unresolved = reviewCompleted
    ? 0
    : new Set([
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
      <header className="page-header split review-page-header">
        <div>
          <span className="eyebrow">OCR REVIEW · REVISION {result.revision}</span>
          <h1>読み取り結果を確認</h1>
          <p>画像と項目を見比べ、原文と整形値を必要に応じて修正してください。</p>
        </div>
        <div className="review-header-actions">
          {result.provider === "openai" && (
            <aside className="review-usage-badge" aria-label="開発用API使用量">
              <strong>{result.modelId ?? "OpenAI OCR"}</strong>
              <span>入力 {result.inputTokens?.toLocaleString() ?? "-"} / 出力 {result.outputTokens?.toLocaleString() ?? "-"} tokens</span>
              <span>API推定 {result.estimatedCost == null ? "計算不可" : `$${result.estimatedCost.toFixed(6)}`}</span>
              <span>prompt {result.promptVersion ?? "-"}</span>
            </aside>
          )}
          <button className="ghost" onClick={onClose}>← ホームへ</button>
        </div>
      </header>
      <aside className="review-template-banner"><strong>使用テンプレート</strong><span>{result.result.templateId} · v{result.result.templateVersion}</span></aside>
      {result.provider === "mock" && <aside className="notice compact"><strong>試運転データ</strong><span>現在の候補は固定fixtureから作られています。</span></aside>}
      {isCattleReview && !identityReady && <aside className="notice external compact"><strong>個体識別番号を確認してください</strong><span>確定保存には、帳票から読み取った10桁の個体識別番号が必要です。</span></aside>}
      {isCattleReview && result.confirmed && result.individualId && <aside className="notice external compact"><strong>登録データへ保存済み</strong><span>データ検索から個体識別番号 {result.individualId} で検索できます。</span></aside>}
      {result.spreadsheetSync?.enabled && result.spreadsheetSync.status === "failed" && (
        <aside className="notice compact external">
          <button type="button" className="secondary compact" disabled={busy} onClick={async () => {
            setBusy(true);
            try {
              const synced = await api.syncSpreadsheet(jobId);
              setResult(synced);
              setMessage(synced.spreadsheetSync?.status === "synced" ? "スプレッドシートへ再同期しました。" : synced.spreadsheetSync?.lastError ?? "再同期できませんでした。");
              if (synced.spreadsheetSync?.status === "synced" && synced.spreadsheetSync.spreadsheetUrl) {
                onNavigationGuardChange?.(false);
                onSpreadsheetNavigate(synced.spreadsheetSync.spreadsheetUrl);
              }
            } catch (reason) {
              setError(reason instanceof Error ? reason.message : "再同期できませんでした。");
            } finally {
              setBusy(false);
            }
          }}>再同期</button>
          {result.spreadsheetSync.spreadsheetUrl && <a className="secondary compact" href={result.spreadsheetSync.spreadsheetUrl} target="_blank" rel="noreferrer">スプレッドシートを開く ↗</a>}
        </aside>
      )}
      {result.spreadsheetSync?.enabled && (
        <aside className="notice compact output-layout-notice is-disabled" aria-disabled="true">
          <strong>カスタム出力レイアウト · 実装中</strong>
          <span>
            OCRで読み取った項目と、カスタム出力用シートの出力先セルを紐付ける設定機能を実装中です。
          </span>
          <button type="button" className="secondary compact" disabled>
            カスタム出力用シートを取り込む
          </button>
        </aside>
      )}
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
              <img
                src={byName.get("alignedSource")}
                alt="位置合わせ済み帳票"
                onError={handlePreviewLoadError}
                onLoad={() => setPreviewWarning(null)}
              />
            ) : regionImage ? (
              <img
                key={`${selectedPath}:${selectedPreview?.mappingId ?? legacyRegionId}`}
                src={regionImage}
                alt={`${selectedPath} の切り出し`}
                onError={handlePreviewLoadError}
                onLoad={() => setPreviewWarning(null)}
              />
            ) : (
              <div className="preview-missing">この項目に対応する矩形画像がありません。</div>
            )}
          </div>
          {selectedPreview?.boundingBox && <p className="preview-bounds">矩形: x {selectedPreview.boundingBox.x} / y {selectedPreview.boundingBox.y} / 幅 {selectedPreview.boundingBox.width} / 高さ {selectedPreview.boundingBox.height} · {selectedPreview.mappingId}</p>}
          <div className="source-switch"><button className={!selectedPath ? "active" : ""} onClick={() => { setSelectedPath(null); setSelectedMappingId(null); }}>帳票全体</button><button className={selectedPath ? "active" : ""} disabled={!suggestedPath} onClick={() => { const entry = suggestedPath ? entryByPath.get(suggestedPath) : undefined; if (entry) selectEntry(entry); }}>選択領域</button><button onClick={() => { const image = byName.get("handwritingOnly"); if (image) window.open(image, "_blank", "noopener,noreferrer"); }}>手書きのみ ↗</button></div>
          <div className="review-summary"><strong>{unresolved}</strong><span>要確認項目</span><p>「不確実」は空欄とは異なります。画像を見て判断してください。</p></div>
        </aside>
        <main className="field-pane">
          <ReviewResultsToolbar
            interactionMode={interactionMode}
            editingLocked={editingLocked}
            confirmed={result.confirmed}
            warningCount={warningEntries.length}
            cellCount={entries.length}
            onInteractionModeChange={(mode) => {
              setInteractionMode(mode);
              if (mode === "reorder") {
                setSelectedPath(null);
                setSelectedMappingId(null);
                setMarkEditorRow(null);
                setChoiceEditorPath(null);
              }
            }}
            onNextWarning={selectNextWarning}
          />
          {entries.length === 0 ? (
            <div className="empty-state">テンプレートで意味づけされた読み取りセルがありません。</div>
          ) : (
            <div className="ocr-edit-tables">
              {isCattleReview && documentEntries.length > 0 && <ReviewTableSection title="基本情報">
                <KeyValueReviewTable
                  entries={documentEntries}
                  selectedPath={selectedPath}
                  warningPaths={displayedWarningPaths}
                  choicePaths={choicePaths}
                  confirmed={editingLocked}
                  onSelect={selectEntry}
                  onEdit={replaceEntryText}
                />
              </ReviewTableSection>}
              {isCattleReview && pedigreeEntries.length > 0 && <ReviewTableSection title="血統">
                <KeyValueReviewTable
                  entries={pedigreeEntries}
                  selectedPath={selectedPath}
                  warningPaths={displayedWarningPaths}
                  choicePaths={choicePaths}
                  confirmed={editingLocked || interactionMode === "reorder"}
                  onSelect={selectEntry}
                  onEdit={replaceEntryText}
                />
              </ReviewTableSection>}
              {isCattleReview && <CollectionReviewTable
                title="売却子牛一覧"
                root="saleCalves"
                rowCount={draft.saleCalves.length}
                columns={SALE_COLUMNS}
                entryByPath={entryByPath}
                selectedPath={selectedPath}
                warningPaths={displayedWarningPaths}
                choicePaths={choicePaths}
                recordKeyByRow={recordKeyByRow}
                confirmed={editingLocked}
                interactionMode={interactionMode}
                onSelect={selectEntry}
                onEdit={replaceEntryText}
                canMoveRow={canMoveCollectionRow}
                onMoveRow={moveCollectionRow}
              />}
              {isCattleReview && <CollectionReviewTable
                title="繁殖履歴"
                root="calvings"
                rowCount={draft.calvings.length}
                columns={CALVING_COLUMNS}
                entryByPath={entryByPath}
                selectedPath={selectedPath}
                warningPaths={displayedWarningPaths}
                choicePaths={choicePaths}
                recordKeyByRow={recordKeyByRow}
                confirmed={editingLocked}
                interactionMode={interactionMode}
                onSelect={selectEntry}
                onEdit={replaceEntryText}
                canMoveRow={canMoveCollectionRow}
                onMoveRow={moveCollectionRow}
              />}
              {structuredRecordSections.map((section) => (
                <GenericRecordReviewTable
                  key={section.id}
                  section={section}
                  selectedPath={selectedPath}
                  warningPaths={displayedWarningPaths}
                  choicePaths={choicePaths}
                  confirmed={editingLocked}
                  interactionMode={interactionMode}
                  onSelect={selectEntry}
                  onEdit={replaceEntryText}
                  onMoveRow={moveGenericRow}
                />
              ))}
              {parentSections.map((section) => (
                <ReviewTableSection key={section.id} title={section.title}>
                  <KeyValueReviewTable
                    entries={section.entries}
                    selectedPath={selectedPath}
                    warningPaths={displayedWarningPaths}
                    choicePaths={choicePaths}
                    confirmed={editingLocked || interactionMode === "reorder"}
                    onSelect={selectEntry}
                    onEdit={replaceEntryText}
                  />
                </ReviewTableSection>
              ))}
              {independentEntries.length > 0 && <ReviewTableSection title={isCattleReview ? "追加項目" : "独立項目"}>
                <IndependentReviewGroups
                  groups={independentGroups}
                  selectedPath={selectedPath}
                  warningPaths={displayedWarningPaths}
                  choicePaths={choicePaths}
                  confirmed={editingLocked || interactionMode === "reorder"}
                  onSelect={selectEntry}
                  onEdit={replaceEntryText}
                />
              </ReviewTableSection>}
            </div>
          )}
          {!reviewCompleted && interactionMode === "edit" && selectedEntry && selectedPreview?.mappingType !== "mark" && (
            <AnchoredReviewPopover
              path={selectedEntry.path}
              wide={requiresDedicatedCellEditor(selectedEntry)}
              onClose={() => { setSelectedPath(null); setSelectedMappingId(null); }}
            >
              {requiresDedicatedCellEditor(selectedEntry) ? (
                <CellEditor
                  entry={selectedEntry}
                  confirmed={editingLocked}
                  warning={displayedWarningPaths.has(selectedEntry.path)}
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
              ) : (
                <CellStatusPopover
                  entry={selectedEntry}
                  confirmed={editingLocked}
                  onChange={(patch) => setDraft(updateReading(draft, selectedEntry.path, patch))}
                  onClose={() => { setSelectedPath(null); setSelectedMappingId(null); }}
                />
              )}
            </AnchoredReviewPopover>
          )}
        </main>
      </div>
      {!result.confirmed && result.documentName !== undefined && (
        <section className="form-card document-name-confirmation">
          <div>
            <span className="eyebrow">DOCUMENT NAME</span>
            <h2>書類名を確認</h2>
            <p>この名前を、書類ライブラリ、保存するXLSX、Googleスプレッドシートで共通使用します。</p>
          </div>
          <label>
            確定する書類名
            <input
              type="text"
              value={documentName}
              maxLength={180}
              onChange={(event) => setDocumentName(event.target.value)}
              placeholder="書類名を入力してください"
            />
          </label>
        </section>
      )}
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
      <div className="decision-bar sticky"><div><strong>{result.confirmed ? "確定済みの結果です" : revisionConflict ? "競合の復旧方法を選んでください" : !documentName.trim() ? "書類名を入力してください" : !identityReady ? "個体識別番号を確認してください" : `${unresolved}件の要確認項目があります`}</strong><span>{result.confirmed ? (result.spreadsheetSync?.status === "synced" ? "確定結果を保存し、Googleスプレッドシートへ反映しました。" : "確定結果から入力済みExcelを保存できます。") : revisionConflict ? "上の復旧欄で最新結果を取得し、退避内容を復元するか選択してください。" : !documentName.trim() ? "書類ライブラリと出力で共用する名前が必要です。" : !identityReady ? "10桁の個体識別番号を入力すると確定保存できます。" : "修正を保存するとGoogleスプレッドシートも更新されます。"}</span></div>{result.confirmed ? <div className="job-action-buttons"><ExcelExportActions jobId={jobId} compact driveFileName={result.spreadsheetSync?.fileName ?? result.documentName} />{result.spreadsheetSync?.spreadsheetUrl ? <a className="primary" href={result.spreadsheetSync.spreadsheetUrl} target="_blank" rel="noreferrer">スプレッドシートを開く</a> : null}</div> : <><button className="secondary" disabled={busy || Boolean(revisionConflict)} onClick={async () => { setBusy(true); setError(null); setMessage(null); try { const saved = await api.correct(jobId, result.revision, draft); setResult(saved); setDraft(saved.result); setRevisionConflict(null); setMessage(saved.spreadsheetSync?.status === "synced" ? "修正を保存し、スプレッドシートへ反映しました。" : saved.spreadsheetSync?.enabled ? "修正を保存しました。スプレッドシート同期は上の状態を確認してください。" : "修正を保存しました。"); } catch (reason) { handleMutationFailure(reason, "保存できませんでした。"); } finally { setBusy(false); } }}>修正を保存</button><button className="primary" disabled={busy || Boolean(revisionConflict) || unresolved > 0 || !identityReady || !documentName.trim()} onClick={async () => { if (!window.confirm("この内容と書類名を確定します。よろしいですか？")) return; setBusy(true); setConfirming(true); setError(null); setMessage(null); let latest = result; try { if (JSON.stringify(draft) !== JSON.stringify(result.result)) { latest = await api.correct(jobId, result.revision, draft); setResult(latest); setDraft(latest.result); } const confirmed = await api.confirm(jobId, latest.revision, documentName.trim()); setResult(confirmed); setDraft(confirmed.result); setDocumentName(confirmed.documentName); setRevisionConflict(null); if (confirmed.confirmed) closeSelectedCell(); setMessage(confirmed.spreadsheetSync?.status === "synced" ? "結果と書類名を確定し、スプレッドシートへ反映しました。" : "結果と書類名を確定しました。"); } catch (reason) { handleMutationFailure(reason, "確定できませんでした。", latest, draft); } finally { setConfirming(false); setBusy(false); } }}>内容を確定</button></>}</div>
      {confirming && (
        <div
          className="review-confirm-loading-backdrop"
          role="status"
          aria-live="assertive"
          aria-label="スプレッドシートを作成中"
        >
          <div className="review-confirm-loading-card">
            <span className="review-confirm-loading-spinner" aria-hidden="true" />
            <strong>スプレッドシートを作成しています</strong>
            <p>結果と書類名を確定し、Googleスプレッドシートへ反映しています。画面を閉じずにお待ちください。</p>
          </div>
        </div>
      )}
      {!reviewCompleted && markEditorRow !== null && (
        <CalvingMarkModal
          rowIndex={markEditorRow}
          draft={draft}
          imageUrl={regionImage}
          confirmed={editingLocked}
          onClose={() => setMarkEditorRow(null)}
          onChange={(outcomeLabels, sexLabels) => {
            setDraft((current) => {
              if (!current) return current;
              const readingPatch = (labels: string[]) => labels.length > 0
                ? {
                  raw: labels.join("・"),
                  normalized: labels.join("・"),
                  status: "readable" as const,
                  reviewReason: null,
                }
                : {
                  raw: null,
                  normalized: null,
                  status: "blank" as const,
                  reviewReason: null,
                };
              const withOutcome = updateReading(
                current,
                `calvings.${markEditorRow}.outcome`,
                readingPatch(outcomeLabels),
              );
              return updateReading(
                withOutcome,
                `calvings.${markEditorRow}.sex`,
                readingPatch(sexLabels),
              );
            });
          }}
        />
      )}
      {!reviewCompleted && choiceEditorPath !== null && selectedEntry && selectedPreview?.mappingType === "mark" && (
        <GenericChoiceModal
          entry={selectedEntry}
          preview={selectedPreview}
          imageUrl={regionImage}
          confirmed={editingLocked}
          onClose={() => setChoiceEditorPath(null)}
          onChange={(patch) => setDraft((current) => (
            current ? updateReading(current, choiceEditorPath, patch) : current
          ))}
        />
      )}
    </div>
  );
}

function GenericChoiceModal({
  entry,
  preview,
  imageUrl,
  confirmed,
  onClose,
  onChange,
}: {
  entry: ReadingEntry;
  preview: OcrFieldPreview;
  imageUrl?: string;
  confirmed: boolean;
  onClose: () => void;
  onChange: (patch: Partial<FieldReading>) => void;
}) {
  const options = preview.markOptions ?? [];
  const selectionMode = preview.markSelectionMode ?? "multiple";
  const currentText = entry.value.normalized ?? entry.value.raw ?? "";
  const selectedLabels = currentText
    .split("・")
    .map((value) => value.trim())
    .filter((value) => options.some((option) => option.label === value));
  const applyLabels = (labels: string[]) => onChange(
    labels.length > 0
      ? {
        raw: labels.join("・"),
        normalized: labels.join("・"),
        status: "readable",
        reviewReason: null,
      }
      : {
        raw: null,
        normalized: null,
        status: "blank",
        reviewReason: null,
      },
  );
  const toggle = (label: string) => {
    if (confirmed) return;
    if (selectionMode === "single") {
      applyLabels([label]);
      return;
    }
    applyLabels(
      selectedLabels.includes(label)
        ? selectedLabels.filter((value) => value !== label)
        : [...selectedLabels, label],
    );
  };
  return (
    <div className="calving-mark-modal generic-choice-modal" role="dialog" aria-modal="true" aria-labelledby="generic-choice-title">
      <section className="calving-mark-dialog">
        <header>
          <div>
            <span className="eyebrow">MANUAL CHOICE REVIEW</span>
            <h2 id="generic-choice-title">{entry.label}を選択</h2>
            <p>
              帳票画像を確認し、該当する選択肢を
              {selectionMode === "single" ? "1つ" : "必要な数だけ"}選択してください。
            </p>
          </div>
          <button type="button" className="ghost" onClick={onClose}>閉じる</button>
        </header>
        {imageUrl && <div className="calving-mark-source"><img src={imageUrl} alt={`${entry.label}の選択欄`} /></div>}
        <div className="calving-mark-layout generic-choice-layout" aria-label={`${entry.label}の選択肢`}>
          {options.map((option) => {
            const selected = selectedLabels.includes(option.label);
            return (
              <button
                type="button"
                key={option.value}
                className={selected ? "selected" : ""}
                aria-pressed={selected}
                disabled={confirmed}
                onClick={() => toggle(option.label)}
              >
                <span className="mark-circle">{option.label}</span>
              </button>
            );
          })}
        </div>
        {options.length === 0 && <p className="error-box">テンプレートに選択肢が設定されていません。</p>}
        <footer>
          <button
            type="button"
            className="secondary"
            disabled={confirmed}
            onClick={() => applyLabels([])}
          >
            未記入
          </button>
          <button
            type="button"
            className="secondary"
            disabled={confirmed}
            onClick={() => onChange({
              raw: null,
              normalized: null,
              status: "uncertain",
              reviewReason: "MANUAL_CHOICE_UNRESOLVED",
            })}
          >
            判別不能
          </button>
          <button type="button" className="primary" onClick={onClose}>この選択を反映</button>
        </footer>
      </section>
    </div>
  );
}

const CALVING_OUTCOME_MARKS = ["双", "早", "流", "死"] as const;
const CALVING_SEX_MARKS = ["♂", "♀"] as const;

function selectedMarkLabels(value: FieldReading | undefined, allowed: readonly string[]) {
  const text = `${value?.raw ?? ""} ${value?.normalized ?? ""}`;
  return allowed.filter((label) => text.includes(label));
}

function CalvingMarkModal({
  rowIndex,
  draft,
  imageUrl,
  confirmed,
  onClose,
  onChange,
}: {
  rowIndex: number;
  draft: OcrDocument;
  imageUrl?: string;
  confirmed: boolean;
  onClose: () => void;
  onChange: (outcomes: string[], sexes: string[]) => void;
}) {
  const record = draft.calvings[rowIndex];
  const outcomes = selectedMarkLabels(record?.outcome, CALVING_OUTCOME_MARKS);
  const sexes = selectedMarkLabels(record?.sex, CALVING_SEX_MARKS);
  const toggle = (label: string, group: "outcome" | "sex") => {
    if (confirmed) return;
    const current = group === "outcome" ? outcomes : sexes;
    const next = current.includes(label)
      ? current.filter((value) => value !== label)
      : [...current, label];
    onChange(group === "outcome" ? next : outcomes, group === "sex" ? next : sexes);
  };
  return (
    <div className="calving-mark-modal" role="dialog" aria-modal="true" aria-labelledby="calving-mark-title">
      <section className="calving-mark-dialog">
        <header>
          <div>
            <span className="eyebrow">CALVING MARK REVIEW</span>
            <h2 id="calving-mark-title">{rowIndex + 1}行目の分娩状況を修正</h2>
            <p>帳票と見比べ、丸が付いている記号をクリックしてください。複数選択できます。</p>
          </div>
          <button type="button" className="ghost" onClick={onClose}>閉じる</button>
        </header>
        {imageUrl && <div className="calving-mark-source"><img src={imageUrl} alt="分娩状況の読み取り矩形" /></div>}
        <div className="calving-mark-layout" aria-label="分娩状況の選択肢">
          {[...CALVING_OUTCOME_MARKS, ...CALVING_SEX_MARKS].map((label) => {
            const group = CALVING_OUTCOME_MARKS.includes(label as (typeof CALVING_OUTCOME_MARKS)[number])
              ? "outcome"
              : "sex";
            const selected = group === "outcome" ? outcomes.includes(label) : sexes.includes(label);
            const longLabel = ({ 双: "双子", 早: "早産", 流: "流産", 死: "死産", "♂": "雄", "♀": "雌" } as Record<string, string>)[label];
            return (
              <button
                type="button"
                key={label}
                className={selected ? "selected" : ""}
                aria-pressed={selected}
                disabled={confirmed}
                onClick={() => toggle(label, group)}
              >
                <span className="mark-circle">{label}</span>
                <small>{longLabel}</small>
              </button>
            );
          })}
        </div>
        <footer>
          <button
            type="button"
            className="secondary"
            disabled={confirmed || (outcomes.length === 0 && sexes.length === 0)}
            onClick={() => onChange([], [])}
          >
            すべての丸を外す
          </button>
          <button type="button" className="primary" onClick={onClose}>この選択を反映</button>
        </footer>
      </section>
    </div>
  );
}

function requiresDedicatedCellEditor(entry: ReadingEntry) {
  return isJapaneseEraDatePath(entry.path)
    || entry.path.endsWith(".sireName")
    || (entry.path.startsWith("saleCalves.") && entry.path.endsWith(".notes"));
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
  const isJapaneseEraDate = isJapaneseEraDatePath(entry.path);
  const isSaleNote = entry.path.startsWith("saleCalves.")
    && entry.path.endsWith(".notes");
  const selectedEra = inferJapaneseEra(
    entry.value.raw,
    entry.value.normalized,
  );
  useEffect(() => {
    if (
      !isJapaneseEraDate
      || confirmed
      || entry.value.status === "blank"
      || entry.value.normalized
      || !entry.value.raw
    ) return;
    const patch = automaticJapaneseEraDatePatch(entry.value.raw);
    if (patch.normalized) onChange(patch);
  }, [
    confirmed,
    entry.path,
    entry.value.normalized,
    entry.value.raw,
    entry.value.status,
    isJapaneseEraDate,
    onChange,
  ]);
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
      {isJapaneseEraDate ? (
        <div className="ocr-cell-editor-fields birth-date-editor-fields">
          <label>
            元号年・月・日（数字のみ）
            <input
              autoFocus
              disabled={confirmed}
              inputMode="numeric"
              value={sanitizeJapaneseEraDateInput(entry.value.raw ?? "")}
              placeholder="例: 26 9 27"
              onChange={(event) => {
                const raw = sanitizeJapaneseEraDateInput(event.target.value) || null;
                onChange(raw
                  ? automaticJapaneseEraDatePatch(raw)
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
              disabled={confirmed || !japaneseEraDateParts(entry.value.raw)}
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
                    raw: sanitizeJapaneseEraDateInput(entry.value.raw ?? ""),
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

function CollectionReviewTable({
  title,
  root,
  rowCount,
  columns,
  entryByPath,
  selectedPath,
  warningPaths,
  choicePaths,
  recordKeyByRow,
  confirmed,
  interactionMode,
  onSelect,
  onEdit,
  canMoveRow,
  onMoveRow,
}: {
  title: string;
  root: CollectionRoot;
  rowCount: number;
  columns: ReviewColumn[];
  entryByPath: ReadonlyMap<string, ReadingEntry>;
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  choicePaths: ReadonlySet<string>;
  recordKeyByRow: ReadonlyMap<string, string>;
  confirmed: boolean;
  interactionMode: ReviewInteractionMode;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
  canMoveRow: (root: CollectionRoot, fromIndex: number, toIndex: number) => boolean;
  onMoveRow: (
    root: CollectionRoot,
    fromIndex: number,
    toIndex: number,
  ) => void;
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
        <table className="ocr-edit-table ocr-collection-table" aria-label={`${title}のレコード`}>
          <thead>
            <tr>
              <th>レコード</th>
              {visibleColumns.map((column) => <th key={column.key}>{column.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((index, visibleIndex) => (
              <tr key={index}>
                <th scope="row">
                  <div className="ocr-row-heading">
                    <span>{recordKeyByRow.get(`${root}.${index}`) ?? index + 1}産次</span>
                    {interactionMode === "reorder" && (
                      <span className="ocr-row-shift-controls">
                        <button
                          type="button"
                          aria-label={`${title} ${index + 1}行目を上へ移動`}
                          title="この行の読み取り結果を一行上へ移動"
                          disabled={
                            confirmed
                            || visibleIndex === 0
                            || index - visibleRows[visibleIndex - 1] !== 1
                            || !canMoveRow(root, index, visibleRows[visibleIndex - 1])
                          }
                          onClick={() => onMoveRow(root, index, visibleRows[visibleIndex - 1])}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          aria-label={`${title} ${index + 1}行目を下へ移動`}
                          title="この行の読み取り結果を一行下へ移動"
                          disabled={
                            confirmed
                            || visibleIndex === visibleRows.length - 1
                            || visibleRows[visibleIndex + 1] - index !== 1
                            || !canMoveRow(root, index, visibleRows[visibleIndex + 1])
                          }
                          onClick={() => onMoveRow(root, index, visibleRows[visibleIndex + 1])}
                        >
                          ↓
                        </button>
                      </span>
                    )}
                  </div>
                </th>
                {visibleColumns.map((column) => {
                  const entry = entryByPath.get(`${root}.${index}.${column.key}`);
                  if (!entry) return <td className="ocr-unmapped-cell" key={column.key}>—</td>;
                  return (
                    <ReviewValueCell
                      key={column.key}
                      entry={entry}
                      selected={selectedPath === entry.path}
                      warning={warningPaths.has(entry.path)}
                      choice={choicePaths.has(entry.path)}
                      confirmed={confirmed || interactionMode === "reorder"}
                      onSelect={onSelect}
                      onEdit={onEdit}
                    />
                  );
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

const SALE_CALF_FIELDS = [
  "parity",
  "ageDays",
  "weightKg",
  "priceYen",
  "sireName",
  "saleDate",
  "notes",
] as const;
const CALVING_FIELDS = [
  "parity",
  "calvingDate",
  "sex",
  "outcome",
  "notes",
] as const;

function blankReading(
  sourceRegionId = "unmapped",
  boundingBox: FieldReading["boundingBox"] = null,
): FieldReading {
  return {
    raw: null,
    normalized: null,
    status: "blank",
    alternatives: [],
    reviewReason: null,
    sourceRegionId,
    boundingBox: boundingBox ? { ...boundingBox } : null,
  };
}

function blankRecord(fields: readonly string[]): Record<string, FieldReading> {
  return Object.fromEntries(
    fields.map((field) => [field, blankReading()]),
  );
}

export function hydrateMappedReadings(
  document: OcrDocument,
  previews: readonly OcrFieldPreview[],
): OcrDocument {
  const next = structuredClone(document);

  for (const preview of previews) {
    const parts = preview.targetPath.split(".");
    const root = parts[0];
    let record: Record<string, FieldReading> | undefined;
    let field: string | undefined;

    if (root === "saleCalves" || root === "calvings") {
      const index = Number(parts[1]);
      field = parts[2];
      if (!Number.isInteger(index) || index < 0 || !field) continue;
      const rows = root === "saleCalves" ? next.saleCalves : next.calvings;
      const fields = root === "saleCalves" ? SALE_CALF_FIELDS : CALVING_FIELDS;
      while (rows.length <= index) rows.push(blankRecord(fields));
      record = rows[index];
    } else if (root === "document" || root === "pedigree" || root === "customFields") {
      field = parts[1];
      if (!field || parts.length !== 2) continue;
      record = next[root];
    }

    if (record && field && (
      !record[field] || record[field].sourceRegionId === "unmapped"
    )) {
      record[field] = blankReading(preview.mappingId, preview.boundingBox);
    }
  }

  return next;
}

type ReadingPathPair = { firstPath: string; secondPath: string };

function readingAtPath(document: OcrDocument, path: string): FieldReading | undefined {
  const parts = path.split(".");
  if (parts.length === 2 && parts[0] === "customFields") {
    return document.customFields[parts[1]];
  }
  if (
    parts.length === 3
    && (parts[0] === "saleCalves" || parts[0] === "calvings")
  ) {
    const index = Number(parts[1]);
    return Number.isInteger(index) ? document[parts[0]][index]?.[parts[2]] : undefined;
  }
  return undefined;
}

function setReadingAtPath(document: OcrDocument, path: string, value: FieldReading): boolean {
  const parts = path.split(".");
  if (parts.length === 2 && parts[0] === "customFields") {
    document.customFields[parts[1]] = value;
    return true;
  }
  if (
    parts.length === 3
    && (parts[0] === "saleCalves" || parts[0] === "calvings")
  ) {
    const index = Number(parts[1]);
    const row = Number.isInteger(index) ? document[parts[0]][index] : undefined;
    if (!row) return false;
    row[parts[2]] = value;
    return true;
  }
  return false;
}

function swapReadingsAtFixedRegions(
  document: OcrDocument,
  pairs: readonly ReadingPathPair[],
): OcrDocument {
  if (pairs.length === 0) return document;
  const paths = pairs.flatMap(({ firstPath, secondPath }) => [firstPath, secondPath]);
  if (new Set(paths).size !== paths.length) return document;
  const snapshots = new Map<string, FieldReading>();
  for (const path of paths) {
    const reading = readingAtPath(document, path);
    if (!reading) return document;
    snapshots.set(path, structuredClone(reading));
  }

  const next = structuredClone(document);
  const destinationBySource = new Map<string, string>();
  for (const { firstPath, secondPath } of pairs) {
    const first = snapshots.get(firstPath);
    const second = snapshots.get(secondPath);
    if (!first || !second) return document;
    setReadingAtPath(next, firstPath, readingValueAtFixedRegion(second, first));
    setReadingAtPath(next, secondPath, readingValueAtFixedRegion(first, second));
    destinationBySource.set(firstPath, secondPath);
    destinationBySource.set(secondPath, firstPath);
  }
  next.reviewItems = next.reviewItems.map((item) => {
    const destinationPath = destinationBySource.get(item.path);
    if (!destinationPath) return item;
    return {
      ...item,
      path: destinationPath,
      sourceRegionId: readingAtPath(next, destinationPath)?.sourceRegionId ?? null,
    };
  });
  return next;
}

function collectionRowPairs(
  document: OcrDocument,
  root: CollectionRoot,
  fromIndex: number,
  toIndex: number,
): ReadingPathPair[] | null {
  const rows = document[root];
  if (
    Math.abs(fromIndex - toIndex) !== 1
    || fromIndex < 0
    || toIndex < 0
    || fromIndex >= rows.length
    || toIndex >= rows.length
  ) return null;
  const fields = Array.from(new Set([
    ...Object.keys(rows[fromIndex]),
    ...Object.keys(rows[toIndex]),
  ]));
  const pairs = fields.map((field) => ({
    firstPath: `${root}.${fromIndex}.${field}`,
    secondPath: `${root}.${toIndex}.${field}`,
  }));
  return pairs.length > 0 && pairs.every(({ firstPath, secondPath }) => (
    readingAtPath(document, firstPath) && readingAtPath(document, secondPath)
  )) ? pairs : null;
}

export function canSwapCollectionRows(
  document: OcrDocument,
  root: CollectionRoot,
  fromIndex: number,
  toIndex: number,
): boolean {
  return collectionRowPairs(document, root, fromIndex, toIndex) !== null;
}

export function swapCollectionRowsAtFixedRegions(
  document: OcrDocument,
  root: CollectionRoot,
  fromIndex: number,
  toIndex: number,
): OcrDocument {
  const pairs = collectionRowPairs(document, root, fromIndex, toIndex);
  return pairs ? swapReadingsAtFixedRegions(document, pairs) : document;
}

function genericRecordRowPairs(
  section: ReviewDisplayRecordSection,
  fromIndex: number,
  toIndex: number,
): ReadingPathPair[] | null {
  if (
    Math.abs(fromIndex - toIndex) !== 1
    || fromIndex < 0
    || toIndex < 0
    || fromIndex >= section.rows.length
    || toIndex >= section.rows.length
  ) return null;
  const fromRow = section.rows[fromIndex];
  const toRow = section.rows[toIndex];
  const pairs: ReadingPathPair[] = [];
  for (const column of section.columns) {
    const firstPath = fromRow.cells.find((cell) => cell.columnId === column.id)?.entry?.path;
    const secondPath = toRow.cells.find((cell) => cell.columnId === column.id)?.entry?.path;
    if (!firstPath || !secondPath) return null;
    pairs.push({ firstPath, secondPath });
  }
  const paths = pairs.flatMap(({ firstPath, secondPath }) => [firstPath, secondPath]);
  return new Set(paths).size === paths.length ? pairs : null;
}

export function canSwapGenericRecordRows(
  section: ReviewDisplayRecordSection,
  fromIndex: number,
  toIndex: number,
): boolean {
  return genericRecordRowPairs(section, fromIndex, toIndex) !== null;
}

export function swapGenericRecordRowsAtFixedRegions(
  document: OcrDocument,
  section: ReviewDisplayRecordSection,
  fromIndex: number,
  toIndex: number,
): OcrDocument {
  const pairs = genericRecordRowPairs(section, fromIndex, toIndex);
  return pairs ? swapReadingsAtFixedRegions(document, pairs) : document;
}

function readingValueAtFixedRegion(
  value: FieldReading,
  fixedRegion: FieldReading,
): FieldReading {
  return {
    ...structuredClone(value),
    sourceRegionId: fixedRegion.sourceRegionId,
    boundingBox: fixedRegion.boundingBox
      ? { ...fixedRegion.boundingBox }
      : null,
  };
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
    MANAGED_CANDIDATE_UNRESOLVED: "登録済みの読み取り候補と一致しません。画像と候補を確認してください。",
    DITTO_SOURCE_UNAVAILABLE: "同上マークの上段に確定した値がありません。",
    MISSING_TRANSCRIPTION: "この欄の読み取り結果が返されませんでした。",
    DITTO_PENDING: "同上マークの参照先を確認しています。",
  } as Record<string, string>)[reason] ?? reason;
}
