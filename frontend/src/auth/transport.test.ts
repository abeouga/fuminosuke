import { beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => { vi.resetModules(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals(); });
describe("private transport and draft storage", () => {
  it("does not restore a logged-out session from a late session response", async () => {
    let resolve!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise(value => { resolve = value; })));
    const api = await import("./transport");
    const pending = api.loadSession(); api.endBrowserSession();
    resolve({ ok: true, json: async () => ({ mode: "cognito", authenticated: true, csrfToken: "obsolete" }) });
    expect((await pending).authenticated).toBe(false);
    expect(api.csrfHeaders()).toEqual({});
  });
  it("adds the session CSRF header and refuses external destinations", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ mode: "cognito", authenticated: true, csrfToken: "csrf-a", storageScope: "tenant-a" }) }).mockResolvedValue({ status: 200 });
    vi.stubGlobal("fetch", fetcher);
    const api = await import("./transport"); await api.loadSession();
    await api.authenticatedFetch("/api/v1/templates", { method: "POST" });
    expect(new Headers(fetcher.mock.calls[1][1].headers).get("X-CSRF-Token")).toBe("csrf-a");
    await expect(api.authenticatedFetch("https://elsewhere.example/api/steal")).rejects.toThrow("許可されていない通信先");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("keeps different accounts' drafts separate", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ mode: "cognito", authenticated: true, storageScope: "tenant-a" }) }));
    let api = await import("./transport"); await api.loadSession();
    api.privateLocalStorage.setItem("draft", "a"); api.privateSessionStorage.setItem("receipt", "a");
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ mode: "cognito", authenticated: true, storageScope: "tenant-b" }) }));
    api = await import("./transport"); await api.loadSession();
    expect(api.privateLocalStorage.getItem("draft")).toBeNull();
    expect(api.privateSessionStorage.getItem("receipt")).toBeNull();
    expect(localStorage.getItem("fuminosuke:tenant-a:draft")).toBe("a");
  });
  it("preserves the owner's draft namespace after expiry and clears the CSRF token", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ mode: "cognito", authenticated: true, storageScope: "tenant-a", csrfToken: "csrf-a" }) }).mockResolvedValue({ ok: true, json: async () => ({ mode: "cognito", authenticated: false }) });
    vi.stubGlobal("fetch", fetcher);
    const api = await import("./transport"); await api.loadSession();
    api.privateLocalStorage.setItem("draft", "retain"); await api.loadSession();
    expect(api.privateLocalStorage.getItem("draft")).toBe("retain"); expect(api.csrfHeaders()).toEqual({});
  });
});
