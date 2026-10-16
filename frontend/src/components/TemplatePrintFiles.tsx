import { useRef, useState } from "react";
import { FaFileExcel, FaFilePdf } from "react-icons/fa";
import { api } from "../api";

export function TemplatePrintFiles({ versionId, revision, sourceImport = false, disabled = false, disabledReason }: {
  versionId: string; revision?: string; sourceImport?: boolean; disabled?: boolean; disabledReason?: string;
}) {
  const [busy, setBusy] = useState<"xlsx" | "pdf" | null>(null);
  const [success, setSuccess] = useState("");
  const [error, setError] = useState("");
  const lock = useRef(false);
  const download = async (format: "xlsx" | "pdf") => {
    if (lock.current || disabled || disabledReason) return;
    lock.current = true; setBusy(format); setError(""); setSuccess("");
    try {
      const blob = sourceImport
        ? await api.downloadImportedTemplatePrint(versionId, format)
        : await api.downloadTemplatePrint(
          versionId, revision ?? (await api.printLayout(versionId)).revision, format,
        );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `print-${versionId}.${format}`;
      document.body.appendChild(link);
      link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setSuccess(`印刷用${format.toUpperCase()}を取得し、ブラウザーへダウンロードを渡しました。`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "印刷用ファイルを取得できません。");
    } finally { lock.current = false; setBusy(null); }
  };
  return <div aria-label={sourceImport ? "未記入の印刷用帳票" : "保存済みレイアウトの印刷用帳票"}>
    <button type="button" className="secondary compact template-print-button" disabled={disabled || Boolean(disabledReason) || busy !== null}
      title={disabledReason ?? (sourceImport ? "元の未記入XLSX" : "保存済みのページ境界を反映した帳票")}
      onClick={() => void download("xlsx")}><FaFileExcel aria-hidden="true" className="template-print-icon-xlsx" />印刷用XLSX</button>
    <button type="button" className="secondary compact template-print-button" disabled={disabled || Boolean(disabledReason) || busy !== null}
      title={disabledReason ?? (sourceImport ? "取込時に保存した未記入の1ページPDF"
        : "保存済みのページ境界を反映したPDF。変換環境によっては取得できません。")}
      onClick={() => void download("pdf")}><FaFilePdf aria-hidden="true" className="template-print-icon-pdf" />印刷用PDF</button>
    {disabledReason && <p className="muted">{disabledReason}</p>}
    {busy && <span role="status">印刷用{busy.toUpperCase()}を生成中…</span>}
    {success && <p role="status">{success}</p>}
    {error && <p role="alert" className="error-box">{error}</p>}
  </div>;
}
