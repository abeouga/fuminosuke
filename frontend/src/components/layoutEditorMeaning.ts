import type { FieldMappingTarget, LayoutCell, LayoutDefinition, LayoutLabelPoolEntry, LayoutRecordGroup, LayoutRecordSection, LayoutRelation, } from "../types";
import { headingConnectionSegments } from "./headingConnections";
import { type Point, rectCenter, segmentDistance, type MeaningRouteSegment, routeSegments, segmentIntersectsRect, intervalOverlap, } from "./layoutEditorGeometry";
import { recordStructureIsLinked, linkLaneCells } from "./layoutEditorRecords";
export function unassignedMeaningCellIds(layout: LayoutDefinition): Set<string> {
    const assignedInputIds = new Set(layout.relations
        .filter((relation) => relation.targetPath.trim().length > 0)
        .map((relation) => relation.inputCellId));
    return new Set(layout.cells
        .filter((cell) => {
        if (cell.role === "input")
            return !assignedInputIds.has(cell.id);
        if (cell.role !== "mark")
            return false;
        return !(cell.markSets ?? []).some((markSet) => markSet.targetPath.trim().length > 0);
    })
        .map((cell) => cell.id));
}
export function incompleteMeaningCellIds(layout: LayoutDefinition): Set<string> {
    const unassigned = unassignedMeaningCellIds(layout);
    return new Set(layout.cells.filter((cell) => cell.role === "mark"
        && !unassigned.has(cell.id)
        && (!(cell.markSets?.length) || cell.markSets.some((set) => (!set.targetPath.trim()
            // A checklist box without listed boxes counts as one whole-cell checkbox.
            || (set.options.length === 0 && set.recognitionMode !== "checklist"))))).map((cell) => cell.id));
}
export function eraseMeaningParentLinksAlongSegment(relations: LayoutRelation[], cellsById: ReadonlyMap<string, LayoutCell>, start: Point, end: Point, tolerance: number) {
    const erasedParentLinks = new Set<string>();
    relations.forEach((relation) => {
        if (!relation.parentLabelCellId)
            return;
        const parent = cellsById.get(relation.parentLabelCellId);
        if (!parent)
            return;
        (relation.labelCellIds.length ? relation.labelCellIds : [relation.inputCellId]).forEach((labelCellId) => {
            const child = cellsById.get(labelCellId);
            if (!child)
                return;
            if (headingConnectionHit(start, end, rectCenter(parent), rectCenter(child), tolerance)) {
                erasedParentLinks.add(`${relation.parentLabelCellId}\u0000${labelCellId}`);
            }
        });
    });
    if (erasedParentLinks.size === 0)
        return { relations, changed: false };
    return {
        relations: relations.map((relation) => {
            if (!relation.parentLabelCellId)
                return relation;
            const parentWasErased = (relation.labelCellIds.length ? relation.labelCellIds : [relation.inputCellId]).some((labelCellId) => (erasedParentLinks.has(`${relation.parentLabelCellId}\u0000${labelCellId}`)));
            return parentWasErased ? { ...relation, parentLabelCellId: null } : relation;
        }),
        changed: true,
    };
}
export function headingConnectionHit(start: Point, end: Point, from: Point, to: Point, tolerance: number) {
    return headingConnectionSegments(from, to)
        .some(segment => segmentDistance(start, end, segment.start, segment.end) <= tolerance);
}
export type MeaningRelationRoute = {
    relationIndex: number;
    labelCellId: string;
    inputCellId: string;
    axis: LayoutRelation["axis"];
    inputAnchor: Point;
    segments: MeaningRouteSegment[];
};
function singleMeaningRoute(label: LayoutCell, input: LayoutCell) {
    const labelCenter = rectCenter(label);
    const inputCenter = rectCenter(input);
    const horizontal = Math.abs(inputCenter.x - labelCenter.x)
        >= Math.abs(inputCenter.y - labelCenter.y);
    if (horizontal) {
        const inputIsRight = inputCenter.x >= labelCenter.x;
        const start = {
            x: inputIsRight ? label.x + label.width : label.x,
            y: labelCenter.y,
        };
        const end = {
            x: inputIsRight ? input.x : input.x + input.width,
            y: inputCenter.y,
        };
        const middleX = (start.x + end.x) / 2;
        return {
            inputAnchor: end,
            segments: routeSegments([
                start,
                { x: middleX, y: start.y },
                { x: middleX, y: end.y },
                end,
            ]),
        };
    }
    const inputIsBelow = inputCenter.y >= labelCenter.y;
    const start = {
        x: labelCenter.x,
        y: inputIsBelow ? label.y + label.height : label.y,
    };
    const end = {
        x: inputCenter.x,
        y: inputIsBelow ? input.y : input.y + input.height,
    };
    const middleY = (start.y + end.y) / 2;
    return {
        inputAnchor: end,
        segments: routeSegments([
            start,
            { x: start.x, y: middleY },
            { x: end.x, y: middleY },
            end,
        ]),
    };
}
export function buildMeaningRelationRoutes(relations: LayoutRelation[], cellsById: ReadonlyMap<string, LayoutCell>, canonicalWidth: number, canonicalHeight: number) {
    type Link = {
        relationIndex: number;
        labelCellId: string;
        inputCellId: string;
        axis: LayoutRelation["axis"];
        input: LayoutCell;
    };
    const groupedLinks = new Map<string, {
        label: LayoutCell;
        links: Link[];
    }>();
    relations.forEach((relation, relationIndex) => {
        const input = cellsById.get(relation.inputCellId);
        if (!input)
            return;
        relation.labelCellIds.forEach((labelCellId) => {
            const label = cellsById.get(labelCellId);
            if (!label)
                return;
            const key = `${labelCellId}\u0000${relation.axis}`;
            const group = groupedLinks.get(key) ?? { label, links: [] };
            group.links.push({
                relationIndex,
                labelCellId,
                inputCellId: relation.inputCellId,
                axis: relation.axis,
                input,
            });
            groupedLinks.set(key, group);
        });
    });
    const routes: MeaningRelationRoute[] = [];
    const gutter = Math.max(6, Math.min(18, canonicalWidth / 180));
    groupedLinks.forEach(({ label, links }) => {
        if (links.length === 1) {
            const link = links[0];
            const route = singleMeaningRoute(label, link.input);
            routes.push({ ...link, ...route });
            return;
        }
        const inputCenters = links.map(({ input }) => rectCenter(input));
        const spreadX = Math.max(...inputCenters.map(({ x }) => x))
            - Math.min(...inputCenters.map(({ x }) => x));
        const spreadY = Math.max(...inputCenters.map(({ y }) => y))
            - Math.min(...inputCenters.map(({ y }) => y));
        const labelCenter = rectCenter(label);
        if (spreadY >= spreadX) {
            const minimumX = Math.min(...links.map(({ input }) => input.x));
            const maximumX = Math.max(...links.map(({ input }) => input.x + input.width));
            const leftTrunkX = minimumX - gutter;
            const rightTrunkX = maximumX + gutter;
            const leftDistance = Math.abs(labelCenter.x - minimumX);
            const rightDistance = Math.abs(labelCenter.x - maximumX);
            const useLeft = rightTrunkX > canonicalWidth - 2
                || (leftTrunkX >= 2 && leftDistance < rightDistance);
            const trunkX = Math.max(2, Math.min(canonicalWidth - 2, useLeft ? leftTrunkX : rightTrunkX));
            const targetYs = inputCenters.map(({ y }) => y);
            const averageTargetY = targetYs.reduce((total, y) => total + y, 0) / targetYs.length;
            const labelAnchor = trunkX <= label.x
                ? {
                    x: label.x,
                    y: Math.max(label.y, Math.min(label.y + label.height, averageTargetY)),
                }
                : trunkX >= label.x + label.width
                    ? {
                        x: label.x + label.width,
                        y: Math.max(label.y, Math.min(label.y + label.height, averageTargetY)),
                    }
                    : {
                        x: trunkX,
                        y: averageTargetY >= labelCenter.y ? label.y + label.height : label.y,
                    };
            const trunkStart = {
                x: trunkX,
                y: Math.min(labelAnchor.y, ...targetYs),
            };
            const trunkEnd = {
                x: trunkX,
                y: Math.max(labelAnchor.y, ...targetYs),
            };
            const commonSegments = [
                ...routeSegments([labelAnchor, { x: trunkX, y: labelAnchor.y }]),
                ...routeSegments([trunkStart, trunkEnd]),
            ];
            links.forEach((link, index) => {
                const inputAnchor = {
                    x: useLeft ? link.input.x : link.input.x + link.input.width,
                    y: inputCenters[index].y,
                };
                routes.push({
                    ...link,
                    inputAnchor,
                    segments: [
                        ...commonSegments,
                        ...routeSegments([{ x: trunkX, y: inputAnchor.y }, inputAnchor]),
                    ],
                });
            });
            return;
        }
        const minimumY = Math.min(...links.map(({ input }) => input.y));
        const maximumY = Math.max(...links.map(({ input }) => input.y + input.height));
        const topTrunkY = minimumY - gutter;
        const bottomTrunkY = maximumY + gutter;
        const topDistance = Math.abs(labelCenter.y - minimumY);
        const bottomDistance = Math.abs(labelCenter.y - maximumY);
        const useTop = bottomTrunkY > canonicalHeight - 2
            || (topTrunkY >= 2 && topDistance < bottomDistance);
        const trunkY = Math.max(2, Math.min(canonicalHeight - 2, useTop ? topTrunkY : bottomTrunkY));
        const targetXs = inputCenters.map(({ x }) => x);
        const averageTargetX = targetXs.reduce((total, x) => total + x, 0) / targetXs.length;
        const labelAnchor = trunkY <= label.y
            ? {
                x: Math.max(label.x, Math.min(label.x + label.width, averageTargetX)),
                y: label.y,
            }
            : trunkY >= label.y + label.height
                ? {
                    x: Math.max(label.x, Math.min(label.x + label.width, averageTargetX)),
                    y: label.y + label.height,
                }
                : {
                    x: averageTargetX >= labelCenter.x ? label.x + label.width : label.x,
                    y: trunkY,
                };
        const trunkStart = {
            x: Math.min(labelAnchor.x, ...targetXs),
            y: trunkY,
        };
        const trunkEnd = {
            x: Math.max(labelAnchor.x, ...targetXs),
            y: trunkY,
        };
        const commonSegments = [
            ...routeSegments([labelAnchor, { x: labelAnchor.x, y: trunkY }]),
            ...routeSegments([trunkStart, trunkEnd]),
        ];
        links.forEach((link, index) => {
            const inputAnchor = {
                x: inputCenters[index].x,
                y: useTop ? link.input.y : link.input.y + link.input.height,
            };
            routes.push({
                ...link,
                inputAnchor,
                segments: [
                    ...commonSegments,
                    ...routeSegments([{ x: inputAnchor.x, y: trunkY }, inputAnchor]),
                ],
            });
        });
    });
    return routes;
}
export function eraseMeaningLabelLinksAlongSegment(relations: LayoutRelation[], cellsById: ReadonlyMap<string, LayoutCell>, start: Point, end: Point, tolerance: number, canonicalWidth: number, canonicalHeight: number) {
    const routes = buildMeaningRelationRoutes(relations, cellsById, canonicalWidth, canonicalHeight);
    const erasedRelationLabels = new Set(routes
        .filter((route) => {
        const label = cellsById.get(route.labelCellId);
        const input = cellsById.get(route.inputCellId);
        return route.segments.some((segment) => segmentDistance(start, end, segment.start, segment.end) <= tolerance)
            || Boolean(label && segmentIntersectsRect(start, end, label, tolerance))
            || Boolean(input && segmentIntersectsRect(start, end, input, tolerance));
    })
        .map((route) => `${route.relationIndex}\u0000${route.labelCellId}`));
    if (erasedRelationLabels.size === 0)
        return { relations, changed: false };
    return {
        relations: relations.flatMap((relation, relationIndex) => {
            if (relation.labelCellIds.length === 0)
                return [relation];
            const remainingLabelIds = relation.labelCellIds.filter((labelId) => (!erasedRelationLabels.has(`${relationIndex}\u0000${labelId}`)));
            if (remainingLabelIds.length === 0)
                return [];
            return [{ ...relation, labelCellIds: remainingLabelIds }];
        }),
        changed: true,
    };
}
type LabelPoolEntry = {
    key: string;
    label: string;
    section: string;
    paths: string[];
    aliases: string[];
};
export function createAutoLabelPoolEntry(labelText: string, targets: FieldMappingTarget[], match: "ocr" | "exact" = "ocr"): LayoutLabelPoolEntry | null {
    const label = (match === "exact" ? labelText : labelText.normalize("NFKC")).trim();
    if (!label)
        return null;
    const sameName = (candidate: string) => match === "exact"
        ? candidate === label : normalizeLabelPoolText(candidate) === normalizeLabelPoolText(label);
    if (targets.some((target) => ((match === "exact" ? [target.label] : [target.label, ...(target.aliases ?? [])]).some(sameName))))
        return null;
    const baseKey = label
        .replace(/[\s　.。・:：／/()（）［］\[\]【】]+/g, "")
        .slice(0, 100);
    if (!baseKey)
        return null;
    const byPath = new Map(targets.map((target) => [target.path, target]));
    let key = baseKey;
    let suffix = 2;
    while (byPath.has(`fields.${key}`)
        && !sameName(byPath.get(`fields.${key}`)?.label ?? "")) {
        const suffixText = `_${suffix}`;
        key = `${baseKey.slice(0, Math.max(1, 100 - suffixText.length))}${suffixText}`;
        suffix += 1;
    }
    const targetPath = `fields.${key}`;
    if (byPath.has(targetPath))
        return null;
    return {
        id: `label_auto_${key}`.slice(0, 120),
        label,
        targetPath,
        section: "自動追加",
        aliases: [],
        source: "ocr",
    };
}
export function createAutoRepeatedLabelPoolEntries(labelText: string, inputCount: number, targets: FieldMappingTarget[], occupiedPaths: ReadonlySet<string> = new Set()): LayoutLabelPoolEntry[] | null {
    if (inputCount < 2)
        return null;
    const label = labelText.normalize("NFKC").trim();
    if (!label)
        return null;
    const rawBaseKey = label
        .replace(/[\s　.。・:：／/()（）［］\[\]【】]+/g, "")
        .slice(0, 92);
    if (!rawBaseKey)
        return null;
    const byPath = new Map(targets.map((target) => [target.path, target]));
    const normalizedLabel = normalizeLabelPoolText(label);
    let baseKey = rawBaseKey;
    let suffix = 2;
    while (true) {
        const conflicting = Array.from({ length: inputCount }, (_, index) => {
            const path = `fields.${baseKey}_${index + 1}`;
            if (occupiedPaths.has(path))
                return true;
            const existing = byPath.get(path);
            if (!existing)
                return false;
            return ![existing.label, ...(existing.aliases ?? [])].some((candidate) => (normalizeLabelPoolText(candidate).includes(normalizedLabel)));
        }).some(Boolean);
        if (!conflicting)
            break;
        const suffixText = `_${suffix}`;
        baseKey = `${rawBaseKey.slice(0, Math.max(1, 92 - suffixText.length))}${suffixText}`;
        suffix += 1;
    }
    return Array.from({ length: inputCount }, (_, index) => {
        const rowNumber = index + 1;
        const key = `${baseKey}_${rowNumber}`;
        const meaningLabel = suffix > 2 ? `${label}${suffix - 1}` : label;
        return {
            id: `label_auto_${key}`.slice(0, 120),
            label: `${meaningLabel} ${rowNumber}行目`,
            targetPath: `fields.${key}`,
            section: "自動追加",
            aliases: [label],
            source: "ocr" as const,
        };
    });
}
export function resolveLabelPoolTargetPath(entry: LabelPoolEntry, assignedPaths: ReadonlySet<string>): string | null {
    return entry.paths.find((path) => !assignedPaths.has(path)) ?? null;
}
export function buildLabelPoolEntries(targets: FieldMappingTarget[]): LabelPoolEntry[] {
    const entries = new Map<string, LabelPoolEntry>();
    targets.forEach((target) => {
        const key = target.path;
        const label = target.label.trim();
        const existing = entries.get(key);
        if (existing) {
            if (!existing.paths.includes(target.path))
                existing.paths.push(target.path);
            target.aliases?.forEach((alias) => {
                if (!existing.aliases.includes(alias))
                    existing.aliases.push(alias);
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
    return [...entries.values()].sort((first, second) => (first.section.localeCompare(second.section, "ja")
        || first.label.localeCompare(second.label, "ja")));
}
export function findLabelPoolMatch(text: string, entries: LabelPoolEntry[]): LabelPoolEntry | null {
    const label = text.trim();
    return label ? entries.find(entry => entry.label === label) ?? null : null;
}
export function normalizeLabelPoolText(value: string): string {
    return value
        .normalize("NFKC")
        .toLowerCase()
        .replace(/[壳殻]/g, "売")
        .replace(/[歷暦]/g, "歴")
        .replace(/曰/g, "日")
        .replace(/令/g, "齢")
        .replace(/[\s　・:：()（）［］\[\]【】]/g, "");
}
function normalizeLayoutCell(cell: LayoutCell): LayoutCell {
    const compactText = Array.from(cell.text.replace(/\s+/g, ""));
    const textDirection = cell.textDirection
        ?? (compactText.length >= 2 && cell.height >= cell.width * 1.4
            ? "vertical"
            : "horizontal");
    return {
        ...cell,
        role: cell.role,
        printedLabelText: cell.printedLabelText ?? "",
        compositeSubfields: cell.compositeSubfields ?? [],
        textDirection,
        textAlignment: cell.textAlignment ?? (textDirection === "vertical" ? "center" : "left"),
        markSets: (cell.markSets ?? []).map((markSet) => ({
            ...markSet,
            recognitionMode: markSet.recognitionMode ?? "automatic",
            options: markSet.options ?? [],
        })),
    };
}
export function normalizeLayout(layout: LayoutDefinition): LayoutDefinition { return { ...layout, cells: layout.cells.map(normalizeLayoutCell), recordSections: layout.recordSections ?? [], recordGroups: layout.recordGroups ?? [], cellMerges: layout.cellMerges ?? [], labelPool: layout.labelPool ?? [] }; }
export function meaningLinkLaneCells(start: Point, end: Point, cells: LayoutCell[], endCell: LayoutCell | undefined): LayoutCell[] {
    const endRole = endCell?.role === "input" || endCell?.role === "mark"
        ? endCell.role
        : null;
    // The release target expresses the kind of field the user intends to link.
    // Rectangles of another kind may overlap or sit across the drag lane, but
    // they must not become assignment targets or block the intended endpoint.
    return linkLaneCells(start, end, endRole ? cells.filter((cell) => cell.role === endRole) : cells);
}
export function targetPathsForInputs(inputs: LayoutCell[], relations: LayoutRelation[]): string[] | null {
    const paths = inputs.map((input) => relations.find((relation) => relation.inputCellId === input.id)?.targetPath);
    return paths.every((path): path is string => Boolean(path)) ? paths : null;
}
export function resolveAvailableLinkTargetPaths(requestedPaths: string[], inputs: LayoutCell[], relations: LayoutRelation[], targets: FieldMappingTarget[]): string[] | null { const ids = new Set(inputs.map(i => i.id)); const occupied = new Set(relations.filter(r => !ids.has(r.inputCellId)).map(r => r.targetPath)); return requestedPaths.every(p => p.startsWith("fields.") && !occupied.has(p)) ? requestedPaths : null; }
export function inferTargetPath(labelText: string, inputCount: number, targets: FieldMappingTarget[], assignedPaths: ReadonlySet<string>): string | null {
    const normalizedLabel = normalizeLabel(labelText);
    if (!normalizedLabel)
        return null;
    const exactCandidates = targets.filter((target) => {
        const fieldLabel = target.label.split("・").at(-1) ?? target.label;
        return [fieldLabel, ...(target.aliases ?? [])].some((candidate) => normalizeLabel(candidate) === normalizedLabel);
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
    if (exactCandidates.length === 0
        && new Set(candidates.map((candidate) => (normalizeLabel(candidate.label.split("・").at(-1) ?? candidate.label)))).size > 1)
        return null;
    if (inputCount !== 1)
        return null;
    return candidates.find((candidate) => !assignedPaths.has(candidate.path))?.path
        ?? candidates[0]?.path ?? null;
}
export function mergeLabelRelations(currentRelations: LayoutRelation[], inputs: LayoutCell[], labelId: string, axis: LayoutRelation["axis"], targetPaths: string[], relationStamp = Date.now(),
    /** The item name's heading: a box linked after the heading was set belongs under it too. */
    parentLabelCellId: string | null = null): LayoutRelation[] {
    const inputIds = new Set(inputs.map((input) => input.id));
    const nextTargetPaths = new Set(targetPaths);
    const targetByInputId = new Map(inputs.map((input, index) => [input.id, targetPaths[index]]));
    const preserved = currentRelations
        .filter((relation) => (!nextTargetPaths.has(relation.targetPath)
        || inputIds.has(relation.inputCellId)))
        .filter((relation) => (!inputIds.has(relation.inputCellId)
        || !relation.labelCellIds.includes(labelId)))
        .filter((relation) => (!inputIds.has(relation.inputCellId)
        || relation.labelCellIds.length > 0))
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
            ...(parentLabelCellId ? { parentLabelCellId } : {}),
            axis,
            order: index + 1,
            targetPath: targetPaths[index],
        })),
    ];
}
/** The name printed in an item-name cell; a merged cell keeps it as printed text. Symbols only ("□", "(") name nothing. */
export function labelCellName(cell: Pick<LayoutCell, "text" | "printedLabelText" | "fieldLabel">): string {
    const named = (value?: string | null) => value && /[\p{L}\p{N}]/u.test(value) ? value.trim() : "";
    return named(cell.text) || named(cell.printedLabelText) || named(cell.fieldLabel);
}
/** 「備考 3行目」→「備考」: a row's meaning name without its row number. */
export function meaningBaseName(label: string): string {
    return label.replace(/\s*\d+行目$/, "").trim();
}
/** Names made up when an item name had no readable text (「項目」「項目2」「番号21」…). */
export function isPlaceholderName(label: string): boolean {
    return /^(項目|番号|見出し|名称未設定の欄)\d*$/.test(meaningBaseName(label));
}
/**
 * Typing a name into an item-name cell renames the meanings that were only named after the old
 * text (or made up because there was none), so the result shows the new name. Names the person
 * chose elsewhere stay as they are.
 */
export function renameMeaningsThroughLabel(layout: LayoutDefinition, labelId: string, previousName: string, nextName: string): LayoutDefinition {
    const next = nextName.normalize("NFKC").trim().slice(0, 90);
    const previous = previousName.normalize("NFKC").trim();
    if (!next || !/[\p{L}\p{N}]/u.test(next))
        return layout;
    const derived = (label?: string | null) => {
        if (!label)
            return true;
        const base = meaningBaseName(label);
        return isPlaceholderName(label) || (Boolean(previous) && (base === previous || base.replace(/\d+$/, "") === previous));
    };
    const cells = new Map(layout.cells.map(cell => [cell.id, cell]));
    const relations = layout.relations
        .filter(relation => relation.labelCellIds.includes(labelId))
        .sort((a, b) => {
        const first = cells.get(a.inputCellId), second = cells.get(b.inputCellId);
        return (first?.y ?? 0) - (second?.y ?? 0) || (first?.x ?? 0) - (second?.x ?? 0);
    });
    if (!relations.length)
        return layout;
    const groupOf = new Map((layout.recordGroups ?? []).flatMap(group => group.inputCellIds.map(id => [id, group] as const)));
    const poolByPath = new Map((layout.labelPool ?? []).map(entry => [entry.targetPath, entry]));
    const renamed = new Map<string, string>();
    const fieldLabels = new Map<string, { old: string | undefined; name: string }>();
    relations.forEach((relation, index) => {
        const entry = poolByPath.get(relation.targetPath);
        if (!entry || !derived(entry.label))
            return;
        const group = groupOf.get(relation.inputCellId);
        const label = relations.length > 1 ? `${next} ${group?.order ?? index + 1}行目` : next;
        renamed.set(entry.targetPath, label);
        fieldLabels.set(relation.inputCellId, { old: entry.label, name: group ? next : label });
    });
    if (!renamed.size)
        return layout;
    return {
        ...layout,
        labelPool: (layout.labelPool ?? []).map(entry => renamed.has(entry.targetPath)
            ? { ...entry, label: renamed.get(entry.targetPath)!, aliases: [next] } : entry),
        cells: layout.cells.map(cell => {
            const change = fieldLabels.get(cell.id);
            if (!change || (cell.fieldLabel && cell.fieldLabel !== change.old && !derived(cell.fieldLabel)))
                return cell;
            return { ...cell, fieldLabel: change.name };
        }),
    };
}
function stableMeaningKeyHash(value: string): string {
    return Array.from(value).reduce((hash, character) => ((hash * 31) + (character.codePointAt(0) ?? 0)) >>> 0, 2166136261).toString(36);
}
function parentMeaningPathSegment(value: string, fallback: string): string {
    const segment = value
        .normalize("NFKC")
        .trim()
        .replace(/[.\\/\s:：・／()（）［\]{}「」『』]+/g, "_")
        .replace(/[^A-Za-z0-9_\u3040-\u30ff\u3400-\u9fff々〆ヶー-]/g, "")
        .replace(/_+/g, "_")
        .replace(/^_+|_+$/g, "");
    return (segment || fallback).slice(0, 54);
}
export function attachParentLabelToMeaningRelations(currentRelations: LayoutRelation[], parentLabel: LayoutCell, childLabel: LayoutCell): {
    relations: LayoutRelation[];
    entries: LayoutLabelPoolEntry[];
    inputCellIds: string[];
} | null {
    const matched = currentRelations
        .filter((relation) => relation.labelCellIds.includes(childLabel.id))
        .sort((first, second) => first.order - second.order || first.inputCellId.localeCompare(second.inputCellId));
    if (matched.length === 0)
        return null;
    // A cell's ID is never a name: a drawn cell without printed text is named generically.
    const parentName = labelCellName(parentLabel) || "見出し";
    const childName = labelCellName(childLabel) || "項目";
    const parentKey = parentMeaningPathSegment(parentName, "parent");
    const childKey = parentMeaningPathSegment(childName, "field");
    const parentIdentity = stableMeaningKeyHash(parentLabel.id).slice(0, 7);
    const basePath = `fields.${parentKey}_${parentIdentity}__${childKey}`;
    const pathByRelationId = new Map(matched.map((relation, index) => [
        relation.id,
        matched.length === 1 ? basePath : `${basePath}_${index + 1}`,
    ]));
    const relations = currentRelations.map((relation) => {
        const targetPath = pathByRelationId.get(relation.id);
        return targetPath
            ? { ...relation, parentLabelCellId: parentLabel.id, targetPath }
            : relation;
    });
    const entries = matched.map((relation, index): LayoutLabelPoolEntry => {
        const targetPath = pathByRelationId.get(relation.id)!;
        const rowSuffix = matched.length === 1 ? "" : ` ${index + 1}行目`;
        return {
            id: `pool_parent_${stableMeaningKeyHash(targetPath)}`.slice(0, 120),
            label: `${parentName} > ${childName}${rowSuffix}`.slice(0, 100),
            targetPath: targetPath.slice(0, 200),
            section: parentName.slice(0, 100),
            aliases: [childName.slice(0, 100)],
            source: "manual",
        };
    });
    return {
        relations,
        entries,
        inputCellIds: matched.map((relation) => relation.inputCellId),
    };
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
export function renumberRelations(relations: LayoutRelation[], cells: LayoutCell[]) {
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
            if (!a || !b)
                return 0;
            return first.axis === "horizontal" ? a.x - b.x : a.y - b.y;
        });
        items.forEach((relation, index) => orderByRelationId.set(relation.id, index + 1));
    });
    return relations.map((relation) => ({
        ...relation,
        order: orderByRelationId.get(relation.id) ?? relation.order,
    }));
}
