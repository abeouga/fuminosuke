import type { Artifact, AuthSession, DataSearchCatalog, DataSearchFilter, DataSearchResponse, DocumentLibrary, DocumentLibraryFolder, DriveOutputFolder, DrivePickerSession, ExcelTable, FieldMapping, FieldMappingsResponse, Health, IndividualRecordCatalog, IndividualRecordSearch, Job, LayoutDefinition, OcrDocument, OcrLibraryContext, OcrResult, OpenAIUsageReport, OutputLayoutTemplateStatus, PortableBackupExport, Problem, SaleCalfSireSearch, SireNamePool, SpreadsheetSyncStatus, Template, TemplateCandidateSettings, TemplateDefinition, TemplateDefinitionRevision, TemplateDeleteResult, TemplateOutputFolder } from "./types";

const API = "/api/v1";

export class ApiError extends Error {
  code: string;
  constructor(message: string, code = "API_ERROR") {
    super(message);
    this.code = code;
  }
}

async function requestResponse(path: string, init?: RequestInit): Promise<Response> {
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
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await requestResponse(path, init);
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
  templateOutputFolder: (id: string) =>
    request<TemplateOutputFolder>(`/templates/${id}/output-folder`),
  selectTemplateOutputFolder: (id: string, folderId: string) =>
    request<TemplateOutputFolder>(`/templates/${id}/output-folder`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folderId }),
    }),
  deleteTemplate: (id: string) => request<TemplateDeleteResult>(`/templates/${id}`, { method: "DELETE" }),
  sireNames: () => request<SireNamePool>("/sire-names"),
  jobs: (limit = 20, options?: { attentionOnly?: boolean }) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (options?.attentionOnly) query.set("attentionOnly", "true");
    return request<Job[]>(`/jobs?${query.toString()}`);
  },
  openaiUsage: () => request<OpenAIUsageReport>("/development/openai-usage"),
  createPortableBackup: () => request<PortableBackupExport>("/system-backups/portable", {
    method: "POST",
  }),
  portableBackupDownload: (id: string) =>
    requestResponse(`/system-backups/portable/${encodeURIComponent(id)}`),
  deletePortableBackup: (id: string) =>
    request<void>(`/system-backups/portable/${encodeURIComponent(id)}`, { method: "DELETE" }),
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
  dataSearchCatalog: () => request<DataSearchCatalog>("/data-search/catalog"),
  dataSearch: (body: {
    query?: string;
    templateId?: string | null;
    filters?: DataSearchFilter[];
    page?: number;
    pageSize?: number;
  }) => request<DataSearchResponse>("/data-search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
  documentLibrary: () => request<DocumentLibrary>("/document-library"),
  createDocumentFolder: (name: string, parentId: string | null) =>
    request<DocumentLibraryFolder>("/document-library/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parentId }),
    }),
  renameDocumentFolder: (id: string, name: string) =>
    request<DocumentLibraryFolder>(`/document-library/folders/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  deleteDocumentFolder: (id: string) =>
    request<void>(`/document-library/folders/${id}`, { method: "DELETE" }),
  moveLibraryDocument: (documentId: string, folderId: string | null, expectedVersion: number) =>
    request<{ documentId: string; folderId: string | null; locationVersion: number }>(
      `/document-library/documents/${documentId}/location`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folderId, expectedVersion }),
      },
    ),
  renameLibraryDocument: (documentId: string, name: string) =>
    request<{ documentId: string; name: string; spreadsheetRenamed: boolean }>(
      `/document-library/documents/${documentId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    ),
  deleteLibraryDocuments: (documentIds: string[]) =>
    request<{ deletedCount: number; driveFilesPreserved: boolean }>(
      "/document-library/documents/bulk-delete",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ documentIds }),
      },
    ),
  driveOutputFolder: () => request<DriveOutputFolder>("/google-drive/output-folder"),
  drivePickerSession: () =>
    request<DrivePickerSession>("/google-drive/picker-session", { method: "POST" }),
  selectDriveFolder: (folderId: string) =>
    request<DriveOutputFolder>("/google-drive/output-folder", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folderId }),
    }),
  searchSaleCalvesBySire: (sireName: string) =>
    request<SaleCalfSireSearch>(
      `/sale-calf-records?sireName=${encodeURIComponent(sireName)}`,
    ),
  createTemplate: async (
    name: string,
    mode: string,
    files: File[],
    idempotencyKey: string,
    fieldNames: string[] = [],
  ) => {
    const body = new FormData();
    body.set("name", name);
    body.set("mode", mode);
    body.set("fieldNames", JSON.stringify(fieldNames));
    files.forEach((file) => body.append("files", file));
    return request<{ jobId: string }>("/template-jobs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body,
    });
  },
  createSpreadsheetTemplate: async (
    name: string,
    file: File,
    idempotencyKey: string,
  ) => {
    const body = new FormData();
    body.set("name", name);
    body.set("file", file);
    return request<{ jobId: string }>("/spreadsheet-template-jobs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body,
    });
  },
  templatePrintUrl: (versionId: string, autoPrint = true) =>
    `${API}/template-versions/${encodeURIComponent(versionId)}/print?auto_print=${String(autoPrint)}`,
  templateBoundaryImageUrl: (jobId: string) =>
    `${API}/template-jobs/${encodeURIComponent(jobId)}/boundary-image`,
  saveTemplateBoundary: (
    jobId: string,
    corners: Array<{ x: number; y: number }>,
  ) => request<Job>(`/template-jobs/${encodeURIComponent(jobId)}/boundary`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ corners }),
  }),
  createOcr: async (
    templateVersionId: string,
    file: File,
    enabled: boolean,
    idempotencyKey: string,
    context?: OcrLibraryContext | null,
  ) => {
    const body = new FormData();
    body.set("templateVersionId", templateVersionId);
    body.set("ocrEnabled", String(enabled));
    body.set("file", file);
    if (context) {
      body.set("documentAction", context.action);
      if (context.documentId) body.set("targetDocumentId", context.documentId);
      if (context.pageId) body.set("targetPageId", context.pageId);
    }
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
  templateCandidateSettings: (templateId: string) =>
    request<TemplateCandidateSettings>(`/templates/${templateId}/candidate-settings`),
  saveTemplateCandidateSettings: (
    templateId: string,
    templateVersionId: string,
    sets: Array<{
      name: string;
      values: string[];
      allowedSuffixes: string[];
      targetPatterns: string[];
    }>,
  ) => request<TemplateCandidateSettings>(`/templates/${templateId}/candidate-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ templateVersionId, sets }),
  }),
  saveFieldMappings: (
    id: string,
    mappings: FieldMapping[],
    documentIdentity: FieldMappingsResponse["documentIdentity"],
    documentIdentityFields: FieldMappingsResponse["documentIdentityFields"] = documentIdentity ? [documentIdentity] : [],
  ) =>
    request<FieldMappingsResponse>(`/template-versions/${id}/field-mappings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mappings, documentIdentity, documentIdentityFields }),
    }),
  layout: (id: string) =>
    request<LayoutDefinition>(`/template-versions/${id}/layout`),
  detectLayout: (id: string) =>
    request<LayoutDefinition>(`/template-versions/${id}/layout/detect`, {
      method: "POST",
    }),
  recognizeLayoutLabels: (id: string, layout: LayoutDefinition) =>
    request<LayoutDefinition>(`/template-versions/${id}/layout/recognize-labels`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(layout),
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
  templateDefinition: (id: string) =>
    request<TemplateDefinitionRevision | null>(`/template-versions/${id}/template-definition`),
  templateDefinitionHistory: (id: string) =>
    request<TemplateDefinitionRevision[]>(`/template-versions/${id}/template-definition/history`),
  generateTemplateDefinition: (id: string) =>
    request<TemplateDefinitionRevision>(`/template-versions/${id}/template-definition/generate`, {
      method: "POST",
    }),
  saveTemplateDefinition: (id: string, definition: TemplateDefinition) =>
    request<TemplateDefinitionRevision>(`/template-versions/${id}/template-definition`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(definition),
    }),
  approveTemplateDefinition: (id: string, definition: TemplateDefinition) =>
    request<TemplateDefinitionRevision>(`/template-versions/${id}/template-definition/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(definition),
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
  resolveDocument: (
    id: string,
    action: "ADD_PAGE" | "RESCAN_PAGE" | "NEW_DOCUMENT",
    target?: { documentId?: string | null; jobId?: string | null; pageId?: string | null },
  ) => request<OcrResult>(`/ocr-jobs/${id}/document-resolution`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action,
      targetDocumentId: target?.documentId ?? null,
      targetJobId: target?.jobId ?? null,
      targetPageId: target?.pageId ?? null,
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
  renameSpreadsheet: (id: string, fileName: string) =>
    request<SpreadsheetSyncStatus>(`/ocr-jobs/${id}/spreadsheet-name`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fileName }),
    }),
  outputLayout: (id: string) =>
    request<OutputLayoutTemplateStatus>(`/ocr-jobs/${id}/output-layout`),
  importOutputLayout: (id: string) =>
    request<OutputLayoutTemplateStatus>(`/ocr-jobs/${id}/output-layout/import`, {
      method: "POST",
    }),
  spreadsheetStatus: (jobId?: string) => request<SpreadsheetSyncStatus>(
    `/google-sheets/status${jobId ? `?jobId=${encodeURIComponent(jobId)}` : ""}`,
  ),
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
