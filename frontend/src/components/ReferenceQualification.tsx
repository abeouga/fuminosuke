import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ReferenceQualification as Evidence } from "../printLayout";

export function ReferenceQualification({ versionId, revision, disabled, onBusyChange }: {
  versionId: string; revision: string; disabled: boolean; onBusyChange: (busy: boolean) => void;
}) {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    setEvidence(null); setError("");
    void api.referenceQualification(versionId).then(result => {
      if (generation.current === current) setEvidence(result);
    }).catch(() => {
      if (generation.current === current) setError("検証状態を取得できません。");
    });
    return () => { ++generation.current; };
  }, [versionId, revision]);
  const run = async () => {
    if (lock.current || disabled) return;
    lock.current = true; setRunning(true); onBusyChange(true); setError("");
    const current = generation.current;
    try {
      const result = await api.qualifyReference(versionId, revision);
      if (generation.current === current) setEvidence(result);
    } catch (e) {
      if (generation.current === current) setError(e instanceof Error ? e.message : "検証に失敗しました。");
    } finally { lock.current = false; setRunning(false); onBusyChange(false); }
  };
  return <div aria-label="基準画像の精度検証">
    <button type="button" disabled={disabled || running} onClick={() => void run()}>実印刷画像との座標を検証</button>
    <span role="status"> {running ? "RUNNING" : evidence?.status ?? "UNVERIFIED"}</span>
    <p>診断用の検証です。合格した座標許容値と実行隔離条件が未確立のため、本番OCRは許可されません。</p>
    {error && <p role="alert">{error}</p>}
    {evidence?.renderer && <p>{evidence.renderer.version}</p>}
    {evidence?.pages?.map(page => <p key={page.page}>
      Page {page.page}: {page.status} / 最大誤差 {page.maxErrorPx ?? "測定不可"} px / 平均 {page.meanErrorPx?.toFixed(2) ?? "測定不可"} px
      {page.codes?.length ? ` / ${page.codes.join(", ")}` : page.code ? ` / ${page.code}` : ""}
    </p>)}
    {evidence?.codes?.length ? <p>{evidence.codes.join(", ")}</p> : null}
    {evidence?.detail && <p>{evidence.detail}</p>}
  </div>;
}
