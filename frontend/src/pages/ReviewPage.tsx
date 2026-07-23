import { useEffect, useMemo, useState } from "react";
import { ApiError, api, exportUrl } from "../api";
import type { Artifact, FieldReading, OcrDocument, OcrResult } from "../types";

type ReadingEntry = { path: string; label: string; value: FieldReading };

export function ReviewPage({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const [result, setResult] = useState<OcrResult | null>(null);
  const [draft, setDraft] = useState<OcrDocument | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [filter, setFilter] = useState<"all" | "review">("review");
  const [region, setRegion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    const [next, artifactList] = await Promise.all([api.result(jobId), api.artifacts(jobId)]);
    setResult(next); setDraft(next.result); setArtifacts(artifactList.items);
  };
  useEffect(() => { void load().catch((reason) => setError(reason instanceof Error ? reason.message : "結果を取得できませんでした。")); }, [jobId]);
  const entries = useMemo(() => draft ? collectReadings(draft) : [], [draft]);
  const visible = entries.filter((entry) => filter === "all" || entry.value.status === "uncertain" || Boolean(entry.value.reviewReason) || draft?.reviewItems.some((item) => item.path === entry.path));
  const byName = useMemo(() => new Map(artifacts.map((item) => [item.name, item.url])), [artifacts]);
  const suggestedRegion = visible[0]?.value.sourceRegionId ?? null;
  const regionImage = region ? byName.get(`fieldCrop.${region}.alignedColor`) : undefined;
  if (error) return <div className="empty-state large"><h2>結果を開けません</h2><p>{error}</p><button className="secondary" onClick={onClose}>戻る</button></div>;
  if (!result || !draft) return <div className="empty-state large">結果を読み込んでいます…</div>;
  const unresolved = entries.filter((entry) => entry.value.status === "uncertain").length + draft.reviewItems.filter((item) => item.severity === "error").length;
  return (
    <div className="page-stack review-page">
      <header className="page-header split"><div><span className="eyebrow">OCR REVIEW · REVISION {result.revision}</span><h1>読み取り結果を確認</h1><p>画像と項目を見比べ、原文と整形値を必要に応じて修正してください。</p></div><button className="ghost" onClick={onClose}>← ホームへ</button></header>
      {result.provider === "mock" && <aside className="notice compact"><strong>試運転データ</strong><span>現在の候補は固定fixtureから作られています。</span></aside>}
      {result.provider === "openai" && <aside className="notice compact"><strong>{result.modelId ?? "OpenAI OCR"}</strong><span>入力 {result.inputTokens?.toLocaleString() ?? "-"} / 出力 {result.outputTokens?.toLocaleString() ?? "-"} tokens · API推定 {result.estimatedCost == null ? "計算不可" : `$${result.estimatedCost.toFixed(6)}`} · prompt {result.promptVersion ?? "-"}</span></aside>}
      {result.confirmed && result.individualId && <aside className="notice external compact"><strong>個体JSONへ保存済み</strong><span>個体識別番号 {result.individualId} で検索できます。</span></aside>}
      <div className="ocr-review-layout">
        <aside className="source-pane">
          <div className="pane-title"><span>対応する画像</span><small>{region ?? "帳票全体"}</small></div>
          <div className="source-image">{regionImage ? <img src={regionImage} alt={`${region} の切り出し`} /> : <img src={byName.get("alignedSource")} alt="位置合わせ済み帳票" />}</div>
          <div className="source-switch"><button className={!region ? "active" : ""} onClick={() => setRegion(null)}>帳票全体</button><button className={region ? "active" : ""} disabled={!suggestedRegion} onClick={() => suggestedRegion && setRegion(suggestedRegion)}>選択領域</button><button onClick={() => { const image = byName.get("handwritingOnly"); if (image) window.open(image, "_blank", "noopener,noreferrer"); }}>手書きのみ ↗</button></div>
          <div className="review-summary"><strong>{unresolved}</strong><span>要確認項目</span><p>「不確実」は空欄とは異なります。画像を見て判断してください。</p></div>
        </aside>
        <main className="field-pane">
          <div className="field-toolbar"><div className="segmented"><button className={filter === "review" ? "active" : ""} onClick={() => setFilter("review")}>要確認のみ <b>{unresolved}</b></button><button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>すべて {entries.length}</button></div><span>{result.confirmed ? "確定済み" : "編集中"}</span></div>
          {visible.length === 0 ? <div className="empty-state">要確認項目はありません。「すべて」で全項目を表示できます。</div> : <div className="reading-list">{visible.map((entry) => (
            <article className={`reading-card reading-${entry.value.status}`} key={entry.path} onFocus={() => setRegion(entry.value.sourceRegionId)}>
              <div className="reading-head"><div><span className="reading-label">{entry.label}</span><small>{entry.path}</small></div><span className={`reading-badge badge-${entry.value.status}`}>{statusLabel(entry.value.status)}</span></div>
              <div className="reading-inputs"><label>原文<input disabled={result.confirmed} value={entry.value.raw ?? ""} placeholder={entry.value.status === "blank" ? "空欄" : "画像どおり入力"} onChange={(event) => setDraft(updateReading(draft, entry.path, { raw: event.target.value || null }))} /></label><label>整形値<input disabled={result.confirmed || entry.value.status !== "readable"} value={entry.value.normalized ?? ""} placeholder="確定用の値" onChange={(event) => setDraft(updateReading(draft, entry.path, { normalized: event.target.value || null }))} /></label></div>
              <div className="reading-actions"><label>判定<select disabled={result.confirmed} value={entry.value.status} onChange={(event) => { const status = event.target.value as FieldReading["status"]; setDraft(updateReading(draft, entry.path, status === "blank" ? { status, raw: null, normalized: null } : status === "uncertain" ? { status, normalized: null, raw: entry.value.raw ?? "" } : { status, raw: entry.value.raw ?? "" })); }}><option value="readable">読めた</option><option value="uncertain">不確実</option><option value="blank">空欄</option></select></label>{entry.value.alternatives.length > 0 && <div className="alternatives"><span>候補:</span>{entry.value.alternatives.map((value) => <button disabled={result.confirmed} key={value} onClick={() => setDraft(updateReading(draft, entry.path, { raw: value }))}>{value}</button>)}</div>}</div>
              {entry.value.reviewReason && <p className="review-reason">確認理由: {entry.value.reviewReason}</p>}
            </article>
          ))}</div>}
        </main>
      </div>
      {error && <p className="error-box">{error}</p>}{message && <p className="success-box">{message}</p>}
      <div className="decision-bar sticky"><div><strong>{result.confirmed ? "確定済みの結果です" : `${unresolved}件の要確認項目があります`}</strong><span>{result.confirmed ? "JSONまたはCSVを保存できます。" : "修正を保存してから確定してください。"}</span></div>{result.confirmed ? <><a className="secondary button-link" href={exportUrl(jobId, "json")}>JSON出力</a><a className="primary button-link" href={exportUrl(jobId, "csv")}>CSV出力</a></> : <><button className="secondary" disabled={busy} onClick={async () => { setBusy(true); setError(null); try { const saved = await api.correct(jobId, result.revision, draft); setResult(saved); setDraft(saved.result); setMessage("修正を保存しました。"); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "保存できませんでした。"); } finally { setBusy(false); } }}>修正を保存</button><button className="primary" disabled={busy || unresolved > 0} onClick={async () => { if (!window.confirm("この内容を確定します。確定後は修正できません。よろしいですか？")) return; setBusy(true); setError(null); try { let latest = result; if (JSON.stringify(draft) !== JSON.stringify(result.result)) { latest = await api.correct(jobId, result.revision, draft); setResult(latest); setDraft(latest.result); } const confirmed = await api.confirm(jobId); setResult(confirmed); setDraft(confirmed.result); setMessage("結果を確定しました。"); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "確定できませんでした。"); } finally { setBusy(false); } }}>内容を確定</button></>}</div>
    </div>
  );
}

function collectReadings(document: OcrDocument): ReadingEntry[] {
  const roots: Array<[string, unknown]> = [["document", document.document], ["saleCalves", document.saleCalves], ["pedigree", document.pedigree], ["calvings", document.calvings]];
  const entries: ReadingEntry[] = [];
  const visit = (value: unknown, path: string) => {
    if (!value || typeof value !== "object") return;
    if ("status" in value && "sourceRegionId" in value) {
      entries.push({ path, label: fieldLabel(path), value: value as FieldReading }); return;
    }
    Object.entries(value).forEach(([key, child]) => visit(child, `${path}.${key}`));
  };
  roots.forEach(([path, value]) => visit(value, path));
  return entries;
}

function updateReading(document: OcrDocument, path: string, patch: Partial<FieldReading>): OcrDocument {
  const next = structuredClone(document);
  const keys = path.split(".");
  let target: unknown = next;
  for (const key of keys) target = Array.isArray(target) ? target[Number(key)] : (target as Record<string, unknown>)[key];
  Object.assign(target as FieldReading, patch);
  next.reviewItems = next.reviewItems.filter((item) => item.path !== path);
  return next;
}

function statusLabel(status: FieldReading["status"]) {
  return status === "readable" ? "✓ 読めた" : status === "uncertain" ? "! 不確実" : "○ 空欄";
}

function fieldLabel(path: string) {
  const key = path.split(".").at(-1) ?? path;
  return ({ unifiedEarTag: "統一耳標番号", cattleName: "牛名", birthDate: "生年月日", registrationNumber: "登録番号", coatColor: "毛色", breeder: "繁殖者", owner: "所有者", father: "父", mother: "母", paternalGrandfather: "父方祖父", paternalGrandmother: "父方祖母", maternalGrandfather: "母方祖父", maternalGrandmother: "母方祖母", parity: "産次", calvingDate: "分娩日", sex: "性別", outcome: "結果", notes: "備考", ageDays: "日齢", weightKg: "体重", priceYen: "価格", sireName: "父名", saleDate: "売却日" } as Record<string, string>)[key] ?? key;
}
