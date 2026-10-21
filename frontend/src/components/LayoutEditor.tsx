import { cellName, displayName, logDetail, userText } from "../userMessages";
import { privateLocalStorage } from "../auth/transport";
import { TemplateAiFocus } from "../editing/AiFocusLayer";
import { changedFacets, facetTouch, patchedCellFacets } from "../editing/authoringFacets";
import type { FacetTouch } from "../editing/template-analysis.generated";
import type { AiFocusState } from "../editing/focusState";
import type { ContextResult } from "../editing/v2.generated";
import { nearbyWireCell } from "./layoutWireInteraction";
import { useInteractionContext } from "../assistant/useInteractionContext";
import type { LayoutAssistantSelection } from "../assistant/templateSelection";
import {
  forwardRef,
  useCallback,
  useEffect,
  useLayoutEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CSSProperties, ReactNode } from "react";
import { createPortal } from "react-dom";
import { useCornerPin } from "./useCornerPin";
import { FiCheckSquare, FiChevronLeft, FiChevronRight, FiArrowRight, FiCornerDownRight, FiEdit3, FiEye, FiGrid, FiLayers, FiLink, FiMousePointer, FiMove, FiPlusSquare, FiRotateCcw, FiSearch, FiSettings, FiTag, FiType, FiX } from "react-icons/fi";
import type {
  DocumentIdentity,
  DocumentNameRule,
  FieldMappingTarget,
  LayoutCell,
  LayoutCellMerge,
  LayoutCellMergePart,
  LayoutDefinition,
  LayoutLabelPoolEntry,
  LayoutMarkSet,
  LayoutRecordGroup,
  LayoutRecordSection,
  LayoutRelation,
} from "../types";
import { showToast } from "./Toast";
import "./LayoutEditor.move.css";
import { movePageHeading, pageDisplayCells, type PageView } from "../pageAuthoring";
import { buildHeadingConnections, traceHeadingConnection } from "./headingConnections";
import {
  attachHeading,
  enclosedHeadingChildren,
  moveLayoutCell,
  pointInCell,
  polygonCell,
  uniqueMeaningEntry,
} from "./layoutAuthoring";
import {
  type Rect,
  type Point,
  routeSegmentKey,
  pointInsideRect,
  segmentDistance,
  rectCenter,
  boundingRect,
  cellsSelectedByRect,
} from "./layoutEditorGeometry";
import {
  normalizeLayout,
  unassignedMeaningCellIds,
  incompleteMeaningCellIds,
  buildLabelPoolEntries,
  normalizeLabelPoolText,
  buildMeaningRelationRoutes,
  type MeaningRelationRoute,
  headingConnectionHit,
  eraseMeaningLabelLinksAlongSegment,
  eraseMeaningParentLinksAlongSegment,
  createAutoLabelPoolEntry,
  findLabelPoolMatch,
  meaningLinkLaneCells,
  targetPathsForInputs,
  inferTargetPath,
  createAutoRepeatedLabelPoolEntries,
  resolveAvailableLinkTargetPaths,
  mergeLabelRelations,
  renumberRelations,
  attachParentLabelToMeaningRelations,
  resolveLabelPoolTargetPath,
  labelCellName,
} from "./layoutEditorMeaning";
import {
  type OcrLabelAlignment,
  type OcrLabelVerticalAlignment,
  LAYOUT_ROLE_STYLE,
  drawOcrLabelText,
  drawCompositeSubfields,
  drawUnassignedMeaningOverlay,
  LAYOUT_ROLE_LABEL,
} from "./layoutEditorDrawing";
import {
  recordStructureIsLinked,
  recordLabelCellsCrossedByLine,
  linkLaneCells,
  recordRowsFromColumnLabels,
  genericRecordCollectionKey,
  recordSectionDisplayName,
  recordSectionAtPoint,
} from "./layoutEditorRecords";
import {
  removeLayoutCells,
  planManualCellMerge,
  unselectedCellsInsideMerge,
  cloneCellMerge,
  findCopiedMergeTargetCells,
} from "./layoutEditorMerges";
import {
  checklistLabel,
  checklistPreset,
  genericManualChoicePreset,
  newMarkOptionId,
  newMarkSetId,
  MarkCellEditor,
} from "./LayoutMarkEditors";

type VirtualHeadingEditorState = { id: string | null; rect: Rect; value: string };
type Mode = "select" | "move" | "classify" | "paint" | "draw" | "link" | "record" | "parent" | "record-parent" | "erase" | "identity" | "virtual" | "virtual-group";
export type LayoutWorkflowStep = "classify" | "meaning" | "records" | "identity";
export type LayoutEditorHandle = {
  readAnalysisDraft: () => LayoutDefinition | null;
  readAnalysisPriority: () => number[] | null;
  matchesAnalysisDraft: (input: LayoutDefinition) => boolean;
  applyAnalysisDraft: (input: LayoutDefinition, merged: LayoutDefinition, action?: "apply" | "undo") => boolean;
  saveIfNeeded: () => Promise<boolean>;
  beginAssistant: () => void;
  endAssistant: () => void;
  discardUnsaved: () => boolean;
  acceptAssistantSaved: (layout: LayoutDefinition, discardDraft: boolean) => boolean;
  acceptOwnSaved: (submitted: LayoutDefinition, saved: LayoutDefinition) => void;
  acceptReviewedSaved: (previousSaved: LayoutDefinition, reviewedSaved: LayoutDefinition) => void;
};
export type LayoutEditorFocusRequest = {
  requestId: number;
  cellId: string;
} & (
  { purpose: "meaning-cell" | "mark-cell" }
  | { purpose: "mark-options"; markSetId: string }
);
export type LayoutEditorFocusResult = {
  requestId: number;
  focused: boolean;
};
export type LayoutEditorMeaningState = {
  cells: Array<{
    id: string;
    role: LayoutCell["role"];
    markSetIds: string[];
  }>;
  unassignedCellIds: string[];
  incompleteMarkCellIds: string[];
  usableMeaningCellCount: number;
};
type LayoutEditorProps = {
  aiFocus?: AiFocusState | null;
  aiContext?: ContextResult | null;
  aiViewRevision?: number;
  source: string;
  pageView?: PageView;
  pageNavigation?: {
    currentPage: number;
    totalPages: number;
    hasPrevious: boolean;
    hasNext: boolean;
    disabled?: boolean;
    onPrevious: () => void;
    onNext: () => void;
  };
  initialLayout: LayoutDefinition;
  targets: FieldMappingTarget[];
  onDetect: () => Promise<LayoutDefinition>;
  onRecognizeLabels?: (layout: LayoutDefinition) => Promise<LayoutDefinition>;
  onSave: (layout: LayoutDefinition) => Promise<LayoutDefinition>;
  operationEndSave?: boolean;
  autoSaveUnchanged?: boolean;
  saveSignal?: string;
  onInteractionChange?: (active: boolean) => void;
  reconcileSaved?: (current: LayoutDefinition, submitted: LayoutDefinition, saved: LayoutDefinition) => LayoutDefinition;
  onUndoSaved?: () => Promise<LayoutDefinition>;
  canUndoSaved?: boolean;
  allowUnchangedSave?: boolean;
  disabled?: boolean;
  workflowStep?: LayoutWorkflowStep;
  /** Previous / next step buttons shown at the top of the floating tools. */
  stepNavigation?: { index: number; count: number; title: string; previous: (() => void) | null; next: (() => void) | null };
  focusRequest?: LayoutEditorFocusRequest | null;
  onFocusRequestResult?: (result: LayoutEditorFocusResult) => void;
  documentIdentityFields?: DocumentIdentity[];
  onDocumentIdentityFieldsChange?: (fields: DocumentIdentity[]) => void;
  documentNameRule?: DocumentNameRule | null;
  onDocumentNameRuleChange?: (rule: DocumentNameRule | null) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onBusyChange?: (busy: boolean) => void;
  onSavingChange?: (saving: boolean) => void;
  onMeaningStateChange?: (state: LayoutEditorMeaningState) => void;
  onAssistantSelectionChange?: (state: LayoutAssistantSelection) => void;
  onManualFacets?: (touches: FacetTouch[]) => void;
  draftStorageKey?: string;
  detectLabel?: string;
};
// 照合グループは画面に出さず、新規の確認欄はこの名前の1グループへまとめて保存する。
const DEFAULT_IDENTITY_GROUP_LABEL = "書類の確認";
// 書類ごとに中身が変わりやすい欄名。「おすすめ」の目安にだけ使う。
const IDENTITY_HINT_PATTERN = /氏名|名前|名称|名号|番号|No\.?|ＮＯ|ID|コード|登録|記号|年月日|日付/i;
const sameStringList = (left: readonly string[], right: readonly string[]) => (
  left.length === right.length && left.every((value, index) => value === right[index])
);
const DEFAULT_MAGNIFIER_SCALE = 2.5;
const DEFAULT_MAGNIFIER_SIZE = 220;
const HIDDEN_REVIEW_INFORMATION_MESSAGES = new Set([
  "XLSXのセル構造から自動作成した案です。区分、意味、繰り返し構造を確認してください。",
  "LibreOfficeの印刷プレビュー上の罫線へXLSXセル座標を補正しました。",
]);
export const LayoutEditor = forwardRef<LayoutEditorHandle, LayoutEditorProps>(function LayoutEditor({
  aiFocus = null,
  aiContext = null,
  aiViewRevision = 0,
  source,
  pageView,
  pageNavigation,
  initialLayout,
  targets,
  onDetect,
  onRecognizeLabels,
  onSave,
  operationEndSave = false,
  autoSaveUnchanged = false,
  saveSignal = "",
  onInteractionChange,
  reconcileSaved,
  onUndoSaved,
  canUndoSaved = false,
  allowUnchangedSave = false,
  disabled = false,
  workflowStep,
  stepNavigation,
  focusRequest = null,
  onFocusRequestResult,
  documentIdentityFields = [],
  onDocumentIdentityFieldsChange,
  documentNameRule = null,
  onDocumentNameRuleChange,
  onDirtyChange,
  onBusyChange,
  onSavingChange,
  onMeaningStateChange,
  onAssistantSelectionChange,
  onManualFacets,
  draftStorageKey,
  detectLabel = "枠を再検出",
}: LayoutEditorProps, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const canvasAnchor = useCallback(() => canvasWrapRef.current?.parentElement ?? null, []);
  const markWarningEditorRef = useRef<HTMLDivElement>(null);
  const handledFocusRequestIdRef = useRef<number | null>(null);
  const completedFocusRequestIdRef = useRef<number | null>(null);
  const magnifierRef = useRef<HTMLDivElement>(null);
  const magnifierCanvasRef = useRef<HTMLCanvasElement>(null);
  const magnifierPointerRef = useRef<{ clientX: number; clientY: number } | null>(null);
  const magnifierFrameRef = useRef<number | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragStart = useRef<Point | null>(null);
  const moveDrag = useRef<{ id: string; start: Point } | null>(null);
  const eraserPathRef = useRef<Point[] | null>(null);
  const additiveSelection = useRef(false);
  const linkLabelId = useRef<string | null>(null);
  const wirePointer = useRef<{ clientX: number; clientY: number } | null>(null);
  const wireOrigin = useRef<{ clientX: number; clientY: number } | null>(null);
  const relationHistory = useRef<Array<{ relations: LayoutRelation[];
    parents: Map<string, string | null | undefined>;
    recordStructureVerified: LayoutDefinition["recordStructureVerified"];
    allowCrossAxisLabels: LayoutDefinition["allowCrossAxisLabels"];
    sectionParents: Map<string, string | null | undefined> }>>([]);
  const copiedCellMergeRef = useRef<LayoutCellMerge | null>(null);
  const operationLockRef = useRef(false);
  const assistantPausedRef = useRef(false);
  const [assistantPaused, setAssistantPaused] = useState(false);
  const autoSaveTimerRef = useRef<number | null>(null);
  const interactionRef = useRef({ pointers: new Set<number>(), text: false, composing: false });
  const [interactionActive, setInteractionActive] = useState(false);
  const syncInteraction = useCallback(() => {
    const value = interactionRef.current;
    const active = value.pointers.size > 0 || value.text || value.composing;
    setInteractionActive(active);
    onInteractionChange?.(active);
  }, [onInteractionChange]);
  useEffect(() => {
    if (!operationEndSave) return;
    const release = (event: PointerEvent) => {
      interactionRef.current.pointers.delete(event.pointerId); syncInteraction();
    };
    const resetPointers = () => { interactionRef.current.pointers.clear(); syncInteraction(); };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("blur", resetPointers);
    return () => {
      window.removeEventListener("pointerup", release); window.removeEventListener("pointercancel", release);
      window.removeEventListener("blur", resetPointers); onInteractionChange?.(false);
    };
  }, [operationEndSave, syncInteraction, onInteractionChange]);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [assistantChoice, setAssistantChoice] = useState<{ cellId: string; markSetId: string; choiceId: string } | null>(null);
  const lastAutoSaveAttemptSignatureRef = useRef<string | null>(null);
  const [restoredLayout] = useState(() => restoreLayoutDraft(initialLayout, draftStorageKey));
  const [layout, setLayoutState] = useState(restoredLayout);
  const layoutRef = useRef(layout);
  const facetsCallbackRef = useRef(onManualFacets);
  facetsCallbackRef.current = onManualFacets;
  const setLayoutSystem = useCallback((update: React.SetStateAction<LayoutDefinition>) => {
    const next = typeof update === "function" ? update(layoutRef.current) : update;
    layoutRef.current = next; setLayoutState(next);
  }, []);
  const setLayout = useCallback((update: React.SetStateAction<LayoutDefinition>) => {
    const previous = layoutRef.current;
    const next = typeof update === "function" ? update(previous) : update;
    setLayoutSystem(next);
    facetsCallbackRef.current?.(changedFacets(previous, next));
  }, [setLayoutSystem]);
  // Restored drafts are no longer held for review, so this gate stays closed.
  const [draftReviewRequired, setDraftReviewRequired] = useState(false);
  const [baselineSignature, setBaselineSignature] = useState(
    () => layoutSignature(normalizeLayout(initialLayout)),
  );
  const [mode, setMode] = useState<Mode>("select");
  const [rectanglesHidden, setRectanglesHidden] = useState(false);
  const [wireHoverId, setWireHoverId] = useState<string | null>(null);
  useEffect(() => {
    moveDrag.current = null; setDraft(null);
    linkLabelId.current = null; setLinkPoint(null);
    wirePointer.current = null; wireOrigin.current = null; setWireHoverId(null);
  }, [mode, pageView?.page, workflowStep]);
  const [drawShape, setDrawShape] = useState<"rectangle" | "polygon">("rectangle");
  const [polygonPoints, setPolygonPoints] = useState<Point[]>([]);
  const [virtualHeadingEditor, setVirtualHeadingEditor] = useState<VirtualHeadingEditorState | null>(null);
  const virtualHeadingInputRef = useRef<HTMLInputElement>(null);
  const virtualHeadingId = useRef<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  useEffect(() => { setAssistantChoice(null); }, [selectedId, pageView?.page, pageView?.range, workflowStep]);
  const [warningFocusRequest, setWarningFocusRequest] = useState<LayoutEditorFocusRequest | null>(null);
  const [identityGroupOptions, setIdentityGroupOptions] = useState<Array<{ id: string; label: string }>>(
    () => {
      const configured = documentIdentityFields.flatMap((field) => (
        field.matchGroupId && field.matchGroupLabel
          ? [{ id: field.matchGroupId, label: field.matchGroupLabel }]
          : []
      ));
      return configured.length > 0
        ? Array.from(new Map(configured.map((group) => [group.id, group])).values())
        : [{ id: "identity_group_1", label: DEFAULT_IDENTITY_GROUP_LABEL }];
    },
  );
  const [activeIdentityGroupId] = useState(
    () => documentIdentityFields.find((field) => field.matchGroupId)?.matchGroupId
      ?? "identity_group_1",
  );
  const [selectedPath, setSelectedPath] = useState("");
  const [draft, setDraft] = useState<Rect | null>(null);
  const [eraserTrail, setEraserTrail] = useState<Point[]>([]);
  // Step 1 palette: the area painted on boxes (or applied to the selected boxes).
  const [paintRole, setPaintRole] = useState<PaintRole | null>(null);
  const [paintPreview, setPaintPreview] = useState<string[]>([]);
  const [paintHover, setPaintHover] = useState<string | null>(null);
  const paintStroke = useRef<{ ids: Set<string>; force: boolean; rectStart: Point | null; last: Point } | null>(null);
  const [linkPoint, setLinkPoint] = useState<Point | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [flushRequested, setFlushRequested] = useState(false);
  const flushRef = useRef<{ promise: Promise<boolean>; resolve: (saved: boolean) => void } | null>(null);
  const flushNeedsSaveRef = useRef(false);
  const [detecting, setDetecting] = useState(false);
  const [recognizingLabels, setRecognizingLabels] = useState(false);
  const [ocrLabelAlignment, setOcrLabelAlignment] = useState<OcrLabelAlignment>("auto");
  const [ocrLabelVerticalAlignment, setOcrLabelVerticalAlignment] = useState<OcrLabelVerticalAlignment>("auto");
  const [magnifierEnabled, setMagnifierEnabled] = useState(false);
  const [magnifierScale, setMagnifierScale] = useState(DEFAULT_MAGNIFIER_SCALE);
  const [magnifierSize, setMagnifierSize] = useState(DEFAULT_MAGNIFIER_SIZE);
  const [message, setMessage] = useState<string | null>(null);
  // A message floats over the page and fades after a while (or when closed).
  const [hiddenMessage, setHiddenMessage] = useState<string | null>(null);
  const [messageRepeat, setMessageRepeat] = useState(0);
  useEffect(() => {
    if (!message) return;
    setHiddenMessage(null);
    const timer = window.setTimeout(() => setHiddenMessage(message), 8000);
    return () => window.clearTimeout(timer);
  }, [message, messageRepeat]);
  const [highlightUnassigned, setHighlightUnassigned] = useState(false);
  const [historyDepth, setHistoryDepth] = useState(0);
  const [customLabel, setCustomLabel] = useState("");
  const [customTargetPlacement, setCustomTargetPlacement] = useState<"sidebar" | "catalog" | null>(null);
  const [meaningCatalogQuery, setMeaningCatalogQuery] = useState("");
  const [meaningCatalogSection, setMeaningCatalogSection] = useState("all");
  const [recordSectionTitle, setRecordSectionTitle] = useState("明細");
  const [selectedRecordSectionId, setSelectedRecordSectionId] = useState("");
  const currentSignature = useMemo(() => layoutSignature(layout), [layout]);
  const unassignedMeaningIds = useMemo(
    () => unassignedMeaningCellIds(layout),
    [layout],
  );
  const currentIncompleteMeaningIds = useMemo(
    () => incompleteMeaningCellIds(layout),
    [layout],
  );
  const meaningState = useMemo<LayoutEditorMeaningState>(() => ({
    cells: layout.cells.map((cell) => ({
      id: cell.id,
      role: cell.role,
      markSetIds: (cell.markSets ?? []).map((markSet) => markSet.id),
    })),
    unassignedCellIds: layout.cells
      .filter((cell) => unassignedMeaningIds.has(cell.id))
      .map((cell) => cell.id),
    incompleteMarkCellIds: layout.cells
      .filter((cell) => currentIncompleteMeaningIds.has(cell.id))
      .map((cell) => cell.id),
    usableMeaningCellCount: layout.cells.filter((cell) => (
      (cell.role === "input" || cell.role === "mark")
      && !unassignedMeaningIds.has(cell.id)
      && !currentIncompleteMeaningIds.has(cell.id)
    )).length,
  }), [currentIncompleteMeaningIds, layout.cells, unassignedMeaningIds]);
  const unassignedMeaningCount = unassignedMeaningIds.size;
  const dirty = saving || currentSignature !== baselineSignature;
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
    relationHistory.current.push({ relations: relations.map((relation) => ({
      ...relation,
      labelCellIds: [...relation.labelCellIds],
    })), parents: new Map(layout.cells.map(cell => [cell.id, cell.parentLabelCellId])),
      recordStructureVerified: layout.recordStructureVerified,
      allowCrossAxisLabels: layout.allowCrossAxisLabels,
      sectionParents: new Map((layout.recordSections ?? []).map(section => [section.id, section.parentLabelCellId])) });
    if (relationHistory.current.length > 50) relationHistory.current.shift();
    setHistoryDepth(relationHistory.current.length);
  };
  const undoMeaning = useCallback(() => {
    if (onUndoSaved) {
      if (dirtyRef.current || operationLockRef.current || interactionRef.current.text || interactionRef.current.composing) {
        setMessage("未保存の入力を先に終了してください。"); return;
      }
      operationLockRef.current = true; setSaving(true);
      void onUndoSaved().then(saved => {
        const normalized = normalizeLayout(saved);
        setLayoutSystem(normalized); setBaselineSignature(layoutSignature(normalized));
        setSaveError(null); setMessage("直前の変更を取り消しました。");
      }).catch(error => setSaveError(error instanceof Error ? error.message : "取り消し結果を確認してください。"))
        .finally(() => { operationLockRef.current = false; setSaving(false); });
      return;
    }
    const previous = relationHistory.current.pop();
    if (!previous) {
      setMessage("戻せる意味づけはありません。");
      return;
    }
    setLayout((current) => {
      const recordSections = current.recordSections?.map(section => previous.sectionParents.has(section.id)
        ? { ...section, parentLabelCellId: previous.sectionParents.get(section.id) ?? null } : section);
      return { ...current, relations: previous.relations,
      cells: current.cells.map(cell => previous.parents.has(cell.id)
        ? { ...cell, parentLabelCellId: previous.parents.get(cell.id) } : cell),
      recordSections, recordStructureVerified: previous.recordStructureVerified,
      allowCrossAxisLabels: previous.allowCrossAxisLabels,
    }; });
    setHistoryDepth(relationHistory.current.length);
    setMessage("直前の意味づけを元に戻しました。");
  }, [onUndoSaved]);

  useEffect(() => {
    const normalized = normalizeLayout(initialLayout);
    if (dirtyRef.current || operationLockRef.current) return;
    setLayoutSystem(normalized);
    setBaselineSignature(layoutSignature(normalized));
    setSelectedRecordSectionId(normalized.recordSections?.[0]?.id ?? "");
    const savedIds = new Set(normalized.cells.map(cell => cell.id));
    // Refreshing an owned save must not become a new user selection/fence.
    const keepSelection = operationEndSave || assistantPausedRef.current;
    setSelectedId(current => keepSelection && current && savedIds.has(current) ? current : null);
    setSelectedIds(current => keepSelection ? new Set([...current].filter(id => savedIds.has(id))) : new Set());
    relationHistory.current = [];
    setHistoryDepth(0);
  }, [initialLayout, operationEndSave]);
  useEffect(() => {
    dirtyRef.current = dirty;
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!draftStorageKey) return;
    try {
      if (dirty) {
        // Remove obsolete provenance first: partial writes must never make a
        // draft look as if it were based on a newer server response.
        if (!draftReviewRequired || assistantPausedRef.current) privateLocalStorage.removeItem(`${draftStorageKey}:baseline`);
        privateLocalStorage.setItem(draftStorageKey, currentSignature);
        // An unconfirmed AI draft can be restored in another tab without its
        // session journal. It must not carry permission to resume auto-save.
        if (!draftReviewRequired && !assistantPausedRef.current) privateLocalStorage.setItem(`${draftStorageKey}:baseline`, baselineSignature);
      } else {
        privateLocalStorage.removeItem(draftStorageKey);
        privateLocalStorage.removeItem(`${draftStorageKey}:baseline`);
      }
    } catch {
      // The API save below remains authoritative when browser storage is unavailable.
    }
  }, [currentSignature, dirty, draftStorageKey, baselineSignature, draftReviewRequired]);
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
  useEffect(() => {
    onSavingChange?.(saving);
  }, [saving, onSavingChange]);
  useEffect(() => {
    onMeaningStateChange?.(meaningState);
  }, [meaningState, onMeaningStateChange]);
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
  const paletteKeyRef = useRef<(event: KeyboardEvent) => void>(() => undefined);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => paletteKeyRef.current(event);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (mode === "paint") return;
    paintStroke.current = null; setPaintPreview([]); setPaintHover(null);
  }, [mode]);
  useEffect(() => {
    setRectanglesHidden(false);
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
  useEffect(() => {
    if (workflowStep !== "meaning") {
      setHighlightUnassigned(false);
      return;
    }
    if (highlightUnassigned && unassignedMeaningCount === 0) {
      setHighlightUnassigned(false);
      setMessage("読み取る内容が未設定の記入欄はありません。");
    }
  }, [highlightUnassigned, unassignedMeaningCount, workflowStep]);
  const displayCells = useMemo(() => pageDisplayCells(layout, pageView), [layout, pageView]);
  const displayCellById = useMemo(() => new Map(displayCells.map(cell => [cell.id, cell])), [displayCells]);
  const displayWidth = pageView?.width ?? layout.canonicalWidth;
  const displayHeight = pageView?.height ?? layout.canonicalHeight;
  const displaySource = pageView?.imageUrl ?? source;
  const wireDragging = mode === "link" && linkPoint !== null;
  useEffect(() => {
    if (!wireDragging) return;
    let frame = 0;
    let lastTime = performance.now();
    const cancel = () => {
      wirePointer.current = null; wireOrigin.current = null;
      linkLabelId.current = null; setLinkPoint(null); setWireHoverId(null);
    };
    const tick = (now: number) => {
      const pointer = wirePointer.current;
      const canvas = canvasRef.current;
      const wrapper = canvasWrapRef.current;
      if (!pointer || !canvas || !wrapper) return;
      const elapsed = Math.min(32, now - lastTime); lastTime = now;
      const area = wrapper.getBoundingClientRect();
      const top = Math.max(area.top, 0), bottom = Math.min(area.bottom, window.innerHeight);
      const edgeSpeed = (position: number, first: number, last: number) =>
        position < first + 32 ? -Math.min(1, (first + 32 - position) / 32)
          : position > last - 32 ? Math.min(1, (position - last + 32) / 32) : 0;
      if (pointer.clientX >= area.left && pointer.clientX <= area.right
        && pointer.clientY >= top && pointer.clientY <= bottom) {
        wrapper.scrollTop += edgeSpeed(pointer.clientY, top, bottom) * elapsed * 0.5;
        wrapper.scrollLeft += edgeSpeed(pointer.clientX, area.left, area.right) * elapsed * 0.5;
      }
      const bounds = canvas.getBoundingClientRect();
      const location = {
        x: Math.round(Math.max(0, Math.min(displayWidth, (pointer.clientX - bounds.left) * displayWidth / bounds.width))),
        y: Math.round(Math.max(0, Math.min(displayHeight, (pointer.clientY - bounds.top) * displayHeight / bounds.height))),
      };
      setLinkPoint(current => current?.x === location.x && current?.y === location.y ? current : location);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    window.addEventListener("blur", cancel);
    return () => { cancelAnimationFrame(frame); window.removeEventListener("blur", cancel); };
  }, [wireDragging, displayWidth, displayHeight]);
  useEffect(() => {
    if (!virtualHeadingEditor) return;
    virtualHeadingInputRef.current?.focus();
    if (virtualHeadingEditor.id) virtualHeadingInputRef.current?.select();
  }, [
    virtualHeadingEditor?.id,
    virtualHeadingEditor?.rect.x,
    virtualHeadingEditor?.rect.y,
  ]);
  useEffect(() => {
    setSelectedId(null); setSelectedIds(new Set());
    dragStart.current = null; linkLabelId.current = null; eraserPathRef.current = null;
    setDraft(null); setLinkPoint(null); setEraserTrail([]);
    setVirtualHeadingEditor(null); virtualHeadingId.current = null;
    setMode(current => current === "virtual-group" ? "select" : current);
    setMessage(null);
  }, [pageView?.page, pageView?.range]);
  useEffect(() => {
    if (!focusRequest || handledFocusRequestIdRef.current === focusRequest.requestId) return;
    handledFocusRequestIdRef.current = focusRequest.requestId;
    const target = layout.cells.find((cell) => cell.id === focusRequest.cellId);
    const targetRoleMatches = focusRequest.purpose === "meaning-cell"
      ? target?.role === "input" || target?.role === "mark"
      : target?.role === "mark";
    const canFocus = workflowStep === "meaning"
      && !disabled
      && !busy
      && !saving
      && targetRoleMatches
      && (focusRequest.purpose !== "mark-options"
        || Boolean(target?.markSets?.some((markSet) => markSet.id === focusRequest.markSetId)))
      && displayCellById.has(focusRequest.cellId);
    if (!canFocus) {
      setWarningFocusRequest(null);
      onFocusRequestResult?.({ requestId: focusRequest.requestId, focused: false });
      return;
    }
    setSelectedId(focusRequest.cellId);
    setSelectedIds(new Set([focusRequest.cellId]));
    setWarningFocusRequest(focusRequest);
  }, [busy, disabled, displayCellById, focusRequest, layout.cells, onFocusRequestResult, saving, workflowStep]);
  useEffect(() => {
    if (!warningFocusRequest) return;
    if (
      workflowStep !== "meaning"
      || selectedId !== warningFocusRequest.cellId
      || selectedIds.size !== 1
      || !selectedIds.has(warningFocusRequest.cellId)
    ) {
      setWarningFocusRequest(null);
    }
  }, [selectedId, selectedIds, warningFocusRequest, workflowStep]);
  useEffect(() => {
    if (
      !warningFocusRequest
      || completedFocusRequestIdRef.current === warningFocusRequest.requestId
      || selectedId !== warningFocusRequest.cellId
    ) return;
    const target = displayCellById.get(warningFocusRequest.cellId);
    const canvas = canvasRef.current;
    const canvasWrap = canvasWrapRef.current;
    const focusTarget = warningFocusRequest.purpose === "mark-options" ? markWarningEditorRef.current : canvas;
    if (!target || !canvas || !canvasWrap || !focusTarget) return;
    completedFocusRequestIdRef.current = warningFocusRequest.requestId;
    const behavior: ScrollBehavior = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
    const scaleX = (canvas.clientWidth || canvas.width) / Math.max(1, displayWidth);
    const scaleY = (canvas.clientHeight || canvas.height) / Math.max(1, displayHeight);
    canvasWrap.scrollTo?.({
      left: Math.max(0, (target.x + target.width / 2) * scaleX - canvasWrap.clientWidth / 2),
      top: Math.max(0, (target.y + target.height / 2) * scaleY - canvasWrap.clientHeight / 2),
      behavior,
    });
    focusTarget.focus({ preventScroll: true });
    (warningFocusRequest.purpose === "mark-options" ? focusTarget : canvasWrap)
      .scrollIntoView?.({ behavior, block: "center", inline: "nearest" });
    onFocusRequestResult?.({ requestId: warningFocusRequest.requestId, focused: true });
  }, [displayCellById, displayHeight, displayWidth, onFocusRequestResult, selectedId, warningFocusRequest]);
  const selected = layout.cells.find((cell) => cell.id === selectedId) ?? null;
  const assistantSelectedIds = useMemo(() => [...new Set([...selectedIds, ...(selectedId ? [selectedId] : [])])].sort(), [selectedIds, selectedId]);
  const assistantInteraction = useInteractionContext(
    JSON.stringify([assistantSelectedIds, assistantChoice, pageView?.page, pageView?.range, workflowStep, mode]),
    JSON.stringify([currentSignature, documentIdentityFields, documentNameRule]),
  );
  const assistantSelection = useMemo<LayoutAssistantSelection>(() => {
    const cell = assistantSelectedIds.length === 1 && selected && displayCellById.has(selected.id) ? selected : null;
    const set = cell && assistantChoice?.cellId === cell.id ? cell.markSets?.find(item => item.id === assistantChoice.markSetId) : undefined;
    const option = set?.options.find(item => item.id === assistantChoice?.choiceId);
    const displayCell = cell ? displayCellById.get(cell.id) : null;
    return {
      selectedIds: assistantSelectedIds, cell,
      choice: set && option ? { markSetId: set.id, choiceId: option.id, label: option.label, value: option.value, tracksLabel: set.recognitionMode === "symbol" || set.recognitionMode === "checklist" } : null,
      selectionEpoch: assistantInteraction.selectionEpoch,
      localEditEpoch: assistantInteraction.localEditEpoch,
      snapshotKey: baselineSignature,
      pageId: pageView ? `${pageView.page}:${pageView.range}` : null,
      categoryChangeBlocked: Boolean(cell && (cell.virtual || cell.readingMode === "number" || cell.markSets?.length || cell.compositeSubfields?.length
        || layout.relations.some(relation => relation.inputCellId === cell.id || relation.labelCellIds.includes(cell.id) || relation.parentLabelCellId === cell.id)
        || layout.cells.some(other => other.parentLabelCellId === cell.id)
        || layout.recordGroups?.some(group => group.inputCellIds.includes(cell.id) || group.recordKeyCellId === cell.id)
        || layout.recordSections?.some(section => section.recordLabelCellIds.includes(cell.id) || section.recordLabelCellId === cell.id || section.rowKeyCellIds?.includes(cell.id) || section.parentLabelCellId === cell.id))),
      image: displayCell ? { kind: "region", url: pageView?.imageUrl ?? source, x: displayCell.x, y: displayCell.y, width: displayCell.width, height: displayCell.height, imageWidth: displayWidth, imageHeight: displayHeight } : null,
      draftReviewRequired, saveError,
    };
  }, [assistantSelectedIds, selected, displayCellById, assistantChoice, assistantInteraction.selectionEpoch, assistantInteraction.localEditEpoch, baselineSignature, pageView, layout.relations, layout.cells, layout.recordGroups, layout.recordSections, source, displayWidth, displayHeight, draftReviewRequired, saveError]);
  const lastAssistantSelectionRef = useRef("");
  useLayoutEffect(() => {
    const signature = JSON.stringify(assistantSelection);
    if (lastAssistantSelectionRef.current === signature) return;
    lastAssistantSelectionRef.current = signature;
    onAssistantSelectionChange?.(assistantSelection);
  }, [assistantSelection, onAssistantSelectionChange]);
  const warningFocusDisplayCell = warningFocusRequest
    ? displayCellById.get(warningFocusRequest.cellId) ?? null
    : null;
  const selectedRelationPath = layout.relations.find(item => item.inputCellId === selectedId)?.targetPath ?? "";
  useEffect(() => {
    if (mode === "link") setSelectedPath("");
  }, [mode]);
  useEffect(() => {
    // A refreshed relations array is not a new selection. In particular, a save
    // response must not erase the user's target for the next drag connection.
    if (mode === "link") return;
    if (!selected || selected.role !== "input") return;
    setSelectedPath(selectedRelationPath);
  }, [mode, selectedId, selected?.role, selectedRelationPath]);
  const selectedCells = layout.cells.filter((cell) => selectedIds.has(cell.id));
  const cellMerges = layout.cellMerges ?? [];
  const cellMergeByParentId = useMemo(
    () => new Map(cellMerges.map((merge) => [merge.parentCellId, merge])),
    [cellMerges],
  );
  const selectedCellMerge = selected
    ? cellMergeByParentId.get(selected.id) ?? null
    : null;
  const recordSections = layout.recordSections ?? [];
  const recordGroups = layout.recordGroups ?? [];
  const ocrProtectedLabelIds = useMemo(() => {
    const protectedIds = new Set(
      layout.relations.flatMap((relation) => [
        ...relation.labelCellIds,
        ...(relation.parentLabelCellId ? [relation.parentLabelCellId] : []),
      ]),
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
        ? { ...existing, label: target.path.startsWith("fields.") ? entry.label : existing.label,
          aliases: [...new Set([...(existing.aliases ?? []), ...entry.aliases])] }
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
  // A lone checkbox with a storage target has nothing to set beside the line drawn to it.
  const selectedChecklist = selected?.role === "mark" && selected.markSets?.length === 1
    && selected.markSets[0].recognitionMode === "checklist" ? selected.markSets[0] : null;
  const singleCheckbox = Boolean(selectedChecklist?.targetPath) && (selectedChecklist?.options.length ?? 0) <= 1;
  const cellNameById = (id: string) => {
    const cell = cellById.get(id);
    return cell ? cellName(cell) : "削除された欄";
  };
  const selectedMeaningRelation = selected
    ? layout.relations.find((relation) => relation.inputCellId === selected.id)
    : undefined;
  const selectedMeaningParent = cellById.get(selectedMeaningRelation?.parentLabelCellId ?? "");
  const selectedMeaningChild = selectedMeaningRelation?.labelCellIds
    .map((labelId) => cellById.get(labelId))
    .find((cell): cell is LayoutCell => Boolean(cell));
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
    ) return [];
    return [{
      cell,
      targetPath: relation.targetPath,
      label: cell.fieldLabel || target?.label || cell.printedLabelText?.trim() || relation.targetPath,
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
  const primaryIdentityCount = documentIdentityFields.filter(
    (field) => field.matchRole === "primary",
  ).length;
  const usesCombinedConfirmation = documentIdentityFields.length >= 2
    && primaryIdentityCount === 0;
  const usesGroupedIdentity = documentIdentityFields.length > 0
    && documentIdentityFields.every((field) => Boolean(field.matchGroupId));
  const identityConfigurationReady = usesGroupedIdentity
    ? documentIdentityFields.every((field) => Boolean(field.matchGroupLabel))
    : documentIdentityFields.length > 0
      && (primaryIdentityCount === 1 || usesCombinedConfirmation)
      && documentIdentityFields.filter((field) => field.displayRole === "title").length === 1
      && documentIdentityFields.filter((field) => field.displayRole === "subtitle").length <= 1;
  const documentNameReady = Boolean(documentNameRule?.fieldPaths.length)
    || workflowStep === undefined;
  const normalizedIdentityFields = (fields: DocumentIdentity[]) => {
    if (fields.length === 0) return [];
    if (fields.some((field) => field.matchGroupId)) {
      const orderByGroup = new Map<string, number>();
      return fields.map((field) => {
        const groupId = field.matchGroupId ?? activeIdentityGroupId;
        const componentOrder = orderByGroup.get(groupId) ?? 0;
        orderByGroup.set(groupId, componentOrder + 1);
        const group = identityGroupOptions.find((item) => item.id === groupId);
        return {
          ...field,
          matchRole: "confirmation" as const,
          displayRole: "none" as const,
          matchGroupId: groupId,
          matchGroupLabel: field.matchGroupLabel ?? group?.label ?? DEFAULT_IDENTITY_GROUP_LABEL,
          componentOrder,
        };
      });
    }
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
  const recommendedIdentityCandidates = identityCandidates.filter(
    (candidate) => IDENTITY_HINT_PATTERN.test(candidate.label) && !/ふりがな|フリガナ|郵便/.test(candidate.label),
  ).slice(0, 3);
  const recommendedIdentityPaths = new Set(recommendedIdentityCandidates.map((candidate) => candidate.targetPath));
  const identityFieldFor = (candidate: (typeof identityCandidates)[number]): DocumentIdentity => ({
    mappingId: `layout_${candidate.cell.id}`,
    targetPath: candidate.targetPath,
    label: candidate.label,
    valueType: "text",
    exactLength: null,
    matchRole: "confirmation",
    displayRole: "none",
    matchGroupId: activeIdentityGroupId,
    matchGroupLabel: identityGroupOptions.find((group) => group.id === activeIdentityGroupId)?.label
      || DEFAULT_IDENTITY_GROUP_LABEL,
    componentOrder: 0,
  });
  // 確認する欄の並びから作るファイル名の提案。利用者が欄を手で変えていなければ選択に追従させる。
  const suggestedDocumentNameRule: DocumentNameRule | null = documentIdentityFields.length > 0
    ? { fieldPaths: documentIdentityFields.map((field) => field.targetPath), separator: "_", prefix: "", suffix: "" }
    : null;
  const commitIdentityFields = (next: DocumentIdentity[]) => {
    const normalized = normalizedIdentityFields(next);
    onDocumentIdentityFieldsChange?.(normalized);
    const followsSelection = !documentNameRule
      || sameStringList(documentNameRule.fieldPaths, documentIdentityFields.map((field) => field.targetPath));
    if (followsSelection) {
      const fieldPaths = normalized.map((field) => field.targetPath);
      onDocumentNameRuleChange?.(fieldPaths.length > 0
        ? { ...(documentNameRule ?? { prefix: "", suffix: "", separator: "_" }), fieldPaths }
        : null);
    }
    return normalized;
  };
  const selectRecommendedIdentityCandidates = () => {
    const additions = recommendedIdentityCandidates
      .filter((candidate) => !selectedIdentityByPath.has(candidate.targetPath))
      .map(identityFieldFor);
    if (additions.length === 0) return;
    commitIdentityFields([...documentIdentityFields, ...additions]);
    setMessage(`おすすめの欄（${additions.map((field) => field.label).join("・")}）を確認する欄に追加しました。`);
  };
  const toggleIdentityCandidate = (candidate: (typeof identityCandidates)[number]) => {
    const existing = selectedIdentityByPath.get(candidate.targetPath);
    const next = existing
      ? documentIdentityFields.filter((field) => field.targetPath !== candidate.targetPath)
      : [...documentIdentityFields, identityFieldFor(candidate)];
    const normalized = commitIdentityFields(next);
    setSelectedId(candidate.cell.id);
    setSelectedIds(new Set(normalized.flatMap((field) => {
      const selectedCandidate = identityCandidates.find((item) => item.targetPath === field.targetPath);
      return selectedCandidate ? [selectedCandidate.cell.id] : [];
    })));
    setMessage(existing
      ? `「${candidate.label}」を確認する欄から外しました。`
      : `「${candidate.label}」を確認する欄に追加しました。`);
  };
  const toggleDocumentNamePath = (targetPath: string) => {
    const current = documentNameRule ?? { fieldPaths: [], separator: "", prefix: "", suffix: "" };
    const fieldPaths = current.fieldPaths.includes(targetPath)
      ? current.fieldPaths.filter((path) => path !== targetPath)
      : [...current.fieldPaths, targetPath];
    onDocumentNameRuleChange?.(fieldPaths.length > 0 ? { ...current, fieldPaths } : null);
  };
  const updateDocumentNameRule = (patch: Partial<DocumentNameRule>) => {
    const current = documentNameRule ?? { fieldPaths: [], separator: "", prefix: "", suffix: "" };
    onDocumentNameRuleChange?.({ ...current, ...patch });
  };
  useEffect(() => {
    const configured = documentIdentityFields.flatMap((field) => (
      field.matchGroupId && field.matchGroupLabel
        ? [{ id: field.matchGroupId, label: field.matchGroupLabel }]
        : []
    ));
    if (configured.length === 0) return;
    setIdentityGroupOptions((current) => Array.from(new Map(
      [...current, ...configured].map((group) => [group.id, group]),
    ).values()));
  }, [documentIdentityFields]);
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
  // Step 2 keeps the step 1 area buttons for the selected box (select mode only).
  const roleEditable = showClassificationStep || (workflowStep === "meaning" && mode === "select");
  const showMeaningStep = !workflowStep || workflowStep === "meaning";
  const showRecordStep = !workflowStep || workflowStep === "records";
  const showIdentityStep = workflowStep === "identity";
  // Without the wizard everything is shown; in it the last step carries the final checks.
  const showFinalChecks = !workflowStep || workflowStep === "identity";
  const showDetectAction = !pageView && (showClassificationStep
    || (showMeaningStep && detectLabel !== "枠を再検出"));
  const cellsVisibleInList = displayCells.filter((cell) => {
    if (!workflowStep || workflowStep === "classify") return true;
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
        `保存していないレイアウト変更があります。変更を破棄して「${detectLabel}」を実行しますか？`,
      )
    ) return;
    operationLockRef.current = true;
    setBusy(true);
    setDetecting(true);
    setMessage(detectLabel === "枠を再検出"
      ? "帳票の枠を再検出しています…"
      : "原本を変更せず、XLSXのセル構造を復元しています…");
    try {
      const detected = await onDetect();
      const normalized = normalizeLayout(detected);
      setLayoutSystem(normalized);
      setSelectedRecordSectionId(normalized.recordSections?.[0]?.id ?? "");
      setSelectedId(null);
      setSelectedIds(new Set());
      relationHistory.current = [];
      setHistoryDepth(0);
      setMessage(detectLabel === "枠を再検出"
        ? `${detected.cells.length}個の枠を検出しました。種類と項目名の文字候補を確認してください。`
        : `${detected.cells.length}個のExcelセルを復元しました。原本XLSXは変更していません。`);
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
    if (automatic && assistantPausedRef.current) return false;
    if (draftReviewRequired) return false;
    if (disabled || operationLockRef.current) return false;
    if (operationEndSave && (interactionRef.current.pointers.size || interactionRef.current.text || interactionRef.current.composing || polygonPoints.length || virtualHeadingEditor)) return false;
    if (!dirty && !allowUnchangedSave) {
      return !dirty;
    }
    operationLockRef.current = true;
    setSaveError(null);
    setSaving(true);
    lastAutoSaveAttemptSignatureRef.current = currentSignature + saveSignal;
    // Authoring can continue during either an automatic or a navigation save.
    // Identity/name configuration still has an explicit completion barrier.
    if (!automatic && !operationEndSave && !["classify", "meaning", "records"].includes(workflowStep ?? "")) setBusy(true);
    try {
      const saved = normalizeLayout(await onSave(layout));
      const submittedSignature = layoutSignature(layout);
      setLayoutSystem(current => layoutSignature(current) === submittedSignature ? saved
        : reconcileSaved ? reconcileSaved(current, layout, saved) : current);
      setBaselineSignature(layoutSignature(saved));
      // An automatic save leaves the current notice (e.g. what was just painted) in place.
      if (!automatic) setMessage(`テンプレートへ${saved.cells.length}個の矩形を保存しました。`);
      // The draft effect clears browser storage only when the current edit,
      // rather than merely this request's snapshot, has been persisted.
      return true;
    } catch (error) {
      // A failed response can follow a successful layout write (for example,
      // mapping refresh failed). Even an edit back to the old baseline is
      // unconfirmed and must remain recoverable until a complete save succeeds.
      setBaselineSignature("");
      setSaveError(error instanceof Error ? error.message : "レイアウトを保存できませんでした。");
      setMessage(error instanceof Error ? error.message : "レイアウトを保存できませんでした。");
      flushRef.current?.resolve(false);
      flushRef.current = null;
      setFlushRequested(false);
      return false;
    } finally {
      setSaving(false);
      setBusy(false);
      operationLockRef.current = false;
    }
  }, [allowUnchangedSave, disabled, dirty, currentSignature, layout, onSave, workflowStep, draftReviewRequired, operationEndSave, saveSignal, reconcileSaved, polygonPoints.length, virtualHeadingEditor]);

  const finishFlush = useCallback((saved: boolean) => {
    flushRef.current?.resolve(saved);
    flushRef.current = null;
    setFlushRequested(false);
  }, []);
  const flushLatest = useCallback((): Promise<boolean> => {
    if (draftReviewRequired) return Promise.resolve(false);
    if (flushRef.current) return flushRef.current.promise;
    if (disabled || (operationLockRef.current && !saving)) return Promise.resolve(false);
    let resolve!: (saved: boolean) => void;
    const promise = new Promise<boolean>(done => { resolve = done; });
    flushRef.current = { promise, resolve };
    flushNeedsSaveRef.current = !saving && allowUnchangedSave;
    setFlushRequested(true);
    return promise;
  }, [allowUnchangedSave, disabled, saving, draftReviewRequired]);
  const beginAssistant = useCallback(() => {
    assistantPausedRef.current = true;
    if (autoSaveTimerRef.current !== null) window.clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = null;
    setAssistantPaused(true);
  }, []);
  const endAssistant = useCallback(() => {
    assistantPausedRef.current = false;
    setAssistantPaused(false);
  }, []);
  const discardUnsaved = useCallback(() => {
    if (disabled || operationLockRef.current || saveError || flushRequested) return false;
    const normalized = normalizeLayout(initialLayout);
    setLayoutSystem(normalized);
    setBaselineSignature(layoutSignature(normalized));
    setDraftReviewRequired(false);
    relationHistory.current = [];
    setHistoryDepth(0);
    lastAutoSaveAttemptSignatureRef.current = null;
    setMessage("未保存のレイアウト変更を破棄しました。");
    return true;
  }, [disabled, saveError, flushRequested, initialLayout]);
  const acceptAssistantSaved = useCallback((saved: LayoutDefinition, discardDraft: boolean) => {
    if (operationLockRef.current || flushRequested || (dirtyRef.current && !discardDraft)) return false;
    const normalized = normalizeLayout(saved);
    dirtyRef.current = false;
    setLayoutSystem(normalized);
    setBaselineSignature(layoutSignature(normalized));
    setDraftReviewRequired(false);
    setSaveError(null);
    relationHistory.current = [];
    setHistoryDepth(0);
    lastAutoSaveAttemptSignatureRef.current = null;
    const ids = new Set(normalized.cells.map(cell => cell.id));
    setSelectedId(current => current && ids.has(current) ? current : null);
    setSelectedIds(current => new Set([...current].filter(id => ids.has(id))));
    return true;
  }, [flushRequested]);
  const acceptOwnSaved = useCallback((submitted: LayoutDefinition, saved: LayoutDefinition) => {
    const normalized = normalizeLayout(saved);
    setLayoutSystem(current => {
      const updated = reconcileSaved ? reconcileSaved(current, submitted, normalized)
        : layoutSignature(current) === layoutSignature(submitted) ? normalized : current;
      // Receipt recovery refreshes the saved view. Retained later input waits
      // for the next explicit editing gesture rather than an automatic resend.
      lastAutoSaveAttemptSignatureRef.current = layoutSignature(updated) + saveSignal;
      return updated;
    });
    setBaselineSignature(layoutSignature(normalized)); setSaveError(null); setDraftReviewRequired(false);
  }, [reconcileSaved, saveSignal]);
  // External state is reconciled only by the explicit review action in the
  // parent. Never call this alias from automatic response/context refreshes.
  const readAnalysisDraft = useCallback(() => {
    if (operationLockRef.current || draftReviewRequired || interactionRef.current.pointers.size || interactionRef.current.text || interactionRef.current.composing || polygonPoints.length || virtualHeadingEditor) return null;
    return structuredClone(layoutRef.current);
  }, [draftReviewRequired, polygonPoints.length, virtualHeadingEditor]);
  const matchesAnalysisDraft = useCallback((input: LayoutDefinition) => Boolean(readAnalysisDraft()) && layoutSignature(normalizeLayout(layoutRef.current)) === layoutSignature(normalizeLayout(input)), [readAnalysisDraft]);
  const applyAnalysisDraft = useCallback((input: LayoutDefinition, merged: LayoutDefinition, action: "apply" | "undo" = "apply") => {
    // Newly drawn cells acquire display defaults only when normalized. Compare
    // both sides in the same form without changing the live manual draft.
    if (!matchesAnalysisDraft(input)) return false;
    beginAssistant(); setLayoutSystem(normalizeLayout(merged));
    setMessage(action === "undo" ? "AI反映を戻しました。まだ保存していません。" : "AI案を下書きに反映しました。確認して保存してください。");
    return true;
  }, [matchesAnalysisDraft, beginAssistant, setLayoutSystem]);
  const readAnalysisPriority = useCallback(() => {
    const selected = layoutRef.current.cells.filter(c => selectedIds.has(c.id) || c.id === selectedId);
    if (selected.length) {
      const x = Math.min(...selected.map(c => c.x)), y = Math.min(...selected.map(c => c.y));
      return [x, y, Math.max(...selected.map(c => c.x + c.width)) - x, Math.max(...selected.map(c => c.y + c.height)) - y].map(Math.round);
    }
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    let left = Math.max(0, rect.left), top = Math.max(0, rect.top), right = Math.min(innerWidth, rect.right), bottom = Math.min(innerHeight, rect.bottom);
    for (let parent = canvas.parentElement; parent; parent = parent.parentElement) {
      if (!/(auto|scroll|hidden)/.test(getComputedStyle(parent).overflow + getComputedStyle(parent).overflowY + getComputedStyle(parent).overflowX)) continue;
      const clip = parent.getBoundingClientRect();
      left = Math.max(left, clip.left); top = Math.max(top, clip.top); right = Math.min(right, clip.right); bottom = Math.min(bottom, clip.bottom);
    }
    if (right <= left || bottom <= top) return null;
    const sx = layoutRef.current.canonicalWidth / rect.width, sy = layoutRef.current.canonicalHeight / rect.height;
    return [(left - rect.left) * sx, (top - rect.top) * sy, (right - left) * sx, (bottom - top) * sy].map(Math.round);
  }, [selectedIds, selectedId]);
  useImperativeHandle(ref, () => ({ readAnalysisDraft, readAnalysisPriority, matchesAnalysisDraft, applyAnalysisDraft, saveIfNeeded: flushLatest, beginAssistant, endAssistant, discardUnsaved, acceptAssistantSaved, acceptOwnSaved, acceptReviewedSaved: acceptOwnSaved }), [readAnalysisDraft, readAnalysisPriority, matchesAnalysisDraft, applyAnalysisDraft, flushLatest, beginAssistant, endAssistant, discardUnsaved, acceptAssistantSaved, acceptOwnSaved]);
  useEffect(() => {
    if (!flushRequested || saving || busy) return;
    if (operationEndSave && interactionActive) return;
    if (disabled) { finishFlush(false); return; }
    if (!dirty && !flushNeedsSaveRef.current) { finishFlush(true); return; }
    flushNeedsSaveRef.current = false;
    // Start the next write only after React commits the response and latest
    // edits/parent props (including the updated XLSX page revision).
    void save(false).then(saved => { if (!saved) finishFlush(false); });
  }, [flushRequested, saving, busy, disabled, dirty, save, finishFlush, operationEndSave, interactionActive]);
  useEffect(() => () => {
    flushRef.current?.resolve(false);
    flushRef.current = null;
  }, []);

  useEffect(() => {
    if (
      (!dirty && !(operationEndSave && autoSaveUnchanged))
      || assistantPaused
      || draftReviewRequired
      || disabled
      || busy
      || saving
      || flushRequested
      || !workflowStep
      || (operationEndSave && (interactionActive || polygonPoints.length > 0 || virtualHeadingEditor))
      || lastAutoSaveAttemptSignatureRef.current === currentSignature + saveSignal
    ) return;
    const timer = window.setTimeout(() => {
      autoSaveTimerRef.current = null;
      if (assistantPausedRef.current) return;
      lastAutoSaveAttemptSignatureRef.current = currentSignature + saveSignal;
      void save(true);
    }, operationEndSave ? 0 : 1200);
    autoSaveTimerRef.current = timer;
    return () => { window.clearTimeout(timer); if (autoSaveTimerRef.current === timer) autoSaveTimerRef.current = null; };
  }, [assistantPaused, busy, saving, flushRequested, currentSignature, dirty, disabled, save, workflowStep, draftReviewRequired, operationEndSave, autoSaveUnchanged, interactionActive, saveSignal, polygonPoints.length, virtualHeadingEditor]);

  const selectionPulseRef = useRef(0.09);
  const hoveredMeaningCellIdRef = useRef<string | null>(null);
  const hoveredHeadingKeysRef = useRef(new Set<string>());
  const headingConnections = useMemo(() => buildHeadingConnections(layout, displayCellById), [layout, displayCellById]);
  const redraw = () => {
    const canvas = canvasRef.current;
    const image = imageRef.current;
    if (!canvas || !image) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    if (rectanglesHidden) return;


    if (mode === "identity") {
      displayCells.forEach((storedCell) => {
        const cell = displayCellById.get(storedCell.id) ?? storedCell;
        const candidate = identityCandidateByCellId.get(storedCell.id);
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

        const fontSize = Math.max(11, Math.min(22, displayWidth / 75));
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

    displayCells.forEach((storedCell) => {
      const cell = displayCellById.get(storedCell.id) ?? storedCell;
      const cellMerge = cellMergeByParentId.get(cell.id);
      const roleStyle = LAYOUT_ROLE_STYLE[cell.role];
      context.save();
      const isPrimarySelection = cell.id === selectedId;
      const isRangeSelection = selectedIds.has(cell.id);
      // 選択中の枠は、角丸・細い縁・やわらかい光彩と薄い塗りで見せる
      context.strokeStyle = isPrimarySelection ? "#f5a800" : isRangeSelection ? "#2468a2" : roleStyle.stroke;
      context.lineWidth = isPrimarySelection ? 3 : isRangeSelection ? 5 : 3;
      context.setLineDash(isPrimarySelection || isRangeSelection ? [] : [...roleStyle.dash]);
      const radius = Math.min(6, cell.width / 4, cell.height / 4);
      if (isPrimarySelection) {
        context.shadowColor = "rgba(245, 168, 0, 0.55)";
        context.shadowBlur = 14;
      }
      if (isPrimarySelection || isRangeSelection) {
        context.fillStyle = isPrimarySelection ? `rgba(255, 214, 10, ${selectionPulseRef.current.toFixed(3)})` : "#2468a21a";
        if (cell.shapeType !== "polygon") {
          context.beginPath();
          context.roundRect(cell.x, cell.y, cell.width, cell.height, radius);
          context.fill();
        }
      }
      if (cell.shapeType === "polygon" && cell.vertices?.length) {
        context.beginPath();
        cell.vertices.forEach((vertex, index) => index === 0
          ? context.moveTo(vertex.x, vertex.y) : context.lineTo(vertex.x, vertex.y));
        context.closePath();
        if (isPrimarySelection || isRangeSelection) context.fill();
        context.stroke();
      } else if (isPrimarySelection) {
        context.beginPath();
        context.roundRect(cell.x, cell.y, cell.width, cell.height, radius);
        context.stroke();
      } else {
        context.strokeRect(cell.x, cell.y, cell.width, cell.height);
      }
      context.shadowColor = "transparent";
      context.shadowBlur = 0;
      context.setLineDash([]);
      if (cellMerge) {
        context.save();
        if (cell.width >= 38 && cell.height >= 20) {
          const badgeWidth = Math.min(54, Math.max(34, cell.width - 8));
          const badgeHeight = Math.min(19, Math.max(14, cell.height - 8));
          const centerX = cell.x + cell.width / 2, centerY = cell.y + cell.height / 2;
          context.fillStyle = "rgba(24, 61, 87, 0.9)";
          context.fillRect(centerX - badgeWidth / 2, centerY - badgeHeight / 2, badgeWidth, badgeHeight);
          context.fillStyle = "#ffffff";
          context.font = `800 ${Math.max(9, Math.min(12, badgeHeight - 6))}px sans-serif`;
          context.textAlign = "center";
          context.textBaseline = "middle";
          context.fillText("結合", centerX, centerY);
        }
        context.restore();
      }
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
      if ((mode !== "link" || cell.virtual) && virtualHeadingEditor?.id !== cell.id) {
        drawOcrLabelText(context, cell.virtual ? { ...cell, text: `◇ ${cell.text}` } : cell,
          displayWidth, ocrLabelAlignment, ocrLabelVerticalAlignment);
      }
      drawCompositeSubfields(context, cell, displayWidth);
      context.restore();
    });

    headingConnections.forEach(connection => {
      if (!hoveredHeadingKeysRef.current.has(connection.key)) return;
      context.save();
      context.strokeStyle = connection.color;
      context.lineWidth = Math.max(3, Math.min(5, displayWidth / 420));
      context.lineCap = "round";
      context.globalAlpha = 0.85;
      context.beginPath();
      traceHeadingConnection(context, connection.start, connection.end);
      context.stroke();
      context.restore();
    });

    recordSections.forEach((section) => {
      const anchors = section.recordLabelCellIds
        .map((labelId) => displayCellById.get(labelId))
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
      const badgeHeight = Math.max(16, Math.min(24, displayWidth / 65));
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

    const meaningRoutes = buildMeaningRelationRoutes(
      layout.relations,
      displayCellById,
      displayWidth,
      displayHeight,
    );
    const connectedMeaningCellIds = new Set<string>();
    meaningRoutes.forEach((route) => {
      connectedMeaningCellIds.add(route.labelCellId);
      connectedMeaningCellIds.add(route.inputCellId);
    });
    const markerRadius = Math.max(3, Math.min(5, displayWidth / 360));
    connectedMeaningCellIds.forEach((cellId) => {
      const cell = displayCellById.get(cellId);
      if (!cell) return;
      const markerX = cell.x + cell.width - markerRadius - 3;
      const markerY = cell.y + markerRadius + 3;
      context.save();
      context.fillStyle = "#2468a2";
      context.strokeStyle = "rgba(255, 255, 255, 0.96)";
      context.lineWidth = 2;
      context.beginPath();
      context.arc(markerX, markerY, markerRadius, 0, Math.PI * 2);
      context.fill();
      context.stroke();
      context.restore();
    });

    const activeMeaningCellId = hoveredMeaningCellIdRef.current ?? selectedId;
    const showAllMeaningRoutes = mode === "erase";
    const visibleMeaningRoutes = showAllMeaningRoutes
      ? meaningRoutes
      : activeMeaningCellId
        ? meaningRoutes.filter((route) => (
          route.labelCellId === activeMeaningCellId
          || route.inputCellId === activeMeaningCellId
        ))
        : [];
    if (!showAllMeaningRoutes && visibleMeaningRoutes.length > 0) {
      const highlightedCellIds = new Set<string>();
      visibleMeaningRoutes.forEach((route) => {
        highlightedCellIds.add(route.labelCellId);
        highlightedCellIds.add(route.inputCellId);
      });
      highlightedCellIds.forEach((cellId) => {
        const cell = displayCellById.get(cellId);
        if (!cell) return;
        context.save();
        context.fillStyle = "rgba(36, 104, 162, 0.22)";
        context.strokeStyle = "rgba(20, 77, 121, 0.96)";
        context.lineWidth = 6;
        context.fillRect(cell.x, cell.y, cell.width, cell.height);
        context.strokeRect(cell.x, cell.y, cell.width, cell.height);
        context.restore();
      });
    }
    const routesByColor = new Map<string, MeaningRelationRoute[]>();
    visibleMeaningRoutes.forEach((route) => {
      const color = showAllMeaningRoutes ? "#577f8a" : "#087d91";
      routesByColor.set(color, [...(routesByColor.get(color) ?? []), route]);
    });
    routesByColor.forEach((routes, color) => {
      context.save();
      context.fillStyle = color;
      context.lineCap = "round";
      context.lineJoin = "round";
      const traceRoutes = () => {
        context.beginPath();
        const drawnSegments = new Set<string>();
        routes.forEach((route) => route.segments.forEach((segment) => {
          const key = routeSegmentKey(segment);
          if (drawnSegments.has(key)) return;
          drawnSegments.add(key);
          context.moveTo(segment.start.x, segment.start.y);
          context.lineTo(segment.end.x, segment.end.y);
        }));
        context.stroke();
      };
      if (!showAllMeaningRoutes) {
        context.strokeStyle = "rgba(255, 255, 255, 0.96)";
        context.lineWidth = 11;
        context.setLineDash([]);
        traceRoutes();
      }
      context.strokeStyle = color;
      const viewScale = Math.max(0.1, canvas.getBoundingClientRect().width / displayWidth);
      context.lineWidth = (showAllMeaningRoutes ? 1.5 : 2.5) / viewScale;
      context.setLineDash([]);
      context.globalAlpha = showAllMeaningRoutes ? 0.65 : 1;
      traceRoutes();
      context.setLineDash([]);
      context.globalAlpha = 1;
      const endpointRadius = showAllMeaningRoutes
        ? markerRadius
        : Math.max(5, Math.min(8, displayWidth / 240));
      const drawnEndpoints = new Set<string>();
      routes.forEach(({ inputAnchor }) => {
        const key = `${inputAnchor.x.toFixed(2)},${inputAnchor.y.toFixed(2)}`;
        if (drawnEndpoints.has(key)) return;
        drawnEndpoints.add(key);
        context.beginPath();
        context.arc(inputAnchor.x, inputAnchor.y, endpointRadius, 0, Math.PI * 2);
        context.fill();
      });
      context.restore();
    });

    if (draft) {
      const isSelectionDraft = mode === "select" || mode === "classify";
      const paintColor = mode === "paint" && paintRole ? LAYOUT_ROLE_STYLE[paintRole].stroke : null;
      context.strokeStyle = paintColor ?? (isSelectionDraft ? "#2468a2" : "#d62929");
      context.fillStyle = paintColor ? `${paintColor}20` : isSelectionDraft ? "#2468a220" : "#d6292920";
      context.lineWidth = 4;
      context.setLineDash([10, 6]);
      context.fillRect(draft.x, draft.y, draft.width, draft.height);
      context.strokeRect(draft.x, draft.y, draft.width, draft.height);
      context.setLineDash([]);
    }
    if (mode === "paint" && paintRole) {
      const color = LAYOUT_ROLE_STYLE[paintRole].stroke;
      const scale = Math.max(0.1, canvas.getBoundingClientRect().width / displayWidth);
      context.save();
      context.setLineDash([]);
      context.lineWidth = 3 / scale;
      context.strokeStyle = color;
      context.fillStyle = `${color}2e`;
      for (const id of new Set([...paintPreview, ...(paintHover ? [paintHover] : [])])) {
        const cell = displayCellById.get(id);
        if (!cell) continue;
        context.fillRect(cell.x, cell.y, cell.width, cell.height);
        context.strokeRect(cell.x, cell.y, cell.width, cell.height);
      }
      context.restore();
    }
    if (mode === "erase" && eraserTrail.length > 0) {
      context.save();
      context.strokeStyle = "#d62929";
      context.lineWidth = Math.max(8, displayWidth / 100);
      context.lineCap = "round";
      context.lineJoin = "round";
      context.globalAlpha = 0.52;
      context.beginPath();
      context.moveTo(eraserTrail[0].x, eraserTrail[0].y);
      eraserTrail.slice(1).forEach((location) => context.lineTo(location.x, location.y));
      if (eraserTrail.length === 1) {
        context.lineTo(eraserTrail[0].x + 0.01, eraserTrail[0].y + 0.01);
      }
      context.stroke();
      context.restore();
    }
    if (mode === "link") {
      const source = displayCellById.get(linkLabelId.current ?? wireHoverId ?? "");
      if (source) {
        const scale = Math.max(0.1, canvas.getBoundingClientRect().width / displayWidth);
        context.save(); context.strokeStyle = "#087d91"; context.lineWidth = 2 / scale;
        context.setLineDash([]); context.strokeRect(source.x, source.y, source.width, source.height);
        const x = source.x + source.width - Math.min(8 / scale, source.width / 2);
        const y = source.y + source.height / 2;
        context.beginPath(); context.arc(x, y, 5 / scale, 0, Math.PI * 2);
        context.fillStyle = "#ffffff"; context.fill(); context.stroke();
        context.restore();
      }
    }
    if (linkPoint && linkLabelId.current) {
      const label = displayCellById.get(linkLabelId.current);
      if (label) {
        const start = {
          x: label.x + label.width / 2,
          y: label.y + label.height / 2,
        };
        const meaningPreview = mode === "link" ? meaningDragSelection(label, linkPoint) : null;
        const highlightedCells = meaningPreview ? meaningPreview.visibleInputs : mode === "parent"
          ? showMeaningStep
            ? displayCells.filter((cell) => (
              cell.role === "label"
              && cell.id !== label.id
              && pointInsideRect(linkPoint, cell)
            ))
            : recordSections.flatMap((section) => {
              const anchors = section.recordLabelCellIds
                .map((labelId) => displayCellById.get(labelId))
                .filter((cell): cell is LayoutCell => Boolean(cell));
              return anchors.some((anchor) => pointInsideRect(linkPoint, anchor)) ? anchors : [];
            })
          : mode === "record"
            ? recordLabelCellsCrossedByLine(
              start,
              linkPoint,
              displayCells.filter((cell) => cell.role === "label"),
            )
            : linkLaneCells(
              start,
              linkPoint,
              displayCells.filter((cell) => cell.role === "input" || cell.role === "mark"),
            );
        highlightedCells.forEach((storedCell) => {
          const cell = displayCellById.get(storedCell.id) ?? storedCell;
          context.save();
          context.fillStyle = "#087d911a";
          context.strokeStyle = "#087d91";
          context.lineWidth = 4;
          context.setLineDash([]);
          context.fillRect(cell.x, cell.y, cell.width, cell.height);
          context.strokeRect(cell.x, cell.y, cell.width, cell.height);
          context.restore();
        });
        const scale = Math.max(0.1, canvas.getBoundingClientRect().width / displayWidth);
        const endpoint = meaningPreview?.resolvedEnd ?? linkPoint;
        const routes = meaningPreview && meaningPreview.visibleInputs.length
          ? buildMeaningRelationRoutes(meaningPreview.visibleInputs.map((cell, index) => ({
            id: `preview_${cell.id}`, inputCellId: cell.id, labelCellIds: [label.id],
            axis: meaningPreview.axis, order: index + 1, targetPath: "",
          })), displayCellById, displayWidth, displayHeight) : [];
        context.save();
        context.lineCap = "round"; context.lineJoin = "round";
        const traceWire = () => {
          context.beginPath();
          if (routes.length) {
            const drawn = new Set<string>();
            routes.forEach(route => route.segments.forEach(segment => {
              const key = routeSegmentKey(segment); if (drawn.has(key)) return; drawn.add(key);
              context.moveTo(segment.start.x, segment.start.y);
              context.lineTo(segment.end.x, segment.end.y);
            }));
          } else if (mode === "parent" || mode === "record-parent") {
            traceHeadingConnection(context, start, endpoint);
          } else {
            context.moveTo(start.x, start.y);
            context.lineTo(endpoint.x, endpoint.y);
          }
          context.stroke();
        };
        context.strokeStyle = "#ffffff"; context.lineWidth = 6 / scale; traceWire();
        context.strokeStyle = "#087d91"; context.lineWidth = 2.5 / scale; traceWire();
        context.fillStyle = "#087d91";
        const endpoints = routes.length ? routes.map(route => route.inputAnchor) : [endpoint];
        endpoints.forEach(anchor => {
          context.beginPath(); context.arc(anchor.x, anchor.y, 4 / scale, 0, Math.PI * 2);
          context.fill();
        });
        context.restore();
      }
    }
    if (mode === "draw" && polygonPoints.length) {
      context.save();
      context.strokeStyle = "#0067c0";
      context.lineWidth = 3;
      context.beginPath();
      polygonPoints.forEach((vertex, index) => index === 0
        ? context.moveTo(vertex.x, vertex.y) : context.lineTo(vertex.x, vertex.y));
      context.stroke();
      polygonPoints.forEach(vertex => {
        context.beginPath(); context.arc(vertex.x, vertex.y, 5, 0, Math.PI * 2); context.stroke();
      });
      context.restore();
    }
    if (highlightUnassigned && workflowStep === "meaning") {
      const unassignedCells = displayCells.filter(
        (cell) => unassignedMeaningIds.has(cell.id),
      );
      drawUnassignedMeaningOverlay(
        context,
        image,
        unassignedCells,
        displayWidth,
        displayHeight,
      );
    }
  };

  // 選択中の枠の塗りをゆっくり点滅（フェードイン・アウト）させる
  const redrawRef = useRef(redraw);
  redrawRef.current = redraw;
  useEffect(() => {
    if (!selectedId || rectanglesHidden || mode === "identity") {
      selectionPulseRef.current = 0.09;
      return;
    }
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      selectionPulseRef.current = 0.09;
      return;
    }
    let frame = 0;
    let last = 0;
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      if (now - last < 33) return;
      last = now;
      const wave = 0.5 - 0.5 * Math.cos((now / 1500) * Math.PI * 2);
      selectionPulseRef.current = 0.02 + 0.14 * wave;
      redrawRef.current();
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      selectionPulseRef.current = 0.09;
    };
  }, [selectedId, rectanglesHidden, mode]);

  useEffect(redraw, [
    layout, displayCells, displayWidth, displayHeight, wireHoverId,
    selectedId,
    selectedIds,
    mode,
    rectanglesHidden,
    polygonPoints,
    virtualHeadingEditor?.id,
    draft,
    linkPoint,
    cellById,
    recordGroups,
    recordSections,
    recordSectionById,
    ocrLabelAlignment,
    ocrLabelVerticalAlignment,
    identityCandidateByCellId,
    selectedIdentityByPath,
    eraserTrail,
    paintRole,
    paintPreview,
    paintHover,
    showMeaningStep,
    highlightUnassigned,
    unassignedMeaningIds,
    workflowStep,
  ]);
  useEffect(() => {
    const image = new Image();
    image.onload = () => {
      imageRef.current = image;
      redraw();
    };
    imageRef.current = null;
    image.src = displaySource;
    return () => {
      image.onload = null;
    };
  // redraw is intentionally driven by state effects after the image is cached.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displaySource]);

  const point = (
    event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>,
  ): Point => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.round(Math.max(0, Math.min(
        displayWidth,
        (event.clientX - bounds.left) * displayWidth / bounds.width,
      ))),
      y: Math.round(Math.max(0, Math.min(
        displayHeight,
        (event.clientY - bounds.top) * displayHeight / bounds.height,
      ))),
    };
  };
  const cellAt = (location: Point) => [...displayCells].reverse().find(
    (cell) => pointInCell(location, cell),
  );
  const wireLabelAt = (location: Point) => {
    const exact = cellAt(location);
    if (exact) return exact.role === "label" ? exact : undefined;
    const scale = Math.max(0.1, (canvasRef.current?.getBoundingClientRect().width ?? displayWidth) / displayWidth);
    return nearbyWireCell(location, displayCells, ["label"], scale).cell;
  };
  function meaningDragSelection(label: LayoutCell, end: Point) {
    const start = { x: label.x + label.width / 2, y: label.y + label.height / 2 };
    const exactEnd = cellAt(end);
    const scale = Math.max(0.1, (canvasRef.current?.getBoundingClientRect().width ?? displayWidth) / displayWidth);
    const nearby = exactEnd ? { cell: undefined, ambiguous: false }
      : nearbyWireCell(end, displayCells, ["input", "mark"], scale);
    const endCell = exactEnd ?? nearby.cell;
    const resolvedEnd = endCell?.role === "input" || endCell?.role === "mark"
      ? {
        x: endCell.x + endCell.width / 2,
        y: endCell.y + endCell.height / 2,
      }
      : end;
    const axis: LayoutRelation["axis"] = Math.abs(resolvedEnd.x - start.x) >= Math.abs(resolvedEnd.y - start.y)
      ? "horizontal"
      : "vertical";
    const visibleInputs = meaningLinkLaneCells(
      start,
      resolvedEnd,
      displayCells.filter((cell) => cell.role === "input" || cell.role === "mark"),
      endCell,
    );
    return { start, resolvedEnd, axis, visibleInputs: nearby.ambiguous ? [] : visibleInputs,
      snapped: Boolean(nearby.cell), ambiguous: nearby.ambiguous };
  }
  const draggingLabel = linkLabelId.current ? displayCellById.get(linkLabelId.current) : undefined;
  const wirePreview = mode === "link" && linkPoint && draggingLabel
    ? meaningDragSelection(draggingLabel, linkPoint) : null;
  const virtualHeadingRectAt = (location: Point): Rect => {
    const width = Math.min(140, displayWidth);
    const height = Math.min(32, displayHeight);
    return {
      x: Math.max(0, Math.min(Math.round(location.x), displayWidth - width)),
      y: Math.max(0, Math.min(Math.round(location.y), displayHeight - height)),
      width,
      height,
    };
  };
  const beginVirtualHeading = (location: Point) => {
    setVirtualHeadingEditor({ id: null, rect: virtualHeadingRectAt(location), value: "" });
    setMessage(null);
  };
  const beginVirtualHeadingRename = (cell: LayoutCell) => {
    setSelectedId(cell.id);
    setSelectedIds(new Set([cell.id]));
    setMode("select");
    setVirtualHeadingEditor({
      id: cell.id,
      rect: { x: cell.x, y: cell.y, width: cell.width, height: cell.height },
      value: cell.text,
    });
    setMessage(null);
  };
  const commitVirtualHeadingEditor = () => {
    if (!virtualHeadingEditor) return;
    const { id: existingId, rect, value } = virtualHeadingEditor;
    const text = value.trim();
    setVirtualHeadingEditor(null);
    if (!text) return;
    if (existingId) {
      setLayout(current => ({
        ...current,
        cells: current.cells.map(cell => cell.id === existingId ? { ...cell, text } : cell),
      }));
      setSelectedId(existingId);
      setSelectedIds(new Set([existingId]));
      return;
    }
    const id = `virtual_${crypto.randomUUID()}`;
    const cell: LayoutCell = {
      id,
      role: "parent_label",
      virtual: true,
      source: "manual",
      ...(pageView ? { pageRange: pageView.range } : {}),
      ...rect,
      text,
      confidence: 1,
      textConfidence: null,
    };
    setLayout(current => ({ ...current, cells: [...current.cells, cell] }));
    virtualHeadingId.current = id;
    setSelectedId(id);
    setSelectedIds(new Set([id]));
    setMode("virtual-group");
    setMessage(null);
  };
  const finishPolygon = () => {
    const cell = polygonCell(polygonPoints, `manual_${crypto.randomUUID()}`);
    if (!cell) {
      setMessage("重複・自己交差のない3頂点以上の多角形を作成してください。");
      return;
    }
    setLayout(current => ({ ...current, cells: [...current.cells, cell] }));
    setSelectedId(cell.id); setSelectedIds(new Set([cell.id]));
    setPolygonPoints([]); setMessage(null);
  };
  const moveEditableCell = (current: LayoutDefinition, id: string, dx: number, dy: number) => (
    pageView ? movePageHeading(current, pageView, id, dx, dy) : moveLayoutCell(current, id, dx, dy)
  );
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const connectedCellIds = new Set<string>();
    layout.relations.forEach((relation) => {
      connectedCellIds.add(relation.inputCellId);
      relation.labelCellIds.forEach((labelId) => connectedCellIds.add(labelId));
    });
    const clearHover = () => {
      if (!hoveredMeaningCellIdRef.current && !hoveredHeadingKeysRef.current.size) return;
      hoveredMeaningCellIdRef.current = null;
      hoveredHeadingKeysRef.current = new Set();
      redraw();
    };
    const updateHover = (event: PointerEvent) => {
      if (event.buttons !== 0) {
        // Keep the hovered curve visible for the duration of an eraser gesture.
        if (mode === "erase") return;
        clearHover();
        return;
      }
      const bounds = canvas.getBoundingClientRect();
      const location = {
        x: Math.max(0, Math.min(
          displayWidth,
          (event.clientX - bounds.left) * displayWidth / bounds.width,
        )),
        y: Math.max(0, Math.min(
          displayHeight,
          (event.clientY - bounds.top) * displayHeight / bounds.height,
        )),
      };
      const hoveredCell = [...displayCells].reverse().find(cell => pointInCell(location, cell));
      const nextHoveredId = workflowStep === "meaning" && (mode === "select" || mode === "link")
        && hoveredCell && connectedCellIds.has(hoveredCell.id) ? hoveredCell.id : null;
      const nextHeadingKeys = new Set(headingConnections.filter(connection => (
        connection.hoverIds.includes(hoveredCell?.id ?? "")
        || (connection.hoverBand && segmentDistance(location, location,
          connection.hoverBand.start, connection.hoverBand.end) <= 6)
        || (mode === "erase" && headingConnectionHit(location, location, connection.start, connection.end, 8))
      )).map(connection => connection.key));
      if (nextHoveredId === hoveredMeaningCellIdRef.current
        && nextHeadingKeys.size === hoveredHeadingKeysRef.current.size
        && [...nextHeadingKeys].every(key => hoveredHeadingKeysRef.current.has(key))) return;
      hoveredMeaningCellIdRef.current = nextHoveredId;
      hoveredHeadingKeysRef.current = nextHeadingKeys;
      redraw();
    };
    canvas.addEventListener("pointermove", updateHover);
    canvas.addEventListener("pointerleave", clearHover);
    return () => {
      canvas.removeEventListener("pointermove", updateHover);
      canvas.removeEventListener("pointerleave", clearHover);
    };
  }, [layout, mode, workflowStep, redraw]);
  useEffect(() => {
    hoveredHeadingKeysRef.current = new Set();
    hoveredMeaningCellIdRef.current = null;
    redraw();
  // Reset transient hover on a view/tool change, never on save responses.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, workflowStep, pageView?.page, pageView?.range]);
  const rectFrom = (start: Point, end: Point): Rect => ({
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(start.x - end.x),
    height: Math.abs(start.y - end.y),
  });
  const eraseLinksAlongSegment = (start: Point, end: Point) => {
    const tolerance = Math.max(8, displayWidth / 100);
    setLayout((current) => {
      let changed = false;
      const storedCellById = new Map(pageDisplayCells(current, pageView).map((cell) => [cell.id, cell]));
      const visualCell = (id: string) => storedCellById.get(id);
      const erasedMeaningLinks = eraseMeaningLabelLinksAlongSegment(
        current.relations,
        storedCellById,
        start,
        end,
        tolerance,
        displayWidth,
        displayHeight,
      );
      let relations = erasedMeaningLinks.relations;
      if (erasedMeaningLinks.changed) changed = true;
      const erasedParentLinks = eraseMeaningParentLinksAlongSegment(
        relations,
        storedCellById,
        start,
        end,
        tolerance,
      );
      relations = erasedParentLinks.relations;
      if (erasedParentLinks.changed) changed = true;

      const removedSectionIds = new Set<string>();
      const recordSections = (current.recordSections ?? []).flatMap((section) => {
        const anchors = section.recordLabelCellIds
          .map((labelId) => visualCell(labelId))
          .filter((cell): cell is LayoutCell => Boolean(cell))
          .sort((first, second) => first.x - second.x || first.y - second.y);
        if (anchors.length === 0) return [section];
        const bandY = anchors.reduce(
          (total, anchor) => total + anchor.y + anchor.height / 2,
          0,
        ) / anchors.length;
        const bandStart = {
          x: anchors[0].x + anchors[0].width / 2,
          y: bandY,
        };
        const lastAnchor = anchors[anchors.length - 1];
        const bandEnd = {
          x: lastAnchor.x + lastAnchor.width / 2,
          y: bandY,
        };
        if (segmentDistance(start, end, bandStart, bandEnd) <= tolerance) {
          changed = true;
          removedSectionIds.add(section.id);
          return [];
        }
        const parent = section.parentLabelCellId
          ? visualCell(section.parentLabelCellId)
          : undefined;
        if (
          parent
          && headingConnectionHit(
            start,
            end,
            rectCenter(parent),
            {
              x: (bandStart.x + bandEnd.x) / 2,
              y: bandY,
            },
            tolerance,
          )
        ) {
          changed = true;
          return [{ ...section, parentLabelCellId: null }];
        }
        return [section];
      });
      const cells = current.cells.map(cell => {
        const parent = visualCell(cell.parentLabelCellId ?? "");
        const child = visualCell(cell.id);
        if (parent && child && headingConnectionHit(start, end, rectCenter(parent), rectCenter(child), tolerance)) {
          changed = true;
          relations = relations.map(relation => relation.parentLabelCellId === parent.id
            && (relation.labelCellIds.includes(cell.id)
              || (!relation.labelCellIds.length && relation.inputCellId === cell.id))
            ? { ...relation, parentLabelCellId: null } : relation);
          return { ...cell, parentLabelCellId: null };
        }
        return cell;
      });
      if (!changed) return current;
      return {
        ...current,
        cells,
        relations,
        recordSections,
        recordGroups: (current.recordGroups ?? []).filter(
          (group) => !removedSectionIds.has(group.sectionId),
        ),
        recordStructureVerified: removedSectionIds.size > 0
          ? false
          : current.recordStructureVerified,
      };
    });
  };
  const patchCell = (id: string, patch: Partial<LayoutCell>) => {
    facetsCallbackRef.current?.(patchedCellFacets(id, patch));
    setLayout((current) => ({
    ...current,
    cells: current.cells.map((cell) => cell.id === id ? { ...cell, ...patch } : cell),
    }));
  };
  const deleteCells = (ids: Iterable<string>) => {
    const removedIds = new Set(ids);
    if (removedIds.size === 0) return;
    setLayout((current) => removeLayoutCells(current, removedIds));
    setSelectedId((current) => current && removedIds.has(current) ? null : current);
    setSelectedIds((current) => new Set([...current].filter((id) => !removedIds.has(id))));
  };
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
  /** Whether giving this box another area would drop a meaning or connection set on it. */
  const losesMeaning = (cell: LayoutCell, role: PaintRole) => {
    const current = paintRoleOf(cell);
    if (current === role) return false;
    if (current === "input" || current === "mark") {
      if (role === "input" || role === "mark") {
        return current === "mark" && (cell.markSets ?? []).some(set => set.targetPath || set.options.length > 0);
      }
      return layout.relations.some(relation => relation.inputCellId === cell.id && (relation.targetPath || relation.labelCellIds.length > 0))
        || (cell.markSets ?? []).some(set => set.targetPath)
        || (layout.recordGroups ?? []).some(group => group.inputCellIds.includes(cell.id));
    }
    if (current === "label") {
      return layout.relations.some(relation => relation.labelCellIds.includes(cell.id))
        || (layout.recordSections ?? []).some(section => section.recordLabelCellIds.includes(cell.id));
    }
    if (current === "parent_label" && role !== "label") {
      return layout.relations.some(relation => relation.parentLabelCellId === cell.id)
        || layout.cells.some(other => other.parentLabelCellId === cell.id)
        || (layout.recordSections ?? []).some(section => section.parentLabelCellId === cell.id);
    }
    return false;
  };
  /** Gives the boxes one area at once (one save, one undo). Boxes with a meaning are kept unless forced. */
  const applyPaint = (ids: Iterable<string>, role: PaintRole, force: boolean) => {
    const cells = [...new Set(ids)].map(id => displayCellById.get(id)).filter((cell): cell is LayoutCell => Boolean(cell));
    const changing = cells.filter(cell => paintRoleOf(cell) !== role);
    const virtual = changing.filter(cell => cell.virtual && role !== "parent_label");
    // A box split into parts keeps its area until the split is undone (the server refuses otherwise).
    const split = changing.filter(cell => !cell.virtual && (cell.compositeSubfields?.length ?? 0) > 0);
    const kept = force ? [] : changing.filter(cell => !cell.virtual && !split.includes(cell) && losesMeaning(cell, role));
    const targets = changing.filter(cell => !virtual.includes(cell) && !split.includes(cell) && !kept.includes(cell)).map(cell => cell.id);
    if (targets.length) setRoles(targets, role, role === "mark" ? "selection" : undefined);
    const notes = [
      targets.length ? `${targets.length}枠を「${PAINT_ROLE_LABEL[role]}」にしました。` : "",
      kept.length ? `意味を付けた${kept.length}枠は変更しませんでした（Altを押しながらで上書き）。` : "",
      virtual.length ? `仮想見出し${virtual.length}枠は見出しのままにしました。` : "",
      split.length ? `分割した記入欄${split.length}枠は変更しませんでした（分割を解除してから変更してください）。` : "",
    ].filter(Boolean);
    if (!notes.length && cells.length) notes.push(`選んだ枠はすでに「${PAINT_ROLE_LABEL[role]}」です。`);
    // The same notice twice in a row is shown again.
    if (notes.length) { setMessage(notes.join(" ")); setMessageRepeat(count => count + 1); }
  };
  const pickPaletteRole = (role: PaintRole, force: boolean) => {
    if (busy || disabled || rectanglesHidden) return;
    if (mode !== "paint" && selectedCells.length > 0) { applyPaint(selectedCells.map(cell => cell.id), role, force); return; }
    if (mode === "paint" && paintRole === role) { setMode("select"); return; }
    setPaintRole(role); setMode("paint");
    setSelectedId(null); setSelectedIds(new Set()); setDraft(null); dragStart.current = null;
  };
  paletteKeyRef.current = (event: KeyboardEvent) => {
    if (!showClassificationStep || event.ctrlKey || event.metaKey) return;
    const target = event.target as HTMLElement | null;
    if (target?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "")) return;
    if (event.key === "Escape" && mode === "paint") { setMode("select"); return; }
    // Esc clears the selection so the palette switches to painting.
    if (event.key === "Escape" && selectedIds.size > 0) { setSelectedId(null); setSelectedIds(new Set()); return; }
    const role = PAINT_ROLES[Number(event.key) - 1]?.[0];
    if (!role || event.repeat) return;
    event.preventDefault();
    pickPaletteRole(role, event.altKey);
  };
  const setRoles = (ids: Iterable<string>, role: LayoutCell["role"], reading?: "symbol" | "selection" | "number" | "checklist") => {
    const idSet = new Set(ids);
    if (idSet.size === 0) return;
    if (role !== "parent_label" && layout.cells.some(cell => idSet.has(cell.id) && cell.virtual)) {
      setMessage("仮想見出しは見出しとして使用します。不要な場合は削除してください。"); return;
    }
    facetsCallbackRef.current?.([...idSet].map(id => facetTouch(id, "role")));
    setLayout((current) => {
      let relations = current.relations;
      if (role === "input") {
        for (const cell of current.cells.filter(cell => idSet.has(cell.id) && cell.role === "mark")) {
          const symbol = cell.markSets?.length === 1 && ["symbol", "checklist"].includes(cell.markSets[0].recognitionMode ?? "")
            ? cell.markSets[0] : null;
          if (!symbol?.targetPath) continue;
          const existing = relations.find(relation => relation.inputCellId === cell.id);
          relations = existing
            ? relations.map(relation => relation.inputCellId === cell.id
              ? { ...relation, targetPath: symbol.targetPath } : relation)
            : [...relations, { id: `relation_${crypto.randomUUID()}`, inputCellId: cell.id,
              labelCellIds: [], axis: "horizontal", order: 1, targetPath: symbol.targetPath }];
        }
      }
      if (role !== "input" && role !== "mark") {
        relations = relations.filter((relation) => !idSet.has(relation.inputCellId));
      }
      if (role !== "label") {
        relations = relations.map((relation) => ({
          ...relation,
          labelCellIds: relation.labelCellIds.filter((labelId) => !idSet.has(labelId)),
        }));
      }
      if (role !== "label" && role !== "parent_label") {
        relations = relations.map((relation) => ({
          ...relation,
          parentLabelCellId: relation.parentLabelCellId && idSet.has(relation.parentLabelCellId)
            ? null
            : relation.parentLabelCellId,
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
      const labelPool = [...(current.labelPool ?? [])];
      if (reading === "number") {
        const numberCells = current.cells.filter(cell => idSet.has(cell.id))
          .sort((first, second) => first.y - second.y || first.x - second.x);
        for (const cell of numberCells) {
          // Keep existing field identity when changing the reading preset.
          if (relations.some(relation => relation.inputCellId === cell.id && relation.targetPath)) continue;
          const entry = uniqueMeaningEntry("番号", { ...current, relations, labelPool }, availableTargets);
          labelPool.push(entry);
          const existing = relations.some(relation => relation.inputCellId === cell.id);
          relations = existing
            ? relations.map(relation => relation.inputCellId === cell.id
              ? { ...relation, targetPath: entry.targetPath } : relation)
            : [...relations, { id: `direct_${cell.id}`, inputCellId: cell.id,
              labelCellIds: [], axis: "horizontal", order: 1, targetPath: entry.targetPath }];
        }
      }
      return {
        ...current,
        labelPool,
        cells: current.cells.map((originalCell) => {
          const cell = { ...originalCell,
            parentLabelCellId: (role !== "label" && role !== "parent_label"
              && idSet.has(originalCell.parentLabelCellId ?? ""))
              || (idSet.has(originalCell.id) && role === "ignored")
              ? null : originalCell.parentLabelCellId,
          };
          if (!idSet.has(cell.id)) return cell;
          cell.readingMode = reading === "number" ? "number" : undefined;
          // The item's write-in 「その他（　）」 stays with the box when its reading changes.
          const keptWriteIn = cell.markSets?.length === 1
            ? cell.markSets[0].options.find(option => option.writeInTargetPath)?.writeInTargetPath : undefined;
          if (reading === "checklist") {
            const previous = cell.markSets?.[0];
            const targetPath = current.relations.find(relation => relation.inputCellId === cell.id)?.targetPath
              || previous?.targetPath || "";
            const sets = previous && previous.recognitionMode !== "symbol" && previous.options.length
              ? [{ ...previous, targetPath, recognitionMode: "checklist" as const, selectionMode: "multiple" as const }]
              : checklistPreset(cell, targetPath);
            return { ...cell, role,
              markSets: keptWriteIn && sets.length === 1 && sets[0].options.length === 1 && !sets[0].options[0].writeInTargetPath
                ? [{ ...sets[0], options: [{ ...sets[0].options[0], writeInTargetPath: keptWriteIn }] }] : sets };
          }
          if (reading === "symbol") {
            const previous = cell.markSets?.[0];
            const targetPath = (cell.role === "input"
              ? current.relations.find(relation => relation.inputCellId === cell.id)?.targetPath
              : previous?.targetPath) || previous?.targetPath || "";
            return { ...cell, role,
              markSets: previous?.recognitionMode === "symbol" ? [{ ...previous, targetPath }] : [{
                id: previous?.id ?? newMarkSetId(),
                targetPath,
                recognitionMode: "symbol" as const,
                selectionMode: "single" as const,
                options: ["○", "◎", "×", "△"].map((symbol) => ({
                  id: newMarkOptionId(), label: symbol, value: symbol,
                  x: 0, y: 0, width: cell.width, height: cell.height,
                  ...(symbol === "○" && keptWriteIn ? { writeInTargetPath: keptWriteIn } : {}),
                })),
              }],
            };
          }
          if (reading === "selection" && cell.markSets?.some(set => ["symbol", "checklist"].includes(set.recognitionMode ?? ""))) {
            // A write-in link only exists on checklists and symbol boxes.
            return { ...cell, role,
              markSets: cell.markSets.map(set => ({ ...set, recognitionMode: "manual" as const,
                options: set.options.map(({ writeInTargetPath: _writeIn, ...option }) => option) })),
            };
          }
          if (role !== "mark" || (cell.markSets?.length ?? 0) > 0) {
            return { ...cell, role };
          }
          return {
            ...cell,
            role,
            markSets: genericManualChoicePreset(
                cell,
                availableTargets.find((target) => (
                  normalizeLabelPoolText(target.label)
                  === normalizeLabelPoolText(cell.text)
                ))?.path ?? "",
              ),
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
  const showMergeError = (message: string) => {
    showToast({ tone: "warning", title: "セルを結合できません", message });
  };
  const mergeSelectedCells = (
    cellsToMerge: LayoutCell[] = selectedCells,
    copiedMerge?: LayoutCellMerge,
  ) => {
    if (pageView && cellsToMerge.some(cell => !displayCellById.has(cell.id))) {
      showMergeError("同じページ内のセルを選択してください。");
      return;
    }
    if (cellsToMerge.length < 2) {
      showMergeError("2つ以上のセルを選択してください。");
      return;
    }
    if (cellsToMerge.some(cell => cell.shapeType === "polygon" || cell.virtual || cell.parentLabelCellId)) {
      showMergeError("多角形・仮想見出し・見出し接続済みセルは結合できません。既存の形状と接続を保持します。");
      return;
    }
    const selectedIdSet = new Set(cellsToMerge.map((cell) => cell.id));
    // A merged box may be merged again (e.g. a label widened over more spilled cells);
    // the new merge records its original cells instead of nesting merges.
    const absorbedMerges = cellsToMerge.flatMap((cell) => {
      const earlier = cellMergeByParentId.get(cell.id);
      return earlier ? [earlier] : [];
    });
    if (absorbedMerges.length > 1) {
      showMergeError("結合済みのセルどうしは結合できません。結合済みのセルは1つだけ選び、ほかは結合していないセルを選んでください。");
      return;
    }
    if (copiedMerge && absorbedMerges.length) {
      showMergeError("結合済みセルへ結合パターンは貼り付けできません。先に結合を解除してください。");
      return;
    }
    if (cellsToMerge.some((cell) => cell.role === "mark" || (cell.markSets?.length ?? 0) > 0)) {
      showMergeError("選択欄はセル結合の対象にできません。");
      return;
    }
    // Field meanings move with the merge (the first one is kept); labels in use still block.
    const referencedByMeaning = layout.relations.some((relation) => (
      relation.labelCellIds.some((labelId) => selectedIdSet.has(labelId))
      || Boolean(relation.parentLabelCellId && selectedIdSet.has(relation.parentLabelCellId))
    ));
    const referencedByRecords = (layout.recordSections ?? []).some((section) => (
      section.recordLabelCellIds.some((labelId) => selectedIdSet.has(labelId))
      || Boolean(section.parentLabelCellId && selectedIdSet.has(section.parentLabelCellId))
      || (section.rowKeyCellIds ?? []).some((cellId) => selectedIdSet.has(cellId))
    )) || (layout.recordGroups ?? []).some((group) => (
      group.inputCellIds.some((inputId) => selectedIdSet.has(inputId))
      || Boolean(group.recordKeyCellId && selectedIdSet.has(group.recordKeyCellId))
    ));
    if (referencedByMeaning || referencedByRecords) {
      showMergeError("意味づけまたは繰り返し部分に使用中のセルです。既存の接続を削除してから結合してください。");
      return;
    }
    const plan = planManualCellMerge(
      cellsToMerge,
      layout.canonicalWidth,
      layout.canonicalHeight,
    );
    if (typeof plan === "string") {
      showMergeError(plan);
      return;
    }
    const unselected = unselectedCellsInsideMerge(layout.cells, cellsToMerge, plan.bounds);
    if (unselected.length > 0) {
      logDetail("結合範囲の未選択セル", unselected.map((cell) => cell.id));
      showMergeError(`結合範囲に選択していない枠（${unselected.map(cellName).join("、")}）があります。含めて選択するか、結合を分けてください。`);
      return;
    }
    const containsInput = plan.orderedCells.some((cell) => cell.role === "input");
    // Differing names (e.g. 記入1, 記入2 on split spreadsheet cells) describe the pieces;
    // the merged field keeps the first one in reading order.
    const names = plan.orderedCells.filter(cell => cell.role === "input" && cell.fieldLabel).map(cell => cell.fieldLabel!);
    const shared = names.length > 1
      ? names.reduce((prefix, value) => { let i = 0; while (i < prefix.length && prefix[i] === value[i]) i += 1; return prefix.slice(0, i); })
        .replace(/[\d\s_\-０-９]+$/u, "")
      : "";
    const fieldLabels = new Set(shared.length >= 2 ? [shared] : names.slice(0, 1));
    const ownRelations = layout.relations.filter((relation) => selectedIdSet.has(relation.inputCellId));
    const keptRelation = containsInput
      ? plan.orderedCells.map((cell) => ownRelations.find((r) => r.inputCellId === cell.id)).find(Boolean)
      : undefined;
    const roles = new Set(plan.orderedCells.map((cell) => cell.role));
    if (!containsInput && roles.size > 1) {
      showMergeError("記入欄を含まない異なる区分のセルは結合できません。区分を揃えてから再実行してください。");
      return;
    }

    const stamp = Date.now();
    const parentCellId = `merged_${stamp}`;
    const parts: LayoutCellMergePart[] = plan.orderedCells.flatMap((cell, index) => {
      const earlier = cellMergeByParentId.get(cell.id);
      if (earlier) return earlier.parts.map((part) => ({ ...part, cell: { ...part.cell } }));
      const copiedPart = copiedMerge?.parts[index];
      const partType: LayoutCellMergePart["partType"] = copiedPart?.partType
        ?? (cell.role === "input"
          ? "input"
          : (cell.text.trim() || cell.printedLabelText?.trim())
            ? "literal"
            : "ignored");
      return [{
        cell: { ...cell },
        partType,
        literal: copiedPart?.literal
          ?? (partType === "literal"
            ? (cell.text.trim() || cell.printedLabelText?.trim() || "")
            : ""),
      }];
    });
    const fixedText = parts
      .flatMap((part) => {
        if (part.partType === "literal") return part.literal ? [part.literal] : [];
        const printed = part.cell.printedLabelText?.trim();
        return printed ? [printed] : [];
      })
      .join("");
    const textConfidences = plan.orderedCells
      .map((cell) => cell.textConfidence)
      .filter((value): value is number => value !== null);
    const parentRole: LayoutCell["role"] = containsInput
      ? "input"
      : plan.orderedCells[0].role;
    const parentCell: LayoutCell = {
      id: parentCellId,
      role: parentRole,
      ...plan.bounds,
      confidence: Math.min(...plan.orderedCells.map((cell) => cell.confidence)),
      text: containsInput
        ? ""
        : plan.orderedCells.map((cell) => cell.text.trim()).filter(Boolean).join(""),
      printedLabelText: fixedText,
      ...(fieldLabels.size === 1 ? { fieldLabel: [...fieldLabels][0] } : {}),
      textConfidence: textConfidences.length > 0 ? Math.min(...textConfidences) : null,
      textDirection: plan.direction,
      textAlignment: "center",
      source: "manual",
      markSets: [],
      compositeSubfields: [],
    };
    const merge: LayoutCellMerge = {
      id: `cell_merge_${stamp}`,
      parentCellId,
      direction: plan.direction,
      parts,
    };
    setLayout((current) => {
      const firstIndex = Math.min(...current.cells
        .map((cell, index) => selectedIdSet.has(cell.id) ? index : Number.POSITIVE_INFINITY));
      const cells = current.cells.filter((cell) => !selectedIdSet.has(cell.id));
      cells.splice(Math.min(firstIndex, cells.length), 0, parentCell);
      const absorbed = new Set(absorbedMerges.map((item) => item.id));
      return {
        ...current,
        cells,
        relations: current.relations.flatMap((relation) => !selectedIdSet.has(relation.inputCellId)
          ? [relation]
          : relation.id === keptRelation?.id ? [{ ...relation, id: `relation_${stamp}`, inputCellId: parentCellId }] : []),
        cellMerges: [...(current.cellMerges ?? []).filter((item) => !absorbed.has(item.id)), merge],
      };
    });
    setSelectedId(parentCellId);
    setSelectedIds(new Set([parentCellId]));
    setMessage(null);
    showToast({
      tone: "success",
      title: copiedMerge ? "結合パターンを貼り付けました" : "セルを結合しました",
      message: `${parts.length}個のセルを1つの${parentRole === "input" ? "記入欄" : "セル"}として扱います。`,
    });
  };
  const unmergeSelectedCell = () => {
    if (!selected || !selectedCellMerge) return;
    const parentId = selected.id;
    const isReferenced = layout.relations.some((relation) => (
      relation.inputCellId === parentId
      || relation.labelCellIds.includes(parentId)
      || relation.parentLabelCellId === parentId
    )) || (layout.recordSections ?? []).some((section) => (
      section.recordLabelCellIds.includes(parentId)
      || section.parentLabelCellId === parentId
      || (section.rowKeyCellIds ?? []).includes(parentId)
    )) || (layout.recordGroups ?? []).some((group) => (
      group.inputCellIds.includes(parentId) || group.recordKeyCellId === parentId
    ));
    if (isReferenced) {
      showToast({
        tone: "warning",
        title: "結合を解除できません",
        message: "この結合セルは意味づけまたは繰り返し部分に使用されています。既存の接続を削除してから解除してください。",
      });
      return;
    }
    const restoredCells = selectedCellMerge.parts.map((part) => ({ ...part.cell }));
    setLayout((current) => {
      const parentIndex = current.cells.findIndex((cell) => cell.id === parentId);
      const cells = current.cells.filter((cell) => cell.id !== parentId);
      cells.splice(Math.max(0, parentIndex), 0, ...restoredCells);
      return {
        ...current,
        cells,
        cellMerges: (current.cellMerges ?? []).filter((merge) => merge.id !== selectedCellMerge.id),
      };
    });
    const restoredId = restoredCells[0]?.id ?? null;
    setSelectedId(restoredId);
    setSelectedIds(restoredId ? new Set([restoredId]) : new Set());
    setMessage(null);
    showToast({ tone: "success", title: "セルの結合を解除しました" });
  };
  useEffect(() => {
    const clipboardShortcutAvailable = (target: EventTarget | null) => (
      showClassificationStep
      && !pageView
      && !busy
      && !disabled
      && !isEditableKeyboardTarget(target)
    );
    const copyMergePattern = () => {
      if (!selectedCellMerge) return false;
      copiedCellMergeRef.current = cloneCellMerge(selectedCellMerge);
      showToast({
        tone: "success",
        title: "結合パターンをコピーしました",
        message: "下の同じ並びの先頭セルを選んでCtrl+Vを押してください。結合セルを選択したまま繰り返し貼り付けることもできます。",
      });
      return true;
    };
    const pasteMergePattern = () => {
      const copiedMerge = copiedCellMergeRef.current;
      if (!copiedMerge) return false;
      const targetCells = findCopiedMergeTargetCells(
        copiedMerge,
        selectedCells,
        layout.cells,
        new Set(cellMerges.map((merge) => merge.parentCellId)),
        layout.canonicalWidth,
        layout.canonicalHeight,
      );
      if (typeof targetCells === "string") {
        showToast({ tone: "warning", title: "結合パターンを貼り付けできません", message: targetCells });
        return true;
      }
      mergeSelectedCells(targetCells, copiedMerge);
      return true;
    };
    const handleMergeClipboard = (event: KeyboardEvent) => {
      if (
        !showClassificationStep
        || pageView
        || busy
        || disabled
        || (!event.ctrlKey && !event.metaKey)
        || event.altKey
        || isEditableKeyboardTarget(event.target)
      ) return;
      const key = event.key.toLowerCase();
      if (key === "c") {
        if (copyMergePattern()) event.preventDefault();
        return;
      }
      if (key !== "v") return;
      if (pasteMergePattern()) event.preventDefault();
    };
    const handleNativeCopy = (event: ClipboardEvent) => {
      if (clipboardShortcutAvailable(event.target) && copyMergePattern()) event.preventDefault();
    };
    const handleNativePaste = (event: ClipboardEvent) => {
      if (clipboardShortcutAvailable(event.target) && pasteMergePattern()) event.preventDefault();
    };
    window.addEventListener("keydown", handleMergeClipboard);
    window.addEventListener("copy", handleNativeCopy);
    window.addEventListener("paste", handleNativePaste);
    return () => {
      window.removeEventListener("keydown", handleMergeClipboard);
      window.removeEventListener("copy", handleNativeCopy);
      window.removeEventListener("paste", handleNativePaste);
    };
  });
  const ensureAutoLabelPoolEntry = (labelText: string): LayoutLabelPoolEntry | null => {
    const entry = createAutoLabelPoolEntry(labelText, availableTargets, "exact");
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
    const duplicateEntry = layout.relations.some(relation => relation.targetPath === requestedPath && relation.inputCellId !== selected.id)
      ? uniqueMeaningEntry(targetByPath.get(requestedPath)?.label ?? "項目", layout, availableTargets) : null;
    if (duplicateEntry) requestedPath = duplicateEntry.targetPath;
    rememberRelations(layout.relations);
    const existing = layout.relations.filter(
      (relation) => relation.inputCellId === selected.id,
    );
    setSelectedPath(requestedPath);
    setLayout((current) => ({
      ...current,
      labelPool: duplicateEntry ? [...(current.labelPool ?? []), duplicateEntry] : current.labelPool,
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
      `「${cellName(selected)}」を「${targetByPath.get(requestedPath)?.label ?? "読み取る内容"}」として設定しました。`,
    );
  };
  const addCustomTarget = () => {
    const label = customLabel.trim();
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
    const generated = createAutoLabelPoolEntry(label, availableTargets, "exact");
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
    const label = displayCellById.get(labelId);
    if (!label) return;
    const { axis, visibleInputs, ambiguous } = meaningDragSelection(label, end);
    if (ambiguous) { setMessage("接続先が近接しています。つなぐ欄の中で離してください。"); return; }
    const inputs = visibleInputs.map(cell => cellById.get(cell.id)!);
    if (inputs.length === 0) {
      setMessage("スライド経路に手書き文字欄がありません。記入欄を通過するようにドラッグしてください。");
      return;
    }
    const linkedRelations = layout.relations.filter(relation => relation.labelCellIds.includes(label.id));
    // A name made only of symbols (a misread "(") or holding a leaked cell ID does not name anything.
    const named = (value?: string | null) => value && /[\p{L}\p{N}]/u.test(value) && displayName(value) === value ? value.trim() : "";
    // The printed name (kept as printed text in a merged cell) before any saved meaning's name.
    const labelName = labelCellName(label)
      || linkedRelations.map(relation => named((layout.labelPool ?? []).find(entry => entry.targetPath === relation.targetPath)?.label
        ?? availableTargets.find(target => target.path === relation.targetPath)?.label)).find(Boolean)
      || "";
    // 「□ その他（　）」「○× その他（　）」: an item name already tied to a check box or a
    // symbol box, dragged onto a write-in box, gets its own write-in field. A checklist option
    // is also linked to it (text there checks it); other readings keep the two values apart.
    const writeInMarks = inputs.length === 1 && inputs[0].role === "input"
      && !targetPathsForInputs(inputs, layout.relations)
      ? linkedRelations
        .map(relation => layout.cells.find(cell => cell.id === relation.inputCellId))
        .filter((cell): cell is LayoutCell => cell?.role === "mark" && (cell.markSets ?? []).length > 0)
      : [];
    const existingTargetPaths = targetPathsForInputs(inputs, layout.relations);
    const markTargetPaths = inputs.every((input) => input.role === "mark")
      ? inputs.map((input) => input.markSets?.[0]?.targetPath ?? "")
      : null;
    const completeMarkTargetPaths = markTargetPaths?.every(Boolean)
      ? markTargetPaths
      : null;
    const inferredTargetPath = completeMarkTargetPaths?.[0]
      || selectedPath
      || (writeInMarks.length ? "" : inferTargetPath(labelName, inputs.length, availableTargets, assignedPaths))
;
    const duplicateMeaning = inputs.length === 1
      && inferredTargetPath
      && !existingTargetPaths
      && layout.relations.some(relation => relation.targetPath === inferredTargetPath
        && relation.inputCellId !== inputs[0].id);
    // An entry whose ID is already taken (e.g. a renamed one) is made unique instead.
    const freshEntry = (entry: LayoutLabelPoolEntry | null) => entry && !(layout.labelPool ?? []).some(other => other.id === entry.id) ? entry : null;
    const autoEntries = (!inferredTargetPath || duplicateMeaning)
      && !existingTargetPaths
      && !completeMarkTargetPaths
      && inputs.every((input) => input.role === "input" || (input.role === "mark" && !(input.markSets ?? []).some(set => set.targetPath)))
      ? inputs.length === 1
        ? [writeInMarks.length ? uniqueMeaningEntry(`${labelName || "その他"}（記入）`, layout, availableTargets)
          : duplicateMeaning || !labelName ? uniqueMeaningEntry(labelName || "項目", layout, availableTargets)
            : freshEntry(createAutoLabelPoolEntry(labelName, availableTargets)) ?? uniqueMeaningEntry(labelName, layout, availableTargets)].filter(
          (entry): entry is LayoutLabelPoolEntry => Boolean(entry),
        )
        : createAutoRepeatedLabelPoolEntries(labelName || "項目", inputs.length, availableTargets, assignedPaths) ?? []
      : [];
    const autoEntry = autoEntries.length === 1
      ? autoEntries[0]
      : null;
    const targetPath = autoEntry?.targetPath ?? inferredTargetPath;
    const singleTargetPaths = targetPath && inputs.length === 1
      && availableTargets.some((target) => target.path === targetPath)
      ? [targetPath] : null;
    const requestedTargetPaths = autoEntries.length > 0
      ? autoEntries.map((entry) => entry.targetPath)
      : completeMarkTargetPaths ?? existingTargetPaths ?? singleTargetPaths;
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
      setMessage(requestedTargetConflict
        ? "選択した保存先は別の記入欄で使用中です。別の保存項目を選択してください。"
        : inputs.length > 1
          ? "複数欄の意味づけでは、保存項目の選択を解除すると、項目名から各欄の保存先を自動作成します。"
          : "保存先を決められませんでした。保存する情報を選択してから、もう一度接続してください。");
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
        undefined,
        current.cells.find(cell => cell.id === labelId)?.parentLabelCellId ?? null,
      );
      relations = renumberRelations(relations, current.cells);
      const writeInPath = writeInMarks.length ? targetPaths[0] : null;
      const markIds = new Set(writeInMarks.map(cell => cell.id));
      const cells = writeInPath ? current.cells.map(cell => {
        if (!markIds.has(cell.id)) return cell;
        return { ...cell, markSets: (cell.markSets ?? []).map(set => {
          // A symbol box (○×) keeps its item's write-in on the "○" (or first) symbol.
          if (set.recognitionMode === "symbol") {
            if (!set.options.length || set.options.some(option => option.writeInTargetPath)) return set;
            const holder = set.options.find(option => option.label === "○") ?? set.options[0];
            return { ...set, options: set.options.map(option => option.id === holder.id ? { ...option, writeInTargetPath: writeInPath } : option) };
          }
          if (set.recognitionMode !== "checklist") return set;
          const name = checklistLabel(labelName) || "その他";
          // A lone check box gets one option named after the item; otherwise the matching or only option.
          const options = set.options.length ? set.options : [{ id: newMarkOptionId(), label: name, value: name, x: 0, y: 0, width: cell.width, height: cell.height }];
          const target = options.find(option => option.label === name) ?? (options.length === 1 ? options[0] : null);
          if (!target || options.some(option => option.id !== target.id && option.writeInTargetPath === writeInPath)) return set;
          return { ...set, options: options.map(option => option.id === target.id ? { ...option, writeInTargetPath: writeInPath } : option) };
        }) };
      }) : current.cells;
      // A check box without a save item takes the meaning it was connected to.
      const pathByMark = new Map(inputs.map((input, index) => [input.id, targetPaths[index]] as const)
        .filter(([id]) => current.cells.some(cell => cell.id === id && cell.role === "mark" && cell.markSets?.length === 1 && !cell.markSets[0].targetPath)));
      const linkedCells = pathByMark.size ? cells.map(cell => pathByMark.has(cell.id)
        ? { ...cell, markSets: cell.markSets!.map(set => ({ ...set, targetPath: pathByMark.get(cell.id)! })) } : cell) : cells;
      return {
        ...current,
        cells: linkedCells,
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
    setMessage(writeInMarks.length
      ? `「${labelName || "その他"}」の記入欄を別の保存項目にしました。${writeInMarks.some(cell => cell.markSets?.some(set => set.recognitionMode === "checklist")) ? "記入があればチェック欄も選択済みとして扱います。" : ""}`
      : null);
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
    const reusableGenericSection = selectedSection;
    const collectionPath = reusableGenericSection?.collectionPath
      ?? `genericRecords.${genericRecordCollectionKey(recordSectionTitle, recordSections)}`;
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
    const section: LayoutRecordSection = existingSection ?? {
      id: `section_${collectionPath.replace(/[^A-Za-z0-9_]/g, "_")}_${Date.now()}_${recordSections.length + 1}`,
      title: recordSectionTitle.trim() || "明細",
      displayName: null,
      collectionPath,
      recordType: "generic",
      recordLabelCellIds: labelIds,
      parentLabelCellId: null,
      rowKeyStrategy: "position",
      rowKeyCellIds: [],
      includeEmptyRows: false,
      source: "manual",
    };
    const previousByOrder = new Map(
      recordGroups
        .filter((group) => group.sectionId === section.id)
        .map((group) => [group.order, group]),
    );
    const groups: LayoutRecordGroup[] = rows.rows.map((row) => {
      const inputs = row.inputCellIds
        .map((inputId) => cellById.get(inputId))
        .filter((cell): cell is LayoutCell => Boolean(cell));
      const previous = previousByOrder.get(row.order);
      return {
        id: previous?.id ?? `record_${section.id}_${row.order}`,
        sectionId: section.id,
        order: row.order,
        ...boundingRect(inputs, layout.canonicalWidth, layout.canonicalHeight),
        inputCellIds: row.inputCellIds,
        recordKeyField: "rowNumber",
        recordKeyValue: previous?.recordKeyValue || String(row.order),
        recordKeyCellId: null,
        source: "manual",
      };
    });
    const nextSection: LayoutRecordSection = {
      ...section,
      recordLabelCellIds: labelIds,
      parentLabelCellId: labelIds.includes(section.parentLabelCellId ?? "")
        ? null
        : section.parentLabelCellId,
      rowKeyStrategy: "position",
      rowKeyCellIds: [],
      includeEmptyRows: section.includeEmptyRows ?? false,
      source: "manual" as const,
    };
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
      return {
        ...current,
        layoutSchemaVersion: 3,
        recordSections: sections,
        recordGroups: nextGroups,
        relations: current.relations,
        labelPool: current.labelPool,
        recordStructureVerified: recordStructureIsLinked(sections, nextGroups),
      };
    });
    setSelectedRecordSectionId(section.id);
    setSelectedId(labelIds[0] ?? null);
    setSelectedIds(new Set(labelIds));
    const labelNames = labels.map((label) => label.text.trim() || label.id).join(" / ");
    setMessage(
      `${labelNames}を「${recordSectionDisplayName(nextSection)}」の列として指定し、${groups.length}行・`
      + `${groups.reduce((total, group) => total + group.inputCellIds.length, 0)}個の記入欄を自動編成しました。`
      + "上からの順番で各行を編成します。"
      + `帳票上の見出しはなくても保存できます。`,
    );
  };

  const finishRecordLabelBand = (end: Point) => {
    const startLabelId = linkLabelId.current;
    linkLabelId.current = null;
    setLinkPoint(null);
    if (!startLabelId) return;
    const startLabel = displayCellById.get(startLabelId);
    if (!startLabel) return;
    const labels = recordLabelCellsCrossedByLine(
      {
        x: startLabel.x + startLabel.width / 2,
        y: startLabel.y + startLabel.height / 2,
      },
      end,
      displayCells.filter((cell) => cell.role === "label"),
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
    const headingChild = [...displayCells].reverse().find(cell => pointInCell(end, cell));
    const headingParent = cellById.get(labelId);
    const section = recordSections.find(item => item.recordLabelCellIds.includes(headingChild?.id ?? ""))
      ?? recordSectionAtPoint(end, recordSections, displayCells, 10);
    if (section) {
      try {
        const next = attachHeading(layout, labelId, section.recordLabelCellIds);
        rememberRelations(layout.relations);
        setLayout({ ...next, recordStructureVerified: recordStructureIsLinked(next.recordSections ?? [], next.recordGroups ?? []) });
        setSelectedRecordSectionId(section.id);
        setMessage(`${headingParent?.text || labelId}を${recordSectionDisplayName(section)}の帳票上の見出しに設定しました。`);
      } catch (error) { setMessage((error as Error).message); }
      return;
    }
    if (headingChild && headingParent && (
      headingChild.role === "parent_label" || headingParent.role === "parent_label"
      || headingChild.role === "input" || headingChild.role === "mark"
    )) {
      try {
        const next = attachHeading(layout, labelId, [headingChild.id]);
        rememberRelations(layout.relations); setLayout(next);
        setMessage(`${cellName(headingParent)} → ${cellName(headingChild)} に接続しました。`);
      } catch (error) { setMessage((error as Error).message); }
      return;
    }
    if (mode === "parent" && showMeaningStep) {
      const parentLabel = cellById.get(labelId);
      const childLabel = [...displayCells].reverse().find((cell) => (
        cell.role === "label"
        && cell.id !== labelId
        && pointInCell(end, cell)
      ));
      if (!parentLabel || !childLabel) {
        setMessage("親項目の見出しから、意味づけ済みの子項目名まで線を引いてください。");
        return;
      }
      const scoped = attachParentLabelToMeaningRelations(
        layout.relations,
        parentLabel,
        childLabel,
      );
      if (!scoped) {
        setMessage(`先に「${cellName(childLabel)}」から記入欄への意味づけを行ってください。`);
        return;
      }
      rememberRelations(layout.relations);
      setLayout((current) => {
        const next = attachParentLabelToMeaningRelations(
          current.relations,
          parentLabel,
          childLabel,
        );
        if (!next) return current;
        return {
          ...current,
          relations: next.relations,
          labelPool: [
            ...(current.labelPool ?? []),
            ...next.entries.filter((entry) => (
              !(current.labelPool ?? []).some(
                (candidate) => candidate.targetPath === entry.targetPath,
              )
            )),
          ],
        };
      });
      const firstInputId = scoped.inputCellIds[0] ?? null;
      setSelectedId(firstInputId);
      setSelectedIds(firstInputId ? new Set([firstInputId]) : new Set());
      setSelectedPath(scoped.entries[0]?.targetPath ?? "");
      setMessage(
        `${cellName(parentLabel)} > ${cellName(childLabel)}`
        + "として親項目を設定しました。",
      );
      return;
    }
    setMessage("見出しから、接続先の見出し・項目名・繰り返し部分へ線を引いてください。");
  };

  const roleCounts = {
    label: layout.cells.filter((cell) => cell.role === "label").length,
    parentLabel: layout.cells.filter((cell) => cell.role === "parent_label").length,
    input: layout.cells.filter((cell) => cell.role === "input").length,
    mark: layout.cells.filter((cell) => cell.role === "mark").length,
    ignored: layout.cells.filter((cell) => cell.role === "ignored").length,
  };
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
  const structuredInputIds = new Set(genericRecordInputIds);
  const groupedInputIds = new Set(
    recordGroups.flatMap((group) => group.inputCellIds),
  );
  const recordStructureReady = structuredInputIds.size === 0 || (
    layout.recordStructureVerified === true
    && [...structuredInputIds].every((inputId) => groupedInputIds.has(inputId))
  );
  const incompleteMeaningIds = incompleteMeaningCellIds(layout);
  const meaningAssignedCellIds = new Set(
    layout.cells
      .filter((cell) => (
        (cell.role === "input" || cell.role === "mark")
        && !unassignedMeaningIds.has(cell.id)
        && !incompleteMeaningIds.has(cell.id)
      ))
      .map((cell) => cell.id),
  );
  const hasMeaningAssignments = layout.cells.some((cell) => (
    (cell.role === "input" || cell.role === "mark") && !unassignedMeaningIds.has(cell.id)
  ));
  const hasUnassignedFields = unassignedMeaningCount > 0;
  const layoutReadyForOcr = hasMeaningAssignments
    && recordStructureReady
    && !hasUnassignedFields
    && incompleteMeaningIds.size === 0;
  const templateReadyForOcr = layoutReadyForOcr
    && identityConfigurationReady
    && documentNameReady;
  const focusMeaningCell = (cell: LayoutCell) => {
    setSelectedId(cell.id);
    setSelectedIds(new Set([cell.id]));
    window.requestAnimationFrame(() => {
      const wrapper = canvasWrapRef.current;
      const canvas = canvasRef.current;
      if (!wrapper || !canvas) return;
      const renderedWidth = canvas.getBoundingClientRect().width;
      const scale = renderedWidth > 0 ? renderedWidth / displayWidth : 1;
      wrapper.scrollLeft = Math.max(0, (cell.x + cell.width / 2) * scale - wrapper.clientWidth / 2);
      wrapper.scrollTop = Math.max(0, (cell.y + cell.height / 2) * scale - wrapper.clientHeight / 2);
      canvas.focus({ preventScroll: true });
    });
  };
  const visibleUnassignedCells = displayCells.filter(cell => unassignedMeaningIds.has(cell.id));
  const selectNextUnassigned = () => {
    if (!visibleUnassignedCells.length) return;
    const currentIndex = visibleUnassignedCells.findIndex(cell => cell.id === selectedId);
    const nextIndex = (currentIndex + 1) % visibleUnassignedCells.length;
    setMode("select");
    setHighlightUnassigned(true);
    focusMeaningCell(visibleUnassignedCells[nextIndex]);
    setMessage(`未設定の記入欄 ${nextIndex + 1}/${visibleUnassignedCells.length}を選択しました。保存する情報を指定してください。`);
  };
  const toggleUnassignedHighlight = () => {
    if (highlightUnassigned) {
      setHighlightUnassigned(false);
      setMessage("未設定の記入欄の強調表示を解除しました。");
      return;
    }
    const firstUnassigned = displayCells.find(
      (cell) => unassignedMeaningIds.has(cell.id),
    );
    if (!firstUnassigned) return;
    setHighlightUnassigned(true);
    focusMeaningCell(firstUnassigned);
    setMessage(
      `読み取る内容が未設定の記入欄を${unassignedMeaningCount}件強調しています。`,
    );
  };

  return (
    <div className="layout-editor" aria-busy={busy}
      onPointerDownCapture={operationEndSave ? event => { interactionRef.current.pointers.add(event.pointerId); syncInteraction(); } : undefined}
      onFocusCapture={operationEndSave ? event => {
        if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
          interactionRef.current.text = !["checkbox", "radio", "button", "submit"].includes(event.target.type); syncInteraction();
        }
      } : undefined}
      onBlurCapture={operationEndSave ? () => { interactionRef.current.text = false; syncInteraction(); } : undefined}
      onCompositionStartCapture={operationEndSave ? () => { interactionRef.current.composing = true; syncInteraction(); } : undefined}
      onCompositionEndCapture={operationEndSave ? () => { interactionRef.current.composing = false; syncInteraction(); } : undefined}
      onKeyDownCapture={operationEndSave ? event => {
        if (event.key === "Enter" && !event.nativeEvent.isComposing && event.target instanceof HTMLInputElement) event.target.blur();
      } : undefined}>
      <fieldset className="layout-editor-fields" disabled={busy || disabled}>
      <FloatingTools anchor={canvasAnchor} disabled={busy || disabled} header={stepNavigation && (
        <div className="layout-step-nav" role="group" aria-label="工程の移動">
          <button type="button" aria-label="前の工程" data-tip="前の工程へ" disabled={!stepNavigation.previous}
            onClick={() => stepNavigation.previous?.()}><FiChevronLeft aria-hidden="true" /></button>
          <span><b>{stepNavigation.index + 1}/{stepNavigation.count}</b>{stepNavigation.title}</span>
          <button type="button" aria-label="次の工程" data-tip="次の工程へ" disabled={!stepNavigation.next}
            onClick={() => stepNavigation.next?.()}><FiChevronRight aria-hidden="true" /></button>
        </div>
      )}>
      <div className="layout-commandbar" role="toolbar" aria-orientation="vertical" aria-label="レイアウト編集">
        {(showClassificationStep || showMeaningStep || showRecordStep || !workflowStep) && (
          <span className="command-separator" />
        )}
        {(showLabelStep || showClassificationStep || showMeaningStep || showRecordStep) && (
          <ModeButton current={mode} mode="select" label="選択" onChange={setMode} />
        )}
        {showClassificationStep && <div className="role-palette" role="group" aria-label="区分">
          <span className="role-palette-caption" data-tip="枠を選んでいればその枠に適用、選んでいなければクリック・ドラッグで塗ります。Shift+ドラッグで四角く囲んで塗る、右クリックで枠の区分を拾う、Altで意味を付けた欄も上書き。">
            {mode !== "paint" && selectedCells.length > 0 ? `選択中の${selectedCells.length}枠に適用`
              : mode === "paint" && paintRole ? `塗る：${PAINT_ROLE_LABEL[paintRole]}` : "区分を選んで塗る"}
          </span>
          {PAINT_ROLES.map(([role, label, tip], index) => (
            <button type="button" key={role}
              className={`role-swatch${mode === "paint" && paintRole === role ? " active" : ""}`}
              aria-pressed={mode === "paint" && paintRole === role}
              data-tip={`${tip}（キー ${index + 1}）`}
              style={{ "--swatch": LAYOUT_ROLE_STYLE[role].stroke } as CSSProperties}
              onClick={event => pickPaletteRole(role, event.altKey)}>
              <i aria-hidden="true" />{label}<kbd>{index + 1}</kbd>
            </button>
          ))}
        </div>}
        {showClassificationStep && !pageView && <ModeButton current={mode} mode="draw" label="枠を追加" onChange={setMode} />}
        {mode === "draw" && !pageView && <>
          <label>形状 <select aria-label="追加する枠の形状" value={drawShape}
            onChange={event => { setDrawShape(event.target.value as "rectangle" | "polygon"); setPolygonPoints([]); }}>
            <option value="rectangle">四角形</option><option value="polygon">多角形</option>
          </select></label>
          {drawShape === "polygon" && <>
            <button type="button" disabled={polygonPoints.length < 3} onClick={finishPolygon}>多角形を確定</button>
            <button type="button" disabled={!polygonPoints.length} onClick={() => setPolygonPoints(points => points.slice(0, -1))}>最後の頂点を取り消す</button>
          </>}
          <button type="button" onClick={() => { setPolygonPoints([]); setMode("select"); }}>作図をキャンセル</button>
        </>}
        {mode === "virtual-group" && <button type="button" onClick={() => {
          setMode("select"); setDraft(null); dragStart.current = null;
        }}>囲い込みをキャンセル</button>}
        {showMeaningStep && (
          <ModeButton
            current={mode}
            mode="link"
            label="意味付け"
            icon={<MeaningLinkIcon />}
            onChange={setMode}
          />
        )}
        {(showMeaningStep || showRecordStep) && <ModeButton current={mode} mode="parent" label="親項目を接続" onChange={setMode} />}
        {showMeaningStep && (
          <button
            type="button"
            className={highlightUnassigned
              ? "unassigned-meaning-toggle active"
              : "unassigned-meaning-toggle"}
            data-tip="読み取る内容がまだ決まっていない記入欄を目立たせます。"
            aria-pressed={highlightUnassigned}
            aria-controls="layout-mapping-canvas"
            aria-label={highlightUnassigned
              ? `未設定の記入欄の強調を解除（${unassignedMeaningCount}件）`
              : unassignedMeaningCount > 0
                ? `未設定の記入欄を強調（${unassignedMeaningCount}件）`
                : "未設定なし"}
            disabled={unassignedMeaningCount === 0}
            onClick={toggleUnassignedHighlight}
          >
            <FiSearch aria-hidden="true" />
            {unassignedMeaningCount > 0
              ? <>未設定を強調 <strong>{unassignedMeaningCount}件</strong></>
              : "未設定なし"}
          </button>
        )}
        {showMeaningStep && visibleUnassignedCells.length > 0 && (
          <button type="button" className="next-unassigned" data-tip="未設定の記入欄を順に選びます。" onClick={selectNextUnassigned}>
            <FiArrowRight aria-hidden="true" />次の未設定欄へ
          </button>
        )}
        {showMeaningStep && (
          <span
            id="unassigned-meaning-status"
            className="sr-only"
            aria-live="polite"
            aria-atomic="true"
          >
            読み取る内容が未設定の記入欄は{unassignedMeaningCount}件です。
            {highlightUnassigned ? "強調表示中です。" : ""}
          </span>
        )}
        {showRecordStep && <ModeButton current={mode} mode="record" label="繰り返し部分をまとめる" onChange={setMode} />}
        {showRecordStep && <ModeButton current={mode} mode="record-parent" label="見出しへ接続" onChange={setMode} />}
        {workflowStep !== "identity" && (
          <>

            <button
              type="button"
              className={rectanglesHidden ? "active" : ""}
              data-tip={rectanglesHidden ? "枠を表示に戻します。" : "枠を隠して帳票の画像だけを確認します。"}
              aria-pressed={rectanglesHidden}
              aria-controls="layout-mapping-canvas"
              aria-label={rectanglesHidden ? "すべての矩形を表示" : "すべての矩形を非表示"}
              onClick={() => {
                if (!rectanglesHidden) {
                  dragStart.current = null;
                  moveDrag.current = null;
                  eraserPathRef.current = null;
                  setDraft(null);
                  setLinkPoint(null);
                  setEraserTrail([]);
                  magnifierPointerRef.current = null;
                  hideMagnifier();
                }
                setRectanglesHidden(current => !current);
              }}
            >
              <FiEye aria-hidden="true" />{rectanglesHidden ? "矩形を表示" : "矩形を非表示"}
            </button>
          </>
        )}
        {showMeaningStep && (
          <button
            type="button"
            disabled={onUndoSaved ? !canUndoSaved || dirty || saving || interactionActive : historyDepth === 0}
            data-tip="直前の意味づけを取り消します（Ctrl+Z）。"
            onClick={undoMeaning}
          >
            <FiRotateCcw aria-hidden="true" />意味づけを戻す
          </button>
        )}
        <span className="command-separator" />
        {workflowStep !== "identity" && (
          <details className="layout-secondary-tools">
            <summary data-tip="再検出・文字候補の取得・移動・仮想見出し・消しゴム"><FiEdit3 aria-hidden="true" />補助操作{mode === "move" ? " · 移動" : mode === "virtual" ? " · 仮想見出し" : mode === "erase" ? " · 消しゴム" : ""}</summary>
            <div className="layout-secondary-content">
        {showDetectAction && (
          <button type="button" onClick={() => void detect()} disabled={busy || saving}>
            <FiGrid aria-hidden="true" />
            {detecting ? "処理中…" : detectLabel}
          </button>
        )}
        {(showLabelStep || showClassificationStep || showMeaningStep || showRecordStep || !workflowStep) && <button
          type="button"
          className="secondary label-ocr-command"
          onClick={() => void recognizeLabels()}
          disabled={busy || saving || !onRecognizeLabels}
        >
          <FiType aria-hidden="true" />{recognizingLabels ? "文字候補を取得中…" : "項目名の文字候補を取得"}
        </button>}
        {(showClassificationStep || showMeaningStep || showRecordStep) && (
          <button type="button" className={`layout-move-button ${mode === "move" ? "active" : ""}`}
            aria-label="矩形を移動" aria-pressed={mode === "move"}
            disabled={!!pageView && !displayCells.some(cell => cell.virtual && cell.pageRange === pageView.range)}
            onClick={() => setMode(mode === "move" ? "select" : "move")}>
            <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" fill="none"
              stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 13V5a1.5 1.5 0 0 1 3 0v6-8a1.5 1.5 0 0 1 3 0v8-6a1.5 1.5 0 0 1 3 0v7-4a1.5 1.5 0 0 1 3 0v7c0 5-3 7-6 7h-1c-2 0-4-1-5-3l-4-5a1.5 1.5 0 0 1 2-2l2 2Z" />
            </svg>
            移動
          </button>
        )}
        {(showClassificationStep || showMeaningStep || showRecordStep) &&
          <ModeButton current={mode} mode="virtual" label="仮想見出し" onChange={next => {
            setVirtualHeadingEditor(null); setMode(next);
          }} />}
        <ModeButton current={mode} mode="erase" label="消しゴム" onChange={setMode} />
            </div>
          </details>
        )}
        <details className="layout-display-settings">
          <summary data-tip="文字の位置や拡大鏡を設定します。"><FiSettings aria-hidden="true" />表示設定</summary>
          <div className="layout-display-content">
        {(showLabelStep || showClassificationStep || showMeaningStep || showRecordStep || !workflowStep) && <div className="ocr-alignment-switch" role="group" aria-label="OCR文字の配置">
          <span>文字位置</span>
          {([
            ["auto", "自動", "自動配置"],
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
              onClick={() => {
                setOcrLabelAlignment(value);
                if (value === "auto") setOcrLabelVerticalAlignment("auto");
              }}
            >
                {label}
              </button>
            ))}
          </div>}
        {(showLabelStep || showClassificationStep || showMeaningStep || showRecordStep || !workflowStep) && (
          <div className="ocr-alignment-switch" role="group" aria-label="OCR文字の縦位置">
            <span>縦位置</span>
            {([
              ["auto", "自動", "縦位置を自動配置"],
              ["top", "上", "上揃え"],
              ["middle", "真ん中", "上下中央揃え"],
              ["bottom", "下", "下揃え"],
            ] as const).map(([value, label, accessibleLabel]) => (
              <button
                key={value}
                type="button"
                className={ocrLabelVerticalAlignment === value ? "active" : ""}
                aria-label={accessibleLabel}
                aria-pressed={ocrLabelVerticalAlignment === value}
                onClick={() => setOcrLabelVerticalAlignment(value)}
              >
                {label}
              </button>
            ))}
          </div>
        )}
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
        </details>
      </div>
      </FloatingTools>

      {/* Floats over the page so a message never pushes the preview down. */}
      <div className="layout-status-slot" aria-live="polite" aria-atomic="true">
        {wirePreview && <p className="layout-wire-hint">
          <FiLink aria-hidden="true" />{wirePreview.ambiguous ? "接続先が近接しています · つなぐ欄の中へ移動"
            : wirePreview.visibleInputs.length
              ? `${wirePreview.visibleInputs.length}欄に接続${wirePreview.snapped ? "（枠に合わせました）" : ""} · 離して反映 / Escで取消`
              : "接続する記入欄までドラッグ · Escで取消"}
        </p>}
        {message && workflowStep && !wirePreview && message !== hiddenMessage && <p className="layout-status">
          <span>{message}</span>
          <button type="button" className="layout-status-close" aria-label="閉じる" onClick={() => setHiddenMessage(message)}><FiX aria-hidden="true" /></button>
        </p>}
      </div>

      <div className="layout-workbench">
        <div className="mapping-preview-shell">
          <div
            ref={canvasWrapRef}
            className={highlightUnassigned && !rectanglesHidden
              ? "mapping-canvas-wrap unassigned-highlight-active"
              : "mapping-canvas-wrap"}
            aria-busy={detecting}
          >
            <div className="layout-canvas-stage">
              <canvas
            id="layout-mapping-canvas"
            ref={canvasRef}
            width={displayWidth}
            height={displayHeight}
            tabIndex={rectanglesHidden ? -1 : 0}
            aria-label={showIdentityStep ? "書類を見分ける欄を選択する帳票プレビュー" : "帳票レイアウト編集キャンバス"}
            aria-describedby="layout-keyboard-help"
            className={`layout-mode-${mode}`}
            style={rectanglesHidden ? { pointerEvents: "none" } : mode === "paint" ? { cursor: "crosshair" } : mode === "link"
              ? { cursor: wireDragging ? "grabbing" : wireHoverId ? "grab" : "default" } : undefined}
            onContextMenu={(event) => {
              if (mode !== "paint") return;
              event.preventDefault();
              const hit = cellAt(point(event));
              if (hit) { setPaintRole(paintRoleOf(hit)); setMessage(`「${PAINT_ROLE_LABEL[paintRoleOf(hit)]}」で塗ります。`); }
            }}
            onPointerEnter={(event) => queueMagnifier(event.clientX, event.clientY)}
            onPointerLeave={() => {
              if (!linkLabelId.current) setWireHoverId(null);
              setPaintHover(null);
              magnifierPointerRef.current = null;
              hideMagnifier();
            }}
            onClick={(event) => {
              if (rectanglesHidden || busy || disabled || mode !== "identity") return;
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
              if (rectanglesHidden) return;
              if (event.key === "Escape") {
                moveDrag.current = null; linkLabelId.current = null; setLinkPoint(null);
                wirePointer.current = null; wireOrigin.current = null; setWireHoverId(null);
                setPolygonPoints([]); setVirtualHeadingEditor(null); setMode("select");
                setDraft(null); dragStart.current = null; return;
              }
              if (mode === "draw" && drawShape === "polygon" && event.key === "Enter"
                && !busy && !disabled) { finishPolygon(); return; }
              if (busy || disabled || displayCells.length === 0) return;
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
                ? identityCandidates.map((candidate) => candidate.cell).filter(cell => displayCellById.has(cell.id))
                : displayCells;
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
              if (rectanglesHidden || busy || disabled) return;
              if (virtualHeadingEditor) {
                event.preventDefault();
                commitVirtualHeadingEditor();
                return;
              }
              const location = point(event);
              const hit = cellAt(location);
              if (mode === "move") {
                if (!hit || (pageView && (!hit.virtual || hit.pageRange !== pageView.range))) return;
                moveDrag.current = { id: hit.id, start: location };
                setSelectedId(hit.id); setSelectedIds(new Set([hit.id]));
                event.currentTarget.setPointerCapture?.(event.pointerId);
                return;
              }
              if (mode === "virtual") {
                event.preventDefault();
                if (hit?.virtual) beginVirtualHeadingRename(hit);
                else beginVirtualHeading(location);
                return;
              }
              if (mode === "virtual-group") {
                if (hit?.id !== virtualHeadingId.current) {
                  setMessage("仮想見出しのアイコンからドラッグを開始してください。"); return;
                }
                event.currentTarget.setPointerCapture?.(event.pointerId);
                dragStart.current = location; setDraft({ ...location, width: 0, height: 0 }); return;
              }
              if (mode === "draw" && drawShape === "polygon") {
                if (polygonPoints.length >= 3 && Math.hypot(location.x - polygonPoints[0].x,
                  location.y - polygonPoints[0].y) < 10) finishPolygon();
                else if (polygonPoints.length < 100) setPolygonPoints(points => [...points, location]);
                return;
              }
              if (mode === "identity") {
                return;
              }
              if (mode === "paint") {
                if (event.button !== 0 || !paintRole) return;
                event.currentTarget.setPointerCapture?.(event.pointerId);
                const ids = new Set(hit && !event.shiftKey ? [hit.id] : []);
                paintStroke.current = { ids, force: event.altKey, rectStart: event.shiftKey ? location : null, last: location };
                setPaintPreview([...ids]);
                if (event.shiftKey) setDraft({ ...location, width: 0, height: 0 });
                return;
              }
              if (mode === "erase") {
                event.currentTarget.setPointerCapture?.(event.pointerId);
                eraserPathRef.current = [location];
                setEraserTrail([location]);
                rememberRelations(layout.relations);
                eraseLinksAlongSegment(location, location);
                return;
              }
              if (mode === "select" || mode === "classify") {
                if (hit?.virtual) {
                  event.preventDefault();
                  beginVirtualHeadingRename(hit);
                  return;
                }
                event.currentTarget.setPointerCapture?.(event.pointerId);
                dragStart.current = location;
                additiveSelection.current = event.ctrlKey || event.metaKey;
                setDraft({ ...location, width: 0, height: 0 });
                return;
              }
              if (mode === "record") {
                const bounds = event.currentTarget.getBoundingClientRect();
                const hitTolerance = Math.max(
                  10,
                  16 * displayWidth / Math.max(1, bounds.width),
                  16 * displayHeight / Math.max(1, bounds.height),
                );
                const recordSectionHit = recordSectionAtPoint(
                  location,
                  recordSections,
                  displayCells,
                  hitTolerance,
                );
                if (recordSectionHit) {
                  const recordLabelIds = recordSectionHit.recordLabelCellIds.filter((id) => (
                    displayCellById.has(id)
                  ));
                  setSelectedRecordSectionId(recordSectionHit.id);
                  setSelectedId(recordLabelIds[0] ?? null);
                  setSelectedIds(new Set(recordLabelIds));
                  setMessage("繰り返し部分を選択しました。セル単位で選択するには対象セルをダブルクリックしてください。");
                  return;
                }
                if (!hit || hit.role !== "label") {
                  setMessage("レコード作成では、最初の列ラベルから最後の列ラベルまで横方向にドラッグしてください。");
                  return;
                }
                event.currentTarget.setPointerCapture?.(event.pointerId);
                linkLabelId.current = hit.id;
                setLinkPoint(location);
                return;
              }
              if (mode === "link" || mode === "parent" || mode === "record-parent") {
                const labelHit = mode === "link" ? wireLabelAt(location) : [...displayCells].reverse().find((cell) => (
                  (cell.role === "label" || ((mode === "parent" || mode === "record-parent") && cell.role === "parent_label"))
                  && pointInCell(location, cell)
                ));
                if (!labelHit) {
                  setMessage(
                    mode === "parent" || mode === "record-parent"
                      ? mode === "parent"
                        ? "親項目の接続は、親となる見出しセルからドラッグを開始してください。"
                        : "親ラベル接続はラベルセルからドラッグを開始してください。"
                      : "意味づけはラベルセルからドラッグを開始してください。",
                  );
                  return;
                }
                event.currentTarget.setPointerCapture?.(event.pointerId);
                linkLabelId.current = labelHit.id;
                if (mode === "link") {
                  wirePointer.current = { clientX: event.clientX, clientY: event.clientY };
                  wireOrigin.current = wirePointer.current;
                  setWireHoverId(labelHit.id); setMessage(null);
                }
                setLinkPoint(location);
                return;
              }
              event.currentTarget.setPointerCapture?.(event.pointerId);
              dragStart.current = location;
              setDraft({ ...location, width: 0, height: 0 });
            }}
            onPointerMove={(event) => {
              if (rectanglesHidden) return;
              queueMagnifier(event.clientX, event.clientY);
              const location = point(event);
              if (mode === "paint") {
                const stroke = paintStroke.current;
                if (!stroke) { setPaintHover(cellAt(location)?.id ?? null); return; }
                if (event.altKey) stroke.force = true;
                if (stroke.rectStart) {
                  const area = rectFrom(stroke.rectStart, location);
                  setDraft(area);
                  setPaintPreview(cellsSelectedByRect(displayCells, area).map(cell => cell.id));
                  return;
                }
                // Sample the path so a fast stroke does not skip small boxes.
                const steps = Math.max(1, Math.ceil(Math.hypot(location.x - stroke.last.x, location.y - stroke.last.y) / 4));
                const before = stroke.ids.size;
                for (let step = 1; step <= steps; step += 1) {
                  const hit = cellAt({
                    x: stroke.last.x + (location.x - stroke.last.x) * step / steps,
                    y: stroke.last.y + (location.y - stroke.last.y) * step / steps,
                  });
                  if (hit) stroke.ids.add(hit.id);
                }
                stroke.last = location;
                if (stroke.ids.size !== before) setPaintPreview([...stroke.ids]);
                return;
              }
              if (mode === "move" && moveDrag.current) {
                const { id, start } = moveDrag.current;
                const moved = moveEditableCell(layout, id, location.x - start.x, location.y - start.y);
                const cell = moved.cells.find(cell => cell.id === id);
                if (cell) setDraft({ x: cell.x, y: cell.y, width: cell.width, height: cell.height });
                return;
              }
              if (
                (mode === "draw" || mode === "select" || mode === "classify" || mode === "virtual-group")
                && dragStart.current
              ) {
                setDraft(rectFrom(dragStart.current, location));
              }
              if (mode === "link") {
                if (linkLabelId.current) wirePointer.current = { clientX: event.clientX, clientY: event.clientY };
                else setWireHoverId(wireLabelAt(location)?.id ?? null);
              }
              if ((mode === "record" || mode === "parent" || mode === "record-parent") && linkLabelId.current) {
                setLinkPoint(location);
              }
              if (mode === "erase" && eraserPathRef.current) {
                const path = eraserPathRef.current;
                const previous = path[path.length - 1];
                if (Math.hypot(location.x - previous.x, location.y - previous.y) >= 2) {
                  eraseLinksAlongSegment(previous, location);
                  const nextPath = [...path, location];
                  eraserPathRef.current = nextPath;
                  setEraserTrail(nextPath);
                }
              }
            }}
            onPointerUp={(event) => {
              if (rectanglesHidden) return;
              const location = point(event);
              if (mode === "move") {
                const moving = moveDrag.current;
                moveDrag.current = null; setDraft(null);
                if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
                if (moving && !busy && !disabled) setLayout(current =>
                  moveEditableCell(current, moving.id, location.x - moving.start.x, location.y - moving.start.y));
                return;
              }
              if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
              if (mode === "paint") {
                const stroke = paintStroke.current;
                paintStroke.current = null; setDraft(null); setPaintPreview([]);
                if (!stroke || !paintRole || busy || disabled) return;
                const ids = stroke.rectStart
                  ? cellsSelectedByRect(displayCells, rectFrom(stroke.rectStart, location)).map(cell => cell.id)
                  : [...stroke.ids];
                applyPaint(ids, paintRole, stroke.force || event.altKey);
                return;
              }
              if (mode === "virtual-group" && dragStart.current && virtualHeadingId.current) {
                const area = rectFrom(dragStart.current, location);
                dragStart.current = null; setDraft(null);
                const children = enclosedHeadingChildren({ ...layout, cells: displayCells }, area);
                if (!children.length) { setMessage("範囲内に項目名・記入欄・選択欄がありません。"); return; }
                try {
                  const next = attachHeading(layout, virtualHeadingId.current, children);
                  rememberRelations(layout.relations); setLayout(next);
                  setMode("select"); setMessage(`${children.length}個の項目を仮想見出しへ接続しました。`);
                } catch (error) { setMessage((error as Error).message); }
                return;
              }
              if (mode === "erase") {
                eraserPathRef.current = null;
                setEraserTrail([]);
                setMessage("なぞった範囲の意味づけ・繰り返し部分のリンクを削除しました。");
                return;
              }
              if (mode === "link") {
                const origin = wireOrigin.current;
                setWireHoverId(wireLabelAt(location)?.id ?? null);
                wirePointer.current = null; wireOrigin.current = null;
                if (origin && Math.hypot(event.clientX - origin.clientX, event.clientY - origin.clientY) < 4) {
                  linkLabelId.current = null; setLinkPoint(null); return;
                }
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
              if (mode === "record-parent") {
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
                      ? null
                      : ids.size === 1
                        ? "1 個のセルを選択しています。Ctrlを押しながら別のセルを追加できます。"
                        : "セルの選択を解除しました。");
                  } else {
                    setSelectedId(hit.id);
                    setSelectedIds(new Set([hit.id]));
                  }
                  return;
                }
                const ids = cellsSelectedByRect(displayCells, next).map((cell) => cell.id);
                const nextIds = additive
                  ? new Set([...selectedIds, ...ids])
                  : new Set(ids);
                setSelectedId(ids[0] ?? (additive ? selectedId : null));
                setSelectedIds(nextIds);
                setMessage(nextIds.size > 0
                  ? null
                  : "選択範囲にセルはありません。変更したいセルを囲むようにドラッグしてください。");
                return;
              }
              if (mode !== "draw" || drawShape !== "rectangle" || !dragStart.current) return;
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
              // Stay in draw mode so several boxes can be added in a row (Esc or a tool ends it).
            }}
            onDoubleClick={(event) => {
              if (rectanglesHidden || busy || disabled || mode !== "record") return;
              const location = point(event);
              const bounds = event.currentTarget.getBoundingClientRect();
              const hitTolerance = Math.max(
                10,
                16 * displayWidth / Math.max(1, bounds.width),
                16 * displayHeight / Math.max(1, bounds.height),
              );
              const recordSectionHit = recordSectionAtPoint(
                location,
                recordSections,
                displayCells,
                hitTolerance,
              );
              const hit = cellAt(location);
              if (!recordSectionHit || !hit) return;
              event.preventDefault();
              setSelectedRecordSectionId(recordSectionHit.id);
              setSelectedId(hit.id);
              setSelectedIds(new Set([hit.id]));
              setMessage("繰り返し部分のセルを1つ選択しました。");
            }}
            onPointerCancel={() => {
              wirePointer.current = null; wireOrigin.current = null; setWireHoverId(null);
              moveDrag.current = null;
              dragStart.current = null;
              additiveSelection.current = false;
              linkLabelId.current = null;
              eraserPathRef.current = null;
              setDraft(null);
              setLinkPoint(null);
              setEraserTrail([]);
            }}
              />
              {aiContext && <TemplateAiFocus canvasRef={canvasRef} cells={displayCells} width={displayWidth} height={displayHeight}
                sections={recordSections} groups={recordGroups} focus={aiFocus} context={aiContext} viewRevision={aiViewRevision} suspendScroll={interactionActive} />}
              {!rectanglesHidden && warningFocusDisplayCell && (
                <span
                  className="layout-warning-cell-highlight"
                  data-cell-id={warningFocusDisplayCell.id}
                  aria-hidden="true"
                  style={{
                    left: `${warningFocusDisplayCell.x / Math.max(1, displayWidth) * 100}%`,
                    top: `${warningFocusDisplayCell.y / Math.max(1, displayHeight) * 100}%`,
                    width: `${warningFocusDisplayCell.width / Math.max(1, displayWidth) * 100}%`,
                    height: `${warningFocusDisplayCell.height / Math.max(1, displayHeight) * 100}%`,
                  }}
                />
              )}
              {virtualHeadingEditor && (
                <input
                  ref={virtualHeadingInputRef}
                  className={`virtual-heading-inline-input ${virtualHeadingEditor.id
                    ? "editing-existing"
                    : "creating-new"}`}
                  aria-label="仮想見出し名"
                  placeholder="見出し名"
                  maxLength={300}
                  value={virtualHeadingEditor.value}
                  style={{
                    left: `${virtualHeadingEditor.rect.x / Math.max(1, displayWidth) * 100}%`,
                    top: `${virtualHeadingEditor.rect.y / Math.max(1, displayHeight) * 100}%`,
                    width: `${virtualHeadingEditor.rect.width / Math.max(1, displayWidth) * 100}%`,
                    height: `${virtualHeadingEditor.rect.height / Math.max(1, displayHeight) * 100}%`,
                  }}
                  onPointerDown={(event) => event.stopPropagation()}
                  onChange={(event) => setVirtualHeadingEditor(current => (
                    current ? { ...current, value: event.target.value } : current
                  ))}
                  onBlur={commitVirtualHeadingEditor}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      event.stopPropagation();
                      commitVirtualHeadingEditor();
                    } else if (event.key === "Escape") {
                      event.preventDefault();
                      event.stopPropagation();
                      setVirtualHeadingEditor(null);
                      setMode("select");
                    }
                  }}
                />
              )}
            </div>
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
            </div>
          )}
          </div>
          {pageNavigation && pageNavigation.totalPages > 1 && <>
            <button
              type="button"
              className="preview-page-navigation previous"
              aria-label="前の編集ページ"
              title={`${pageNavigation.currentPage} / ${pageNavigation.totalPages}ページ`}
              disabled={pageNavigation.disabled || !pageNavigation.hasPrevious}
              onClick={pageNavigation.onPrevious}
            >
              ‹
            </button>
            <button
              type="button"
              className="preview-page-navigation next"
              aria-label="次の編集ページ"
              title={`${pageNavigation.currentPage} / ${pageNavigation.totalPages}ページ`}
              disabled={pageNavigation.disabled || !pageNavigation.hasNext}
              onClick={pageNavigation.onNext}
            >
              ›
            </button>
          </>}
        </div>

        <aside className="layout-properties">
          <h3>{workflowStep
            ? { classify: "欄の種類", meaning: "読み取る内容", records: "繰り返し部分", identity: "確認する欄とファイル名", review: "確認・保存" }[workflowStep]
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
                    {index + 1}. {cellMergeByParentId.has(cell.id)
                      ? `結合セル (${cellMergeByParentId.get(cell.id)?.parts.length ?? 0}セル)`
                      : cell.role === "label"
                      ? "項目名"
                      : cell.role === "parent_label"
                        ? "見出し"
                      : cell.role === "input"
                        || cell.role === "record_key_cell"
                        || cell.role === "column_key_cell"
                        ? "記入欄"
                          : cell.role === "mark"
                            ? "選択欄"
                            : "読み取らない"} · {cell.text || "文字情報なし"}
                    {unassignedMeaningIds.has(cell.id) ? " · 未設定" : ""}
                    {incompleteMeaningIds.has(cell.id) ? " · 設定不完全" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {!selected && workflowStep !== "identity" && !showMeaningStep && <p className="empty-property">帳票上の矩形を選択してください。</p>}
          {showMeaningStep && (
            <section className="meaning-context" aria-label="意味づけの次の操作">
              <span className="selection-caption">{selected ? "選択中の欄" : "まず帳票の欄を選択"}</span>
              {selected && <strong>{LAYOUT_ROLE_LABEL[selected.role]} · {selected.fieldLabel?.trim() || selected.text.trim() || `欄 ${displayCells.findIndex(cell => cell.id === selected.id) + 1}`}</strong>}
              <p>{selected?.role === "input"
                  ? "下の「保存する情報」を選ぶと、この記入欄に割り当てます。"
                  : selected?.role === "mark"
                    ? singleCheckbox
                      ? "チェック枠1つの欄です。線でつないだ項目に、チェックの有無を保存します。"
                      : "この欄の選択肢と保存する値を設定してください。"
                    : "記入欄を選んで保存先を指定します。項目名と線でつなぐ場合は「意味付け」を選びます。"}</p>
              {mode === "link" && selected?.role === "input" && <button type="button" className="secondary compact" onClick={() => setMode("select")}><FiEdit3 aria-hidden="true" />保存先を直接指定</button>}
            </section>
          )}
          {showClassificationStep && selectedCells.length > 1 && (
            <section className="bulk-selection">
              <h3>範囲選択: {selectedCells.length} セル</h3>
              <p>選択したセルの結合・完全削除・選択解除ができます。区分は下の「区分」からまとめて変更できます。</p>
              <div className="cell-merge-actions">
                <button type="button" className="merge-cell-button" onClick={() => mergeSelectedCells()}>
                  選択セルを結合
                </button>
                <small>元のセルは保持され、結合解除で復元できます。</small>
              </div>
              <button
                type="button"
                className="danger-button compact full"
                onClick={() => deleteCells(selectedIds)}
              >
                選択した矩形を完全に削除
              </button>
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
              {showClassificationStep && selectedCellMerge && (
                <section className="merged-cell-summary">
                  <strong>結合セル</strong>
                  <span>{selectedCellMerge.parts.length}個のセルを{selectedCellMerge.direction === "horizontal" ? "左から右" : "上から下"}の順で結合しています。</span>
                  <ol>
                    {selectedCellMerge.parts.map((part) => (
                      <li key={part.cell.id}>
                        {part.partType === "input" ? "入力" : part.partType === "literal" ? `固定文字「${part.literal || "未設定"}」` : "無視"}
                      </li>
                    ))}
                  </ol>
                  {!pageView && <small><kbd>Ctrl+C</kbd>で結合パターンをコピーし、下段の先頭セルまたは現在の結合セルを選んで<kbd>Ctrl+V</kbd>を押すと同じ結合を適用できます。</small>}
                  <button type="button" onClick={unmergeSelectedCell}>結合を解除</button>
                </section>
              )}
              {roleEditable && (
                <fieldset>
                  <legend>区分</legend>
                  <div className="role-buttons">
                    <button className={selectedCells.length > 0 && selectedCells.every(cell => cell.role === "label") ? "active label-role" : ""} onClick={() => setRoles(selectedIds, "label")}>項目名</button>
                    <button className={selectedCells.length > 0 && selectedCells.every(cell => cell.role === "parent_label") ? "active parent-label-role" : ""} onClick={() => setRoles(selectedIds, "parent_label")}>見出し</button>
                    <button className={selectedCells.length > 0 && selectedCells.every(cell => cell.role === "input" || cell.role === "mark") ? "active input-role" : ""} onClick={() => setRoles(selectedCells.filter(cell => cell.role !== "input" && cell.role !== "mark").map(cell => cell.id), "input")}>記入欄</button>
                    <button className={selectedCells.length > 0 && selectedCells.every(cell => cell.role === "ignored") ? "active" : ""} onClick={() => setRoles(selectedIds, "ignored")}>読み取らない</button>
                  </div>
                </fieldset>
              )}
              {roleEditable && selectedCells.length > 0
                && selectedCells.every(cell => cell.role === "input" || cell.role === "mark") && (
                <fieldset>
                  <legend>読み取り方法</legend>
                  <div className="role-buttons reading-method-buttons">
                    <button aria-pressed={selectedCells.every(cell => cell.role === "input" && cell.readingMode !== "number")}
                      onClick={() => setRoles(selectedIds, "input")}>文字を読む</button>
                    <button aria-pressed={selectedCells.every(cell => cell.role === "input" && cell.readingMode === "number")}
                      disabled={selectedCells.some(cell => (cell.compositeSubfields?.length ?? 0) > 0 || (cell.markSets?.length ?? 0) > 1)}
                      onClick={() => setRoles(selectedIds, "input", "number")}>番号を読む</button>
                    <button aria-pressed={selectedCells.every(cell => cell.role === "mark" && cell.markSets?.some(set => set.recognitionMode === "symbol"))}
                      disabled={selectedCells.some(cell => (cell.markSets?.length ?? 0) > 1 || (cell.compositeSubfields?.length ?? 0) > 0)}
                      onClick={() => setRoles(selectedIds, "mark", "symbol")}>記入された記号を読む</button>
                    <button aria-pressed={selectedCells.every(cell => cell.role === "mark" && !cell.markSets?.some(set => ["symbol", "checklist"].includes(set.recognitionMode ?? "")))}
                      onClick={() => setRoles(selectedIds, "mark", "selection")}>印刷された選択肢を読む</button>
                    <button aria-pressed={selectedCells.every(cell => cell.role === "mark" && cell.markSets?.some(set => set.recognitionMode === "checklist"))}
                      disabled={selectedCells.some(cell => (cell.markSets?.length ?? 0) > 1 || (cell.compositeSubfields?.length ?? 0) > 0)}
                      onClick={() => setRoles(selectedIds, "mark", "checklist")}><FiCheckSquare aria-hidden="true" /> チェックリストを読む</button>
                  </div>
                  {selectedCells.some(cell => cell.readingMode === "number") && <small>番号欄には上から順に1、2、3…を自動で入れます。同じ高さの欄は左から順に数えます。番号の画像はOCRしません。保存先がない欄には「番号」を追加します。</small>}
                  {selectedCells.some(cell => (cell.markSets?.length ?? 0) > 1 || (cell.compositeSubfields?.length ?? 0) > 0)
                    && <small>記号の判定には、複合項目や複数の選択グループを含まない欄を使用してください。</small>}
                </fieldset>
              )}
              {showLabelStep && !selected.virtual && (selected.role === "label" || selected.role === "parent_label") && (
                <label>項目名の文字候補
                  <input
                    value={selected.text}
                    // A merged item name keeps its printed name, which names the item until replaced here.
                    placeholder={selected.printedLabelText?.trim() || "未認識の場合は入力"}
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
              {(showClassificationStep || showMeaningStep) && selected.role === "input" && (
                <label>出力項目の表示名
                  <input aria-label="出力項目の表示名" maxLength={100}
                    value={selected.fieldLabel ?? ""} placeholder="未設定の場合は接続した項目名を使用"
                    onChange={event => patchCell(selected.id, { fieldLabel: event.target.value || null })} />
                </label>
              )}
              {showMeaningStep && selected.role === "mark" && !singleCheckbox && (
                <div
                  ref={markWarningEditorRef}
                  className={warningFocusRequest?.purpose === "mark-options" && warningFocusRequest.cellId === selected.id
                    ? "mark-warning-focus-target active"
                    : "mark-warning-focus-target"}
                  tabIndex={-1}
                  aria-label="対象セルの選択肢エディタ"
                >
                  <MarkCellEditor
                    cell={selected}
                    targets={availableTargets}
                    writeInTargets={availableTargets.filter((target) => layout.relations.some((relation) => (
                      relation.targetPath === target.path
                      && layout.cells.some((cell) => cell.id === relation.inputCellId && cell.role === "input" && !cell.markSets?.length)
                    )))}
                    sets={selected.markSets ?? []}
                    source={source}
                    canonicalWidth={layout.canonicalWidth}
                    canonicalHeight={layout.canonicalHeight}
                    onChange={(markSets) => patchCell(selected.id, { markSets })}
                    selectedChoiceId={assistantChoice?.cellId === selected.id ? assistantChoice.choiceId : null}
                    onChoiceSelect={(markSetId, choiceId) => setAssistantChoice({ cellId: selected.id, markSetId, choiceId })}
                    hideTarget={Boolean(selectedChecklist?.targetPath)
                      && layout.relations.some(relation => relation.inputCellId === selected.id)}
                  />
                </div>
              )}
              {showClassificationStep && <button
                className="danger-button compact"
                type="button"
                onClick={() => deleteCells([selected.id])}
              >
                矩形を完全に削除
              </button>}
            </div>
          )}

          {showMeaningStep && <>
          <h3>読み取る内容の保存先</h3>
          {selectedMeaningParent && selectedMeaningChild && (
            <div className="meaning-parent-context" aria-label="設定済みの親項目">
              <span>親項目で区別</span>
              <strong>
                {cellName(selectedMeaningParent)}
                <b aria-hidden="true">›</b>
                {cellName(selectedMeaningChild)}
              </strong>
              <small>同名の項目は、この親項目ごとに別の情報として保存されます。</small>
            </div>
          )}
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
                      {meaningDestinationLabel(target, items)}{assignedPaths.has(target.path) ? " ✓" : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <small className="destination-legend">✓ はすでに使われている保存先です。</small>
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
              <p>複数の列見出しを横断すると、上からの順番で各行を自動編成します。帳票上の見出しは任意です。</p>
              <span>{recordGroups.length}レコードを定義済み</span>
            </div>
          )}
          {showIdentityStep && (
            <div className="document-identity-canvas-setting">
              <strong>① 読み取り後に確認する欄</strong>
              <p>
                読み取りが終わると、ここで選んだ欄の画像と読み取り結果を最初に表示し、
                「この書類で合っているか」を確認します。氏名や番号など、書類ごとに中身が変わる欄を1〜3個選んでください。
              </p>
              {identityCandidates.length > 0 ? (
                <>
                  {recommendedIdentityCandidates.some((candidate) => !selectedIdentityByPath.has(candidate.targetPath)) && (
                    <button type="button" className="secondary" onClick={selectRecommendedIdentityCandidates}>
                      おすすめの欄をまとめて選ぶ
                    </button>
                  )}
                  <div className="identity-checklist" aria-label="確認する欄">
                    {identityCandidates.map((candidate) => (
                      <label
                        key={candidate.targetPath}
                        className={selectedIdentityByPath.has(candidate.targetPath) ? "checked" : undefined}
                        onMouseEnter={() => setSelectedId(candidate.cell.id)}
                      >
                        <input
                          type="checkbox"
                          checked={selectedIdentityByPath.has(candidate.targetPath)}
                          onChange={() => toggleIdentityCandidate(candidate)}
                        />
                        <span>{candidate.label}</span>
                        {recommendedIdentityPaths.has(candidate.targetPath) && <em>おすすめ</em>}
                      </label>
                    ))}
                  </div>
                  <small className="identity-checklist-hint">左の帳票で枠をクリックしても選べます。</small>
                </>
              ) : (
                <p className="inline-warning">
                  選べる欄がありません。前の工程で、繰り返し行ではない記入欄に項目名を付けてください。
                </p>
              )}
              {selectedIdentityCandidates.length > 0 ? (
                <div className="identity-rule-summary" aria-live="polite">
                  <strong>読み取り後に {selectedIdentityCandidates.map((candidate) => `「${candidate.label}」`).join("")} を確認します。</strong>
                </div>
              ) : identityCandidates.length > 0 && (
                <p className="inline-warning">確認する欄を1つ以上選んでください。</p>
              )}
              <div className="document-name-rule-setting">
                <strong>② 保存するファイル名</strong>
                <div className="identity-card-preview" aria-label="書類名の生成プレビュー">
                  <span>ファイル名の例</span>
                  <strong>{documentNameRule
                    ? `${documentNameRule.prefix}${documentNameRule.fieldPaths.map((path) => `〈${identityCandidates.find((candidate) => candidate.targetPath === path)?.label ?? path}〉`).join(documentNameRule.separator)}${documentNameRule.suffix}.xlsx`
                    : "まだ決まっていません"}</strong>
                  <small>〈 〉の部分は、読み取った内容に置き換わります。読み取り後にも変更できます。</small>
                </div>
                {suggestedDocumentNameRule && !sameStringList(suggestedDocumentNameRule.fieldPaths, documentNameRule?.fieldPaths ?? []) && (
                  <button type="button" className="secondary" onClick={() => onDocumentNameRuleChange?.({
                    ...(documentNameRule ?? suggestedDocumentNameRule),
                    fieldPaths: suggestedDocumentNameRule.fieldPaths,
                    separator: documentNameRule?.separator || suggestedDocumentNameRule.separator,
                  })}>
                    確認する欄からファイル名を作る
                  </button>
                )}
                <details className="document-name-details">
                  <summary>ファイル名を細かく変える</summary>
                  <p>ファイル名に入れる欄</p>
                  <div className="document-name-field-options">
                    {identityCandidates.map((candidate) => (
                      <label key={candidate.targetPath}>
                        <input
                          type="checkbox"
                          checked={documentNameRule?.fieldPaths.includes(candidate.targetPath) ?? false}
                          onChange={() => toggleDocumentNamePath(candidate.targetPath)}
                        />
                        {candidate.label}
                      </label>
                    ))}
                  </div>
                  <div className="document-name-format-grid">
                    <label>前に付ける文字<input value={documentNameRule?.prefix ?? ""} maxLength={100} onChange={(event) => updateDocumentNameRule({ prefix: event.target.value })} placeholder="例: 面接カード_" /></label>
                    <label>欄と欄の間の文字<input value={documentNameRule?.separator ?? ""} maxLength={20} onChange={(event) => updateDocumentNameRule({ separator: event.target.value })} placeholder="例: _（空欄でも可）" /></label>
                    <label>後ろに付ける文字<input value={documentNameRule?.suffix ?? ""} maxLength={100} onChange={(event) => updateDocumentNameRule({ suffix: event.target.value })} placeholder="空欄でも可" /></label>
                  </div>
                </details>
                {!documentNameReady && <p className="inline-warning">ファイル名に入れる欄を1つ以上選んでください。</p>}
              </div>
            </div>
          )}
        </aside>
      </div>

      {showMeaningStep && (
        <details className="meaning-target-manager" aria-label="保存する情報を直接指定">
          <summary>
            <div>
              <span className="panel-kicker">保存する情報</span>
              <h3>登録済みの項目から保存先を選ぶ</h3>
            </div>
            <span className="meaning-target-count">
              登録済み {labelPoolEntries.length}項目
            </span>
          </summary>
          <div className="meaning-target-content">
          <p className="meaning-target-description">
            登録済みの項目を確認してから、プレビューで選択した記入欄へ割り当てます。
          </p>
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
          </div>
        </details>
      )}

      {showRecordStep && <section className="record-structure-editor" hidden>
        <div className="record-structure-head">
          <div>
            <h3>繰り返し表の行と列</h3>
            <p>列をまとめ、上からの順番で各行を編成します。帳票上の見出しは任意です。</p>
          </div>
          <div className="record-structure-status">
            <span className={layout.recordStructureVerified ? "structure-verified" : "structure-unverified"}>
              {layout.recordStructureVerified
                ? "繰り返し表を定義済み"
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
          <button
            type="button"
            className="secondary"
            disabled={!recordSectionTitle.trim()}
            onClick={() => {
              const collectionPath = `genericRecords.${genericRecordCollectionKey(recordSectionTitle, recordSections)}`;
              const section: LayoutRecordSection = {
                id: `section_${collectionPath.replace(/[^A-Za-z0-9_]/g, "_")}_${Date.now()}`,
                title: recordSectionTitle.trim(),
                displayName: recordSectionTitle.trim(),
                collectionPath,
                recordType: "generic",
                recordLabelCellIds: [],
                parentLabelCellId: null,
                rowKeyStrategy: "position",
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
              setMessage(`${recordSectionDisplayName(section)}を作成しました。次に「繰り返し部分をまとめる」で、表に含める列ラベルを横方向に横断してください。`);
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
                {recordSectionDisplayName(section, cellById)}
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
                <fieldset className="record-section-name-settings">
                  <legend>繰り返し部分の名前</legend>
                  <div className="record-section-name-choice">
                    <label>
                      <input
                        type="radio"
                        name={`record-section-name-${section.id}`}
                        checked={section.displayName === null}
                        onChange={() => setLayout((current) => ({
                          ...current,
                          recordSections: (current.recordSections ?? []).map((item) => (
                            item.id === section.id
                              ? { ...item, displayName: null, source: "manual" as const }
                              : item
                          )),
                        }))}
                      />
                      名前を付けない
                    </label>
                    <label>
                      <input
                        type="radio"
                        name={`record-section-name-${section.id}`}
                        checked={section.displayName !== null}
                        onChange={() => setLayout((current) => ({
                          ...current,
                          recordSections: (current.recordSections ?? []).map((item) => (
                            item.id === section.id
                              ? {
                                ...item,
                                displayName: item.displayName?.trim()
                                  || item.title.trim()
                                  || "明細",
                                source: "manual" as const,
                              }
                              : item
                          )),
                        }))}
                      />
                      名前を付ける
                    </label>
                  </div>
                  {section.displayName !== null && (
                    <label>名前
                      <input
                        aria-label="繰り返し部分の名前"
                        value={section.displayName ?? section.title}
                        maxLength={200}
                        onChange={(event) => setLayout((current) => ({
                          ...current,
                          recordSections: (current.recordSections ?? []).map((item) => (
                            item.id === section.id
                              ? { ...item, displayName: event.target.value, source: "manual" }
                              : item
                          )),
                        }))}
                        onBlur={(event) => {
                          const displayName = event.target.value.trim();
                          if (!displayName) {
                            setMessage("名前を付ける場合は空欄にできません。名前を入力するか「名前を付けない」を選択してください。");
                          }
                          setLayout((current) => ({
                            ...current,
                            recordSections: (current.recordSections ?? []).map((item) => (
                              item.id === section.id
                                ? {
                                  ...item,
                                  displayName: displayName || item.title.trim() || "明細",
                                  source: "manual" as const,
                                }
                                : item
                            )),
                          }));
                        }}
                      />
                    </label>
                  )}
                </fieldset>
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
                          {cellName(cell)}
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
                      ? "上からの順番で各行を定義しています。帳票上の見出しは任意です。"
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
              <thead><tr><th>順番</th><th>方向</th><th>ラベル</th><th>保存先</th><th /></tr></thead>
              <tbody>
                {layout.relations.map((relation) => {
                  const invalidTarget = !targetByPath.has(relation.targetPath);
                  return (
                  <tr key={relation.id} className={invalidTarget ? "invalid-relation" : ""}>
                    <td>{relation.order}行目</td>
                    <td>{relation.axis === "horizontal" ? "横積み" : "縦積み"}</td>
                    <td>
                      {relation.parentLabelCellId && (
                        <span className="relation-parent-breadcrumb">
                          {cellNameById(relation.parentLabelCellId)}
                          <b aria-hidden="true">›</b>
                        </span>
                      )}
                      {relation.labelCellIds.length > 0
                        ? relation.labelCellIds.map(cellNameById).join(" / ")
                        : cellById.get(relation.inputCellId)?.printedLabelText || "矩形へ直接設定"}
                    </td>
                    <td>{displayName(targetByPath.get(relation.targetPath)?.label ?? "一覧にない保存先")}</td>
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
                  <tr><td colSpan={5}>まだ意味づけされていません。「意味づけ」モードでラベルから記入欄へドラッグしてください。</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {showFinalChecks && <>
      {layout.cells.filter((cell) => incompleteMeaningIds.has(cell.id)).map((cell) => (
        <p className="inline-warning" key={`incomplete-${cell.id}`}>
          設定不完全: {cellName(cell)} — 各選択欄に保存先と1件以上の選択肢を設定してください。
        </p>
      ))}
      {layout.warnings
        .filter((warning) => !HIDDEN_REVIEW_INFORMATION_MESSAGES.has(warning))
        .map((warning) => <p className="inline-warning" key={warning}>{userText(warning, "レイアウトの注意")}</p>)}
      {!recordStructureReady && (
        <p className="inline-warning">
          途中保存できます。OCRへ進むには、複数の列見出しを横断して各行を自動編成してください。旧形式の表では親見出しへの接続も必要です。
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
      {!showIdentityStep && (!identityConfigurationReady || !documentNameReady) && (
        <p className="inline-warning">
          途中保存できます。OCRへ進むには、「確認する欄とファイル名」で、確認する欄とファイル名を決めてください。
        </p>
      )}
      {/* Saving runs in the background; a line that appears and disappears with every
          autosave shifts the page, so only lasting states are shown. */}
      {((dirty && !operationEndSave) || (!dirty && allowUnchangedSave)) && <p className={dirty ? "inline-warning" : "layout-status"} aria-live="polite">
        {dirty
          ? "保存していないレイアウト変更があります。"
          : "検出内容を確認し、意味づけを保存してください。"}
      </p>}
      {message && !workflowStep && <p className="layout-status" aria-live="polite">{message}</p>}
      {(!workflowStep || dirty) && <button
        type="button"
        className="primary layout-save"
        disabled={
          busy
          || saving
          || (!dirty && !allowUnchangedSave)
        }
        onClick={() => void flushLatest()}
      >
        {busy
          ? "処理中…"
          : dirty || allowUnchangedSave
          ? templateReadyForOcr
            ? "テンプレート設定を保存"
            : "レイアウトを途中保存"
          : "レイアウトは保存済み"}
      </button>}
      </>}
      </fieldset>
    </div>
  );
});
function meaningDestinationLabel(target: FieldMappingTarget, siblings: FieldMappingTarget[]) {
  const duplicates = siblings.filter(item => item.label === target.label);
  if (duplicates.length < 2) return target.label;
  const rowLabel = (path: string) => [...path.matchAll(/\.(\d+)(?=\.)/g)].map(match => `${Number(match[1]) + 1}行目`).join("・");
  const row = rowLabel(target.path);
  const uniqueRow = row && duplicates.filter(item => rowLabel(item.path) === row).length === 1;
  return `${target.label}（${uniqueRow ? row : target.path}）`;
}

const MODE_ICONS = {
  select: FiMousePointer, move: FiMousePointer, classify: FiTag, paint: FiTag, draw: FiPlusSquare,
  link: FiLink, record: FiLayers, parent: FiCornerDownRight, "record-parent": FiCornerDownRight,
  erase: FiX, identity: FiSearch, virtual: FiType, "virtual-group": FiGrid,
};

type PaintRole = "label" | "parent_label" | "input" | "mark" | "ignored";
const PAINT_ROLES: readonly (readonly [PaintRole, string, string])[] = [
  ["label", "項目名", "記入欄の名前になる印字"],
  ["parent_label", "見出し", "いくつかの項目をまとめる見出し"],
  ["input", "記入欄", "文字や数字を書き込む欄"],
  ["mark", "選択・記号", "丸・チェック・記号で答える欄"],
  ["ignored", "読み取らない", "読み取りに使わない枠"],
];
const PAINT_ROLE_LABEL: Record<PaintRole, string> = {
  label: "項目名", parent_label: "見出し", input: "記入欄", mark: "選択・記号", ignored: "読み取らない",
};
function paintRoleOf(cell: LayoutCell): PaintRole {
  return cell.role === "record_key_cell" || cell.role === "column_key_cell" ? "input" : cell.role;
}

/** Short descriptions shown when hovering the tools (instead of hints that push the preview down). */
const MODE_TIPS: Partial<Record<Mode, string>> = {
  select: "枠をクリックして選び、右の欄で編集します。ドラッグで範囲選択。",
  classify: "枠をクリックして、記入欄・項目名・見出しなどの区分を変えます。",
  draw: "ドラッグで新しい枠を描きます。続けて描けます（Escで終了）。",
  link: "項目名から記入欄へドラッグして、何を書く欄かを設定します。",
  parent: "見出しから項目名へドラッグして、上の見出しにまとめます。",
  record: "表の列見出しを横にドラッグして、繰り返す行をまとめます。",
  "record-parent": "まとめた繰り返し部分を見出しへドラッグしてつなぎます。",
  move: "仮想見出しの枠をドラッグして動かします。",
  virtual: "帳票にない見出しを作り、枠をまとめます。",
  erase: "なぞった接続線を消します。",
};

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
  const Icon = MODE_ICONS[mode];
  return (
    <button
      type="button"
      data-tip={MODE_TIPS[mode]}
      className={[
        current === mode ? "active" : "",
        "mode-button-with-icon",
      ].filter(Boolean).join(" ")}
      aria-pressed={current === mode}
      onClick={() => onChange(mode)}
    >
      <span className="mode-button-icon" aria-hidden="true">{icon ?? <Icon />}</span>
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
function isEditableKeyboardTarget(target: EventTarget | null): boolean {
  const element = target instanceof HTMLElement ? target : null;
  return Boolean(
    element
    && (element.isContentEditable
      || element instanceof HTMLInputElement
      || element instanceof HTMLTextAreaElement
      || element instanceof HTMLSelectElement),
  );
}
// The latest saved layout wins. A local draft is used only when it was made on
// top of that same saved version; otherwise it is stale and is dropped silently.
function restoreLayoutDraft(
  initialLayout: LayoutDefinition,
  draftStorageKey?: string,
): LayoutDefinition {
  const saved = normalizeLayout(initialLayout);
  if (!draftStorageKey) return saved;
  try {
    const stored = privateLocalStorage.getItem(draftStorageKey);
    if (!stored) return saved;
    const layout = normalizeLayout(JSON.parse(stored) as LayoutDefinition);
    const savedSignature = layoutSignature(saved);
    if (layoutSignature(layout) === savedSignature) return saved;
    if (privateLocalStorage.getItem(`${draftStorageKey}:baseline`) === savedSignature) return layout;
    privateLocalStorage.removeItem(draftStorageKey);
    privateLocalStorage.removeItem(`${draftStorageKey}:baseline`);
    return saved;
  } catch {
    try {
      privateLocalStorage.removeItem(draftStorageKey);
      privateLocalStorage.removeItem(`${draftStorageKey}:baseline`);
    } catch {
      // Ignore storage failures and continue with the server-backed layout.
    }
    return saved;
  }
}
function layoutSignature(layout: LayoutDefinition): string {
  return JSON.stringify(layout);
}
export {
  type Point,
  segmentDistance,
  cellsSelectedByRect,
  segmentIntersectsRect,
} from "./layoutEditorGeometry";
export {
  unassignedMeaningCellIds,
  incompleteMeaningCellIds,
  eraseMeaningParentLinksAlongSegment,
  eraseMeaningLabelLinksAlongSegment,
  createAutoLabelPoolEntry,
  createAutoRepeatedLabelPoolEntries,
  resolveLabelPoolTargetPath,
  buildLabelPoolEntries,
  normalizeLayout,
  meaningLinkLaneCells,
  targetPathsForInputs,
  resolveAvailableLinkTargetPaths,
  inferTargetPath,
  mergeLabelRelations,
  attachParentLabelToMeaningRelations,
} from "./layoutEditorMeaning";
export {
  recordSectionDisplayName,
  recordSectionAtPoint,
  recordRowsFromColumnLabels,
  recordLabelCellsCrossedByLine,
  linkLaneCells,
} from "./layoutEditorRecords";
export { removeLayoutCells, unselectedCellsInsideMerge } from "./layoutEditorMerges";
export { LAYOUT_ROLE_STYLE, drawOcrLabelText } from "./layoutEditorDrawing";

/** Editing tools as a vertical column pinned to a corner of the preview, separate from voice. */
function FloatingTools({ anchor, disabled, header, children }: { anchor: () => HTMLElement | null; disabled: boolean; header?: ReactNode; children: ReactNode }) {
  const { corner, style, dragging, grip, pin, container } = useCornerPin(anchor, "fuminosuke.layoutToolsCorner", "top-left", 0);
  // One description bubble beside the hovered tool, on the side away from the frame edge.
  const [tip, setTip] = useState<{ text: string; top: number; x: number } | null>(null);
  const tipTimer = useRef<number | null>(null);
  const hideTip = () => { if (tipTimer.current) window.clearTimeout(tipTimer.current); tipTimer.current = null; setTip(null); };
  const showTip = (target: EventTarget | null) => {
    const element = target instanceof Element ? target.closest<HTMLElement>("[data-tip]") : null;
    if (!element?.dataset.tip) { hideTip(); return; }
    const text = element.dataset.tip;
    if (tipTimer.current) window.clearTimeout(tipTimer.current);
    tipTimer.current = window.setTimeout(() => {
      const box = element.getBoundingClientRect();
      const panel = element.closest(".layout-floating-tools")?.getBoundingClientRect() ?? box;
      setTip({ text, top: box.top + box.height / 2, x: corner.endsWith("right") ? panel.left - 10 : panel.right + 10 });
    }, 350);
  };
  useEffect(() => () => { if (tipTimer.current) window.clearTimeout(tipTimer.current); }, []);
  return createPortal(
    <div className={`layout-floating-tools is-${corner}${dragging ? " is-dragging" : ""}`} style={style} {...pin}
      onPointerOver={event => showTip(event.target)} onPointerLeave={hideTip} onPointerDown={hideTip}
      onFocus={event => showTip(event.target)} onBlur={hideTip}>
      {tip && !dragging && createPortal(<div className={`layout-tool-tip is-${corner.endsWith("right") ? "left" : "right"}`} role="tooltip"
        style={{ top: tip.top, ...(corner.endsWith("right") ? { right: window.innerWidth - tip.x } : { left: tip.x }) }}>{tip.text}</div>, document.body)}
      <button type="button" className="layout-floating-grip" aria-label="編集ツールの位置を移動（ドラッグ又は矢印キー）"
        title="ドラッグで四隅へ移動" {...grip}><FiMove aria-hidden="true" /></button>
      {header}
      <fieldset className="layout-floating-fields" disabled={disabled}>{children}</fieldset>
    </div>,
    container,
  );
}
