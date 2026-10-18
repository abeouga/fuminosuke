import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";

const PREVIEW_PRIORITY = ["cleanPreview", "cleanTemplate", "normalized", "alignedSource.1"] as const;
const PREVIEW_CONCURRENCY = 3;
type Preview = { loading: boolean; url?: string };
type PreviewVersion = { id: string; sourceJobId: string | null };

export function templatePreviewKey(version: PreviewVersion): string {
  return JSON.stringify([version.id, version.sourceJobId]);
}

// Loads the artifact URL of each version's source job. The list is processed in order
// (put the selected version first) with a small concurrency cap so a long template
// list does not flood the API.
export function useTemplatePreviews(versions: readonly PreviewVersion[], enabled: boolean) {
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const cache = useRef(new Map<string, Preview>());
  const inflight = useRef(new Set<string>());
  const mounted = useRef(false);
  const sources = useMemo(
    () => versions.filter((version) => version.sourceJobId),
    // The key string carries the identity of every entry, so a new array with the same content is a no-op.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [versions.map(templatePreviewKey).join("|")],
  );
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const queue = sources.filter((version) => {
      const key = templatePreviewKey(version);
      return !cache.current.has(key) && !inflight.current.has(key);
    });
    const load = async (version: PreviewVersion) => {
      const key = templatePreviewKey(version);
      inflight.current.add(key);
      if (mounted.current) setPreviews((current) => ({ ...current, [key]: { loading: true } }));
      let preview: Preview = { loading: false };
      try {
        const response = await api.artifacts(version.sourceJobId!);
        const url = PREVIEW_PRIORITY
          .map((name) => response.items.find((artifact) => artifact.name === name)?.url)
          .find(Boolean);
        preview = { loading: false, url };
      } catch {
        // A missing or failed preview is a placeholder, not an OCR admission failure.
      }
      inflight.current.delete(key);
      cache.current.set(key, preview);
      if (mounted.current) setPreviews((current) => ({ ...current, [key]: preview }));
    };
    const worker = async () => {
      for (let next = queue.shift(); next && !cancelled; next = queue.shift()) await load(next);
      // A cancelled run leaves unstarted entries untouched; the next run picks them up.
    };
    void Promise.all(Array.from({ length: Math.min(PREVIEW_CONCURRENCY, queue.length) }, worker));
    return () => { cancelled = true; };
  }, [enabled, sources]);
  return (candidate: PreviewVersion) => previews[templatePreviewKey(candidate)];
}
