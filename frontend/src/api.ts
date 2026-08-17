import type { Artifact, AuthSession, ExcelTable, FieldMapping, FieldMappingsResponse, Health, IndividualRecordCatalog, IndividualRecordSearch, Job, LayoutDefinition, OcrDocument, OcrResult, Problem, SaleCalfSireSearch, SireName, SireNamePool, Template } from "./types";

const API = "/api/v1";

export class ApiError extends Error {
  code: string;
  constructor(message: string, code = "API_ERROR") {
    super(message);
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...init, credentials: "include" });
  if (!response.ok) {
    let problem: Problem = {};
    try {
      problem = (await response.json()) as Problem;
    } catch {
      // A non-JSON reverse-proxy error still becomes a safe local message.
    }
    const apiError = new ApiError(
      problem.detail ?? problem.message ?? "バックエンドとの通信に失敗しました。",
      problem.code,
    );
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("ledger-auth-required"));
    }
    throw apiError;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function createIdempotencyKey(scope: "template" | "ocr"): string {
  const value = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${scope}-${value}`;
}

export const api = {
  health: () => request<Health>("/health"),
  authSession: () => request<AuthSession>("/auth/session"),
  logout: () => request<{ authenticated: false }>("/auth/logout", { method: "POST" }),
  disconnectGoogle: () =>
    request<{ authenticated: false }>("/auth/google/disconnect", { method: "POST" }),
  templates: () => request<Template[]>("/templates"),
  template: (id: string) => request<Template>(`/templates/${id}`),
  sireNames: () => request<SireNamePool>("/sire-names"),
  createSireName: (name: string) =>
    request<SireName>("/sire-names", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  deleteSireName: (id: string) =>
    request<void>(`/sire-names/${id}`, { method: "DELETE" }),
  jobs: (limit = 20, options?: { attentionOnly?: boolean }) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (options?.attentionOnly) query.set("attentionOnly", "true");
    return request<Job[]>(`/jobs?${query.toString()}`);
  },
  job: (id: string) => request<Job>(`/jobs/${id}`),
  artifacts: (id: string) => request<{ items: Artifact[] }>(`/jobs/${id}/artifacts`),
  result: (id: string) => request<OcrResult>(`/ocr-jobs/${id}/result`),
  excelTable: (id: string) => request<ExcelTable>(`/ocr-jobs/${id}/excel-table`),
  searchIndividualRecords: (individualId: string) =>
    request<IndividualRecordSearch>(
      `/individual-records?individualId=${encodeURIComponent(individualId)}`,
    ),
  individualRecordCatalog: () =>
    request<IndividualRecordCatalog>("/individual-records/catalog"),
  searchSaleCalvesBySire: (sireName: string) =>
    request<SaleCalfSireSearch>(
      `/sale-calf-records?sireName=${encodeURIComponent(sireName)}`,
    ),
  createTemplate: async (
    name: string,
    mode: string,
    files: File[],
    idempotencyKey: string,
  ) => {
    const body = new FormData();
    body.set("name", name);
    body.set("mode", mode);
    files.forEach((file) => body.append("files", file));
    return request<{ jobId: string }>("/template-jobs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body,
    });
  },
  createOcr: async (
    templateVersionId: string,
    file: File,
    enabled: boolean,
    idempotencyKey: string,
  ) => {
    const body = new FormData();
    body.set("templateVersionId", templateVersionId);
    body.set("ocrEnabled", String(enabled));
    body.set("file", file);
    return request<{ jobId: string }>("/ocr-jobs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body,
    });
  },
  approve: (id: string) => request(`/template-versions/${id}/approve`, { method: "POST" }),
  reject: (id: string) => request(`/template-versions/${id}/reject`, { method: "POST" }),
  reprocess: (id: string) =>
    request<{ jobId: string }>(`/template-versions/${id}/reprocess`, { method: "POST" }),
  saveMask: async (id: string, file: Blob) => {
    const body = new FormData();
    body.set("file", file, "handwriting_mask.png");
    return request(`/template-versions/${id}/handwriting-mask`, { method: "PUT", body });
  },
  fieldMappings: (id: string) =>
    request<FieldMappingsResponse>(`/template-versions/${id}/field-mappings`),
  saveFieldMappings: (id: string, mappings: FieldMapping[]) =>
    request<FieldMappingsResponse>(`/template-versions/${id}/field-mappings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mappings }),
    }),
  layout: (id: string) =>
    request<LayoutDefinition>(`/template-versions/${id}/layout`),
  detectLayout: (id: string) =>
    request<LayoutDefinition>(`/template-versions/${id}/layout/detect`, {
      method: "POST",
    }),
  saveLayout: (id: string, layout: LayoutDefinition) =>
    request<LayoutDefinition>(`/template-versions/${id}/layout`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(layout),
    }),
  verifyLayout: (id: string) =>
    request<LayoutDefinition>(`/template-versions/${id}/layout/verify`, {
      method: "POST",
    }),
  correct: (id: string, revision: number, value: OcrDocument) =>
    request<OcrResult>(`/ocr-jobs/${id}/corrections`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        baseRevision: revision,
        document: value.document,
        saleCalves: value.saleCalves,
        pedigree: value.pedigree,
        calvings: value.calvings,
        customFields: value.customFields,
        reviewItems: value.reviewItems,
      }),
    }),
  confirm: (id: string, revision: number) =>
    request<OcrResult>(`/ocr-jobs/${id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baseRevision: revision }),
    }),
  reopenResult: (id: string) =>
    request<OcrResult>(`/ocr-jobs/${id}/reopen`, { method: "POST" }),
  syncSpreadsheet: (id: string) =>
    request<OcrResult>(`/ocr-jobs/${id}/spreadsheet-sync`, { method: "POST" }),
  retry: (id: string) => request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  cancel: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
};

export const googleLoginUrl = (returnTo: string) =>
  `${API}/auth/google/start?returnTo=${encodeURIComponent(returnTo)}`;

export const absoluteArtifactUrl = (path: string) => path;
export const exportUrl = (jobId: string, format: "json" | "csv" | "xlsx") =>
  `${API}/ocr-jobs/${jobId}/export?format=${format}`;
export const individualRecordSearchUrl = (individualId: string) =>
  `${API}/individual-records?individualId=${encodeURIComponent(individualId)}`;
export const saleCalfSireSearchUrl = (sireName: string) =>
  `${API}/sale-calf-records?sireName=${encodeURIComponent(sireName)}`;
