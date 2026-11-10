// The messages between the embed page and the page that embeds it (Agorack's evidence panel).
// Both ways the other side's origin must be on the allowed list; nothing is posted to "*".

declare const __EMBED_ORIGINS__: string[];

/** Origins allowed to embed this page (the same list as the frame-ancestors header). */
export const EMBED_ORIGINS: readonly string[] = typeof __EMBED_ORIGINS__ === "undefined" ? [] : __EMBED_ORIGINS__;

/** At most this many cells in one `fieldIds`, each id 1 to `MAX_ID_LENGTH` characters. */
export const MAX_FIELD_IDS = 500;
const MAX_ID_LENGTH = 200;

export type EmbedErrorCode = "DOCUMENT_NOT_FOUND" | "FIELD_NOT_FOUND" | "LOAD_FAILED";

/**
 * `fieldId` is the cell to show most; `fieldIds`, when given, are all of the answer's cells on this
 * document, drawn together the way the chat's document panel draws them.
 */
export type ParentMessage = { type: "agorack:highlight"; fieldId: string | null; fieldIds: string[] | null };
export type EmbedMessage = { type: "embed:ready" } | { type: "embed:error"; code: EmbedErrorCode } | { type: "embed:close" };

export function allowedOrigin(origin: string | null | undefined): origin is string {
  return Boolean(origin) && EMBED_ORIGINS.includes(origin!);
}

/** The embedding page's origin when it is allowed, else null (then nothing is posted). */
export function initialParentOrigin(): string | null {
  if (window.parent === window) return null;
  const ancestor = window.location.ancestorOrigins?.[0] ?? null;
  let referrer: string | null = null;
  try { referrer = document.referrer ? new URL(document.referrer).origin : null; } catch { referrer = null; }
  const origin = ancestor ?? referrer;
  return allowedOrigin(origin) ? origin : null;
}

function validId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= MAX_ID_LENGTH;
}

/** A message from the parent, or null when it is not one this page understands. */
export function parentMessage(data: unknown): ParentMessage | null {
  if (!data || typeof data !== "object") return null;
  const value = data as Record<string, unknown>;
  if (value.type !== "agorack:highlight") return null;
  const fieldId = value.fieldId ?? null;
  if (fieldId !== null && !validId(fieldId)) return null;
  const fieldIds = value.fieldIds ?? null;
  if (fieldIds === null) return { type: "agorack:highlight", fieldId, fieldIds: null };
  if (!Array.isArray(fieldIds) || fieldIds.length > MAX_FIELD_IDS || !fieldIds.every(validId)) return null;
  return { type: "agorack:highlight", fieldId, fieldIds: [...fieldIds] };
}

export function postToParent(origin: string | null, message: EmbedMessage): void {
  if (!origin || window.parent === window) return;
  window.parent.postMessage(message, origin);
}
