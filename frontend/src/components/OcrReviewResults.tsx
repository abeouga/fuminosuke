import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { FieldReading, OcrFieldPreview, ReviewStructure } from "../types";

export type ReadingEntry = { path: string; label: string; value: FieldReading };

export type ReviewDisplayRecordSection = {
  id: string;
  title: string;
  collectionPath: string;
  columns: Array<{ id: string; label: string }>;
  rows: Array<{
    id: string;
    cells: Array<{ columnId: string; entry: ReadingEntry | null }>;
  }>;
};

export type ReviewDisplayModel = {
  recordSections: ReviewDisplayRecordSection[];
  parentSections: Array<{ id: string; title: string; entries: ReadingEntry[] }>;
  independentEntries: ReadingEntry[];
  independentGroups: Array<{ id: string; label: string; entries: ReadingEntry[] }>;
};

export type ReviewInteractionMode = "edit" | "reorder";
type RectBounds = { left: number; top: number; right: number; bottom: number };
type PopoverSize = { width: number; height: number };
export type ReviewPopoverPosition = {
  left: number;
  top: number;
  placement: "above" | "below";
};

export function positionReviewPopover(
  anchor: RectBounds,
  popover: PopoverSize,
  boundary: RectBounds,
  gap = 8,
): ReviewPopoverPosition {
  const availableAbove = anchor.top - boundary.top;
  const availableBelow = boundary.bottom - anchor.bottom;
  const placement = availableAbove >= popover.height + gap || availableAbove >= availableBelow
    ? "above"
    : "below";
  const desiredTop = placement === "above"
    ? anchor.top - popover.height - gap
    : anchor.bottom + gap;
  const maxLeft = Math.max(boundary.left, boundary.right - popover.width);
  const maxTop = Math.max(boundary.top, boundary.bottom - popover.height);
  return {
    left: Math.min(
      Math.max(anchor.left + (anchor.right - anchor.left - popover.width) / 2, boundary.left),
      maxLeft,
    ),
    top: Math.min(Math.max(desiredTop, boundary.top), maxTop),
    placement,
  };
}

export function PenModeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 20l4.2-1 10.6-10.6-3.2-3.2L5 15.8 4 20zm10.5-13.7 3.2 3.2" />
    </svg>
  );
}

export function PointerModeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 3l11.5 10.2-5.2.8 2.7 5.2-2.5 1.3-2.7-5.2L6 19V3z" />
    </svg>
  );
}

export function ReviewResultsToolbar({
  interactionMode,
  editingLocked,
  confirmed,
  warningCount,
  cellCount,
  onInteractionModeChange,
  onNextWarning,
}: {
  interactionMode: ReviewInteractionMode;
  editingLocked: boolean;
  confirmed: boolean;
  warningCount: number;
  cellCount: number;
  onInteractionModeChange: (mode: ReviewInteractionMode) => void;
  onNextWarning: () => void;
}) {
  return (
    <div className="field-toolbar">
      <div className="ocr-cell-legend">
        <span><i className="legend-ok" />問題なし</span>
        <span><i className="legend-warning" />要確認</span>
      </div>
      <div className="field-toolbar-actions">
        <div className="review-mode-switch" role="group" aria-label="結果表の操作モード">
          <button
            type="button"
            className={interactionMode === "edit" ? "active" : ""}
            aria-label="結果編集モード"
            aria-pressed={interactionMode === "edit"}
            title="ペンでセルの内容を編集"
            disabled={editingLocked}
            onClick={() => onInteractionModeChange("edit")}
          >
            <PenModeIcon />
            <span>編集</span>
          </button>
          <button
            type="button"
            className={interactionMode === "reorder" ? "active" : ""}
            aria-label="行移動モード"
            aria-pressed={interactionMode === "reorder"}
            title="繰り返し表の行全体を移動"
            disabled={editingLocked}
            onClick={() => onInteractionModeChange("reorder")}
          >
            <PointerModeIcon />
            <span>移動</span>
          </button>
        </div>
        {!confirmed && (
          <button
            type="button"
            className="secondary compact"
            disabled={warningCount === 0}
            onClick={onNextWarning}
          >
            次の要確認（{warningCount}件）
          </button>
        )}
        <span>{confirmed
          ? "確定済み"
          : interactionMode === "reorder"
            ? "行移動モード"
            : `${cellCount}セルを編集中`}</span>
      </div>
    </div>
  );
}

export function AnchoredReviewPopover({
  path,
  wide = false,
  children,
  onClose,
}: {
  path: string;
  wide?: boolean;
  children: ReactNode;
  onClose: () => void;
}) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const [position, setPosition] = useState<ReviewPopoverPosition | null>(null);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useLayoutEffect(() => {
    const updatePosition = () => {
      const anchor = Array.from(
        document.querySelectorAll<HTMLElement>("[data-review-path]"),
      ).find((candidate) => candidate.dataset.reviewPath === path);
      const popover = popoverRef.current;
      if (!anchor || !popover) {
        onCloseRef.current();
        return;
      }
      const anchorRect = anchor.getBoundingClientRect();
      const paneRect = anchor.closest<HTMLElement>(".field-pane")?.getBoundingClientRect();
      const viewport = {
        left: 8,
        top: 8,
        right: Math.max(8, window.innerWidth - 8),
        bottom: Math.max(8, window.innerHeight - 8),
      };
      const hasUsablePane = Boolean(
        paneRect && paneRect.right > paneRect.left && paneRect.bottom > paneRect.top,
      );
      const boundary = hasUsablePane && paneRect
        ? {
          left: Math.max(viewport.left, paneRect.left + 8),
          top: Math.max(viewport.top, paneRect.top + 8),
          right: Math.min(viewport.right, paneRect.right - 8),
          bottom: Math.min(viewport.bottom, paneRect.bottom - 8),
        }
        : viewport;
      setPosition(positionReviewPopover(
        anchorRect,
        {
          width: popover.offsetWidth || (wide ? 520 : 260),
          height: popover.offsetHeight || (wide ? 220 : 96),
        },
        boundary,
      ));
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [path, wide]);

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, []);

  return (
    <div
      ref={popoverRef}
      className={`ocr-review-popover${wide ? " ocr-review-popover-wide" : ""}`}
      data-placement={position?.placement}
      style={{
        left: position?.left ?? 0,
        top: position?.top ?? 0,
        visibility: position ? "visible" : "hidden",
      }}
    >
      {children}
    </div>
  );
}

export function CellStatusPopover({
  entry,
  confirmed,
  onChange,
  onClose,
}: {
  entry: ReadingEntry;
  confirmed: boolean;
  onChange: (patch: Partial<FieldReading>) => void;
  onClose: () => void;
}) {
  const setStatus = (status: FieldReading["status"]) => {
    onChange(status === "blank"
      ? { status, raw: null, normalized: null, reviewReason: null }
      : status === "uncertain"
        ? { status, normalized: null, raw: entry.value.raw ?? "" }
        : { status, raw: entry.value.raw ?? "", reviewReason: null });
  };
  return (
    <section className="ocr-cell-status-popover" aria-label="選択セルの判定">
      <header>
        <strong>{entry.label}</strong>
        <button type="button" aria-label="判定を閉じる" onClick={onClose}>×</button>
      </header>
      <div role="group" aria-label={`${entry.label}の判定`}>
        {([
          ["readable", "読めた"],
          ["uncertain", "不確実"],
          ["blank", "空欄"],
        ] as const).map(([status, label]) => (
          <button
            type="button"
            key={status}
            className={entry.value.status === status ? "active" : ""}
            aria-pressed={entry.value.status === status}
            disabled={confirmed}
            onClick={() => setStatus(status)}
          >
            {label}
          </button>
        ))}
      </div>
    </section>
  );
}

export function ReviewTableSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  if (!children) return null;
  return (
    <section className="ocr-edit-section" aria-label={title}>
      <header><h3>{title}</h3></header>
      {children}
    </section>
  );
}

function canSwapVisibleRecordRows(
  section: ReviewDisplayRecordSection,
  fromIndex: number,
  toIndex: number,
) {
  if (
    Math.abs(fromIndex - toIndex) !== 1
    || fromIndex < 0
    || toIndex < 0
    || fromIndex >= section.rows.length
    || toIndex >= section.rows.length
  ) return false;
  const fromRow = section.rows[fromIndex];
  const toRow = section.rows[toIndex];
  const paths = section.columns.flatMap((column) => {
    const firstPath = fromRow.cells.find((cell) => cell.columnId === column.id)?.entry?.path;
    const secondPath = toRow.cells.find((cell) => cell.columnId === column.id)?.entry?.path;
    return firstPath && secondPath ? [firstPath, secondPath] : [];
  });
  return paths.length === section.columns.length * 2 && new Set(paths).size === paths.length;
}

export function GenericRecordReviewTable({
  section,
  selectedPath,
  warningPaths,
  choicePaths,
  confirmed,
  interactionMode,
  onSelect,
  onEdit,
  onMoveRow,
}: {
  section: ReviewDisplayRecordSection;
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  choicePaths: ReadonlySet<string>;
  confirmed: boolean;
  interactionMode: ReviewInteractionMode;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
  onMoveRow: (
    section: ReviewDisplayRecordSection,
    fromIndex: number,
    toIndex: number,
  ) => void;
}) {
  return (
    <ReviewTableSection title={section.title}>
      <div className="ocr-edit-table-wrap">
        <table
          className="ocr-edit-table ocr-collection-table ocr-generic-record-table"
          aria-label={`${section.title}のレコード`}
        >
          <thead>
            <tr>
              <th scope="col">行</th>
              {section.columns.map((column) => (
                <th scope="col" key={column.id}>{column.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {section.rows.map((row, rowIndex) => (
              <tr key={row.id}>
                <th scope="row" aria-label={`${rowIndex + 1}行目`}>
                  <div className="ocr-row-heading">
                    <span>{rowIndex + 1}</span>
                    {interactionMode === "reorder" && (
                      <span className="ocr-row-shift-controls">
                        <button
                          type="button"
                          aria-label={`${section.title} ${rowIndex + 1}行目を上へ移動`}
                          title="この行の読み取り結果を一行上へ移動"
                          disabled={confirmed || !canSwapVisibleRecordRows(section, rowIndex, rowIndex - 1)}
                          onClick={() => onMoveRow(section, rowIndex, rowIndex - 1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          aria-label={`${section.title} ${rowIndex + 1}行目を下へ移動`}
                          title="この行の読み取り結果を一行下へ移動"
                          disabled={confirmed || !canSwapVisibleRecordRows(section, rowIndex, rowIndex + 1)}
                          onClick={() => onMoveRow(section, rowIndex, rowIndex + 1)}
                        >
                          ↓
                        </button>
                      </span>
                    )}
                  </div>
                </th>
                {section.columns.map((column) => {
                  const cell = row.cells.find((item) => item.columnId === column.id);
                  const entry = cell?.entry;
                  if (!entry) {
                    return (
                      <td
                        className="ocr-unmapped-cell"
                        key={column.id}
                        aria-label={`${rowIndex + 1}行目 ${column.label} 未設定`}
                      >
                        —
                      </td>
                    );
                  }
                  return (
                    <ReviewValueCell
                      key={column.id}
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

export function KeyValueReviewTable({
  entries,
  selectedPath,
  warningPaths,
  choicePaths,
  confirmed,
  onSelect,
  onEdit,
}: {
  entries: ReadingEntry[];
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  choicePaths: ReadonlySet<string>;
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
                  choice={choicePaths.has(entry.path)}
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

export function IndependentReviewGroups({
  groups,
  selectedPath,
  warningPaths,
  choicePaths,
  confirmed,
  onSelect,
  onEdit,
}: {
  groups: ReviewDisplayModel["independentGroups"];
  selectedPath: string | null;
  warningPaths: ReadonlySet<string>;
  choicePaths: ReadonlySet<string>;
  confirmed: boolean;
  onSelect: (entry: ReadingEntry) => void;
  onEdit: (entry: ReadingEntry, text: string) => void;
}) {
  return (
    <div className="ocr-independent-groups">
      {groups.map((group) => (
        <section className="ocr-independent-group" key={group.id}>
          <h4 className="ocr-independent-label">{group.label}</h4>
          <table className="ocr-edit-table ocr-independent-values">
            <tbody>
              <tr>
                {group.entries.map((entry) => (
                  <ReviewValueCell
                    key={entry.path}
                    entry={entry}
                    selected={selectedPath === entry.path}
                    warning={warningPaths.has(entry.path)}
                    choice={choicePaths.has(entry.path)}
                    confirmed={confirmed}
                    onSelect={onSelect}
                    onEdit={onEdit}
                  />
                ))}
              </tr>
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

function ReviewLabelAndCell({
  entry,
  selected,
  warning,
  choice,
  confirmed,
  onSelect,
  onEdit,
}: {
  entry: ReadingEntry;
  selected: boolean;
  warning: boolean;
  choice: boolean;
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
        choice={choice}
        confirmed={confirmed}
        onSelect={onSelect}
        onEdit={onEdit}
      />
    </>
  );
}

export function ReviewValueCell({
  entry,
  selected,
  warning,
  choice,
  confirmed,
  onSelect,
  onEdit,
}: {
  entry: ReadingEntry;
  selected: boolean;
  warning: boolean;
  choice: boolean;
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
          title={confirmed || choice ? undefined : "ダブルクリックで直接編集"}
          onClick={() => onSelect(entry)}
          onDoubleClick={(event) => {
            if (confirmed) return;
            event.preventDefault();
            onSelect(entry);
            if (choice) return;
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

export function buildReviewDisplayModel(
  entries: readonly ReadingEntry[],
  previews: readonly OcrFieldPreview[],
  structure: ReviewStructure | undefined,
): ReviewDisplayModel {
  if (!structure) {
    const independentEntries = [...entries];
    return {
      recordSections: [],
      parentSections: [],
      independentEntries,
      independentGroups: independentEntries.map((entry) => ({
        id: `path:${entry.path}`,
        label: entry.label,
        entries: [entry],
      })),
    };
  }

  const entryByPath = new Map(entries.map((entry) => [entry.path, entry]));
  const entriesBySource = new Map<string, ReadingEntry[]>();
  entries.forEach((entry) => {
    if (entry.value.sourceRegionId !== "unmapped") {
      const candidates = entriesBySource.get(entry.value.sourceRegionId) ?? [];
      candidates.push(entry);
      entriesBySource.set(entry.value.sourceRegionId, candidates);
    }
  });
  const previewByMappingId = new Map(
    previews.map((preview) => [preview.mappingId, preview]),
  );
  const fieldById = new Map(structure.fields.map((field) => [field.id, field]));
  const consumedPaths = new Set<string>();
  const resolveField = (fieldId: string | null): ReadingEntry | null => {
    if (!fieldId) return null;
    const field = fieldById.get(fieldId);
    if (!field) return null;
    const sourceCandidates = entriesBySource.get(field.mappingId) ?? [];
    const entry = (sourceCandidates.length === 1 ? sourceCandidates[0] : undefined)
      ?? sourceCandidates.find((candidate) => candidate.path === field.targetPath)
      ?? sourceCandidates.find(
        (candidate) => candidate.path === previewByMappingId.get(field.mappingId)?.targetPath,
      )
      ?? entryByPath.get(previewByMappingId.get(field.mappingId)?.targetPath ?? "")
      ?? entryByPath.get(field.targetPath);
    if (!entry || consumedPaths.has(entry.path)) return null;
    consumedPaths.add(entry.path);
    return { ...entry, label: field.label || entry.label };
  };

  const recordTitleCounts = new Map<string, number>();
  structure.recordSections.forEach((section) => {
    recordTitleCounts.set(section.title, (recordTitleCounts.get(section.title) ?? 0) + 1);
  });
  const recordTitleIndexes = new Map<string, number>();
  const recordSections = structure.recordSections.map((section) => {
    const titleIndex = (recordTitleIndexes.get(section.title) ?? 0) + 1;
    recordTitleIndexes.set(section.title, titleIndex);
    return {
      id: section.id,
      title: (recordTitleCounts.get(section.title) ?? 0) > 1
        ? `${section.title} ${titleIndex}`
        : section.title,
      collectionPath: section.collectionPath,
      columns: section.columns.map((column) => ({ ...column })),
      rows: section.rows.map((row) => ({
        id: row.id,
        cells: row.cells.map((cell) => ({
          columnId: cell.columnId,
          entry: resolveField(cell.fieldId),
        })),
      })),
    };
  });
  const parentSections = structure.parentSections.map((section) => ({
    id: section.id,
    title: section.title,
    entries: section.fieldIds
      .map((fieldId) => resolveField(fieldId))
      .filter((entry): entry is ReadingEntry => entry !== null),
  }));

  const independentEntries: ReadingEntry[] = [];
  const independentGroups: ReviewDisplayModel["independentGroups"] = [];
  const independentGroupById = new Map<string, ReviewDisplayModel["independentGroups"][number]>();
  const appendIndependent = (
    entry: ReadingEntry,
    groupId: string,
    label: string,
  ) => {
    independentEntries.push(entry);
    const existing = independentGroupById.get(groupId);
    if (existing) {
      existing.entries.push(entry);
      return;
    }
    const group = { id: groupId, label, entries: [entry] };
    independentGroupById.set(groupId, group);
    independentGroups.push(group);
  };
  for (const fieldId of [
    ...structure.independentFieldIds,
    ...structure.fields.map((field) => field.id),
  ]) {
    const entry = resolveField(fieldId);
    const field = fieldById.get(fieldId);
    if (entry && field) {
      appendIndependent(entry, field.groupId ?? `field:${field.id}`, field.label || entry.label);
    }
  }
  entries.forEach((entry) => {
    if (!consumedPaths.has(entry.path)) {
      consumedPaths.add(entry.path);
      appendIndependent(entry, `path:${entry.path}`, entry.label);
    }
  });
  return { recordSections, parentSections, independentEntries, independentGroups };
}

function chunk<T>(items: T[], size: number): T[][] {
  const rows: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    rows.push(items.slice(index, index + size));
  }
  return rows;
}
