import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import {
  FiCpu,
  FiEdit2,
  FiFileText,
  FiInfo,
  FiLayers,
  FiLayout,
  FiPlus,
  FiTrash2,
} from "react-icons/fi";
import type { IconType } from "react-icons";
import type { Template } from "../types";
import { templatePreviewKey, type useTemplatePreviews } from "../hooks/useTemplatePreviews";
import { TemplatePrintFiles } from "./TemplatePrintFiles";
import { usePhoneDevice } from "../hooks/usePhoneDevice";
import { ITEM_JOIN_EDITING_VISIBLE, openItemJoinEditor } from "./TemplateItemJoinEditor";
import { openDocumentNameEditor } from "./TemplateDocumentNameEditor";
import { ImageSpinner, Spinner } from "./Spinner";

export type ReadingTemplateCard = {
  template: Template;
  version: Template["versions"][number];
  editVersion: Template["versions"][number];
  inputAvailable: boolean;
};
export function OcrTemplateSelector({ templateCards, previewFor, previewImageState, setPreviewImageState, deletingTemplateId, openReadingSetup, deleteTemplate, openTemplateWorkspace }: {
  templateCards: ReadingTemplateCard[];
  previewFor: ReturnType<typeof useTemplatePreviews>;
  previewImageState: Record<string, "loaded" | "failed">;
  setPreviewImageState: Dispatch<SetStateAction<Record<string, "loaded" | "failed">>>;
  deletingTemplateId: string | null;
  openReadingSetup: (versionId: string) => void;
  deleteTemplate: (template: Template) => Promise<void>;
  openTemplateWorkspace: (workspace: "create" | "manage", editVersionId?: string, editTarget?: "structure" | "fields") => void;
}) {
  // Template authoring is PC-only; phones keep reading with existing templates.
  const phoneDevice = usePhoneDevice();
  return (<section className="ocr-step-panel" aria-labelledby="ocr-step-template">
                <div className="ocr-step-heading">
                  <span className="ocr-template-heading-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none">
                      <path d="M7 3.75h7l4 4v12.5H7z" />
                      <path d="M14 3.75v4h4M10 12h5M10 15.5h5" />
                    </svg>
                  </span>
                  <h2 id="ocr-step-template">読み取る書類のテンプレートを選ぶ</h2>
                </div>
                <div className="ocr-template-grid" aria-label="使用するテンプレート">
                  {templateCards.map(({ template, version, editVersion, inputAvailable }) => {
                    const preview = previewFor(version);
                    const previewKey = templatePreviewKey(version);
                    const deleting = deletingTemplateId === template.id;
                    const editing = !inputAvailable;
                    return (
                      <article
                        key={version.id}
                        className={`ocr-template-card${editing ? " editing" : ""}`}
                      >
                        <button
                          type="button"
                          className="ocr-template-card-select"
                          aria-label={editing
                            ? `${template.name}は編集中のためOCRに使用できません`
                            : `${template.name}の読み取り画像を準備`}
                          disabled={editing || deleting}
                          onClick={() => openReadingSetup(version.id)}
                        >
                          <span className="ocr-template-card-visual">
                            {(preview?.loading || (Boolean(preview?.url) && !previewImageState[previewKey]))
                              && <ImageSpinner />}
                            {(!preview?.loading && (!preview?.url || previewImageState[previewKey] === "failed"))
                              && <ImageSpinner />}
                            {preview?.url && (
                              <img
                                key={previewKey}
                                src={preview.url}
                                alt=""
                                loading="lazy"
                                className={previewImageState[previewKey] === "loaded" ? "loaded" : ""}
                                onLoad={(event) => {
                                  event.currentTarget.hidden = false;
                                  setPreviewImageState((current) => ({ ...current, [previewKey]: "loaded" }));
                                }}
                                onError={(event) => {
                                  event.currentTarget.hidden = true;
                                  setPreviewImageState((current) => ({ ...current, [previewKey]: "failed" }));
                                }}
                              />
                            )}
                            {editing && (
                              <span className="ocr-template-editing-badge">編集中</span>
                            )}
                          </span>
                          <span className="ocr-template-card-copy">
                            <strong>{template.name}</strong>
                          </span>
                        </button>
                        {/* Printing the blank form is a PC task; a phone only reads. */}
                        {!phoneDevice && version.status === "ACTIVE" && version.mode === "SPREADSHEET_TEMPLATE"
                          && (version.quality.printLayout != null || blankSource(version.quality) !== null) && (
                          <div className="ocr-template-print-files">
                            <TemplatePrintFiles key={version.id} versionId={version.id} disabled={deleting}
                              sourceImport={version.quality.printLayout == null && blankSource(version.quality) !== null}
                              pdf={blankSource(version.quality) !== "package" || Boolean(version.quality.spreadsheetPrintablePdfPath)} />
                          </div>
                        )}
                        {!phoneDevice && <span className="ocr-template-card-actions">
                          <ReadingEditMenu
                            templateName={template.name}
                            disabled={!editVersion.sourceJobId || deleting}
                            onStructure={() => openTemplateWorkspace("manage", editVersion.id)}
                            onFields={() => openTemplateWorkspace("manage", version.id, "fields")}
                            onJoins={() => openItemJoinEditor(template.id, version.id)}
                            onDocumentName={() => openDocumentNameEditor(template.id, template.name)}
                          />
                          <button
                            type="button"
                            className="danger-button compact"
                            aria-label={`${template.name}を削除`}
                            disabled={Boolean(deletingTemplateId)}
                            onClick={() => void deleteTemplate(template)}
                          >
                            {!deleting && <FiTrash2 aria-hidden="true" focusable="false" />}
                            {deleting ? <><Spinner />削除中…</> : "削除"}
                          </button>
                        </span>}
                      </article>
                    );
                  })}
                  {!phoneDevice && <article className="ocr-template-card ocr-template-add-card">
                    <button type="button" className="ocr-template-add-trigger"
                      aria-label="テンプレートを追加" onClick={() => openTemplateWorkspace("create")}>
                      <FiPlus aria-hidden="true" focusable="false" />
                      <span>テンプレートを追加</span>
                    </button>
                  </article>}
                </div>
                {templateCards.length === 0 && (
                  <p className="ocr-template-empty">{phoneDevice
                    ? "有効化済みのテンプレートはありません。テンプレートはパソコンで作成してください。"
                    : "有効化済みのテンプレートはありません。追加カードから新しく作成してください。"}</p>
                )}
              </section>);
}

/** 「編集」: what to edit — the form's structure, the reading settings, or how items are shown. */
function ReadingEditMenu({ templateName, disabled, onStructure, onFields, onJoins, onDocumentName }: {
  templateName: string;
  disabled: boolean;
  onStructure: () => void;
  onFields: () => void;
  onJoins: () => void;
  onDocumentName: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const choose = (action: () => void) => () => { setOpen(false); action(); };
  return (
    <div className="template-edit-menu reading-edit-menu" ref={root}>
      <button
        type="button"
        className="secondary compact"
        aria-label={`${templateName}のテンプレートを編集`}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <FiEdit2 aria-hidden="true" focusable="false" />
        編集
      </button>
      {open && (
        <div className="template-edit-menu-list reading-edit-menu-list" role="menu" aria-label={`${templateName}の編集`}>
          <MenuItem icon={FiLayout} title="テンプレートを編集する" note="欄や表の形を直します。" onClick={choose(onStructure)} />
          <MenuItem icon={FiCpu} title="メモリ" note="よく出る言葉と、覚えた書き方です。" onClick={choose(onFields)} />
          {DOCUMENT_NAME_EDITING_VISIBLE && <MenuItem icon={FiFileText} title="ファイル名の付け方" note="書類名に入れる欄と、前後に付ける文字を変えます。" onClick={choose(onDocumentName)} />}
          {ITEM_JOIN_EDITING_VISIBLE && <MenuItem icon={FiLayers} title="表示する順番を変える"
            note="結果に出す順番を変えます。" onClick={choose(onJoins)} />}
        </div>
      )}
    </div>
  );
}

/**
 * 「ファイル名の付け方」 is not offered (2026-10-26, unlikely to be used). Its dialog and the saved
 * settings still work; set this to true to show the entry again.
 */
const DOCUMENT_NAME_EDITING_VISIBLE = false;

/** The name only; what it does is in the 「i」, shown on hover. */
function MenuItem({ icon: Icon, title, note, onClick }: { icon: IconType; title: string; note: string; onClick: () => void }) {
  return (
    <button type="button" role="menuitem" aria-label={`${title}（${note}）`} onClick={onClick}>
      <span className="menu-item-icon" aria-hidden="true"><Icon focusable="false" /></span>
      <strong className="menu-item-title">{title}</strong>
      <span className="menu-item-info" data-tip={note} aria-hidden="true"><FiInfo focusable="false" /></span>
    </button>
  );
}

/**
 * Where the blank workbook of a template without saved print pages comes from: the XLSX it was made
 * from, or the workbook carried in the template package it was imported from (with its PDF only when
 * the package carried one).
 */
function blankSource(quality: Record<string, unknown>): "xlsx" | "package" | null {
  if (quality.sourceType === "xlsx") return "xlsx";
  if (typeof quality.sourceType === "string" && quality.sourceType.startsWith("template-package")
    && typeof quality.spreadsheetSourcePath === "string" && quality.spreadsheetSourcePath) return "package";
  return null;
}
