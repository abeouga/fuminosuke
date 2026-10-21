import type { ContextResult, Focus } from "./v2.generated";
export type AiPreviewRegion = { anchorId: string; cellId: string; x: number; y: number; width: number; height: number;
  kind: "added" | "before" | "changed" | "removed" | "unresolved"; label: string };
export type AiFocusState = { commandId: string; phase: "candidates" | "resolved" | "applied" | "preview"; focus: Focus; receivedAt: number; regions?: AiPreviewRegion[] };
export function validFocus(focus: Focus, context: ContextResult, viewRevision: number): boolean {
  const scope = context.scopeContext, ids = new Set<string>();
  if (focus.contextRevision !== scope.contextRevision || focus.interactionEpoch !== scope.interactionEpoch ||
      focus.viewRevision !== viewRevision || focus.viewRevision !== scope.resourceRevision) return false;
  for (const a of focus.anchors) {
    if (ids.has(a.anchorId) || a.resourceId !== scope.resource.id ||
        (scope.pageScope.mode === "current" && a.pageId !== scope.pageScope.pageId) ||
        (a.kind === "review_field" ? !a.fieldId || a.cellId !== null || scope.resource.kind !== "ocr"
          : !a.cellId || a.fieldId !== null || scope.resource.kind !== "template")) return false;
    ids.add(a.anchorId);
  }
  return focus.primaryAnchorId === null ? ids.size === 0 : ids.has(focus.primaryAnchorId);
}
