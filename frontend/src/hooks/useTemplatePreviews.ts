import { useEffect, useRef, useState } from "react";
import { api } from "../api";

const PREVIEW_PRIORITY = ["cleanPreview", "cleanTemplate", "normalized", "alignedSource.1"] as const;
type Preview = { loading: boolean; url?: string };
type PreviewVersion = { id: string; sourceJobId: string | null };

export function templatePreviewKey(version: PreviewVersion): string {
  return JSON.stringify([version.id, version.sourceJobId]);
}

export function useTemplatePreviews(version: PreviewVersion | undefined, enabled: boolean) {
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const cache = useRef(new Map<string, Preview>());
  const pending = useRef<Promise<void>>(Promise.resolve());
  const mounted = useRef(false);
  const versionId = version?.id;
  const sourceJobId = version?.sourceJobId;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!enabled || !versionId || !sourceJobId) return;
    const key = templatePreviewKey({ id: versionId, sourceJobId });
    if (cache.current.has(key)) return;
    let cancelled = false;
    // Only the latest selection starts after the outstanding request settles.
    // This keeps concurrency at one without changing the artifact API.
    pending.current = pending.current.then(async () => {
      if (cancelled || cache.current.has(key)) return;
      if (mounted.current) setPreviews((current) => ({ ...current, [key]: { loading: true } }));
      let preview: Preview = { loading: false };
      try {
        const response = await api.artifacts(sourceJobId);
        const url = PREVIEW_PRIORITY
          .map((name) => response.items.find((artifact) => artifact.name === name)?.url)
          .find(Boolean);
        preview = { loading: false, url };
      } catch {
        // A missing or failed preview is a placeholder, not an OCR admission failure.
      }
      cache.current.set(key, preview);
      if (mounted.current) setPreviews((current) => ({ ...current, [key]: preview }));
    });
    return () => { cancelled = true; };
  }, [enabled, sourceJobId, versionId]);
  return (candidate: PreviewVersion) => previews[templatePreviewKey(candidate)];
}
