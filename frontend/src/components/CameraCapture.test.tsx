import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { CameraCapture } from "./CameraCapture";

const originalMediaDevices = Object.getOwnPropertyDescriptor(
  navigator,
  "mediaDevices",
);
const originalVideoWidth = Object.getOwnPropertyDescriptor(
  HTMLVideoElement.prototype,
  "videoWidth",
);
const originalVideoHeight = Object.getOwnPropertyDescriptor(
  HTMLVideoElement.prototype,
  "videoHeight",
);

let getUserMedia: ReturnType<typeof vi.fn>;
let stopTrack: ReturnType<typeof vi.fn>;

beforeEach(() => {
  stopTrack = vi.fn();
  getUserMedia = vi.fn().mockResolvedValue({
    getTracks: () => [{ stop: stopTrack }],
  } as unknown as MediaStream);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  Object.defineProperty(HTMLVideoElement.prototype, "videoWidth", {
    configurable: true,
    get: () => 640,
  });
  Object.defineProperty(HTMLVideoElement.prototype, "videoHeight", {
    configurable: true,
    get: () => 480,
  });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    (() => ({
      drawImage: vi.fn(),
      getImageData: () => ({
        data: new Uint8ClampedArray(180 * 130 * 4),
      }),
    })) as unknown as HTMLCanvasElement["getContext"],
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  if (originalMediaDevices) {
    Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
  } else {
    Reflect.deleteProperty(navigator, "mediaDevices");
  }
  if (originalVideoWidth) {
    Object.defineProperty(
      HTMLVideoElement.prototype,
      "videoWidth",
      originalVideoWidth,
    );
  }
  if (originalVideoHeight) {
    Object.defineProperty(
      HTMLVideoElement.prototype,
      "videoHeight",
      originalVideoHeight,
    );
  }
});

test("shows an actionable message when camera permission is denied", async () => {
  getUserMedia.mockRejectedValue(
    new DOMException("Permission denied", "NotAllowedError"),
  );

  render(
    <CameraCapture
      documentAspect={1.414}
      onCapture={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "カメラの使用が許可されていません",
  );
  expect(screen.getByRole("button", { name: "手動シャッター" })).toBeDisabled();
});

test("stops every camera track when the dialog is unmounted", async () => {
  const { unmount } = render(
    <CameraCapture
      documentAspect={1.414}
      onCapture={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  await waitFor(() => {
    expect(screen.getByRole("button", { name: "手動シャッター" })).toBeEnabled();
  });
  unmount();

  expect(stopTrack).toHaveBeenCalledTimes(1);
});

test("stops the acquired stream when video startup fails", async () => {
  vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(
    new DOMException("Playback failed", "NotAllowedError"),
  );

  render(
    <CameraCapture
      documentAspect={1.414}
      onCapture={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "カメラの使用が許可されていません",
  );
  expect(stopTrack).toHaveBeenCalledTimes(1);
});

test("locks capture so rapid shutter clicks create only one image", async () => {
  const blobControl: { finish?: BlobCallback } = {};
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(
    (callback) => {
      blobControl.finish = callback;
    },
  );
  const onCapture = vi.fn();
  const onClose = vi.fn();
  render(
    <CameraCapture
      documentAspect={1.414}
      onCapture={onCapture}
      onClose={onClose}
    />,
  );

  const shutter = screen.getByRole("button", { name: "手動シャッター" });
  await waitFor(() => expect(shutter).toBeEnabled());
  fireEvent.click(shutter);
  fireEvent.click(shutter);

  expect(HTMLCanvasElement.prototype.toBlob).toHaveBeenCalledTimes(1);
  expect(blobControl.finish).toBeDefined();
  blobControl.finish?.(new Blob(["image"], { type: "image/jpeg" }));

  await waitFor(() => {
    expect(onCapture).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  expect(stopTrack).toHaveBeenCalledTimes(1);
});
