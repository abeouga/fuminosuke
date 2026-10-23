import { api } from "./api";

/** Re-edits being put back to their confirmed version when a review screen is left. */
const pending = new Set<Promise<unknown>>();

/** Leaving by the side menu: the restore runs after the screen is gone, so lists wait for it. */
export function restoreAfterLeaving(jobId: string, revision: number, paged = false): void {
    const request = (paged ? api.cancelReopenPages(jobId, revision) : api.cancelReopen(jobId, revision)).catch(() => undefined);
    pending.add(request);
    void request.finally(() => pending.delete(request));
}

/** Resolves once every restore started by a closed review screen has finished. */
export function leftReviewsSettled(): Promise<unknown> {
    return Promise.allSettled([...pending]);
}
