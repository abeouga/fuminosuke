export type Health = {
  status: "ok";
  database: "ok";
  jobWorker: "running" | "not_started";
  ocrProvider: "mock" | "openai" | "disabled";
};

export type TemplateVersion = {
  id: string;
  versionNumber: number;
  mode: string;
  status: "DRAFT" | "ACTIVE" | "REJECTED" | "ARCHIVED";
  sourceJobId: string | null;
  canonicalWidth: number;
  canonicalHeight: number;
  quality: Record<string, unknown>;
};

export type Template = {
  id: string;
  name: string;
  documentType: string;
  activeVersionId: string | null;
  createdAt: string;
  versions: TemplateVersion[];
};

export type Job = {
  id: string;
  jobType: "PREPARE_TEMPLATE" | "EXTRACT_DOCUMENT";
  status: string;
  progressStage: string | null;
  progressPercent: number;
  attemptNumber: number;
  error: { code: string; message: string } | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
};

export type Artifact = { name: string; mediaType: string; url: string };

export type FieldMapping = {
  mappingId: string;
  targetPath: string;
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  padding: number;
  enabled: boolean;
  modelDetail: string;
};

export type FieldMappingTarget = { path: string; label: string; section: string };

export type FieldMappingsResponse = {
  canonicalWidth: number;
  canonicalHeight: number;
  mappings: FieldMapping[];
  targets: FieldMappingTarget[];
};

export type ReadingStatus = "readable" | "uncertain" | "blank";
export type FieldReading = {
  raw: string | null;
  normalized: string | null;
  status: ReadingStatus;
  alternatives: string[];
  reviewReason: string | null;
  sourceRegionId: string;
  boundingBox: { x: number; y: number; width: number; height: number } | null;
};

export type ReviewItem = {
  code: string;
  path: string;
  message: string;
  severity: "warning" | "error";
  sourceRegionId: string | null;
};

export type OcrDocument = {
  schemaVersion: string;
  templateId: string;
  templateVersion: string;
  document: Record<string, FieldReading>;
  saleCalves: Array<Record<string, FieldReading>>;
  pedigree: Record<string, FieldReading>;
  calvings: Array<Record<string, FieldReading>>;
  reviewItems: ReviewItem[];
  processingMetadata: Record<string, unknown>;
};

export type OcrResult = {
  jobId: string;
  revision: number;
  result: OcrDocument;
  validation: { valid: boolean; issues: ReviewItem[] };
  provider: string;
  modelId: string | null;
  promptVersion: string | null;
  apiCallCount: number;
  inputTokens: number | null;
  outputTokens: number | null;
  estimatedCost: number | null;
  confirmed: boolean;
  individualId: string | null;
};

export type IndividualRecord = {
  recordId: string;
  individualId: string;
  sourceJobId: string;
  ocrResultRevision: number;
  confirmedAt: string;
  data: OcrDocument;
};

export type IndividualRecordSearch = {
  query: string;
  individualId: string;
  count: number;
  records: IndividualRecord[];
};

export type Problem = { code?: string; detail?: string; message?: string };
