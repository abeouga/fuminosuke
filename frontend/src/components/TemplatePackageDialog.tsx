import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, uploadTemplatePackageInspection } from "../api";
import type {
  Template,
  TemplatePackageDriveItem,
  TemplatePackageOperation,
  TemplateVersion,
} from "../types";

type DialogMode = "export" | "import";
type ErrorCategory = "SECURITY" | "COMPATIBILITY" | "CONFLICT" | "VALIDATION" | "SYSTEM";

type UiError = {
  category: ErrorCategory;
  message: string;
  code: string;
};

export function TemplatePackageDialog({
  mode,
  template,
  version,
  refreshTemplates,
  onClose,
  onReviewImported,
  onActivated,
}: {
  mode: DialogMode;
  template?: Template;
  version?: TemplateVersion;
  refreshTemplates: () => Promise<void>;
  onClose: () => void;
  onReviewImported: (versionId: string) => void;
  onActivated: (versionId: string) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const [operation, setOperation] = useState<TemplatePackageOperation | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [allowOlderVersion, setAllowOlderVersion] = useState(false);
  const [publisherId, setPublisherId] = useState("");
  const [packageId, setPackageId] = useState(
    template ? `template-${template.id.slice(0, 8).toLowerCase()}` : "",
  );
  const [releaseVersion, setReleaseVersion] = useState("1.0.0");
  const [includePreview, setIncludePreview] = useState(true);
  const [includeCandidates, setIncludeCandidates] = useState(false);
  const [includeOutputLayout, setIncludeOutputLayout] = useState(false);
  const [exportPreviewUrl, setExportPreviewUrl] = useState<string | null>(null);
  const [driveItems, setDriveItems] = useState<TemplatePackageDriveItem[]>([]);
  const [driveResult, setDriveResult] = useState<TemplatePackageDriveItem | null>(null);
  const [driveLoading, setDriveLoading] = useState(false);

  const running = busy || operation?.status === "RUNNING";
  const inspection = operation?.importInspection;
  const exportInspection = operation?.exportInspection;
  const importConflict = inspection?.conflictStatus === "VERSION_CONFLICT";
  const requiresOlderConfirmation = inspection?.conflictStatus === "OLDER_RELEASE";

  useEffect(() => {
    openerRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    requestAnimationFrame(() => dialogRef.current?.focus());
    return () => openerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (mode !== "export" || !version?.sourceJobId) return;
    let cancelled = false;
    void api.artifacts(version.sourceJobId).then((response) => {
      if (cancelled) return;
      const preview = ["cleanPreview", "cleanTemplate", "preview", "normalized"]
        .map((name) => response.items.find((item) => item.name === name)?.url)
        .find(Boolean);
      setExportPreviewUrl(preview ?? null);
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [mode, version?.sourceJobId]);

  const progress = operation?.progressPercent ?? uploadProgress;
  const title = mode === "export" ? "テンプレートを配布用に書き出す" : "テンプレートを取り込む";

  const close = () => {
    if (running) return;
    if (operation && operation.status !== "RUNNING") {
      void api.deleteTemplatePackageOperation(operation.operationId).catch(() => undefined);
    }
    onClose();
  };

  const inspectExport = async () => {
    if (!template || !version) return;
    setBusy(true);
    setError(null);
    try {
      const inspected = await api.inspectTemplatePackageExport({
        templateId: template.id,
        templateVersionId: version.id,
        publisherId: publisherId.trim(),
        packageId: packageId.trim(),
        version: releaseVersion.trim(),
        includePreview,
        includeHandwritingCandidates: includeCandidates,
        includeOutputLayout,
      });
      setOperation(inspected);
      void loadDriveItems(false);
    } catch (reason) {
      setError(packageError(reason));
    } finally {
      setBusy(false);
    }
  };

  const loadDriveItems = async (reportError = true) => {
    setDriveLoading(true);
    if (reportError) setError(null);
    try {
      setDriveItems(await api.templatePackageDriveItems());
    } catch (reason) {
      if (reportError) setError(packageError(reason));
    } finally {
      setDriveLoading(false);
    }
  };

  const publishToDrive = async (
    kind: "RELEASE" | "DRAFT",
    draft?: TemplatePackageDriveItem,
  ) => {
    if (!operation?.confirmationToken) return;
    setBusy(true);
    setError(null);
    try {
      const item = kind === "RELEASE"
        ? await api.publishTemplatePackageDriveRelease(operation.confirmationToken)
        : await api.publishTemplatePackageDriveDraft(operation.confirmationToken, {
          draftId: draft?.draftId ?? null,
          expectedRevision: draft?.draftRevision ?? null,
          baseReleaseVersion: releaseVersion.trim() || null,
        });
      setDriveResult(item);
      setOperation((current) => current ? {
        ...current,
        status: "COMPLETED",
        stage: "DRIVE_SYNCED",
        progressPercent: 100,
        message: kind === "RELEASE"
          ? "正式版をGoogle Driveへ同期しました。"
          : "作業版をGoogle Driveへ同期しました。",
        confirmationToken: null,
      } : current);
      await loadDriveItems(false);
    } catch (reason) {
      setError(packageError(reason));
    } finally {
      setBusy(false);
    }
  };

  const inspectDriveItem = async (item: TemplatePackageDriveItem) => {
    setBusy(true);
    setError(null);
    try {
      setOperation(await api.inspectDriveTemplatePackage(item.fileId, item.kind));
      setFile(null);
    } catch (reason) {
      setError(packageError(reason));
    } finally {
      setBusy(false);
    }
  };

  const exportPackage = async () => {
    if (!operation?.confirmationToken) return;
    setBusy(true);
    setError(null);
    setOperation({
      ...operation,
      status: "RUNNING",
      stage: "BUILDING_PACKAGE",
      progressPercent: 82,
      message: "確認済み内容からpackageを生成しています。",
    });
    try {
      const response = await api.confirmTemplatePackageExport(operation.confirmationToken);
      const blob = await response.blob();
      const filename = responseFilename(response) ?? `${packageId}-${releaseVersion}.llmocr-template`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setOperation((current) => current ? {
        ...current,
        status: "COMPLETED",
        stage: "COMPLETED",
        progressPercent: 100,
        message: "配布用template packageを保存しました。",
        confirmationToken: null,
        fileName: filename,
      } : current);
    } catch (reason) {
      setError(packageError(reason));
      setOperation((current) => current ? { ...current, status: "FAILED", stage: "FAILED" } : current);
    } finally {
      setBusy(false);
    }
  };

  const inspectImport = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setUploadProgress(1);
    try {
      const inspected = await uploadTemplatePackageInspection(file, (loaded, total) => {
        setUploadProgress(Math.max(1, Math.min(35, Math.round((loaded / total) * 35))));
      });
      setOperation(inspected);
      setUploadProgress(inspected.progressPercent);
    } catch (reason) {
      setError(packageError(reason));
    } finally {
      setBusy(false);
    }
  };

  const importPackage = async () => {
    if (!operation?.confirmationToken) return;
    setBusy(true);
    setError(null);
    try {
      let current = await api.confirmTemplatePackageImport(
        operation.confirmationToken,
        allowOlderVersion,
      );
      setOperation(current);
      while (current.status === "RUNNING") {
        await delay(250);
        current = await api.templatePackageOperation(current.operationId);
        setOperation(current);
      }
      if (current.status === "FAILED") {
        setError({
          category: current.errorCategory ?? categoryForCode(current.errorCode ?? ""),
          message: current.errorMessage ?? current.message,
          code: current.errorCode ?? "TEMPLATE_PACKAGE_IMPORT_FAILED",
        });
        return;
      }
      await refreshTemplates();
    } catch (reason) {
      setError(packageError(reason));
    } finally {
      setBusy(false);
    }
  };

  const activateImported = async () => {
    const result = operation?.result;
    if (!result) return;
    setBusy(true);
    setError(null);
    try {
      const currentTemplate = await api.template(result.localTemplateId);
      const activated = await api.activateTemplatePackage(
        result.installationId,
        currentTemplate.activeVersionId,
      );
      await refreshTemplates();
      onActivated(activated.activeVersionId);
      onClose();
    } catch (reason) {
      setError(packageError(reason));
    } finally {
      setBusy(false);
    }
  };

  const reviewImported = async () => {
    const versionId = operation?.result?.localTemplateVersionId;
    if (!versionId) return;
    await refreshTemplates();
    onReviewImported(versionId);
    onClose();
  };

  const handleDialogKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && !running) {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      "button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex='0']",
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="template-package-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="template-package-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="template-package-title"
        tabIndex={-1}
        onKeyDown={handleDialogKey}
      >
        <header className="template-package-dialog-header">
          <div><span className="eyebrow">TEMPLATE PACKAGE</span><h2 id="template-package-title">{title}</h2></div>
          <button type="button" className="ghost compact" disabled={running} onClick={close} aria-label="閉じる">×</button>
        </header>

        {progress > 0 && (
          <div className="template-package-progress" role="status" aria-live="polite">
            <div><strong>{operation?.message ?? "packageを受け付けています。"}</strong><span>{progress}%</span></div>
            <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><i style={{ width: `${progress}%` }} /></div>
          </div>
        )}
        {error && <PackageError error={error} />}

        {mode === "export" ? (
          <ExportContent
            template={template}
            version={version}
            operation={operation}
            publisherId={publisherId}
            packageId={packageId}
            releaseVersion={releaseVersion}
            includePreview={includePreview}
            includeCandidates={includeCandidates}
            includeOutputLayout={includeOutputLayout}
            previewUrl={exportPreviewUrl}
            driveItems={driveItems}
            driveResult={driveResult}
            busy={running}
            setPublisherId={setPublisherId}
            setPackageId={setPackageId}
            setReleaseVersion={setReleaseVersion}
            setIncludePreview={setIncludePreview}
            setIncludeCandidates={setIncludeCandidates}
            setIncludeOutputLayout={setIncludeOutputLayout}
            onPublishDraft={(draft) => void publishToDrive("DRAFT", draft)}
          />
        ) : (
          <ImportContent
            file={file}
            operation={operation}
            allowOlderVersion={allowOlderVersion}
            busy={running}
            driveItems={driveItems}
            driveLoading={driveLoading}
            loadDriveItems={() => void loadDriveItems()}
            inspectDriveItem={(item) => void inspectDriveItem(item)}
            setFile={(next) => {
              setFile(next);
              setOperation(null);
              setError(null);
              setUploadProgress(0);
            }}
            setAllowOlderVersion={setAllowOlderVersion}
          />
        )}

        <footer className="template-package-dialog-actions">
          <button type="button" className="ghost" disabled={running} onClick={close}>閉じる</button>
          {mode === "export" && !exportInspection && (
            <button type="button" className="primary" disabled={running || !publisherId.trim() || !packageId.trim() || !releaseVersion.trim()} onClick={() => void inspectExport()}>
              書き出す内容を確認
            </button>
          )}
          {mode === "export" && exportInspection && operation?.status !== "COMPLETED" && (
            <>
              <button type="button" className="secondary" disabled={running || !operation.confirmationToken} onClick={() => void exportPackage()}>
                ファイルに保存
              </button>
              <button type="button" className="secondary" disabled={running || !operation.confirmationToken} onClick={() => void publishToDrive("DRAFT")}>
                新しい作業版をDriveへ同期
              </button>
              <button type="button" className="primary" disabled={running || !operation.confirmationToken} onClick={() => void publishToDrive("RELEASE")}>
                正式版をDriveへ同期
              </button>
            </>
          )}
          {mode === "import" && !inspection && (
            <button type="button" className="primary" disabled={running || !file} onClick={() => void inspectImport()}>
              packageを検証
            </button>
          )}
          {mode === "import" && inspection && !operation?.result && (
            <button
              type="button"
              className="primary"
              disabled={running || importConflict || (requiresOlderConfirmation && !allowOlderVersion) || !operation.confirmationToken}
              onClick={() => void importPackage()}
            >
              有効化せず取り込む
            </button>
          )}
          {mode === "import" && operation?.result && (
            <>
              <button type="button" className="secondary" disabled={running} onClick={() => void reviewImported()}>取り込んだ版を確認</button>
              <button type="button" className="primary" disabled={running} onClick={() => void activateImported()}>有効版にする</button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}

function ExportContent({
  template,
  version,
  operation,
  publisherId,
  packageId,
  releaseVersion,
  includePreview,
  includeCandidates,
  includeOutputLayout,
  previewUrl,
  driveItems,
  driveResult,
  busy,
  setPublisherId,
  setPackageId,
  setReleaseVersion,
  setIncludePreview,
  setIncludeCandidates,
  setIncludeOutputLayout,
  onPublishDraft,
}: {
  template?: Template;
  version?: TemplateVersion;
  operation: TemplatePackageOperation | null;
  publisherId: string;
  packageId: string;
  releaseVersion: string;
  includePreview: boolean;
  includeCandidates: boolean;
  includeOutputLayout: boolean;
  previewUrl: string | null;
  driveItems: TemplatePackageDriveItem[];
  driveResult: TemplatePackageDriveItem | null;
  busy: boolean;
  setPublisherId: (value: string) => void;
  setPackageId: (value: string) => void;
  setReleaseVersion: (value: string) => void;
  setIncludePreview: (value: boolean) => void;
  setIncludeCandidates: (value: boolean) => void;
  setIncludeOutputLayout: (value: boolean) => void;
  onPublishDraft: (draft: TemplatePackageDriveItem) => void;
}) {
  const inspection = operation?.exportInspection;
  if (!inspection) {
    return (
      <div className="template-package-body">
        <div className="template-package-summary-card">
          {previewUrl ? <img src={previewUrl} alt={`${template?.name ?? "テンプレート"}のpreview`} /> : <span className="template-package-preview-placeholder">型</span>}
          <div><strong>{template?.name}</strong><span>local version {version?.versionNumber}</span></div>
        </div>
        <div className="template-package-form-grid">
          <label>配布者ID<input value={publisherId} disabled={busy} placeholder="example.jpではなく jp.example の形式" onChange={(event) => setPublisherId(event.target.value)} /></label>
          <label>Package ID<input value={packageId} disabled={busy} placeholder="interview-card" onChange={(event) => setPackageId(event.target.value)} /></label>
          <label>リリースversion<input value={releaseVersion} disabled={busy} placeholder="1.0.0" onChange={(event) => setReleaseVersion(event.target.value)} /></label>
        </div>
        <fieldset className="template-package-options"><legend>含める任意データ</legend>
          <label><input type="checkbox" checked={includePreview} disabled={busy} onChange={(event) => setIncludePreview(event.target.checked)} /> preview画像</label>
          <label><input type="checkbox" checked={includeCandidates} disabled={busy} onChange={(event) => setIncludeCandidates(event.target.checked)} /> 手書き候補画像</label>
          <label><input type="checkbox" checked={includeOutputLayout} disabled={busy} onChange={(event) => setIncludeOutputLayout(event.target.checked)} /> 現在の利用者の出力レイアウト</label>
        </fieldset>
      </div>
    );
  }
  return (
    <div className="template-package-body">
      <section className="template-package-confirm-section"><h3>書き出し対象</h3>
        <dl className="template-package-facts"><div><dt>テンプレート</dt><dd>{inspection.templateName}</dd></div><div><dt>local version</dt><dd>{inspection.templateVersionNumber}</dd></div><div><dt>content digest</dt><dd className="mono">{inspection.contentDigest}</dd></div></dl>
      </section>
      <CandidateInspection operation={operation} />
      <AssetInspection operation={operation} />
      <section className="template-package-confirm-section"><h3>packageへ含めない情報</h3><ul><li>APIキー・OAuth token</li><li>Google Drive／Spreadsheet IDとowner key</li><li>PC固有path・local DB ID</li><li>OCR済み書類・処理履歴</li></ul></section>
      <DriveDrafts
        items={driveItems.filter((item) => item.kind === "DRAFT" && item.publisherId === publisherId && item.packageId === packageId)}
        busy={busy}
        onPublish={onPublishDraft}
      />
      {driveResult && <div className="success-box" role="status"><strong>{driveResult.kind === "RELEASE" ? "正式版同期完了" : "作業版同期完了"}</strong><span>{driveResult.fileName} · revision {driveResult.draftRevision ?? driveResult.driveVersion}</span></div>}
      {operation.status === "COMPLETED" && operation.stage === "COMPLETED" && <div className="success-box" role="status">{operation.fileName} を保存しました。</div>}
    </div>
  );
}

function ImportContent({ file, operation, allowOlderVersion, busy, driveItems, driveLoading, loadDriveItems, inspectDriveItem, setFile, setAllowOlderVersion }: {
  file: File | null;
  operation: TemplatePackageOperation | null;
  allowOlderVersion: boolean;
  busy: boolean;
  driveItems: TemplatePackageDriveItem[];
  driveLoading: boolean;
  loadDriveItems: () => void;
  inspectDriveItem: (item: TemplatePackageDriveItem) => void;
  setFile: (file: File | null) => void;
  setAllowOlderVersion: (value: boolean) => void;
}) {
  const inspection = operation?.importInspection;
  if (!inspection) {
    return <div className="template-package-body">
      <label className="template-package-file-picker"><span>このPCから取り込むpackage</span><input type="file" accept=".llmocr-template" disabled={busy} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><small>{file ? `${file.name} · ${formatBytes(file.size)}` : ".llmocr-templateを選択してください。"}</small></label>
      <section className="template-package-confirm-section"><h3>Google Driveのtemplate package</h3>
        <button type="button" className="secondary" disabled={busy || driveLoading} onClick={loadDriveItems}>{driveLoading ? "取得中…" : "Driveの一覧を取得"}</button>
        <DriveItems items={driveItems} busy={busy} onInspect={inspectDriveItem} />
      </section>
    </div>;
  }
  const previewRole = inspection.assets.some((item) => item.role === "preview") ? "preview" : "clean-template";
  const conflict = inspection.conflictStatus === "VERSION_CONFLICT";
  return (
    <div className="template-package-body">
      <div className="template-package-summary-card">
        <img src={api.templatePackagePreviewUrl(operation.operationId, previewRole)} alt={`${inspection.name}の検証済みpreview`} />
        <div><strong>{inspection.name}</strong><span>{inspection.publisherId} / {inspection.packageId} / {inspection.version}</span><em>配布者未確認</em></div>
      </div>
      <section className="template-package-confirm-section"><h3>互換性と識別情報</h3><dl className="template-package-facts">
        <div><dt>format / schema</dt><dd>{inspection.formatVersion} / {inspection.schemaVersion}</dd></div>
        <div><dt>minimum app</dt><dd>{inspection.minimumAppVersion}</dd></div>
        <div><dt>content digest</dt><dd className="mono">{inspection.contentDigest}</dd></div>
        <div><dt>required features</dt><dd>{inspection.requiredFeatures.join(", ")}</dd></div>
        <div><dt>競合判定</dt><dd>{conflictLabel(inspection.conflictStatus)}</dd></div>
      </dl></section>
      <section className="template-package-confirm-section"><h3>読み取り規則</h3><p>書類判別: {inspection.documentIdentityFieldCount}項目</p><p>書類名: {inspection.documentNameFieldPaths.length ? inspection.documentNameFieldPaths.join(" + ") : "未設定"}</p><p>読み取り候補: {inspection.candidateSetCount}種類</p></section>
      <AssetInspection operation={operation} />
      {inspection.conflictStatus === "OLDER_RELEASE" && <label className="template-package-risk-confirm"><input type="checkbox" checked={allowOlderVersion} onChange={(event) => setAllowOlderVersion(event.target.checked)} /> 古いversionを非active履歴版として取り込む</label>}
      {conflict && <div className="package-error conflict" role="alert"><strong>競合エラー</strong><span>同一identity/versionに異なる内容が登録済みです。上書きはできません。</span></div>}
      {operation.result && <div className="success-box" role="status"><strong>取り込み完了</strong><span>有効版は変更していません。内容確認または明示的な有効化を選択してください。</span></div>}
    </div>
  );
}

function CandidateInspection({ operation }: { operation: TemplatePackageOperation }) {
  const sets = operation.exportInspection?.candidateSets ?? [];
  if (!sets.length) return <section className="template-package-confirm-section"><h3>読み取り候補</h3><p>packageへ含まれません。</p></section>;
  return <section className="template-package-confirm-section"><h3>読み取り候補</h3>{sets.map((set) => <details key={set.name}><summary>{set.name}（{set.valueCount}件）</summary><p>{set.values.join("、")}</p><small>{set.targetPatterns.join(", ")}</small></details>)}</section>;
}

function AssetInspection({ operation }: { operation: TemplatePackageOperation }) {
  const assets = operation.exportInspection?.assets ?? operation.importInspection?.assets ?? [];
  return <section className="template-package-confirm-section"><h3>asset</h3><ul className="template-package-assets">{assets.map((asset) => <li key={`${asset.role}-${asset.sha256}`}><span><strong>{asset.role}</strong><small>{asset.mediaType}</small></span><span>{formatBytes(asset.size)}{asset.width && asset.height ? ` · ${asset.width}×${asset.height}` : ""}</span></li>)}</ul></section>;
}

function DriveItems({ items, busy, onInspect }: {
  items: TemplatePackageDriveItem[];
  busy: boolean;
  onInspect: (item: TemplatePackageDriveItem) => void;
}) {
  if (!items.length) return <p className="muted">Driveから取得したtemplate packageはありません。</p>;
  return <ul className="template-package-drive-items">{items.map((item) => <li key={item.fileId}>
    <div><strong>{item.publisherId} / {item.packageId}</strong><span>{item.kind === "RELEASE" ? `正式版 ${item.releaseVersion}` : `作業版 revision ${item.draftRevision}`}</span><small>配布者未確認 · {driveSyncLabel(item.syncStatus)}</small></div>
    <button type="button" className="secondary compact" disabled={busy || item.syncStatus === "CONFLICT"} onClick={() => onInspect(item)}>内容を確認</button>
  </li>)}</ul>;
}

function DriveDrafts({ items, busy, onPublish }: {
  items: TemplatePackageDriveItem[];
  busy: boolean;
  onPublish: (item: TemplatePackageDriveItem) => void;
}) {
  if (!items.length) return null;
  return <section className="template-package-confirm-section"><h3>既存のDrive作業版を更新</h3><ul className="template-package-drive-items">{items.map((item) => <li key={item.fileId}>
    <div><strong>revision {item.draftRevision}</strong><span>{item.releaseVersion}</span><small>{driveSyncLabel(item.syncStatus)}</small></div>
    <button type="button" className="secondary compact" disabled={busy || item.syncStatus === "CONFLICT"} onClick={() => onPublish(item)}>この作業版を更新</button>
  </li>)}</ul></section>;
}

function PackageError({ error }: { error: UiError }) {
  const labels: Record<ErrorCategory, string> = {
    SECURITY: "セキュリティ検証エラー",
    COMPATIBILITY: "互換性エラー",
    CONFLICT: "競合エラー",
    VALIDATION: "内容確認エラー",
    SYSTEM: "処理エラー",
  };
  return <div className={`package-error ${error.category.toLowerCase()}`} role="alert"><strong>{labels[error.category]}</strong><span>{error.message}</span><small>{error.code}</small></div>;
}

function packageError(reason: unknown): UiError {
  if (reason instanceof ApiError) {
    return { category: categoryForCode(reason.code), message: reason.message, code: reason.code };
  }
  return { category: "SYSTEM", message: "template package処理に失敗しました。", code: "SYSTEM_ERROR" };
}

function categoryForCode(code: string): ErrorCategory {
  if (["PACKAGE_REQUIRED_FEATURE_UNSUPPORTED", "PACKAGE_APP_VERSION_UNSUPPORTED"].includes(code)) return "COMPATIBILITY";
  if (code.includes("CONFLICT") || code.startsWith("IMPORT_")) return "CONFLICT";
  if (code.startsWith("PACKAGE_") || code.includes("TOKEN") || code.includes("CHANGED")) return "SECURITY";
  if (code.includes("INVALID") || code.includes("NOT_FOUND")) return "VALIDATION";
  return "SYSTEM";
}

function conflictLabel(status: NonNullable<TemplatePackageOperation["importInspection"]>["conflictStatus"]) {
  return {
    NEW_IDENTITY: "新しいテンプレート",
    SAME_RELEASE: "同一releaseを取り込み済み",
    NEWER_RELEASE: "新しいversion",
    OLDER_RELEASE: "古いversion・確認が必要",
    VERSION_CONFLICT: "同一versionの内容競合",
  }[status];
}

function driveSyncLabel(status: TemplatePackageDriveItem["syncStatus"]) {
  return {
    REMOTE_ONLY: "このPCでは未取得",
    SYNCED: "同期済み",
    CONFLICT: "同期競合",
  }[status];
}

function responseFilename(response: Response): string | null {
  const disposition = response.headers.get("Content-Disposition");
  const match = disposition?.match(/filename="([^"]+)"/);
  return match?.[1] ?? null;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
