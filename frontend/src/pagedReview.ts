import type { FieldReading, OcrDocument } from "./types";

export type PageFieldEdit = Pick<FieldReading,
  "raw" | "normalized" | "status" | "alternatives" | "reviewReason"> & { mappingId: string };

export type PagedReviewResult = {
  jobId: string;
  revision: number;
  confirmed: boolean;
  documentName?: string | null;
  documentMetadata?: {
    proposedName: string | null;
    identityFields: Array<{ pageId: string; mappingId: string; targetPath: string; label: string }>;
  };
  confirmAvailable: boolean;
  outputAvailable: boolean;
  pages: Array<{
    pageId: string;
    pageNumber: number;
    pageRange: string;
    inputId: string;
    inputRevision: number;
    inputSha256: string;
    referenceImageSha256: string;
    result: OcrDocument;
    validation: { valid: boolean; issues: Array<{ message: string }> };
    fields: Array<{ mappingId: string; targetPath: string; label: string;
      reading: FieldReading; cropUrl: string }>;
  }>;
};
