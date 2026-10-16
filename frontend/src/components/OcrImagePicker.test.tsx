import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { OcrImagePicker } from "./OcrImagePicker";

beforeEach(() => {
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn(() => "blob:preview");
    static revokeObjectURL = vi.fn();
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("releases previews on replacement and unmount", () => {
  const create = vi.spyOn(URL, "createObjectURL").mockReturnValueOnce("blob:first").mockReturnValueOnce("blob:second");
  const revoke = vi.spyOn(URL, "revokeObjectURL");
  const first = new File(["a"], "first.png", { type: "image/png" });
  const second = new File(["b"], "second.png", { type: "image/png" });
  const onChange = vi.fn();
  const { rerender, unmount } = render(<OcrImagePicker file={first} onChange={onChange} />);
  expect(screen.getByRole("img")).toHaveAttribute("src", "blob:first");
  rerender(<OcrImagePicker file={second} onChange={onChange} />);
  expect(revoke).toHaveBeenCalledWith("blob:first");
  expect(screen.getByRole("img")).toHaveAttribute("src", "blob:second");
  expect(create).toHaveBeenCalledTimes(2);
  unmount();
  expect(revoke).toHaveBeenCalledWith("blob:second");
});

test("cancelled or invalid file choices preserve the current file", () => {
  const onChange = vi.fn();
  render(<OcrImagePicker file={new File(["a"], "kept.png", { type: "image/png" })} onChange={onChange} />);
  const input = screen.getByLabelText("帳票画像を置き換える");
  fireEvent.change(input, { target: { files: [] } });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { files: [new File(["text"], "notes.txt", { type: "text/plain" })] } });
  expect(screen.getByRole("alert")).toHaveTextContent("対応していないファイル形式");
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByText("kept.png")).toBeInTheDocument();
});
