/** Three-way reconciliation of this client's successful write only. */
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const referenceKeys = new Set(["id", "draftEntityId", "inputCellId", "parentLabelCellId", "sectionId", "parentCellId", "recordKeyCellId", "mappingId", "targetPath", "collectionPath", "matchGroupId", "labelCellIds", "inputCellIds", "recordLabelCellIds", "rowKeyCellIds", "fieldPaths"]);
export function substituteOwnReferences<T>(value: T, substitutions: Record<string, string>): T {
  const walk = (node: unknown, key = "", cellId = "", setId = ""): unknown => {
    if (typeof node === "string") return referenceKeys.has(key) ? substitutions[node] ?? node : node;
    if (Array.isArray(node)) return node.map(item => walk(item, key, cellId, setId));
    if (isObject(node)) {
      const cell = key === "cells" && typeof node.id === "string" ? substitutions[node.id] ?? node.id : cellId;
      const markSet = key === "markSets" && typeof node.id === "string" ? node.id : setId;
      return Object.fromEntries(Object.entries(node).map(([k, v]) => {
        if (k === "id" && typeof v === "string" && key === "markSets") return [k, substitutions[`mark_set:${JSON.stringify([cell, v])}`] ?? v];
        if (k === "id" && typeof v === "string" && key === "options") return [k, substitutions[`mark_option:${JSON.stringify([cell, markSet, v])}`] ?? v];
        return [k, walk(v, k, cell, markSet)];
      }));
    }
    return node;
  };
  return walk(value) as T;
}
export function reconcileOwnSave<T>(current: T, submitted: T, saved: T, substitutions: Record<string, string>): T {
  const ours = substituteOwnReferences(current, substitutions);
  const base = substituteOwnReferences(submitted, substitutions);
  const merge = (next: unknown, sent: unknown, canonical: unknown): unknown => {
    if (same(next, sent)) return structuredClone(canonical);
    if (Array.isArray(next) && Array.isArray(sent) && Array.isArray(canonical)) {
      if ([...next, ...sent, ...canonical].every(v => isObject(v) && typeof v.id === "string")) {
        const byId = (items: unknown[]) => new Map(items.map(item => [(item as Record<string, unknown>).id, item]));
        const old = byId(sent), result = byId(canonical), live = byId(next);
        return [
          ...next.flatMap(item => {
            const id = (item as Record<string, unknown>).id;
            if (old.has(id) && !result.has(id) && same(item, old.get(id))) return [];
            return [old.has(id) && result.has(id) ? merge(item, old.get(id), result.get(id)) : item];
          }),
          ...canonical.filter(item => !old.has((item as Record<string, unknown>).id) && !live.has((item as Record<string, unknown>).id)),
        ];
      }
      return next; // Ordered primitive lists are user input, not an identity match.
    }
    if (isObject(next) && isObject(sent) && isObject(canonical)) {
      return Object.fromEntries([...new Set([...Object.keys(next), ...Object.keys(canonical)])].flatMap(key => {
        if (same(next[key], sent[key])) return key in canonical ? [[key, canonical[key]]] : [];
        return key in next ? [[key, merge(next[key], sent[key], canonical[key])]] : [];
      }));
    }
    return next;
  };
  return merge(ours, base, saved) as T;
}
