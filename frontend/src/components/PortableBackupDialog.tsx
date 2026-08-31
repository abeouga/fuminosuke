import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";
import { ApiError, api } from "../api";
import type { PortableBackupExport } from "../types";
import { showToast } from "./Toast";

type BackupPhase =
  | "confirm"
  | "choosing"
  | "creating"
  | "saving"
  | "error"
  | "saved"
  | "download-started";

type BackupWritable = {
  write(data: Uint8Array): Promise<void>;
  close(): Promise<void>;
  abort?(reason?: unknown): Promise<void>;
};

type BackupFileHandle = {
  name: string;
  createWritable(): Promise<BackupWritable>;
};

type SaveFilePicker = (options: {
  id: string;
  suggestedName: string;
  startIn: "downloads";
  excludeAcceptAllOption: boolean;
  types: Array<{
    description: string;
    accept: Record<string, string[]>;
  }>;
}) => Promise<BackupFileHandle>;

type WindowWithSavePicker = Window & {
  showSaveFilePicker?: SaveFilePicker;
};

export function PortableBackupDialog({
  onClose,
  returnFocusRef,
}: {
  onClose: () => void;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const [phase, setPhase] = useState<BackupPhase>("confirm");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [backup, setBackup] = useState<PortableBackupExport | null>(null);
  const [destination, setDestination] = useState<BackupFileHandle | null>(null);
  const [savedName, setSavedName] = useState<string | null>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const busy = phase === "choosing" || phase === "creating" || phase === "saving";
  const savePicker = getSavePicker();

  useEffect(() => {
    cancelRef.current?.focus();
    return () => returnFocusRef.current?.focus();
  }, [returnFocusRef]);

  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) closeDialog();
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  });

  const closeDialog = () => {
    if (busy) return;
    if (backup && phase !== "saved" && phase !== "download-started") {
      void api.deletePortableBackup(backup.id).catch(() => undefined);
    }
    onClose();
  };

  const chooseAndCreate = async () => {
    setError(null);
    if (!savePicker) {
      await createBrowserDownload();
      return;
    }
    setPhase("choosing");
    try {
      const handle = await savePicker({
        id: "ledger-ocr-portable-backup",
        suggestedName: suggestedBackupName(),
        startIn: "downloads",
        excludeAcceptAllOption: true,
        types: [{
          description: "ZIPバックアップ",
          accept: { "application/zip": [".zip"] },
        }],
      });
      setDestination(handle);
      await createAndSave(handle);
    } catch (reason) {
      if (isAbortError(reason)) {
        setPhase("confirm");
        return;
      }
      fail(reason, "保存先を選択できませんでした。");
    }
  };

  const createAndSave = async (handle: BackupFileHandle) => {
    setError(null);
    setPhase("creating");
    setProgress(0);
    try {
      const created = backup ?? await api.createPortableBackup();
      setBackup(created);
      await saveBackup(created, handle);
    } catch (reason) {
      fail(reason, "持ち出しバックアップを作成できませんでした。");
    }
  };

  const saveBackup = async (created: PortableBackupExport, handle: BackupFileHandle) => {
    setPhase("saving");
    const writable = await handle.createWritable();
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    let failure: unknown = new Error("バックアップの保存を完了できませんでした。");
    let completed = false;
    try {
      const response = await api.portableBackupDownload(created.id);
      reader = response.body?.getReader() ?? null;
      if (!reader) {
        throw new Error("バックアップの保存用ストリームを開始できませんでした。");
      }
      const total = numberHeader(response.headers.get("Content-Length")) ?? created.fileBytes;
      let written = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        await writable.write(value);
        written += value.byteLength;
        setProgress(total > 0 ? Math.min(100, Math.round((written / total) * 100)) : 0);
      }
      await writable.close();
      completed = true;
    } catch (reason) {
      failure = reason;
      throw reason;
    } finally {
      if (!completed) {
        await reader?.cancel(failure).catch(() => undefined);
        if (writable.abort) await writable.abort(failure).catch(() => undefined);
      }
    }
    setProgress(100);
    setSavedName(handle.name);
    setPhase("saved");
    void api.deletePortableBackup(created.id).catch(() => undefined);
    showToast({
      tone: "success",
      title: "持ち出しバックアップを保存しました",
      message: `${handle.name} · ${formatBytes(created.fileBytes)}`,
    });
  };

  const createBrowserDownload = async () => {
    setPhase("creating");
    setError(null);
    try {
      const created = await api.createPortableBackup();
      setBackup(created);
      startBrowserDownload(created);
      setPhase("download-started");
    } catch (reason) {
      fail(reason, "持ち出しバックアップを作成できませんでした。");
    }
  };

  const fail = (reason: unknown, fallback: string) => {
    const message = reason instanceof ApiError || reason instanceof Error
      ? reason.message
      : fallback;
    setError(message || fallback);
    setPhase("error");
    showToast({ tone: "error", title: "バックアップ処理に失敗しました", message: message || fallback });
  };

  const retry = () => {
    if (backup && destination) {
      void saveBackup(backup, destination).catch((reason: unknown) => {
        fail(reason, "バックアップを保存できませんでした。");
      });
      return;
    }
    if (destination) {
      void createAndSave(destination);
      return;
    }
    setPhase("confirm");
    setError(null);
  };

  return (
    <div
      className="navigation-confirm-backdrop portable-backup-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeDialog();
      }}
    >
      <section
        ref={dialogRef}
        className="navigation-confirm-dialog portable-backup-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="portable-backup-title"
        aria-describedby="portable-backup-description"
        aria-busy={busy}
        onKeyDown={trapFocus}
      >
        <span className="navigation-confirm-icon backup" aria-hidden="true">⇩</span>
        <div className="portable-backup-copy">
          <p className="eyebrow">PORTABLE BACKUP</p>
          <h2 id="portable-backup-title">持ち出しバックアップ</h2>
          <BackupBody
            phase={phase}
            progress={progress}
            error={error}
            backup={backup}
            savedName={savedName}
          />
        </div>
        <div className="navigation-confirm-actions portable-backup-actions">
          {phase === "confirm" && (
            <>
              <button ref={cancelRef} type="button" className="secondary" onClick={closeDialog}>
                キャンセル
              </button>
              <button type="button" className="primary" onClick={() => void chooseAndCreate()}>
                {savePicker ? "保存先を選んで作成" : "バックアップを作成"}
              </button>
            </>
          )}
          {busy && <button type="button" className="secondary" disabled>処理中です</button>}
          {phase === "error" && (
            <>
              <button type="button" className="secondary" onClick={closeDialog}>閉じる</button>
              {backup && savePicker ? (
                <button type="button" className="primary" onClick={() => void chooseAndCreate()}>
                  保存先を選び直す
                </button>
              ) : (
                <button type="button" className="primary" onClick={retry}>再実行</button>
              )}
            </>
          )}
          {phase === "saved" && (
            <button type="button" className="primary" onClick={closeDialog}>完了</button>
          )}
          {phase === "download-started" && backup && (
            <>
              <button type="button" className="secondary" onClick={closeDialog}>閉じる</button>
              <button type="button" className="primary" onClick={() => startBrowserDownload(backup)}>
                もう一度ダウンロード
              </button>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function BackupBody({
  phase,
  progress,
  error,
  backup,
  savedName,
}: {
  phase: BackupPhase;
  progress: number;
  error: string | null;
  backup: PortableBackupExport | null;
  savedName: string | null;
}) {
  if (phase === "confirm" || phase === "choosing") {
    return (
      <div id="portable-backup-description" className="portable-backup-description">
        <p>保存済みのDB、帳票画像、テンプレート、出力ファイルを一つのZIPにまとめます。</p>
        <ul>
          <li>APIキー、ローカル設定、ログ、一時ファイルは含みません。</li>
          <li>Google更新トークンは無効化され、別PCではGoogle再接続が必要です。</li>
          <li>個人・業務データを含む非暗号化ZIPです。暗号化済みSSDへ保存してください。</li>
          <li>別PCではZIPをローカルディスクへコピーしてから復元します。SSDから直接起動しません。</li>
        </ul>
        {phase === "choosing" && <p role="status">保存先の選択を待っています。</p>}
      </div>
    );
  }
  if (phase === "creating") {
    return (
      <div id="portable-backup-description" className="portable-backup-progress" role="status">
        <strong>整合性を確認しながらZIPを作成しています</strong>
        <p>データ量により数分かかります。この間は保存や読み取りを開始できません。</p>
        <span className="portable-backup-indeterminate" aria-hidden="true"><i /></span>
      </div>
    );
  }
  if (phase === "saving") {
    return (
      <div id="portable-backup-description" className="portable-backup-progress">
        <strong>選択した保存先へ書き込んでいます</strong>
        <p>{backup ? `${formatBytes(backup.fileBytes)} · ${backup.fileCount.toLocaleString("ja-JP")}ファイル` : "保存準備中"}</p>
        <div
          className="portable-backup-progressbar"
          role="progressbar"
          aria-label="バックアップ保存進捗"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
        >
          <i style={{ width: `${progress}%` }} />
        </div>
        <span>{progress}%</span>
      </div>
    );
  }
  if (phase === "error") {
    return (
      <div id="portable-backup-description" className="portable-backup-result error" role="alert">
        <strong>バックアップを完了できませんでした</strong>
        <p>{error}</p>
        <small>読み取りや編集が処理中の場合は、完了またはキャンセル後に再実行してください。</small>
      </div>
    );
  }
  if (phase === "saved") {
    return (
      <div id="portable-backup-description" className="portable-backup-result success" role="status">
        <strong>保存が完了しました</strong>
        <p>{savedName}{backup ? ` · ${formatBytes(backup.fileBytes)}` : ""}</p>
        <small>別PCでは外付け媒体からローカルディスクへコピーして復元してください。</small>
      </div>
    );
  }
  return (
    <div id="portable-backup-description" className="portable-backup-result success" role="status">
      <strong>ブラウザのダウンロードを開始しました</strong>
      <p>{backup?.fileName} · {backup ? formatBytes(backup.fileBytes) : ""}</p>
      <small>ブラウザで保存先を確認してください。保存完了はこの画面では判定できません。</small>
    </div>
  );
}

function getSavePicker(): SaveFilePicker | null {
  const candidate = (window as WindowWithSavePicker).showSaveFilePicker;
  return window.isSecureContext && typeof candidate === "function"
    ? candidate.bind(window)
    : null;
}

function startBrowserDownload(backup: PortableBackupExport) {
  const link = document.createElement("a");
  link.href = backup.downloadUrl;
  link.download = backup.fileName;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function suggestedBackupName(): string {
  const now = new Date();
  const date = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((value) => String(value).padStart(2, "0"))
    .join("");
  const time = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((value) => String(value).padStart(2, "0"))
    .join("");
  return `llm-ocr-portable-backup-${date}-${time}.zip`;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} bytes`;
}

function numberHeader(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

function trapFocus(event: ReactKeyboardEvent<HTMLElement>) {
  if (event.key !== "Tab") return;
  const elements = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
    ),
  );
  if (elements.length === 0) return;
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
