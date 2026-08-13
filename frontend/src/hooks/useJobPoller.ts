import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Job } from "../types";

const TERMINAL = new Set(["REVIEW_REQUIRED", "CONFIRMED", "FAILED", "CANCELLED", "INTERRUPTED"]);

export function useJobPoller(
  jobId: string | null,
  onTerminal?: (job: Job) => void,
  refreshToken = 0,
) {
  const [job, setJob] = useState<Job | null>(null);
  const [connectionLost, setConnectionLost] = useState(false);
  const terminalCallback = useRef(onTerminal);
  terminalCallback.current = onTerminal;

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let timer = 0;
    let delay = 1000;
    const poll = async () => {
      try {
        const next = await api.job(jobId);
        if (stopped) return;
        setJob(next);
        setConnectionLost(false);
        delay = 1000;
        if (TERMINAL.has(next.status)) {
          terminalCallback.current?.(next);
          return;
        }
      } catch {
        if (stopped) return;
        setConnectionLost(true);
        delay = Math.min(delay * 2, 15000);
      }
      timer = window.setTimeout(poll, delay);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [jobId, refreshToken]);
  return { job, connectionLost };
}
