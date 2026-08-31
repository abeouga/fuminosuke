import { useEffect, useState } from "react";
import { api, exportUrl } from "../api";
import { copyExcelTable } from "../excelClipboard";

export function ExcelExportActions({
  jobId,
  compact = false,
  driveFileName,
}: {
  jobId: string;
  compact?: boolean;
  driveFileName?: string | null;
}) {
  const [copying, setCopying] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [fileName, setFileName] = useState(driveFileName ?? "");
  const [lastSavedName, setLastSavedName] = useState(driveFileName ?? "");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (driveFileName === undefined) return;
    const next = driveFileName ?? "";
    setFileName(next);
    setLastSavedName(next);
  }, [driveFileName, jobId]);

  useEffect(() => {
    if (driveFileName === undefined || fileName === lastSavedName) return;
    const trimmed = fileName.trim();
    if (!trimmed) {
      setMessage("出力ファイル名を入力してください。");
      return;
    }
    const timeout = window.setTimeout(async () => {
      setRenaming(true);
      setMessage(null);
      try {
        const renamed = await api.renameSpreadsheet(jobId, trimmed);
        const savedName = renamed.fileName ?? trimmed.replace(/\.xlsx$/i, "").trim();
        setFileName(savedName);
        setLastSavedName(savedName);
        setMessage("Drive上のファイル名へ反映しました。");
      } catch (reason) {
        setMessage(reason instanceof Error ? reason.message : "ファイル名を変更できませんでした。");
      } finally {
        setRenaming(false);
      }
    }, 500);
    return () => window.clearTimeout(timeout);
  }, [driveFileName, fileName, jobId, lastSavedName]);

  async function copy(): Promise<void> {
    setCopying(true);
    setMessage(null);
    try {
      await copyExcelTable(await api.excelTable(jobId));
      setMessage("罫線付きでコピーしました。Excelで貼り付けできます。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "コピーできませんでした。");
    } finally {
      setCopying(false);
    }
  }

  return (
    <div className={`excel-export-actions${compact ? " compact-export" : ""}`}>
      {driveFileName !== undefined && (
        <label className="output-file-name-field">
          <span>書類名</span>
          <span className="output-file-name-input">
            <input
              type="text"
              value={fileName}
              maxLength={185}
              aria-label="書類名"
              onChange={(event) => setFileName(event.target.value)}
            />
            <span>.xlsx</span>
          </span>
          <small>{renaming ? "Driveへ反映中…" : "ライブラリ、Drive、保存するXLSX名に反映します。"}</small>
        </label>
      )}
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
