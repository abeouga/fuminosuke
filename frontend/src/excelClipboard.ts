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

function rowCellStyle(
  rowKind: ExcelTable["rows"][number]["kind"],
  cellKind: ExcelCellKind,
): string {
  const base = [
    "border:1px solid #D7E0E7",
    "mso-border-alt:solid #D7E0E7 .5pt",
    "padding:5px 8px",
    "height:20px",
    "vertical-align:middle",
    "font-family:'Yu Gothic UI','Meiryo',sans-serif",
    "font-size:10pt",
    "color:#172431",
    "background:#FFFFFF",
  ];
  if (rowKind === "title") {
    base.push(
      "border-color:#1F4E78",
      "mso-border-alt:solid #1F4E78 .5pt",
      "background:#1F4E78",
      "color:#FFFFFF",
      "font-size:16pt",
      "font-weight:700",
      "text-align:left",
    );
  } else if (rowKind === "section") {
    base.push(
      "border-color:#8FAFC6",
      "mso-border-alt:solid #8FAFC6 .5pt",
      "background:#D9EAF7",
      "color:#173F5D",
      "font-size:12pt",
      "font-weight:700",
      "text-align:left",
    );
  } else if (rowKind === "header") {
    base.push(
      "border-color:#FFFFFF",
      "mso-border-alt:solid #FFFFFF .5pt",
      "background:#5B9BD5",
      "color:#FFFFFF",
      "font-weight:700",
      "text-align:center",
    );
  } else if (["integer", "decimal", "currency"].includes(cellKind)) {
    base.push("text-align:right");
  } else if (cellKind === "date") {
    base.push("text-align:center");
  } else {
    base.push("text-align:left");
  }
  return `${base.join(";")};${excelCellStyle(cellKind)}`;
}

export function excelTableToHtml(table: ExcelTable): string {
  const rows = table.rows.map((row) => {
    if (row.kind === "blank") {
      return `<tr><td colspan="${table.maxColumns}" style="border:0;height:8px;padding:0"></td></tr>`;
    }
    const heading = row.kind === "title" || row.kind === "section";
    const tag = row.kind === "header" || heading ? "th" : "td";
    return `<tr>${row.cells.map((cell) => {
      const value = cell.kind === "text" || cell.kind === "identifier"
        ? safeText(cell.display, cell.kind)
        : String(cell.value ?? "");
      const colspan = heading ? ` colspan="${table.maxColumns}"` : "";
      return `<${tag}${colspan} style="${rowCellStyle(row.kind, cell.kind)}">${escapeHtml(value)}</${tag}>`;
    }).join("")}</tr>`;
  }).join("");
  return [
    '<html><head><meta charset="utf-8"></head><body>',
    '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;border-spacing:0">',
    rows,
    "</table></body></html>",
  ].join("");
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
