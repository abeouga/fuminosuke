export type AssistantOperation = "ocr.set_value" | "template.rename_cell" | "template.change_category" | "template.rename_choice";
export type AssistantCategory = "input" | "label" | "parent_label" | "ignored";

type InteractionContext = {
  viewInstanceId: string;
  selectionEpoch: number;
  localEditEpoch: number;
  /** The server revision is the only accepted basis for a proposal. */
  resourceRevision: number | null;
  snapshotKey: string;
};
export type AssistantContext = InteractionContext & (
  | { resourceKind: "ocr"; jobId: string; mappingId: string | null; targetPath: string | null }
  | { resourceKind: "template"; versionId: string; pageId: string | null; cellIds: string[]; markSetId: string | null; choiceId: string | null }
);
export type AssistantImage =
  | { kind: "crop"; url: string }
  | { kind: "region"; url: string; x: number; y: number; width: number; height: number; imageWidth: number; imageHeight: number };
export type AssistantTarget = {
  label: string;
  currentValue: string | null;
  category?: string;
  choiceValue?: string;
  choiceTracksLabel?: boolean;
  image?: AssistantImage;
};
export type AssistantSnapshot = {
  context: AssistantContext;
  target: AssistantTarget | null;
  operations: AssistantOperation[];
  dirty: boolean;
  busy: boolean;
  blocker: string | null;
  saveError: string | null;
  resourceBlocker?: string | null;
  /** Includes partial inline edits and drafts outside the selected field. */
  recoveryDirty?: boolean;
  recoveryBusy?: boolean;
};
export type AssistantEditAdapter = {
  /** Synchronously stops pending autosave; does not flush any edit. */
  begin: () => void;
  end: () => void;
  save: () => Promise<boolean>;
  discard: () => boolean;
  setApplying?: (applying: boolean) => void;
  saved?: (receipt: AssistantReceipt, discardDraft?: boolean) => Promise<void>;
};
export type AssistantPreviewRequest = {
  context: AssistantContext;
  operation: AssistantOperation;
  value: string | null;
};
export type AssistantPreview = {
  id: string;
  origin: "local-preview" | "server-proposal";
  context: AssistantContext;
  operation: AssistantOperation;
  target: AssistantTarget;
  before: string | null;
  after: string | null;
  beforeStoredValue?: string;
  afterStoredValue?: string;
  diffHash?: string;
  contextRevision?: number;
  expiresAt?: string;
  effects?: { label: string; before: string | null; after: string | null }[];
  undoOfId?: string | null;
};
export type AssistantReceipt = {
  requestKey: string; proposalId: string; operationId: string;
  resourceKind: "ocr" | "template"; resourceId: string; revision: number;
  target: { label: string }; after: string | null; saved: true;
  undoOfId?: string | null;
};
export type AssistantHistoryItem = {
  operationId: string; operation: string; undoOfId: string | null; revision: number;
  createdAt: string; target: { label: string }; after: string | null; canUndo: boolean; reason: string | null;
};
export type AssistantRecovery = { key: string; label: string; receipt?: AssistantReceipt; failure?: string; completed?: boolean };
export type VoiceStatus = { configured: boolean; recordingAvailable: boolean; maxAudioBytes: number; maxRecordSeconds: number; budget: { used: number; limit: number; remaining: number } };
export type VoiceTurnResult = { turnId: string; proposal?: AssistantPreview; approved?: boolean; message?: string };
/** Server operations return canonical receipts; editor state changes through the adapter. */
export interface AssistantPreviewProvider {
  preview(request: AssistantPreviewRequest, snapshot: AssistantSnapshot, signal: AbortSignal): Promise<AssistantPreview>;
  sync?: (context: AssistantContext) => Promise<void>;
  present?: (preview: AssistantPreview, signal: AbortSignal) => Promise<{ challenge: string }>;
  apply?: (preview: AssistantPreview, challenge: string, key: string, approvalTurnId?: string) => Promise<AssistantReceipt>;
  cancel?: (preview: AssistantPreview) => Promise<void>;
  reset?: () => void;
  history?: (context: AssistantContext) => Promise<AssistantHistoryItem[]>;
  undoPreview?: (operationId: string, snapshot: AssistantSnapshot, signal: AbortSignal) => Promise<AssistantPreview>;
  recovery?: () => AssistantRecovery | null;
  recover?: () => Promise<AssistantRecovery | null>;
  acknowledge?: (key: string) => Promise<void>;
  subscribe?: (listener: () => void) => () => void;
  voiceStatus?: () => Promise<VoiceStatus>;
  voiceText?: (text: string, snapshot: AssistantSnapshot, signal: AbortSignal) => Promise<VoiceTurnResult>;
  voiceStart?: (snapshot: AssistantSnapshot, signal: AbortSignal, approval?: { preview: AssistantPreview; challenge: string; speechId: string }) => Promise<string>;
  voiceAudio?: (turnId: string, audio: Blob, snapshot: AssistantSnapshot, signal: AbortSignal) => Promise<VoiceTurnResult>;
  voiceCancel?: (turnId: string) => Promise<void>;
  voiceSpeech?: (preview: AssistantPreview, challenge: string, signal: AbortSignal) => Promise<{ speechId: string; audio: string; mime: string }>;
  voiceEnded?: (speechId: string, completed: boolean) => Promise<void>;
  voiceReceiptSpeech?: (receipt: AssistantReceipt, signal: AbortSignal) => Promise<{ speechId: string; audio: string; mime: string }>;
}

export const resourceKey = (context: AssistantContext) => `${context.resourceKind}:${context.resourceKind === "ocr" ? context.jobId : context.versionId}`;

export const OPERATION_LABEL: Record<AssistantOperation, string> = {
  "ocr.set_value": "読み取り値を変更",
  "template.rename_cell": "項目の名称を変更",
  "template.change_category": "欄の種類を変更",
  "template.rename_choice": "選択肢の表示名を変更",
};
export const CATEGORY_LABEL: Record<AssistantCategory, string> = {
  input: "記入欄", label: "項目名", parent_label: "見出し", ignored: "読み取らない",
};
export const contextKey = (context: AssistantContext) => JSON.stringify(context);
