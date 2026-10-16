import type { LayoutCell, LayoutDefinition } from "../types";
import type { FacetTouch } from "./template-analysis.generated";

export type Facet = FacetTouch["facet"];
export const facetKey = (id: string, facet: Facet) => JSON.stringify([id, facet]);
export const facetTouch = (id: string, facet: Facet): FacetTouch => ({ target: { draftEntityId: id }, facet });

/** Mirrors the finite semantic and physical domain facets. */
export function authoringFacets(layout: LayoutDefinition): Map<string, unknown> {
  const result = new Map<string, unknown>();
  const pools = new Map((layout.labelPool ?? []).map(p => [p.targetPath, p]));
  for (const cell of layout.cells) {
    const relations = layout.relations.filter(r => r.inputCellId === cell.id);
    const groups = (layout.recordGroups ?? []).filter(g => g.inputCellIds.includes(cell.id));
    const values: Record<Facet, unknown> = {
      geometry: { x: cell.x, y: cell.y, width: cell.width, height: cell.height,
        shapeType: cell.shapeType ?? "rectangle", vertices: cell.vertices ?? [], pageRange: cell.pageRange ?? null },
      role: cell.role,
      meaning: { fieldLabel: cell.fieldLabel ?? null, meanings: relations.map(r => pools.get(r.targetPath) ?? null) },
      binding: relations, parent: cell.parentLabelCellId ?? null,
      records: { groups, sections: (layout.recordSections ?? []).filter(s => groups.some(g => g.sectionId === s.id)) },
      reading: cell.readingMode ?? null, mark: [cell.markSets ?? [], cell.compositeSubfields ?? []],
    };
    for (const facet of Object.keys(values) as Facet[]) result.set(facetKey(cell.id, facet), values[facet]);
  }
  for (const pool of layout.labelPool ?? []) result.set(facetKey(pool.id, "meaning"), pool);
  for (const section of layout.recordSections ?? []) result.set(facetKey(section.id, "records"), section);
  for (const group of layout.recordGroups ?? []) result.set(facetKey(group.id, "records"), group);
  for (const relation of layout.relations) result.set(facetKey(relation.id, "binding"), relation);
  result.set(facetKey("@layout", "geometry"), { cells: layout.cells.map(c => c.id),
    merges: layout.cellMerges ?? [], size: [layout.canonicalWidth, layout.canonicalHeight] });
  return result;
}
export function changedFacets(before: LayoutDefinition, after: LayoutDefinition): FacetTouch[] {
  const prior = authoringFacets(before);
  const current = authoringFacets(after);
  return [...new Set([...prior.keys(), ...current.keys()])].flatMap(key => {
    if (JSON.stringify(prior.get(key)) === JSON.stringify(current.get(key))) return [];
    const [id, facet] = JSON.parse(key) as [string, Facet];
    if (!current.has(key) && facet !== "geometry") return [];
    if (!prior.has(key) && facet !== "geometry" && facet !== "role") {
      const value = current.get(key);
      const meaning = value as { fieldLabel?: string; meanings?: unknown[] } | undefined;
      const records = value as { groups?: unknown[]; sections?: unknown[] } | undefined;
      const nonempty = facet === "meaning" ? Boolean(meaning?.fieldLabel || meaning?.meanings?.length)
        : facet === "records" ? Boolean(records?.groups?.length || records?.sections?.length)
        : facet === "mark" ? Array.isArray(value) && value.some(v => Array.isArray(v) && v.length > 0)
        : Array.isArray(value) ? value.length > 0 : Boolean(value);
      if (!nonempty) return [];
    }
    return [facetTouch(id, facet)];
  });
}
export function patchedCellFacets(id: string, patch: Partial<LayoutCell>): FacetTouch[] {
  const fields: [Facet, (keyof LayoutCell)[]][] = [
    ["geometry", ["x", "y", "width", "height", "shapeType", "vertices", "pageRange"]],
    ["role", ["role"]], ["meaning", ["fieldLabel", "text", "printedLabelText"]],
    ["parent", ["parentLabelCellId"]], ["reading", ["readingMode"]], ["mark", ["markSets", "compositeSubfields"]],
  ];
  return fields.filter(([, keys]) => keys.some(k => k in patch)).map(([facet]) => facetTouch(id, facet));
}
