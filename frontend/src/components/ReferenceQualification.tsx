import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import {
  referenceQualificationState,
  type ReferenceQualification as Evidence,
} from "../printLayout";

export function ReferenceQualification({ versionId, revision, disabled, fallbackExpiresAt,
  preparationReady, hasStoredEvidence, onBusyChange, onLoadingChange, onEvidenceChange }: {
  versionId: string; revision: string; disabled: boolean; preparationReady: boolean;
  hasStoredEvidence: boolean;
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
  const run = async (candidate = false) => {
    if (lock.current || disabled || !preparationReady) return;
    lock.current = true; setRunning(true); onBusyChange(true); setError("");
    const current = generation.current;
    try {
      const result = await (candidate
        ? api.prepareCandidateReadiness(versionId, revision)
        : api.qualifyReference(versionId, revision));
      if (generation.current === current) { setEvidence(result); onEvidenceChange?.(result); }
    } catch (e) {
      if (generation.current === current) setError(e instanceof Error ? e.message : "検証に失敗しました。");
    } finally { lock.current = false; setRunning(false); onBusyChange(false); }
  };
  const displayStatus = loading
    ? "LOADING"
    : referenceQualificationState(evidence, fallbackExpiresAt);
  const isRecheck = hasStoredEvidence
    || displayStatus === "EXPIRED"
    || displayStatus === "FAIL"
    || Boolean(evidence?.codes?.length);
  const blockers = Array.from(new Set([
    ...(evidence?.codes ?? []),
    ...(evidence?.productionBlockers ?? []),
    ...(evidence?.unresolvedBlockers ?? []),
  ]));
  return <div aria-label="基準画像の精度検証">
    {displayStatus !== "PASS" && displayStatus !== "CANDIDATE_READY" && <button type="button"
      disabled={disabled || !preparationReady || running || loading}
      aria-describedby={!preparationReady ? "reference-preparation-required" : undefined}
      onClick={() => void run()}>{running ? "資格確認中…" : isRecheck ? "資格を再確認" : "資格を確認"}</button>}
    {hasStoredEvidence && preparationReady && displayStatus !== "PASS" && displayStatus !== "CANDIDATE_READY"
      && <button type="button" disabled={disabled || running || loading}
        onClick={() => void run(true)}>候補OCRの準備を確認</button>}
    <span role="status"> {running ? "RUNNING" : displayStatus === "CANDIDATE_READY" ? "候補OCR利用可" : displayStatus}</span>
    {displayStatus === "CANDIDATE_READY" && <p>
      再描画・欄の対応・実行環境を確認しました。この版は人間レビューを前提に有効化できます。
      読み取り後は全ページの画像と文字を確認してください。
      {evidence?.validUntilChange && " 通常利用では重い準備検証を繰り返さず、版・原本・証拠が変わった場合だけ再準備します。"}
      {evidence?.environmentDifference?.changed && " 環境更新の差分は検証記録に保存されています。"}
    </p>}
    {!preparationReady && <p id="reference-preparation-required">
      正式資格の確認前に「印刷用ファイルを確定」を実行してください。
    </p>}
    {displayStatus === "PASS" && <p>この版の正式資格は有効です。</p>}
    {displayStatus === "UNVERIFIED" && <p>現在の版は資格確認が必要です。</p>}
    {displayStatus === "EXPIRED" && <p>資格期限切れです。正式資格を再確認してください。</p>}
    {displayStatus === "FAIL" && <p>正式資格が成立していません。未解決条件を確認してください。</p>}
    {typeof evidence?.expiresAt === "number" && <p>{evidence.qualificationPolicy?.startsWith("paged-candidate-readiness-")
      ? "候補OCR準備の期限" : "資格期限"}: {new Date(evidence.expiresAt * 1000).toLocaleString("ja-JP")}</p>}
    {stages ? <>
      <p>有効化の検証: {stages.activation.status === "QUALIFIED"
        ? "正式資格あり"
        : stages.activation.status === "BLOCKED" ? "未完了" : "資格判定待ち"}。</p>
      <p>入力画像の検証: 未実施。候補OCRでも位置合わせ・切り出し領域の対応は送信前に検査します。筆画の欠落や隣欄の混入は完全には自動保証せず、全ページの人間による確認が必要です。事前の記入画像は有効化の前提ではありません。</p>
      {stages.activation.codes.length > 0 && <p aria-label="有効化の未解決条件">
        {stages.activation.codes.join(", ")}
      </p>}
    </> : displayStatus !== "CANDIDATE_READY" && <p>有効化には、現在の版の基準画像・欄の対応・実行環境の確認が必要です。</p>}
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
