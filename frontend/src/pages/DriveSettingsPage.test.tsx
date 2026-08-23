import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DriveSettingsPage } from "./DriveSettingsPage";

const pickerMock = vi.hoisted(() => vi.fn());

vi.mock("../googlePicker", () => ({
  pickGoogleDriveFolder: pickerMock,
}));

afterEach(() => {
  cleanup();
  pickerMock.mockReset();
  vi.unstubAllGlobals();
});

test("selects a Drive output folder through Google Picker", async () => {
  pickerMock.mockResolvedValue({ id: "year-1", name: "2026年度" });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/google-drive/output-folder") && (!init?.method || init.method === "GET")) {
      return new Response(JSON.stringify({
        pickerConfigured: true,
        pickerConfigurationError: null,
        folderId: "root-1",
        folderName: "帳票OCR",
      }), { status: 200 });
    }
    if (path.endsWith("/google-drive/picker-session") && init?.method === "POST") {
      return new Response(JSON.stringify({
        accessToken: "short-lived-token",
        developerKey: "browser-key",
        appId: "123456789",
      }), { status: 200 });
    }
    if (path.endsWith("/google-drive/output-folder") && init?.method === "PUT") {
      expect(JSON.parse(String(init.body))).toEqual({ folderId: "year-1" });
      return new Response(JSON.stringify({
        pickerConfigured: true,
        pickerConfigurationError: null,
        folderId: "year-1",
        folderName: "2026年度",
      }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<DriveSettingsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Google Driveから保存先を選ぶ" }));

  expect(await screen.findByRole("heading", { name: "2026年度" })).toBeInTheDocument();
  expect(pickerMock).toHaveBeenCalledWith({
    accessToken: "short-lived-token",
    developerKey: "browser-key",
    appId: "123456789",
  });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
});

test("keeps the current folder when the Picker is cancelled", async () => {
  pickerMock.mockResolvedValue(null);
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/google-drive/output-folder")) {
      return new Response(JSON.stringify({
        pickerConfigured: true,
        pickerConfigurationError: null,
        folderId: "root-1",
        folderName: "帳票OCR",
      }), { status: 200 });
    }
    return new Response(JSON.stringify({
      accessToken: "short-lived-token",
      developerKey: "browser-key",
      appId: "123456789",
    }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<DriveSettingsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Google Driveから保存先を選ぶ" }));

  await waitFor(() => expect(pickerMock).toHaveBeenCalledOnce());
  expect(screen.getByRole("heading", { name: "帳票OCR" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("explains when the Picker project does not match OAuth", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    pickerConfigured: false,
    pickerConfigurationError: "PickerのApp IDがOAuthクライアントのプロジェクト番号と一致していません。",
    folderId: null,
    folderName: null,
  }), { status: 200 })));

  render(<DriveSettingsPage />);

  expect(await screen.findByText(/プロジェクト番号と一致していません/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Google Driveから保存先を選ぶ" })).toBeDisabled();
});
