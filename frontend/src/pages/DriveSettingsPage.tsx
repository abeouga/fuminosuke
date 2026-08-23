import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { showToast } from "../components/Toast";
import { pickGoogleDriveFolder } from "../googlePicker";
import type { DriveOutputFolder } from "../types";

export function DriveSettingsPage() {
  const [outputFolder, setOutputFolder] = useState<DriveOutputFolder | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    try {
      setOutputFolder(await api.driveOutputFolder());
    } catch (reason) {
      showToast({
        tone: "error",
        title: "保存先を取得できませんでした",
        message: reason instanceof ApiError ? reason.message : "Google Driveとの接続を確認してください。",
      });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const chooseFolder = async () => {
    setBusy(true);
    try {
      const session = await api.drivePickerSession();
      const picked = await pickGoogleDriveFolder(session);
      if (!picked) return;
      const selected = await api.selectDriveFolder(picked.id);
      setOutputFolder(selected);
      showToast({
        tone: "success",
        title: "保存先を更新しました",
        message: selected.folderName ?? "選択したGoogle Driveフォルダーを使用します。",
      });
    } catch (reason) {
      showToast({
        tone: "error",
        title: "保存先を変更できませんでした",
        message: reason instanceof ApiError || reason instanceof Error
          ? reason.message
          : "Google Driveとの接続を確認してください。",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-stack drive-settings-page">
      <header className="page-header">
        <span className="eyebrow">GOOGLE DRIVE OUTPUT</span>
        <h1>読み取り結果の保存先</h1>
        <p>
          Google Driveの選択画面で、台帳ごとのスプレッドシートを保存するフォルダーを指定します。
          選択後はこの画面へ戻ります。
        </p>
      </header>

      {!outputFolder ? (
        <div className="empty-state large">
          <span>G</span>
          <h2>{busy ? "保存先を確認しています" : "保存先を表示できません"}</h2>
          {!busy && <button className="secondary" onClick={() => void load()}>再試行</button>}
        </div>
      ) : (
        <section className="drive-picker-panel" aria-labelledby="drive-folder-title">
          <div className="drive-selected-folder">
            <span className="drive-folder-mark" aria-hidden="true">▰</span>
            <div>
              <span className="eyebrow">CURRENT OUTPUT FOLDER</span>
              <h2 id="drive-folder-title">
                {outputFolder.folderName ?? "保存先が未選択です"}
              </h2>
              <p>
                {outputFolder.folderId
                  ? "新しい台帳のスプレッドシートはこのフォルダーへ作成されます。"
                  : "初回の読み取り結果を確定する前に保存先を選択してください。"}
              </p>
            </div>
          </div>
          <button
            type="button"
            className="primary drive-picker-button"
            disabled={busy || !outputFolder.pickerConfigured}
            onClick={() => void chooseFolder()}
          >
            {busy ? "Google Driveを開いています…" : "Google Driveから保存先を選ぶ"}
          </button>
          {!outputFolder.pickerConfigured && (
            <p className="notice compact" role="status">
              <strong>管理者のGoogle Picker設定が必要です</strong>
              <span>
                {outputFolder.pickerConfigurationError
                  ?? "GOOGLE_PICKER_API_KEY と GOOGLE_PICKER_APP_ID を設定してください。"}
              </span>
            </p>
          )}
        </section>
      )}

      <aside className="notice compact">
        <strong>保存単位</strong>
        <span>
          新しい台帳を確定すると、選択した場所に台帳専用のスプレッドシートを1件作成します。
          ページ追加と読み直しは同じファイルの差分だけを更新します。
        </span>
      </aside>
      <aside className="notice compact">
        <strong>フォルダーの整理</strong>
        <span>
          フォルダーの新規作成、移動、名前変更はGoogle Drive側で行ってください。
          このシステムは選択した保存先だけを記録します。
        </span>
      </aside>
    </div>
  );
}
