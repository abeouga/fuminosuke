import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TemplateNameEditor } from "./TemplateNameEditor";

afterEach(cleanup);

it("edits the heading inline and saves with Enter", async () => {
  const onSave = vi.fn().mockResolvedValue("新しい帳票名");
  render(<TemplateNameEditor headingId="template-title" name="帳票名" onSave={onSave} />);

  fireEvent.click(screen.getByRole("button", { name: "テンプレート名を変更" }));
  const input = screen.getByRole("textbox", { name: "テンプレート名" });
  expect(input).toHaveFocus();
  fireEvent.change(input, { target: { value: " 新しい帳票名 " } });
  fireEvent.keyDown(input, { key: "Enter" });

  await waitFor(() => expect(onSave).toHaveBeenCalledWith("新しい帳票名"));
  expect(await screen.findByRole("heading", { name: "新しい帳票名" })).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "テンプレート名" })).not.toBeInTheDocument();
});

it("cancels inline editing with Escape without saving", () => {
  const onSave = vi.fn();
  render(<TemplateNameEditor headingId="template-title" name="帳票名" onSave={onSave} />);

  fireEvent.click(screen.getByRole("button", { name: "テンプレート名を変更" }));
  const input = screen.getByRole("textbox", { name: "テンプレート名" });
  fireEvent.change(input, { target: { value: "変更途中" } });
  fireEvent.keyDown(input, { key: "Escape" });

  expect(screen.getByRole("heading", { name: "帳票名" })).toBeInTheDocument();
  expect(onSave).not.toHaveBeenCalled();
});
