import type { LayoutDefinition } from "../types";
import type { AiFocusState, AiPreviewRegion } from "./focusState";
import type { AnalysisResult, Binding, EditUnit, GeometryCell } from "./template-analysis.generated";
import type { FocusAnchor } from "./v2.generated";

/** Display-only coordinates from validated proposals, never a layout or a write payload. */
export function analysisPreview(run: AnalysisResult, units: EditUnit[], layout: LayoutDefinition,
  binding: Binding | undefined, mode: "proposal" | "draft", targetId?: string, unresolved = false): AiFocusState | null {
  if (!binding || run.baseRevision !== binding.expectedRevision || run.scopeDigest !== binding.scopeDigest) return null;
  const anchors = new Map<string, FocusAnchor>(), regions: AiPreviewRegion[] = [];
  const regionIds = new Set<string>();
  const add = (cellId: string, pageId: string | null, rect?: GeometryCell, kind?: AiPreviewRegion["kind"], label?: string) => {
    const anchorId = `analysis:${run.analysisId}:${kind ?? "cell"}:${cellId}`;
    if (anchors.has(anchorId)) return;
    anchors.set(anchorId, { anchorId, kind: "template_cell", resourceId: run.resource.id, pageId,
      tableId: null, fieldId: null, cellId, rowId: null, columnId: null, label: label ?? "解析対象" });
    if (rect && kind) {
      regions.push({ anchorId, cellId, x: rect.x, y: rect.y, width: rect.width, height: rect.height, kind, label: label! });
      regionIds.add(cellId);
    }
  };
  for (const unit of units) {
    const geometry = unit.geometry;
    if (!geometry) continue;
    for (const cell of geometry.before) {
      if (targetId && cell.id !== targetId) continue;
      if (geometry.removedIds.includes(cell.id)) add(cell.id, unit.pageId, cell, "removed", mode === "draft" ? "除去済み・下書き" : "除去予定");
      else if (mode === "proposal") add(cell.id, unit.pageId, cell, "before", "変更前");
    }
    for (const cell of geometry.after) {
      if (targetId && cell.id !== targetId) continue;
      const kind = unresolved ? "unresolved" : geometry.addedIds.includes(cell.id) ? "added" : "changed";
      add(cell.id, unit.pageId, cell, kind, unresolved ? "手動設定が必要" : kind === "added" ? mode === "draft" ? "追加・下書き" : "追加予定" : mode === "draft" ? "変更・下書き" : "変更後");
    }
  }
  const current = new Map(layout.cells.map(c => [c.id, c]));
  for (const unit of units) for (const id of unit.cellIds) {
    if ((!targetId || id === targetId) && !regionIds.has(id) && current.has(id)) add(id, unit.pageId);
  }
  if (targetId && !anchors.size && current.has(targetId)) {
    const issue = run.proposal?.unresolved.find(u => u.cellId === targetId)
      ?? run.pages.flatMap(p => p.proposal?.unresolved ?? []).find(u => u.cellId === targetId);
    if (issue) add(targetId, issue.pageId);
  }
  if (!anchors.size) return null;
  const values = [...anchors.values()];
  return { commandId: `${run.analysisId}:${mode}:${targetId ?? units.map(u => u.unitId).join()}`,
    phase: mode === "draft" ? "applied" : "resolved", receivedAt: performance.now(), regions,
    focus: { viewRevision: run.baseRevision, contextRevision: binding.contextRevision, interactionEpoch: binding.interactionEpoch,
      anchors: values, primaryAnchorId: values[0].anchorId } };
}
