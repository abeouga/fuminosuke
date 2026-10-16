import type { LayoutCell } from "../types";
import type { AssistantImage } from "./contracts";

export type LayoutAssistantSelection = {
  selectedIds: string[];
  cell: LayoutCell | null;
  choice: { markSetId: string; choiceId: string; label: string; value: string; tracksLabel: boolean } | null;
  selectionEpoch: number;
  localEditEpoch: number;
  snapshotKey: string;
  pageId: string | null;
  categoryChangeBlocked: boolean;
  image: AssistantImage | null;
  draftReviewRequired: boolean;
  saveError: string | null;
};
