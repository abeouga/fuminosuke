import type { FieldReading, OcrDocument, OcrFieldPreview, ReviewStructure } from "./types";

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
  reviewStructure?: ReviewStructure | null;
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
    pageImageUrl: string;
    result: OcrDocument;
    validation: { valid: boolean; issues: Array<{ message: string }> };
    fields: Array<{ mappingId: string; targetPath: string; label: string;
      reading: FieldReading; cropUrl: string } & Pick<OcrFieldPreview, "mappingType" | "markRecognitionMode" | "markSelectionMode" | "markOptions">>;
  }>;
};
