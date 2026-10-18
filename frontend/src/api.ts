import { authenticatedFetch, csrfHeaders } from "./auth/transport";
import type { TemplateAuthoringUpdate, TemplateAuthoringView } from "./assistant/generatedAuthoring";
import type { Artifact, ExcelTable, FieldMappingsResponse, Health, Job, LayoutDefinition, OcrDocument, OcrResult, Problem, Template, TemplateCandidateSettings, TemplateDefinition, TemplateDefinitionRevision, TemplateDeleteResult, TemplatePackageActivation, TemplatePackageOperation } from "./types";
const API = "/api/v1";
import type { PrintPlan, PrintRequest, PrintState } from "./printLayout";
import type { PageFieldEdit, PagedReviewResult } from "./pagedReview";
import type { DocumentIdentity } from "./types";
/** A confirmed document (latest version) an upload can be read as an update of. */
export type SeriesCandidate = {
    jobId: string;
    documentName: string | null;
    templateName: string;
    templateVersionId: string | null;
    confirmedAt: string;
    confirmedRevision: number;
    seriesVersion: number;
    identity: { label: string; value: string | null }[];
    selectable: boolean;
    reason: string | null;
};
export class ApiError extends Error {
    code: string;
    constructor(message: string, code = "API_ERROR") {
        super(message);
        this.code = code;
    }
}
async function requestResponse(path: string, init?: RequestInit): Promise<Response> {
    const response = await authenticatedFetch(`${API}${path}`, { ...init, credentials: "include" });
    if (!response.ok) {
        let problem: Problem = {};
        try {
            problem = (await response.json()) as Problem;
        }
        catch {
            // A non-JSON reverse-proxy error still becomes a safe local message.
        }
        const apiError = new ApiError(problem.detail ?? problem.message ?? "バックエンドとの通信に失敗しました。", problem.code);
        throw apiError;
    }
    return response;
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await requestResponse(path, init);
    if (response.status === 204)
        return undefined as T;
    return (await response.json()) as T;
}
async function templateMutation<T>(path: string, expectedRevision: number, init: RequestInit): Promise<T & { resourceRevision: number }> {
    const response = await requestResponse(`${path}${path.includes("?") ? "&" : "?"}expectedRevision=${expectedRevision}`, init);
    const resourceRevision = Number(response.headers.get("X-Template-Revision"));
    if (!Number.isSafeInteger(resourceRevision) || resourceRevision < 1) {
        throw new ApiError("保存後の世代を確認できません。入力を保持して状態を再取得してください。", "REVISION_RESPONSE_MISSING");
    }
    return { ...(await response.json() as T), resourceRevision };
}

export function uploadTemplatePackageInspection(file: File, onProgress: (loaded: number, total: number) => void): Promise<TemplatePackageOperation> {
    return new Promise((resolve, reject) => {
        const upload = new XMLHttpRequest();
        upload.open("POST", `${API}/template-packages/imports/inspect`);
        upload.withCredentials = true;
        upload.responseType = "json";
        for (const [name, value] of Object.entries(csrfHeaders())) upload.setRequestHeader(name, value);
        upload.upload.addEventListener("progress", (event) => {
            if (event.lengthComputable)
                onProgress(event.loaded, event.total);
        });
        upload.addEventListener("load", () => {
            if (upload.status === 401) window.dispatchEvent(new Event("fuminosuke-auth-required"));
            if (upload.status >= 200 && upload.status < 300) {
                resolve(upload.response as TemplatePackageOperation);
                return;
            }
            const problem = (upload.response ?? {}) as Problem;
            reject(new ApiError(problem.detail ?? problem.message ?? "template packageを検証できませんでした。", problem.code));
        });
        upload.addEventListener("error", () => {
            reject(new ApiError("template packageの送信中に通信が切断されました。"));
        });
        const form = new FormData();
        form.append("file", file, file.name);
        upload.send(form);
    });
}
export function createIdempotencyKey(scope: "template" | "ocr"): string {
    const value = typeof globalThis.crypto?.randomUUID === "function"
        ? globalThis.crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `${scope}-${value}`;
}
export const api = {
    authoring: (id: string) => request<TemplateAuthoringView>(`/template-versions/${id}/authoring`),
    saveAuthoring: (id: string, expectedRevision: number, body: TemplateAuthoringUpdate) => templateMutation<TemplateAuthoringView>(`/template-versions/${id}/authoring`, expectedRevision, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }),
    downloadPageOutput: async (id: string, revision: number) => {
        const response = await requestResponse(`/ocr-jobs/${encodeURIComponent(id)}/page-review/output.xlsx?revision=${revision}`);
        if (response.headers.get("X-Result-Revision") !== String(revision)) {
            throw new ApiError("取得したXLSXの確認版が一致しません。", "PAGE_OUTPUT_REVISION_CHANGED");
        }
        return response.blob();
    },
    downloadTemplatePrint: async (id: string, revision: string, format: "xlsx" | "pdf") => {
        const response = await requestResponse(`/template-versions/${encodeURIComponent(id)}/print-layout/files/${format}?base_revision=${encodeURIComponent(revision)}`);
        return response.blob();
    },
    downloadImportedTemplatePrint: async (id: string, format: "xlsx" | "pdf") => {
        const response = await requestResponse(`/template-versions/${encodeURIComponent(id)}/blank-files/${format}`);
        return response.blob();
    },
    referenceQualification: (id: string) => request<import("./printLayout").ReferenceQualification>(`/template-versions/${id}/print-layout/qualification`),
    prepareReviewedOcr: (id: string, expectedRevision: number, revision: string) => templateMutation<import("./printLayout").ReferenceQualification>(`/template-versions/${id}/print-layout/ocr-readiness?base_revision=${encodeURIComponent(revision)}`, expectedRevision, { method: "POST" }),
    prepareCandidateReadiness: (id: string, expectedRevision: number, revision: string) => templateMutation<import("./printLayout").ReferenceQualification>(`/template-versions/${id}/print-layout/candidate-readiness?base_revision=${encodeURIComponent(revision)}`, expectedRevision, { method: "POST" }),
    qualifyReference: (id: string, expectedRevision: number, revision: string) => templateMutation<import("./printLayout").ReferenceQualification>(`/template-versions/${id}/print-layout/qualification?base_revision=${encodeURIComponent(revision)}`, expectedRevision, { method: "POST" }),
    getOcrPages: (versionId: string) => request<import("./components/OcrPageBindings").PageCatalog>(`/template-versions/${versionId}/ocr-pages`),
    createOcrPageJob: (body: FormData, key: string) => request<{
        jobId: string;
    }>("/ocr-page-jobs", { method: "POST", body, headers: { "Idempotency-Key": key } }),
    printLayout: (id: string) => request<PrintState>(`/template-versions/${id}/print-layout`),
    preparePrintFiles: (id: string, expectedRevision: number, revision: string) => templateMutation<PrintState>(`/template-versions/${id}/print-layout/prepare?base_revision=${encodeURIComponent(revision)}`, expectedRevision, { method: "POST" }),
    previewPrintLayout: (id: string, body: PrintRequest) => request<PrintPlan>(`/template-versions/${id}/print-layout/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    savePrintLayout: (id: string, expectedRevision: number, body: PrintRequest) => templateMutation<PrintState>(`/template-versions/${id}/print-layout`, expectedRevision, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    clearPrintLayout: (id: string, expectedRevision: number, revision: string) => templateMutation<PrintState>(`/template-versions/${id}/print-layout?base_revision=${encodeURIComponent(revision)}`, expectedRevision, { method: "DELETE" }),
    health: () => request<Health>("/health"),
    templates: () => request<Template[]>("/templates"),
    template: (id: string) => request<Template>(`/templates/${id}`),
    renameTemplate: (id: string, name: string) => request<{
        id: string;
        name: string;
    }>(`/templates/${encodeURIComponent(id)}/name`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
    }),
    deleteTemplate: (id: string) => request<TemplateDeleteResult>(`/templates/${id}`, { method: "DELETE" }),
    inspectTemplatePackageExport: (body: {
        templateId: string;
        templateVersionId: string;
        publisherId: string;
        packageId: string;
        version: string;
        includePreview: boolean;
        includeHandwritingCandidates: boolean;
        includeOutputLayout: boolean;
    }) => request<TemplatePackageOperation>("/template-packages/exports/inspect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    }),
    confirmTemplatePackageExport: (confirmationToken: string) => requestResponse("/template-packages/exports/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmationToken }),
    }),
    confirmTemplatePackageImport: (confirmationToken: string, allowOlderVersion: boolean) => request<TemplatePackageOperation>("/template-packages/imports/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmationToken, allowOlderVersion }),
    }),
    templatePackageOperation: (operationId: string) => request<TemplatePackageOperation>(`/template-packages/operations/${encodeURIComponent(operationId)}`),
    templatePackagePreviewUrl: (operationId: string, role: "preview" | "clean-template") => `${API}/template-packages/operations/${encodeURIComponent(operationId)}/assets/${role}`,
    deleteTemplatePackageOperation: (operationId: string) => request<void>(`/template-packages/operations/${encodeURIComponent(operationId)}`, {
        method: "DELETE",
    }),
    activateTemplatePackage: (installationId: string, expectedActiveVersionId: string | null, expectedRevision: number) => request<TemplatePackageActivation>(`/template-packages/installations/${encodeURIComponent(installationId)}/activate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expectedActiveVersionId, expectedRevision }),
    }),
    jobs: (limit = 20, options?: {
        attentionOnly?: boolean;
    }) => {
        const query = new URLSearchParams({ limit: String(limit) });
        if (options?.attentionOnly)
            query.set("attentionOnly", "true");
        return request<Job[]>(`/jobs?${query.toString()}`);
    },
    job: (id: string) => request<Job>(`/jobs/${id}`),
    artifacts: (id: string) => request<{
        items: Artifact[];
    }>(`/jobs/${id}/artifacts`),
    result: (id: string) => request<OcrResult>(`/ocr-jobs/${id}/result`),
    reviewContext: (id: string) => request<{
        identityFields: DocumentIdentity[];
    }>(`/ocr-jobs/${id}/review-context`),
    pageReview: (id: string) => request<PagedReviewResult>(`/ocr-jobs/${id}/page-review`),
    confirmPages: (id: string, baseRevision: number, reviewedPageIds: string[], documentName: string) => request<PagedReviewResult>(`/ocr-jobs/${id}/page-review/confirm`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseRevision, reviewedPageIds, documentName }),
    }),
    reopenPages: (id: string, baseRevision: number) => request<PagedReviewResult>(`/ocr-jobs/${id}/page-review/reopen`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseRevision }),
    }),
    correctPage: (id: string, pageId: string, baseRevision: number, fields: PageFieldEdit[]) => request<PagedReviewResult>(`/ocr-jobs/${id}/page-review/${encodeURIComponent(pageId)}/corrections`, {
        method: "POST", body: JSON.stringify({ baseRevision, fields }),
    }),
    excelTable: (id: string) => request<ExcelTable>(`/ocr-jobs/${id}/excel-table`),
    createTemplate: async (name: string, mode: string, files: File[], idempotencyKey: string, fieldNames: string[] = []) => {
        const body = new FormData();
        body.set("name", name);
        body.set("mode", mode);
        body.set("fieldNames", JSON.stringify(fieldNames));
        files.forEach((file) => body.append("files", file));
        return request<{
            jobId: string;
        }>("/template-jobs", {
            method: "POST",
            headers: { "Idempotency-Key": idempotencyKey },
            body,
        });
    },
    createSpreadsheetTemplate: async (name: string, file: File, idempotencyKey: string) => {
        const body = new FormData();
        body.set("name", name);
        body.set("file", file);
        return request<{
            jobId: string;
        }>("/spreadsheet-template-jobs", {
            method: "POST",
            headers: { "Idempotency-Key": idempotencyKey },
            body,
        });
    },
    templatePrintUrl: (versionId: string, autoPrint = true) => `${API}/template-versions/${encodeURIComponent(versionId)}/print?auto_print=${String(autoPrint)}`,
    templateBoundaryImageUrl: (jobId: string) => `${API}/template-jobs/${encodeURIComponent(jobId)}/boundary-image`,
    saveTemplateBoundary: (jobId: string, corners: Array<{
        x: number;
        y: number;
    }>) => request<Job>(`/template-jobs/${encodeURIComponent(jobId)}/boundary`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ corners }),
    }),
    createOcr: async (templateVersionId: string, file: File, enabled: boolean, idempotencyKey: string, predecessorJobId?: string) => {
        const body = new FormData();
        body.set("templateVersionId", templateVersionId);
        body.set("ocrEnabled", String(enabled));
        if (predecessorJobId) body.set("predecessorJobId", predecessorJobId);
        body.set("file", file);
        return request<{
            jobId: string;
        }>("/ocr-jobs", {
            method: "POST",
            headers: { "Idempotency-Key": idempotencyKey },
            body,
        });
    },
    seriesCandidates: (templateVersionId: string, q?: string) => request<{
        items: SeriesCandidate[];
    }>(`/document-series/candidates?${new URLSearchParams({ templateVersionId, ...(q ? { q } : {}) }).toString()}`),
    approve: (id: string, expectedRevision: number, baseRevision?: string) => templateMutation<Template["versions"][number]>(`/template-versions/${id}/approve${baseRevision
        ? `?base_revision=${encodeURIComponent(baseRevision)}`
        : ""}`, expectedRevision, { method: "POST" }),
    editTemplateVersion: (id: string, expectedRevision: number, baseRevision?: string) => templateMutation<Template["versions"][number]>(`/template-versions/${id}/edit${baseRevision
        ? `?base_revision=${encodeURIComponent(baseRevision)}`
        : ""}`, expectedRevision, { method: "POST" }),
    duplicateTemplateVersion: (id: string, expectedRevision: number, name: string, activate = false) => templateMutation<{
        templateId: string;
        templateVersionId: string;
    }>(`/template-versions/${encodeURIComponent(id)}/duplicate`, expectedRevision, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, activate }),
    }),
    reject: (id: string, expectedRevision: number) => templateMutation<Record<string, unknown>>(`/template-versions/${id}/reject`, expectedRevision, { method: "POST" }),
    reprocess: (id: string, expectedRevision: number) => templateMutation<{
        jobId: string;
    }>(`/template-versions/${id}/reprocess`, expectedRevision, { method: "POST" }),
    saveMask: async (id: string, expectedRevision: number, file: Blob) => {
        const body = new FormData();
        body.set("file", file, "handwriting_mask.png");
        return templateMutation<Record<string, unknown>>(`/template-versions/${id}/handwriting-mask`, expectedRevision, { method: "PUT", body });
    },
    fieldMappings: (id: string) => request<FieldMappingsResponse>(`/template-versions/${id}/field-mappings`),
    templateCandidateSettings: (templateId: string, templateVersionId: string) => request<TemplateCandidateSettings>(`/templates/${templateId}/candidate-settings?templateVersionId=${encodeURIComponent(templateVersionId)}`),
    saveTemplateCandidateSettings: (templateId: string, templateVersionId: string, expectedRevision: number, sets: Array<{
        name: string;
        values: string[];
        allowedSuffixes: string[];
        targetPatterns: string[];
    }>) => request<TemplateCandidateSettings>(`/templates/${templateId}/candidate-settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ templateVersionId, expectedRevision, sets }),
    }),
    layout: (id: string) => request<LayoutDefinition>(`/template-versions/${id}/layout`),
    detectLayout: (id: string, expectedRevision: number) => templateMutation<LayoutDefinition>(`/template-versions/${id}/layout/detect`, expectedRevision, {
        method: "POST",
    }),
    recognizeLayoutLabels: (id: string, expectedRevision: number, layout: LayoutDefinition) => templateMutation<LayoutDefinition>(`/template-versions/${id}/layout/recognize-labels`, expectedRevision, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(layout),
    }),
    verifyLayout: (id: string, expectedRevision: number) => templateMutation<LayoutDefinition>(`/template-versions/${id}/layout/verify`, expectedRevision, {
        method: "POST",
    }),
    templateDefinition: (id: string) => request<TemplateDefinitionRevision | null>(`/template-versions/${id}/template-definition`),
    templateDefinitionHistory: (id: string) => request<TemplateDefinitionRevision[]>(`/template-versions/${id}/template-definition/history`),
    generateTemplateDefinition: (id: string, expectedRevision: number) => templateMutation<TemplateDefinitionRevision>(`/template-versions/${id}/template-definition/generate`, expectedRevision, {
        method: "POST",
    }),
    saveTemplateDefinition: (id: string, expectedRevision: number, definition: TemplateDefinition) => templateMutation<TemplateDefinitionRevision>(`/template-versions/${id}/template-definition`, expectedRevision, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(definition),
    }),
    approveTemplateDefinition: (id: string, expectedRevision: number, definition: TemplateDefinition) => templateMutation<TemplateDefinitionRevision>(`/template-versions/${id}/template-definition/approve`, expectedRevision, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(definition),
    }),
    correct: (id: string, revision: number, value: OcrDocument) => request<OcrResult>(`/ocr-jobs/${id}/corrections`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            baseRevision: revision,
            fields: value.fields,
            reviewItems: value.reviewItems,
        }),
    }),
    confirm: (id: string, revision: number, documentName?: string, acceptIdentityMismatch = false) => request<OcrResult>(`/ocr-jobs/${id}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            baseRevision: revision,
            ...(documentName ? { documentName } : {}),
            ...(acceptIdentityMismatch ? { acceptIdentityMismatch: true } : {}),
        }),
    }),
    reopenResult: (id: string, baseRevision: number) => request<OcrResult>(`/ocr-jobs/${id}/reopen`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ baseRevision }) }),
    retry: (id: string, revision: number | null | undefined) => request<Job>(`/jobs/${id}/retry${revision == null ? "" : `?expectedRevision=${revision}`}`, { method: "POST" }),
    cancel: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
};
export const absoluteArtifactUrl = (path: string) => path;
export const exportUrl = (jobId: string, format: "xlsx") => `${API}/ocr-jobs/${jobId}/export?format=${format}`;
