import type { DrivePickerSession } from "./types";

const PICKER_SCRIPT_ID = "google-picker-api";
const PICKER_SCRIPT_URL = "https://apis.google.com/js/api.js";
const FOLDER_MIME_TYPE = "application/vnd.google-apps.folder";

type PickerData = Record<string, unknown>;

type PickerInstance = {
  setVisible: (visible: boolean) => void;
};

type DocsViewInstance = {
  setIncludeFolders: (enabled: boolean) => DocsViewInstance;
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
  Document: { ID: string; NAME: string };
  DocsView: new (viewId: unknown) => DocsViewInstance;
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
