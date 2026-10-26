import { useSyncExternalStore } from "react";

// Notices when a newer release of the screen is deployed, so a tab left open does not keep running
// the old code against the new server. The deployed index.html names its entry script with a content
// hash (assets/index-<hash>.js); a different name there than the one this tab loaded means a newer
// release. The dev server loads /src/main.tsx instead, so nothing is checked there.

const ENTRY = /\/assets\/index-[\w-]+\.js/;
const CHECK_EVERY_MS = 5 * 60_000;

let stale = false;
const listeners = new Set<() => void>();

function loadedEntry(): string | null {
  for (const script of document.querySelectorAll<HTMLScriptElement>("script[src]")) {
    const match = script.src.match(ENTRY);
    if (match) return match[0];
  }
  return null;
}

async function check() {
  const mine = loadedEntry();
  if (!mine || stale || document.hidden) return;
  try {
    const response = await fetch("/index.html", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) return;
    const deployed = (await response.text()).match(ENTRY)?.[0];
    if (!deployed || deployed === mine) return;
    stale = true;
    listeners.forEach((listener) => listener());
  } catch {
    // Offline or blocked: try again at the next check.
  }
}

/** Checks on returning to the tab and every few minutes while it is shown. */
export function installVersionWatch() {
  if (!loadedEntry()) return;
  document.addEventListener("visibilitychange", () => { void check(); });
  window.addEventListener("focus", () => { void check(); });
  window.setInterval(() => { void check(); }, CHECK_EVERY_MS);
}

/** True once a newer release than this tab's is deployed. */
export function newerReleaseDeployed() {
  return stale;
}

export function useNewerRelease() {
  return useSyncExternalStore(
    (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    () => stale,
  );
}
