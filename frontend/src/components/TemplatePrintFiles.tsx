import { useRef, useState } from "react";
import { api } from "../api";

export function TemplatePrintFiles({ versionId, revision, disabled = false }: {
  versionId: string; revision?: string; disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  const download = async (format: "xlsx" | "pdf") => {
    if (lock.current || disabled) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const current = revision ?? (await api.printLayout(versionId)).revision;
      const blob = await api.downloadTemplatePrint(versionId, current, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `print-${versionId}.${format}`;
      document.body.appendChild(link);
      link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "印刷用ファイルを取得できません。");
    } finally { lock.current = false; setBusy(false); }
  };
  return <div aria-label="保存済みレイアウトの印刷用帳票">
    <button type="button" className="secondary compact" disabled={disabled || busy}
      title="保存済みのページ境界を反映した帳票" onClick={() => void download("xlsx")}>印刷用XLSX</button>
    <button type="button" className="secondary compact" disabled={disabled || busy}
      title="保存済みのページ境界を反映したPDF。変換環境によっては取得できません。"
      onClick={() => void download("pdf")}>印刷用PDF</button>
    {busy && <span role="status">印刷用ファイルを生成中…</span>}
    {error && <p role="alert" className="error-box">{error}</p>}
  </div>;
}
