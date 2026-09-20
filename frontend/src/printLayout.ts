export type PrintCell = {
  sourceRange: string; startRow: number; endRow: number;
  x: number; y: number; width: number; height: number;
  text: string; fill: string; fontSize: number; bold: boolean;
  align: string; borders: Record<string, boolean>;
  rectangle?: { x: number; y: number; width: number; height: number };
};
export type ReferenceQualification = {
  status: "UNVERIFIED" | "UNSUPPORTED" | "FAIL" | "PASS";
  qualified: boolean; codes?: string[]; detail?: string;
  qualificationIssued?: boolean;
  environmentQualified?: boolean;
  geometryQualified?: boolean;
  unresolvedBlockers?: string[];
  issuedAt?: number | null;
  expiresAt?: number | null;
  templateVersionRevision?: string;
  renderer?: { type: string; version: string } | null;
  productionBlockers?: string[];
  safetyStages?: {
    policy: "paged-two-stage-safety-v1";
    activation: {
      status: "BLOCKED" | "READY_FOR_QUALIFICATION" | "QUALIFIED";
      codes: string[]; requiresSubmittedScan: false; certificateIssued: boolean;
    };
    execution: {
      status: "NOT_EVALUATED"; requiredBefore: "PROVIDER_REQUEST";
      checks: string[]; requiresSubmittedScan: true;
    };
  };
  pages?: { page: number; status: string; maxErrorPx?: number | null;
    meanErrorPx?: number | null; codes?: string[]; code?: string }[];
};

export type ReferenceQualificationState = "LOADING" | "PASS" | "UNVERIFIED" | "FAIL" | "EXPIRED";

export function referenceQualificationState(
  evidence: ReferenceQualification | null,
  fallbackExpiresAt?: number | null,
  nowSeconds = Date.now() / 1000,
): Exclude<ReferenceQualificationState, "LOADING"> {
  const expiresAt = evidence?.expiresAt ?? fallbackExpiresAt;
  if (typeof expiresAt === "number" && expiresAt <= nowSeconds) return "EXPIRED";
  if (!evidence) return "UNVERIFIED";
  const unresolved = evidence.unresolvedBlockers ?? [];
  const blockers = evidence.productionBlockers ?? [];
  if (
    evidence.status === "PASS"
    && evidence.qualified === true
    && evidence.qualificationIssued === true
    && evidence.environmentQualified === true
    && evidence.geometryQualified === true
    && unresolved.length === 0
    && blockers.length === 0
    && typeof evidence.expiresAt === "number"
    && evidence.expiresAt > nowSeconds
  ) return "PASS";
  if (evidence.status === "FAIL" || evidence.status === "UNSUPPORTED") return "FAIL";
  return "UNVERIFIED";
}
export type PrintPage = {
  page: number; range: string; startRow: number; endRow: number;
  width: number; height: number;
  transform: { scale: number; offsetX: number; offsetY: number; originX: number; originY: number };
  cells: PrintCell[];
  fields: { cellId: string; sourceRange: string; templatePage: number;
    sourceParts?: { cellId: string; sourceRange: string }[];
    rectangle: { x: number; y: number; width: number; height: number } }[];
  referenceImage?: { sha256: string };
};
export type PrintRequest = {
  baseRevision: string; paperSize: "A4" | "A3" | "Letter";
  orientation: "portrait" | "landscape";
  margins: { left: number; right: number; top: number; bottom: number };
  horizontalCentering: boolean; verticalCentering: boolean;
  targetPagesWide: 1; targetPagesTall: number | null;
  breakAfterRows: number[] | null; autoArrange: boolean;
};
export type PrintPlan = Omit<PrintRequest, "baseRevision" | "breakAfterRows" | "autoArrange"> & {
  commonScale: number; pages: PrintPage[]; warnings: string[]; ocrReady: false;
  printFiles?: { renderKey: string };
};
export type PrintState = {
  revision: string; editable: boolean; saved: PrintPlan | null;
  source: {
    sheetName: string; startRow: number; endRow: number; width: number; height: number;
    rowBoundaries: { row: number; y: number }[]; validBreakRows: number[];
    cells: PrintCell[]; unsupported: string[]; warnings: string[];
    importedSettings: {
      pageSetup: Record<string, string> | null;
      margins: Record<string, string> | null;
      printOptions: Record<string, string> | null;
    };
  };
};

export function printDefaults(state: PrintState): PrintRequest {
  const saved = state.saved;
  const imported = state.source.importedSettings;
  const paper = imported.pageSetup?.paperSize;
  const margins = Object.fromEntries(["left", "right", "top", "bottom"].map((key) => {
    const raw = imported.margins?.[key];
    const mm = raw === undefined ? 10 : Number(raw) * 25.4;
    return [key, Number.isFinite(mm) && mm >= 0 && mm <= 100 ? Math.round(mm * 100) / 100 : 10];
  })) as PrintRequest["margins"];
  const fit = Number(imported.pageSetup?.fitToHeight);
  return {
    baseRevision: state.revision,
    paperSize: saved?.paperSize ?? (paper === "8" ? "A3" : paper === "1" ? "Letter" : "A4"),
    orientation: saved?.orientation ?? (imported.pageSetup?.orientation === "landscape" ? "landscape" : "portrait"),
    margins: saved?.margins ?? margins,
    horizontalCentering: saved?.horizontalCentering ?? ["1", "true"].includes(imported.printOptions?.horizontalCentered ?? ""),
    verticalCentering: saved?.verticalCentering ?? ["1", "true"].includes(imported.printOptions?.verticalCentered ?? ""),
    targetPagesWide: 1,
    targetPagesTall: saved?.targetPagesTall ?? (Number.isInteger(fit) && fit >= 1 && fit <= 50 ? fit : null),
    breakAfterRows: saved ? saved.pages.slice(0, -1).map(p => p.endRow) : null,
    autoArrange: false,
  };
}

export function nearestBreak(y: number, source: PrintState["source"], occupied: number[]): number | null {
  const candidates = source.validBreakRows.filter(r => !occupied.includes(r));
  if (!candidates.length) return null;
  const positions = new Map(source.rowBoundaries.map(b => [b.row - 1, b.y]));
  return candidates.reduce((a, b) => Math.abs((positions.get(a) ?? 0) - y)
    <= Math.abs((positions.get(b) ?? 0) - y) ? a : b);
}
