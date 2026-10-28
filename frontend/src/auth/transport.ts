export type AuthSession = {
  mode: "local" | "cognito"; authenticated: boolean; csrfToken?: string; storageScope?: string;
  /** May read everyone's correction rate (always true without sign-in). */
  admin?: boolean;
};
let current: AuthSession | null = null;
let sessionEpoch = 0;
/**
 * What earlier versions kept for whoever used the tab, not per user (the open conversation, the
 * images sent in the chat, the template order): with sign-in it could show one user's data to the
 * next, so it is removed once the app knows it runs with sign-in.
 */
const SHARED_KEYS: Record<"localStorage" | "sessionStorage", string[]> = {
  sessionStorage: ["fuminosuke-ask-v1", "fuminosuke-chat-readings-v2"],
  localStorage: ["fuminosuke-ask-history-v1", "fuminosuke-chat-readings-v3", "fuminosuke.templateOpenedAt"],
};
function removeSharedKeys() {
  for (const kind of ["localStorage", "sessionStorage"] as const) {
    for (const key of SHARED_KEYS[kind]) {
      try { window[kind].removeItem(key); } catch { /* storage may be off */ }
    }
  }
}
export function endBrowserSession(): AuthSession {
  sessionEpoch++;
  current = { ...current, mode: "cognito", authenticated: false, csrfToken: undefined };
  window.dispatchEvent(new Event("fuminosuke-auth-required"));
  return current;
}
export async function loadSession(): Promise<AuthSession> {
  const epoch = sessionEpoch;
  const response = await fetch("/api/auth/session", { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new Error("ログイン状態を確認できません。再読み込みしてください。");
  const next = await response.json() as AuthSession;
  if (epoch !== sessionEpoch && current) return current;
  if (current?.storageScope && next.authenticated && current.storageScope !== next.storageScope) {
    window.location.reload();
    throw new Error("利用者が変更されました。");
  }
  if (current?.authenticated && !next.authenticated) {
    window.dispatchEvent(new Event("fuminosuke-auth-required"));
  }
  current = next.authenticated ? next : { ...next, storageScope: current?.storageScope };
  if (next.mode === "cognito") removeSharedKeys();
  return next;
}
/** The app's own login form (cloud mode): the server checks the password with Cognito. */
export async function signIn(username: string, password: string): Promise<AuthSession> {
  const response = await fetch("/api/auth/password", {
    method: "POST", credentials: "include", cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(response.status === 401 ? "ユーザー名またはパスワードが違います。"
      : body?.detail || "ログインできませんでした。時間をおいてもう一度お試しください。");
  }
  return loadSession();
}
export function csrfHeaders(): Record<string, string> {
  return current?.mode === "cognito" && current.csrfToken ? { "X-CSRF-Token": current.csrfToken } : {};
}
export async function authenticatedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
  if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/")) {
    throw new Error("許可されていない通信先です。");
  }
  const headers = new Headers(init?.headers);
  for (const [key, value] of Object.entries(csrfHeaders())) headers.set(key, value);
  const response = await fetch(input, { ...init, headers, credentials: "include" });
  if (response.status === 401 && current?.mode === "cognito") {
    window.dispatchEvent(new Event("fuminosuke-auth-required"));
  }
  return response;
}
export function isAdmin(): boolean {
  return current?.authenticated === true && current.admin === true;
}
export function isSignInMode(): boolean {
  return current?.mode === "cognito";
}
/** Which user's storage the private stores read now (changes once the session is known). */
export function storageScope(): string {
  return current?.mode === "cognito" ? current.storageScope ?? "signed-out" : "";
}
function scoped(kind: "localStorage" | "sessionStorage"): Pick<Storage, "getItem" | "setItem" | "removeItem"> {
  const key = (value: string) => current?.mode === "cognito"
    ? `fuminosuke:${current.storageScope ?? "signed-out"}:${value}` : value;
  return {
    getItem: value => window[kind].getItem(key(value)),
    setItem: (value, data) => window[kind].setItem(key(value), data),
    removeItem: value => window[kind].removeItem(key(value)),
  };
}
export const privateLocalStorage = scoped("localStorage");
export const privateSessionStorage = scoped("sessionStorage");
