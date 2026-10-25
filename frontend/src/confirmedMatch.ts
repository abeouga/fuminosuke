import { api } from "./api";

// Finds the confirmed document an image is a later photo of (more written on since), so it can be
// read as that document's next version without the user picking it. The server compares the image
// with each confirmed document's handwriting; nothing is stored and no external API is called.

export type ConfirmedMatch = { jobId: string; label: string; seriesVersion: number; similarity: number };

/** How many of the template's latest confirmed documents are compared (the server's limit). */
const MAX_COMPARED = 24;

export async function findConfirmedMatch(file: File, templateVersionId: string): Promise<ConfirmedMatch | null> {
  if (!file.type.startsWith("image/")) return null;
  const { items } = await api.seriesCandidates(templateVersionId);
  const selectable = items.filter((item) => item.selectable).slice(0, MAX_COMPARED);
  if (selectable.length === 0) return null;
  const result = await api.compareContinuations(file, selectable.map((item) => item.jobId));
  const best = result.items
    .filter((item) => item.continuation)
    .sort((a, b) => (b.similarity ?? 0) - (a.similarity ?? 0))[0];
  const found = best && selectable.find((item) => item.jobId === best.jobId);
  if (!found) return null;
  return {
    jobId: found.jobId,
    label: found.documentName ?? "（名称なし）",
    seriesVersion: found.seriesVersion,
    similarity: best.similarity ?? 0,
  };
}
