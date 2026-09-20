import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import {
  referenceQualificationState,
  type ReferenceQualification as Evidence,
} from "../printLayout";

export function ReferenceQualification({ versionId, revision, disabled, fallbackExpiresAt,
  onBusyChange, onLoadingChange, onEvidenceChange }: {
  versionId: string; revision: string; disabled: boolean;
  fallbackExpiresAt?: number | null;
  onBusyChange: (busy: boolean) => void;
  onLoadingChange?: (loading: boolean) => void;
  onEvidenceChange?: (evidence: Evidence | null) => void;
}) {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  const generation = useRef(0);
  const stages = evidence?.safetyStages?.policy === "paged-two-stage-safety-v1"
    ? evidence.safetyStages : null;
  useEffect(() => {
    const current = ++generation.current;
    setEvidence(null); onEvidenceChange?.(null); setLoading(true); onLoadingChange?.(true); setError("");
    void api.referenceQualification(versionId).then(result => {
      if (generation.current === current) { setEvidence(result); onEvidenceChange?.(result); }
    }).catch(() => {
      if (generation.current === current) setError("検証状態を取得できません。");
    }).finally(() => {
      if (generation.current === current) { setLoading(false); onLoadingChange?.(false); }
    });
    return () => { ++generation.current; };
  }, [versionId, revision, onEvidenceChange, onLoadingChange]);
  const run = async () => {
    if (lock.current || disabled) return;
    lock.current = true; setRunning(true); onBusyChange(true); setError("");
    const current = generation.current;
    try {
      const result = await api.qualifyReference(versionId, revision);
      if (generation.current === current) { setEvidence(result); onEvidenceChange?.(result); }
    } catch (e) {
      if (generation.current === current) setError(e instanceof Error ? e.message : "検証に失敗しました。");
    } finally { lock.current = false; setRunning(false); onBusyChange(false); }
  };
  const displayStatus = loading
    ? "LOADING"
    : referenceQualificationState(evidence, fallbackExpiresAt);
  const blockers = Array.from(new Set([
    ...(evidence?.codes ?? []),
    ...(evidence?.productionBlockers ?? []),
    ...(evidence?.unresolvedBlockers ?? []),
  ]));
  return <div aria-label="基準画像の精度検証">
    {displayStatus !== "PASS" && <button type="button" disabled={disabled || running || loading}
      onClick={() => void run()}>{running ? "資格確認中…" : "資格を再確認"}</button>}
    <span role="status"> {running ? "RUNNING" : displayStatus}</span>
    {displayStatus === "PASS" && <p>この版の正式資格は有効です。</p>}
    {displayStatus === "UNVERIFIED" && <p>現在の版は資格確認が必要です。</p>}
    {displayStatus === "EXPIRED" && <p>資格期限切れです。正式資格を再確認してください。</p>}
    {displayStatus === "FAIL" && <p>正式資格が成立していません。未解決条件を確認してください。</p>}
    {typeof evidence?.expiresAt === "number" && <p>資格期限: {new Date(evidence.expiresAt * 1000).toLocaleString("ja-JP")}</p>}
    {stages ? <>
      <p>有効化の検証: {stages.activation.status === "QUALIFIED"
        ? "正式資格あり"
        : stages.activation.status === "BLOCKED" ? "未完了" : "資格判定待ち"}。</p>
      <p>入力画像の検証: 未実施。位置合わせ・切り出し領域・筆画の安全性は、OCR送信前の検査対象です。事前の記入画像は有効化の前提ではありません。</p>
      {stages.activation.codes.length > 0 && <p aria-label="有効化の未解決条件">
        {stages.activation.codes.join(", ")}
      </p>}
    </> : <p>診断用の検証です。合格した座標許容値と実行隔離条件が未確立のため、本番OCRは許可されません。</p>}
    {error && <p role="alert">{error}</p>}
    {evidence?.renderer && <p>{evidence.renderer.version}</p>}
    {evidence?.pages?.map(page => <p key={page.page}>
      Page {page.page}: {page.status} / 最大誤差 {page.maxErrorPx ?? "測定不可"} px / 平均 {page.meanErrorPx?.toFixed(2) ?? "測定不可"} px
      {page.codes?.length ? ` / ${page.codes.join(", ")}` : page.code ? ` / ${page.code}` : ""}
    </p>)}
    {blockers.length > 0 ? <p aria-label="正式資格の未解決条件">{blockers.join(", ")}</p> : null}
    {evidence?.detail && <p>{evidence.detail}</p>}
  </div>;
}
