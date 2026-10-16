import { authenticatedFetch } from "../auth/transport";
import { useRef, useState } from "react";
import { FiDownload } from "react-icons/fi";
import { exportUrl } from "../api";

export function ExcelExportActions({ jobId, documentName, compact = false }: {
  jobId: string;
  compact?: boolean;
  documentName?: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const downloading = useRef(false);
  async function download() {
    if (downloading.current) return;
    downloading.current = true; setBusy(true); setError(null);
    try {
      const response = await authenticatedFetch(exportUrl(jobId, "xlsx"), { credentials: "include" });
      if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        throw new Error(problem.detail ?? problem.message ?? "XLSXを取得できませんでした。もう一度お試しください。");
      }
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const encoded = /filename\*=utf-8''([^;]+)/i.exec(disposition)?.[1];
      let filename = /filename="([^"]+)"/i.exec(disposition)?.[1] ?? "読み取り結果.xlsx";
      if (encoded) {
        try { filename = decodeURIComponent(encoded); } catch { /* Use the fallback filename. */ }
      }
      const savedName = documentName?.trim().replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_")
        .replace(/[. ]+$/g, "");
      if (savedName) filename = `${savedName.replace(/\.xlsx$/i, "")}.xlsx`;
      const url = URL.createObjectURL(await response.blob());
      const revokeObjectURL = URL.revokeObjectURL.bind(URL);
      const link = document.createElement("a");
      link.href = url; link.download = filename;
      document.body.appendChild(link);
      try { link.click(); } finally {
        link.remove(); window.setTimeout(() => revokeObjectURL(url), 1000);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "XLSXを取得できませんでした。");
    } finally { downloading.current = false; setBusy(false); }
  }
  return (
    <div className={`excel-export-actions${compact ? " compact-export" : ""}`}>
      <button className={`primary${compact ? " compact" : ""}`} disabled={busy} onClick={() => void download()}>
        <FiDownload aria-hidden="true" />{busy ? "XLSXを取得中…" : ".xlsxを保存"}
      </button>
      {error && <p className="error-box" role="alert">{error}</p>}
    </div>
  );
}
