import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { ApiError, api, googleLoginUrl } from "../api";
import { showToast } from "../components/Toast";
import { mountGoogleDriveSpreadsheetPicker, type PickedDriveSpreadsheet } from "../googlePicker";
import type { DocumentLibrary, DocumentLibraryFolder, DocumentLibraryGroup, DocumentLibraryLedger, DocumentLibrarySyncResult, DriveOutputFolder, DrivePickerSession, OcrLibraryContext } from "../types";

type Props = {
  startReading: (context: OcrLibraryContext) => void;
  openReview: (jobId: string) => void;
  initialDocumentId?: string | null;
};

export function DocumentLibraryPage({ startReading, openReview, initialDocumentId = null }: Props) {
  const [libraryView, setLibraryView] = useState<"library" | "drive">("library");
  const [library, setLibrary] = useState<DocumentLibrary | null>(null);
  const [selectedLedger, setSelectedLedger] = useState<DocumentLibraryLedger | null>(null);
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<string>>(new Set());
  const [librarySyncing, setLibrarySyncing] = useState(true);
  const [librarySyncOperation, setLibrarySyncOperation] = useState<"bidirectional" | "push" | null>("bidirectional");
  const [libraryReconnecting, setLibraryReconnecting] = useState(false);
  const [librarySyncResult, setLibrarySyncResult] = useState<DocumentLibrarySyncResult | null>(null);
  const [librarySyncError, setLibrarySyncError] = useState<string | null>(null);
  const initialSyncStartedRef = useRef(false);
  const syncInFlightRef = useRef(false);

  const load = async () => {
    try {
      const next = await api.documentLibrary();
      setLibrary(next);
      setSelectedDocumentIds((current) => new Set(
        [...current].filter((id) => next.documents.some((document) => document.documentId === id)),
      ));
    } catch (reason) {
      showToast({
        tone: "error",
        title: "書類ライブラリを取得できませんでした",
        message: reason instanceof ApiError ? reason.message : "バックエンドとの接続を確認してください。",
      });
    }
  };

  const synchronizeLibrary = async (operation: "bidirectional" | "push" = "bidirectional") => {
    if (syncInFlightRef.current) return;
    syncInFlightRef.current = true;
    setLibrarySyncing(true);
    setLibrarySyncOperation(operation);
    setLibrarySyncError(null);
    try {
      const result = operation === "push"
        ? await api.syncDocumentLibraryToDrive()
        : await api.syncDocumentLibrary();
      setLibrarySyncResult(result);
    } catch (reason) {
      setLibrarySyncError(reason instanceof ApiError || reason instanceof Error
        ? reason.message
        : "Google Driveと同期できませんでした。");
    } finally {
      await load();
      syncInFlightRef.current = false;
      setLibrarySyncing(false);
      setLibrarySyncOperation(null);
    }
  };

  const reconnectGoogle = () => {
    if (libraryReconnecting) return;
    setLibraryReconnecting(true);
    setLibrarySyncError(null);
    window.location.assign(googleLoginUrl(window.location.href));
  };

  const confirmLegacyOwnership = async (
    documentId: string,
    resourceName?: string,
    driveRootName?: string,
  ) => {
    const label = resourceName ? `「${resourceName}」` : "この書類";
    const rootLabel = driveRootName ? `\n保存先: ${driveRootName}` : "";
    if (!window.confirm(
      `${label}のスプレッドシートが、現在接続中のGoogleアカウントのファイルであることを確認します。${rootLabel}\n\nDriveのルート直下にある旧書類は、独自ライブラリの現在の保存場所へ整理されます。Drive側ですでに同期フォルダへ移動済みの場合は、その位置を独自ライブラリへ取り込みます。\n\n別のアカウントのファイルではないことを確認してから続行してください。`,
    )) return;
    try {
      const confirmed = await api.confirmDocumentDriveOwnership(documentId);
      showToast({
        tone: "success",
        title: "Googleアカウントを確認しました",
        message: confirmed.locationAlignedFromLibrary
          ? "この書類を双方向同期へ追加し、独自ライブラリの保存場所をGoogle Driveへ反映しました。"
          : "この書類を双方向同期の対象に追加しました。",
      });
      await synchronizeLibrary();
    } catch (reason) {
      showToast({
        tone: "error",
        title: "所有アカウントを確認できませんでした",
        message: reason instanceof ApiError ? reason.message : "保存先とGoogleアカウントを確認してください。",
      });
    }
  };

  useEffect(() => {
    if (initialSyncStartedRef.current) return;
    initialSyncStartedRef.current = true;
    void load();
    void synchronizeLibrary();
  }, []);
  useEffect(() => { setSelectedDocumentIds(new Set()); }, [currentFolderId, query]);
  useEffect(() => {
    if (!library || !initialDocumentId) return;
    const document = library.documents.find((item) => item.documentId === initialDocumentId);
    if (document) setSelectedLedger(document);
  }, [initialDocumentId, library]);

  const folderById = useMemo(
    () => new Map(library?.folders.map((folder) => [folder.id, folder]) ?? []),
    [library],
  );
  const childFolders = library?.folders.filter((folder) => folder.parentId === currentFolderId) ?? [];
  const normalizedQuery = query.trim().toLowerCase();
  const visibleDocuments = (library?.documents ?? []).filter((document) => {
    if (!normalizedQuery && document.libraryFolderId !== currentFolderId) return false;
    return !normalizedQuery
      || document.individualId.toLowerCase().includes(normalizedQuery)
      || document.cattleName?.toLowerCase().includes(normalizedQuery)
      || document.displayTitle?.toLowerCase().includes(normalizedQuery)
      || document.displaySubtitle?.toLowerCase().includes(normalizedQuery)
      || document.templateName.toLowerCase().includes(normalizedQuery);
  });

  const moveDocument = async (document: DocumentLibraryLedger, folderId: string | null) => {
    if (document.libraryFolderId === folderId) return;
    try {
      await api.moveLibraryDocument(document.documentId, folderId, document.locationVersion);
      await load();
      showToast({
        tone: "success",
        title: "書類を移動しました",
        message: folderId
          ? `「${folderById.get(folderId)?.name ?? "フォルダ"}」へ収納しました。`
          : "ルートへ移動しました。",
      });
    } catch (reason) {
      showToast({
        tone: "error",
        title: "書類を移動できませんでした",
        message: reason instanceof ApiError ? reason.message : "最新状態を読み込んで再試行してください。",
      });
      await load();
    }
  };

  const dropDocument = (event: DragEvent, folderId: string | null) => {
    event.preventDefault();
    const documentId = event.dataTransfer.getData("application/x-ledger-document");
    const document = library?.documents.find((item) => item.documentId === documentId);
    if (document) void moveDocument(document, folderId);
  };

  const createFolder = async () => {
    if (!folderName.trim()) return;
    try {
      await api.createDocumentFolder(folderName, currentFolderId);
      setFolderName("");
      setCreatingFolder(false);
      await load();
      showToast({ tone: "success", title: "フォルダを作成しました" });
    } catch (reason) {
      await load();
      showToast({
        tone: "error",
        title: "フォルダを作成できませんでした",
        message: reason instanceof ApiError && /^(DRIVE_|GOOGLE_)/.test(reason.code)
          ? `${reason.message} フォルダは保持されるため、再同期してください。`
          : reason instanceof ApiError
            ? reason.message
          : "入力内容を確認してください。",
      });
    }
  };

  const renameFolder = async (folder: DocumentLibraryFolder) => {
    const name = window.prompt("新しいフォルダ名を入力してください。", folder.name);
    if (!name || name === folder.name) return;
    try {
      await api.renameDocumentFolder(folder.id, name);
      await load();
      showToast({ tone: "success", title: "フォルダ名を変更しました" });
    } catch (reason) {
      showToast({ tone: "error", title: "フォルダ名を変更できませんでした", message: reason instanceof ApiError ? reason.message : undefined });
    }
  };

  const deleteFolder = async (folder: DocumentLibraryFolder) => {
    if (!window.confirm(`「${folder.name}」を削除しますか？`)) return;
    try {
      await api.deleteDocumentFolder(folder.id);
      if (currentFolderId === folder.id) setCurrentFolderId(folder.parentId);
      await load();
      showToast({ tone: "success", title: "フォルダを削除しました" });
    } catch (reason) {
      showToast({ tone: "error", title: "フォルダを削除できませんでした", message: reason instanceof ApiError ? reason.message : undefined });
    }
  };

  const renameDocument = async (document: DocumentLibraryLedger) => {
    const currentName = document.libraryName
      || document.displayTitle
      || document.cattleName
      || document.individualId;
    const name = window.prompt("新しい書類名を入力してください。", currentName);
    if (!name || name === currentName) return;
    try {
      const renamed = await api.renameLibraryDocument(document.documentId, name);
      await load();
      showToast({
        tone: "success",
        title: "書類名を変更しました",
        message: renamed.spreadsheetRenamed
          ? "Drive上のスプレッドシート名にも反映しました。"
          : undefined,
      });
    } catch (reason) {
      showToast({
        tone: "error",
        title: "書類名を変更できませんでした",
        message: reason instanceof ApiError ? reason.message : undefined,
      });
    }
  };

  const deleteSelectedDocuments = async () => {
    const documentIds = [...selectedDocumentIds];
    if (documentIds.length === 0) return;
    if (!window.confirm(`${documentIds.length}件の書類をライブラリから削除しますか？\nOCR履歴とDrive上のスプレッドシートは削除されません。`)) return;
    try {
      const deleted = await api.deleteLibraryDocuments(documentIds);
      setSelectedDocumentIds(new Set());
      await load();
      showToast({
        tone: "success",
        title: `${deleted.deletedCount}件の書類を削除しました`,
        message: "Drive上のスプレッドシートは保持されています。",
      });
    } catch (reason) {
      showToast({
        tone: "error",
        title: "書類を削除できませんでした",
        message: reason instanceof ApiError ? reason.message : undefined,
      });
      await load();
    }
  };

  const allVisibleSelected = visibleDocuments.length > 0
    && visibleDocuments.every((document) => selectedDocumentIds.has(document.documentId));

  if (selectedLedger) {
    return <GroupDetail group={ledgerGroup(selectedLedger)} onBack={() => setSelectedLedger(null)} startReading={startReading} openReview={openReview} />;
  }

  return (
    <div className="page-stack document-library-page">
      <header className="page-header split">
        <div><span className="eyebrow">DOCUMENT LIBRARY</span><h1>書類ライブラリ</h1><p>生成された書類はルートに保存されます。必要なフォルダを作成し、ドラッグ＆ドロップで整理してください。</p></div>
        {libraryView === "library" && <button className="secondary" onClick={() => void load()}>再読み込み</button>}
      </header>

      <div className="library-view-switch" role="tablist" aria-label="書類の表示元">
        <button
          type="button"
          role="tab"
          aria-selected={libraryView === "library"}
          className={libraryView === "library" ? "active" : ""}
          onClick={() => setLibraryView("library")}
        >
          独自ライブラリ
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={libraryView === "drive"}
          className={libraryView === "drive" ? "active" : ""}
          onClick={() => setLibraryView("drive")}
        >
          Google Drive
        </button>
      </div>

      <LibrarySyncStatus
        syncing={librarySyncing}
        syncOperation={librarySyncOperation}
        reconnecting={libraryReconnecting}
        result={librarySyncResult}
        requestError={librarySyncError}
        onSync={() => void synchronizeLibrary()}
        onPush={() => void synchronizeLibrary("push")}
        onReconnect={reconnectGoogle}
        onConfirmOwnership={(documentId, resourceName, driveRootName) => void confirmLegacyOwnership(documentId, resourceName, driveRootName)}
      />

      {libraryView === "library" ? (
        <>
          <div className="library-explorer-toolbar">
            <div className="library-breadcrumbs">
              <button type="button" onClick={() => setCurrentFolderId(null)}>ルート</button>
              {folderPath(currentFolderId, folderById).map((folder) => <button type="button" key={folder.id} onClick={() => setCurrentFolderId(folder.id)}>{folder.name}</button>)}
            </div>
            <button className="primary compact" onClick={() => setCreatingFolder(true)}>新しいフォルダ</button>
          </div>

          <label className="library-search"><span>書類を検索</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="識別番号・名称・テンプレート名" /></label>

          <div className="library-selection-bar">
            <label><input type="checkbox" checked={allVisibleSelected} disabled={visibleDocuments.length === 0} onChange={(event) => setSelectedDocumentIds(event.target.checked ? new Set(visibleDocuments.map((document) => document.documentId)) : new Set())} /><span>表示中をすべて選択</span></label>
            <strong>{selectedDocumentIds.size}件選択中</strong>
            {selectedDocumentIds.size > 0 && <button type="button" className="ghost compact" onClick={() => setSelectedDocumentIds(new Set())}>選択解除</button>}
            <button type="button" className="danger compact" disabled={selectedDocumentIds.size === 0} onClick={() => void deleteSelectedDocuments()}>選択した書類を削除</button>
          </div>

          <div className="library-explorer">
            <aside className="library-folder-tree" aria-label="フォルダ一覧">
              <button className={currentFolderId === null ? "active" : ""} onClick={() => setCurrentFolderId(null)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => dropDocument(event, null)}><FolderIcon />ルート</button>
              <FolderTree folders={library?.folders ?? []} parentId={null} currentFolderId={currentFolderId} onSelect={setCurrentFolderId} onDrop={dropDocument} />
            </aside>

            <main className="library-directory-content" onDragOver={(event) => event.preventDefault()} onDrop={(event) => dropDocument(event, currentFolderId)}>
              {creatingFolder && (
                <form className="library-folder-create" onSubmit={(event) => { event.preventDefault(); void createFolder(); }}>
                  <input autoFocus value={folderName} onChange={(event) => setFolderName(event.target.value)} placeholder="フォルダ名" maxLength={100} />
                  <button className="primary compact" disabled={!folderName.trim()}>作成</button>
                  <button type="button" className="ghost compact" onClick={() => { setCreatingFolder(false); setFolderName(""); }}>キャンセル</button>
                </form>
              )}

              {!normalizedQuery && childFolders.length > 0 && (
                <section className="library-folder-grid" aria-label="フォルダ">
                  {childFolders.map((folder) => (
                    <article className="library-folder-card" key={folder.id} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.stopPropagation(); dropDocument(event, folder.id); }}>
                      <button className="library-folder-open" onClick={() => setCurrentFolderId(folder.id)}><FolderIcon /><span><strong>{folder.name}</strong><small>{folder.documentCount}件の書類</small></span></button>
                      <div><button className="ghost compact" onClick={() => void renameFolder(folder)}>名前変更</button><button className="ghost compact" onClick={() => void deleteFolder(folder)}>削除</button></div>
                    </article>
                  ))}
                </section>
              )}

              {!library ? (
                <div className="empty-state large">書類を読み込んでいます…</div>
              ) : visibleDocuments.length === 0 ? (
                <div className="empty-state large"><FolderIcon /><h2>{normalizedQuery ? "該当する書類がありません" : "このフォルダは空です"}</h2><p>{normalizedQuery ? "検索条件を変更してください。" : "ルートまたは別のフォルダから書類を移動できます。"}</p></div>
              ) : (
                <section className="library-card-grid" aria-label="書類一覧">
                  {visibleDocuments.map((document) => <LibraryDocumentCard document={document} folders={library.folders} folderById={folderById} key={document.documentId} selected={selectedDocumentIds.has(document.documentId)} onSelect={(selected) => setSelectedDocumentIds((current) => { const next = new Set(current); if (selected) next.add(document.documentId); else next.delete(document.documentId); return next; })} onOpen={() => setSelectedLedger(document)} onMove={moveDocument} onRename={() => void renameDocument(document)} />)}
                </section>
              )}
            </main>
          </div>
        </>
      ) : (
        <GoogleDriveLibraryView />
      )}
    </div>
  );
}

function LibrarySyncStatus({ syncing, syncOperation, reconnecting, result, requestError, onSync, onPush, onReconnect, onConfirmOwnership }: {
  syncing: boolean;
  syncOperation: "bidirectional" | "push" | null;
  reconnecting: boolean;
  result: DocumentLibrarySyncResult | null;
  requestError: string | null;
  onSync: () => void;
  onPush: () => void;
  onReconnect: () => void;
  onConfirmOwnership: (documentId: string, resourceName?: string, driveRootName?: string) => void;
}) {
  const busy = syncing || reconnecting;
  const counts = result ? [
    result.foldersImported > 0 ? `Driveから${result.foldersImported}フォルダ取込` : null,
    result.foldersMirrored > 0 ? `Driveへ${result.foldersMirrored}フォルダ作成` : null,
    result.foldersUpdated > 0 ? `${result.foldersUpdated}フォルダ更新` : null,
    result.documentsUpdated > 0 ? `${result.documentsUpdated}書類更新` : null,
  ].filter((item): item is string => Boolean(item)) : [];
  const tone = busy
    ? "syncing"
    : requestError || result?.status === "ERROR" || result?.status === "REAUTHORIZATION_REQUIRED"
      ? "error"
      : result?.status === "PARTIAL"
        ? "partial"
        : result?.status === "SYNCED"
          ? "synced"
          : "idle";
  const title = reconnecting
    ? "Google連携を更新しています"
    : syncing
      ? syncOperation === "push"
        ? "Google Driveへ未反映を同期中"
        : "Google Driveと同期中"
    : requestError
      ? "Google Driveと同期できませんでした"
      : result?.status === "SYNCED"
        ? result.mode === "LIBRARY_TO_DRIVE"
          ? "Google Driveへの反映が完了しました"
          : "Google Driveと同期済み"
        : result?.status === "PARTIAL"
          ? result.mode === "LIBRARY_TO_DRIVE"
            ? "反映できる項目をGoogle Driveへ同期しました"
            : "一部を同期しました"
          : result?.status === "REAUTHORIZATION_REQUIRED"
            ? "Googleアカウントへの再接続が必要です"
            : result?.status === "ERROR"
              ? "Google Driveと同期できませんでした"
              : "同期状態を確認できませんでした";
  const detail = reconnecting
    ? "保存先設定を保持したまま、追加権限の認証画面を開いています。"
    : syncing
      ? syncOperation === "push"
        ? "独自ライブラリを基準に、未反映のフォルダと書類を確認しています。"
        : "独自ライブラリとGoogle Driveのフォルダ構造を確認しています。"
    : requestError
      || result?.error
      || (result?.status === "PARTIAL" && result.conflicts > 0
        ? `${result.conflicts}件の競合を検出しました。データを失わないよう独自ライブラリの内容を保持しました。`
        : counts.length > 0
          ? counts.join("・")
          : result?.status === "SYNCED"
            ? result.mode === "LIBRARY_TO_DRIVE"
              ? "Google Driveへ未反映のフォルダ・書類はありません。"
              : "フォルダ構造に未反映の変更はありません。"
            : "再同期して最新状態を確認してください。");
  const activityAt = result?.lastSuccessAt ?? (!busy ? result?.lastAttemptAt : null);
  const activityLabel = result?.lastSuccessAt ? "最終同期" : "最終試行";

  return (
    <aside
      className={`library-sync-status ${tone}`}
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
      aria-busy={busy}
    >
      <span className="library-sync-indicator" aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{detail}</p>
        {!busy && result?.conflictItems?.length ? (
          <details className="library-sync-conflicts">
            <summary>競合の詳細を表示</summary>
            <ul>
              {result.conflictItems.map((item, index) => (
                <li key={`${item.code}-${item.resourceId}-${index}`}>
                  {item.resourceName && <strong>{item.resourceName}</strong>}
                  {(item.driveRootName || item.driveRootId) && (
                    <small>保存先: {item.driveRootName || item.driveRootId}</small>
                  )}
                  <span>{item.detail}</span>
                  {item.code === "DRIVE_DOCUMENT_OWNER_UNCONFIRMED" && (
                    <button
                      type="button"
                      className="secondary compact"
                      disabled={busy}
                      onClick={() => onConfirmOwnership(
                        item.resourceId,
                        item.resourceName,
                        item.driveRootName || item.driveRootId,
                      )}
                    >
                      現在のGoogleアカウントで確認
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {activityAt && (
          <small>
            {activityLabel} <time dateTime={activityAt}>{new Date(activityAt).toLocaleString("ja-JP")}</time>
          </small>
        )}
      </div>
      <div className="library-sync-actions">
        <button
          type="button"
          className="primary compact"
          disabled={busy || result?.status === "REAUTHORIZATION_REQUIRED"}
          onClick={onPush}
        >
          {syncing && syncOperation === "push"
            ? "Driveへ反映中…"
            : "未反映をGoogle Driveへ同期"}
        </button>
        <button
          type="button"
          className="secondary compact"
          disabled={busy}
          onClick={result?.status === "REAUTHORIZATION_REQUIRED" ? onReconnect : onSync}
        >
          {reconnecting
            ? "更新中…"
            : syncing && syncOperation === "bidirectional"
              ? "同期中…"
              : result?.status === "REAUTHORIZATION_REQUIRED"
                ? "Google連携を更新"
                : "再同期"}
        </button>
      </div>
    </aside>
  );
}

function GoogleDriveLibraryView() {
  const [folder, setFolder] = useState<DriveOutputFolder | null>(null);
  const [pickerSession, setPickerSession] = useState<DrivePickerSession | null>(null);
  const pickerHostRef = useRef<HTMLDivElement | null>(null);
  const [pickerLoading, setPickerLoading] = useState(false);
  const [selectedSpreadsheet, setSelectedSpreadsheet] = useState<PickedDriveSpreadsheet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setFolder(null);
    setPickerSession(null);
    setPickerLoading(false);
    setSelectedSpreadsheet(null);
    setError(null);

    void (async () => {
      try {
        const outputFolder = await api.driveOutputFolder();
        if (!outputFolder.pickerConfigured) {
          throw new Error(outputFolder.pickerConfigurationError ?? "Google Pickerの設定が必要です。");
        }
        if (!outputFolder.folderId) {
          throw new Error("保存先設定でGoogle Driveの保存先フォルダーを選択してください。");
        }
        if (cancelled) return;
        setFolder(outputFolder);
        const session = await api.drivePickerSession();
        if (!cancelled) setPickerSession(session);
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof ApiError || reason instanceof Error
          ? reason.message
          : "Google Driveを表示できませんでした。");
      }
    })();

    return () => { cancelled = true; };
  }, [reloadKey]);

  useEffect(() => {
    if (!folder?.folderId || !pickerSession || !pickerHostRef.current) return;
    let cancelled = false;
    let disposePicker: (() => void) | null = null;
    setPickerLoading(true);
    setError(null);

    void mountGoogleDriveSpreadsheetPicker(
      pickerHostRef.current,
      pickerSession,
      folder.folderId,
      (spreadsheet) => {
        if (cancelled) return;
        setSelectedSpreadsheet(spreadsheet);
        window.open(spreadsheet.url, "_blank", "noopener,noreferrer");
      },
    ).then((mountedPicker) => {
      if (cancelled) {
        mountedPicker.dispose();
        return;
      }
      disposePicker = mountedPicker.dispose;
      setPickerLoading(false);
    }).catch((reason: unknown) => {
      if (cancelled) return;
      setPickerLoading(false);
      setError(reason instanceof ApiError || reason instanceof Error
        ? reason.message
        : "Google Pickerを表示できませんでした。");
    });

    return () => {
      cancelled = true;
      disposePicker?.();
    };
  }, [folder?.folderId, pickerSession]);

  return (
    <section className="library-drive-browser" aria-labelledby="library-drive-title">
      <header className="library-drive-browser-header">
        <div>
          <span className="eyebrow">GOOGLE DRIVE</span>
          <h2 id="library-drive-title">{folder?.folderName ?? "保存先フォルダー"}</h2>
          <p>保存先からスプレッドシートを選択します。選択したファイルは新しいタブで開きます。</p>
        </div>
        {folder?.folderId && (
          <a
            className="secondary button-link"
            href={`https://drive.google.com/drive/folders/${encodeURIComponent(folder.folderId)}`}
            target="_blank"
            rel="noreferrer"
          >
            Google Driveで開く ↗
          </a>
        )}
      </header>

      {folder && folder.roots.length > 1 && (
        <label className="library-drive-root-select">
          <span>表示する保存先</span>
          <select
            value={folder.folderId ?? ""}
            onChange={(event) => {
              const selected = folder.roots.find((root) => root.folderId === event.target.value);
              if (selected) {
                setFolder({
                  ...folder,
                  folderId: selected.folderId,
                  folderName: selected.folderName,
                });
              }
            }}
          >
            {folder.roots.map((root) => (
              <option key={root.folderId} value={root.folderId}>{root.folderName}</option>
            ))}
          </select>
        </label>
      )}

      {error ? (
        <div className="library-drive-error">
          <p className="error-box" role="alert">{error}</p>
          <button type="button" className="secondary" onClick={() => setReloadKey((current) => current + 1)}>再試行</button>
        </div>
      ) : !folder || !pickerSession ? (
        <div className="empty-state large" role="status">Google Driveを読み込んでいます…</div>
      ) : (
        <div className="library-drive-picker-embed">
          <div className="library-drive-picker-toolbar">
            <p>保存先フォルダー内を移動し、開くスプレッドシートを選択してください。</p>
            <button type="button" className="secondary compact" onClick={() => setReloadKey((current) => current + 1)}>表示を再読み込み</button>
          </div>
          <div className="library-drive-picker-frame">
            {pickerLoading && <div className="library-drive-picker-loading" role="status">Google Driveを読み込んでいます…</div>}
            <div className="library-drive-picker-host" ref={pickerHostRef} />
          </div>
        </div>
      )}

      {selectedSpreadsheet && (
        <aside className="notice compact" role="status">
          <strong>{selectedSpreadsheet.name}</strong>
          <span>新しいタブが開かなかった場合は、右のボタンから開いてください。</span>
          <a className="secondary button-link compact" href={selectedSpreadsheet.url} target="_blank" rel="noreferrer">スプレッドシートを開く ↗</a>
        </aside>
      )}
    </section>
  );
}

function FolderTree({ folders, parentId, currentFolderId, onSelect, onDrop }: {
  folders: DocumentLibraryFolder[];
  parentId: string | null;
  currentFolderId: string | null;
  onSelect: (id: string) => void;
  onDrop: (event: DragEvent, folderId: string) => void;
}) {
  const children = folders.filter((folder) => folder.parentId === parentId);
  if (children.length === 0) return null;
  return <div className={parentId ? "library-folder-children" : ""}>{children.map((folder) => <div key={folder.id}><button className={currentFolderId === folder.id ? "active" : ""} onClick={() => onSelect(folder.id)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.stopPropagation(); onDrop(event, folder.id); }}><FolderIcon />{folder.name}<small>{folder.documentCount}</small></button><FolderTree folders={folders} parentId={folder.id} currentFolderId={currentFolderId} onSelect={onSelect} onDrop={onDrop} /></div>)}</div>;
}

function FolderIcon() {
  return <span className="library-folder-icon" aria-hidden="true" />;
}

function LibraryDocumentCard({ document, folders, folderById, selected, onSelect, onOpen, onMove, onRename }: {
  document: DocumentLibraryLedger;
  folders: DocumentLibraryFolder[];
  folderById: Map<string, DocumentLibraryFolder>;
  selected: boolean;
  onSelect: (selected: boolean) => void;
  onOpen: () => void;
  onMove: (document: DocumentLibraryLedger, folderId: string | null) => Promise<void>;
  onRename: () => void;
}) {
  const [printMenuOpen, setPrintMenuOpen] = useState(false);
  const [printerStatus, setPrinterStatus] = useState<{ printerName: string | null; connected: boolean; state: string; detail: string } | null>(null);
  const [printCopies, setPrintCopies] = useState(1);
  const [printBusy, setPrintBusy] = useState(false);
  const [printMessage, setPrintMessage] = useState("");
  const [printMode, setPrintMode] = useState<"spreadsheet" | "image">(document.spreadsheetUrl ? "spreadsheet" : "image");
  const [paperSize, setPaperSize] = useState<"A4" | "A5" | "B5" | "LETTER">("A4");
  const [printOrientation, setPrintOrientation] = useState<"portrait" | "landscape">("portrait");
  const [fitMode, setFitMode] = useState<"contain" | "shrink">("contain");
  const [marginMm, setMarginMm] = useState(10);
  const firstPageImage = document.pages[0]?.thumbnailUrl;
  const documentTitle = document.displayTitle || document.cattleName || document.individualId;
  const previewParameters = new URLSearchParams({
    mode: printMode,
    paperSize,
    orientation: printOrientation,
    fitMode,
    marginMm: String(marginMm),
  });
  const printPreviewUrl = `/api/v1/document-library/documents/${encodeURIComponent(document.documentId)}/print-preview?${previewParameters.toString()}`;

  const openPrintMenu = async () => {
    setPrintMenuOpen(true);
    setPrintMode(document.spreadsheetUrl ? "spreadsheet" : "image");
    setPrinterStatus(null);
    setPrintMessage("EPSONプリンターとの接続を確認しています。");
    try {
      const response = await fetch("/api/v1/printer/status");
      const status = await response.json();
      if (!response.ok) throw new Error(status.detail || "プリンターの状態を取得できませんでした。");
      setPrinterStatus(status);
      setPrintMessage(status.detail);
    } catch (error) {
      setPrinterStatus({ printerName: null, connected: false, state: "未接続", detail: error instanceof Error ? error.message : "EPSONプリンターに接続されていません。" });
      setPrintMessage(error instanceof Error ? error.message : "EPSONプリンターに接続されていません。");
    }
  };

  const submitPrint = async () => {
    if (!printerStatus?.connected || printBusy) return;
    setPrintBusy(true);
    setPrintMessage("印刷データをEPSONプリンターへ送信しています。");
    try {
      const response = await fetch(`/api/v1/document-library/documents/${encodeURIComponent(document.documentId)}/print`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: printMode,
          copies: printCopies,
          paperSize,
          orientation: printOrientation,
          fitMode,
          marginMm,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "印刷データを送信できませんでした。");
      setPrintMessage(result.detail);
    } catch (error) {
      setPrintMessage(error instanceof Error ? error.message : "印刷データを送信できませんでした。");
    } finally {
      setPrintBusy(false);
    }
  };

  return (
    <article
      className={`library-card library-document-card${selected ? " selected" : ""}${printMenuOpen ? " print-open" : ""}`}
      draggable={!printMenuOpen}
      onDragStart={(event) => { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("application/x-ledger-document", document.documentId); }}
      onPointerMove={(event) => {
        if (event.pointerType === "touch") return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return;
        const x = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
        const y = Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height));
        event.currentTarget.style.setProperty("--card-tilt-x", `${((0.5 - y) * 4.8).toFixed(2)}deg`);
        event.currentTarget.style.setProperty("--card-tilt-y", `${((x - 0.5) * 4.8).toFixed(2)}deg`);
        event.currentTarget.style.setProperty("--card-glare-x", `${(x * 100).toFixed(1)}%`);
        event.currentTarget.style.setProperty("--card-glare-y", `${(y * 100).toFixed(1)}%`);
        event.currentTarget.style.setProperty("--card-glare-opacity", "1");
      }}
      onPointerLeave={(event) => {
        event.currentTarget.style.setProperty("--card-tilt-x", "0deg");
        event.currentTarget.style.setProperty("--card-tilt-y", "0deg");
        event.currentTarget.style.setProperty("--card-glare-opacity", "0");
      }}
    >
      <label className="library-document-select"><input type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} /><span>選択</span></label>
      <button className="library-document-open" onClick={onOpen}>
        <div className="library-card-preview">
          <span aria-hidden="true">書</span>
          {firstPageImage && <img src={firstPageImage} alt={`${documentTitle}の1ページ目`} loading="lazy" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
          <small>1 / {document.pageCount}ページ</small>
        </div>
        <div><span className="eyebrow">{document.displayTitleLabel || "書類"}</span><h2>{document.displayTitle || document.cattleName || document.individualId}</h2><strong>{document.displaySubtitle || document.individualId}</strong><p>{document.templateName} · 最終更新 {new Date(document.updatedAt).toLocaleString("ja-JP")}</p>{document.libraryFolderId && <small className="library-document-path">{folderPath(document.libraryFolderId, folderById).map((folder) => folder.name).join(" / ")}</small>}</div>
      </button>
      <div className="library-document-actions"><button type="button" className="ghost compact" onClick={onRename}>名前変更</button><button type="button" className="secondary compact" onClick={() => void openPrintMenu()}>印刷</button>{document.spreadsheetUrl && <a className="secondary compact" href={document.spreadsheetUrl} target="_blank" rel="noreferrer">スプレッドシートを開く ↗</a>}</div>
      <label className="library-move-select"><span>移動先</span><select value={document.libraryFolderId ?? ""} onChange={(event) => void onMove(document, event.target.value || null)}><option value="">ルート</option>{folders.map((folder) => <option value={folder.id} key={folder.id}>{folderPath(folder.id, folderById).map((item) => item.name).join(" / ")}</option>)}</select></label>
      {printMenuOpen && <div className="library-print-backdrop" onClick={() => setPrintMenuOpen(false)}><section className="library-print-dialog library-print-preview-dialog" role="dialog" aria-modal="true" aria-labelledby={`print-title-${document.documentId}`} onClick={(event) => event.stopPropagation()}><header><div><span className="eyebrow">PRINT PREVIEW</span><h2 id={`print-title-${document.documentId}`}>印刷プレビュー</h2></div><button type="button" className="ghost compact" onClick={() => setPrintMenuOpen(false)}>閉じる</button></header><p>{documentTitle}</p><div className="library-print-workspace"><div className="library-print-settings"><div className="library-print-source"><button type="button" className={printMode === "spreadsheet" ? "active" : ""} disabled={!document.spreadsheetUrl} onClick={() => setPrintMode("spreadsheet")}><strong>構造化済みシート</strong><small>OCR結果をXLSX構造へ配置</small></button><button type="button" className={printMode === "image" ? "active" : ""} disabled={!firstPageImage} onClick={() => setPrintMode("image")}><strong>読み取り実画像</strong><small>読み取った1ページ目</small></button></div><div className="library-print-fields"><label><span>用紙サイズ</span><select value={paperSize} onChange={(event) => setPaperSize(event.target.value as "A4" | "A5" | "B5" | "LETTER")}><option value="A4">A4</option><option value="A5">A5</option><option value="B5">B5</option><option value="LETTER">Letter</option></select></label><label><span>用紙の向き</span><select value={printOrientation} onChange={(event) => setPrintOrientation(event.target.value as "portrait" | "landscape")}><option value="portrait">縦</option><option value="landscape">横</option></select></label><label><span>配置基準</span><select value={fitMode} onChange={(event) => setFitMode(event.target.value as "contain" | "shrink")}><option value="contain">用紙内に収める</option><option value="shrink">原寸・大きい場合のみ縮小</option></select></label><label><span>余白</span><select value={marginMm} onChange={(event) => setMarginMm(Number(event.target.value))}><option value={5}>5 mm</option><option value={10}>10 mm</option><option value={15}>15 mm</option><option value={20}>20 mm</option></select></label></div><div className={`library-printer-status ${printerStatus?.connected ? "connected" : "disconnected"}`}><strong>{printerStatus?.printerName || "EPSONプリンター"}</strong><span>{printerStatus?.state || "確認中"}</span><small>{printMessage}</small></div><label className="library-print-copies"><span>印刷部数</span><select value={printCopies} onChange={(event) => setPrintCopies(Number(event.target.value))} disabled={printBusy}><option value={1}>1部</option><option value={2}>2部</option><option value={3}>3部</option></select></label><button type="button" className="primary library-print-submit" disabled={!printerStatus?.connected || printBusy || (printMode === "spreadsheet" ? !document.spreadsheetUrl : !firstPageImage)} onClick={() => void submitPrint()}>{printBusy ? "印刷データを送信中..." : `${paperSize}で印刷を実行`}</button></div><div className="library-print-preview"><div className="library-print-preview-label"><strong>{paperSize}・{printOrientation === "portrait" ? "縦" : "横"}</strong><span>余白 {marginMm} mm</span></div><iframe key={printPreviewUrl} title={`${documentTitle}の印刷プレビュー`} src={printPreviewUrl} /></div></div></section></div>}
    </article>
  );
}

function folderPath(folderId: string | null, folderById: Map<string, DocumentLibraryFolder>): DocumentLibraryFolder[] {
  const path: DocumentLibraryFolder[] = [];
  const visited = new Set<string>();
  let current = folderId ? folderById.get(folderId) : undefined;
  while (current && !visited.has(current.id)) {
    visited.add(current.id);
    path.unshift(current);
    current = current.parentId ? folderById.get(current.parentId) : undefined;
  }
  return path;
}

function ledgerGroup(ledger: DocumentLibraryLedger): DocumentLibraryGroup {
  return {
    individualId: ledger.individualId,
    cattleName: ledger.cattleName,
    displayTitle: ledger.displayTitle,
    displayTitleLabel: ledger.displayTitleLabel,
    displaySubtitle: ledger.displaySubtitle,
    displaySubtitleLabel: ledger.displaySubtitleLabel,
    ledgerCount: 1,
    pageCount: ledger.pageCount,
    lastUpdatedAt: ledger.updatedAt,
    ledgers: [ledger],
  };
}

function GroupDetail({ group, onBack, startReading, openReview }: {
  group: DocumentLibraryGroup;
  onBack: () => void;
  startReading: (context: OcrLibraryContext) => void;
  openReview: (jobId: string) => void;
}) {
  return (
    <div className="page-stack document-library-detail">
      <header className="page-header split"><div><span className="eyebrow">{group.displaySubtitle || group.individualId}</span><h1>{group.displayTitle || group.cattleName || group.individualId}の書類</h1><p>既存ページの読み直しまたは続きのページ追加を開始します。</p></div><button className="ghost" onClick={onBack}>← ライブラリへ戻る</button></header>
      {group.ledgers.map((ledger, index) => <LedgerPanel key={ledger.documentId} ledger={ledger} index={index} startReading={startReading} openReview={openReview} />)}
      {group.ledgers[0]?.pages[0]?.templateVersionId && <aside className="notice compact"><strong>同じ個体の別の台帳を作る</strong><span>現在の台帳とは別のスプレッドシートとして保存します。</span><button className="secondary" onClick={() => startReading({ action: "NEW_DOCUMENT", individualId: group.individualId, cattleName: group.cattleName, templateVersionId: group.ledgers[0].pages[0].templateVersionId! })}>別の台帳を読み取る</button></aside>}
    </div>
  );
}

function LedgerPanel({ ledger, index, startReading, openReview }: {
  ledger: DocumentLibraryLedger;
  index: number;
  startReading: (context: OcrLibraryContext) => void;
  openReview: (jobId: string) => void;
}) {
  const versionId = ledger.pages[0]?.templateVersionId;
  return (
    <section className="library-ledger-panel">
      <header><div><span className="eyebrow">台帳 {index + 1}</span><h2>{ledger.templateName}</h2><p>全{ledger.pageCount}ページ · 更新 {new Date(ledger.updatedAt).toLocaleString("ja-JP")}</p></div><div className="library-ledger-links">{ledger.currentJobId && <button className="secondary compact" onClick={() => openReview(ledger.currentJobId!)}>現在の結果を開く</button>}{ledger.spreadsheetUrl && <a className="secondary compact" href={ledger.spreadsheetUrl} target="_blank" rel="noreferrer">スプレッドシートを開く ↗</a>}</div></header>
      {ledger.spreadsheetSyncError && <p className="error-box">{ledger.spreadsheetSyncError}</p>}
      <div className="library-page-grid">{ledger.pages.map((page) => <article className="library-page-card" key={page.pageId}><img src={page.thumbnailUrl} alt={`${page.pageNumber}ページ目`} onError={(event) => { event.currentTarget.style.display = "none"; }} /><div><strong>{page.pageNumber}ページ目</strong><span>{page.versionCount > 1 ? `読み取り履歴 ${page.versionCount}件` : "現在の読み取り"}</span></div><button className="primary compact" disabled={!page.templateVersionId} onClick={() => page.templateVersionId && startReading({ action: "RESCAN_PAGE", individualId: ledger.individualId, cattleName: ledger.cattleName, templateVersionId: page.templateVersionId, documentId: ledger.documentId, pageId: page.pageId, pageNumber: page.pageNumber })}>このページを読み直す</button></article>)}</div>
      <button className="secondary library-add-page" disabled={!versionId} onClick={() => versionId && startReading({ action: "ADD_PAGE", individualId: ledger.individualId, cattleName: ledger.cattleName, templateVersionId: versionId, documentId: ledger.documentId })}>＋ 続きのページを追加</button>
    </section>
  );
}
