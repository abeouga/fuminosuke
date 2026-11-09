// The messages between the embed page and the page that embeds it (Agorack's evidence panel).
// Both ways the other side's origin must be on the allowed list; nothing is posted to "*".

declare const __EMBED_ORIGINS__: string[];

/** Origins allowed to embed this page (the same list as the frame-ancestors header). */
export const EMBED_ORIGINS: readonly string[] = typeof __EMBED_ORIGINS__ === "undefined" ? [] : __EMBED_ORIGINS__;

export type EmbedErrorCode = "DOCUMENT_NOT_FOUND" | "FIELD_NOT_FOUND" | "LOAD_FAILED";

export type ParentMessage = { type: "agorack:highlight"; fieldId: string | null };
export type EmbedMessage = { type: "embed:ready" } | { type: "embed:error"; code: EmbedErrorCode };

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

/** A message from the parent, or null when it is not one this page understands. */
export function parentMessage(data: unknown): ParentMessage | null {
  if (!data || typeof data !== "object") return null;
  const value = data as Record<string, unknown>;
  if (value.type !== "agorack:highlight") return null;
  const fieldId = value.fieldId;
  if (fieldId === null || fieldId === undefined) return { type: "agorack:highlight", fieldId: null };
  if (typeof fieldId !== "string" || fieldId.length === 0 || fieldId.length > 200) return null;
  return { type: "agorack:highlight", fieldId };
}

export function postToParent(origin: string | null, message: EmbedMessage): void {
  if (!origin || window.parent === window) return;
  window.parent.postMessage(message, origin);
}
