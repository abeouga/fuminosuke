import type { DrivePickerSession } from "./types";

const PICKER_SCRIPT_ID = "google-picker-api";
const PICKER_SCRIPT_URL = "https://apis.google.com/js/api.js";
const PICKER_FRAME_TIMEOUT_MS = 10_000;
const FOLDER_MIME_TYPE = "application/vnd.google-apps.folder";
const SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet";

type PickerData = Record<string, unknown>;

type PickerInstance = {
  setVisible: (visible: boolean) => void;
};

type DocsViewInstance = {
  setIncludeFolders: (enabled: boolean) => DocsViewInstance;
  setMode: (mode: unknown) => DocsViewInstance;
  setSelectFolderEnabled: (enabled: boolean) => DocsViewInstance;
  setMimeTypes: (mimeTypes: string) => DocsViewInstance;
  setParent: (folderId: string) => DocsViewInstance;
};

type PickerBuilderInstance = {
  addView: (view: DocsViewInstance) => PickerBuilderInstance;
  setAppId: (appId: string) => PickerBuilderInstance;
  setCallback: (callback: (data: PickerData) => void) => PickerBuilderInstance;
  setDeveloperKey: (developerKey: string) => PickerBuilderInstance;
  setOAuthToken: (accessToken: string) => PickerBuilderInstance;
  setOrigin: (origin: string) => PickerBuilderInstance;
  setSize: (width: number, height: number) => PickerBuilderInstance;
  setTitle: (title: string) => PickerBuilderInstance;
  build: () => PickerInstance;
};

type GooglePickerApi = {
  Action: { CANCEL: unknown; PICKED: unknown };
  Document: { ID: string; NAME: string; URL: string };
  DocsView: new (viewId: unknown) => DocsViewInstance;
  DocsViewMode: { LIST: unknown };
  PickerBuilder: new () => PickerBuilderInstance;
  Response: { ACTION: string; DOCUMENTS: string };
  ViewId: { DOCS: unknown };
};

type PickerGlobals = Window & {
  gapi?: {
    load: (
      name: string,
      options: {
        callback: () => void;
        onerror: () => void;
        ontimeout: () => void;
        timeout: number;
      },
    ) => void;
  };
  google?: { picker?: GooglePickerApi };
};

export type PickedDriveFolder = { id: string; name: string };
export type PickedDriveSpreadsheet = { id: string; name: string; url: string };
export type MountedDriveSpreadsheetPicker = { dispose: () => void };

let pickerApiPromise: Promise<GooglePickerApi> | null = null;

export async function pickGoogleDriveFolder(
  session: DrivePickerSession,
  initialFolderId?: string | null,
): Promise<PickedDriveFolder | null> {
  const picker = await loadPickerApi();
  let view = new picker.DocsView(picker.ViewId.DOCS)
    .setIncludeFolders(true)
    .setSelectFolderEnabled(true)
    .setMimeTypes(FOLDER_MIME_TYPE);
  if (initialFolderId) view = view.setParent(initialFolderId);

  return new Promise((resolve) => {
    let settled = false;
    const settle = (value: PickedDriveFolder | null) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const instance = new picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(session.accessToken)
      .setDeveloperKey(session.developerKey)
      .setAppId(session.appId)
      .setOrigin(window.location.origin)
      .setTitle("読み取り結果の保存先を選択")
      .setSize(
        Math.max(320, Math.min(920, window.innerWidth - 32)),
        Math.max(360, Math.min(640, window.innerHeight - 32)),
      )
      .setCallback((data) => {
        const action = data[picker.Response.ACTION];
        if (action === picker.Action.CANCEL) {
          settle(null);
          return;
        }
        if (action !== picker.Action.PICKED) return;
        const documents = data[picker.Response.DOCUMENTS];
        const document = Array.isArray(documents) ? documents[0] : null;
        if (!document || typeof document !== "object") {
          settle(null);
          return;
        }
        const item = document as PickerData;
        const id = item[picker.Document.ID];
        const name = item[picker.Document.NAME];
        settle(
          typeof id === "string" && id
            ? { id, name: typeof name === "string" && name ? name : "選択したフォルダー" }
            : null,
        );
      })
      .build();
    instance.setVisible(true);
  });
}

export async function mountGoogleDriveSpreadsheetPicker(
  container: HTMLElement,
  session: DrivePickerSession,
  initialFolderId: string,
  onPicked: (spreadsheet: PickedDriveSpreadsheet) => void,
): Promise<MountedDriveSpreadsheetPicker> {
  const picker = await loadPickerApi();
  const view = new picker.DocsView(picker.ViewId.DOCS)
    .setIncludeFolders(true)
    .setSelectFolderEnabled(false)
    .setMimeTypes(SPREADSHEET_MIME_TYPE)
    .setMode(picker.DocsViewMode.LIST)
    .setParent(initialFolderId);

  const preexistingFrames = new Set(document.querySelectorAll("iframe"));
  const preexistingPickerChrome = new Set(
    document.querySelectorAll(".picker-dialog, .picker-dialog-bg"),
  );
  let pickerFrame: HTMLIFrameElement | null = null;
  let pickerInstance: PickerInstance | null = null;
  const builder = new picker.PickerBuilder()
    .addView(view)
    .setOAuthToken(session.accessToken)
    .setDeveloperKey(session.developerKey)
    .setAppId(session.appId)
    .setOrigin(window.location.origin)
    .setTitle("Google Driveのスプレッドシートを選択")
    .setSize(1051, 650)
    .setCallback((data) => {
      if (data[picker.Response.ACTION] !== picker.Action.PICKED) return;
      const documents = data[picker.Response.DOCUMENTS];
      const document = Array.isArray(documents) ? documents[0] : null;
      if (!document || typeof document !== "object") return;
      const item = document as PickerData;
      const id = item[picker.Document.ID];
      if (typeof id !== "string" || !id) return;
      const name = item[picker.Document.NAME];
      const url = item[picker.Document.URL];
      onPicked({
        id,
        name: typeof name === "string" && name ? name : "選択したスプレッドシート",
        url: typeof url === "string" && url
          ? url
          : `https://docs.google.com/spreadsheets/d/${encodeURIComponent(id)}/edit`,
      });
    });
  const framePromise = waitForGooglePickerFrame(preexistingFrames);
  try {
    pickerInstance = builder.build();
    pickerInstance.setVisible(true);
    pickerFrame = await framePromise;
  } catch (error) {
    pickerInstance?.setVisible(false);
    removeNewPickerChrome(preexistingPickerChrome);
    throw error;
  }

  pickerFrame.title = "Google Driveのスプレッドシート選択";
  pickerFrame.setAttribute("aria-label", "Google Driveのスプレッドシート選択");
  pickerFrame.removeAttribute("style");
  pickerFrame.removeAttribute("width");
  pickerFrame.removeAttribute("height");
  container.replaceChildren(pickerFrame);
  removeNewPickerChrome(preexistingPickerChrome);

  return {
    dispose: () => {
      pickerInstance?.setVisible(false);
      pickerFrame?.remove();
      removeNewPickerChrome(preexistingPickerChrome);
    },
  };
}

function waitForGooglePickerFrame(
  preexistingFrames: ReadonlySet<HTMLIFrameElement>,
): Promise<HTMLIFrameElement> {
  const findFrame = () => Array.from(document.querySelectorAll("iframe")).find((frame) => {
    if (preexistingFrames.has(frame)) return false;
    try {
      const url = new URL(frame.src, window.location.href);
      return url.hostname === "docs.google.com" && url.pathname === "/picker";
    } catch {
      return false;
    }
  });
  const existing = findFrame();
  if (existing) return Promise.resolve(existing);

  return new Promise((resolve, reject) => {
    const observer = new MutationObserver(() => {
      const frame = findFrame();
      if (!frame) return;
      window.clearTimeout(timeoutId);
      observer.disconnect();
      resolve(frame);
    });
    const timeoutId = window.setTimeout(() => {
      observer.disconnect();
      reject(new Error("Google Pickerの表示がタイムアウトしました。"));
    }, PICKER_FRAME_TIMEOUT_MS);
    observer.observe(document.body, { childList: true, subtree: true });
  });
}

function removeNewPickerChrome(preexisting: ReadonlySet<Element>): void {
  document.querySelectorAll(".picker-dialog, .picker-dialog-bg").forEach((element) => {
    if (!preexisting.has(element)) element.remove();
  });
}

function loadPickerApi(): Promise<GooglePickerApi> {
  if (pickerApiPromise) return pickerApiPromise;
  pickerApiPromise = loadPickerScript()
    .then(() => new Promise<void>((resolve, reject) => {
      const globals = window as PickerGlobals;
      if (!globals.gapi) {
        reject(new Error("Google Pickerの読み込みに失敗しました。"));
        return;
      }
      globals.gapi.load("picker", {
        callback: resolve,
        onerror: () => reject(new Error("Google Pickerを初期化できませんでした。")),
        ontimeout: () => reject(new Error("Google Pickerの初期化がタイムアウトしました。")),
        timeout: 10_000,
      });
    }))
    .then(() => {
      const picker = (window as PickerGlobals).google?.picker;
      if (!picker) throw new Error("Google Pickerを利用できません。ページを再読み込みしてください。");
      return picker;
    })
    .catch((error: unknown) => {
      pickerApiPromise = null;
      throw error;
    });
  return pickerApiPromise;
}

function loadPickerScript(): Promise<void> {
  if ((window as PickerGlobals).gapi) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.getElementById(PICKER_SCRIPT_ID) as HTMLScriptElement | null;
    existing?.remove();
    const script = document.createElement("script");
    const onLoad = () => resolve();
    const onError = () => {
      script.remove();
      reject(new Error("Google Pickerのスクリプトを取得できませんでした。"));
    };
    script.addEventListener("load", onLoad, { once: true });
    script.addEventListener("error", onError, { once: true });
    script.id = PICKER_SCRIPT_ID;
    script.src = PICKER_SCRIPT_URL;
    script.async = true;
    document.head.appendChild(script);
  });
}
