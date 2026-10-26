import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { AuthBoundary } from "./AuthBoundary";

const mocks = vi.hoisted(() => ({ load: vi.fn(), fetch: vi.fn(), end: vi.fn() }));
vi.mock("./transport", () => ({ loadSession: mocks.load, authenticatedFetch: mocks.fetch, endBrowserSession: mocks.end }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function Draft() {
  const [value, setValue] = useState("unsaved");
  return <input aria-label="draft" value={value} onChange={event => setValue(event.target.value)} />;
}
describe("authenticated workspace", () => {
  it("does not mount private content before login", async () => {
    mocks.load.mockResolvedValue({ mode: "cognito", authenticated: false });
    render(<AuthBoundary><Draft /></AuthBoundary>);
    expect(await screen.findByRole("link", { name: "ログイン" })).toHaveAttribute("href", "/api/auth/login");
    expect(screen.queryByLabelText("draft")).toBeNull();
  });
  it("hides an expired workspace while retaining the unsaved draft", async () => {
    mocks.load.mockResolvedValue({ mode: "cognito", authenticated: true });
    render(<AuthBoundary><Draft /></AuthBoundary>);
    fireEvent.change(await screen.findByLabelText("draft"), { target: { value: "retain me" } });
    act(() => { window.dispatchEvent(new Event("fuminosuke-auth-required")); });
    expect(screen.getByLabelText("draft")).not.toBeVisible();
    mocks.load.mockResolvedValue({ mode: "cognito", authenticated: true });
    act(() => { window.dispatchEvent(new Event("focus")); });
    await screen.findByRole("button", { name: "ログアウト" });
    expect(screen.getByLabelText("draft")).toHaveValue("retain me");
    expect(screen.getByLabelText("draft")).toBeVisible();
  });
  it("keeps the workspace on a failed logout", async () => {
    mocks.load.mockResolvedValue({ mode: "cognito", authenticated: true });
    mocks.fetch.mockResolvedValue({ ok: false });
    render(<AuthBoundary><Draft /></AuthBoundary>);
    fireEvent.click(await screen.findByRole("button", { name: "ログアウト" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ログアウトできませんでした。");
    expect(screen.getByLabelText("draft")).toBeVisible();
  });
  it("checks unsaved work before sending logout and allows returning to the draft", async () => {
    mocks.load.mockResolvedValue({ mode: "cognito", authenticated: true });
    const guard = (event: Event) => event.preventDefault();
    window.addEventListener("fuminosuke-before-logout", guard);
    try {
      render(<AuthBoundary><Draft /></AuthBoundary>);
      fireEvent.click(await screen.findByRole("button", { name: "ログアウト" }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(mocks.fetch).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "戻る" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.getByLabelText("draft")).toHaveValue("unsaved");
      fireEvent.click(screen.getByRole("button", { name: "ログアウト" }));
      mocks.fetch.mockResolvedValue({ ok: true });
      mocks.end.mockReturnValue({ mode: "cognito", authenticated: false });
      fireEvent.click(screen.getByRole("button", { name: "ログアウトする" }));
      expect(await screen.findByText("ログアウトしました。")).toBeInTheDocument();
      expect(screen.queryByLabelText("draft")).toBeNull();
    } finally { window.removeEventListener("fuminosuke-before-logout", guard); }
  });
});
