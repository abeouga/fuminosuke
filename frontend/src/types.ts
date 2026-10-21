export type Health = {
  status: "ok";
  database: "ok";
  jobWorker: "running" | "not_started";
  ocrProvider: "mock" | "openai" | "disabled";
  ocrTextRecognitionToggleEnabled: boolean;
};

export type TemplateVersion = {
  id: string;
  versionNumber: number;
  revision: number;
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

export type TemplateDeleteResult = {
  outcome: "deleted" | "archived";
};

export type TemplatePackageAssetInspection = {
  path?: string;
  role: string;
  mediaType: string;
  size: number;
  sha256: string;
  width: number | null;
  height: number | null;
  sanitized?: boolean;
};

export type TemplatePackageExportInspection = {
  templateName: string;
  templateVersionNumber: number;
  mode: string;
  candidateSets: Array<{
    name: string;
    valueCount: number;
    values: string[];
    targetPatterns: string[];
  }>;
  assets: TemplatePackageAssetInspection[];
  outputLayoutIncluded: boolean;
  spreadsheetSourceIncluded: boolean;
  warnings: string[];
  contentDigest: string;
  confirmationDigest: string;
};

export type TemplatePackageImportInspection = {
  formatVersion: number;
  schemaVersion: number;
  publisherId: string;
  packageId: string;
  version: string;
  name: string;
  contentDigest: string;
  packageSha256: string;
  minimumAppVersion: string;
  requiredFeatures: string[];
  optionalFeatures: string[];
  mode: string;
  conflictStatus: "NEW_IDENTITY" | "SAME_RELEASE" | "NEWER_RELEASE" | "OLDER_RELEASE" | "VERSION_CONFLICT";
  existingLocalTemplateId: string | null;
  existingLocalTemplateVersionId: string | null;
  documentIdentityFieldCount: number;
  documentNameFieldPaths: string[];
  candidateSetCount: number;
  assets: TemplatePackageAssetInspection[];
  spreadsheetSourceIncluded: boolean;
  outputLayoutIncluded: boolean;
  trustStatus: "UNVERIFIED";
};

export type TemplatePackageImportResult = {
  installationId: string;
  localTemplateId: string;
  localTemplateVersionId: string;
  localJobId: string;
  contentDigest: string;
  releaseVersion: string;
  created: boolean;
  activeVersionChanged: false;
};

export type TemplatePackageOperation = {
  operationId: string;
  kind: "IMPORT" | "EXPORT";
  status: "READY" | "RUNNING" | "COMPLETED" | "FAILED";
  stage: string;
  progressPercent: number;
  message: string;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
  fileName: string | null;
  packageSha256: string | null;
  confirmationToken: string | null;
  importInspection: TemplatePackageImportInspection | null;
  exportInspection: TemplatePackageExportInspection | null;
  result: TemplatePackageImportResult | null;
  errorCode: string | null;
  errorMessage: string | null;
  errorCategory: "SECURITY" | "COMPATIBILITY" | "CONFLICT" | "VALIDATION" | "SYSTEM" | null;
};

export type TemplatePackageActivation = {
  installationId: string;
  localTemplateId: string;
  localTemplateVersionId: string;
  previousActiveVersionId: string | null;
  activeVersionId: string;
  candidateSetCount: number;
  outputLayoutMaterialized: boolean;
  alreadyActive: boolean;
};

export type TemplateFieldSetting = {
  targetPatterns: string[];
  label: string;
  keywords: string[];
  keywordsEnabled: boolean;
  keywordsForced: boolean;
  instruction: string;
};

export type TemplateFieldSettings = {
  resourceRevision: number;
  templateId: string;
  items: TemplateFieldSetting[];
};

export type Job = {
  id: string;
  jobType: "PREPARE_TEMPLATE" | "EXTRACT_DOCUMENT";
  status: string;
  progressStage: string | null;
  progressPercent: number;
  attemptNumber: number;
  resourceRevision?: number | null;
  error: { code: string; message: string; stackTrace?: string | null } | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
};

export type Artifact = { name: string; mediaType: string; url: string };

export type FieldMapping = {
  readingMode?: "number" | null;
  sequenceNumber?: number | null;
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
  vertices?: { x: number; y: number }[];
  printedLabelText?: string;
  mappingType?: "text" | "mark";
  markRecognitionMode?: "automatic" | "manual" | "symbol" | "checklist";
  markSelectionMode?: "single" | "multiple";
  markOptions?: Array<{
    id: string;
    label: string;
    value: string;
    x: number;
    y: number;
    width: number;
    height: number;
  }>;
  compositeSubfields?: LayoutCompositeSubfieldMapping[];
};

export type LayoutCompositeSubfieldMapping = {
  id: string;
  semanticType: "address_prefecture" | "address_municipality";
  targetPath: string;
  label: string;
  xRatio: number;
  yRatio: number;
  widthRatio: number;
  heightRatio: number;
};

export type FieldMappingTarget = {
  path: string;
  label: string;
  section: string;
  aliases?: string[];
};

export type DocumentIdentity = {
  mappingId: string;
  targetPath: string;
  label: string;
  valueType: "text" | "digits";
  exactLength: number | null;
  matchRole: "primary" | "confirmation";
  displayRole: "title" | "subtitle" | "none";
  matchGroupId?: string | null;
  matchGroupLabel?: string | null;
  componentOrder?: number;
};

export type DocumentNameRule = {
  fieldPaths: string[];
  separator: string;
  prefix: string;
  suffix: string;
};

export type TemplateDocumentNameSettings = {
  rule: DocumentNameRule | null;
  /** The rule the template was made with. */
  templateRule: DocumentNameRule | null;
  /** True when the rule was changed after the template was made. */
  custom: boolean;
  candidates: { targetPath: string; label: string; identity: boolean }[];
};

export type FieldMappingsResponse = {
  fieldErrors?: { field: string; message: string }[];
  canonicalWidth: number;
  canonicalHeight: number;
  mappings: FieldMapping[];
  documentIdentityFields: DocumentIdentity[];
  documentName: DocumentNameRule | null;
  targets: FieldMappingTarget[];
  validForOcr: boolean;
  warnings: string[];
};

export type LayoutCell = {
  id: string;
  role: "label" | "parent_label" | "input" | "mark" | "record_key_cell" | "column_key_cell" | "ignored";
  x: number;
  y: number;
  width: number;
  height: number;
  confidence: number;
  text: string;
  printedLabelText?: string;
  fieldLabel?: string | null;
  readingMode?: "number" | null;
  textConfidence: number | null;
  textDirection?: "horizontal" | "vertical";
  textAlignment?: "left" | "center" | "right";
  textVerticalAlignment?: "top" | "middle" | "bottom" | null;
  source: "detected" | "manual";
  shapeType?: "rectangle" | "polygon";
  vertices?: { x: number; y: number }[];
  virtual?: boolean;
  /** Virtual heading only: x/y/width/height belong to this saved page range. */
  pageRange?: string;
  parentLabelCellId?: string | null;
  markSets?: LayoutMarkSet[];
  compositeSubfields?: LayoutCompositeSubfield[];
};

export type LayoutCellMergePart = {
  cell: LayoutCell;
  partType: "input" | "literal" | "ignored";
  literal: string;
};

export type LayoutCellMerge = {
  id: string;
  parentCellId: string;
  direction: "horizontal" | "vertical";
  parts: LayoutCellMergePart[];
};

export type LayoutCompositeSubfield = {
  id: string;
  semanticType: "address_prefecture" | "address_municipality";
  label: string;
  xRatio: number;
  yRatio: number;
  widthRatio: number;
  heightRatio: number;
};

export type LayoutMarkOption = {
  id: string;
  label: string;
  value: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Checklist or symbol box: the item's text field written next to it, e.g. 「その他（　）」. */
  writeInTargetPath?: string;
};

export type LayoutMarkSet = {
  id: string;
  targetPath: string;
  recognitionMode: "automatic" | "manual" | "symbol" | "checklist";
  selectionMode: "single" | "multiple";
  options: LayoutMarkOption[];
};

export type LayoutRelation = {
  id: string;
  inputCellId: string;
  labelCellIds: string[];
  parentLabelCellId?: string | null;
  axis: "horizontal" | "vertical";
  order: number;
  targetPath: string;
};

export type LayoutLabelPoolEntry = {
  id: string;
  label: string;
  targetPath: string;
  section: string;
  aliases: string[];
  source: "manual" | "ocr";
};

export type LayoutRecordSection = {
  id: string;
  /** Legacy/internal title. displayName=null explicitly disables the optional user-facing name. */
  title: string;
  displayName?: string | null;
  collectionPath: string;
  recordType: "generic";
  recordLabelCellIds: string[];
  /** Legacy single-column format. normalizeLayout migrates this value. */
  recordLabelCellId?: string | null;
  parentLabelCellId: string | null;
  rowKeyStrategy?: "printed" | "position";
  rowKeyCellIds?: string[];
  includeEmptyRows?: boolean;
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
  recordKeyCellId?: string | null;
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

export type ReviewStructureField = {
  id: string;
  mappingId: string;
  targetPath: string;
  label: string;
  groupId?: string;
  inputCellId: string | null;
  relationId: string | null;
};

export type ReviewStructure = {
  schemaVersion: number;
  fields: ReviewStructureField[];
  recordSections: Array<{
    id: string;
    title: string;
    collectionPath: string;
    columns: Array<{ id: string; label: string }>;
    rows: Array<{
      id: string;
      order: number;
      cells: Array<{ columnId: string; fieldId: string | null }>;
    }>;
  }>;
  parentSections: Array<{
    id: string;
    title: string;
    fieldIds: string[];
  }>;
  independentFieldIds: string[];
};

/** Printed choices read from the template image for selection boxes (not saved by itself). */
export type MarkOptionSuggestion = {
    cells: Array<{ cellId: string; options: Array<{ label: string; x: number; y: number; width: number; height: number }> }>;
    similar: Array<{ cellId: string; cells: Array<{ cellId: string; options: Array<{ label: string; x: number; y: number; width: number; height: number }> }> }>;
};

export type LayoutDefinition = {
  layoutSchemaVersion?: number;
  canonicalWidth: number;
  canonicalHeight: number;
  detector: string;
  ocrAvailable: boolean;
  cells: LayoutCell[];
  cellMerges?: LayoutCellMerge[];
  relations: LayoutRelation[];
  labelPool?: LayoutLabelPoolEntry[];
  recordSections?: LayoutRecordSection[];
  recordGroups?: LayoutRecordGroup[];
  recordStructureVerified?: boolean;
  allowCrossAxisLabels: boolean;
  warnings: string[];
};

export type TemplateBox = { x: number; y: number; width: number; height: number };

export type TemplateCell = {
  id: string;
  type: "LABEL" | "VALUE" | "PARENT_LABEL" | "IGNORE";
  text: string;
  bbox: TemplateBox;
  detectionConfidence: number;
  textConfidence: number | null;
  sourceRole: string | null;
  markSets?: LayoutMarkSet[];
};

export type TemplateField = {
  id: string;
  key: string;
  name: string;
  labelCellId: string | null;
  valueCellId: string;
  valueType: "text" | "integer" | "decimal" | "date" | "boolean" | "enum";
  required: boolean;
  confidence: number;
  reasonCodes: string[];
  sourceTargetPath: string | null;
  markSetId?: string | null;
};

export type TemplateRecord = {
  id: string;
  name: string;
  order: number;
  bbox: TemplateBox;
  fields: TemplateField[];
  recordKeyValue: string | null;
  confidence: number;
  reasonCodes: string[];
};

export type TemplateSection = {
  id: string;
  key: string;
  name: string;
  parentLabelCellId: string | null;
  bbox: TemplateBox;
  records: TemplateRecord[];
  confidence: number;
  reasonCodes: string[];
};

export type TemplateDefinition = {
  schemaVersion: string;
  templateVersionId: string;
  templateName: string;
  state: "AI_GENERATED" | "USER_REVIEWING" | "APPROVED";
  canonicalWidth: number;
  canonicalHeight: number;
  cells: TemplateCell[];
  geometry: unknown[];
  standaloneFields: TemplateField[];
  sections: TemplateSection[];
  unassignedCellIds: string[];
  confidence: number;
  phases: Array<{ phase: string; confidence: number; reasonCodes: string[] }>;
  warnings: string[];
};

export type TemplateDefinitionValidation = {
  valid: boolean;
  issues: Array<{
    severity: "ERROR" | "WARNING";
    code: string;
    path: string;
    message: string;
    cellIds: string[];
  }>;
};

export type TemplateDefinitionRevision = {
  id: string;
  revision: number;
  source: "AI_GENERATED" | "HUMAN_REVISION" | "APPROVED";
  definition: TemplateDefinition;
  validation: TemplateDefinitionValidation;
  createdAt: string;
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
  fields: Record<string, FieldReading>;
  fieldLabels: Record<string, string>;
  genericData?: {
    templateName: string;
    standaloneFields: Array<{
      fieldId: string;
      key: string;
      name: string;
      valueType: string;
      reading: FieldReading;
    }>;
    sections: Array<{
      sectionId: string;
      key: string;
      name: string;
      records: Array<{
        recordId: string;
        name: string;
        order: number;
        recordKeyValue: string | null;
        fields: Array<{
          fieldId: string;
          key: string;
          name: string;
          valueType: string;
          reading: FieldReading;
        }>;
      }>;
    }>;
  } | null;
  genericValues?: Record<string, unknown> | null;
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
  markRecognitionMode?: "automatic" | "manual" | "symbol" | "checklist";
  markSelectionMode?: "single" | "multiple";
  markOptions?: Array<{ label: string; value: string; writeInTargetPath?: string }>;
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
  fieldPreviews: OcrFieldPreview[];
  recordStructure?: RecordStructure;
  reviewStructure?: ReviewStructure;
  documentName: string;
  /** Present when the job was read as an update of a confirmed document. */
  series?: OcrSeriesInfo | null;
};

export type OcrSeriesInfo = {
  predecessorJobId: string;
  predecessorDocumentName: string | null;
  seriesVersion: number;
  /** "blank_only": filled fields kept from the previous version; "full": every field read again. */
  mode: "blank_only" | "full" | string;
  carriedPaths: string[];
  /** value: confirmed value; raw: the text read from the paper (the same paper reads the same). */
  predecessorIdentity: { targetPath: string; label: string; value: string | null; raw: string | null }[];
};

export type Problem = { code?: string; detail?: string; message?: string };

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
