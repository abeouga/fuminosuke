import type { ExcelCellKind, ExcelTable } from "./types";

const formulaPrefix = /^[=+\-@]/;

function safeText(value: string, kind: ExcelCellKind): string {
  const flattened = value.replace(/\r?\n/g, " ").replace(/\t/g, " ");
  return kind === "text" && formulaPrefix.test(flattened.trimStart())
    ? `'${flattened}`
    : flattened;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function excelTableToTsv(table: ExcelTable): string {
  return table.rows
    .map((row) => row.cells.map((cell) => safeText(cell.display, cell.kind)).join("\t"))
    .join("\r\n");
}

function excelCellStyle(kind: ExcelCellKind): string {
  if (kind === "text" || kind === "identifier") return "mso-number-format:'\\@';";
  if (kind === "date") return "mso-number-format:'yyyy年m月d日';";
  if (kind === "currency") return "mso-number-format:'#,##0円';";
  if (kind === "decimal") return "mso-number-format:'#,##0.###kg';";
  if (kind === "integer") return "mso-number-format:'#,##0';";
  return "";
}

export function excelTableToHtml(table: ExcelTable): string {
  const rows = table.rows.map((row) => {
    if (row.kind === "blank") return "<tr><td>&nbsp;</td></tr>";
    const heading = row.kind === "title" || row.kind === "section";
    const tag = row.kind === "header" || heading ? "th" : "td";
    return `<tr>${row.cells.map((cell) => {
      const value = cell.kind === "text" || cell.kind === "identifier"
        ? safeText(cell.display, cell.kind)
        : String(cell.value ?? "");
      const colspan = heading ? ` colspan="${table.maxColumns}"` : "";
      return `<${tag}${colspan} style="${excelCellStyle(cell.kind)}">${escapeHtml(value)}</${tag}>`;
    }).join("")}</tr>`;
  }).join("");
  return `<table>${rows}</table>`;
}

export async function copyExcelTable(table: ExcelTable): Promise<void> {
  const clipboard = navigator.clipboard;
  if (!clipboard) throw new Error("このブラウザではクリップボードを利用できません。");
  const plain = excelTableToTsv(table);
  const html = excelTableToHtml(table);
  if (typeof clipboard.write === "function" && typeof ClipboardItem !== "undefined") {
    try {
      await clipboard.write([new ClipboardItem({
        "text/plain": new Blob([plain], { type: "text/plain" }),
        "text/html": new Blob([html], { type: "text/html" }),
      })]);
      return;
    } catch {
      // Some browsers allow plain-text writes but reject rich clipboard payloads.
    }
  }
  if (typeof clipboard.writeText !== "function") {
    throw new Error("このブラウザではクリップボードを利用できません。");
  }
  await clipboard.writeText(plain);
}
