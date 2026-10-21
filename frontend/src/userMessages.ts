/**
 * Text shown to people never carries internal codes, cell IDs or storage paths.
 * The original text is written to the browser console for diagnosis.
 */

const CODE = /\s*\[[A-Z][A-Z0-9_]{2,}\]\s*/g;
const ID = String.raw`(?:xlsx_[A-Z]{1,3}\d+(?:_[\w-]+)?|(?:manual|merged|preview|direct|record|meaning|relation|mark_option|mark_set|template_field|cell_merge|layout|section|group|pool)_[\w-]+|field_[0-9a-f]{6,}|fields\.[\w.-]+|(?:t|c|o)_[0-9a-f]{12,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})`;
const BRACKETED_IDS = new RegExp(String.raw`\s*[（(]\s*${ID}(?:\s*[/／,、]\s*${ID})*\s*[)）]`, "g");
const IDS = new RegExp(String.raw`\b${ID}\b`, "g");
const JAPANESE = /[぀-ヿ一-鿿]/;
// Internal words people cannot act on (the server words its refusals plainly; this catches the rest).
const JARGON = /session|turn|context|revision|digest|cursor|localRef|requestId|eventId|handle|\bmode\b|DRAFT|ACTIVE|\bDB\b|\bID\b|\bAPI\b|schema|mapping|snapshot|payload|Traceback|Error:|世代|物理|操作群|選択肢群|矩形|依存関係|一意|正本|由来|所有者|単一値|識別子|形式が不正|ログのエラーコード/;

export function userText(raw: string | null | undefined, context = "メッセージ", fallback = "処理を完了できませんでした。画面を再読み込みしてから、もう一度試してください。"): string {
  const text = (raw ?? "").trim();
  if (!text) return text;
  let clean = text.replace(CODE, " ").replace(BRACKETED_IDS, "").replace(IDS, "該当の欄").replace(/\s{2,}/g, " ").trim();
  // A purely technical (non-Japanese) message is replaced by a plain one.
  if (!JAPANESE.test(clean) || JARGON.test(clean)) clean = fallback;
  if (clean !== text) console.warn(`[文之助] ${context}の詳細:`, text);
  return clean;
}

/** Logs a technical detail (error code, IDs) without showing it on screen. */
export function logDetail(context: string, ...detail: unknown[]) {
  console.warn(`[文之助] ${context}:`, ...detail);
}

/** How a template cell is named to people: its visible name, never its ID. */
export function cellName(cell: { fieldLabel?: string | null; text?: string | null; printedLabelText?: string | null }): string {
  return cell.fieldLabel?.trim() || cell.text?.trim() || cell.printedLabelText?.trim() || "名称未設定の欄";
}

/** A saved name shown as is, except an internal ID that slipped into it (older automatic names). */
export function displayName(name: string): string {
  return name.replace(IDS, "名称未設定");
}
