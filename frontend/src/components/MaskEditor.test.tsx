import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MaskEditor } from "./MaskEditor";

class MockImageData {
  data: Uint8ClampedArray;
  width: number;
  height: number;

  constructor(data: Uint8ClampedArray, width: number, height: number) {
    this.data = data;
    this.width = width;
    this.height = height;
  }
}

const imageData = (width: number, height: number) => new MockImageData(
  new Uint8ClampedArray(Math.max(0, width * height * 4)),
  width,
  height,
);

beforeEach(() => {
  class MockImage {
    naturalWidth = 4;
    naturalHeight = 4;
    onload: ((event: Event) => void) | null = null;

    set src(_value: string) {
      queueMicrotask(() => this.onload?.(new Event("load")));
    }
  }

  const context = {
    arc: vi.fn(),
    beginPath: vi.fn(),
    clearRect: vi.fn(),
    createImageData: vi.fn((width: number, height: number) => imageData(width, height)),
    drawImage: vi.fn(),
    fill: vi.fn(),
    fillStyle: "",
    getImageData: vi.fn((
      _x: number,
      _y: number,
      width: number,
      height: number,
    ) => imageData(width, height)),
    globalCompositeOperation: "source-over",
    putImageData: vi.fn(),
    restore: vi.fn(),
    save: vi.fn(),
  };

  vi.stubGlobal("Image", MockImage);
  vi.stubGlobal("ImageData", MockImageData);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    (() => context) as unknown as HTMLCanvasElement["getContext"],
  );
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((callback) => {
    callback(new Blob(["mask"], { type: "image/png" }));
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("keeps dirty edits and shows the API error until a save succeeds", async () => {
  const onSave = vi.fn()
    .mockRejectedValueOnce(new Error("保存APIが失敗しました。"))
    .mockResolvedValueOnce(undefined);
  const onDirtyChange = vi.fn();
  render(
    <MaskEditor
      source="data:image/png;base64,"
      background="data:image/png;base64,"
      onSave={onSave}
      onDirtyChange={onDirtyChange}
    />,
  );

  await waitFor(() => {
    expect(screen.getByLabelText("手書き候補マスク編集")).toHaveAttribute("width", "4");
  });
  fireEvent.click(screen.getByRole("button", { name: "すべて除外" }));
  expect(screen.getByText("保存していない選別変更があります。")).toBeInTheDocument();
  expect(onDirtyChange).toHaveBeenLastCalledWith(true);

  fireEvent.click(screen.getByRole("button", { name: "選別結果を保存" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("保存APIが失敗しました。");
  expect(screen.getByText("保存していない選別変更があります。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "選別結果を保存" })).toBeEnabled();
  expect(onDirtyChange).toHaveBeenLastCalledWith(true);

  fireEvent.click(screen.getByRole("button", { name: "選別結果を保存" }));

  await waitFor(() => {
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("保存していない選別変更があります。")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "選別結果は保存済み" })).toBeDisabled();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });
  expect(onSave).toHaveBeenCalledTimes(2);
});
