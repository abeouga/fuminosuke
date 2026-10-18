import type { Dispatch, SetStateAction } from "react";
import {
  FiCopy,
  FiEdit2,
  FiPlus,
  FiTrash2,
} from "react-icons/fi";
import type { Template } from "../types";
import { templatePreviewKey, type useTemplatePreviews } from "../hooks/useTemplatePreviews";
import { TemplatePrintFiles } from "./TemplatePrintFiles";
import { usePhoneDevice } from "../hooks/usePhoneDevice";

export type ReadingTemplateCard = {
  template: Template;
  version: Template["versions"][number];
  editVersion: Template["versions"][number];
  inputAvailable: boolean;
};
export function OcrTemplateSelector({ templateCards, visibleVersions, previewFor, previewImageState, setPreviewImageState, deletingTemplateId, duplicateBusyTemplateId, openReadingSetup, deleteTemplate, duplicateTemplate, openTemplateWorkspace }: {
  templateCards: ReadingTemplateCard[]; visibleVersions: ReadingTemplateCard[];
  previewFor: ReturnType<typeof useTemplatePreviews>;
  previewImageState: Record<string, "loaded" | "failed">;
  setPreviewImageState: Dispatch<SetStateAction<Record<string, "loaded" | "failed">>>;
  deletingTemplateId: string | null; duplicateBusyTemplateId: string | null;
  openReadingSetup: (versionId: string) => void;
  deleteTemplate: (template: Template) => Promise<void>;
  duplicateTemplate: (template: Template, version: Template["versions"][number]) => Promise<void>;
  openTemplateWorkspace: (workspace: "create" | "manage", editVersionId?: string) => void;
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
                  {visibleVersions.map(({ template, version, editVersion, inputAvailable }) => {
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
                            ? `${template.name} v${version.versionNumber}は編集中のためOCRに使用できません`
                            : `${template.name} v${version.versionNumber}の読み取り画像を準備`}
                          disabled={editing || deleting}
                          onClick={() => openReadingSetup(version.id)}
                        >
                          <span className="ocr-template-card-visual">
                            {(preview?.loading || (Boolean(preview?.url) && !previewImageState[previewKey]))
                              && <span className="ocr-template-card-skeleton" aria-hidden="true" />}
                            {(!preview?.loading && (!preview?.url || previewImageState[previewKey] === "failed"))
                              && <span className="ocr-template-card-placeholder" aria-hidden="true">型</span>}
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
                        {version.status === "ACTIVE" && version.mode === "SPREADSHEET_TEMPLATE" && (
                          <div className="ocr-template-print-files">
                            <TemplatePrintFiles key={version.id} versionId={version.id} disabled={deleting}
                              sourceImport={version.quality.printLayout == null && version.quality.sourceType === "xlsx"}
                              disabledReason={version.quality.printLayout != null || version.quality.sourceType === "xlsx"
                                ? undefined : "印刷用ファイルを取得できるXLSX原本がありません。"} />
                          </div>
                        )}
                        {!phoneDevice && <span className="ocr-template-card-actions">
                          <button
                            type="button"
                            className="secondary compact"
                            aria-label={`${template.name}を複製`}
                            disabled={deleting || duplicateBusyTemplateId !== null}
                            onClick={() => { void duplicateTemplate(template, version); }}
                          >
                            <FiCopy aria-hidden="true" focusable="false" />
                            {duplicateBusyTemplateId === template.id ? "複製中…" : "複製"}
                          </button>

                          <button
                            type="button"
                            className="secondary compact"
                            aria-label={`${template.name}のテンプレートを編集`}
                            disabled={!editVersion.sourceJobId || deleting}
                            onClick={() => openTemplateWorkspace("manage", editVersion.id)}
                          >
                            <FiEdit2 aria-hidden="true" focusable="false" />
                            編集
                          </button>
                          <button
                            type="button"
                            className="danger-button compact"
                            aria-label={`${template.name}を削除`}
                            disabled={Boolean(deletingTemplateId)}
                            onClick={() => void deleteTemplate(template)}
                          >
                            {!deleting && <FiTrash2 aria-hidden="true" focusable="false" />}
                            {deleting ? "削除中…" : "削除"}
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
