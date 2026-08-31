import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";
import { ApiError, api, uploadPortableRestore } from "../api";
import type {
  PortableBackupExport,
  PortableBackupOperation,
  PortableRestoreOperation,
} from "../types";
import { showToast } from "./Toast";

type BackupMode = "export" | "import";
type ExportPhase =
  | "confirm"
  | "choosing"
  | "creating"
  | "saving"
  | "error"
  | "saved"
  | "download-started";
type ImportPhase = "select" | "uploading" | "ready" | "applying" | "error" | "completed";

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
  const [mode, setMode] = useState<BackupMode>("export");
  const [exportPhase, setExportPhase] = useState<ExportPhase>("confirm");
  const [exportProgress, setExportProgress] = useState(0);
  const [exportStage, setExportStage] = useState("PREPARING");
  const [exportMessage, setExportMessage] = useState("保存内容と保存先を確認してください。");
  const [exportError, setExportError] = useState<string | null>(null);
  const [backup, setBackup] = useState<PortableBackupExport | null>(null);
  const [destination, setDestination] = useState<BackupFileHandle | null>(null);
  const [savedName, setSavedName] = useState<string | null>(null);
  const [importPhase, setImportPhase] = useState<ImportPhase>("select");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importOperation, setImportOperation] = useState<PortableRestoreOperation | null>(null);
  const [importProgress, setImportProgress] = useState(0);
  const [importError, setImportError] = useState<string | null>(null);
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const importOperationIdRef = useRef<string | null>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const exportBusy = ["choosing", "creating", "saving"].includes(exportPhase);
  const importBusy = importPhase === "uploading" || importPhase === "applying";
  const busy = exportBusy || importBusy;
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

  const cleanupPending = () => {
    if (backup && exportPhase !== "saved" && exportPhase !== "download-started") {
      void api.deletePortableBackup(backup.id).catch(() => undefined);
    }
    const importId = importOperationIdRef.current;
    if (importId && importPhase !== "completed") {
      void api.deletePortableRestore(importId).catch(() => undefined);
      importOperationIdRef.current = null;
    }
  };

  const closeDialog = () => {
    if (busy) return;
    cleanupPending();
    onClose();
  };

  const changeMode = (nextMode: BackupMode) => {
    if (busy || nextMode === mode) return;
    cleanupPending();
    setMode(nextMode);
    setExportPhase("confirm");
    setExportProgress(0);
    setExportStage("PREPARING");
    setExportMessage("保存内容と保存先を確認してください。");
    setExportError(null);
    setBackup(null);
    setDestination(null);
    setSavedName(null);
    setImportPhase("select");
    setImportFile(null);
    setImportOperation(null);
    setImportProgress(0);
    setImportError(null);
    setReplaceConfirmed(false);
  };

  const chooseAndCreate = async () => {
    setExportError(null);
    if (!savePicker) {
      await createBrowserDownload();
      return;
    }
    setExportPhase("choosing");
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
        setExportPhase("confirm");
        return;
      }
      failExport(reason, "保存先を選択できませんでした。");
    }
  };

  const createBackup = async () => {
    const operationId = createOperationId();
    let stopped = false;
    setExportPhase("creating");
    setExportProgress(1);
    setExportStage("PREPARING");
    setExportMessage("保存データを確認しています。");
    const tracker = trackBackupOperation(
      operationId,
      () => stopped,
      (operation) => {
        setExportStage(operation.stage);
        setExportMessage(operation.message);
        setExportProgress(Math.min(72, Math.round(operation.progressPercent * 0.72)));
      },
    );
    try {
      const created = await api.createPortableBackup(operationId);
      stopped = true;
      await tracker;
      setBackup(created);
      setExportProgress(72);
      return created;
    } catch (reason) {
      stopped = true;
      await tracker;
      throw reason;
    }
  };

  const createAndSave = async (handle: BackupFileHandle) => {
    setExportError(null);
    try {
      const created = backup ?? await createBackup();
      await saveBackup(created, handle);
    } catch (reason) {
      failExport(reason, "持ち出しバックアップを作成できませんでした。");
    }
  };

  const saveBackup = async (created: PortableBackupExport, handle: BackupFileHandle) => {
    setExportPhase("saving");
    setExportStage("SAVING_DESTINATION");
    setExportMessage("選択した保存先へZIPを書き込んでいます。");
    const writable = await handle.createWritable();
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    let failure: unknown = new Error("バックアップの保存を完了できませんでした。");
    let completed = false;
    try {
      const response = await api.portableBackupDownload(created.id);
      reader = response.body?.getReader() ?? null;
      if (!reader) throw new Error("バックアップの保存用ストリームを開始できませんでした。");
      const total = numberHeader(response.headers.get("Content-Length")) ?? created.fileBytes;
      let written = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        await writable.write(value);
        written += value.byteLength;
        const ratio = total > 0 ? Math.min(1, written / total) : 0;
        setExportProgress(72 + Math.round(ratio * 28));
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
    setExportProgress(100);
    setExportStage("COMPLETED");
    setExportMessage("指定した保存先への書き込みが完了しました。");
    setSavedName(handle.name);
    setExportPhase("saved");
    void api.deletePortableBackup(created.id).catch(() => undefined);
    showToast({
      tone: "success",
      title: "持ち出しバックアップを保存しました",
      message: `${handle.name} · ${formatBytes(created.fileBytes)}`,
    });
  };

  const createBrowserDownload = async () => {
    setExportError(null);
    try {
      const created = await createBackup();
      startBrowserDownload(created);
      setExportProgress(100);
      setExportStage("COMPLETED");
      setExportPhase("download-started");
    } catch (reason) {
      failExport(reason, "持ち出しバックアップを作成できませんでした。");
    }
  };

  const failExport = (reason: unknown, fallback: string) => {
    const message = errorMessage(reason, fallback);
    setExportError(message);
    setExportPhase("error");
    showToast({ tone: "error", title: "バックアップ処理に失敗しました", message });
  };

  const retryExport = () => {
    if (backup && destination) {
      void saveBackup(backup, destination).catch((reason: unknown) => {
        failExport(reason, "バックアップを保存できませんでした。");
      });
      return;
    }
    if (destination) {
      void createAndSave(destination);
      return;
    }
    setExportPhase("confirm");
    setExportError(null);
  };

  const selectImportFile = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    setImportError(null);
    setImportOperation(null);
    setReplaceConfirmed(false);
    if (selected && !selected.name.toLowerCase().endsWith(".zip")) {
      setImportFile(null);
      setImportError("持ち出しバックアップのZIPファイルを選択してください。");
      return;
    }
    setImportFile(selected);
  };

  const validateImport = async () => {
    if (!importFile) return;
    const operationId = createOperationId();
    importOperationIdRef.current = operationId;
    setImportPhase("uploading");
    setImportError(null);
    setImportProgress(1);
    setReplaceConfirmed(false);
    let stopped = false;
    const tracker = trackRestoreOperation(
      operationId,
      () => stopped,
      (operation) => {
        setImportOperation(operation);
        setImportProgress(operation.progressPercent);
      },
    );
    try {
      const validated = await uploadPortableRestore(operationId, importFile, (loaded, total) => {
        const ratio = total > 0 ? loaded / total : 0;
        setImportProgress(Math.max(1, Math.min(15, Math.round(ratio * 15))));
      });
      stopped = true;
      await tracker;
      setImportOperation(validated);
      setImportProgress(validated.progressPercent);
      setImportPhase("ready");
    } catch (reason) {
      stopped = true;
      await tracker;
      failImport(reason, "バックアップZIPを検証できませんでした。");
    }
  };

  const applyImport = async () => {
    const operationId = importOperationIdRef.current;
    if (!operationId || !replaceConfirmed) return;
    setImportPhase("applying");
    setImportError(null);
    setImportProgress(78);
    setImportOperation((current) => current ? {
      ...current,
      status: "APPLYING",
      stage: "STOPPING_SERVICES",
      progressPercent: 78,
      message: "保存処理を停止し、検証済みデータへ切り替えています。",
    } : current);
    try {
      const completed = await api.applyPortableRestore(operationId);
      setImportOperation(completed);
      setImportProgress(100);
      setImportPhase("completed");
      showToast({
        tone: "success",
        title: "持ち出しバックアップを取り込みました",
        message: "取り込んだ保存データでシステムを再初期化しました。",
      });
    } catch (reason) {
      failImport(reason, "バックアップの取り込みを完了できませんでした。");
    }
  };

  const failImport = (reason: unknown, fallback: string) => {
    const message = errorMessage(reason, fallback);
    setImportError(message);
    setImportPhase("error");
    showToast({ tone: "error", title: "バックアップ取り込みに失敗しました", message });
  };

  const resetImport = () => {
    const operationId = importOperationIdRef.current;
    if (operationId) void api.deletePortableRestore(operationId).catch(() => undefined);
    importOperationIdRef.current = null;
    setImportPhase("select");
    setImportFile(null);
    setImportOperation(null);
    setImportProgress(0);
    setImportError(null);
    setReplaceConfirmed(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
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
        <span className="navigation-confirm-icon backup" aria-hidden="true">⇄</span>
        <div className="portable-backup-copy">
          <p className="eyebrow">PORTABLE BACKUP</p>
          <h2 id="portable-backup-title">持ち出しバックアップ</h2>
          <div className="segmented portable-backup-mode" role="tablist" aria-label="バックアップ操作">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "export"}
              className={mode === "export" ? "active" : ""}
              disabled={busy}
              onClick={() => changeMode("export")}
            >
              書き出し
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "import"}
              className={mode === "import" ? "active" : ""}
              disabled={busy}
              onClick={() => changeMode("import")}
            >
              取り込み
            </button>
          </div>
          {mode === "export" ? (
            <ExportBody
              phase={exportPhase}
              progress={exportProgress}
              stage={exportStage}
              message={exportMessage}
              error={exportError}
              backup={backup}
              savedName={savedName}
            />
          ) : (
            <ImportBody
              phase={importPhase}
              file={importFile}
              operation={importOperation}
              progress={importProgress}
              error={importError}
              confirmed={replaceConfirmed}
              fileInputRef={fileInputRef}
              onFileChange={selectImportFile}
              onChoose={() => fileInputRef.current?.click()}
              onConfirm={setReplaceConfirmed}
            />
          )}
        </div>
        <div className="navigation-confirm-actions portable-backup-actions">
          {mode === "export" ? (
            <ExportActions
              phase={exportPhase}
              hasBackup={Boolean(backup)}
              hasSavePicker={Boolean(savePicker)}
              cancelRef={cancelRef}
              onClose={closeDialog}
              onCreate={() => void chooseAndCreate()}
              onRetry={retryExport}
              onReselect={() => void chooseAndCreate()}
              onDownloadAgain={() => backup && startBrowserDownload(backup)}
            />
          ) : (
            <ImportActions
              phase={importPhase}
              hasFile={Boolean(importFile)}
              confirmed={replaceConfirmed}
              cancelRef={cancelRef}
              onClose={closeDialog}
              onValidate={() => void validateImport()}
              onApply={() => void applyImport()}
              onReset={resetImport}
            />
          )}
        </div>
      </section>
    </div>
  );
}

function ExportBody({
  phase,
  progress,
  stage,
  message,
  error,
  backup,
  savedName,
}: {
  phase: ExportPhase;
  progress: number;
  stage: string;
  message: string;
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
          <li>別PCではZIPをローカルディスクへコピーしてから取り込みます。SSDから直接起動しません。</li>
        </ul>
        {phase === "choosing" && <p role="status">保存先の選択を待っています。</p>}
      </div>
    );
  }
  if (["creating", "saving"].includes(phase)) {
    return (
      <ProgressPanel
        kind="export"
        stage={stage}
        progress={progress}
        message={message}
        detail={backup ? `${formatBytes(backup.fileBytes)} · ${backup.fileCount.toLocaleString("ja-JP")}ファイル` : null}
      />
    );
  }
  if (phase === "error") {
    return <ResultPanel tone="error" title="バックアップを完了できませんでした" message={error ?? "処理に失敗しました。"} />;
  }
  if (phase === "saved") {
    return (
      <ResultPanel
        tone="success"
        title="保存が完了しました"
        message={`${savedName ?? "保存先"}${backup ? ` · ${formatBytes(backup.fileBytes)}` : ""}`}
        note="このZIPを別PCのローカルディスクへコピーし、取り込み画面で選択してください。"
      />
    );
  }
  return (
    <ResultPanel
      tone="success"
      title="ブラウザのダウンロードを開始しました"
      message={`${backup?.fileName ?? "バックアップZIP"}${backup ? ` · ${formatBytes(backup.fileBytes)}` : ""}`}
      note="ブラウザ経由では保存中の進捗と完了を判定できません。ダウンロード一覧を確認してください。"
    />
  );
}

function ImportBody({
  phase,
  file,
  operation,
  progress,
  error,
  confirmed,
  fileInputRef,
  onFileChange,
  onChoose,
  onConfirm,
}: {
  phase: ImportPhase;
  file: File | null;
  operation: PortableRestoreOperation | null;
  progress: number;
  error: string | null;
  confirmed: boolean;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onChoose: () => void;
  onConfirm: (checked: boolean) => void;
}) {
  if (phase === "select") {
    return (
      <div id="portable-backup-description" className="portable-backup-description portable-import-select">
        <p>別PCで書き出した持ち出しバックアップZIPを検証し、このPCへ取り込みます。</p>
        <div className="portable-import-warning">
          <strong>取り込みを実行すると、現在のDB・帳票画像・テンプレート・出力ファイルを置換します。</strong>
          <span>APIキーとローカル設定は置換しません。Google連携は取り込み後に再接続が必要です。</span>
        </div>
        <input
          ref={fileInputRef}
          className="portable-import-file-input"
          type="file"
          aria-label="バックアップZIPファイル"
          accept=".zip,application/zip"
          onChange={onFileChange}
        />
        <button type="button" className="portable-import-file" onClick={onChoose}>
          <span aria-hidden="true">ZIP</span>
          <strong>{file?.name ?? "バックアップZIPを選択"}</strong>
          <small>{file ? formatBytes(file.size) : "外付け媒体からローカルディスクへコピーしたZIPを選択"}</small>
        </button>
        {error && <p className="portable-import-inline-error" role="alert">{error}</p>}
      </div>
    );
  }
  if (phase === "uploading" || phase === "applying") {
    return (
      <ProgressPanel
        kind="import"
        stage={operation?.stage ?? "RECEIVING_FILE"}
        progress={progress}
        message={operation?.message ?? "選択したZIPをシステム領域へ受け付けています。"}
        detail={operation?.totalBytes ? `${formatBytes(operation.bytesProcessed)} / ${formatBytes(operation.totalBytes)}` : file ? formatBytes(file.size) : null}
      />
    );
  }
  if (phase === "ready" && operation) {
    return (
      <div id="portable-backup-description" className="portable-import-ready">
        <ResultPanel
          tone="success"
          title="ZIPの検証が完了しました"
          message="内容を確認してから取り込みを実行してください。"
        />
        <dl className="portable-import-summary">
          <div><dt>バックアップ日時</dt><dd>{formatDate(operation.backupCreatedAt)}</dd></div>
          <div><dt>保存データ</dt><dd>{formatBytes(operation.sourceBytes ?? 0)}</dd></div>
          <div><dt>ファイル数</dt><dd>{(operation.fileCount ?? 0).toLocaleString("ja-JP")}</dd></div>
          <div><dt>システム版</dt><dd>{operation.schemaRevision ?? "不明"}</dd></div>
        </dl>
        <label className="portable-import-confirm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => onConfirm(event.target.checked)}
          />
          <span>現在の保存データが置換されることを確認しました</span>
        </label>
      </div>
    );
  }
  if (phase === "error") {
    return (
      <ResultPanel
        tone="error"
        title="バックアップを取り込めませんでした"
        message={error ?? operation?.errorMessage ?? "処理に失敗しました。"}
        note="検証または適用に失敗した場合、現在の保存データは維持されます。"
      />
    );
  }
  return (
    <ResultPanel
      tone="success"
      title="取り込みが完了しました"
      message="DB・帳票画像・テンプレート・出力ファイルを取り込んだ内容へ切り替えました。"
      note="Google連携を利用する場合は、再読み込み後に接続し直してください。"
    />
  );
}

function ProgressPanel({
  kind,
  stage,
  progress,
  message,
  detail,
}: {
  kind: BackupMode;
  stage: string;
  progress: number;
  message: string;
  detail: string | null;
}) {
  const steps = kind === "export"
    ? ["準備", "DB複製", "ZIP作成", "保存先へ書込"]
    : ["ZIP受信", "ZIP検証", "DB検証", "データ切替", "再起動"];
  const active = stageIndex(kind, stage);
  return (
    <div id="portable-backup-description" className="portable-backup-progress" role="status" aria-live="polite">
      <strong>{message}</strong>
      {detail && <p>{detail}</p>}
      <ol className="portable-backup-steps" aria-label={kind === "export" ? "書き出し工程" : "取り込み工程"}>
        {steps.map((step, index) => (
          <li
            key={step}
            className={progress >= 100 || index < active ? "done" : index === active ? "active" : ""}
            aria-current={index === active && progress < 100 ? "step" : undefined}
          >
            <i aria-hidden="true">{progress >= 100 || index < active ? "✓" : index + 1}</i>
            <span>{step}</span>
          </li>
        ))}
      </ol>
      <div
        className="portable-backup-progressbar"
        role="progressbar"
        aria-label={kind === "export" ? "バックアップ書き出し進捗" : "バックアップ取り込み進捗"}
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

function ResultPanel({
  tone,
  title,
  message,
  note,
}: {
  tone: "success" | "error";
  title: string;
  message: string;
  note?: string;
}) {
  return (
    <div
      id="portable-backup-description"
      className={`portable-backup-result ${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      <strong>{title}</strong>
      <p>{message}</p>
      {note && <small>{note}</small>}
    </div>
  );
}

function ExportActions({
  phase,
  hasBackup,
  hasSavePicker,
  cancelRef,
  onClose,
  onCreate,
  onRetry,
  onReselect,
  onDownloadAgain,
}: {
  phase: ExportPhase;
  hasBackup: boolean;
  hasSavePicker: boolean;
  cancelRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onCreate: () => void;
  onRetry: () => void;
  onReselect: () => void;
  onDownloadAgain: () => void;
}) {
  if (phase === "confirm") {
    return (
      <>
        <button ref={cancelRef} type="button" className="secondary" onClick={onClose}>キャンセル</button>
        <button type="button" className="primary" onClick={onCreate}>
          {hasSavePicker ? "保存先を選んで作成" : "バックアップを作成"}
        </button>
      </>
    );
  }
  if (["choosing", "creating", "saving"].includes(phase)) {
    return <button type="button" className="secondary" disabled>処理中です</button>;
  }
  if (phase === "error") {
    return (
      <>
        <button type="button" className="secondary" onClick={onClose}>閉じる</button>
        <button type="button" className="primary" onClick={hasBackup && hasSavePicker ? onReselect : onRetry}>
          {hasBackup && hasSavePicker ? "保存先を選び直す" : "再実行"}
        </button>
      </>
    );
  }
  if (phase === "saved") {
    return <button type="button" className="primary" onClick={onClose}>完了</button>;
  }
  return (
    <>
      <button type="button" className="secondary" onClick={onClose}>閉じる</button>
      <button type="button" className="primary" onClick={onDownloadAgain}>もう一度ダウンロード</button>
    </>
  );
}

function ImportActions({
  phase,
  hasFile,
  confirmed,
  cancelRef,
  onClose,
  onValidate,
  onApply,
  onReset,
}: {
  phase: ImportPhase;
  hasFile: boolean;
  confirmed: boolean;
  cancelRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onValidate: () => void;
  onApply: () => void;
  onReset: () => void;
}) {
  if (phase === "select") {
    return (
      <>
        <button ref={cancelRef} type="button" className="secondary" onClick={onClose}>キャンセル</button>
        <button type="button" className="primary" disabled={!hasFile} onClick={onValidate}>ZIPを検証</button>
      </>
    );
  }
  if (phase === "uploading" || phase === "applying") {
    return <button type="button" className="secondary" disabled>処理中です</button>;
  }
  if (phase === "ready") {
    return (
      <>
        <button type="button" className="secondary" onClick={onReset}>別のZIPを選ぶ</button>
        <button type="button" className="danger-button" disabled={!confirmed} onClick={onApply}>現在データを置換して取り込む</button>
      </>
    );
  }
  if (phase === "error") {
    return (
      <>
        <button type="button" className="secondary" onClick={onClose}>閉じる</button>
        <button type="button" className="primary" onClick={onReset}>ZIPを選び直す</button>
      </>
    );
  }
  return <button type="button" className="primary" onClick={() => window.location.reload()}>取り込んだデータで再読み込み</button>;
}

async function trackBackupOperation(
  operationId: string,
  stopped: () => boolean,
  update: (operation: PortableBackupOperation) => void,
) {
  while (!stopped()) {
    try {
      const operation = await api.portableBackupOperation(operationId);
      update(operation);
      if (operation.status !== "RUNNING") return;
    } catch {
      // The first poll can arrive before the POST route creates its progress record.
    }
    if (!stopped()) await delay(180);
  }
}

async function trackRestoreOperation(
  operationId: string,
  stopped: () => boolean,
  update: (operation: PortableRestoreOperation) => void,
) {
  while (!stopped()) {
    try {
      const operation = await api.portableRestoreOperation(operationId);
      update(operation);
      if (["READY", "COMPLETED", "FAILED"].includes(operation.status)) return;
    } catch {
      // Upload registration and the first status poll can cross by a few milliseconds.
    }
    if (!stopped()) await delay(180);
  }
}

function stageIndex(kind: BackupMode, stage: string): number {
  if (kind === "export") {
    if (stage === "SAVING_DESTINATION" || stage === "COMPLETED") return 3;
    if (["WRITING_ARCHIVE", "VERIFYING_ARCHIVE", "READY"].includes(stage)) return 2;
    if (stage === "SNAPSHOTTING_DATABASE") return 1;
    return 0;
  }
  if (["RESTARTING_SERVICES", "COMPLETED"].includes(stage)) return 4;
  if (["CHECKPOINTING_DATABASE", "SWAPPING_DATA"].includes(stage)) return 3;
  if (["VALIDATING_DATABASE", "READY_TO_APPLY", "STOPPING_SERVICES"].includes(stage)) return 2;
  if (["VALIDATING_STRUCTURE", "VERIFYING_FILES"].includes(stage)) return 1;
  return 0;
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

function createOperationId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  const random = () => Math.floor(Math.random() * 16).toString(16);
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const value = Number.parseInt(random(), 16);
    return (token === "x" ? value : (value & 0x3) | 0x8).toString(16);
  });
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

function formatDate(value: string | null): string {
  if (!value) return "不明";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "不明";
  return new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function numberHeader(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof ApiError || reason instanceof Error
    ? reason.message || fallback
    : fallback;
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));
}

function trapFocus(event: ReactKeyboardEvent<HTMLElement>) {
  if (event.key !== "Tab") return;
  const elements = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
    ),
  ).filter((element) => !element.hasAttribute("hidden") && element.offsetParent !== null);
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
