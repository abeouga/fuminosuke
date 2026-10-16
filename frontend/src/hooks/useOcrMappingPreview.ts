import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { FieldMapping } from "../types";

export function useOcrMappingPreview(versionId: string) {
  const [mappingPreview, setMappingPreview] = useState<FieldMapping[] | null>(null);
  const [mappingValid, setMappingValid] = useState(false);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [mappingRetryToken, setMappingRetryToken] = useState(0);
  const [mappingResolvedVersionId, setMappingResolvedVersionId] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setMappingPreview(null);
    setMappingValid(false);
    setMappingError(null);
    setMappingResolvedVersionId(null);
    if (!versionId) return;
    void api.fieldMappings(versionId)
      .then((response) => {
        if (!cancelled) {
          setMappingPreview(response.mappings.filter((mapping) => mapping.enabled));
          setMappingValid(response.validForOcr);
          setMappingResolvedVersionId(versionId);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setMappingError(error instanceof ApiError ? error.message : "意味づけを確認できませんでした。");
          setMappingResolvedVersionId(versionId);
        }
      });
    return () => { cancelled = true; };
  }, [mappingRetryToken, versionId]);
  const reloadMappings = () => {
    setMappingResolvedVersionId(null);
    setMappingRetryToken((value) => value + 1);
  };
  return { mappingPreview, mappingValid, mappingError, mappingResolvedVersionId, reloadMappings };
}
