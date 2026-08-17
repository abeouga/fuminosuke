export type Health = {
  status: "ok";
  database: "ok";
  jobWorker: "running" | "not_started";
  ocrProvider: "mock" | "openai" | "disabled";
};

export type SireName = {
  id: string;
  name: string;
  createdAt: string;
};

export type SireNamePool = {
  items: SireName[];
  count: number;
  allowedSuffixes: string[];
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
  createdAt: string;
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
  printedLabelText?: string;
};

export type FieldMappingTarget = { path: string; label: string; section: string };

export type FieldMappingsResponse = {
  canonicalWidth: number;
  canonicalHeight: number;
  mappings: FieldMapping[];
  targets: FieldMappingTarget[];
  validForOcr: boolean;
  warnings: string[];
};

export type LayoutCell = {
  id: string;
  role: "label" | "input" | "mark" | "ignored";
  x: number;
  y: number;
  width: number;
  height: number;
  confidence: number;
  text: string;
  printedLabelText?: string;
  textConfidence: number | null;
  source: "detected" | "manual";
  markSets?: LayoutMarkSet[];
};

export type LayoutMarkOption = {
  id: string;
  label: string;
  value: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type LayoutMarkSet = {
  id: string;
  targetPath: string;
  selectionMode: "single" | "multiple";
  options: LayoutMarkOption[];
};

export type LayoutRelation = {
  id: string;
  inputCellId: string;
  labelCellIds: string[];
  axis: "horizontal" | "vertical";
  order: number;
  targetPath: string;
};

export type LayoutRecordSection = {
  id: string;
  title: string;
  collectionPath: "saleCalves" | "calvings";
  recordType: "saleCalf" | "calving";
  parentLabelCellId: string | null;
  source: "manual" | "inferred";
};

export type LayoutRecordGroup = {
  id: string;
  sectionId: string;
  order: number;
  x: number;
  y: number;
  width: number;
  height: number;
  inputCellIds: string[];
  recordKeyField: string;
  recordKeyValue: string | null;
  source: "manual" | "inferred";
};

export type RecordFieldBinding = {
  mappingId: string;
  recordGroupId: string;
  fieldKey: string;
  targetPath: string;
};

export type RecordStructure = {
  layoutSchemaVersion: number;
  verified: boolean;
  canonicalWidth: number;
  canonicalHeight: number;
  sections: LayoutRecordSection[];
  groups: LayoutRecordGroup[];
  fieldBindings: RecordFieldBinding[];
};

export type LayoutDefinition = {
  layoutSchemaVersion?: number;
  canonicalWidth: number;
  canonicalHeight: number;
  detector: string;
  ocrAvailable: boolean;
  cells: LayoutCell[];
  relations: LayoutRelation[];
  recordSections?: LayoutRecordSection[];
  recordGroups?: LayoutRecordGroup[];
  recordStructureVerified?: boolean;
  allowCrossAxisLabels: boolean;
  warnings: string[];
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
  customFields: Record<string, FieldReading>;
  reviewItems: ReviewItem[];
  processingMetadata: Record<string, unknown>;
};

export type OcrFieldPreview = {
  targetPath: string;
  mappingId: string;
  artifactUrl: string;
  boundingBox: { x: number; y: number; width: number; height: number } | null;
  recordGroupId?: string | null;
  fieldKey?: string | null;
  mappingType?: "text" | "mark";
  markOptions?: Array<{ label: string; value: string }>;
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
  fieldPreviews: OcrFieldPreview[];
  recordStructure?: RecordStructure;
  spreadsheetSync?: {
    enabled: boolean;
    status: "disabled" | "pending" | "ready" | "synced" | "failed";
    spreadsheetUrl: string | null;
    lastError: string | null;
  };
};

export type IndividualRecord = {
  recordId: string;
  individualId: string;
  sourceJobId: string;
  ocrResultRevision: number;
  confirmedAt: string;
  data: OcrDocument;
  needsReview: boolean;
  reviewWarnings: string[];
};

export type IndividualRecordSearch = {
  query: string;
  individualId: string;
  count: number;
  records: IndividualRecord[];
};

export type IndividualRecordSummary = {
  individualId: string;
  cattleName: string | null;
  recordCount: number;
  lastConfirmedAt: string;
  needsReview: boolean;
};

export type IndividualRecordCatalog = {
  count: number;
  items: IndividualRecordSummary[];
};

export type SaleCalfRecordMatch = {
  recordFieldId: string;
  confirmedResultId: string;
  individualId: string | null;
  sourceJobId: string;
  confirmedAt: string;
  sectionKey: string;
  sectionTitle: string;
  recordOrder: number;
  recordKey: string | null;
  matchedValue: string | null;
  data: Record<string, FieldReading>;
  reviewWarnings: string[];
};

export type SaleCalfSireSearch = {
  query: string;
  normalizedQuery: string;
  count: number;
  matches: SaleCalfRecordMatch[];
};

export type Problem = { code?: string; detail?: string; message?: string };

export type AuthUser = {
  subject: string;
  email: string;
  displayName: string | null;
  pictureUrl: string | null;
};

export type AuthSession = {
  required: boolean;
  configured: boolean;
  authenticated: boolean;
  user: AuthUser | null;
};

export type ExcelCellKind = "text" | "identifier" | "integer" | "decimal" | "currency" | "date";

export type ExcelTable = {
  fileName: string;
  sheetName: string;
  maxColumns: number;
  rows: Array<{
    kind: "title" | "section" | "header" | "data" | "blank";
    cells: Array<{
      value: string | number | null;
      display: string;
      kind: ExcelCellKind;
    }>;
  }>;
};
