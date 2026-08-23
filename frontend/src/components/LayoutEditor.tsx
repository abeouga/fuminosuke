import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type {
  DocumentIdentity,
  FieldMappingTarget,
  LayoutCell,
  LayoutDefinition,
  LayoutLabelPoolEntry,
  LayoutMarkSet,
  LayoutRecordGroup,
  LayoutRecordSection,
  LayoutRelation,
} from "../types";

type Point = { x: number; y: number };
type Rect = Point & { width: number; height: number };
type Mode = "select" | "classify" | "draw" | "link" | "record" | "parent" | "identity";
type OcrLabelAlignment = "left" | "center" | "right";
export type LayoutWorkflowStep = "classify" | "meaning" | "records" | "identity" | "review";

export type LayoutEditorHandle = {
  saveIfNeeded: () => Promise<boolean>;
};

type LayoutEditorProps = {
  source: string;
  initialLayout: LayoutDefinition;
  targets: FieldMappingTarget[];
  onDetect: () => Promise<LayoutDefinition>;
  onRecognizeLabels?: (layout: LayoutDefinition) => Promise<LayoutDefinition>;
  onSave: (layout: LayoutDefinition) => Promise<LayoutDefinition>;
  allowUnchangedSave?: boolean;
  disabled?: boolean;
  workflowStep?: LayoutWorkflowStep;
  documentIdentityFields?: DocumentIdentity[];
  onDocumentIdentityFieldsChange?: (fields: DocumentIdentity[]) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onBusyChange?: (busy: boolean) => void;
  draftStorageKey?: string;
};

const DEFAULT_MAGNIFIER_SCALE = 2.5;
const DEFAULT_MAGNIFIER_SIZE = 220;

const ROLE_COLOR = {
  label: "#b36b00",
  parent_label: "#16805d",
  input: "#00777d",
  mark: "#7c3aed",
  record_key_cell: "#d97706",
  column_key_cell: "#a16207",
  ignored: "#6b7280",
} as const;

const OCR_LABEL_BADGE_STYLE = {
  label: {
    background: "rgba(255, 240, 213, 0.92)",
    text: "#5f3a00",
  },
  parent_label: {
    background: "rgba(226, 243, 236, 0.92)",
    text: "#0f513d",
  },
} as const;

export function drawOcrLabelText(
  context: CanvasRenderingContext2D,
  cell: LayoutCell,
  canonicalWidth: number,
  alignment: OcrLabelAlignment = "left",
) {
  if (cell.role !== "label" && cell.role !== "parent_label") return;
  const text = cell.text.trim();
  if (!text) return;
  const padding = 4;
  const fontSize = Math.max(8, Math.min(22, cell.height - 6, canonicalWidth / 85));
  const badgeX = cell.x + 2;
  const badgeY = cell.y + 2;
  const badgeWidth = Math.max(1, cell.width - 4);
  const badgeHeight = Math.max(1, Math.min(cell.height - 4, fontSize + padding * 2));
  const badgeStyle = OCR_LABEL_BADGE_STYLE[cell.role];
  context.save();
  context.fillStyle = badgeStyle.background;
  context.fillRect(badgeX, badgeY, badgeWidth, badgeHeight);
  context.fillStyle = badgeStyle.text;
  context.font = `700 ${fontSize}px sans-serif`;
  context.textBaseline = "top";
  context.textAlign = alignment;
  const textX = alignment === "left"
    ? badgeX + padding
    : alignment === "center"
      ? badgeX + badgeWidth / 2
      : badgeX + badgeWidth - padding;
  context.fillText(
    text,
    textX,
    badgeY + padding,
    Math.max(1, badgeWidth - padding * 2),
  );
  context.restore();
}

export const LayoutEditor = forwardRef<LayoutEditorHandle, LayoutEditorProps>(function LayoutEditor({
  source,
  initialLayout,
  targets,
  onDetect,
  onRecognizeLabels,
  onSave,
  allowUnchangedSave = false,
  disabled = false,
  workflowStep,
  documentIdentityFields = [],
  onDocumentIdentityFieldsChange,
  onDirtyChange,
  onBusyChange,
  draftStorageKey,
}: LayoutEditorProps, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const magnifierRef = useRef<HTMLDivElement>(null);
  const magnifierCanvasRef = useRef<HTMLCanvasElement>(null);
  const magnifierPointerRef = useRef<{ clientX: number; clientY: number } | null>(null);
  const magnifierFrameRef = useRef<number | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragStart = useRef<Point | null>(null);
  const additiveSelection = useRef(false);
  const linkLabelId = useRef<string | null>(null);
  const relationHistory = useRef<LayoutRelation[][]>([]);
  const operationLockRef = useRef(false);
  const lastAutoSaveAttemptSignatureRef = useRef<string | null>(null);
  const [layout, setLayout] = useState(() => restoreLayoutDraft(initialLayout, draftStorageKey));
  const [baselineSignature, setBaselineSignature] = useState(
    () => layoutSignature(normalizeLayout(initialLayout)),
  );
  const [mode, setMode] = useState<Mode>("select");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [selectedPath, setSelectedPath] = useState("");
  const [draft, setDraft] = useState<Rect | null>(null);
  const [linkPoint, setLinkPoint] = useState<Point | null>(null);
  const [busy, setBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [recognizingLabels, setRecognizingLabels] = useState(false);
  const [ocrLabelAlignment, setOcrLabelAlignment] = useState<OcrLabelAlignment>("left");
  const [magnifierEnabled, setMagnifierEnabled] = useState(false);
  const [magnifierScale, setMagnifierScale] = useState(DEFAULT_MAGNIFIER_SCALE);
  const [magnifierSize, setMagnifierSize] = useState(DEFAULT_MAGNIFIER_SIZE);
  const [message, setMessage] = useState<string | null>(null);
  const [historyDepth, setHistoryDepth] = useState(0);
  const [customLabel, setCustomLabel] = useState("");
  const [customTargetPlacement, setCustomTargetPlacement] = useState<"sidebar" | "catalog" | null>(null);
  const [meaningCatalogQuery, setMeaningCatalogQuery] = useState("");
  const [meaningCatalogSection, setMeaningCatalogSection] = useState("all");
  const [recordSectionTitle, setRecordSectionTitle] = useState("明細");
  const [rowKeyStrategy, setRowKeyStrategy] = useState<"printed" | "position">("printed");
  const [selectedRecordSectionId, setSelectedRecordSectionId] = useState("");
  const currentSignature = useMemo(() => layoutSignature(layout), [layout]);
  const dirty = currentSignature !== baselineSignature;
  const dirtyRef = useRef(dirty);

  const hideMagnifier = useCallback(() => {
    if (magnifierFrameRef.current !== null) {
      window.cancelAnimationFrame(magnifierFrameRef.current);
      magnifierFrameRef.current = null;
    }
    if (magnifierRef.current) magnifierRef.current.hidden = true;
  }, []);

  const drawMagnifier = useCallback(() => {
    magnifierFrameRef.current = null;
    const pointer = magnifierPointerRef.current;
    const sourceCanvas = canvasRef.current;
    const wrapper = canvasWrapRef.current;
    const lens = magnifierRef.current;
    const lensCanvas = magnifierCanvasRef.current;
    if (!pointer || !sourceCanvas || !wrapper || !lens || !lensCanvas || !magnifierEnabled) {
      hideMagnifier();
      return;
    }
    const sourceBounds = sourceCanvas.getBoundingClientRect();
    if (
      sourceBounds.width <= 0
      || sourceBounds.height <= 0
      || pointer.clientX < sourceBounds.left
      || pointer.clientX > sourceBounds.right
      || pointer.clientY < sourceBounds.top
      || pointer.clientY > sourceBounds.bottom
    ) {
      hideMagnifier();
      return;
    }
    const context = lensCanvas.getContext("2d");
    if (!context) {
      hideMagnifier();
      return;
    }

    const deviceScale = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    const outputSize = Math.round(magnifierSize * deviceScale);
    if (lensCanvas.width !== outputSize || lensCanvas.height !== outputSize) {
      lensCanvas.width = outputSize;
      lensCanvas.height = outputSize;
    }
    const sourceX = (pointer.clientX - sourceBounds.left)
      * sourceCanvas.width / sourceBounds.width;
    const sourceY = (pointer.clientY - sourceBounds.top)
      * sourceCanvas.height / sourceBounds.height;
    const cropWidth = Math.min(
      sourceCanvas.width,
      magnifierSize * sourceCanvas.width / sourceBounds.width / magnifierScale,
    );
    const cropHeight = Math.min(
      sourceCanvas.height,
      magnifierSize * sourceCanvas.height / sourceBounds.height / magnifierScale,
    );
    const cropX = Math.max(0, Math.min(sourceCanvas.width - cropWidth, sourceX - cropWidth / 2));
    const cropY = Math.max(0, Math.min(sourceCanvas.height - cropHeight, sourceY - cropHeight / 2));
    context.clearRect(0, 0, outputSize, outputSize);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(
      sourceCanvas,
      cropX,
      cropY,
      cropWidth,
      cropHeight,
      0,
      0,
      outputSize,
      outputSize,
    );
    const wrapperBounds = wrapper.getBoundingClientRect();
    const pointerX = pointer.clientX - wrapperBounds.left;
    const pointerY = pointer.clientY - wrapperBounds.top;
    const gap = 18;
    const visibleWidth = wrapper.clientWidth || wrapperBounds.width;
    const visibleHeight = wrapper.clientHeight || wrapperBounds.height;
    const lensX = pointerX + gap + magnifierSize <= visibleWidth
      ? pointerX + gap
      : pointerX - gap - magnifierSize;
    const lensY = pointerY + gap + magnifierSize <= visibleHeight
      ? pointerY + gap
      : pointerY - gap - magnifierSize;
    lens.style.left = `${Math.max(0, lensX) + wrapper.scrollLeft}px`;
    lens.style.top = `${Math.max(0, lensY) + wrapper.scrollTop}px`;
    lens.hidden = false;
  }, [hideMagnifier, magnifierEnabled, magnifierScale, magnifierSize]);

  const queueMagnifier = useCallback((clientX: number, clientY: number) => {
    magnifierPointerRef.current = { clientX, clientY };
    if (!magnifierEnabled || magnifierFrameRef.current !== null) return;
    magnifierFrameRef.current = window.requestAnimationFrame(drawMagnifier);
  }, [drawMagnifier, magnifierEnabled]);

  useEffect(() => {
    if (!magnifierEnabled) {
      hideMagnifier();
      return;
    }
    if (magnifierPointerRef.current && magnifierFrameRef.current === null) {
      magnifierFrameRef.current = window.requestAnimationFrame(drawMagnifier);
    }
  }, [drawMagnifier, hideMagnifier, magnifierEnabled, magnifierScale, magnifierSize]);
  useEffect(() => () => hideMagnifier(), [hideMagnifier]);

  const rememberRelations = (relations: LayoutRelation[]) => {
    relationHistory.current.push(relations.map((relation) => ({
      ...relation,
      labelCellIds: [...relation.labelCellIds],
    })));
    if (relationHistory.current.length > 50) relationHistory.current.shift();
    setHistoryDepth(relationHistory.current.length);
  };
  const undoMeaning = useCallback(() => {
    const previous = relationHistory.current.pop();
    if (!previous) {
      setMessage("戻せる意味づけはありません。");
      return;
    }
    setLayout((current) => ({ ...current, relations: previous }));
    setHistoryDepth(relationHistory.current.length);
    setMessage("直前の意味づけを元に戻しました。");
  }, []);

  useEffect(() => {
    const normalized = normalizeLayout(initialLayout);
    if (dirtyRef.current) return;
    setLayout(normalized);
    setBaselineSignature(layoutSignature(normalized));
    setSelectedRecordSectionId(normalized.recordSections?.[0]?.id ?? "");
    setSelectedId(null);
    setSelectedIds(new Set());
    relationHistory.current = [];
    setHistoryDepth(0);
  }, [initialLayout]);
  useEffect(() => {
    dirtyRef.current = dirty;
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!draftStorageKey) return;
    try {
      if (dirty) {
        window.localStorage.setItem(draftStorageKey, currentSignature);
      } else {
        window.localStorage.removeItem(draftStorageKey);
      }
    } catch {
      // The API save below remains authoritative when browser storage is unavailable.
    }
  }, [currentSignature, dirty, draftStorageKey]);
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
      const target = event.target as HTMLElement | null;
      if (
        target?.isContentEditable
        || target?.tagName === "INPUT"
        || target?.tagName === "TEXTAREA"
      ) return;
      event.preventDefault();
      undoMeaning();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [undoMeaning]);
  useEffect(() => {
    if (!workflowStep) return;
    setMode({
      classify: "select",
      meaning: "select",
      records: "record",
      identity: "identity",
      review: "select",
    }[workflowStep] as Mode);
    dragStart.current = null;
    linkLabelId.current = null;
    setDraft(null);
    setLinkPoint(null);
  }, [workflowStep]);
  const selected = layout.cells.find((cell) => cell.id === selectedId) ?? null;
  useEffect(() => {
    if (mode === "link") {
      setSelectedPath("");
      return;
    }
    if (!selected || selected.role !== "input") return;
    const relation = layout.relations.find(
      (item) => item.inputCellId === selected.id,
    );
    setSelectedPath(relation?.targetPath ?? "");
  }, [mode, selectedId, selected?.role, layout.relations]);
  const selectedCells = layout.cells.filter((cell) => selectedIds.has(cell.id));
  const recordSections = layout.recordSections ?? [];
  const recordGroups = layout.recordGroups ?? [];
  const ocrProtectedLabelIds = useMemo(() => {
    const protectedIds = new Set(
      layout.relations.flatMap((relation) => relation.labelCellIds),
    );
    recordSections.forEach((section) => {
      section.recordLabelCellIds.forEach((labelId) => protectedIds.add(labelId));
      if (section.parentLabelCellId) protectedIds.add(section.parentLabelCellId);
    });
    return protectedIds;
  }, [layout.relations, recordSections]);
  const recordSectionById = useMemo(
    () => new Map(recordSections.map((section) => [section.id, section])),
    [recordSections],
  );
  const hasIncompleteLegacyRecordSection = recordGroups.some((group) => {
    const section = recordSectionById.get(group.sectionId);
    return section?.recordType !== "generic" && !section?.parentLabelCellId;
  });
  const availableTargets = useMemo(() => {
    const byPath = new Map(targets.map((target) => [target.path, target]));
    (layout.labelPool ?? []).forEach((entry) => {
      const target: FieldMappingTarget = {
        path: entry.targetPath,
        label: entry.label,
        section: entry.section,
        aliases: entry.aliases,
      };
      const existing = byPath.get(target.path);
      byPath.set(target.path, existing
        ? { ...existing, aliases: [...new Set([...(existing.aliases ?? []), ...entry.aliases])] }
        : target);
    });
    return [...byPath.values()];
  }, [targets, layout.labelPool]);
  const labelPoolEntries = useMemo(
    () => buildLabelPoolEntries(availableTargets),
    [availableTargets],
  );
  const targetByPath = useMemo(
    () => new Map(availableTargets.map((target) => [target.path, target])),
    [availableTargets],
  );
  const cellById = useMemo(
    () => new Map(layout.cells.map((cell) => [cell.id, cell])),
    [layout.cells],
  );
  const recordInputIds = useMemo(
    () => new Set(recordGroups.flatMap((group) => group.inputCellIds)),
    [recordGroups],
  );
  const identityCandidates = useMemo(() => layout.relations.flatMap((relation) => {
    const cell = cellById.get(relation.inputCellId);
    const target = targetByPath.get(relation.targetPath);
    if (
      !cell
      || cell.role !== "input"
      || recordInputIds.has(cell.id)
      || /^(saleCalves|calvings)\.\d+\./.test(relation.targetPath)
    ) return [];
    return [{
      cell,
      targetPath: relation.targetPath,
      label: target?.label || cell.printedLabelText?.trim() || relation.targetPath,
    }];
  }), [cellById, layout.relations, recordInputIds, targetByPath]);
  const identityCandidateByCellId = useMemo(
    () => new Map(identityCandidates.map((candidate) => [candidate.cell.id, candidate])),
    [identityCandidates],
  );
  const selectedIdentityByPath = useMemo(
    () => new Map(documentIdentityFields.map((field) => [field.targetPath, field])),
    [documentIdentityFields],
  );
  const selectedIdentityCandidates = identityCandidates.filter(
    (candidate) => selectedIdentityByPath.has(candidate.targetPath),
  );
  const primaryIdentityField = documentIdentityFields.find(
    (field) => field.matchRole === "primary",
  ) ?? null;
  const titleIdentityField = documentIdentityFields.find(
    (field) => field.displayRole === "title",
  ) ?? null;
  const subtitleIdentityField = documentIdentityFields.find(
    (field) => field.displayRole === "subtitle",
  ) ?? null;
  const primaryIdentityCount = documentIdentityFields.filter(
    (field) => field.matchRole === "primary",
  ).length;
  const usesCombinedConfirmation = documentIdentityFields.length >= 2
    && primaryIdentityCount === 0;
  const identityConfigurationReady = documentIdentityFields.length > 0
    && (primaryIdentityCount === 1 || usesCombinedConfirmation)
    && documentIdentityFields.filter((field) => field.displayRole === "title").length === 1
    && documentIdentityFields.filter((field) => field.displayRole === "subtitle").length <= 1;
  const normalizedIdentityFields = (fields: DocumentIdentity[]) => {
    if (fields.length === 0) return [];
    let primaryAssigned = false;
    let titleAssigned = false;
    let subtitleAssigned = false;
    return fields.map((field) => {
      const matchRole = field.matchRole === "primary" && !primaryAssigned
        ? (primaryAssigned = true, "primary" as const)
        : "confirmation" as const;
      let displayRole: DocumentIdentity["displayRole"] = "none";
      if (field.displayRole === "title" && !titleAssigned) {
        titleAssigned = true;
        displayRole = "title";
      } else if (field.displayRole === "subtitle" && !subtitleAssigned) {
        subtitleAssigned = true;
        displayRole = "subtitle";
      }
      return { ...field, matchRole, displayRole };
    }).map((field, index) => ({
      ...field,
      displayRole: titleAssigned ? field.displayRole : (index === 0 ? "title" : field.displayRole),
    }));
  };
  const toggleIdentityCandidate = (candidate: (typeof identityCandidates)[number]) => {
    const existing = selectedIdentityByPath.get(candidate.targetPath);
    const next = existing
      ? documentIdentityFields.filter((field) => field.targetPath !== candidate.targetPath)
      : [...documentIdentityFields, {
          mappingId: candidate.cell.id,
          targetPath: candidate.targetPath,
          label: candidate.label,
          valueType: candidate.targetPath === "document.unifiedEarTag" ? "digits" as const : "text" as const,
          exactLength: candidate.targetPath === "document.unifiedEarTag" ? 10 : null,
          matchRole: documentIdentityFields.length === 0 ? "primary" as const : "confirmation" as const,
          displayRole: documentIdentityFields.length === 0 ? "title" as const : "none" as const,
        }];
    const normalized = normalizedIdentityFields(next);
    onDocumentIdentityFieldsChange?.(normalized);
    setSelectedId(candidate.cell.id);
    setSelectedIds(new Set(normalized.flatMap((field) => {
      const selectedCandidate = identityCandidates.find((item) => item.targetPath === field.targetPath);
      return selectedCandidate ? [selectedCandidate.cell.id] : [];
    })));
    setMessage(existing
      ? `「${candidate.label}」を書類の見分け方から外しました。`
      : `「${candidate.label}」を書類を見分ける情報へ追加しました。`);
  };
  const updateIdentityField = (
    targetPath: string,
    property: "matchRole" | "displayRole",
    value: DocumentIdentity[typeof property],
  ) => {
    const next = documentIdentityFields.map((field) => {
      if (field.targetPath === targetPath) return { ...field, [property]: value };
      if (property === "matchRole" && value === "primary") {
        return { ...field, matchRole: "confirmation" as const };
      }
      if (property === "displayRole" && value !== "none" && field.displayRole === value) {
        return { ...field, displayRole: "none" as const };
      }
      return field;
    });
    onDocumentIdentityFieldsChange?.(next);
  };
  useEffect(() => {
    if (documentIdentityFields.length === 0) return;
    const validFields = documentIdentityFields.filter((field) => (
      identityCandidates.some((candidate) => candidate.targetPath === field.targetPath)
    ));
    if (validFields.length !== documentIdentityFields.length) {
      onDocumentIdentityFieldsChange?.(normalizedIdentityFields(validFields));
      return;
    }
    if (workflowStep === "identity") {
      const selectedCandidateCells = identityCandidates.filter((candidate) => (
        selectedIdentityByPath.has(candidate.targetPath)
      ));
      const focusCandidate = selectedCandidateCells.find((candidate) => (
        candidate.targetPath === primaryIdentityField?.targetPath
      )) ?? selectedCandidateCells[0];
      setSelectedId(focusCandidate?.cell.id ?? null);
      setSelectedIds(new Set(selectedCandidateCells.map((candidate) => candidate.cell.id)));
    }
  }, [
    documentIdentityFields,
    identityCandidates,
    onDocumentIdentityFieldsChange,
    primaryIdentityField?.targetPath,
    selectedIdentityByPath,
    workflowStep,
  ]);
  const assignedPaths = useMemo(
    () => new Set(layout.relations.map((relation) => relation.targetPath)),
    [layout.relations],
  );
  const meaningCatalogSections = useMemo(
    () => [...new Set(labelPoolEntries.map((entry) => entry.section))],
    [labelPoolEntries],
  );
  const filteredLabelPoolEntries = useMemo(() => {
    const query = normalizeLabelPoolText(meaningCatalogQuery);
    return labelPoolEntries.filter((entry) => (
      (meaningCatalogSection === "all" || entry.section === meaningCatalogSection)
      && (!query || [entry.label, entry.section, ...entry.aliases].some(
        (value) => normalizeLabelPoolText(value).includes(query),
      ))
    ));
  }, [labelPoolEntries, meaningCatalogQuery, meaningCatalogSection]);
  const labelPoolUsageCounts = useMemo(() => {
    const counts = new Map<string, number>();
    layout.relations.forEach((relation) => {
      counts.set(relation.targetPath, (counts.get(relation.targetPath) ?? 0) + 1);
    });
    return new Map(labelPoolEntries.map((entry) => [
      entry.key,
      entry.paths.reduce((total, path) => total + (counts.get(path) ?? 0), 0),
    ]));
  }, [labelPoolEntries, layout.relations]);
  const similarLabelPoolEntries = useMemo(() => {
    const query = normalizeLabelPoolText(customLabel);
    if (!query) return [];
    return labelPoolEntries.filter((entry) => [entry.label, ...entry.aliases].some((value) => {
      const candidate = normalizeLabelPoolText(value);
      return candidate.includes(query) || query.includes(candidate);
    })).slice(0, 5);
  }, [customLabel, labelPoolEntries]);
  const sections = useMemo(() => {
    const result = new Map<string, FieldMappingTarget[]>();
    availableTargets.forEach((target) => result.set(
      target.section,
      [...(result.get(target.section) ?? []), target],
    ));
    return [...result.entries()];
  }, [availableTargets]);
  const showLabelStep = !workflowStep || workflowStep === "classify";
  const showClassificationStep = !workflowStep || workflowStep === "classify";
  const showMeaningStep = !workflowStep || workflowStep === "meaning";
  const showRecordStep = !workflowStep || workflowStep === "records";
  const showIdentityStep = workflowStep === "identity";
  const showReviewStep = !workflowStep || workflowStep === "review";
  const cellsVisibleInList = layout.cells.filter((cell) => {
    if (!workflowStep || workflowStep === "classify" || workflowStep === "review") return true;
    if (workflowStep === "identity") {
      return identityCandidateByCellId.has(cell.id);
    }
    if (workflowStep === "meaning") {
      return cell.role === "label" || cell.role === "parent_label"
        || cell.role === "input" || cell.role === "mark";
    }
    return cell.role === "label" || cell.role === "parent_label";
  });

  const detect = async () => {
    if (disabled || operationLockRef.current) return;
    if (
      dirty
      && !window.confirm(
        "保存していないレイアウト変更があります。変更を破棄して枠を再検出しますか？",
      )
    ) return;
    operationLockRef.current = true;
    setBusy(true);
    setDetecting(true);
    setMessage("OpenCVを中心に帳票の枠を再検出しています…");
    try {
      const detected = await onDetect();
      const normalized = normalizeLayout(detected);
      setLayout(normalized);
      setSelectedRecordSectionId(normalized.recordSections?.[0]?.id ?? "");
      setSelectedId(null);
      setSelectedIds(new Set());
      relationHistory.current = [];
      setHistoryDepth(0);
      setMessage(`${detected.cells.length}個の枠を検出しました。種類と項目名の文字候補を確認してください。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "セルを検出できませんでした。");
    } finally {
      setDetecting(false);
      setBusy(false);
      operationLockRef.current = false;
    }
  };

  const recognizeLabels = async () => {
    if (disabled || operationLockRef.current || !onRecognizeLabels) return;
    const labelCount = layout.cells.filter(
      (cell) => (cell.role === "label" || cell.role === "parent_label")
        && !ocrProtectedLabelIds.has(cell.id),
    ).length;
    if (labelCount === 0) {
      setMessage("文字候補を取得する未設定の項目名がありません。読み取る内容や繰り返し部分を設定済みの項目名は対象から除外されます。");
      return;
    }
    operationLockRef.current = true;
    setBusy(true);
    setRecognizingLabels(true);
    setMessage(`${labelCount}個の項目名から文字候補を取得しています…`);
    try {
      const recognized = normalizeLayout(await onRecognizeLabels(layout));
      setLayout(recognized);
      setSelectedPath("");
      const recognizedCount = recognized.cells.filter(
        (cell) => (cell.role === "label" || cell.role === "parent_label")
          && !ocrProtectedLabelIds.has(cell.id)
          && cell.text.trim().length > 0,
      ).length;
      setMessage(
        `${labelCount}個の項目名を確認し、${recognizedCount}個に文字候補を反映しました。未認識の文字は右側で修正できます。`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "項目名の文字候補を取得できませんでした。");
    } finally {
      setRecognizingLabels(false);
      setBusy(false);
      operationLockRef.current = false;
    }
  };

  const save = useCallback(async (automatic = false): Promise<boolean> => {
    if ((!dirty && !allowUnchangedSave) || disabled || operationLockRef.current) {
      return !dirty;
    }
    operationLockRef.current = true;
    setBusy(true);
    try {
      const prepared = repairDuplicateCalvingMarkRows(layout);
      const saved = normalizeLayout(await onSave(prepared.layout));
      setLayout(saved);
      setBaselineSignature(layoutSignature(saved));
      relationHistory.current = [];
      setHistoryDepth(0);
      setMessage(
        prepared.repairedCellCount > 0
          ? `重複していた${prepared.repairedCellCount}個の選択マーク欄を繁殖履歴の行へ振り分け、テンプレートを保存しました。`
          : automatic
            ? `編集中の内容を自動保存しました（${saved.cells.length}個の矩形）。`
            : `テンプレートへ${saved.cells.length}個の矩形を保存しました。`,
      );
      if (draftStorageKey) {
        try {
          window.localStorage.removeItem(draftStorageKey);
        } catch {
          // The server save succeeded, so a storage cleanup failure is non-fatal.
        }
      }
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "レイアウトを保存できませんでした。");
      return false;
    } finally {
      setBusy(false);
      operationLockRef.current = false;
    }
  }, [allowUnchangedSave, disabled, dirty, draftStorageKey, layout, onSave]);

  useImperativeHandle(ref, () => ({
    saveIfNeeded: () => save(false),
  }), [save]);

  useEffect(() => {
    if (
      !dirty
      || disabled
      || busy
      || !workflowStep
      || !(["classify", "meaning", "records"] as LayoutWorkflowStep[]).includes(workflowStep)
      || lastAutoSaveAttemptSignatureRef.current === currentSignature
    ) return;
    const timer = window.setTimeout(() => {
      lastAutoSaveAttemptSignatureRef.current = currentSignature;
      void save(true);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [busy, currentSignature, dirty, disabled, save, workflowStep]);

  const redraw = () => {
    const canvas = canvasRef.current;
    const image = imageRef.current;
    if (!canvas || !image) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    if (mode === "identity") {
      layout.cells.forEach((cell) => {
        const candidate = identityCandidateByCellId.get(cell.id);
        const selectedIdentity = candidate
          ? selectedIdentityByPath.get(candidate.targetPath)
          : null;
        const isSelected = Boolean(selectedIdentity);
        const isPrimary = selectedIdentity?.matchRole === "primary";
        context.save();
        if (!candidate) {
          context.fillStyle = "rgba(239, 242, 243, 0.68)";
          context.fillRect(cell.x, cell.y, cell.width, cell.height);
          context.strokeStyle = "rgba(91, 108, 112, 0.38)";
          context.lineWidth = 2;
          context.setLineDash([8, 6]);
          context.strokeRect(cell.x, cell.y, cell.width, cell.height);
          context.restore();
          return;
        }
        context.fillStyle = isPrimary
          ? "rgba(30, 105, 154, 0.22)"
          : isSelected
            ? "rgba(177, 111, 0, 0.19)"
            : "rgba(0, 119, 125, 0.12)";
        context.strokeStyle = isPrimary ? "#1e699a" : isSelected ? "#a46400" : "#00777d";
        context.lineWidth = isSelected ? 8 : 4;
        context.fillRect(cell.x, cell.y, cell.width, cell.height);
        context.strokeRect(cell.x, cell.y, cell.width, cell.height);

        const fontSize = Math.max(11, Math.min(22, layout.canonicalWidth / 75));
        const badgeHeight = fontSize + 12;
        context.font = `800 ${fontSize}px sans-serif`;
        const badgeWidth = Math.min(
          Math.max(56, context.measureText(candidate.label).width + (isSelected ? 64 : 24)),
          Math.max(56, cell.width),
        );
        const badgeX = cell.x;
        const badgeY = Math.max(0, cell.y - badgeHeight);
        context.fillStyle = isPrimary ? "#1e699a" : isSelected ? "#a46400" : "rgba(0, 92, 97, 0.92)";
        context.fillRect(badgeX, badgeY, badgeWidth, badgeHeight);
        context.fillStyle = "#ffffff";
        context.textBaseline = "middle";
        context.textAlign = "left";
        context.fillText(
          `${isPrimary ? "基準 " : isSelected ? "確認 " : ""}${candidate.label}`,
          badgeX + 8,
          badgeY + badgeHeight / 2,
          Math.max(1, badgeWidth - 16),
        );
        context.restore();
      });
      return;
    }

    layout.cells.forEach((cell) => {
      const color = ROLE_COLOR[cell.role];
      context.save();
      const isPrimarySelection = cell.id === selectedId;
      const isRangeSelection = selectedIds.has(cell.id);
      context.strokeStyle = isPrimarySelection ? "#d62929" : isRangeSelection ? "#2468a2" : color;
      context.lineWidth = isPrimarySelection ? 5 : isRangeSelection ? 5 : 3;
      if (cell.role === "ignored") context.setLineDash([12, 8]);
      context.fillStyle = `${color}${cell.role === "ignored" ? "0c" : "19"}`;
      context.fillRect(cell.x, cell.y, cell.width, cell.height);
      context.strokeRect(cell.x, cell.y, cell.width, cell.height);
      context.setLineDash([]);
      if (cell.role === "mark") {
        context.setLineDash([5, 4]);
        context.lineWidth = 2;
        for (const markSet of cell.markSets ?? []) {
          for (const option of markSet.options) {
            context.strokeRect(
              cell.x + option.x,
              cell.y + option.y,
              option.width,
              option.height,
            );
          }
        }
        context.setLineDash([]);
      }
      drawOcrLabelText(context, cell, layout.canonicalWidth, ocrLabelAlignment);
      context.restore();
    });

    recordSections.forEach((section) => {
      const anchors = section.recordLabelCellIds
        .map((labelId) => cellById.get(labelId))
        .filter((cell): cell is LayoutCell => Boolean(cell))
        .sort((first, second) => first.x - second.x || first.y - second.y);
      if (anchors.length === 0) return;
      const groupCount = recordGroups.filter((group) => group.sectionId === section.id).length;
      context.save();
      const bandY = anchors.reduce(
        (total, anchor) => total + anchor.y + anchor.height / 2,
        0,
      ) / anchors.length;
      const firstAnchor = anchors[0];
      const lastAnchor = anchors[anchors.length - 1];
      const bandStartX = firstAnchor.x + firstAnchor.width / 2;
      const bandEndX = lastAnchor.x + lastAnchor.width / 2;
      const parent = cellById.get(section.parentLabelCellId ?? "");
      if (parent) {
        context.strokeStyle = "#16805d";
        context.lineWidth = 5;
        context.setLineDash([13, 7]);
        context.beginPath();
        context.moveTo(parent.x + parent.width / 2, parent.y + parent.height / 2);
        context.lineTo((bandStartX + bandEndX) / 2, bandY);
        context.stroke();
      }
      context.strokeStyle = "#2468a2";
      context.lineWidth = 5;
      context.setLineDash([10, 6]);
      context.beginPath();
      context.moveTo(bandStartX, bandY);
      context.lineTo(bandEndX, bandY);
      context.stroke();
      anchors.forEach((anchor) => {
        context.strokeRect(anchor.x, anchor.y, anchor.width, anchor.height);
      });
      context.setLineDash([]);
      const badgeHeight = Math.max(16, Math.min(24, layout.canonicalWidth / 65));
      const badgeWidth = Math.max(
        1,
        Math.min(Math.max(38, badgeHeight * 3.3), Math.max(1, lastAnchor.width - 4)),
      );
      const badgeX = lastAnchor.x + lastAnchor.width - badgeWidth - 2;
      const badgeY = lastAnchor.y + lastAnchor.height - badgeHeight - 2;
      context.fillStyle = "rgba(36, 104, 162, 0.94)";
      context.fillRect(badgeX, badgeY, badgeWidth, badgeHeight);
      context.fillStyle = "#ffffff";
      context.font = `800 ${Math.max(10, badgeHeight * 0.58)}px sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(`R ${groupCount}`, badgeX + badgeWidth / 2, badgeY + badgeHeight / 2);
      context.restore();
    });

    layout.relations.forEach((relation) => {
      const input = cellById.get(relation.inputCellId);
      if (!input) return;
      relation.labelCellIds.forEach((labelId) => {
        const label = cellById.get(labelId);
        if (!label) return;
        context.save();
        context.strokeStyle = relation.axis === "horizontal" ? "#6b3fc5" : "#c43f82";
        context.lineWidth = 4;
        context.setLineDash([10, 6]);
        context.beginPath();
        context.moveTo(input.x + input.width / 2, input.y + input.height / 2);
        context.lineTo(label.x + label.width / 2, label.y + label.height / 2);
        context.stroke();
        context.restore();
      });
    });

    if (draft) {
      const isSelectionDraft = mode === "select" || mode === "classify";
      context.strokeStyle = isSelectionDraft ? "#2468a2" : "#d62929";
      context.fillStyle = isSelectionDraft ? "#2468a220" : "#d6292920";
      context.lineWidth = 4;
      context.setLineDash([10, 6]);
      context.fillRect(draft.x, draft.y, draft.width, draft.height);
      context.strokeRect(draft.x, draft.y, draft.width, draft.height);
      context.setLineDash([]);
    }
    if (linkPoint && linkLabelId.current) {
      const label = cellById.get(linkLabelId.current);
      if (label) {
        const start = {
          x: label.x + label.width / 2,
          y: label.y + label.height / 2,
        };
        const highlightedCells = mode === "parent"
          ? recordSections.flatMap((section) => {
            const anchors = section.recordLabelCellIds
              .map((labelId) => cellById.get(labelId))
              .filter((cell): cell is LayoutCell => Boolean(cell));
            return anchors.some((anchor) => pointInsideRect(linkPoint, anchor)) ? anchors : [];
          })
          : mode === "record"
            ? recordLabelCellsCrossedByLine(
              start,
              linkPoint,
              layout.cells.filter((cell) => cell.role === "label"),
            )
            : linkLaneCells(
              start,
              linkPoint,
              layout.cells.filter((cell) => cell.role === "input" || cell.role === "mark"),
            );
        highlightedCells.forEach((cell) => {
          context.save();
          context.fillStyle = "#6b3fc530";
          context.strokeStyle = "#6b3fc5";
          context.lineWidth = 6;
          context.setLineDash([10, 5]);
          context.fillRect(cell.x, cell.y, cell.width, cell.height);
          context.strokeRect(cell.x, cell.y, cell.width, cell.height);
          context.restore();
        });
        context.strokeStyle = "#6b3fc5";
        context.lineWidth = 7;
        context.globalAlpha = 0.75;
        context.beginPath();
        context.moveTo(start.x, start.y);
        context.lineTo(linkPoint.x, linkPoint.y);
        context.stroke();
        context.globalAlpha = 1;
      }
    }
  };

  useEffect(redraw, [
    layout,
    selectedId,
    selectedIds,
    mode,
    draft,
    linkPoint,
    cellById,
    recordGroups,
    recordSections,
    recordSectionById,
    ocrLabelAlignment,
    identityCandidateByCellId,
    selectedIdentityByPath,
  ]);
  useEffect(() => {
    const image = new Image();
    image.onload = () => {
      imageRef.current = image;
      redraw();
    };
    image.src = source;
    return () => {
      image.onload = null;
    };
  // redraw is intentionally driven by state effects after the image is cached.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  const point = (
    event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>,
  ): Point => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.round(Math.max(0, Math.min(
        layout.canonicalWidth,
        (event.clientX - bounds.left) * layout.canonicalWidth / bounds.width,
      ))),
      y: Math.round(Math.max(0, Math.min(
        layout.canonicalHeight,
        (event.clientY - bounds.top) * layout.canonicalHeight / bounds.height,
      ))),
    };
  };
  const cellAt = (location: Point) => [...layout.cells].reverse().find(
    (cell) => location.x >= cell.x
      && location.x <= cell.x + cell.width
      && location.y >= cell.y
      && location.y <= cell.y + cell.height,
  );
  const rectFrom = (start: Point, end: Point): Rect => ({
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(start.x - end.x),
    height: Math.abs(start.y - end.y),
  });
  const patchCell = (id: string, patch: Partial<LayoutCell>) => setLayout((current) => ({
    ...current,
    cells: current.cells.map((cell) => cell.id === id ? { ...cell, ...patch } : cell),
  }));
  const patchMarkCells = (updates: ReadonlyMap<string, LayoutMarkSet[]>) => {
    if (updates.size === 0) return;
    setLayout((current) => ({
      ...current,
      cells: current.cells.map((cell) => {
        const markSets = updates.get(cell.id);
        return markSets ? { ...cell, markSets } : cell;
      }),
    }));
  };
  const setRoles = (ids: Iterable<string>, role: LayoutCell["role"]) => {
    const idSet = new Set(ids);
    if (idSet.size === 0) return;
    setLayout((current) => {
      const markRowIndexes = role === "mark"
        ? inferCalvingMarkRowIndexes(
          current,
          current.cells.filter((cell) => idSet.has(cell.id)),
        )
        : new Map<string, number>();
      let relations = current.relations;
      if (role !== "input" && role !== "mark") {
        relations = relations.filter((relation) => !idSet.has(relation.inputCellId));
      }
      if (role !== "label") {
        relations = relations.map((relation) => ({
          ...relation,
          labelCellIds: relation.labelCellIds.filter((labelId) => !idSet.has(labelId)),
        }));
      }
      const invalidatedSectionIds = new Set(
        (current.recordSections ?? [])
          .filter((section) => (
            role !== "label"
            && section.recordLabelCellIds.some((labelId) => idSet.has(labelId))
          ))
          .map((section) => section.id),
      );
      const recordSections = (current.recordSections ?? []).map((section) => ({
        ...section,
        recordLabelCellIds: invalidatedSectionIds.has(section.id)
          ? []
          : section.recordLabelCellIds,
        parentLabelCellId: (
          role !== "label"
          && role !== "parent_label"
          && section.parentLabelCellId
          && idSet.has(section.parentLabelCellId)
        )
          ? null
          : section.parentLabelCellId,
      }));
      const recordGroups = (role === "input" || role === "mark"
        ? current.recordGroups
        : (current.recordGroups ?? [])
          .map((group) => ({
            ...group,
            inputCellIds: group.inputCellIds.filter((inputId) => !idSet.has(inputId)),
          }))
          .filter((group) => group.inputCellIds.length > 0))
        ?.filter((group) => !invalidatedSectionIds.has(group.sectionId));
      const structureChanged = invalidatedSectionIds.size > 0
        || (recordGroups?.length ?? 0) !== (current.recordGroups?.length ?? 0)
        || recordSections.some((section, index) => (
          section.parentLabelCellId
          !== (current.recordSections ?? [])[index]?.parentLabelCellId
        ));
      return {
        ...current,
        cells: current.cells.map((cell) => {
          if (!idSet.has(cell.id)) return cell;
          if (role !== "mark" || (cell.markSets?.length ?? 0) > 0) {
            return { ...cell, role };
          }
          const relatedPath = current.relations.find(
            (relation) => relation.inputCellId === cell.id
              && relation.targetPath.startsWith("calvings."),
          )?.targetPath;
          const relatedIndex = relatedPath ? Number(relatedPath.split(".")[1]) : NaN;
          const group = (current.recordGroups ?? []).find(
            (candidate) => candidate.inputCellIds.includes(cell.id),
          );
          const section = (current.recordSections ?? []).find(
            (candidate) => candidate.id === group?.sectionId,
          );
          const rowIndex = Number.isInteger(relatedIndex)
            ? relatedIndex
            : section?.collectionPath === "calvings" && group
              ? group.order - 1
              : markRowIndexes.get(cell.id) ?? 0;
          return {
            ...cell,
            role,
            markSets: calvingMarkPreset(cell, rowIndex),
          };
        }),
        relations,
        recordSections,
        recordGroups,
        recordStructureVerified: structureChanged
          ? false
          : current.recordStructureVerified,
      };
    });
  };
  const setRole = (id: string, role: LayoutCell["role"]) => setRoles([id], role);
  const ensureAutoLabelPoolEntry = (labelText: string): LayoutLabelPoolEntry | null => {
    const entry = createAutoLabelPoolEntry(labelText, availableTargets);
    if (!entry) return null;
    setLayout((current) => (
      (current.labelPool ?? []).some((candidate) => candidate.targetPath === entry.targetPath)
        ? current
        : { ...current, labelPool: [...(current.labelPool ?? []), entry] }
    ));
    return entry;
  };
  const assignSelectedInputDirectly = (requestedPath = selectedPath) => {
    if (!selected || selected.role !== "input" || !requestedPath) return;
    if (!targetByPath.has(requestedPath)) {
      setMessage("保存先を選択してください。");
      return;
    }
    rememberRelations(layout.relations);
    const existing = layout.relations.filter(
      (relation) => relation.inputCellId === selected.id,
    );
    setSelectedPath(requestedPath);
    setLayout((current) => ({
      ...current,
      relations: existing.length > 0
        ? current.relations.map((relation) => (
            relation.inputCellId === selected.id
              ? { ...relation, targetPath: requestedPath }
              : relation
          ))
        : [
            ...current.relations,
            {
              id: `direct_${selected.id}`,
              inputCellId: selected.id,
              labelCellIds: [],
              axis: "horizontal",
              order: 1,
              targetPath: requestedPath,
            },
          ],
    }));
    setMessage(
      `${selected.id} を ${targetByPath.get(requestedPath)?.label ?? requestedPath} として設定しました。`,
    );
  };
  const addCustomTarget = () => {
    const label = customLabel.normalize("NFKC").trim();
    if (!label) return;
    const existing = findLabelPoolMatch(label, labelPoolEntries);
    if (existing?.paths[0]) {
      setSelectedPath(existing.paths[0]);
      if (selected?.role === "input") assignSelectedInputDirectly(existing.paths[0]);
      setCustomLabel("");
      setCustomTargetPlacement(null);
      setMessage(`「${existing.label}」は保存する項目に登録済みです。`);
      return;
    }
    const generated = createAutoLabelPoolEntry(label, availableTargets);
    if (!generated) {
      setMessage("保存する項目を追加できませんでした。項目名を確認してください。");
      return;
    }
    const entry: LayoutLabelPoolEntry = {
      ...generated,
      id: `label_pool_${Date.now()}`,
      section: "追加項目",
      aliases: [],
      source: "manual",
    };
    if (selected?.role === "input") rememberRelations(layout.relations);
    setLayout((current) => {
      const relations = selected?.role === "input"
        ? current.relations.some((relation) => relation.inputCellId === selected.id)
          ? current.relations.map((relation) => (
              relation.inputCellId === selected.id
                ? { ...relation, targetPath: entry.targetPath }
                : relation
            ))
          : [...current.relations, {
              id: `direct_${selected.id}`,
              inputCellId: selected.id,
              labelCellIds: [],
              axis: "horizontal" as const,
              order: 1,
              targetPath: entry.targetPath,
            }]
        : current.relations;
      return {
        ...current,
        labelPool: [...(current.labelPool ?? []), entry],
        relations,
      };
    });
    setSelectedPath(entry.targetPath);
    setCustomLabel("");
    setCustomTargetPlacement(null);
    setMessage(selected?.role === "input"
      ? `「${label}」を保存する項目へ追加し、選択中の記入欄へ設定しました。`
      : `「${label}」を保存する項目へ追加しました。`);
  };
  const customTargetAddition = (
    placement: "sidebar" | "catalog",
    buttonLabel: string,
  ) => {
    const open = customTargetPlacement === placement;
    return (
      <div className={`meaning-target-addition ${placement === "sidebar" ? "compact-addition" : ""}`}>
        <button
          type="button"
          className="secondary"
          aria-expanded={open}
          onClick={() => setCustomTargetPlacement((current) => (
            current === placement ? null : placement
          ))}
        >
          {open ? "追加を閉じる" : buttonLabel}
        </button>
        {open && (
          <div className={`meaning-target-creator ${placement === "sidebar" ? "compact-creator" : ""}`}>
            <label>
              新しい項目名
              <input
                value={customLabel}
                maxLength={100}
                placeholder="例: 治療歴"
                onChange={(event) => setCustomLabel(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && customLabel.trim()) {
                    event.preventDefault();
                    addCustomTarget();
                  }
                }}
              />
            </label>
            <button
              type="button"
              className="primary"
              disabled={!customLabel.trim()}
              onClick={addCustomTarget}
            >
              この項目を追加
            </button>
            <small>
              {placement === "sidebar"
                ? "追加した項目を、選択中の記入欄へ設定します。"
                : "内部の識別子はシステムが自動生成します。"}
            </small>
            {similarLabelPoolEntries.length > 0 && (
              <p className="meaning-target-similar" role="status">
                類似する登録済み項目: {similarLabelPoolEntries.map((entry) => entry.label).join("、")}
              </p>
            )}
          </div>
        )}
      </div>
    );
  };
  const finishLink = (end: Point) => {
    const labelId = linkLabelId.current;
    linkLabelId.current = null;
    setLinkPoint(null);
    if (!labelId) return;
    const label = cellById.get(labelId);
    if (!label) return;
    const start = { x: label.x + label.width / 2, y: label.y + label.height / 2 };
    const endCell = cellAt(end);
    const resolvedEnd = endCell?.role === "input" || endCell?.role === "mark"
      ? {
        x: endCell.x + endCell.width / 2,
        y: endCell.y + endCell.height / 2,
      }
      : end;
    const axis: LayoutRelation["axis"] = Math.abs(resolvedEnd.x - start.x) >= Math.abs(resolvedEnd.y - start.y)
      ? "horizontal"
      : "vertical";
    const inputs = linkLaneCells(
      start,
      resolvedEnd,
      layout.cells.filter((cell) => cell.role === "input" || cell.role === "mark"),
    );
    if (inputs.length === 0) {
      setMessage("スライド経路に手書き文字欄がありません。記入欄を通過するようにドラッグしてください。");
      return;
    }
    const existingTargetPaths = targetPathsForInputs(inputs, layout.relations);
    const markTargetPaths = inputs.every((input) => input.role === "mark")
      ? inputs.map((input) => input.markSets?.[0]?.targetPath ?? "")
      : null;
    const completeMarkTargetPaths = markTargetPaths?.every(Boolean)
      ? markTargetPaths
      : null;
    const inferredTargetPath = completeMarkTargetPaths?.[0]
      || selectedPath
      || inferTargetPath(label.text, inputs.length, availableTargets, assignedPaths)
      || inferRecordTargetPath(
        inputs,
        layout.cells,
        recordGroups,
        recordSections,
        availableTargets,
      )
      || inferTableColumnTargetPath(
        inputs,
        layout.cells,
        recordSectionById.get(selectedRecordSectionId),
        availableTargets,
      );
    const autoEntries = !inferredTargetPath
      && !existingTargetPaths
      && inputs.every((input) => input.role === "input")
      ? inputs.length === 1
        ? [createAutoLabelPoolEntry(label.text, availableTargets)].filter(
          (entry): entry is LayoutLabelPoolEntry => Boolean(entry),
        )
        : createAutoRepeatedLabelPoolEntries(label.text, inputs.length, availableTargets) ?? []
      : [];
    const autoEntry = autoEntries.length === 1
      ? autoEntries[0]
      : null;
    const targetPath = inferredTargetPath ?? autoEntry?.targetPath;
    const groupedTargetPaths = targetPath
      ? targetPathsFromRecordGroups(
        targetPath,
        inputs,
        recordGroups,
        recordSections,
        availableTargets,
      )
      : null;
    const requestedTargetPaths = autoEntries.length > 0
      ? autoEntries.map((entry) => entry.targetPath)
      : completeMarkTargetPaths
      ?? (selectedPath
      ? groupedTargetPaths
        ?? expandTargetPaths(selectedPath, inputs.length, availableTargets)
      : existingTargetPaths
        ?? groupedTargetPaths
        ?? (targetPath ? expandTargetPaths(targetPath, inputs.length, availableTargets) : null));
    const targetPaths = requestedTargetPaths && inputs.every((input) => input.role === "input")
      ? resolveAvailableLinkTargetPaths(
        requestedTargetPaths,
        inputs,
        layout.relations,
        availableTargets,
      )
      : requestedTargetPaths;
    if (!targetPaths) {
      const requestedTargetConflict = requestedTargetPaths?.some((path) => (
        layout.relations.some((relation) => (
          relation.targetPath === path
          && !inputs.some((input) => input.id === relation.inputCellId)
        ))
      ));
      const capacity = targetPath
        ? sequentialTargetCapacity(targetPath, availableTargets)
        : null;
      setMessage(
        requestedTargetConflict
          ? "選択した保存先は別の記入欄で使用中です。同じ項目名から別の記入欄へ接続する場合は、別の保存先を選択してください。"
        : !targetPath
          ? "保存先を自動推定できませんでした。右側のJSON項目を選択してからもう一度ドラッグしてください。"
          : capacity !== null && inputs.length > capacity
          ? `この列は最大${capacity}欄です。現在${inputs.length}欄が選択されています。隣の列を巻き込んでいないか確認してください。`
          : inputs.length > 1
          ? "複数欄を一括設定するには、保存先で表の1行目（例: 売却子牛 1行目・価格）を選択してください。"
          : "選択した保存先を割り当てられませんでした。",
      );
      return;
    }
    rememberRelations(layout.relations);
    setLayout((current) => {
      let relations = mergeLabelRelations(
        current.relations,
        inputs,
        labelId,
        axis,
        targetPaths,
      );
      relations = renumberRelations(relations, current.cells);
      return {
        ...current,
        labelPool: autoEntries.length > 0
          ? [
            ...(current.labelPool ?? []),
            ...autoEntries.filter((entry) => (
              !(current.labelPool ?? []).some(
                (candidate) => candidate.targetPath === entry.targetPath,
              )
            )),
          ]
          : current.labelPool,
        relations,
        allowCrossAxisLabels: true,
      };
    });
    setSelectedId(inputs[0].id);
    setSelectedIds(new Set(inputs.map((input) => input.id)));
    setSelectedPath("");
    setMessage(
      `${label.text || label.id}から${inputs.length}個の手書き文字欄へ、`
      + `${axis === "horizontal" ? "横方向" : "縦方向"}にまとめて意味づけしました。`
      + ` 保存先: ${autoEntries.length > 1
        ? `${label.text || label.id} ${autoEntries.length}行分`
        : autoEntry?.label ?? targetByPath.get(targetPaths[0])?.label ?? targetPaths[0]}`
      + `${autoEntries.length > 0 ? "（保存する情報へ自動追加）" : ""}`,
    );
  };

  const createRecordsFromColumnLabels = (labelIds: string[]) => {
    const rows = recordRowsFromColumnLabels(labelIds, layout.cells, layout.relations);
    const labels = labelIds
      .map((labelId) => cellById.get(labelId))
      .filter((cell): cell is LayoutCell => Boolean(cell));
    if (!rows || labels.length !== labelIds.length) {
      setMessage("選択した列の一部に読み取る内容が設定されていないか、列ごとの行数が一致しません。ステップ2の縦方向の意味づけを確認してください。");
      return;
    }
    const selectedSection = recordSectionById.get(selectedRecordSectionId);
    const genericRows = rows.collectionPath === null;
    const reusableGenericSection = genericRows && selectedSection?.recordType === "generic"
      ? selectedSection
      : undefined;
    const genericCollectionPath = reusableGenericSection?.collectionPath
      ?? `genericRecords.${genericRecordCollectionKey(recordSectionTitle, recordSections)}`;
    const collectionPath = rows.collectionPath ?? genericCollectionPath;
    const sameLabelBand = (candidate: LayoutRecordSection) => (
      candidate.recordLabelCellIds.length === labelIds.length
      && labelIds.every((labelId) => candidate.recordLabelCellIds.includes(labelId))
    );
    const matchingSection = recordSections.find(
      (candidate) => candidate.collectionPath === collectionPath
        && sameLabelBand(candidate),
    );
    const reusableSelectedSection = selectedSection?.collectionPath === collectionPath
      && (
        selectedSection.recordLabelCellIds.length === 0
        || sameLabelBand(selectedSection)
      )
      ? selectedSection
      : undefined;
    const existingSection = reusableSelectedSection ?? matchingSection;
    const inheritedParentLabelCellId = recordSections.find(
      (candidate) => candidate.collectionPath === collectionPath
        && candidate.parentLabelCellId,
    )?.parentLabelCellId ?? null;
    const effectiveRowKeyStrategy = existingSection?.rowKeyStrategy
      ?? (genericRows ? rowKeyStrategy : "position");
    const matchedRowKeys = effectiveRowKeyStrategy === "printed"
      ? matchRowKeyCells(rows.rows, layout.cells)
      : rows.rows.map(() => null);
    if (effectiveRowKeyStrategy === "printed" && !matchedRowKeys) {
      setMessage("行番号の数または位置が表の行と一致しません。ステップ1で1～9などの番号セルを「行番号」にするか、行の決め方を「上から順番」に変更してください。");
      return;
    }
    const section: LayoutRecordSection = existingSection ?? {
      id: `section_${collectionPath.replace(/[^A-Za-z0-9_]/g, "_")}_${Date.now()}_${recordSections.length + 1}`,
      title: genericRows
        ? recordSectionTitle.trim() || "明細"
        : collectionPath === "saleCalves" ? "売却子牛一覧" : "繁殖履歴",
      collectionPath,
      recordType: genericRows
        ? "generic"
        : collectionPath === "saleCalves" ? "saleCalf" : "calving",
      recordLabelCellIds: labelIds,
      parentLabelCellId: genericRows ? null : inheritedParentLabelCellId,
      rowKeyStrategy: effectiveRowKeyStrategy,
      rowKeyCellIds: (matchedRowKeys ?? []).flatMap((cell) => cell ? [cell.id] : []),
      includeEmptyRows: false,
      source: "manual",
    };
    const previousByOrder = new Map(
      recordGroups
        .filter((group) => group.sectionId === section.id)
        .map((group) => [group.order, group]),
    );
    const groups: LayoutRecordGroup[] = rows.rows.map((row, index) => {
      const inputs = row.inputCellIds
        .map((inputId) => cellById.get(inputId))
        .filter((cell): cell is LayoutCell => Boolean(cell));
      const previous = previousByOrder.get(row.order);
      const rowKeyCell = matchedRowKeys?.[index] ?? null;
      return {
        id: previous?.id ?? `record_${section.id}_${row.order}`,
        sectionId: section.id,
        order: row.order,
        ...boundingRect(inputs, layout.canonicalWidth, layout.canonicalHeight),
        inputCellIds: row.inputCellIds,
        recordKeyField: genericRows ? "rowNumber" : "parity",
        recordKeyValue: rowKeyCell?.text.trim()
          || previous?.recordKeyValue
          || String(row.order),
        recordKeyCellId: rowKeyCell?.id ?? null,
        source: "manual",
      };
    });
    const nextSection = {
      ...section,
      recordLabelCellIds: labelIds,
      parentLabelCellId: labelIds.includes(section.parentLabelCellId ?? "")
        ? null
        : section.parentLabelCellId,
      rowKeyStrategy: effectiveRowKeyStrategy,
      rowKeyCellIds: (matchedRowKeys ?? []).flatMap((cell) => cell ? [cell.id] : []),
      includeEmptyRows: section.includeEmptyRows ?? false,
      source: "manual" as const,
    };
    const genericEntriesByInput = new Map<string, LayoutLabelPoolEntry>();
    if (genericRows) {
      labels.forEach((label) => {
        const columnInputs = layout.relations
          .filter((relation) => relation.labelCellIds.includes(label.id))
          .flatMap((relation) => {
            const input = cellById.get(relation.inputCellId);
            return input ? [input] : [];
          })
          .filter((cell, index, items) => items.findIndex((item) => item.id === cell.id) === index)
          .sort((first, second) => (
            first.y + first.height / 2 - (second.y + second.height / 2)
          ));
        const entries = createAutoRepeatedLabelPoolEntries(
          label.text,
          columnInputs.length,
          availableTargets,
        ) ?? [];
        columnInputs.forEach((input, index) => {
          const entry = entries[index];
          if (entry) genericEntriesByInput.set(input.id, entry);
        });
      });
    }
    setLayout((current) => {
      const sections = (current.recordSections ?? []).some(
        (candidate) => candidate.id === section.id,
      )
        ? (current.recordSections ?? []).map((candidate) => (
          candidate.id === section.id ? nextSection : candidate
        ))
        : [...(current.recordSections ?? []), nextSection];
      const nextInputIds = new Set(groups.flatMap((group) => group.inputCellIds));
      const nextGroups = [
        ...(current.recordGroups ?? []).filter((group) => (
          group.sectionId !== section.id
          && !group.inputCellIds.some((inputId) => nextInputIds.has(inputId))
        )),
        ...groups,
      ];
      const relations = genericRows
        ? renumberRelations(current.relations.map((relation) => {
          const entry = genericEntriesByInput.get(relation.inputCellId);
          return entry ? { ...relation, targetPath: entry.targetPath } : relation;
        }), current.cells)
        : current.relations;
      const nextLabelPool = genericRows
        ? [
          ...(current.labelPool ?? []),
          ...[...genericEntriesByInput.values()].filter((entry, index, entries) => (
            entries.findIndex((candidate) => candidate.targetPath === entry.targetPath) === index
            && !(current.labelPool ?? []).some(
              (candidate) => candidate.targetPath === entry.targetPath,
            )
          )),
        ]
        : current.labelPool;
      return {
        ...current,
        layoutSchemaVersion: 3,
        recordSections: sections,
        recordGroups: nextGroups,
        relations,
        labelPool: nextLabelPool,
        recordStructureVerified: recordStructureIsLinked(sections, nextGroups),
      };
    });
    setSelectedRecordSectionId(section.id);
    setSelectedId(labelIds[0] ?? null);
    setSelectedIds(new Set(labelIds));
    const labelNames = labels.map((label) => label.text.trim() || label.id).join(" / ");
    setMessage(
      `${labelNames}を「${nextSection.title}」の列として指定し、${groups.length}行・`
      + `${groups.reduce((total, group) => total + group.inputCellIds.length, 0)}個の記入欄を自動編成しました。`
      + `${nextSection.rowKeyStrategy === "printed" ? `${nextSection.rowKeyCellIds?.length ?? 0}個の行番号を使用します。` : "上からの順番を行番号として使用します。"}`
      + `${nextSection.recordType === "generic" ? "帳票上の見出しはなくても保存できます。" : nextSection.parentLabelCellId ? "親ラベルとの定義も維持されています。" : "次に親ラベルへ接続してください。"}`,
    );
  };

  const finishRecordLabelBand = (end: Point) => {
    const startLabelId = linkLabelId.current;
    linkLabelId.current = null;
    setLinkPoint(null);
    if (!startLabelId) return;
    const startLabel = cellById.get(startLabelId);
    if (!startLabel) return;
    const labels = recordLabelCellsCrossedByLine(
      {
        x: startLabel.x + startLabel.width / 2,
        y: startLabel.y + startLabel.height / 2,
      },
      end,
      layout.cells.filter((cell) => cell.role === "label"),
    );
    if (labels.length < 2) {
      setMessage("レコード作成では、最初の列ラベルから最後の列ラベルまで横方向にドラッグし、2個以上をまとめて選択してください。");
      return;
    }
    createRecordsFromColumnLabels(labels.map((label) => label.id));
  };

  const finishParentLink = (end: Point) => {
    const labelId = linkLabelId.current;
    linkLabelId.current = null;
    setLinkPoint(null);
    if (!labelId) return;
    const recordLabel = [...layout.cells].reverse().find((cell) => (
      cell.role === "label" && pointInsideRect(end, cell)
    ));
    const section = recordLabel
      ? recordSections.find((item) => item.recordLabelCellIds.includes(recordLabel.id))
      : undefined;
    if (!recordLabel || !section) {
      setMessage("親ラベルから、青線でまとめたレコード列ラベルのいずれかまで線を引いてください。");
      return;
    }
    const label = cellById.get(labelId);
    if (section.recordLabelCellIds.includes(labelId)) {
      setMessage("レコード列ラベルとは別の、表全体を表す親ラベルから接続してください。");
      return;
    }
    setLayout((current) => {
      const sections = (current.recordSections ?? []).map((item) => (
        item.collectionPath === section.collectionPath
          ? {
            ...item,
            parentLabelCellId: labelId,
            title: item.title.trim() || label?.text.trim() || item.title,
            source: "manual" as const,
          }
          : item
      ));
      return {
        ...current,
        recordSections: sections,
        recordStructureVerified: recordStructureIsLinked(
          sections,
          current.recordGroups ?? [],
        ),
      };
    });
    setSelectedRecordSectionId(section.id);
    setMessage(`${label?.text || labelId}を${section.title}の帳票上の見出しに設定しました。`);
  };

  const roleCounts = {
    label: layout.cells.filter((cell) => cell.role === "label").length,
    parentLabel: layout.cells.filter((cell) => cell.role === "parent_label").length,
    input: layout.cells.filter((cell) => cell.role === "input").length,
    mark: layout.cells.filter((cell) => cell.role === "mark").length,
    ignored: layout.cells.filter((cell) => cell.role === "ignored").length,
  };
  const arrayInputIds = new Set([
    ...layout.relations
      .filter((relation) => /^(saleCalves|calvings)\.\d+\./.test(relation.targetPath))
      .map((relation) => relation.inputCellId),
    ...layout.cells
      .filter((cell) => cell.role === "mark"
        && (cell.markSets ?? []).some((markSet) => /^(saleCalves|calvings)\.\d+\./.test(markSet.targetPath)))
      .map((cell) => cell.id),
  ]);
  const genericSectionIds = new Set(
    recordSections
      .filter((section) => section.recordType === "generic")
      .map((section) => section.id),
  );
  const genericRecordInputIds = new Set(
    recordGroups
      .filter((group) => genericSectionIds.has(group.sectionId))
      .flatMap((group) => group.inputCellIds),
  );
  const structuredInputIds = new Set([...arrayInputIds, ...genericRecordInputIds]);
  const groupedInputIds = new Set(
    recordGroups.flatMap((group) => group.inputCellIds),
  );
  const recordStructureReady = structuredInputIds.size === 0 || (
    layout.recordStructureVerified === true
    && [...structuredInputIds].every((inputId) => groupedInputIds.has(inputId))
  );
  const hasMeaningAssignments = layout.relations.length > 0 || layout.cells.some(
    (cell) => cell.role === "mark" && (cell.markSets?.length ?? 0) > 0,
  );
  const meaningAssignedCellIds = new Set([
    ...layout.relations.map((relation) => relation.inputCellId),
    ...layout.cells
      .filter((cell) => cell.role === "mark" && (cell.markSets?.length ?? 0) > 0)
      .map((cell) => cell.id),
  ]);
  const hasUnassignedFields = layout.cells.some(
    (cell) => (cell.role === "input" || cell.role === "mark")
      && !meaningAssignedCellIds.has(cell.id),
  );
  const layoutReadyForOcr = hasMeaningAssignments
    && recordStructureReady
    && !hasUnassignedFields;
  const templateReadyForOcr = layoutReadyForOcr && identityConfigurationReady;

  return (
    <div className="layout-editor" aria-busy={busy}>
      <fieldset className="layout-editor-fields" disabled={busy || disabled}>
      <div className="layout-commandbar" role="toolbar" aria-label="レイアウト編集">
        {workflowStep && (
          <strong className="layout-step-kicker">
            {{ classify: "欄の種類", meaning: "読み取る内容", records: "繰り返し部分", identity: "書類の見分け方", review: "確認・保存" }[workflowStep]}
          </strong>
        )}
        {showClassificationStep && (
          <button type="button" onClick={() => void detect()} disabled={busy}>
            {detecting ? "検出中…" : "枠を再検出"}
          </button>
        )}
        {(showLabelStep || showClassificationStep || showMeaningStep || showRecordStep || !workflowStep) && <button
          type="button"
          className="primary label-ocr-command"
          onClick={() => void recognizeLabels()}
          disabled={busy || !onRecognizeLabels}
        >
          {recognizingLabels ? "文字候補を取得中…" : "項目名の文字候補を取得"}
        </button>}
        {(showLabelStep || showClassificationStep || showMeaningStep || showRecordStep || !workflowStep) && <div className="ocr-alignment-switch" role="group" aria-label="OCR文字の配置">
          <span>文字位置</span>
          {([
            ["left", "左", "左揃え"],
            ["center", "中央", "中央揃え"],
            ["right", "右", "右揃え"],
          ] as const).map(([value, label, accessibleLabel]) => (
            <button
              key={value}
              type="button"
              className={ocrLabelAlignment === value ? "active" : ""}
              aria-label={accessibleLabel}
              aria-pressed={ocrLabelAlignment === value}
              onClick={() => setOcrLabelAlignment(value)}
            >
                {label}
              </button>
            ))}
          </div>}
        {(showClassificationStep || showMeaningStep || showRecordStep || !workflowStep) && (
          <span className="command-separator" />
        )}
        {(showLabelStep || showClassificationStep || showMeaningStep || showRecordStep) && (
          <ModeButton current={mode} mode="select" label="選択" onChange={setMode} />
        )}
        {showClassificationStep && <ModeButton current={mode} mode="classify" label="区分変更" onChange={setMode} />}
        {showClassificationStep && <ModeButton current={mode} mode="draw" label="枠を追加" onChange={setMode} />}
        {showMeaningStep && (
          <ModeButton
            current={mode}
            mode="link"
            label="意味付け"
            icon={<MeaningLinkIcon />}
            onChange={setMode}
          />
        )}
        {showRecordStep && <ModeButton current={mode} mode="record" label="繰り返し部分をまとめる" onChange={setMode} />}
        {showRecordStep && <ModeButton current={mode} mode="parent" label="見出しへ接続" onChange={setMode} />}
        {showMeaningStep && (
          <button
            type="button"
            disabled={historyDepth === 0}
            title="Ctrl+Z"
            onClick={undoMeaning}
          >
            ↶ 意味づけを戻す
          </button>
        )}
        {showMeaningStep && <span className="command-hint">1つの項目名から複数の記入欄へ接続可能</span>}
        <span className="command-separator" />
        <div className="layout-magnifier-control" role="group" aria-label="拡大鏡">
          <button
            type="button"
            className={magnifierEnabled ? "magnifier-toggle active" : "magnifier-toggle"}
            aria-pressed={magnifierEnabled}
            aria-label={magnifierEnabled ? "拡大鏡をオフにする" : "拡大鏡をオンにする"}
            onClick={() => setMagnifierEnabled((current) => !current)}
          >
            <span className="mode-button-icon" aria-hidden="true"><MagnifierIcon /></span>
            拡大鏡
          </button>
          <label className="layout-magnifier-slider">
            <span>倍率</span>
            <input
              type="range"
              aria-label="拡大率"
              min="1.5"
              max="6"
              step="0.25"
              value={magnifierScale}
              disabled={!magnifierEnabled}
              onChange={(event) => setMagnifierScale(Number(event.target.value))}
            />
            <span className="layout-magnifier-value">{formatMagnifierScale(magnifierScale)}倍</span>
          </label>
          <label className="layout-magnifier-slider">
            <span>窓</span>
            <input
              type="range"
              aria-label="拡大窓の大きさ"
              min="140"
              max="360"
              step="20"
              value={magnifierSize}
              disabled={!magnifierEnabled}
              onChange={(event) => setMagnifierSize(Number(event.target.value))}
            />
            <span className="layout-magnifier-value">{magnifierSize}px</span>
          </label>
        </div>
      </div>

      <div className="layout-instructions">
        {workflowStep === "classify" && mode === "select" && "検出された枠を選び、右側で種類と項目名の文字候補を確認してください。"}
        {workflowStep === "identity" && "帳票上で強調されている欄を複数選択できます。選択後、同じ書類と判断する基準とカードの見出しを指定してください。薄く表示された矩形は選択できません。"}
        {workflowStep === "review" && "分類数、未割当、レコード構造を確認し、問題がなければ保存してください。"}
        {workflowStep !== "identity" && workflowStep !== "review" && mode === "select" && "クリックで1つの枠、Ctrl（MacはCmd）+クリックで複数の枠を追加・解除できます。ドラッグで範囲をまとめて選択することもできます。"}
        {workflowStep !== "review" && mode === "classify" && "変更する枠をクリックまたはドラッグで選び、右側で区分を明示的に指定してください。選択しただけでは区分は変わりません。"}
        {workflowStep !== "review" && mode === "draw" && "検出されなかった記入欄をドラッグして追加します。"}
        {workflowStep !== "review" && mode === "link" && "項目名から記入欄を通過するようにドラッグします。項目名がない記入欄は右側から保存する情報を直接指定できます。"}
        {workflowStep !== "review" && mode === "record" && "車種名から備考など、同じ繰り返し表に含める複数の列見出しを横方向の1本の線で横断します。"}
        {workflowStep !== "review" && mode === "parent" && "「売却子牛一覧」などの見出しから、青線でまとめた項目名のいずれかまで線を引きます。"}
      </div>
      {message && !showReviewStep && (
        <p className="layout-status" aria-live="polite">{message}</p>
      )}

      <div className="layout-workbench">
        <div ref={canvasWrapRef} className="mapping-canvas-wrap" aria-busy={detecting}>
          <canvas
            ref={canvasRef}
            width={layout.canonicalWidth}
            height={layout.canonicalHeight}
            tabIndex={0}
            aria-label={showIdentityStep ? "書類を見分ける欄を選択する帳票プレビュー" : "帳票レイアウト編集キャンバス"}
            aria-describedby="layout-keyboard-help"
            className={`layout-mode-${mode}`}
            onPointerEnter={(event) => queueMagnifier(event.clientX, event.clientY)}
            onPointerLeave={() => {
              magnifierPointerRef.current = null;
              hideMagnifier();
            }}
            onClick={(event) => {
              if (busy || disabled || mode !== "identity") return;
              const hit = cellAt(point(event));
              const candidate = hit ? identityCandidateByCellId.get(hit.id) : null;
              if (!candidate) {
                setMessage("この矩形は書類を見分ける欄には使用できません。単独の文字項目を選択してください。");
                return;
              }
              setSelectedId(candidate.cell.id);
              toggleIdentityCandidate(candidate);
            }}
            onKeyDown={(event) => {
              if (busy || disabled || layout.cells.length === 0) return;
              if (mode === "identity" && (event.key === "Enter" || event.key === " ")) {
                const candidate = selectedId ? identityCandidateByCellId.get(selectedId) : null;
                if (!candidate) return;
                event.preventDefault();
                toggleIdentityCandidate(candidate);
                return;
              }
              if (!["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) return;
              event.preventDefault();
              const navigableCells = mode === "identity"
                ? identityCandidates.map((candidate) => candidate.cell)
                : layout.cells;
              if (navigableCells.length === 0) return;
              const currentIndex = navigableCells.findIndex((cell) => cell.id === selectedId);
              const delta = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
              const nextIndex = currentIndex < 0
                ? 0
                : (currentIndex + delta + navigableCells.length) % navigableCells.length;
              const nextId = navigableCells[nextIndex].id;
              setSelectedId(nextId);
              setSelectedIds(mode === "identity"
                ? new Set(selectedIdentityCandidates.map((candidate) => candidate.cell.id))
                : new Set([nextId]));
            }}
            onPointerDown={(event) => {
              if (busy || disabled) return;
              const location = point(event);
              const hit = cellAt(location);
              if (mode === "identity") {
                return;
              }
              if (mode === "select" || mode === "classify") {
                event.currentTarget.setPointerCapture?.(event.pointerId);
                dragStart.current = location;
                additiveSelection.current = event.ctrlKey || event.metaKey;
                setDraft({ ...location, width: 0, height: 0 });
                return;
              }
              if (mode === "record") {
                if (!hit || hit.role !== "label") {
                  setMessage("レコード作成では、最初の列ラベルから最後の列ラベルまで横方向にドラッグしてください。");
                  return;
                }
                event.currentTarget.setPointerCapture?.(event.pointerId);
                linkLabelId.current = hit.id;
                setLinkPoint(location);
                return;
              }
              if (mode === "link" || mode === "parent") {
                const labelHit = [...layout.cells].reverse().find((cell) => (
                  (cell.role === "label" || (mode === "parent" && cell.role === "parent_label"))
                  && location.x >= cell.x
                  && location.x <= cell.x + cell.width
                  && location.y >= cell.y
                  && location.y <= cell.y + cell.height
                ));
                if (!labelHit) {
                  setMessage(
                    mode === "parent"
                      ? "親ラベル接続はラベルセルからドラッグを開始してください。"
                      : "意味づけはラベルセルからドラッグを開始してください。",
                  );
                  return;
                }
                event.currentTarget.setPointerCapture?.(event.pointerId);
                linkLabelId.current = labelHit.id;
                setLinkPoint(location);
                return;
              }
              event.currentTarget.setPointerCapture?.(event.pointerId);
              dragStart.current = location;
              setDraft({ ...location, width: 0, height: 0 });
            }}
            onPointerMove={(event) => {
              queueMagnifier(event.clientX, event.clientY);
              const location = point(event);
              if (
                (mode === "draw" || mode === "select" || mode === "classify")
                && dragStart.current
              ) {
                setDraft(rectFrom(dragStart.current, location));
              }
              if ((mode === "link" || mode === "record" || mode === "parent") && linkLabelId.current) {
                setLinkPoint(location);
              }
            }}
            onPointerUp={(event) => {
              const location = point(event);
              if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
              if (mode === "link") {
                finishLink(location);
                return;
              }
              if (mode === "record") {
                finishRecordLabelBand(location);
                return;
              }
              if (mode === "parent") {
                finishParentLink(location);
                return;
              }
              if ((mode === "select" || mode === "classify") && dragStart.current) {
                const start = dragStart.current;
                const additive = additiveSelection.current;
                dragStart.current = null;
                additiveSelection.current = false;
                setDraft(null);
                const next = rectFrom(start, location);
                if (Math.hypot(next.width, next.height) < 8) {
                  const hit = cellAt(location);
                  if (!hit) {
                    if (!additive) {
                      setSelectedId(null);
                      setSelectedIds(new Set());
                    }
                    return;
                  }
                  if (additive) {
                    const ids = new Set(selectedIds);
                    if (ids.has(hit.id)) ids.delete(hit.id);
                    else ids.add(hit.id);
                    setSelectedIds(ids);
                    setSelectedId(ids.has(hit.id) ? hit.id : ids.values().next().value ?? null);
                    setMessage(ids.size > 1
                      ? `${ids.size} 個のセルを複数選択しました。右側で区分を一括変更できます。`
                      : ids.size === 1
                        ? "1 個のセルを選択しています。Ctrlを押しながら別のセルを追加できます。"
                        : "セルの選択を解除しました。");
                  } else {
                    setSelectedId(hit.id);
                    setSelectedIds(new Set([hit.id]));
                  }
                  return;
                }
                const ids = cellsSelectedByRect(layout.cells, next).map((cell) => cell.id);
                const nextIds = additive
                  ? new Set([...selectedIds, ...ids])
                  : new Set(ids);
                setSelectedId(ids[0] ?? (additive ? selectedId : null));
                setSelectedIds(nextIds);
                setMessage(nextIds.size > 0
                  ? `${nextIds.size} 個のセルをまとめて選択しました。右側で区分を一括変更できます。`
                  : "選択範囲にセルはありません。変更したいセルを囲むようにドラッグしてください。");
                return;
              }
              if (mode !== "draw" || !dragStart.current) return;
              const next = rectFrom(dragStart.current, location);
              dragStart.current = null;
              setDraft(null);
              if (next.width < 8 || next.height < 8) return;
              const id = `manual_${Date.now()}`;
              setLayout((current) => ({
                ...current,
                cells: [...current.cells, {
                  id,
                  role: "input",
                  ...next,
                  confidence: 1,
                  text: "",
                  textConfidence: null,
                  source: "manual",
                }],
              }));
              setSelectedId(id);
              setSelectedIds(new Set([id]));
              setMode("select");
            }}
            onPointerCancel={() => {
              dragStart.current = null;
              additiveSelection.current = false;
              linkLabelId.current = null;
              setDraft(null);
              setLinkPoint(null);
            }}
          />
          <div
            ref={magnifierRef}
            className="layout-magnifier"
            aria-hidden="true"
            style={{ width: magnifierSize, height: magnifierSize }}
            hidden
          >
            <canvas
              ref={magnifierCanvasRef}
              className="layout-magnifier-canvas"
              style={{ width: magnifierSize, height: magnifierSize }}
            />
            <span>{formatMagnifierScale(magnifierScale)}×</span>
          </div>
          <span id="layout-keyboard-help" className="sr-only">
            {showIdentityStep
            ? "矢印キーで候補を移動し、Enterまたはスペースキーで書類を見分ける欄を追加または解除できます。"
              : "矢印キーでセルを順番に選択できます。"}
          </span>
          {detecting && (
            <div className="layout-loading-overlay" role="status" aria-live="assertive">
              <span className="layout-loading-spinner" aria-hidden="true" />
              <strong>記入欄を再検出しています</strong>
              <small>OpenCVで枠と文字密度を確認中です…</small>
            </div>
          )}
        </div>

        <aside className="layout-properties">
          <h3>{workflowStep
            ? { classify: "欄の種類", meaning: "読み取る内容", records: "繰り返し部分", identity: "書類の見分け方", review: "確認・保存" }[workflowStep]
            : "選択セル"}</h3>
          {!showIdentityStep && (
            <label className="accessible-cell-select sr-only">
              セル一覧から選択
              <select
                value={selectedId ?? ""}
                onChange={(event) => {
                  const id = event.target.value || null;
                  setSelectedId(id);
                  setSelectedIds(id ? new Set([id]) : new Set());
                }}
              >
                <option value="">セルを選択</option>
                {cellsVisibleInList.map((cell, index) => (
                  <option key={cell.id} value={cell.id}>
                    {index + 1}. {cell.role === "label"
                      ? "項目名"
                      : cell.role === "parent_label"
                        ? "見出し"
                      : cell.role === "input"
                        ? "記入欄"
                          : cell.role === "mark"
                            ? "選択欄"
                          : cell.role === "record_key_cell"
                            ? "行番号"
                            : cell.role === "column_key_cell"
                              ? "列番号"
                            : "読み取らない"} · {cell.text || cell.id}
                  </option>
                ))}
              </select>
            </label>
          )}
          {!selected && workflowStep !== "identity" && workflowStep !== "review" && <p className="empty-property">帳票上の矩形を選択してください。</p>}
          {showClassificationStep && selectedCells.length > 1 && (
            <section className="bulk-selection">
              <h3>範囲選択: {selectedCells.length} セル</h3>
              <p>区分変更モードでドラッグしたセルです。区分を一括で指定できます。</p>
              <div className="role-buttons">
                <button type="button" className="label-role" onClick={() => setRoles(selectedIds, "label")}>項目名にする</button>
                <button type="button" className="parent-label-role" onClick={() => setRoles(selectedIds, "parent_label")}>見出しにする</button>
                <button type="button" className="input-role" onClick={() => setRoles(selectedIds, "input")}>記入欄にする</button>
                <button type="button" className="mark-role" onClick={() => setRoles(selectedIds, "mark")}>選択欄にする</button>
                <button
                  type="button"
                  className="record-key-role"
                  title="表の各行を識別する「1、2、3…」などの印刷済みセル"
                  onClick={() => setRoles(selectedIds, "record_key_cell")}
                >
                  行番号にする
                </button>
                <button
                  type="button"
                  className="record-key-role"
                  title="表の各列を識別する印刷済みセル"
                  onClick={() => setRoles(selectedIds, "column_key_cell")}
                >
                  列番号にする
                </button>
                <button type="button" onClick={() => setRoles(selectedIds, "ignored")}>選択した枠を読み取らない</button>
              </div>
              <button
                type="button"
                className="selection-clear"
                onClick={() => {
                  setSelectedId(null);
                  setSelectedIds(new Set());
                }}
              >
                選択解除
              </button>
            </section>
          )}
          {selected && (showLabelStep || showClassificationStep || showMeaningStep) && (
            <div className="property-form">
              {showClassificationStep && (
                <fieldset>
                  <legend>区分</legend>
                  <div className="role-buttons">
                    <button className={selected.role === "label" ? "active label-role" : ""} onClick={() => setRole(selected.id, "label")}>項目名</button>
                    <button className={selected.role === "parent_label" ? "active parent-label-role" : ""} onClick={() => setRole(selected.id, "parent_label")}>見出し</button>
                    <button className={selected.role === "input" ? "active input-role" : ""} onClick={() => setRole(selected.id, "input")}>記入欄</button>
                    <button className={selected.role === "mark" ? "active mark-role" : ""} onClick={() => setRole(selected.id, "mark")}>選択欄</button>
                    <button
                      className={selected.role === "record_key_cell" ? "active record-key-role" : "record-key-role"}
                      title="表の各行を識別する「1、2、3…」などの印刷済みセル"
                      onClick={() => setRole(selected.id, "record_key_cell")}
                    >
                      行番号
                    </button>
                    <button
                      className={selected.role === "column_key_cell" ? "active record-key-role" : "record-key-role"}
                      title="表の各列を識別する印刷済みセル"
                      onClick={() => setRole(selected.id, "column_key_cell")}
                    >
                      列番号
                    </button>
                    <button className={selected.role === "ignored" ? "active" : ""} onClick={() => setRole(selected.id, "ignored")}>読み取らない</button>
                  </div>
                </fieldset>
              )}
              {showLabelStep && (selected.role === "label" || selected.role === "parent_label") && (
                <label>項目名の文字候補
                  <input
                    value={selected.text}
                    placeholder="未認識の場合は入力"
                    onChange={(event) => patchCell(selected.id, { text: event.target.value })}
                    onBlur={() => {
                      if (findLabelPoolMatch(selected.text, labelPoolEntries)) return;
                      const entry = ensureAutoLabelPoolEntry(selected.text);
                      if (entry) {
                        setMessage(`未登録ラベル「${entry.label}」を候補プールへ自動追加しました。`);
                      }
                    }}
                  />
                </label>
              )}
              {showClassificationStep && selected.role === "input" && (
                <label>
                  矩形内の印刷ラベル（読み取りから除外）
                  <input
                    aria-label="矩形内の印刷ラベル（読み取りから除外）"
                    value={selected.printedLabelText ?? ""}
                    placeholder="例: 名号（印刷ラベルがない欄は空欄）"
                    onChange={(event) => patchCell(selected.id, {
                      printedLabelText: event.target.value,
                    })}
                  />
                  <small>
                    入力すると、OCRには固定印刷を除いた手書き文字画像だけを送ります。
                  </small>
                </label>
              )}
              {showMeaningStep && selected.role === "mark" && (
                <MarkCellEditor
                  cell={selected}
                  batchCells={selectedCells.filter((cell) => cell.role === "mark")}
                  batchRowIndexes={inferCalvingMarkRowIndexes(
                    layout,
                    selectedCells.filter((cell) => cell.role === "mark"),
                  )}
                  source={source}
                  canonicalWidth={layout.canonicalWidth}
                  canonicalHeight={layout.canonicalHeight}
                  rowCount={Math.max(
                    1,
                    ...availableTargets
                      .map((target) => /^calvings\.(\d+)\./.exec(target.path))
                      .filter((match): match is RegExpExecArray => Boolean(match))
                      .map((match) => Number(match[1]) + 1),
                  )}
                  onChange={(markSets) => patchCell(selected.id, { markSets })}
                  onBatchChange={patchMarkCells}
                />
              )}
              {showClassificationStep && <button
                className="danger-button compact"
                type="button"
                onClick={() => {
                  setLayout((current) => {
                    const invalidatedSectionIds = new Set(
                      (current.recordSections ?? [])
                        .filter((section) => section.recordLabelCellIds.includes(selected.id))
                        .map((section) => section.id),
                    );
                    return {
                      ...current,
                      cells: current.cells.filter((cell) => cell.id !== selected.id),
                      relations: current.relations.filter((relation) => (
                        relation.inputCellId !== selected.id
                        && !relation.labelCellIds.includes(selected.id)
                      )),
                      recordGroups: (current.recordGroups ?? [])
                        .filter((group) => !invalidatedSectionIds.has(group.sectionId))
                        .map((group) => ({
                          ...group,
                          inputCellIds: group.inputCellIds.filter((id) => id !== selected.id),
                        }))
                        .filter((group) => group.inputCellIds.length > 0),
                      recordSections: (current.recordSections ?? []).map((section) => ({
                        ...section,
                        recordLabelCellIds: section.recordLabelCellIds.includes(selected.id)
                          ? []
                          : section.recordLabelCellIds,
                        parentLabelCellId: section.parentLabelCellId === selected.id
                          ? null
                          : section.parentLabelCellId,
                      })),
                      recordStructureVerified: false,
                    };
                  });
                  setSelectedId(null);
                  setSelectedIds((current) => {
                    const next = new Set(current);
                    next.delete(selected.id);
                    return next;
                  });
                }}
              >
                矩形を完全に削除
              </button>}
            </div>
          )}

          {showMeaningStep && <>
          <h3>読み取る内容の保存先</h3>
          <label className="property-select">
            保存する情報
            <select
              value={selectedPath}
              onChange={(event) => {
                const path = event.target.value;
                setSelectedPath(path);
                if (selected?.role === "input" && mode !== "link" && path) {
                  assignSelectedInputDirectly(path);
                }
              }}
            >
              <option value="">
                {selected && (selected.role === "label" || selected.role === "parent_label")
                  ? `${selected.text.trim() || "未認識"}（文字候補）`
                  : "項目名を選択"}
              </option>
              {sections.map(([section, items]) => (
                <optgroup key={section} label={section}>
                  {items.map((target) => (
                    <option key={target.path} value={target.path}>
                      {target.label}{assignedPaths.has(target.path) ? " ✓" : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <p className="property-note">
            {targetByPath.get(selectedPath)?.label
              ? `${targetByPath.get(selectedPath)?.label}として、次に意味づけした記入欄を保存します。`
              : selected && (selected.role === "label" || selected.role === "parent_label")
                ? `文字候補「${selected.text.trim() || "未認識"}」から保存先を自動推定します。`
                : "項目名を選択すると、文字候補をここに表示します。"}
          </p>
          {selected?.role === "input" && customTargetAddition("sidebar", "新しい保存項目を追加")}
          </>}

          {workflowStep === "records" && (
            <div className="wizard-side-summary">
              <strong>列ラベルをまとめる</strong>
              <p>複数の列見出しを横断すると、行番号または上からの順番で各行を自動編成します。帳票上の見出しは任意です。</p>
              <span>{recordGroups.length}レコードを定義済み</span>
            </div>
          )}
          {showIdentityStep && (
            <div className="document-identity-canvas-setting">
              <span className="panel-kicker">DOCUMENT MATCHING</span>
              <strong>帳票上の欄を複数選択</strong>
              <p>
                同じ書類か判断する基準と、違いを確認する情報を選択します。選択済みの矩形をもう一度クリックすると解除できます。
              </p>
              <dl>
                <div><dt>選択できる欄</dt><dd>{identityCandidates.length}件</dd></div>
                <div><dt>選択中</dt><dd>{selectedIdentityCandidates.length}件</dd></div>
              </dl>
              {selectedIdentityCandidates.length > 0 ? (
                <div className="identity-field-list" aria-live="polite">
                  {selectedIdentityCandidates.map((candidate) => {
                    const field = selectedIdentityByPath.get(candidate.targetPath)!;
                    return (
                      <section className={field.matchRole === "primary" ? "primary-match" : "confirmation-match"} key={candidate.targetPath}>
                        <header>
                          <div>
                            <strong>{candidate.label}</strong>
                            <small>{field.matchRole === "primary" ? "同じ書類と判断する基準" : "違っていたら確認する情報"}</small>
                          </div>
                          <button type="button" className="ghost compact" onClick={() => toggleIdentityCandidate(candidate)}>解除</button>
                        </header>
                        <label>
                          照合での役割
                          <select
                            aria-label={`${candidate.label}の照合での役割`}
                            value={field.matchRole}
                            onChange={(event) => updateIdentityField(
                              candidate.targetPath,
                              "matchRole",
                              event.target.value as DocumentIdentity["matchRole"],
                            )}
                          >
                            <option value="primary">同じ書類と判断する基準</option>
                            <option value="confirmation">違っていたら確認</option>
                          </select>
                        </label>
                        <label>
                          カードでの表示
                          <select
                            aria-label={`${candidate.label}のカードでの表示`}
                            value={field.displayRole}
                            onChange={(event) => updateIdentityField(
                              candidate.targetPath,
                              "displayRole",
                              event.target.value as DocumentIdentity["displayRole"],
                            )}
                          >
                            <option value="title">一覧で大きく表示</option>
                            <option value="subtitle">見出しの下に表示</option>
                            <option value="none">カードには表示しない</option>
                          </select>
                        </label>
                      </section>
                    );
                  })}
                </div>
              ) : (
                <p className="inline-warning">強調された矩形をクリックし、書類を見分ける情報を選択してください。</p>
              )}
              {selectedIdentityCandidates.length > 0 && (
                <div className="identity-card-preview" aria-label="書類カードの表示プレビュー">
                  <span>カード表示プレビュー</span>
                  <strong>{titleIdentityField?.label ?? "見出しを選択"}</strong>
                  <small>{subtitleIdentityField?.label ?? (
                    primaryIdentityField?.targetPath !== titleIdentityField?.targetPath
                      ? primaryIdentityField?.label
                      : "補足表示なし"
                  )}</small>
                </div>
              )}
              {!identityConfigurationReady && selectedIdentityCandidates.length > 0 && (
                <p className="inline-warning">
                  同じ書類と判断する基準を1つ指定するか、2項目以上をすべて「違っていたら確認」にしてください。カードの見出しは1つ指定してください。
                </p>
              )}
              {usesCombinedConfirmation && (
                <p className="identity-combination-note">
                  選択した項目がすべて以前の読み取りと違う場合に確認します。
                </p>
              )}
              {identityCandidates.length === 0 && (
                <p className="inline-warning">
                  選択できる欄がありません。意味づけ工程で、繰り返し行ではない文字欄へ項目を割り当ててください。
                </p>
              )}
            </div>
          )}
          {workflowStep === "review" && (
            <div className="wizard-review-summary">
              <dl>
                <div><dt>項目名・見出し</dt><dd>{roleCounts.label + roleCounts.parentLabel}</dd></div>
                <div><dt>記入欄</dt><dd>{roleCounts.input + roleCounts.mark}</dd></div>
                <div><dt>内容設定済み</dt><dd>{meaningAssignedCellIds.size}</dd></div>
                <div><dt>未割当</dt><dd>{hasUnassignedFields ? "あり" : "なし"}</dd></div>
                <div><dt>繰り返し部分</dt><dd>{recordGroups.length}</dd></div>
                <div><dt>照合条件</dt><dd>{usesCombinedConfirmation ? "選択項目がすべて違うとき確認" : primaryIdentityField?.label ?? "未選択"}</dd></div>
                <div><dt>確認する情報</dt><dd>{documentIdentityFields.filter((field) => field.matchRole === "confirmation").map((field) => field.label).join("、") || "なし"}</dd></div>
                <div><dt>カードの見出し</dt><dd>{titleIdentityField?.label ?? "未選択"}</dd></div>
                <div><dt>保存判定</dt><dd>{layoutReadyForOcr && identityConfigurationReady ? "保存可能" : "要修正"}</dd></div>
              </dl>
            </div>
          )}
        </aside>
      </div>

      {showMeaningStep && (
        <section className="meaning-target-manager" aria-label="保存する情報を直接指定">
          <header>
            <div>
              <span className="panel-kicker">保存する情報</span>
              <h3>登録済みの項目から保存先を選ぶ</h3>
              <p>
                登録済みの項目を確認してから、プレビューで選択した記入欄へ割り当てます。
              </p>
            </div>
            <span className="meaning-target-count">
              登録済み {labelPoolEntries.length}項目
            </span>
          </header>
          <div className={`meaning-target-selection ${selected?.role === "input" ? "ready" : "waiting"}`}>
            <strong>{selected?.role === "input" ? "選択中の記入欄" : "記入欄を選択してください"}</strong>
            <span>{selected?.role === "input"
              ? selected.id
              : "先にプレビュー上の記入欄を選択すると、項目をクリックして割り当てられます。"}</span>
          </div>
          {customTargetAddition("catalog", "一覧にない項目を追加")}
          {labelPoolEntries.length > 0 ? <>
            <div className="meaning-target-filters">
              <label>
                登録済み項目を検索
                <input
                  type="search"
                  value={meaningCatalogQuery}
                  placeholder="項目名で検索"
                  onChange={(event) => setMeaningCatalogQuery(event.target.value)}
                />
              </label>
              <label>
                分類
                <select
                  value={meaningCatalogSection}
                  onChange={(event) => setMeaningCatalogSection(event.target.value)}
                >
                  <option value="all">すべての分類</option>
                  {meaningCatalogSections.map((section) => (
                    <option key={section} value={section}>{section}</option>
                  ))}
                </select>
              </label>
              <span>{filteredLabelPoolEntries.length}項目を表示</span>
            </div>
            <div className="meaning-pool-list" aria-label="登録済みの保存項目">
              {filteredLabelPoolEntries.map((entry) => {
              const inputCellId = selected?.role === "input" ? selected.id : null;
              const targetPath = inputCellId
                ? resolveLabelPoolTargetPath(
                  entry,
                  inputCellId,
                  recordGroups,
                  recordSections,
                  assignedPaths,
                )
                : null;
              const active = inputCellId !== null && layout.relations.some((relation) => (
                relation.inputCellId === inputCellId
                && entry.paths.includes(relation.targetPath)
              ));
              const usageCount = labelPoolUsageCounts.get(entry.key) ?? 0;
              const canAssign = inputCellId !== null && targetPath !== null;
              return (
                <button
                  type="button"
                  key={entry.key}
                  className={active ? "active" : ""}
                  aria-disabled={!canAssign}
                  aria-label={`${entry.section}・${entry.label}${canAssign ? "をこの欄に設定" : ""}`}
                  onClick={() => {
                    if (inputCellId && targetPath) {
                      assignSelectedInputDirectly(targetPath);
                      return;
                    }
                    setMessage("先にプレビュー上の記入欄を選択してください。");
                  }}
                >
                  <span>{entry.label}</span>
                  <small>{entry.section} · {usageCount > 0 ? `${usageCount}か所で使用` : "未使用"}</small>
                </button>
              );
              })}
              {filteredLabelPoolEntries.length === 0 && (
                <p className="meaning-pool-empty">条件に一致する登録済み項目はありません。</p>
              )}
            </div>
          </> : (
            <div className="meaning-pool-empty-state">
              <strong>このテンプレートには保存する項目が登録されていません</strong>
              <span>必要な項目を追加すると、このテンプレートだけの候補として表示されます。</span>
            </div>
          )}
        </section>
      )}

      {showRecordStep && <section className="record-structure-editor">
        <div className="record-structure-head">
          <div>
            <span className="eyebrow">RECORD STRUCTURE</span>
            <h3>繰り返し表の行と列</h3>
            <p>車種名・車台番号などの列をまとめ、印刷された行番号または上からの順番で各行を編成します。帳票上の見出しは任意です。</p>
          </div>
          <div className="record-structure-status">
            <span className={layout.recordStructureVerified ? "structure-verified" : "structure-unverified"}>
              {layout.recordStructureVerified
                ? "繰り返し表を定義済み"
                : hasIncompleteLegacyRecordSection
                  ? "帳票上の見出しが未設定"
                  : "表の列が未設定"}
            </span>
          </div>
        </div>
        <div className="record-section-adder">
          <label>繰り返し表の名前
            <input
              value={recordSectionTitle}
              maxLength={200}
              placeholder="例: 車両明細"
              onChange={(event) => setRecordSectionTitle(event.target.value)}
            />
          </label>
          <label>行の決め方
            <select
              value={rowKeyStrategy}
              onChange={(event) => setRowKeyStrategy(event.target.value as "printed" | "position")}
            >
              <option value="printed">印刷された行番号を使う</option>
              <option value="position">上から順番に行を作る</option>
            </select>
          </label>
          <button
            type="button"
            className="secondary"
            disabled={!recordSectionTitle.trim()}
            onClick={() => {
              const collectionPath = `genericRecords.${genericRecordCollectionKey(recordSectionTitle, recordSections)}`;
              const existing = recordSections.find(
                (section) => section.recordType === "generic" && section.title === recordSectionTitle.trim(),
              );
              if (existing) {
                setSelectedRecordSectionId(existing.id);
                setMessage(`${existing.title}は作成済みです。`);
                return;
              }
              const section: LayoutRecordSection = {
                id: `section_${collectionPath.replace(/[^A-Za-z0-9_]/g, "_")}_${Date.now()}`,
                title: recordSectionTitle.trim(),
                collectionPath,
                recordType: "generic",
                recordLabelCellIds: [],
                parentLabelCellId: null,
                rowKeyStrategy,
                rowKeyCellIds: [],
                includeEmptyRows: false,
                source: "manual",
              };
              setLayout((current) => ({
                ...current,
                layoutSchemaVersion: 3,
                recordSections: [...(current.recordSections ?? []), section],
                recordStructureVerified: false,
              }));
              setSelectedRecordSectionId(section.id);
              setMessage(`${section.title}を作成しました。次に「繰り返し部分をまとめる」で、表に含める列ラベルを横方向に横断してください。`);
            }}
          >
            繰り返し表を追加
          </button>
        </div>

        {recordSections.length > 0 && (
          <div className="record-section-tabs" role="tablist" aria-label="繰り返し表">
            {recordSections.map((section) => (
              <button
                type="button"
                key={section.id}
                className={selectedRecordSectionId === section.id ? "active" : ""}
                onClick={() => setSelectedRecordSectionId(section.id)}
              >
                {section.title}
                <small>{recordGroups.filter((group) => group.sectionId === section.id).length}レコード</small>
              </button>
            ))}
          </div>
        )}

        {recordSections
          .filter((section) => section.id === selectedRecordSectionId)
          .map((section) => (
            <div className="record-section-panel" key={section.id}>
              <div className="record-section-properties">
                <label>表の表示名
                  <input
                    value={section.title}
                    maxLength={200}
                    onChange={(event) => setLayout((current) => ({
                      ...current,
                      recordSections: (current.recordSections ?? []).map((item) => (
                        item.id === section.id
                          ? { ...item, title: event.target.value, source: "manual" }
                          : item
                      )),
                      recordStructureVerified: current.recordStructureVerified,
                    }))}
                  />
                </label>
                <label>行の決め方
                  <select
                    value={section.rowKeyStrategy ?? "position"}
                    onChange={(event) => setLayout((current) => ({
                      ...current,
                      recordSections: (current.recordSections ?? []).map((item) => (
                        item.id === section.id
                          ? {
                            ...item,
                            rowKeyStrategy: event.target.value as "printed" | "position",
                            rowKeyCellIds: event.target.value === "position"
                              ? []
                              : item.rowKeyCellIds ?? [],
                            source: "manual" as const,
                          }
                          : item
                      )),
                    }))}
                  >
                    <option value="printed">印刷された行番号を使う</option>
                    <option value="position">上から順番に行を作る</option>
                  </select>
                </label>
                <label>帳票上の見出し（任意）
                  <select
                    value={section.parentLabelCellId ?? ""}
                    onChange={(event) => setLayout((current) => {
                      const sections = (current.recordSections ?? []).map((item) => (
                        item.id === section.id
                          ? {
                            ...item,
                            parentLabelCellId: event.target.value || null,
                            source: "manual" as const,
                          }
                          : item
                      ));
                      return {
                        ...current,
                        recordSections: sections,
                        recordStructureVerified: recordStructureIsLinked(
                          sections,
                          current.recordGroups ?? [],
                        ),
                      };
                    })}
                  >
                    <option value="">見出しなし</option>
                    {layout.cells
                      .filter((cell) => (
                        (cell.role === "label" || cell.role === "parent_label")
                        && !section.recordLabelCellIds.includes(cell.id)
                      ))
                      .map((cell) => (
                        <option value={cell.id} key={cell.id}>
                          {cell.text || cell.id}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="record-empty-row-option">
                  <input
                    type="checkbox"
                    checked={section.includeEmptyRows ?? false}
                    onChange={(event) => setLayout((current) => ({
                      ...current,
                      recordSections: (current.recordSections ?? []).map((item) => (
                        item.id === section.id
                          ? { ...item, includeEmptyRows: event.target.checked, source: "manual" as const }
                          : item
                      )),
                    }))}
                  />
                  空行も読み取り結果に含める
                </label>
                <button
                  type="button"
                  className="danger-button compact"
                  onClick={() => {
                    setLayout((current) => ({
                      ...current,
                      recordSections: (current.recordSections ?? []).filter(
                        (item) => item.id !== section.id,
                      ),
                      recordGroups: (current.recordGroups ?? []).filter(
                        (group) => group.sectionId !== section.id,
                      ),
                      recordStructureVerified: false,
                    }));
                    setSelectedRecordSectionId("");
                  }}
                >
                  この表を削除
                </button>
              </div>
              <div className="record-column-summary">
                <div>
                  <strong>1レコードに含める列ラベル</strong>
                  <span>
                    {section.recordLabelCellIds.length > 0
                      ? section.recordLabelCellIds
                        .map((labelId) => cellById.get(labelId)?.text.trim() || labelId)
                        .join(" / ")
                      : "未指定"}
                  </span>
                </div>
                <dl>
                  <div>
                    <dt>自動編成</dt>
                    <dd>{recordGroups.filter((group) => group.sectionId === section.id).length}行</dd>
                  </div>
                  <div>
                    <dt>記入欄</dt>
                    <dd>{new Set(recordGroups
                      .filter((group) => group.sectionId === section.id)
                      .flatMap((group) => group.inputCellIds)).size}個</dd>
                  </div>
                  <div>
                    <dt>設定状態</dt>
                    <dd>{section.recordType === "generic" ? "設定済み" : section.parentLabelCellId ? "帳票上の見出し設定済み" : "未確定"}</dd>
                  </div>
                </dl>
                <p>
                  {section.recordLabelCellIds.length > 0
                    ? section.recordType === "generic"
                      ? `${section.rowKeyStrategy === "printed" ? `${section.rowKeyCellIds?.length ?? 0}個の行番号` : "上からの順番"}で各行を定義しています。帳票上の見出しは任意です。`
                    : section.parentLabelCellId
                      ? "選択した複数列が1レコードとして定義されています。行ごとの紐は表示しません。"
                      : "次に「親ラベル接続」で、親ラベルから青線でまとめた列ラベルのいずれかへ線を引いてください。"
                    : "「繰り返し部分をまとめる」を選び、1行に含める複数の列見出しを横方向に横断してください。"}
                </p>
                <button
                  type="button"
                  className="danger-button compact"
                  disabled={section.recordLabelCellIds.length === 0}
                  onClick={() => setLayout((current) => ({
                    ...current,
                    recordSections: (current.recordSections ?? []).map((item) => (
                      item.id === section.id
                        ? {
                          ...item,
                          recordLabelCellIds: [],
                          parentLabelCellId: null,
                          source: "manual",
                        }
                        : item
                    )),
                    recordGroups: (current.recordGroups ?? []).filter(
                      (group) => group.sectionId !== section.id,
                    ),
                    recordStructureVerified: false,
                  }))}
                >
                  このレコード定義を解除
                </button>
              </div>
            </div>
          ))}
      </section>}

      {showMeaningStep && (
        <details className="relation-table-disclosure">
          <summary>
            <span>設定済みのセル対応を確認</span>
            <small>{layout.relations.length}件</small>
          </summary>
          <div className="relation-table-wrap">
            <table className="relation-table">
              <thead><tr><th>順番</th><th>方向</th><th>ラベル</th><th>手書き文字欄</th><th>保存先</th><th /></tr></thead>
              <tbody>
                {layout.relations.map((relation) => {
                  const invalidTarget = !targetByPath.has(relation.targetPath);
                  return (
                  <tr key={relation.id} className={invalidTarget ? "invalid-relation" : ""}>
                    <td>{relation.order}行目</td>
                    <td>{relation.axis === "horizontal" ? "横積み" : "縦積み"}</td>
                    <td>
                      {relation.labelCellIds.length > 0
                        ? relation.labelCellIds.map((id) => cellById.get(id)?.text || id).join(" / ")
                        : cellById.get(relation.inputCellId)?.printedLabelText || "矩形へ直接設定"}
                    </td>
                    <td>{relation.inputCellId}</td>
                    <td>{targetByPath.get(relation.targetPath)?.label ?? `範囲外: ${relation.targetPath}`}</td>
                    <td><button className="danger-button compact" onClick={() => {
                      rememberRelations(layout.relations);
                      setLayout((current) => ({
                        ...current,
                        relations: current.relations.filter((item) => item.id !== relation.id),
                      }));
                    }}>解除</button></td>
                  </tr>
                  );
                })}
                {layout.relations.length === 0 && (
                  <tr><td colSpan={6}>まだ意味づけされていません。「意味づけ」モードでラベルから記入欄へドラッグしてください。</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {showReviewStep && <>
      {layout.warnings.map((warning) => <p className="inline-warning" key={warning}>{warning}</p>)}
      {!recordStructureReady && (
        <p className="inline-warning">
          途中保存できます。OCRへ進むには、複数の列見出しを横断して各行を自動編成してください。既存の繁殖台帳形式だけは親ラベル接続も必要です。
        </p>
      )}
      {!hasMeaningAssignments && (
        <p className="inline-warning">
          途中保存できます。OCRへ進むには、手書き文字欄または選択マーク欄を保存先へ意味づけしてください。
        </p>
      )}
      {hasMeaningAssignments && hasUnassignedFields && (
        <p className="inline-warning">
          途中保存できます。OCRへ進むには、未紐づけの手書き文字欄・選択マーク欄を意味づけするか、除外へ変更してください。
        </p>
      )}
      {!identityConfigurationReady && (
        <p className="inline-warning">
          途中保存できます。OCRへ進むには、「書類の見分け方」で照合基準とカードの見出しを指定してください。
        </p>
      )}
      <p className={dirty ? "inline-warning" : "layout-status"} aria-live="polite">
        {dirty
          ? "保存していないレイアウト変更があります。"
          : allowUnchangedSave
          ? "検出内容を確認し、意味づけをDBへ保存してください。"
          : "レイアウト変更は保存済みです。"}
      </p>
      {message && <p className="layout-status" aria-live="polite">{message}</p>}
      <button
        type="button"
        className="primary layout-save"
        disabled={
          busy
          || (!dirty && !allowUnchangedSave)
        }
        onClick={() => void save()}
      >
        {busy
          ? "処理中…"
          : dirty || allowUnchangedSave
          ? templateReadyForOcr
            ? "テンプレート設定をDBへ保存"
            : "レイアウトを途中保存"
          : "レイアウトは保存済み"}
      </button>
      </>}
      </fieldset>
    </div>
  );
});

function ModeButton({
  current,
  mode,
  label,
  icon,
  onChange,
}: {
  current: Mode;
  mode: Mode;
  label: string;
  icon?: ReactNode;
  onChange: (mode: Mode) => void;
}) {
  return (
    <button
      type="button"
      className={[
        current === mode ? "active" : "",
        icon ? "mode-button-with-icon" : "",
      ].filter(Boolean).join(" ")}
      onClick={() => onChange(mode)}
    >
      {icon && <span className="mode-button-icon" aria-hidden="true">{icon}</span>}
      {label}
    </button>
  );
}

function MeaningLinkIcon() {
  return (
    <svg viewBox="0 0 20 20" focusable="false">
      <rect x="1.5" y="3" width="6" height="5" rx="1" />
      <rect x="12.5" y="12" width="6" height="5" rx="1" />
      <path d="M7.5 5.5h2.2c2.1 0 2.8 1.1 2.8 3v3.5" />
      <path d="m10.5 10 2 2 2-2" />
    </svg>
  );
}

function MagnifierIcon() {
  return (
    <svg viewBox="0 0 20 20" focusable="false">
      <circle cx="8.5" cy="8.5" r="5.5" />
      <path d="m12.6 12.6 4.2 4.2" />
      <path d="M8.5 5.8v5.4M5.8 8.5h5.4" />
    </svg>
  );
}

function formatMagnifierScale(scale: number) {
  return Number.isInteger(scale) ? String(scale) : scale.toFixed(2).replace(/0$/, "");
}

export function cellsSelectedByRect(
  cells: LayoutCell[],
  selection: Rect,
): LayoutCell[] {
  if (selection.width <= 0 || selection.height <= 0) return [];
  return cells.filter((cell) => {
    const overlapWidth = Math.max(
      0,
      Math.min(selection.x + selection.width, cell.x + cell.width)
        - Math.max(selection.x, cell.x),
    );
    const overlapHeight = Math.max(
      0,
      Math.min(selection.y + selection.height, cell.y + cell.height)
        - Math.max(selection.y, cell.y),
    );
    if (overlapWidth === 0 || overlapHeight === 0) return false;
    const centerX = cell.x + cell.width / 2;
    const centerY = cell.y + cell.height / 2;
    const centerInside = centerX >= selection.x
      && centerX <= selection.x + selection.width
      && centerY >= selection.y
      && centerY <= selection.y + selection.height;
    const coveredRatio = overlapWidth * overlapHeight / (cell.width * cell.height);
    return centerInside || coveredRatio >= 0.25;
  });
}

function MarkCellEditor({
  cell,
  batchCells,
  batchRowIndexes,
  source,
  canonicalWidth,
  canonicalHeight,
  rowCount,
  onChange,
  onBatchChange,
}: {
  cell: LayoutCell;
  batchCells: LayoutCell[];
  batchRowIndexes: ReadonlyMap<string, number>;
  source: string;
  canonicalWidth: number;
  canonicalHeight: number;
  rowCount: number;
  onChange: (sets: LayoutMarkSet[]) => void;
  onBatchChange: (updates: ReadonlyMap<string, LayoutMarkSet[]>) => void;
}) {
  const sets = cell.markSets ?? [];
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{
    setId: string;
    optionId: string;
    mode: "move" | "resize";
    pointerId: number;
    start: Point;
    original: Rect;
  } | null>(null);
  const [activeOptionId, setActiveOptionId] = useState(
    () => sets[0]?.options[0]?.id ?? "",
  );
  const [autoDetecting, setAutoDetecting] = useState(false);
  const [autoDetectionMessage, setAutoDetectionMessage] = useState<string | null>(null);
  const match = sets[0] ? /^calvings\.(\d+)\./.exec(sets[0].targetPath) : null;
  const rowIndex = match ? Number(match[1]) : 0;
  const autoDetectionCells = batchCells.length > 0 ? batchCells : [cell];
  const patchOption = (
    setId: string,
    optionId: string,
    patch: Partial<Rect>,
  ) => {
    onChange(sets.map((markSet) => markSet.id !== setId
      ? markSet
      : {
        ...markSet,
        options: markSet.options.map((option) => option.id === optionId
          ? { ...option, ...patch }
          : option),
      }));
  };
  const updateOption = (
    setId: string,
    optionId: string,
    key: "x" | "y" | "width" | "height",
    requested: number,
  ) => {
    const markSet = sets.find((candidate) => candidate.id === setId);
    const option = markSet?.options.find((candidate) => candidate.id === optionId);
    if (!option) return;
    const maximum = key === "x"
      ? cell.width - option.width
      : key === "y"
        ? cell.height - option.height
        : key === "width"
          ? cell.width - option.x
          : cell.height - option.y;
    const minimum = key === "width" || key === "height" ? 1 : 0;
    patchOption(setId, optionId, {
      [key]: Math.round(Math.max(minimum, Math.min(requested, maximum))),
    });
  };
  const pointerPosition = (event: React.PointerEvent<SVGSVGElement>): Point => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: (event.clientX - bounds.left) * cell.width / Math.max(1, bounds.width),
      y: (event.clientY - bounds.top) * cell.height / Math.max(1, bounds.height),
    };
  };
  const startDrag = (
    event: React.PointerEvent<SVGElement>,
    setId: string,
    optionId: string,
    mode: "move" | "resize",
  ) => {
    event.preventDefault();
    event.stopPropagation();
    const svg = svgRef.current;
    const markSet = sets.find((candidate) => candidate.id === setId);
    const option = markSet?.options.find((candidate) => candidate.id === optionId);
    if (!svg || !option) return;
    const bounds = svg.getBoundingClientRect();
    dragRef.current = {
      setId,
      optionId,
      mode,
      pointerId: event.pointerId,
      start: {
        x: (event.clientX - bounds.left) * cell.width / Math.max(1, bounds.width),
        y: (event.clientY - bounds.top) * cell.height / Math.max(1, bounds.height),
      },
      original: {
        x: option.x,
        y: option.y,
        width: option.width,
        height: option.height,
      },
    };
    setActiveOptionId(optionId);
    svg.setPointerCapture?.(event.pointerId);
  };
  const moveDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    const dragging = dragRef.current;
    if (!dragging || dragging.pointerId !== event.pointerId) return;
    const position = pointerPosition(event);
    const deltaX = position.x - dragging.start.x;
    const deltaY = position.y - dragging.start.y;
    if (dragging.mode === "move") {
      patchOption(dragging.setId, dragging.optionId, {
        x: Math.round(Math.max(0, Math.min(
          dragging.original.x + deltaX,
          cell.width - dragging.original.width,
        ))),
        y: Math.round(Math.max(0, Math.min(
          dragging.original.y + deltaY,
          cell.height - dragging.original.height,
        ))),
      });
      return;
    }
    patchOption(dragging.setId, dragging.optionId, {
      width: Math.round(Math.max(1, Math.min(
        dragging.original.width + deltaX,
        cell.width - dragging.original.x,
      ))),
      height: Math.round(Math.max(1, Math.min(
        dragging.original.height + deltaY,
        cell.height - dragging.original.y,
      ))),
    });
  };
  const stopDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };
  return (
    <fieldset className="mark-cell-editor">
      <legend>分娩状況の選択マーク</legend>
      <label>
        繁殖履歴の行
        <select
          value={rowIndex}
          onChange={(event) => {
            const nextRow = Number(event.target.value);
            onChange(sets.map((markSet) => ({
              ...markSet,
              targetPath: `calvings.${nextRow}.${markSet.id}`,
            })));
          }}
        >
          {Array.from({ length: rowCount }, (_, index) => (
            <option key={index} value={index}>{index + 1}行目</option>
          ))}
        </select>
      </label>
      <p>帳票画像の色付き枠をドラッグして移動できます。右下の白い四角をドラッグすると大きさを変更できます。</p>
      <svg
        ref={svgRef}
        className="mark-option-canvas"
        viewBox={`0 0 ${cell.width} ${cell.height}`}
        preserveAspectRatio="none"
        aria-label="選択マークの判定範囲を画像上で調整"
        onPointerMove={moveDrag}
        onPointerUp={stopDrag}
        onPointerCancel={stopDrag}
      >
        <image
          href={source}
          x={-cell.x}
          y={-cell.y}
          width={canonicalWidth}
          height={canonicalHeight}
          preserveAspectRatio="none"
        />
        {sets.flatMap((markSet) => markSet.options.map((option) => (
          <g key={`${markSet.id}:${option.id}`}>
            <rect
              className={`mark-option-region ${activeOptionId === option.id ? "active" : ""}`}
              x={option.x}
              y={option.y}
              width={option.width}
              height={option.height}
              onPointerDown={(event) => startDrag(
                event,
                markSet.id,
                option.id,
                "move",
              )}
            />
            <text
              className="mark-option-canvas-label"
              x={option.x + option.width / 2}
              y={option.y + option.height / 2}
            >
              {option.label}
            </text>
            <rect
              className="mark-option-resize-handle"
              x={Math.max(option.x, option.x + option.width - 7)}
              y={Math.max(option.y, option.y + option.height - 7)}
              width={7}
              height={7}
              onPointerDown={(event) => startDrag(
                event,
                markSet.id,
                option.id,
                "resize",
              )}
            />
          </g>
        )))}
      </svg>
      <div className="mark-auto-actions">
        <button
          type="button"
          className="primary compact mark-auto-layout"
          disabled={autoDetecting}
          onClick={async () => {
            setAutoDetecting(true);
            setAutoDetectionMessage(null);
            try {
              const result = await detectCalvingMarkCells(
                source,
                autoDetectionCells,
                canonicalWidth,
                canonicalHeight,
                undefined,
                batchRowIndexes,
              );
              onBatchChange(result.updates);
              if (result.total === 1) {
                setAutoDetectionMessage(
                  result.detectedCount === 1
                    ? "印刷された選択肢を検出し、2行×3列で配置しました。"
                    : "印刷領域を確定できなかったため、2行×3列の標準配置にしました。",
                );
              } else {
                setAutoDetectionMessage(
                  `${result.total}個の選択マーク欄をまとめて処理しました。`
                  + ` 自動検出 ${result.detectedCount}件、標準配置 ${result.fallbackCount}件。`,
                );
              }
            } finally {
              setAutoDetecting(false);
            }
          }}
        >
          {autoDetecting ? "画像を解析中…" : "画像から2行×3列の枠を自動検出"}
        </button>
        <button
          type="button"
          className="secondary compact"
          onClick={() => {
            onChange(calvingMarkPreset(cell, rowIndex));
            setAutoDetectionMessage("2行×3列の標準配置に戻しました。");
          }}
        >
          2行×3列の標準配置
        </button>
      </div>
      {autoDetectionMessage && <p className="mark-auto-message">{autoDetectionMessage}</p>}
      <details className="mark-numeric-editor">
        <summary>数値で微調整する</summary>
        {sets.map((markSet) => (
          <section key={markSet.id} className="mark-option-set">
            <strong>{markSet.id === "outcome" ? "分娩状況" : "性別"}</strong>
            <small>{markSet.targetPath}</small>
            {markSet.options.map((option) => (
              <div key={option.id} className="mark-option-row">
                <b>{option.label}</b>
                {(["x", "y", "width", "height"] as const).map((key) => (
                  <label key={key}>
                    {{ x: "X", y: "Y", width: "幅", height: "高さ" }[key]}
                    <input
                      type="number"
                      min={key === "width" || key === "height" ? 1 : 0}
                      value={option[key]}
                      onChange={(event) => updateOption(
                        markSet.id,
                        option.id,
                        key,
                        Number(event.target.value) || 0,
                      )}
                    />
                  </label>
                ))}
              </div>
            ))}
          </section>
        ))}
      </details>
    </fieldset>
  );
}

function calvingMarkPreset(
  cell: LayoutCell,
  rowIndex: number,
  detectedArea?: Rect,
): LayoutMarkSet[] {
  const definitions = [
    { id: "male", label: "♂", value: "male", target: "sex", column: 0, row: 0 },
    { id: "twin", label: "双", value: "twin", target: "outcome", column: 1, row: 0 },
    { id: "premature", label: "早", value: "premature", target: "outcome", column: 2, row: 0 },
    { id: "female", label: "♀", value: "female", target: "sex", column: 0, row: 1 },
    { id: "miscarriage", label: "流", value: "miscarriage", target: "outcome", column: 1, row: 1 },
    { id: "stillbirth", label: "死", value: "stillbirth", target: "outcome", column: 2, row: 1 },
  ] as const;
  const area = detectedArea ?? {
    x: 0,
    y: 0,
    width: Math.max(3, Math.round(cell.width * 0.42)),
    height: cell.height,
  };
  const option = (definition: (typeof definitions)[number]) => {
    const left = Math.round(area.x + area.width * definition.column / 3);
    const right = Math.round(area.x + area.width * (definition.column + 1) / 3);
    const top = Math.round(area.y + area.height * definition.row / 2);
    const bottom = Math.round(area.y + area.height * (definition.row + 1) / 2);
    return {
      id: definition.id,
      label: definition.label,
      value: definition.value,
      x: left,
      y: top,
      width: Math.max(1, right - left),
      height: Math.max(1, bottom - top),
    };
  };
  return (["outcome", "sex"] as const).map((target) => ({
    id: target,
    targetPath: `calvings.${rowIndex}.${target}`,
    selectionMode: "multiple",
    options: definitions
      .map((definition) => ({ definition }))
      .filter(({ definition }) => definition.target === target)
      .map(({ definition }) => option(definition)),
  }));
}

type CalvingMarkDetection = {
  sets: LayoutMarkSet[];
  usedImageDetection: boolean;
};

type CalvingMarkDetector = (
  source: string,
  cell: LayoutCell,
  canonicalWidth: number,
  canonicalHeight: number,
  rowIndex: number,
) => Promise<CalvingMarkDetection>;

export async function detectCalvingMarkCells(
  source: string,
  cells: LayoutCell[],
  canonicalWidth: number,
  canonicalHeight: number,
  detector: CalvingMarkDetector = detectCalvingMarkPresetFromImage,
  rowIndexes?: ReadonlyMap<string, number>,
): Promise<{
  updates: ReadonlyMap<string, LayoutMarkSet[]>;
  total: number;
  detectedCount: number;
  fallbackCount: number;
}> {
  const markCells = cells.filter((cell) => cell.role === "mark");
  const results = await Promise.all(markCells.map(async (cell) => {
    const rowIndex = rowIndexes?.get(cell.id) ?? calvingMarkRowIndex(cell);
    try {
      const detected = await detector(
        source,
        cell,
        canonicalWidth,
        canonicalHeight,
        rowIndex,
      );
      return {
        cell,
        sets: detected.sets,
        usedImageDetection: detected.usedImageDetection,
      };
    } catch {
      return {
        cell,
        sets: calvingMarkPreset(cell, rowIndex),
        usedImageDetection: false,
      };
    }
  }));
  const updates = new Map(results.map((result) => [result.cell.id, result.sets]));
  const detectedCount = results.filter((result) => result.usedImageDetection).length;
  return {
    updates,
    total: results.length,
    detectedCount,
    fallbackCount: results.length - detectedCount,
  };
}

function calvingMarkRowIndex(cell: LayoutCell): number {
  const targetPath = cell.markSets?.find((markSet) => (
    /^calvings\.\d+\./.test(markSet.targetPath)
  ))?.targetPath;
  const match = targetPath ? /^calvings\.(\d+)\./.exec(targetPath) : null;
  return match ? Number(match[1]) : 0;
}

export function inferCalvingMarkRowIndexes(
  layout: LayoutDefinition,
  requestedCells: LayoutCell[],
): ReadonlyMap<string, number> {
  const cells = [...new Map(requestedCells.map((cell) => [cell.id, cell])).values()];
  if (cells.length === 0) return new Map();
  if (cells.length === 1) {
    return new Map([[cells[0].id, calvingMarkRowIndex(cells[0])]]);
  }
  const cellById = new Map(layout.cells.map((cell) => [cell.id, cell]));
  const anchors = layout.relations.flatMap((relation) => {
    const match = /^calvings\.(\d+)\./.exec(relation.targetPath);
    const anchor = cellById.get(relation.inputCellId);
    return match && anchor
      ? [{ cell: anchor, rowIndex: Number(match[1]) }]
      : [];
  });
  const requestedIds = new Set(cells.map((cell) => cell.id));
  const requestedRowCounts = new Map<number, number>();
  anchors.forEach((anchor) => {
    if (!requestedIds.has(anchor.cell.id)) return;
    requestedRowCounts.set(
      anchor.rowIndex,
      (requestedRowCounts.get(anchor.rowIndex) ?? 0) + 1,
    );
  });
  const reliableAnchors = anchors.filter((anchor) => (
    !requestedIds.has(anchor.cell.id)
    || requestedRowCounts.get(anchor.rowIndex) === 1
  ));
  const candidates = cells.flatMap((cell) => {
    const centerX = cell.x + cell.width / 2;
    const centerY = cell.y + cell.height / 2;
    const ownRelation = reliableAnchors.find((anchor) => anchor.cell.id === cell.id);
    if (ownRelation) {
      return [{ cell, rowIndex: ownRelation.rowIndex, score: -1 }];
    }
    const nearest = reliableAnchors
      .map((anchor) => {
        const anchorCenterX = anchor.cell.x + anchor.cell.width / 2;
        const anchorCenterY = anchor.cell.y + anchor.cell.height / 2;
        const deltaY = Math.abs(anchorCenterY - centerY);
        return {
          rowIndex: anchor.rowIndex,
          deltaY,
          score: deltaY * 10 + Math.abs(anchorCenterX - centerX),
        };
      })
      .filter((candidate) => (
        candidate.deltaY <= Math.max(cell.height, 20) * 0.8
      ))
      .sort((first, second) => first.score - second.score)[0];
    return nearest
      ? [{ cell, rowIndex: nearest.rowIndex, score: nearest.score }]
      : [];
  }).sort((first, second) => first.score - second.score);

  const result = new Map<string, number>();
  const usedRows = new Set<number>();
  candidates.forEach((candidate) => {
    if (result.has(candidate.cell.id) || usedRows.has(candidate.rowIndex)) return;
    result.set(candidate.cell.id, candidate.rowIndex);
    usedRows.add(candidate.rowIndex);
  });

  const typicalWidth = [...cells]
    .map((cell) => cell.width)
    .sort((first, second) => first - second)[Math.floor(cells.length / 2)] ?? 1;
  const columnThreshold = Math.max(20, typicalWidth * 1.25);
  const columns: Array<{ centerX: number; cells: LayoutCell[] }> = [];
  [...cells]
    .sort((first, second) => (
      first.x + first.width / 2 - (second.x + second.width / 2)
    ))
    .forEach((cell) => {
      const centerX = cell.x + cell.width / 2;
      const column = columns.find((candidate) => (
        Math.abs(candidate.centerX - centerX) <= columnThreshold
      ));
      if (!column) {
        columns.push({ centerX, cells: [cell] });
        return;
      }
      column.cells.push(cell);
      column.centerX = column.cells.reduce(
        (sum, candidate) => sum + candidate.x + candidate.width / 2,
        0,
      ) / column.cells.length;
    });
  const readingOrder = columns
    .sort((first, second) => first.centerX - second.centerX)
    .flatMap((column) => column.cells.sort(
      (first, second) => first.y - second.y || first.x - second.x,
    ));
  readingOrder.forEach((cell, spatialIndex) => {
    if (result.has(cell.id)) return;
    let rowIndex = spatialIndex;
    while (usedRows.has(rowIndex)) rowIndex += 1;
    result.set(cell.id, rowIndex);
    usedRows.add(rowIndex);
  });
  return result;
}

export function repairDuplicateCalvingMarkRows(layout: LayoutDefinition): {
  layout: LayoutDefinition;
  repairedCellCount: number;
} {
  const markCells = layout.cells.filter((cell) => (
    cell.role === "mark"
    && cell.markSets?.some((markSet) => /^calvings\.\d+\./.test(markSet.targetPath))
  ));
  const targetCounts = new Map<string, number>();
  markCells.forEach((cell) => cell.markSets?.forEach((markSet) => {
    targetCounts.set(markSet.targetPath, (targetCounts.get(markSet.targetPath) ?? 0) + 1);
  }));
  if (![...targetCounts.values()].some((count) => count > 1)) {
    return { layout, repairedCellCount: 0 };
  }
  const rowIndexes = inferCalvingMarkRowIndexes(layout, markCells);
  const repairedCellIds = new Set<string>();
  const cells = layout.cells.map((cell) => {
    const rowIndex = rowIndexes.get(cell.id);
    if (rowIndex === undefined || cell.role !== "mark") return cell;
    const markSets = (cell.markSets ?? []).map((markSet) => {
      const match = /^calvings\.\d+\.(.+)$/.exec(markSet.targetPath);
      if (!match) return markSet;
      const targetPath = `calvings.${rowIndex}.${match[1]}`;
      if (targetPath !== markSet.targetPath) repairedCellIds.add(cell.id);
      return { ...markSet, targetPath };
    });
    return { ...cell, markSets };
  });
  const relations = layout.relations.map((relation) => {
    const rowIndex = rowIndexes.get(relation.inputCellId);
    const match = /^calvings\.\d+\.(outcome|sex)$/.exec(relation.targetPath);
    if (rowIndex === undefined || !match) return relation;
    const targetPath = `calvings.${rowIndex}.${match[1]}`;
    return targetPath === relation.targetPath
      ? relation
      : { ...relation, targetPath, order: rowIndex + 1 };
  });
  return {
    layout: { ...layout, cells, relations },
    repairedCellCount: repairedCellIds.size,
  };
}

async function detectCalvingMarkPresetFromImage(
  source: string,
  cell: LayoutCell,
  canonicalWidth: number,
  canonicalHeight: number,
  rowIndex: number,
): Promise<CalvingMarkDetection> {
  const image = await new Promise<HTMLImageElement>((resolve, reject) => {
    const candidate = new Image();
    candidate.onload = () => resolve(candidate);
    candidate.onerror = () => reject(new Error("MARK_SOURCE_IMAGE_UNAVAILABLE"));
    candidate.src = source;
  });
  const canvas = document.createElement("canvas");
  canvas.width = canonicalWidth;
  canvas.height = canonicalHeight;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("MARK_CANVAS_UNAVAILABLE");
  context.drawImage(image, 0, 0, canonicalWidth, canonicalHeight);
  const pixels = context.getImageData(cell.x, cell.y, cell.width, cell.height);
  const detectedArea = detectCalvingChoiceArea(
    pixels.data,
    pixels.width,
    pixels.height,
  );
  return {
    sets: calvingMarkPreset(cell, rowIndex, detectedArea ?? undefined),
    usedImageDetection: detectedArea !== null,
  };
}

export function detectCalvingChoiceArea(
  rgba: ArrayLike<number>,
  width: number,
  height: number,
): Rect | null {
  if (width < 12 || height < 8 || rgba.length < width * height * 4) return null;
  const grayscale: number[] = [];
  for (let index = 0; index < width * height; index += 1) {
    const offset = index * 4;
    grayscale.push(
      rgba[offset] * 0.299 + rgba[offset + 1] * 0.587 + rgba[offset + 2] * 0.114,
    );
  }
  const sampled = grayscale.filter((_, index) => index % 7 === 0).sort((a, b) => a - b);
  const paper = sampled[Math.floor(sampled.length * 0.72)] ?? 220;
  const threshold = Math.max(85, Math.min(190, paper - 38));
  const ink = grayscale.map((value) => value < threshold);
  const rowInk = Array.from({ length: height }, (_, y) => {
    let count = 0;
    for (let x = 0; x < width; x += 1) count += ink[y * width + x] ? 1 : 0;
    return count;
  });
  const columnInk = Array.from({ length: width }, (_, x) => {
    let count = 0;
    for (let y = 0; y < height; y += 1) count += ink[y * width + x] ? 1 : 0;
    return count;
  });
  const lineRows = new Set(rowInk
    .map((count, index) => ({ count, index }))
    .filter(({ count }) => count >= width * 0.55)
    .map(({ index }) => index));
  const lineColumns = new Set(columnInk
    .map((count, index) => ({ count, index }))
    .filter(({ count }) => count >= height * 0.72)
    .map(({ index }) => index));
  const cleanedColumnInk = Array.from({ length: width }, (_, x) => {
    if (lineColumns.has(x)) return 0;
    let count = 0;
    for (let y = 0; y < height; y += 1) {
      if (!lineRows.has(y) && ink[y * width + x]) count += 1;
    }
    return count;
  });
  const xMargin = Math.max(2, Math.round(width * 0.015));
  const firstInk = cleanedColumnInk.findIndex(
    (count, x) => x >= xMargin && count >= Math.max(2, height * 0.025),
  );
  if (firstInk < 0 || firstInk > width * 0.35) return null;
  const minimumGap = Math.max(6, Math.round(width * 0.055));
  const searchStart = Math.max(firstInk + Math.round(width * 0.18), xMargin);
  const searchEnd = Math.min(width - xMargin, Math.round(width * 0.7));
  let optionRight = -1;
  let gapStart = -1;
  for (let x = searchStart; x <= searchEnd; x += 1) {
    const blank = cleanedColumnInk[x] <= 1;
    if (blank && gapStart < 0) gapStart = x;
    if ((!blank || x === searchEnd) && gapStart >= 0) {
      const gapEnd = blank && x === searchEnd ? x + 1 : x;
      if (gapEnd - gapStart >= minimumGap) {
        optionRight = gapStart;
        break;
      }
      gapStart = -1;
    }
  }
  if (optionRight < 0) optionRight = Math.round(width * 0.42);
  const areaLeft = Math.max(0, firstInk - Math.round(width * 0.018));
  const areaRight = Math.min(width, optionRight + Math.round(width * 0.01));
  if (areaRight - areaLeft < width * 0.18 || areaRight - areaLeft > width * 0.6) {
    return null;
  }
  const cleanedRowInk = Array.from({ length: height }, (_, y) => {
    if (lineRows.has(y)) return 0;
    let count = 0;
    for (let x = areaLeft; x < areaRight; x += 1) {
      if (!lineColumns.has(x) && ink[y * width + x]) count += 1;
    }
    return count;
  });
  const yMargin = Math.max(1, Math.round(height * 0.025));
  const activeRows = cleanedRowInk
    .map((count, y) => ({ count, y }))
    .filter(({ count, y }) => y >= yMargin
      && y < height - yMargin
      && count >= Math.max(2, (areaRight - areaLeft) * 0.025))
    .map(({ y }) => y);
  if (activeRows.length === 0) return null;
  const top = Math.max(0, activeRows[0] - Math.round(height * 0.04));
  const bottom = Math.min(
    height,
    activeRows[activeRows.length - 1] + 1 + Math.round(height * 0.04),
  );
  if (bottom - top < height * 0.45) return null;
  return {
    x: areaLeft,
    y: top,
    width: areaRight - areaLeft,
    height: bottom - top,
  };
}

type LabelPoolEntry = {
  key: string;
  label: string;
  section: string;
  paths: string[];
  aliases: string[];
};

export function createAutoLabelPoolEntry(
  labelText: string,
  targets: FieldMappingTarget[],
): LayoutLabelPoolEntry | null {
  const label = labelText.normalize("NFKC").trim();
  if (!label) return null;
  const normalizedLabel = normalizeLabelPoolText(label);
  if (targets.some((target) => (
    [target.label, ...(target.aliases ?? [])].some(
      (candidate) => normalizeLabelPoolText(candidate) === normalizedLabel,
    )
  ))) return null;
  const baseKey = label
    .replace(/[\s　.。・:：／/()（）［］\[\]【】]+/g, "")
    .slice(0, 100);
  if (!baseKey) return null;
  const byPath = new Map(targets.map((target) => [target.path, target]));
  let key = baseKey;
  let suffix = 2;
  while (
    byPath.has(`customFields.${key}`)
    && normalizeLabelPoolText(byPath.get(`customFields.${key}`)?.label ?? "")
      !== normalizeLabelPoolText(label)
  ) {
    const suffixText = `_${suffix}`;
    key = `${baseKey.slice(0, Math.max(1, 100 - suffixText.length))}${suffixText}`;
    suffix += 1;
  }
  const targetPath = `customFields.${key}`;
  if (byPath.has(targetPath)) return null;
  return {
    id: `label_auto_${key}`.slice(0, 120),
    label,
    targetPath,
    section: "自動追加",
    aliases: [],
    source: "ocr",
  };
}

export function createAutoRepeatedLabelPoolEntries(
  labelText: string,
  inputCount: number,
  targets: FieldMappingTarget[],
): LayoutLabelPoolEntry[] | null {
  if (inputCount < 2) return null;
  const label = labelText.normalize("NFKC").trim();
  if (!label) return null;
  const rawBaseKey = label
    .replace(/[\s　.。・:：／/()（）［］\[\]【】]+/g, "")
    .slice(0, 92);
  if (!rawBaseKey) return null;
  const byPath = new Map(targets.map((target) => [target.path, target]));
  const normalizedLabel = normalizeLabelPoolText(label);
  let baseKey = rawBaseKey;
  let suffix = 2;
  while (true) {
    const conflicting = Array.from({ length: inputCount }, (_, index) => {
      const existing = byPath.get(`customFields.${baseKey}_${index + 1}`);
      if (!existing) return false;
      return ![existing.label, ...(existing.aliases ?? [])].some((candidate) => (
        normalizeLabelPoolText(candidate).includes(normalizedLabel)
      ));
    }).some(Boolean);
    if (!conflicting) break;
    const suffixText = `_${suffix}`;
    baseKey = `${rawBaseKey.slice(0, Math.max(1, 92 - suffixText.length))}${suffixText}`;
    suffix += 1;
  }
  return Array.from({ length: inputCount }, (_, index) => {
    const rowNumber = index + 1;
    const key = `${baseKey}_${rowNumber}`;
    return {
      id: `label_auto_${key}`.slice(0, 120),
      label: `${label} ${rowNumber}行目`,
      targetPath: `customFields.${key}`,
      section: "自動追加",
      aliases: [label],
      source: "ocr" as const,
    };
  });
}

export function resolveLabelPoolTargetPath(
  entry: LabelPoolEntry,
  inputCellId: string,
  groups: LayoutRecordGroup[],
  sections: LayoutRecordSection[],
  assignedPaths: ReadonlySet<string>,
): string | null {
  const repeated = /^(saleCalves|calvings)\[\]\.([^.]+)$/.exec(entry.key);
  if (repeated) {
    const group = groups.find((candidate) => candidate.inputCellIds.includes(inputCellId));
    const section = group
      ? sections.find((candidate) => candidate.id === group.sectionId)
      : undefined;
    if (group && section?.collectionPath === repeated[1]) {
      const groupedPath = `${section.collectionPath}.${group.order - 1}.${repeated[2]}`;
      if (entry.paths.includes(groupedPath)) return groupedPath;
    }
    return entry.paths.find((path) => !assignedPaths.has(path)) ?? entry.paths[0] ?? null;
  }
  return entry.paths[0] ?? null;
}

export function buildLabelPoolEntries(targets: FieldMappingTarget[]): LabelPoolEntry[] {
  const entries = new Map<string, LabelPoolEntry>();
  targets.forEach((target) => {
    const arrayMatch = /^(saleCalves|calvings)\.\d+\.([^.]+)$/.exec(target.path);
    const key = arrayMatch ? `${arrayMatch[1]}[].${arrayMatch[2]}` : target.path;
    const label = arrayMatch
      ? target.label.split("・").at(-1)?.trim() || target.label
      : target.label.trim();
    const existing = entries.get(key);
    if (existing) {
      if (!existing.paths.includes(target.path)) existing.paths.push(target.path);
      target.aliases?.forEach((alias) => {
        if (!existing.aliases.includes(alias)) existing.aliases.push(alias);
      });
      return;
    }
    entries.set(key, {
      key,
      label,
      section: target.section,
      paths: [target.path],
      aliases: [...(target.aliases ?? [])],
    });
  });
  return [...entries.values()].sort((first, second) => (
    first.section.localeCompare(second.section, "ja")
    || first.label.localeCompare(second.label, "ja")
  ));
}

function findLabelPoolMatch(text: string, entries: LabelPoolEntry[]): LabelPoolEntry | null {
  const normalized = normalizeLabelPoolText(text);
  if (!normalized) return null;
  return entries.find((entry) => {
    return [entry.label, ...entry.aliases].some((label) => {
      const candidate = normalizeLabelPoolText(label);
      return normalized === candidate
        || (candidate.length >= 2 && normalized.includes(candidate))
        || (normalized.length >= 2 && candidate.includes(normalized));
    });
  }) ?? null;
}

function normalizeLabelPoolText(value: string): string {
  return value
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[壳殻]/g, "売")
    .replace(/[歷暦]/g, "歴")
    .replace(/曰/g, "日")
    .replace(/令/g, "齢")
    .replace(/[\s　・:：()（）［］\[\]【】]/g, "");
}

function normalizeLayout(layout: LayoutDefinition): LayoutDefinition {
  const recordSections = (layout.recordSections ?? []).map((section) => {
    const { recordLabelCellId, ...current } = section;
    return {
      ...current,
      recordLabelCellIds: [...new Set(
        section.recordLabelCellIds
        ?? (recordLabelCellId ? [recordLabelCellId] : []),
      )],
      rowKeyStrategy: section.rowKeyStrategy ?? "position",
      rowKeyCellIds: [...new Set(section.rowKeyCellIds ?? [])],
      includeEmptyRows: section.includeEmptyRows ?? false,
    };
  });
  const recordGroups = layout.recordGroups ?? [];
  return {
    ...layout,
    layoutSchemaVersion: layout.layoutSchemaVersion ?? 3,
    recordSections,
    recordGroups,
    labelPool: layout.labelPool ?? [],
    // Older drafts can retain false even after every record band has been
    // connected to its parent label. Derive the UI state from the saved
    // structure so the user can save and continue to verification.
    recordStructureVerified: recordStructureIsLinked(recordSections, recordGroups),
    cells: layout.cells.map((cell) => ({
      ...cell,
      printedLabelText: cell.printedLabelText ?? "",
      markSets: cell.markSets ?? [],
    })),
  };
}

function restoreLayoutDraft(
  initialLayout: LayoutDefinition,
  draftStorageKey?: string,
): LayoutDefinition {
  if (!draftStorageKey) return normalizeLayout(initialLayout);
  try {
    const stored = window.localStorage.getItem(draftStorageKey);
    if (!stored) return normalizeLayout(initialLayout);
    return normalizeLayout(JSON.parse(stored) as LayoutDefinition);
  } catch {
    try {
      window.localStorage.removeItem(draftStorageKey);
    } catch {
      // Ignore storage failures and continue with the server-backed layout.
    }
    return normalizeLayout(initialLayout);
  }
}

function layoutSignature(layout: LayoutDefinition): string {
  return JSON.stringify(layout);
}

function pointInsideRect(point: Point, rect: Rect): boolean {
  return point.x >= rect.x
    && point.x <= rect.x + rect.width
    && point.y >= rect.y
    && point.y <= rect.y + rect.height;
}

function boundingRect(
  cells: LayoutCell[],
  canonicalWidth: number,
  canonicalHeight: number,
): Rect {
  const padding = 4;
  const left = Math.max(0, Math.min(...cells.map((cell) => cell.x)) - padding);
  const top = Math.max(0, Math.min(...cells.map((cell) => cell.y)) - padding);
  const right = Math.min(
    canonicalWidth,
    Math.max(...cells.map((cell) => cell.x + cell.width)) + padding,
  );
  const bottom = Math.min(
    canonicalHeight,
    Math.max(...cells.map((cell) => cell.y + cell.height)) + padding,
  );
  return {
    x: left,
    y: top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
  };
}

export function inferRecordTargetPath(
  inputs: LayoutCell[],
  cells: LayoutCell[],
  groups: LayoutRecordGroup[],
  sections: LayoutRecordSection[],
  targets: FieldMappingTarget[],
): string | null {
  const groupByInput = new Map(
    groups.flatMap((group) => group.inputCellIds.map((inputId) => [inputId, group] as const)),
  );
  const sectionById = new Map(sections.map((section) => [section.id, section]));
  const selectedByGroup = new Map<string, LayoutCell>();
  for (const input of inputs) {
    const group = groupByInput.get(input.id);
    if (!group) return null;
    selectedByGroup.set(group.id, input);
  }
  const firstGroup = groupByInput.get(inputs[0]?.id ?? "");
  const firstSelected = firstGroup ? selectedByGroup.get(firstGroup.id) : undefined;
  const section = firstGroup ? sectionById.get(firstGroup.sectionId) : undefined;
  if (!firstGroup || !firstSelected || !section) return null;
  if (
    [...selectedByGroup.keys()].some(
      (groupId) => groupByInput.get(
        selectedByGroup.get(groupId)?.id ?? "",
      )?.sectionId !== section.id,
    )
  ) return null;

  const cellById = new Map(cells.map((cell) => [cell.id, cell]));
  const members = firstGroup.inputCellIds
    .map((inputId) => cellById.get(inputId))
    .filter((cell): cell is LayoutCell => Boolean(cell))
    .sort((first, second) => first.x - second.x || first.y - second.y);
  const fields = targets
    .filter((target) => target.path.startsWith(`${section.collectionPath}.0.`))
    .map((target) => target.path.split(".").at(-1))
    .filter((field): field is string => Boolean(field));
  if (members.length !== fields.length) return null;
  const columnIndex = members.findIndex((member) => member.id === firstSelected.id);
  if (columnIndex < 0 || !fields[columnIndex]) return null;
  return `${section.collectionPath}.0.${fields[columnIndex]}`;
}

export function inferTableColumnTargetPath(
  inputs: LayoutCell[],
  cells: LayoutCell[],
  section: LayoutRecordSection | undefined,
  targets: FieldMappingTarget[],
): string | null {
  if (!section || inputs.length === 0) return null;
  const selectedCentersY = inputs.map((cell) => cell.y + cell.height / 2);
  const minimumY = Math.min(...selectedCentersY) - 5;
  const maximumY = Math.max(...selectedCentersY) + 5;
  const candidates = cells
    .filter((cell) => (
      cell.role === "input"
      && cell.y + cell.height / 2 >= minimumY
      && cell.y + cell.height / 2 <= maximumY
    ))
    .sort((first, second) => first.x - second.x);
  const columnCenters: number[] = [];
  candidates.forEach((cell) => {
    const center = cell.x + cell.width / 2;
    const existing = columnCenters.findIndex((value) => Math.abs(value - center) <= 8);
    if (existing < 0) columnCenters.push(center);
    else columnCenters[existing] = (columnCenters[existing] + center) / 2;
  });
  columnCenters.sort((first, second) => first - second);
  const fields = targets
    .filter((target) => target.path.startsWith(`${section.collectionPath}.0.`))
    .map((target) => target.path.split(".").at(-1))
    .filter((field): field is string => Boolean(field));
  if (columnCenters.length === 0 || columnCenters.length !== fields.length) return null;
  const selectedCenter = inputs.reduce(
    (total, input) => total + input.x + input.width / 2,
    0,
  ) / inputs.length;
  const columnIndex = columnCenters.reduce(
    (nearest, center, index) => (
      Math.abs(center - selectedCenter) < Math.abs(
        (columnCenters[nearest] ?? selectedCenter) - selectedCenter,
      )
        ? index
        : nearest
    ),
    0,
  );
  return fields[columnIndex]
    ? `${section.collectionPath}.0.${fields[columnIndex]}`
    : null;
}

export function targetPathsFromRecordGroups(
  firstPath: string,
  inputs: LayoutCell[],
  groups: LayoutRecordGroup[],
  sections: LayoutRecordSection[],
  targets: FieldMappingTarget[],
): string[] | null {
  const match = /^(saleCalves|calvings)\.\d+\.([^.]+)$/.exec(firstPath);
  if (!match) return null;
  const fieldKey = match[2];
  const groupByInput = new Map(
    groups.flatMap((group) => group.inputCellIds.map((inputId) => [inputId, group] as const)),
  );
  const sectionById = new Map(sections.map((section) => [section.id, section]));
  const knownPaths = new Set(targets.map((target) => target.path));
  const paths = inputs.map((input) => {
    const group = groupByInput.get(input.id);
    const section = group ? sectionById.get(group.sectionId) : undefined;
    return group && section
      ? `${section.collectionPath}.${group.order - 1}.${fieldKey}`
      : null;
  });
  return paths.every((path): path is string => path !== null && knownPaths.has(path))
    ? paths
    : null;
}

export function expandTargetPaths(
  firstPath: string,
  count: number,
  targets: FieldMappingTarget[],
): string[] | null {
  if (count === 1) return targets.some((target) => target.path === firstPath) ? [firstPath] : null;
  const match = /^(saleCalves|calvings)\.(\d+)\.([^.]+)$/.exec(firstPath);
  if (!match) return null;
  const [, collection, firstIndexText, field] = match;
  const firstIndex = Number(firstIndexText);
  const knownPaths = new Set(targets.map((target) => target.path));
  const paths = Array.from(
    { length: count },
    (_, index) => `${collection}.${firstIndex + index}.${field}`,
  );
  return paths.every((path) => knownPaths.has(path)) ? paths : null;
}

function sequentialTargetCapacity(
  firstPath: string,
  targets: FieldMappingTarget[],
): number | null {
  const match = /^(saleCalves|calvings)\.(\d+)\.([^.]+)$/.exec(firstPath);
  if (!match) return null;
  const [, collection, firstIndexText, field] = match;
  const firstIndex = Number(firstIndexText);
  const knownPaths = new Set(targets.map((target) => target.path));
  let count = 0;
  while (knownPaths.has(`${collection}.${firstIndex + count}.${field}`)) {
    count += 1;
  }
  return count;
}

export function inferRecordCollectionPath(
  inputs: LayoutCell[],
  relations: LayoutRelation[],
): "saleCalves" | "calvings" | null {
  const inputIds = new Set(inputs.map((input) => input.id));
  const collections = new Set<"saleCalves" | "calvings">();
  relations.forEach((relation) => {
    if (!inputIds.has(relation.inputCellId)) return;
    const match = /^(saleCalves|calvings)\.\d+\.[^.]+$/.exec(
      relation.targetPath,
    );
    if (match?.[1] === "saleCalves" || match?.[1] === "calvings") {
      collections.add(match[1]);
    }
  });
  return collections.size === 1 ? [...collections][0] ?? null : null;
}

export function recordRowsFromColumnLabels(
  labelIds: string[],
  cells: LayoutCell[],
  relations: LayoutRelation[],
): {
  collectionPath: "saleCalves" | "calvings" | null;
  rows: Array<{ order: number; inputCellIds: string[] }>;
} | null {
  const cellById = new Map(cells.map((cell) => [cell.id, cell]));
  const genericRows = (): {
    collectionPath: null;
    rows: Array<{ order: number; inputCellIds: string[] }>;
  } | null => {
    const columns = [...new Set(labelIds)].map((labelId) => relations
      .filter((relation) => relation.labelCellIds.includes(labelId))
      .flatMap((relation) => {
        const input = cellById.get(relation.inputCellId);
        return input && (input.role === "input" || input.role === "mark")
          ? [input]
          : [];
      })
      .filter((cell, index, items) => items.findIndex((item) => item.id === cell.id) === index)
      .sort((first, second) => (
        first.y + first.height / 2 - (second.y + second.height / 2)
        || first.x - second.x
      )));
    const counts = new Set(columns.map((column) => column.length));
    if (columns.length < 2 || counts.size !== 1 || columns[0].length === 0) return null;
    return {
      collectionPath: null,
      rows: Array.from({ length: columns[0].length }, (_, index) => ({
        order: index + 1,
        inputCellIds: columns.map((column) => column[index].id),
      })),
    };
  };
  const parse = (targetPath: string): {
    collectionPath: "saleCalves" | "calvings";
    rowIndex: number;
    field: string;
  } | null => {
    const match = /^(saleCalves|calvings)\.(\d+)\.([^.]+)$/.exec(targetPath);
    return match && (match[1] === "saleCalves" || match[1] === "calvings")
      ? {
        collectionPath: match[1] as "saleCalves" | "calvings",
        rowIndex: Number(match[2]),
        field: match[3],
      }
      : null;
  };
  const uniqueLabelIds = [...new Set(labelIds)];
  if (uniqueLabelIds.length < 2) return null;
  const descriptors = uniqueLabelIds.flatMap((labelId) => {
    const matches = relations.flatMap((relation) => {
      if (!relation.labelCellIds.includes(labelId)) return [];
      const parsed = parse(relation.targetPath);
      return parsed ? [parsed] : [];
    });
    const targets = new Set(matches.map((match) => `${match.collectionPath}.${match.field}`));
    if (matches.length === 0 || targets.size !== 1) return [];
    const first = matches[0];
    return [{
      labelId,
      collectionPath: first.collectionPath,
      field: first.field,
      rowIndexes: matches.map((match) => match.rowIndex),
    }];
  });
  if (descriptors.length !== uniqueLabelIds.length) return genericRows();
  const collections = new Set(descriptors.map((descriptor) => descriptor.collectionPath));
  const fields = new Set(descriptors.map((descriptor) => descriptor.field));
  if (collections.size !== 1 || fields.size !== descriptors.length) return genericRows();
  const collectionPath = [...collections][0];
  const rowIndexes = [...new Set(
    descriptors.flatMap((descriptor) => descriptor.rowIndexes),
  )].sort((first, second) => first - second);
  const rows: Array<{ order: number; inputCellIds: string[] }> = [];
  for (const rowIndex of rowIndexes) {
    const inputIds = new Set<string>();
    for (const descriptor of descriptors) {
      const fieldInputIds = new Set<string>();
      relations.forEach((relation) => {
        const parsed = parse(relation.targetPath);
        if (
          relation.labelCellIds.includes(descriptor.labelId)
          && parsed?.collectionPath === collectionPath
          && parsed.rowIndex === rowIndex
          && parsed.field === descriptor.field
          && cellById.has(relation.inputCellId)
        ) fieldInputIds.add(relation.inputCellId);
      });
      cells.forEach((cell) => {
        if (cell.role !== "mark") return;
        if ((cell.markSets ?? []).some((markSet) => {
          const parsed = parse(markSet.targetPath);
          return parsed?.collectionPath === collectionPath
            && parsed.rowIndex === rowIndex
            && parsed.field === descriptor.field;
        })) fieldInputIds.add(cell.id);
      });
      if (fieldInputIds.size === 0) return genericRows();
      fieldInputIds.forEach((inputId) => inputIds.add(inputId));
    }
    const orderedIds = [...inputIds].sort((firstId, secondId) => {
      const first = cellById.get(firstId);
      const second = cellById.get(secondId);
      if (!first || !second) return 0;
      return first.x - second.x || first.y - second.y;
    });
    if (orderedIds.length === 0) return genericRows();
    rows.push({ order: rowIndex + 1, inputCellIds: orderedIds });
  }
  return rows.length > 0 ? { collectionPath, rows } : null;
}

export function matchRowKeyCells(
  rows: Array<{ order: number; inputCellIds: string[] }>,
  cells: LayoutCell[],
): Array<LayoutCell | null> | null {
  const cellById = new Map(cells.map((cell) => [cell.id, cell]));
  const rowKeys = cells
    .filter((cell) => cell.role === "record_key_cell")
    .sort((first, second) => first.y - second.y || first.x - second.x);
  if (rowKeys.length < rows.length) return null;
  const unused = new Set(rowKeys.map((cell) => cell.id));
  const matches: Array<LayoutCell | null> = [];
  for (const row of rows) {
    const inputs = row.inputCellIds
      .map((inputId) => cellById.get(inputId))
      .filter((cell): cell is LayoutCell => Boolean(cell));
    if (inputs.length === 0) return null;
    const rowCenter = inputs.reduce(
      (total, cell) => total + cell.y + cell.height / 2,
      0,
    ) / inputs.length;
    const candidate = rowKeys
      .filter((cell) => unused.has(cell.id))
      .sort((first, second) => (
        Math.abs(first.y + first.height / 2 - rowCenter)
        - Math.abs(second.y + second.height / 2 - rowCenter)
      ))[0];
    if (!candidate) return null;
    const rowHeight = Math.max(...inputs.map((cell) => cell.height));
    const distance = Math.abs(candidate.y + candidate.height / 2 - rowCenter);
    if (distance > Math.max(rowHeight, candidate.height) * 1.5) return null;
    unused.delete(candidate.id);
    matches.push(candidate);
  }
  return matches;
}

function genericRecordCollectionKey(
  title: string,
  sections: LayoutRecordSection[],
): string {
  const base = `section_${Array.from(title.normalize("NFKC"))
    .reduce((hash, character) => ((hash * 31) + (character.codePointAt(0) ?? 0)) >>> 0, 2166136261)
    .toString(36)}`;
  const used = new Set(sections.map((section) => section.collectionPath));
  let key = base;
  let suffix = 2;
  while (used.has(`genericRecords.${key}`)) {
    key = `${base}_${suffix}`;
    suffix += 1;
  }
  return key;
}

export function recordLabelCellsCrossedByLine(
  start: Point,
  end: Point,
  labelCells: LayoutCell[],
): LayoutCell[] {
  return labelCells
    .filter((cell) => segmentIntersectsRect(start, end, cell, 8))
    .sort((first, second) => first.x - second.x || first.y - second.y);
}

function recordStructureIsLinked(
  sections: LayoutRecordSection[],
  groups: LayoutRecordGroup[],
): boolean {
  if (groups.length === 0) return false;
  const groupedSectionIds = new Set(groups.map((group) => group.sectionId));
  return [...groupedSectionIds].every((sectionId) => (
    Boolean((() => {
      const section = sections.find((candidate) => candidate.id === sectionId);
      if (!section || section.recordLabelCellIds.length === 0) return false;
      return section.recordType === "generic" || section.parentLabelCellId;
    })())
  ));
}

export function linkLaneCells(
  start: Point,
  end: Point,
  cells: LayoutCell[],
): LayoutCell[] {
  const intersecting = cells.filter((cell) => segmentIntersectsRect(
    start,
    end,
    cell,
    12,
  ));
  if (intersecting.length <= 1) return intersecting;
  const vertical = Math.abs(end.y - start.y) > Math.abs(end.x - start.x);
  const seed = intersecting.reduce((nearest, cell) => (
    distanceToCenterSquared(cell, end) < distanceToCenterSquared(nearest, end)
      ? cell
      : nearest
  ));
  const longitudinalStart = vertical
    ? Math.min(start.y, end.y)
    : Math.min(start.x, end.x);
  const longitudinalEnd = vertical
    ? Math.max(start.y, end.y)
    : Math.max(start.x, end.x);
  const sameLane = cells.filter((cell) => {
    const laneOverlap = vertical
      ? intervalOverlap(cell.x, cell.width, seed.x, seed.width)
        >= Math.min(cell.width, seed.width) * 0.35
      : intervalOverlap(cell.y, cell.height, seed.y, seed.height)
        >= Math.min(cell.height, seed.height) * 0.35;
    const center = vertical
      ? cell.y + cell.height / 2
      : cell.x + cell.width / 2;
    return laneOverlap
      && center >= longitudinalStart - 12
      && center <= longitudinalEnd + 12;
  });
  const deltaX = end.x - start.x;
  const deltaY = end.y - start.y;
  return sameLane.sort((a, b) => {
    const aProgress = (a.x + a.width / 2 - start.x) * deltaX
      + (a.y + a.height / 2 - start.y) * deltaY;
    const bProgress = (b.x + b.width / 2 - start.x) * deltaX
      + (b.y + b.height / 2 - start.y) * deltaY;
    return aProgress - bProgress;
  });
}

function distanceToCenterSquared(cell: LayoutCell, point: Point): number {
  const deltaX = cell.x + cell.width / 2 - point.x;
  const deltaY = cell.y + cell.height / 2 - point.y;
  return deltaX * deltaX + deltaY * deltaY;
}

function intervalOverlap(
  startA: number,
  lengthA: number,
  startB: number,
  lengthB: number,
): number {
  return Math.max(
    0,
    Math.min(startA + lengthA, startB + lengthB) - Math.max(startA, startB),
  );
}

export function targetPathsForInputs(
  inputs: LayoutCell[],
  relations: LayoutRelation[],
): string[] | null {
  const paths = inputs.map((input) => relations.find(
    (relation) => relation.inputCellId === input.id,
  )?.targetPath);
  return paths.every((path): path is string => Boolean(path)) ? paths : null;
}

export function resolveAvailableLinkTargetPaths(
  requestedPaths: string[],
  inputs: LayoutCell[],
  relations: LayoutRelation[],
  targets: FieldMappingTarget[],
): string[] | null {
  const inputIds = new Set(inputs.map((input) => input.id));
  const occupiedPaths = new Set(
    relations
      .filter((relation) => !inputIds.has(relation.inputCellId))
      .map((relation) => relation.targetPath),
  );
  if (requestedPaths.every((path) => !occupiedPaths.has(path))) return requestedPaths;
  if (requestedPaths.length !== 1) return null;

  const requested = /^(saleCalves|calvings)\.(\d+)\.([^.]+)$/.exec(requestedPaths[0]);
  if (!requested) return null;
  const [, collection, requestedIndexText, field] = requested;
  const requestedIndex = Number(requestedIndexText);
  const candidates = targets
    .flatMap((target) => {
      const match = /^(saleCalves|calvings)\.(\d+)\.([^.]+)$/.exec(target.path);
      return match && match[1] === collection && match[3] === field
        ? [{ path: target.path, index: Number(match[2]) }]
        : [];
    })
    .filter((candidate) => !occupiedPaths.has(candidate.path))
    .sort((first, second) => (
      (first.index < requestedIndex ? 1 : 0) - (second.index < requestedIndex ? 1 : 0)
      || first.index - second.index
    ));
  return candidates[0] ? [candidates[0].path] : null;
}

export function inferTargetPath(
  labelText: string,
  inputCount: number,
  targets: FieldMappingTarget[],
  assignedPaths: ReadonlySet<string>,
): string | null {
  const normalizedLabel = normalizeLabel(labelText);
  if (!normalizedLabel) return null;
  const exactCandidates = targets.filter((target) => {
    const fieldLabel = target.label.split("・").at(-1) ?? target.label;
    return [fieldLabel, ...(target.aliases ?? [])].some(
      (candidate) => normalizeLabel(candidate) === normalizedLabel,
    );
  });
  const partialCandidates = targets.filter((target) => {
    const fieldLabel = target.label.split("・").at(-1) ?? target.label;
    return [fieldLabel, ...(target.aliases ?? [])].some((candidate) => {
      const normalizedField = normalizeLabel(candidate);
      return normalizedLabel.includes(normalizedField)
        || normalizedField.includes(normalizedLabel);
    });
  });
  const candidates = exactCandidates.length > 0 ? exactCandidates : partialCandidates;
  if (
    exactCandidates.length === 0
    && new Set(
      candidates.map((candidate) => (
        normalizeLabel(candidate.label.split("・").at(-1) ?? candidate.label)
      )),
    ).size > 1
  ) return null;
  for (const candidate of candidates) {
    const paths = expandTargetPaths(candidate.path, inputCount, targets);
    if (paths && paths.every((path) => !assignedPaths.has(path))) return candidate.path;
  }
  return candidates.find(
    (candidate) => expandTargetPaths(candidate.path, inputCount, targets) !== null,
  )?.path ?? null;
}

export function segmentIntersectsRect(
  start: Point,
  end: Point,
  rect: Rect,
  padding = 0,
): boolean {
  const left = rect.x - padding;
  const right = rect.x + rect.width + padding;
  const top = rect.y - padding;
  const bottom = rect.y + rect.height + padding;
  const deltaX = end.x - start.x;
  const deltaY = end.y - start.y;
  let minimum = 0;
  let maximum = 1;
  const edges = [
    [-deltaX, start.x - left],
    [deltaX, right - start.x],
    [-deltaY, start.y - top],
    [deltaY, bottom - start.y],
  ];
  for (const [direction, distance] of edges) {
    if (direction === 0) {
      if (distance < 0) return false;
      continue;
    }
    const ratio = distance / direction;
    if (direction < 0) minimum = Math.max(minimum, ratio);
    else maximum = Math.min(maximum, ratio);
    if (minimum > maximum) return false;
  }
  return true;
}

export function mergeLabelRelations(
  currentRelations: LayoutRelation[],
  inputs: LayoutCell[],
  labelId: string,
  axis: LayoutRelation["axis"],
  targetPaths: string[],
  relationStamp = Date.now(),
): LayoutRelation[] {
  const inputIds = new Set(inputs.map((input) => input.id));
  const nextTargetPaths = new Set(targetPaths);
  const targetByInputId = new Map(
    inputs.map((input, index) => [input.id, targetPaths[index]]),
  );
  const preserved = currentRelations
    .filter((relation) => (
      !nextTargetPaths.has(relation.targetPath)
      || inputIds.has(relation.inputCellId)
    ))
    .filter((relation) => (
      !inputIds.has(relation.inputCellId)
      || !relation.labelCellIds.includes(labelId)
    ))
    .filter((relation) => (
      !inputIds.has(relation.inputCellId)
      || relation.labelCellIds.length > 0
    ))
    .map((relation) => {
      const nextTargetPath = targetByInputId.get(relation.inputCellId);
      return nextTargetPath ? { ...relation, targetPath: nextTargetPath } : relation;
    });
  return [
    ...preserved,
    ...inputs.map((input, index) => ({
      id: `rel_${relationStamp}_${index + 1}`,
      inputCellId: input.id,
      labelCellIds: [labelId],
      axis,
      order: index + 1,
      targetPath: targetPaths[index],
    })),
  ];
}

function normalizeLabel(value: string) {
  return value
    .normalize("NFKC")
    .replace(/[壳殻]/g, "売")
    .replace(/[歷暦]/g, "歴")
    .replace(/曰/g, "日")
    .replace(/令/g, "齢")
    .replace(/[\s:：・／/()（）［\]\-―ー]/g, "")
    .toLowerCase();
}

function renumberRelations(relations: LayoutRelation[], cells: LayoutCell[]) {
  const byId = new Map(cells.map((cell) => [cell.id, cell]));
  const groups = new Map<string, LayoutRelation[]>();
  relations.forEach((relation) => {
    const key = `${relation.labelCellIds.join("|")}:${relation.axis}`;
    groups.set(key, [...(groups.get(key) ?? []), relation]);
  });
  const orderByRelationId = new Map<string, number>();
  groups.forEach((items) => {
    items.sort((first, second) => {
      const a = byId.get(first.inputCellId);
      const b = byId.get(second.inputCellId);
      if (!a || !b) return 0;
      return first.axis === "horizontal" ? a.x - b.x : a.y - b.y;
    });
    items.forEach((relation, index) => orderByRelationId.set(relation.id, index + 1));
  });
  return relations.map((relation) => ({
    ...relation,
    order: orderByRelationId.get(relation.id) ?? relation.order,
  }));
}
