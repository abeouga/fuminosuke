import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { showToast, ToastViewport } from "./Toast";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("announces errors immediately and removes them after the configured duration", () => {
  vi.useFakeTimers();
  render(<ToastViewport />);

  act(() => {
    showToast({
      tone: "error",
      title: "読み取りを開始できませんでした",
      duration: 1200,
    });
  });

  const alert = screen.getByRole("alert");
  expect(alert).toHaveAttribute("aria-live", "assertive");
  expect(alert).toHaveTextContent("読み取りを開始できませんでした");

  act(() => vi.advanceTimersByTime(1200));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("keeps informational notifications polite", () => {
  render(<ToastViewport />);

  act(() => {
    showToast({ tone: "success", title: "保存しました" });
  });

  const status = screen.getByRole("status");
  expect(status).toHaveAttribute("aria-live", "polite");
});
