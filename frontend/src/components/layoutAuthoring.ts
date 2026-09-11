import type { FieldMappingTarget, LayoutCell, LayoutDefinition, LayoutLabelPoolEntry } from "../types";

type Point = { x: number; y: number };

export function pointInCell(point: Point, cell: LayoutCell): boolean {
  if (point.x < cell.x || point.y < cell.y
    || point.x > cell.x + cell.width || point.y > cell.y + cell.height) return false;
  if (cell.shapeType !== "polygon" || !cell.vertices?.length) return true;
  let inside = false;
  const points = cell.vertices;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const a = points[j], b = points[i];
    const cross = (point.x - a.x) * (b.y - a.y) - (point.y - a.y) * (b.x - a.x);
    if (Math.abs(cross) < 0.001 && point.x >= Math.min(a.x, b.x)
      && point.x <= Math.max(a.x, b.x) && point.y >= Math.min(a.y, b.y)
      && point.y <= Math.max(a.y, b.y)) return true;
    if ((a.y > point.y) !== (b.y > point.y)
      && point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}

export function polygonCell(vertices: Point[], id: string): LayoutCell | null {
  if (vertices.length < 3 || vertices.length > 100
    || new Set(vertices.map(p => `${p.x},${p.y}`)).size !== vertices.length) return null;
  const cross = (a: Point, b: Point, c: Point) =>
    (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  let area = 0;
  for (let i = 0; i < vertices.length; i++) {
    const a = vertices[i], b = vertices[(i + 1) % vertices.length];
    area += a.x * b.y - a.y * b.x;
    for (let j = i + 1; j < vertices.length; j++) {
      if (j === i + 1 || (i === 0 && j === vertices.length - 1)) continue;
      const c = vertices[j], d = vertices[(j + 1) % vertices.length];
      const overlap = Math.max(a.x, b.x) >= Math.min(c.x, d.x)
        && Math.max(c.x, d.x) >= Math.min(a.x, b.x)
        && Math.max(a.y, b.y) >= Math.min(c.y, d.y)
        && Math.max(c.y, d.y) >= Math.min(a.y, b.y);
      if (overlap && cross(a, b, c) * cross(a, b, d) <= 0
        && cross(c, d, a) * cross(c, d, b) <= 0) return null;
    }
  }
  if (!area) return null;
  const x = Math.min(...vertices.map(p => p.x)), y = Math.min(...vertices.map(p => p.y));
  return { id, role: "input", x, y,
    width: Math.max(...vertices.map(p => p.x)) - x,
    height: Math.max(...vertices.map(p => p.y)) - y,
    shapeType: "polygon", vertices: vertices.map(p => ({ ...p })),
    confidence: 1, text: "", textConfidence: null, source: "manual" };
}

/** Allocate only the new meaning; printed labels and all existing bindings stay intact. */
export function uniqueMeaningEntry(
  labelText: string, layout: LayoutDefinition, targets: FieldMappingTarget[] = [],
): LayoutLabelPoolEntry {
  const original = labelText.normalize("NFKC").trim().slice(0, 100) || "項目";
  const occupiedNames = new Set((layout.labelPool ?? []).map(entry => entry.label));
  const byId = new Map(layout.cells.map(cell => [cell.id, cell]));
  layout.relations.forEach(relation => {
    relation.labelCellIds.forEach(id => { const text = byId.get(id)?.text.trim(); if (text) occupiedNames.add(text); });
    const printed = byId.get(relation.inputCellId)?.printedLabelText?.trim();
    if (printed) occupiedNames.add(printed);
  });
  const occupiedPaths = new Set([
    ...layout.relations.map(relation => relation.targetPath),
    ...(layout.labelPool ?? []).map(entry => entry.targetPath),
    ...layout.cells.flatMap(cell => (cell.markSets ?? []).map(set => set.targetPath)),
  ]);
  targets.filter(target => occupiedPaths.has(target.path)).forEach(target => occupiedNames.add(target.label));
  let label = original;
  let number = 1;
  const keyFor = (value: string) => value.replace(/[\s　.。・:：／/()（）［］\[\]【】]+/g, "") || "項目";
  while (occupiedNames.has(label) || occupiedPaths.has(`customFields.${keyFor(label)}`)) {
    const suffix = String(++number);
    label = original.slice(0, 100 - suffix.length) + suffix;
  }
  const key = keyFor(label);
  return { id: `label_auto_${key}`, label, targetPath: `customFields.${key}`,
    section: "自動追加", aliases: [], source: "manual" };
}

/** The existing parentLabelCellId edge now also belongs to unassigned cells/headings. */
export function attachHeading(
  layout: LayoutDefinition, parentId: string, childIds: string[],
): LayoutDefinition {
  const parent = layout.cells.find(cell => cell.id === parentId);
  if (!parent || !["label", "parent_label"].includes(parent.role)) throw new Error("接続元は見出しを選択してください。");
  const ids = new Set(childIds);
  for (const id of ids) {
    const child = layout.cells.find(cell => cell.id === id);
    if (!child || !["label", "parent_label", "input", "mark"].includes(child.role)) {
      throw new Error("見出し・項目名・記入欄・選択欄へ接続してください。");
    }
  }
  const cells = layout.cells.map(cell => ids.has(cell.id) ? { ...cell, parentLabelCellId: parentId } : cell);
  const relations = layout.relations.map(relation => (
    (ids.has(relation.inputCellId) && !relation.labelCellIds.length)
      || relation.labelCellIds.some(id => ids.has(id))
      ? { ...relation, parentLabelCellId: parentId } : relation
  ));
  const parents = new Map<string, Set<string>>();
  const add = (child: string, parent: string) => {
    const values = parents.get(child) ?? new Set<string>(); values.add(parent); parents.set(child, values);
  };
  cells.forEach(cell => { if (cell.parentLabelCellId) add(cell.id, cell.parentLabelCellId); });
  relations.forEach(relation => {
    if (relation.parentLabelCellId) (relation.labelCellIds.length ? relation.labelCellIds : [relation.inputCellId])
      .forEach(id => add(id, relation.parentLabelCellId!));
  });
  for (const cell of cells) {
    if (cell.parentLabelCellId && (parents.get(cell.id)?.size ?? 0) > 1) {
      throw new Error("同じ項目に異なる親を設定できません。項目名側の接続を確認してください。");
    }
    const visited = new Set<string>();
    const pending = [...(parents.get(cell.id) ?? [])];
    while (pending.length) {
      const id = pending.pop()!;
      if (id === cell.id) throw new Error("見出しの自己参照・循環参照は設定できません。");
      if (visited.has(id)) continue;
      visited.add(id); pending.push(...(parents.get(id) ?? []));
    }
  }
  return { ...layout, cells, relations };
}

export function enclosedHeadingChildren(layout: LayoutDefinition, rect: Point & { width: number; height: number }): string[] {
  return layout.cells.filter(cell => ["label", "input", "mark"].includes(cell.role)
    && cell.x >= rect.x && cell.y >= rect.y && cell.x + cell.width <= rect.x + rect.width
    && cell.y + cell.height <= rect.y + rect.height).map(cell => cell.id);
}
