import { userText } from "../userMessages";
import { useEffect, useRef, useState } from "react";

type ToastTone = "info" | "success" | "warning" | "error";

type ToastInput = {
  tone?: ToastTone;
  title: string;
  message?: string;
  duration?: number;
  actionLabel?: string;
  onAction?: () => void;
};

type ToastItem = ToastInput & { id: string };

const TOAST_EVENT = "ledger-toast";

export function showToast(input: ToastInput) {
  const shown = { ...input, title: userText(input.title, "通知", input.title), message: input.message ? userText(input.message, "通知") : input.message };
  window.dispatchEvent(new CustomEvent<ToastInput>(TOAST_EVENT, { detail: shown }));
}

export function ToastViewport() {
  const [items, setItems] = useState<ToastItem[]>([]);
  const timers = useRef(new Map<string, number>());

  const dismiss = (id: string) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.current.delete(id);
    setItems((current) => current.filter((item) => item.id !== id));
  };

  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<ToastInput>).detail;
      const id = typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random()}`;
      const item = { ...detail, id };
      setItems((current) => [...current.slice(-2), item]);
      const timer = window.setTimeout(() => dismiss(id), detail.duration ?? 5200);
      timers.current.set(id, timer);
    };
    window.addEventListener(TOAST_EVENT, receive);
    return () => {
      window.removeEventListener(TOAST_EVENT, receive);
      timers.current.forEach((timer) => window.clearTimeout(timer));
      timers.current.clear();
    };
  }, []);

  return (
    <div className="toast-viewport">
      {items.map((item) => (
        <section
          aria-atomic="true"
          aria-live={item.tone === "error" ? "assertive" : "polite"}
          className={`toast-card ${item.tone ?? "info"}`}
          key={item.id}
          role={item.tone === "error" ? "alert" : "status"}
        >
          <span className="toast-mark" aria-hidden="true" />
          <div className="toast-copy">
            <strong>{item.title}</strong>
            {item.message && <span>{item.message}</span>}
          </div>
          {item.actionLabel && item.onAction && (
            <button
              type="button"
              className="toast-action"
              onClick={() => {
                item.onAction?.();
                dismiss(item.id);
              }}
            >
              {item.actionLabel}
            </button>
          )}
          <button
            type="button"
            className="toast-close"
            aria-label="通知を閉じる"
            onClick={() => dismiss(item.id)}
          >
            ×
          </button>
        </section>
      ))}
    </div>
  );
}
