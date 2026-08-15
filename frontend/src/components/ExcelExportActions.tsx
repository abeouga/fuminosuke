import { useState } from "react";
import { api, exportUrl } from "../api";
import { copyExcelTable } from "../excelClipboard";

export function ExcelExportActions({ jobId, compact = false }: { jobId: string; compact?: boolean }) {
  const [copying, setCopying] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function copy(): Promise<void> {
    setCopying(true);
    setMessage(null);
    try {
      await copyExcelTable(await api.excelTable(jobId));
      setMessage("コピーしました。Excelで貼り付けできます。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "コピーできませんでした。");
    } finally {
      setCopying(false);
    }
  }

  return (
    <div className={`excel-export-actions${compact ? " compact-export" : ""}`}>
      <button className={`primary${compact ? " compact" : ""}`} disabled={copying} onClick={copy}>
        {copying ? "コピー中…" : "Excelへコピー"}
      </button>
      <a className={`secondary button-link${compact ? " compact" : ""}`} href={exportUrl(jobId, "xlsx")}>
        .xlsxを保存
      </a>
      {message && <span className="excel-export-message" role="status">{message}</span>}
    </div>
  );
}
