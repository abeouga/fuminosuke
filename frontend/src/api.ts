import type { Artifact, FieldMapping, FieldMappingsResponse, Health, IndividualRecordSearch, Job, OcrDocument, OcrResult, Problem, Template } from "./types";

const API = "/api/v1";

export class ApiError extends Error {
  code: string;
  constructor(message: string, code = "API_ERROR") {
    super(message);
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    let problem: Problem = {};
    try {
      problem = (await response.json()) as Problem;
    } catch {
      // A non-JSON reverse-proxy error still becomes a safe local message.
    }
    throw new ApiError(
      problem.detail ?? problem.message ?? "バックエンドとの通信に失敗しました。",
      problem.code,
    );
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  templates: () => request<Template[]>("/templates"),
  template: (id: string) => request<Template>(`/templates/${id}`),
  job: (id: string) => request<Job>(`/jobs/${id}`),
  artifacts: (id: string) => request<{ items: Artifact[] }>(`/jobs/${id}/artifacts`),
  result: (id: string) => request<OcrResult>(`/ocr-jobs/${id}/result`),
  searchIndividualRecords: (individualId: string) =>
    request<IndividualRecordSearch>(
      `/individual-records?individualId=${encodeURIComponent(individualId)}`,
    ),
  createTemplate: async (name: string, mode: string, files: File[]) => {
    const body = new FormData();
    body.set("name", name);
    body.set("mode", mode);
    files.forEach((file) => body.append("files", file));
    return request<{ jobId: string }>("/template-jobs", { method: "POST", body });
  },
  createOcr: async (templateVersionId: string, file: File, enabled: boolean) => {
    const body = new FormData();
    body.set("templateVersionId", templateVersionId);
    body.set("ocrEnabled", String(enabled));
    body.set("file", file);
    return request<{ jobId: string }>("/ocr-jobs", { method: "POST", body });
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
        reviewItems: value.reviewItems,
      }),
    }),
  confirm: (id: string) => request<OcrResult>(`/ocr-jobs/${id}/confirm`, { method: "POST" }),
  retry: (id: string) => request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  cancel: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
};

export const absoluteArtifactUrl = (path: string) => path;
export const exportUrl = (jobId: string, format: "json" | "csv") =>
  `${API}/ocr-jobs/${jobId}/export?format=${format}`;
export const individualRecordSearchUrl = (individualId: string) =>
  `${API}/individual-records?individualId=${encodeURIComponent(individualId)}`;
