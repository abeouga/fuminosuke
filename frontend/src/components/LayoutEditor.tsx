import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  FieldMappingTarget,
  LayoutCell,
  LayoutDefinition,
  LayoutRecordGroup,
  LayoutRecordSection,
  LayoutRelation,
} from "../types";

type Point = { x: number; y: number };
type Rect = Point & { width: number; height: number };
type Mode = "select" | "classify" | "draw" | "link" | "record" | "parent";

const ROLE_COLOR = {
  label: "#b36b00",
  input: "#00777d",
  ignored: "#6b7280",
} as const;

export function LayoutEditor({
  source,
  initialLayout,
  targets,
  onDetect,
  onSave,
  allowUnchangedSave = false,
  disabled = false,
  onDirtyChange,
  onBusyChange,
}: {
  source: string;
  initialLayout: LayoutDefinition;
  targets: FieldMappingTarget[];
  onDetect: () => Promise<LayoutDefinition>;
  onSave: (layout: LayoutDefinition) => Promise<LayoutDefinition>;
  allowUnchangedSave?: boolean;
  disabled?: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragStart = useRef<Point | null>(null);
  const linkLabelId = useRef<string | null>(null);
  const relationHistory = useRef<LayoutRelation[][]>([]);
  const operationLockRef = useRef(false);
  const [layout, setLayout] = useState(() => normalizeLayout(initialLayout));
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
  const [message, setMessage] = useState<string | null>(null);
  const [historyDepth, setHistoryDepth] = useState(0);
  const [customTargets, setCustomTargets] = useState<FieldMappingTarget[]>([]);
  const [customLabel, setCustomLabel] = useState("");
  const [customKey, setCustomKey] = useState("");
  const [recordSectionTitle, setRecordSectionTitle] = useState("売却子牛一覧");
  const [recordCollectionPath, setRecordCollectionPath] = useState<"saleCalves" | "calvings">("saleCalves");
  const [selectedRecordSectionId, setSelectedRecordSectionId] = useState("");
  const [manualRect, setManualRect] = useState<Rect>({
    x: 0,
    y: 0,
    width: 120,
    height: 40,
  });
  const [accessibleLabelId, setAccessibleLabelId] = useState("");
  const [accessibleInputId, setAccessibleInputId] = useState("");
  const [accessibleRecordInputIds, setAccessibleRecordInputIds] = useState<Set<string>>(
    () => new Set(),
  );
  const currentSignature = useMemo(() => layoutSignature(layout), [layout]);
  const dirty = currentSignature !== baselineSignature;
  const dirtyRef = useRef(dirty);

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
  const selected = layout.cells.find((cell) => cell.id === selectedId) ?? null;
  useEffect(() => {
    if (!selected || selected.role !== "input") return;
    const relation = layout.relations.find(
      (item) => item.inputCellId === selected.id,
    );
    setSelectedPath(relation?.targetPath ?? "");
  }, [selectedId, selected?.role, layout.relations]);
  const selectedCells = layout.cells.filter((cell) => selectedIds.has(cell.id));
  const recordSections = layout.recordSections ?? [];
  const recordGroups = layout.recordGroups ?? [];
  const recordSectionById = useMemo(
    () => new Map(recordSections.map((section) => [section.id, section])),
    [recordSections],
  );
  const availableTargets = useMemo(() => {
    const byPath = new Map(targets.map((target) => [target.path, target]));
    customTargets.forEach((target) => {
      if (!byPath.has(target.path)) byPath.set(target.path, target);
    });
    return [...byPath.values()];
  }, [targets, customTargets]);
  const targetByPath = useMemo(
    () => new Map(availableTargets.map((target) => [target.path, target])),
    [availableTargets],
  );
  const cellById = useMemo(
    () => new Map(layout.cells.map((cell) => [cell.id, cell])),
    [layout.cells],
  );
  const assignedPaths = useMemo(
    () => new Set(layout.relations.map((relation) => relation.targetPath)),
    [layout.relations],
  );
  const sections = useMemo(() => {
    const result = new Map<string, FieldMappingTarget[]>();
    availableTargets.forEach((target) => result.set(
      target.section,
      [...(result.get(target.section) ?? []), target],
    ));
    return [...result.entries()];
  }, [availableTargets]);

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
      setMessage(`${detected.cells.length}個のセル候補を検出しました。色と文字を確認してください。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "セルを検出できませんでした。");
    } finally {
      setDetecting(false);
      setBusy(false);
      operationLockRef.current = false;
    }
  };

  const save = async () => {
    if (disabled || operationLockRef.current) return;
    operationLockRef.current = true;
    setBusy(true);
    try {
      const saved = normalizeLayout(await onSave(layout));
      setLayout(saved);
      setBaselineSignature(layoutSignature(saved));
      relationHistory.current = [];
      setHistoryDepth(0);
      setMessage(`テンプレートへ${saved.relations.length}件の意味づけを保存しました。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "レイアウトを保存できませんでした。");
    } finally {
      setBusy(false);
      operationLockRef.current = false;
    }
  };

  const redraw = () => {
    const canvas = canvasRef.current;
    const image = imageRef.current;
    if (!canvas || !image) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    recordGroups.forEach((group) => {
      const section = recordSectionById.get(group.sectionId);
      const members = group.inputCellIds
        .map((inputId) => cellById.get(inputId))
        .filter((cell): cell is LayoutCell => Boolean(cell))
        .sort((first, second) => first.x - second.x || first.y - second.y);
      if (members.length === 0) return;
      context.save();
      context.strokeStyle = group.source === "manual" ? "#2468a2" : "#7a8792";
      context.fillStyle = context.strokeStyle;
      context.lineWidth = 7;
      if (group.source === "inferred") context.setLineDash([12, 7]);
      context.beginPath();
      members.forEach((member, index) => {
        const centerX = member.x + member.width / 2;
        const centerY = member.y + member.height / 2;
        if (index === 0) context.moveTo(centerX, centerY);
        else context.lineTo(centerX, centerY);
      });
      context.stroke();
      context.setLineDash([]);
      members.forEach((member) => {
        context.beginPath();
        context.arc(
          member.x + member.width / 2,
          member.y + member.height / 2,
          7,
          0,
          Math.PI * 2,
        );
        context.fill();
      });
      context.fillStyle = "#2468a2";
      context.font = `700 ${Math.max(14, layout.canonicalWidth / 90)}px sans-serif`;
      context.fillText(
        `${section?.title ?? "レコード"} · ${group.recordKeyValue ?? group.order}`,
        members[0].x,
        Math.max(18, members[0].y - 7),
      );
      context.restore();
    });

    recordSections.forEach((section) => {
      if (!section.parentLabelCellId) return;
      const parent = cellById.get(section.parentLabelCellId);
      if (!parent) return;
      recordGroups
        .filter((group) => group.sectionId === section.id)
        .forEach((group) => {
          context.save();
          context.strokeStyle = "#16805d";
          context.lineWidth = 5;
          context.setLineDash([13, 7]);
          context.beginPath();
          context.moveTo(parent.x + parent.width / 2, parent.y + parent.height / 2);
          context.lineTo(group.x + group.width / 2, group.y + group.height / 2);
          context.stroke();
          context.restore();
        });
    });

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
      if (cell.role === "label" && cell.text) {
        const fontSize = Math.max(14, layout.canonicalWidth / 85);
        context.font = `700 ${fontSize}px sans-serif`;
        const label = cell.text.slice(0, 28);
        const width = Math.min(context.measureText(label).width + 12, cell.width);
        context.fillStyle = color;
        context.fillRect(cell.x, cell.y, width, fontSize + 8);
        context.fillStyle = "white";
        context.fillText(label, cell.x + 6, cell.y + fontSize + 1);
      }
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
    if (mode === "record" && linkPoint && dragStart.current) {
      const start = dragStart.current;
      const highlighted = linkLaneCells(
        start,
        linkPoint,
        layout.cells.filter((cell) => cell.role === "input"),
      );
      context.save();
      context.strokeStyle = "#2468a2";
      context.fillStyle = "#2468a230";
      context.lineWidth = 7;
      context.beginPath();
      context.moveTo(start.x, start.y);
      context.lineTo(linkPoint.x, linkPoint.y);
      context.stroke();
      highlighted.forEach((cell) => {
        context.fillRect(cell.x, cell.y, cell.width, cell.height);
        context.strokeRect(cell.x, cell.y, cell.width, cell.height);
      });
      context.restore();
    }
    if (linkPoint && linkLabelId.current) {
      const label = cellById.get(linkLabelId.current);
      if (label) {
        const start = {
          x: label.x + label.width / 2,
          y: label.y + label.height / 2,
        };
        const highlightedCells = mode === "parent"
          ? recordGroups
            .filter((group) => pointInsideRect(linkPoint, group))
            .flatMap((group) => group.inputCellIds)
            .map((inputId) => cellById.get(inputId))
            .filter((cell): cell is LayoutCell => Boolean(cell))
          : linkLaneCells(
            start,
            linkPoint,
            layout.cells.filter((cell) => cell.role === "input"),
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

  const point = (event: React.PointerEvent<HTMLCanvasElement>): Point => {
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
  const setRoles = (ids: Iterable<string>, role: LayoutCell["role"]) => {
    const idSet = new Set(ids);
    if (idSet.size === 0) return;
    setLayout((current) => {
      let relations = current.relations;
      if (role !== "input") {
        relations = relations.filter((relation) => !idSet.has(relation.inputCellId));
      }
      if (role !== "label") {
        relations = relations.map((relation) => ({
          ...relation,
          labelCellIds: relation.labelCellIds.filter((labelId) => !idSet.has(labelId)),
        }));
      }
      const recordGroups = role === "input"
        ? current.recordGroups
        : (current.recordGroups ?? [])
          .map((group) => ({
            ...group,
            inputCellIds: group.inputCellIds.filter((inputId) => !idSet.has(inputId)),
          }))
          .filter((group) => group.inputCellIds.length > 0);
      return {
        ...current,
        cells: current.cells.map((cell) => idSet.has(cell.id) ? { ...cell, role } : cell),
        relations,
        recordGroups,
      };
    });
  };
  const setRole = (id: string, role: LayoutCell["role"]) => setRoles([id], role);
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
  const addManualCell = () => {
    const width = Math.max(8, Math.min(manualRect.width, layout.canonicalWidth));
    const height = Math.max(8, Math.min(manualRect.height, layout.canonicalHeight));
    const x = Math.max(0, Math.min(manualRect.x, layout.canonicalWidth - width));
    const y = Math.max(0, Math.min(manualRect.y, layout.canonicalHeight - height));
    const id = `manual_${Date.now()}`;
    setLayout((current) => ({
      ...current,
      cells: [...current.cells, {
        id,
        role: "input",
        x,
        y,
        width,
        height,
        confidence: 1,
        text: "",
        textConfidence: null,
        source: "manual",
      }],
    }));
    setSelectedId(id);
    setSelectedIds(new Set([id]));
    setMessage(`${id} を手書き文字欄として追加しました。`);
  };
  const addAccessibleRelation = () => {
    const label = cellById.get(accessibleLabelId);
    const input = cellById.get(accessibleInputId);
    if (!label || label.role !== "label" || !input || input.role !== "input") return;
    const targetPath = selectedPath
      || inferTargetPath(label.text, 1, availableTargets, assignedPaths);
    if (!targetPath || !targetByPath.has(targetPath)) {
      setMessage("先に「JSON項目」で保存先を選択してください。");
      return;
    }
    const labelCenter = {
      x: label.x + label.width / 2,
      y: label.y + label.height / 2,
    };
    const inputCenter = {
      x: input.x + input.width / 2,
      y: input.y + input.height / 2,
    };
    const axis: LayoutRelation["axis"] = (
      Math.abs(inputCenter.x - labelCenter.x)
      >= Math.abs(inputCenter.y - labelCenter.y)
    ) ? "horizontal" : "vertical";
    rememberRelations(layout.relations);
    setLayout((current) => ({
      ...current,
      relations: mergeLabelRelations(
        current.relations,
        [input],
        label.id,
        axis,
        [targetPath],
      ),
    }));
    setSelectedId(input.id);
    setSelectedIds(new Set([input.id]));
    setMessage(`${label.text || label.id} と ${input.id} を ${targetByPath.get(targetPath)?.label ?? targetPath} へ意味づけしました。`);
  };
  const addAccessibleRecordGroup = () => {
    const section = recordSectionById.get(selectedRecordSectionId);
    const inputs = layout.cells.filter(
      (cell) => cell.role === "input" && accessibleRecordInputIds.has(cell.id),
    );
    if (!section || inputs.length === 0) return;
    const order = Math.max(
      0,
      ...recordGroups
        .filter((group) => group.sectionId === section.id)
        .map((group) => group.order),
    ) + 1;
    const inputIds = new Set(inputs.map((input) => input.id));
    const group: LayoutRecordGroup = {
      id: `record_${section.collectionPath}_${Date.now()}`,
      sectionId: section.id,
      order,
      ...boundingRect(inputs, layout.canonicalWidth, layout.canonicalHeight),
      inputCellIds: inputs.map((input) => input.id),
      recordKeyField: "parity",
      recordKeyValue: String(order),
      source: "manual",
    };
    setLayout((current) => ({
      ...current,
      layoutSchemaVersion: 3,
      recordGroups: [
        ...(current.recordGroups ?? [])
          .map((item) => ({
            ...item,
            inputCellIds: item.inputCellIds.filter((id) => !inputIds.has(id)),
          }))
          .filter((item) => item.inputCellIds.length > 0),
        group,
      ],
      relations: current.relations.map((relation) => {
        if (!inputIds.has(relation.inputCellId)) return relation;
        const fieldKey = relation.targetPath.split(".").at(-1);
        const targetPath = fieldKey
          ? `${section.collectionPath}.${order - 1}.${fieldKey}`
          : relation.targetPath;
        return targetByPath.has(targetPath)
          ? { ...relation, targetPath, order }
          : relation;
      }),
      recordStructureVerified: false,
    }));
    setAccessibleRecordInputIds(new Set());
    setMessage(`${section.title}の${order}番目のレコードへ${inputs.length}セルをまとめました。内容を確認してください。`);
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
    const resolvedEnd = endCell?.role === "input"
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
      layout.cells.filter((cell) => cell.role === "input"),
    );
    if (inputs.length === 0) {
      setMessage("スライド経路に手書き文字欄がありません。記入欄を通過するようにドラッグしてください。");
      return;
    }
    const existingTargetPaths = targetPathsForInputs(inputs, layout.relations);
    const targetPath = selectedPath
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
    const groupedTargetPaths = targetPath
      ? targetPathsFromRecordGroups(
        targetPath,
        inputs,
        recordGroups,
        recordSections,
        availableTargets,
      )
      : null;
    const targetPaths = selectedPath
      ? groupedTargetPaths
        ?? expandTargetPaths(selectedPath, inputs.length, availableTargets)
      : existingTargetPaths
        ?? groupedTargetPaths
        ?? (targetPath ? expandTargetPaths(targetPath, inputs.length, availableTargets) : null);
    if (!targetPaths) {
      const capacity = targetPath
        ? sequentialTargetCapacity(targetPath, availableTargets)
        : null;
      setMessage(
        !targetPath
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
      return { ...current, relations, allowCrossAxisLabels: true };
    });
    setSelectedId(inputs[0].id);
    setSelectedIds(new Set(inputs.map((input) => input.id)));
    setSelectedPath("");
    setMessage(
      `${label.text || label.id}から${inputs.length}個の手書き文字欄へ、`
      + `${axis === "horizontal" ? "横方向" : "縦方向"}にまとめて意味づけしました。`
      + ` 保存先: ${targetByPath.get(targetPaths[0])?.label ?? targetPaths[0]}`,
    );
  };

  const finishRecordLine = (start: Point, end: Point) => {
    const inputs = linkLaneCells(
      start,
      end,
      layout.cells.filter((cell) => cell.role === "input"),
    );
    if (inputs.length === 0) {
      setMessage("線が通過した手書き文字欄がありません。1行目のセルから最終セルまで線を引いてください。");
      return;
    }
    const inferredCollectionPath = inferRecordCollectionPath(
      inputs,
      layout.relations,
    );
    const selectedSection = recordSectionById.get(selectedRecordSectionId);
    const matchingSection = inferredCollectionPath
      ? recordSections.find(
        (candidate) => candidate.collectionPath === inferredCollectionPath,
      )
      : undefined;
    const existingSection = selectedSection && (
      !inferredCollectionPath
      || selectedSection.collectionPath === inferredCollectionPath
    )
      ? selectedSection
      : matchingSection;
    const collectionPath = existingSection?.collectionPath
      ?? inferredCollectionPath
      ?? recordCollectionPath;
    const section: LayoutRecordSection = existingSection ?? {
      id: `section_${collectionPath}_${Date.now()}`,
      title: collectionPath === "saleCalves" ? "売却子牛一覧" : "繁殖履歴",
      collectionPath,
      recordType: collectionPath === "saleCalves" ? "saleCalf" : "calving",
      parentLabelCellId: null,
      source: "manual",
    };
    const inputIds = new Set(inputs.map((input) => input.id));
    const previousGroup = recordGroups.find((group) => (
      group.sectionId === section.id
      && group.inputCellIds.some((inputId) => inputIds.has(inputId))
    ));
    const order = previousGroup?.order ?? Math.max(
      0,
      ...recordGroups
        .filter((group) => group.sectionId === section.id)
        .map((group) => group.order),
    ) + 1;
    const group: LayoutRecordGroup = {
      id: previousGroup?.id ?? `record_${section.collectionPath}_${Date.now()}`,
      sectionId: section.id,
      order,
      ...boundingRect(inputs, layout.canonicalWidth, layout.canonicalHeight),
      inputCellIds: inputs.map((input) => input.id),
      recordKeyField: "parity",
      recordKeyValue: previousGroup?.recordKeyValue ?? String(order),
      source: "manual",
    };
    setLayout((current) => ({
      ...current,
      layoutSchemaVersion: 3,
      recordSections: (current.recordSections ?? []).some(
        (candidate) => candidate.id === section.id,
      )
        ? current.recordSections
        : [...(current.recordSections ?? []), section],
      recordGroups: [
        ...(current.recordGroups ?? [])
          .filter((item) => item.id !== group.id)
          .map((item) => ({
            ...item,
            inputCellIds: item.inputCellIds.filter((id) => !inputIds.has(id)),
          }))
          .filter((item) => item.inputCellIds.length > 0),
        group,
      ],
      relations: current.relations.map((relation) => {
        if (!inputIds.has(relation.inputCellId)) return relation;
        const fieldKey = relation.targetPath.split(".").at(-1);
        const targetPath = fieldKey
          ? `${section.collectionPath}.${order - 1}.${fieldKey}`
          : relation.targetPath;
        return targetByPath.has(targetPath)
          ? { ...relation, targetPath, order }
          : relation;
      }),
      recordStructureVerified: true,
    }));
    setSelectedRecordSectionId(section.id);
    setSelectedIds(inputIds);
    setSelectedId(inputs[0].id);
    setMessage(
      `${existingSection ? "" : `${section.title}を自動作成し、`}`
      + `${section.title}の${order}番目のレコードとして、`
      + `${inputs.length}個の記入欄をまとめました。`,
    );
  };

  const finishParentLink = (end: Point) => {
    const labelId = linkLabelId.current;
    linkLabelId.current = null;
    setLinkPoint(null);
    if (!labelId) return;
    const group = [...recordGroups].reverse().find((item) => pointInsideRect(end, item));
    if (!group) {
      setMessage("親ラベルから、青いレコード線上のセルまで線を引いてください。");
      return;
    }
    const label = cellById.get(labelId);
    setLayout((current) => ({
      ...current,
      recordSections: (current.recordSections ?? []).map((section) => (
        section.id === group.sectionId
          ? {
            ...section,
            parentLabelCellId: labelId,
            title: section.title.trim() || label?.text.trim() || section.title,
            source: "manual",
          }
          : section
      )),
      recordStructureVerified: true,
    }));
    setSelectedRecordSectionId(group.sectionId);
    setMessage(
      `${label?.text || labelId}を`
      + `${recordSectionById.get(group.sectionId)?.title ?? "レコード一覧"}の親ラベルに設定しました。`,
    );
  };

  const roleCounts = {
    label: layout.cells.filter((cell) => cell.role === "label").length,
    input: layout.cells.filter((cell) => cell.role === "input").length,
    ignored: layout.cells.filter((cell) => cell.role === "ignored").length,
  };
  const arrayInputIds = new Set(
    layout.relations
      .filter((relation) => /^(saleCalves|calvings)\.\d+\./.test(relation.targetPath))
      .map((relation) => relation.inputCellId),
  );
  const groupedInputIds = new Set(
    recordGroups.flatMap((group) => group.inputCellIds),
  );
  const recordStructureReady = arrayInputIds.size === 0 || (
    layout.recordStructureVerified === true
    && [...arrayInputIds].every((inputId) => groupedInputIds.has(inputId))
  );

  return (
    <div className="layout-editor" aria-busy={busy}>
      <fieldset className="layout-editor-fields" disabled={busy || disabled}>
      <div className="layout-commandbar" role="toolbar" aria-label="レイアウト編集">
        <button type="button" onClick={() => void detect()} disabled={busy}>
          {busy ? "検出中…" : "枠と文字を再検出"}
        </button>
        <span className="command-separator" />
        <ModeButton current={mode} mode="select" label="選択" onChange={setMode} />
        <ModeButton current={mode} mode="classify" label="区分変更" onChange={setMode} />
        <ModeButton current={mode} mode="draw" label="矩形追加" onChange={setMode} />
        <ModeButton current={mode} mode="link" label="意味づけ" onChange={setMode} />
        <ModeButton current={mode} mode="record" label="レコード作成" onChange={setMode} />
        <ModeButton current={mode} mode="parent" label="親ラベル接続" onChange={setMode} />
        <button
          type="button"
          disabled={historyDepth === 0}
          title="Ctrl+Z"
          onClick={undoMeaning}
        >
          ↶ 意味づけを戻す
        </button>
        <span className="command-hint">同じ記入欄への複数ラベル対応</span>
      </div>

      <div className="layout-instructions">
        {mode === "select" && "クリックで1セル、ドラッグで矩形範囲の複数セルを選び、右側で区分を一括変更できます。"}
        {mode === "classify" && "クリックで「ラベル → 手書き文字欄 → 除外」を切替。ドラッグではセルの中心が範囲内にある矩形をまとめて選択し、右側で一括指定できます。"}
        {mode === "draw" && "検出されなかった手書き文字欄をドラッグして追加します。"}
        {mode === "link" && "ラベルセルから手書き文字欄を通過するようにドラッグします。経路上の全記入欄へ、選択した保存先から行番号を進めて一括設定します。"}
        {mode === "record" && "同じ行の端にある記入欄から反対端まで線を引きます。線が通過した価格・種雄牛名などが同じ産次として固定されます。"}
        {mode === "parent" && "「売却子牛一覧」などの親ラベルセルから、所属させるレコード線上のセルまで線を引きます。"}
      </div>

      <div className="layout-workbench">
        <div className="mapping-canvas-wrap" aria-busy={detecting}>
          <canvas
            ref={canvasRef}
            width={layout.canonicalWidth}
            height={layout.canonicalHeight}
            tabIndex={0}
            aria-label="帳票レイアウト編集キャンバス"
            aria-describedby="layout-keyboard-help"
            className={`layout-mode-${mode}`}
            onKeyDown={(event) => {
              if (busy || disabled || layout.cells.length === 0) return;
              if (!["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) return;
              event.preventDefault();
              const currentIndex = layout.cells.findIndex((cell) => cell.id === selectedId);
              const delta = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
              const nextIndex = currentIndex < 0
                ? 0
                : (currentIndex + delta + layout.cells.length) % layout.cells.length;
              const nextId = layout.cells[nextIndex].id;
              setSelectedId(nextId);
              setSelectedIds(new Set([nextId]));
            }}
            onPointerDown={(event) => {
              if (busy || disabled) return;
              const location = point(event);
              const hit = cellAt(location);
              if (mode === "select" || mode === "classify") {
                event.currentTarget.setPointerCapture?.(event.pointerId);
                dragStart.current = location;
                setDraft({ ...location, width: 0, height: 0 });
                return;
              }
              if (mode === "record") {
                if (!hit || hit.role !== "input") {
                  setMessage("レコード作成は、行の端にある手書き文字欄から線を引き始めてください。");
                  return;
                }
                event.currentTarget.setPointerCapture?.(event.pointerId);
                dragStart.current = {
                  x: hit.x + hit.width / 2,
                  y: hit.y + hit.height / 2,
                };
                setLinkPoint(location);
                return;
              }
              if (mode === "link" || mode === "parent") {
                const labelHit = [...layout.cells].reverse().find((cell) => (
                  cell.role === "label"
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
              const location = point(event);
              if (
                (mode === "draw" || mode === "select" || mode === "classify")
                && dragStart.current
              ) {
                setDraft(rectFrom(dragStart.current, location));
              }
              if (mode === "record" && dragStart.current) setLinkPoint(location);
              if ((mode === "link" || mode === "parent") && linkLabelId.current) setLinkPoint(location);
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
              if (mode === "parent") {
                finishParentLink(location);
                return;
              }
              if (mode === "record" && dragStart.current) {
                const start = dragStart.current;
                const endCell = cellAt(location);
                const end = endCell?.role === "input"
                  ? {
                    x: endCell.x + endCell.width / 2,
                    y: endCell.y + endCell.height / 2,
                  }
                  : location;
                dragStart.current = null;
                setLinkPoint(null);
                if (Math.hypot(end.x - start.x, end.y - start.y) >= 8) {
                  finishRecordLine(start, end);
                }
                return;
              }
              if ((mode === "select" || mode === "classify") && dragStart.current) {
                const start = dragStart.current;
                dragStart.current = null;
                setDraft(null);
                const next = rectFrom(start, location);
                if (Math.hypot(next.width, next.height) < 8) {
                  const hit = cellAt(location);
                  if (!hit) {
                    setSelectedId(null);
                    setSelectedIds(new Set());
                    return;
                  }
                  if (mode === "classify") {
                    const role = hit.role === "label"
                      ? "input"
                      : hit.role === "input"
                      ? "ignored"
                      : "label";
                    setRole(hit.id, role);
                  }
                  setSelectedId(hit.id);
                  setSelectedIds(new Set([hit.id]));
                  return;
                }
                const ids = cellsSelectedByRect(layout.cells, next).map((cell) => cell.id);
                setSelectedId(ids[0] ?? null);
                setSelectedIds(new Set(ids));
                setMessage(ids.length > 0
                  ? `${ids.length} 個のセルをまとめて選択しました。右側で区分を一括変更できます。`
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
              linkLabelId.current = null;
              setDraft(null);
              setLinkPoint(null);
            }}
          />
          <span id="layout-keyboard-help" className="sr-only">
            矢印キーでセルを順番に選択できます。右側のセル一覧、座標入力、意味づけ一覧からも全操作を行えます。
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
          <h3>セル情報</h3>
          <div className="cell-counts">
            <span className="count-label">ラベル {roleCounts.label}</span>
            <span className="count-input">手書き文字欄 {roleCounts.input}</span>
            <span>除外 {roleCounts.ignored}</span>
          </div>
          <label className="accessible-cell-select">
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
              {layout.cells.map((cell, index) => (
                <option key={cell.id} value={cell.id}>
                  {index + 1}. {cell.role === "label" ? "ラベル" : cell.role === "input" ? "記入欄" : "除外"} · {cell.text || cell.id}
                </option>
              ))}
            </select>
          </label>
          {!selected && <p className="empty-property">帳票上の矩形を選択してください。</p>}
          {selectedCells.length > 1 && (
            <section className="bulk-selection">
              <h3>範囲選択: {selectedCells.length} セル</h3>
              <p>区分変更モードでドラッグしたセルです。区分を一括で指定できます。</p>
              <div className="role-buttons">
                <button type="button" className="label-role" onClick={() => setRoles(selectedIds, "label")}>ラベルにする</button>
                <button type="button" className="input-role" onClick={() => setRoles(selectedIds, "input")}>記入欄にする</button>
                <button type="button" onClick={() => setRoles(selectedIds, "ignored")}>無視する</button>
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
          {selected && (
            <div className="property-form">
              <label>セルID<input value={selected.id} disabled /></label>
              <fieldset>
                <legend>区分</legend>
                <div className="role-buttons">
                  <button className={selected.role === "label" ? "active label-role" : ""} onClick={() => setRole(selected.id, "label")}>ラベル</button>
                  <button className={selected.role === "input" ? "active input-role" : ""} onClick={() => setRole(selected.id, "input")}>手書き文字欄</button>
                  <button className={selected.role === "ignored" ? "active" : ""} onClick={() => setRole(selected.id, "ignored")}>除外</button>
                </div>
              </fieldset>
              <label>OCRラベル文字
                <input
                  value={selected.text}
                  disabled={selected.role !== "label"}
                  placeholder="自動検出した文字を修正"
                  onChange={(event) => patchCell(selected.id, { text: event.target.value })}
                />
              </label>
              {selected.role === "input" && (
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
              <fieldset className="geometry-editor">
                <legend>位置と大きさ</legend>
                {([
                  ["x", "X", layout.canonicalWidth - selected.width],
                  ["y", "Y", layout.canonicalHeight - selected.height],
                  ["width", "幅", layout.canonicalWidth - selected.x],
                  ["height", "高さ", layout.canonicalHeight - selected.y],
                ] as const).map(([key, label, maximum]) => (
                  <label key={key}>
                    {label}
                    <input
                      type="number"
                      min={key === "width" || key === "height" ? 8 : 0}
                      max={Math.max(key === "width" || key === "height" ? 8 : 0, maximum)}
                      value={selected[key]}
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        if (!Number.isFinite(value)) return;
                        const minimum = key === "width" || key === "height" ? 8 : 0;
                        patchCell(selected.id, {
                          [key]: Math.round(Math.max(minimum, Math.min(value, maximum))),
                        });
                      }}
                    />
                  </label>
                ))}
                <small>検出確度 {Math.round(selected.confidence * 100)}%</small>
              </fieldset>
              <button
                className="danger-button compact"
                type="button"
                onClick={() => {
                  setLayout((current) => ({
                    ...current,
                    cells: current.cells.filter((cell) => cell.id !== selected.id),
                    relations: current.relations.filter((relation) => (
                      relation.inputCellId !== selected.id
                      && !relation.labelCellIds.includes(selected.id)
                    )),
                    recordGroups: (current.recordGroups ?? [])
                      .map((group) => ({
                        ...group,
                        inputCellIds: group.inputCellIds.filter((id) => id !== selected.id),
                      }))
                      .filter((group) => group.inputCellIds.length > 0),
                    recordSections: (current.recordSections ?? []).map((section) => (
                      section.parentLabelCellId === selected.id
                        ? { ...section, parentLabelCellId: null }
                        : section
                    )),
                  }));
                  setSelectedId(null);
                  setSelectedIds((current) => {
                    const next = new Set(current);
                    next.delete(selected.id);
                    return next;
                  });
                }}
              >
                矩形を完全に削除
              </button>
            </div>
          )}

          <div className="accessible-layout-adder">
            <strong>座標を指定して記入欄を追加</strong>
            <div>
              {(["x", "y", "width", "height"] as const).map((key) => (
                <label key={key}>
                  {{ x: "X", y: "Y", width: "幅", height: "高さ" }[key]}
                  <input
                    type="number"
                    min={key === "width" || key === "height" ? 8 : 0}
                    max={key === "x" || key === "width" ? layout.canonicalWidth : layout.canonicalHeight}
                    value={manualRect[key]}
                    onChange={(event) => setManualRect((current) => ({
                      ...current,
                      [key]: Math.max(0, Number(event.target.value) || 0),
                    }))}
                  />
                </label>
              ))}
            </div>
            <button type="button" className="secondary compact" onClick={addManualCell}>
              記入欄を追加
            </button>
          </div>

          <h3>意味づけの保存先</h3>
          <label className="property-select">
            JSON項目
            <select
              value={selectedPath}
              onChange={(event) => {
                const path = event.target.value;
                setSelectedPath(path);
                if (selected?.role === "input" && path) {
                  assignSelectedInputDirectly(path);
                }
              }}
            >
              <option value="">ラベル文字から自動推定</option>
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
            {targetByPath.get(selectedPath)?.label ?? "ラベル文字から保存先を自動推定"}して、次に意味づけした記入欄を保存します。
          </p>
          {selected?.role === "input" && (
            <div className="direct-meaning-editor">
              <strong>選択中の矩形へ直接意味づけ</strong>
              <span>
                別のラベル矩形がない欄や、ラベルと手書き欄が一つの矩形に混在する場合に使用します。
              </span>
              <button
                type="button"
                className="secondary compact"
                disabled={!selectedPath}
                onClick={() => assignSelectedInputDirectly()}
              >
                選択した保存先をこの矩形に設定
              </button>
            </div>
          )}
          <div className="accessible-link-editor">
            <strong>一覧から意味づけ</strong>
            <label>
              ラベルセル
              <select value={accessibleLabelId} onChange={(event) => setAccessibleLabelId(event.target.value)}>
                <option value="">選択してください</option>
                {layout.cells.filter((cell) => cell.role === "label").map((cell) => (
                  <option key={cell.id} value={cell.id}>{cell.text || cell.id}</option>
                ))}
              </select>
            </label>
            <label>
              手書き文字欄
              <select value={accessibleInputId} onChange={(event) => setAccessibleInputId(event.target.value)}>
                <option value="">選択してください</option>
                {layout.cells.filter((cell) => cell.role === "input").map((cell) => (
                  <option key={cell.id} value={cell.id}>{cell.id}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="secondary compact"
              disabled={!accessibleLabelId || !accessibleInputId}
              onClick={addAccessibleRelation}
            >
              選んだ2セルを意味づけ
            </button>
          </div>
          <div className="custom-target-adder">
            <strong>JSON項目にないラベルを追加</strong>
            <label>
              表示名
              <input
                value={customLabel}
                maxLength={100}
                placeholder="例: 治療歴"
                onChange={(event) => setCustomLabel(event.target.value)}
              />
            </label>
            <label>
              JSONキー
              <input
                value={customKey}
                maxLength={100}
                placeholder="例: treatmentHistory"
                onChange={(event) => setCustomKey(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="secondary compact"
              disabled={!customLabel.trim() || !customKey.trim()}
              onClick={() => {
                const key = normalizeCustomKey(customKey);
                if (!key) {
                  setMessage("JSONキーには空白、ピリオド、角括弧を使用できません。");
                  return;
                }
                const path = `customFields.${key}`;
                if (targetByPath.has(path)) {
                  setSelectedPath(path);
                  setMessage(`${path} は追加済みです。`);
                  return;
                }
                const target = {
                  path,
                  label: customLabel.trim(),
                  section: "追加項目",
                };
                setCustomTargets((current) => [...current, target]);
                setSelectedPath(path);
                setCustomLabel("");
                setCustomKey("");
                setMessage(`${target.label} を ${path} として追加しました。ラベルから記入欄へ意味づけしてください。`);
              }}
            >
              項目を追加
            </button>
            <small>追加項目は結果JSONの customFields 配下へ保存されます。</small>
          </div>
        </aside>
      </div>

      <section className="record-structure-editor">
        <div className="record-structure-head">
          <div>
            <span className="eyebrow">RECORD STRUCTURE</span>
            <h3>親セクションと行レコード</h3>
            <p>同じ行の価格・種雄牛名などを1レコードとして束ね、さらに親の一覧名へ接続します。</p>
          </div>
          <div className="record-structure-status">
            <span className={layout.recordStructureVerified ? "structure-verified" : "structure-unverified"}>
              {layout.recordStructureVerified ? "構造確認済み" : "構造未確認"}
            </span>
            {!layout.recordStructureVerified && recordGroups.length > 0 && (
              <button
                type="button"
                className="secondary compact"
                onClick={() => {
                  setLayout((current) => ({
                    ...current,
                    recordStructureVerified: true,
                  }));
                  setMessage("表示されている親セクション・行順・産次キーを確認済みにしました。");
                }}
              >
                このレコード構造を確認
              </button>
            )}
          </div>
        </div>
        <div className="record-section-adder">
          <label>親セクション名
            <input
              value={recordSectionTitle}
              maxLength={200}
              placeholder="例: 売却子牛一覧"
              onChange={(event) => setRecordSectionTitle(event.target.value)}
            />
          </label>
          <label>レコードの保存先
            <select
              value={recordCollectionPath}
              onChange={(event) => {
                const collection = event.target.value as "saleCalves" | "calvings";
                setRecordCollectionPath(collection);
                setRecordSectionTitle(collection === "saleCalves" ? "売却子牛一覧" : "繁殖履歴");
              }}
            >
              <option value="saleCalves">売却子牛レコード</option>
              <option value="calvings">繁殖履歴レコード</option>
            </select>
          </label>
          <button
            type="button"
            className="secondary"
            disabled={!recordSectionTitle.trim()}
            onClick={() => {
              const existing = recordSections.find(
                (section) => section.collectionPath === recordCollectionPath,
              );
              if (existing) {
                setSelectedRecordSectionId(existing.id);
                setMessage(`${existing.title}は作成済みです。`);
                return;
              }
              const section: LayoutRecordSection = {
                id: `section_${recordCollectionPath}_${Date.now()}`,
                title: recordSectionTitle.trim(),
                collectionPath: recordCollectionPath,
                recordType: recordCollectionPath === "saleCalves" ? "saleCalf" : "calving",
                parentLabelCellId: null,
                source: "manual",
              };
              setLayout((current) => ({
                ...current,
                layoutSchemaVersion: 3,
                recordSections: [...(current.recordSections ?? []), section],
                recordStructureVerified: false,
              }));
              setSelectedRecordSectionId(section.id);
              setMessage(`${section.title}を作成しました。次に「レコード作成」で各行を線でつないでください。`);
            }}
          >
            親セクションを追加
          </button>
        </div>

        {recordSections.length > 0 && (
          <div className="record-section-tabs" role="tablist" aria-label="親セクション">
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
                <label>親セクション表示名
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
                      recordStructureVerified: true,
                    }))}
                  />
                </label>
                <label>親ラベルセル
                  <select
                    value={section.parentLabelCellId ?? ""}
                    onChange={(event) => setLayout((current) => ({
                      ...current,
                      recordSections: (current.recordSections ?? []).map((item) => (
                        item.id === section.id
                          ? {
                            ...item,
                            parentLabelCellId: event.target.value || null,
                            source: "manual",
                          }
                          : item
                      )),
                      recordStructureVerified: true,
                    }))}
                  >
                    <option value="">線を引く、またはここで選択</option>
                    {layout.cells
                      .filter((cell) => cell.role === "label")
                      .map((cell) => (
                        <option value={cell.id} key={cell.id}>
                          {cell.text || cell.id}
                        </option>
                      ))}
                  </select>
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
                  親セクションを削除
                </button>
              </div>
              <div className="accessible-record-adder">
                <strong>一覧から同じ行の記入欄をまとめる</strong>
                <label>
                  このレコードに含める記入欄
                  <select
                    multiple
                    size={Math.min(8, Math.max(3, layout.cells.filter((cell) => cell.role === "input").length))}
                    value={[...accessibleRecordInputIds]}
                    onChange={(event) => setAccessibleRecordInputIds(new Set(
                      [...event.currentTarget.selectedOptions].map((option) => option.value),
                    ))}
                  >
                    {layout.cells.filter((cell) => cell.role === "input").map((cell) => (
                      <option key={cell.id} value={cell.id}>{cell.id}</option>
                    ))}
                  </select>
                </label>
                <small>CtrlキーまたはCommandキーを押しながら複数選択できます。</small>
                <button
                  type="button"
                  className="secondary compact"
                  disabled={accessibleRecordInputIds.size === 0}
                  onClick={addAccessibleRecordGroup}
                >
                  選択した記入欄を次のレコードにする
                </button>
              </div>
              <div className="record-group-list">
                {recordGroups
                  .filter((group) => group.sectionId === section.id)
                  .sort((first, second) => first.order - second.order)
                  .map((group) => (
                    <article className="record-group-row" key={group.id}>
                      <div>
                        <strong>{group.recordKeyValue || group.order}産次のレコード</strong>
                        <small>{group.inputCellIds.length}セル · {group.id}</small>
                      </div>
                      <label>順番
                        <input
                          type="number"
                          min={1}
                          max={section.collectionPath === "saleCalves" ? 10 : 20}
                          value={group.order}
                          onChange={(event) => {
                            const order = Number(event.target.value);
                            if (!Number.isInteger(order) || order < 1) return;
                            const inputIds = new Set(group.inputCellIds);
                            setLayout((current) => ({
                              ...current,
                              recordGroups: (current.recordGroups ?? []).map((item) => (
                                item.id === group.id ? { ...item, order, source: "manual" } : item
                              )),
                              relations: current.relations.map((relation) => {
                                if (!inputIds.has(relation.inputCellId)) return relation;
                                const fieldKey = relation.targetPath.split(".").at(-1);
                                const targetPath = `${section.collectionPath}.${order - 1}.${fieldKey}`;
                                return targetByPath.has(targetPath)
                                  ? { ...relation, targetPath, order }
                                  : relation;
                              }),
                              recordStructureVerified: true,
                            }));
                          }}
                        />
                      </label>
                      <label>産次キー
                        <input
                          value={group.recordKeyValue ?? ""}
                          maxLength={100}
                          onChange={(event) => setLayout((current) => ({
                            ...current,
                            recordGroups: (current.recordGroups ?? []).map((item) => (
                              item.id === group.id
                                ? {
                                  ...item,
                                  recordKeyValue: event.target.value || null,
                                  source: "manual",
                                }
                                : item
                            )),
                            recordStructureVerified: true,
                          }))}
                        />
                      </label>
                      <button
                        type="button"
                        className="danger-button compact"
                        onClick={() => setLayout((current) => ({
                          ...current,
                          recordGroups: (current.recordGroups ?? []).filter(
                            (item) => item.id !== group.id,
                          ),
                          recordStructureVerified: false,
                        }))}
                      >
                        レコード解除
                      </button>
                    </article>
                  ))}
                {recordGroups.every((group) => group.sectionId !== section.id) && (
                  <p className="empty-property">
                    「レコード作成」モードで、同じ産次に属する行の端から端まで線を引いてください。
                  </p>
                )}
              </div>
            </div>
          ))}
      </section>

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

      {layout.warnings.map((warning) => <p className="inline-warning" key={warning}>{warning}</p>)}
      {!recordStructureReady && (
        <p className="error-box">
          配列項目の記入欄をすべて行レコードへまとめ、レコード構造を確認してください。
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
          || layout.relations.length === 0
          || !recordStructureReady
          || (!dirty && !allowUnchangedSave)
        }
        onClick={() => void save()}
      >
        {busy
          ? "処理中…"
          : dirty || allowUnchangedSave
          ? "レイアウトと意味づけをDBへ保存"
          : "レイアウトは保存済み"}
      </button>
      </fieldset>
    </div>
  );
}

function ModeButton({
  current,
  mode,
  label,
  onChange,
}: {
  current: Mode;
  mode: Mode;
  label: string;
  onChange: (mode: Mode) => void;
}) {
  return <button type="button" className={current === mode ? "active" : ""} onClick={() => onChange(mode)}>{label}</button>;
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

function normalizeLayout(layout: LayoutDefinition): LayoutDefinition {
  return {
    ...layout,
    layoutSchemaVersion: layout.layoutSchemaVersion ?? 3,
    recordSections: layout.recordSections ?? [],
    recordGroups: layout.recordGroups ?? [],
    recordStructureVerified: layout.recordStructureVerified ?? false,
    cells: layout.cells.map((cell) => ({
      ...cell,
      printedLabelText: cell.printedLabelText ?? "",
    })),
  };
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
    const normalizedField = normalizeLabel(fieldLabel);
    return normalizedField === normalizedLabel;
  });
  const partialCandidates = targets.filter((target) => {
    const fieldLabel = target.label.split("・").at(-1) ?? target.label;
    const normalizedField = normalizeLabel(fieldLabel);
    return normalizedLabel.includes(normalizedField)
      || normalizedField.includes(normalizedLabel);
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
    .replace(/曰/g, "日")
    .replace(/令/g, "齢")
    .replace(/[\s:：・／/()（）［\]\-―ー]/g, "")
    .toLowerCase();
}

function normalizeCustomKey(value: string) {
  const key = value.normalize("NFKC").trim();
  return key && !/[\s.\[\]]/.test(key) ? key : "";
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
