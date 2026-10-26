import { Component, type ReactNode } from "react";

// A page that fails to load or draw (most often: a part of the screen removed by a newer release while
// this tab stayed open) shows a way back instead of leaving the whole window blank.
const RELOAD_KEY = "fuminosuke.chunkReloadAt";

/** Reloads once when a part of the screen can no longer be fetched (a newer release replaced it). */
export function installChunkReload() {
  window.addEventListener("vite:preloadError", (event) => {
    let last = 0;
    try { last = Number(sessionStorage.getItem(RELOAD_KEY)) || 0; } catch { /* storage may be off */ }
    // Once per ten seconds at most, so a part that is really missing ends in the message below, not a loop.
    if (Date.now() - last < 10_000) return;
    try { sessionStorage.setItem(RELOAD_KEY, String(Date.now())); } catch { /* storage may be off */ }
    event.preventDefault();
    window.location.reload();
  });
}

export class PageErrorBoundary extends Component<{ resetKey: string; children: ReactNode }, { failed: boolean; key: string }> {
  state = { failed: false, key: this.props.resetKey };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: { resetKey: string }, state: { failed: boolean; key: string }) {
    // Moving to another page tries again.
    return props.resetKey !== state.key ? { failed: false, key: props.resetKey } : null;
  }

  componentDidCatch(error: unknown) {
    console.error("[文之助] 画面を表示できませんでした", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="page-load-failed" role="alert">
        <strong>画面を表示できませんでした。</strong>
        <span>新しい版が配信された可能性があります。再読み込みすると開き直します。</span>
        <button type="button" className="primary" onClick={() => window.location.reload()}>再読み込み</button>
      </div>
    );
  }
}
