import { afterEach, expect, test, vi } from "vitest";
import { mountGoogleDriveSpreadsheetPicker } from "./googlePicker";

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

test("embeds the official Picker URI in an iframe and removes only that iframe", async () => {
  const unrelated = document.createElement("span");
  unrelated.textContent = "既存画面の通知";
  const container = document.createElement("div");
  document.body.append(unrelated, container);
  const pickerCallbacks: Array<(data: Record<string, unknown>) => void> = [];
  const visibleStates: boolean[] = [];

  class FakeDocsView {
    setIncludeFolders() { return this; }
    setSelectFolderEnabled() { return this; }
    setMimeTypes() { return this; }
    setMode() { return this; }
    setParent() { return this; }
  }

  class FakePickerBuilder {
    addView() { return this; }
    setOAuthToken() { return this; }
    setDeveloperKey() { return this; }
    setAppId() { return this; }
    setOrigin() { return this; }
    setTitle() { return this; }
    setSize() { return this; }
    setCallback(callback: (data: Record<string, unknown>) => void) {
      pickerCallbacks.push(callback);
      return this;
    }
    build() {
      return {
        setVisible(visible: boolean) {
          visibleStates.push(visible);
          if (!visible) return;
          const backdrop = document.createElement("div");
          backdrop.className = "picker-dialog-bg";
          const dialog = document.createElement("div");
          dialog.className = "picker-dialog";
          const iframe = document.createElement("iframe");
          iframe.src = "https://docs.google.com/picker?embedded=true";
          iframe.style.position = "absolute";
          iframe.width = "1051";
          iframe.height = "650";
          dialog.appendChild(iframe);
          document.body.append(backdrop, dialog);
        },
      };
    }
  }

  const globals = window as unknown as {
    gapi: { load: (_name: string, options: { callback: () => void }) => void };
    google: { picker: Record<string, unknown> };
  };
  globals.gapi = { load: (_name, options) => options.callback() };
  globals.google = {
    picker: {
      Action: { CANCEL: "cancel", PICKED: "picked" },
      Document: { ID: "id", NAME: "name", URL: "url" },
      DocsView: FakeDocsView,
      DocsViewMode: { LIST: "list" },
      PickerBuilder: FakePickerBuilder,
      Response: { ACTION: "action", DOCUMENTS: "documents" },
      ViewId: { DOCS: "docs" },
    },
  };
  const onPicked = vi.fn();

  const mounting = mountGoogleDriveSpreadsheetPicker(
    container,
    { accessToken: "token", developerKey: "key", appId: "app" },
    "root-folder",
    onPicked,
  );

  await vi.waitFor(() => {
    expect(container.querySelector("iframe")).not.toBeNull();
  });
  const iframe = container.querySelector("iframe");
  expect(iframe).not.toBeNull();
  expect(iframe?.src).toBe("https://docs.google.com/picker?embedded=true");
  expect(iframe?.title).toBe("Google Driveのスプレッドシート選択");
  expect(iframe?.getAttribute("style")).toBeNull();
  expect(iframe?.getAttribute("width")).toBeNull();
  expect(iframe?.getAttribute("height")).toBeNull();
  expect(document.querySelector(".picker-dialog")).toBeNull();
  expect(document.querySelector(".picker-dialog-bg")).toBeNull();
  expect(visibleStates).toEqual([true]);
  expect(unrelated.parentElement).toBe(document.body);
  const mounted = await mounting;

  const pickerCallback = pickerCallbacks[0];
  if (pickerCallback === undefined) {
    throw new Error("Picker callback was not registered");
  }
  pickerCallback({
    action: "picked",
    documents: [{ id: "sheet-1", name: "台帳", url: "https://docs.google.com/sheet-1" }],
  });
  expect(onPicked).toHaveBeenCalledWith({
    id: "sheet-1",
    name: "台帳",
    url: "https://docs.google.com/sheet-1",
  });

  mounted.dispose();
  expect(visibleStates).toEqual([true, false]);
  expect(iframe?.isConnected).toBe(false);
  expect(unrelated.parentElement).toBe(document.body);
});
