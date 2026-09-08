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

export type TemplatePackageDriveItem = {
  kind: "RELEASE" | "DRAFT";
  fileId: string;
  fileName: string;
  publisherId: string;
  packageId: string;
  releaseVersion: string;
  contentDigest: string;
  packageSha256: string;
  draftId: string | null;
  draftRevision: number | null;
  baseReleaseVersion: string | null;
  driveVersion: string;
  headRevisionId: string | null;
  etag: string | null;
  modifiedTime: string | null;
  fileBytes: number;
  trustStatus: "UNVERIFIED";
  syncStatus: "REMOTE_ONLY" | "SYNCED" | "CONFLICT";
};

export type TemplateCandidateBinding = {
  targetPattern: string;
  label: string;
};

export type TemplateCandidateSet = {
  id: string;
  name: string;
  values: string[];
  allowedSuffixes: string[];
  bindings: TemplateCandidateBinding[];
};

export type TemplateCandidateSettings = {
  templateId: string;
  sets: TemplateCandidateSet[];
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

export type OpenAIUsageRecord = {
  jobId: string;
  jobType: string;
  jobStatus: string;
  attemptNumber: number;
  recordedAt: string;
  modelId: string | null;
  apiCallCount: number;
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  estimatedCost: string | null;
  durationMs: number | null;
  requestId: string | null;
  source: "audit" | "historical";
};

export type OpenAIUsageReport = {
  summary: {
    jobCount: number;
    recordCount: number;
    apiCallCount: number;
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
    estimatedCost: string | null;
  };
  records: OpenAIUsageRecord[];
};

export type PortableBackupExport = {
  id: string;
  fileName: string;
  fileBytes: number;
  fileCount: number;
  sourceBytes: number;
  createdAt: string;
  expiresAt: string;
  downloadUrl: string;
};

export type PortableBackupOperation = {
  operationId: string;
  status: "RUNNING" | "READY" | "FAILED";
  stage: string;
  progressPercent: number;
  message: string;
  bytesProcessed: number;
  totalBytes: number | null;
  backupId: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  updatedAt: string;
};

export type PortableRestoreOperation = {
  operationId: string;
  status: "RUNNING" | "READY" | "APPLYING" | "COMPLETED" | "FAILED";
  stage: string;
  progressPercent: number;
  message: string;
  fileName: string;
  fileBytes: number;
  bytesProcessed: number;
  totalBytes: number | null;
  fileCount: number | null;
  sourceBytes: number | null;
  backupCreatedAt: string | null;
  schemaRevision: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  rollbackExpiresAt: string | null;
  updatedAt: string;
};

export type WorkspaceSyncStatus = {
  enabled: boolean;
  status: string;
  stage: string;
  progressPercent: number;
  message: string;
  dirty: boolean;
  remoteRevision: number;
  latestSnapshotFileId: string | null;
  latestSnapshotSha256: string | null;
  latestSnapshotCreatedAt: string | null;
  leaseOwnerDeviceId: string | null;
  leaseOwnerDeviceName: string | null;
  leaseExpiresAt: string | null;
  lastAttemptAt: string | null;
  lastSuccessAt: string | null;
  error: string | null;
  deviceId: string;
  deviceName: string;
  readOnly: boolean;
};

export type WorkspaceSnapshot = {
  fileId: string;
  fileName: string;
  revision: number;
  sha256: string;
  deviceId: string;
  createdAt: string | null;
  fileBytes: number;
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
  mappingType?: "text" | "mark";
  markRecognitionMode?: "automatic" | "manual";
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

export type FieldMappingsResponse = {
  canonicalWidth: number;
  canonicalHeight: number;
  mappings: FieldMapping[];
  documentIdentity: DocumentIdentity | null;
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
  textConfidence: number | null;
  textDirection?: "horizontal" | "vertical";
  textAlignment?: "left" | "center" | "right";
  textVerticalAlignment?: "top" | "middle" | "bottom" | null;
  source: "detected" | "manual";
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
};

export type LayoutMarkSet = {
  id: string;
  targetPath: string;
  recognitionMode: "automatic" | "manual";
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
  /** Legacy generated title; displayName is preferred for user-facing text. */
  title: string;
  displayName?: string | null;
  collectionPath: string;
  recordType: "saleCalf" | "calving" | "generic";
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
  legacyTargetPath: string | null;
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
  document: Record<string, FieldReading>;
  saleCalves: Array<Record<string, FieldReading>>;
  pedigree: Record<string, FieldReading>;
  calvings: Array<Record<string, FieldReading>>;
  customFields: Record<string, FieldReading>;
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
  markRecognitionMode?: "automatic" | "manual";
  markSelectionMode?: "single" | "multiple";
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
  reviewStructure?: ReviewStructure;
  spreadsheetSync?: SpreadsheetSyncStatus;
  documentResolution?: DocumentResolution;
  documentName: string;
};

export type DocumentResolutionPage = {
  pageId: string | null;
  sourceJobId: string | null;
  pageNumber: number;
  active: boolean;
  confirmedAt: string | null;
};

export type DocumentResolutionCandidate = {
  documentId: string | null;
  sourceJobId: string | null;
  individualId: string;
  cattleName: string | null;
  documentName?: string | null;
  pageCount: number;
  lastConfirmedAt: string;
  pages: DocumentResolutionPage[];
};

export type DocumentResolutionChange = {
  path: string;
  kind: "ADDED" | "CHANGED" | "REMOVED";
  previousValue: string | null;
  currentValue: string | null;
};

export type DocumentResolution = {
  required: boolean;
  resolved: boolean;
  action: "ADD_PAGE" | "RESCAN_PAGE" | "NEW_DOCUMENT" | null;
  documentId: string | null;
  pageId: string | null;
  candidates: DocumentResolutionCandidate[];
  changes: DocumentResolutionChange[];
};

export type SpreadsheetSyncStatus = {
  enabled: boolean;
  status: "disabled" | "pending" | "ready" | "synced" | "failed";
  spreadsheetUrl: string | null;
  fileName?: string | null;
  lastError: string | null;
};

export type OutputLayoutTemplateStatus = {
  active: boolean;
  templateVersionId: string;
  revision: number | null;
  sheetName: string;
  bindingCount: number;
  sourceJobId: string | null;
  createdAt: string | null;
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

export type DriveOutputFolder = {
  pickerConfigured: boolean;
  pickerConfigurationError: string | null;
  folderId: string | null;
  folderName: string | null;
  roots: Array<{ folderId: string; folderName: string }>;
};

export type DrivePickerSession = {
  accessToken: string;
  developerKey: string;
  appId: string;
};

export type TemplateOutputFolder = {
  templateFolderId: string | null;
  templateFolderName: string | null;
  effectiveFolderId: string | null;
  effectiveFolderName: string | null;
  usesDefault: boolean;
};

export type DocumentLibraryPage = {
  pageId: string;
  pageNumber: number;
  sourceJobId: string;
  templateVersionId: string | null;
  thumbnailUrl: string;
  confirmedAt: string | null;
  versionCount: number;
  history: Array<{
    pageId: string;
    sourceJobId: string;
    createdAt: string;
    confirmedAt: string | null;
    active: boolean;
  }>;
};

export type DocumentLibraryLedger = {
  documentId: string;
  templateId: string;
  templateName: string;
  individualId: string;
  cattleName: string | null;
  displayTitle?: string;
  displayTitleLabel?: string;
  displaySubtitle?: string | null;
  displaySubtitleLabel?: string | null;
  currentJobId: string | null;
  updatedAt: string;
  pageCount: number;
  pages: DocumentLibraryPage[];
  spreadsheetId: string | null;
  spreadsheetUrl: string | null;
  driveFolderPath: string | null;
  spreadsheetSyncStatus: string | null;
  spreadsheetSyncError: string | null;
  libraryFolderId: string | null;
  locationVersion: number;
  libraryName?: string | null;
};

export type DocumentLibraryFolder = {
  id: string;
  parentId: string | null;
  name: string;
  documentCount: number;
  createdAt: string;
  updatedAt: string;
};

export type DocumentLibraryGroup = {
  individualId: string;
  cattleName: string | null;
  displayTitle?: string;
  displayTitleLabel?: string;
  displaySubtitle?: string | null;
  displaySubtitleLabel?: string | null;
  ledgerCount: number;
  pageCount: number;
  lastUpdatedAt: string;
  ledgers: DocumentLibraryLedger[];
};

export type DocumentLibrary = {
  count: number;
  folders: DocumentLibraryFolder[];
  documents: DocumentLibraryLedger[];
  items: DocumentLibraryGroup[];
};

export type DocumentLibrarySyncResult = {
  mode?: "BIDIRECTIONAL" | "LIBRARY_TO_DRIVE";
  status: "SYNCED" | "PARTIAL" | "ERROR" | "REAUTHORIZATION_REQUIRED";
  lastAttemptAt: string;
  lastSuccessAt: string | null;
  foldersImported: number;
  foldersMirrored: number;
  foldersUpdated: number;
  documentsUpdated: number;
  conflicts: number;
  conflictItems: Array<{
    code: string;
    resourceId: string;
    resourceName?: string;
    mappingId?: string;
    driveRootId?: string;
    driveRootName?: string;
    detail: string;
  }>;
  error: string | null;
};

export type OcrLibraryContext = {
  action: "ADD_PAGE" | "RESCAN_PAGE" | "NEW_DOCUMENT";
  individualId: string;
  cattleName: string | null;
  templateVersionId: string;
  documentId?: string;
  pageId?: string;
  pageNumber?: number;
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

export type DataSearchField = {
  fieldKey: string;
  label: string;
  sectionTitle: string;
  valueType: "text" | "number" | "date" | string;
  repeated: boolean;
  identity: boolean;
  suggestions: string[];
};

export type DataSearchTemplate = {
  templateId: string;
  templateName: string;
  documentCount: number;
  fields: DataSearchField[];
};

export type DataSearchCatalog = {
  count: number;
  templates: DataSearchTemplate[];
};

export type DataSearchFilter = {
  fieldKey: string;
  operator: "exact" | "contains" | "startsWith" | "gte" | "lte" | "between" | "on" | "before" | "after";
  value: string;
  valueTo?: string | null;
};

export type DataSearchMatch = {
  fieldKey: string;
  label: string;
  sectionTitle: string;
  recordOrder: number | null;
  recordKey: string | null;
  value: string;
};

export type DataSearchItem = {
  documentId: string;
  templateId: string;
  templateName: string;
  sourceJobId: string;
  confirmedAt: string;
  pageCount: number;
  spreadsheetUrl: string | null;
  displayTitle: string;
  displayTitleLabel: string;
  displaySubtitle: string | null;
  displaySubtitleLabel: string | null;
  matches: DataSearchMatch[];
};

export type DataSearchResponse = {
  query: string;
  count: number;
  page: number;
  pageSize: number;
  items: DataSearchItem[];
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
  authorizationCurrent: boolean;
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
