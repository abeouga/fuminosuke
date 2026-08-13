import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, createIdempotencyKey } from "../api";
import { CameraCapture } from "../components/CameraCapture";
import { FilePicker } from "../components/FilePicker";
import { JobProgress } from "../components/JobProgress";
import { useJobPoller } from "../hooks/useJobPoller";
import type { FieldMapping, Template } from "../types";

type OcrStep = 1 | 2 | 3 | 4;

const OCR_STEPS = [
  { number: 1 as const, title: "母牛を指定", caption: "個体識別番号" },
  { number: 2 as const, title: "型を選択", caption: "テンプレート" },
  { number: 3 as const, title: "画像を準備", caption: "撮影・アップロード" },
  { number: 4 as const, title: "確認・実行", caption: "読み取り開始" },
];

export function normalizeMotherIndividualId(value: string): string {
  return value
    .normalize("NFKC")
    .replace(/\D/g, "")
    .slice(0, 10);
}

export function OcrPage({
  templates,
  rememberJob,
  openReview,
  openTemplates,
  onNavigationGuardChange,
}: {
  templates: Template[];
  rememberJob: (id: string) => void;
  openReview: (id: string) => void;
  openTemplates: () => void;
  onNavigationGuardChange?: (shouldWarn: boolean) => void;
}) {
  const activeVersions = useMemo(
    () => templates.flatMap((template) => template.versions.filter((version) => version.status === "ACTIVE").map((version) => ({ template, version }))),
    [templates],
  );
  const versions = useMemo(
    () => activeVersions.filter(({ version }) => version.quality.layoutVerified === true),
    [activeVersions],
  );
  const unverifiedVersions = useMemo(
    () => activeVersions.filter(({ version }) => version.quality.layoutVerified !== true),
    [activeVersions],
  );
  const [versionId, setVersionId] = useState(versions[0]?.version.id ?? "");
  const [step, setStep] = useState<OcrStep>(1);
  const [motherIndividualId, setMotherIndividualId] = useState("");
  const [motherTouched, setMotherTouched] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [mappingPreview, setMappingPreview] = useState<FieldMapping[] | null>(null);
  const [mappingValid, setMappingValid] = useState(false);
  const [mappingWarnings, setMappingWarnings] = useState<string[]>([]);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [mappingRetryToken, setMappingRetryToken] = useState(0);
  const creatingRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);
  const activeSelection = versions.find((item) => item.version.id === versionId);
  const activeVersion = activeSelection?.version;
  const normalizedMotherId = normalizeMotherIndividualId(motherIndividualId);
  const motherIdValid = /^\d{10}$/.test(normalizedMotherId);
  const mappedReadingReady = !enabled || (
    Boolean(mappingPreview?.length)
    && mappingValid
  );
  useEffect(() => {
    if (!versions.some((item) => item.version.id === versionId)) {
      idempotencyKeyRef.current = null;
      setVersionId(versions[0]?.version.id ?? "");
    }
  }, [versionId, versions]);
  useEffect(() => {
    const shouldWarn = (
      !jobId
      && (busy || step > 1 || motherIndividualId.length > 0 || file !== null)
    );
    onNavigationGuardChange?.(shouldWarn);
    return () => onNavigationGuardChange?.(false);
  }, [
    busy,
    file,
    jobId,
    motherIndividualId,
    onNavigationGuardChange,
    step,
  ]);
  useEffect(() => {
    let cancelled = false;
    setMappingPreview(null);
    setMappingValid(false);
    setMappingWarnings([]);
    setMappingError(null);
    if (!versionId) return;
    void api.fieldMappings(versionId)
      .then((response) => {
        if (!cancelled) {
          setMappingPreview(response.mappings.filter((mapping) => mapping.enabled));
          setMappingValid(response.validForOcr);
          setMappingWarnings(response.warnings);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setMappingError(
            error instanceof ApiError ? error.message : "意味づけを確認できませんでした。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [mappingRetryToken, versionId]);
  const { job, connectionLost } = useJobPoller(jobId, (terminal) => {
    if (terminal.status === "REVIEW_REQUIRED") openReview(terminal.id);
  });
  return (
    <div className="page-stack narrow">
      <header className="page-header">
        <span className="eyebrow">NEW READING</span>
        <h1>台帳を読み取る</h1>
        <p>母牛、テンプレート、画像の順に確認してから読み取りを開始します。</p>
      </header>
      {versions.length === 0 ? (
        <div className="empty-state large">
          <span>型</span>
          {unverifiedVersions.length > 0 ? (
            <>
              <h2>テンプレートの設定確認が必要です</h2>
              <p>
                読み取り欄が変更されたテンプレートは、確認が終わるまで使用できません。
                テンプレート設定で「記入欄を再検出」を実行してください。
              </p>
              <button type="button" className="primary" onClick={openTemplates}>
                テンプレート設定を開く
              </button>
            </>
          ) : (
            <>
              <h2>有効なテンプレートがありません</h2>
              <p>先にテンプレートを準備して承認してください。</p>
              <button type="button" className="primary" onClick={openTemplates}>
                テンプレートを準備する
              </button>
            </>
          )}
        </div>
      ) : (
        <>
          {unverifiedVersions.length > 0 && (
            <aside className="notice compact action-message" role="status">
              <span>
                <strong>設定確認が必要なテンプレートが {unverifiedVersions.length} 件あります</strong>
                <small>
                  読み取り欄を変更したテンプレートは選択できません。
                  設定画面で「記入欄を再検出」すると使用できます。
                </small>
              </span>
              <button type="button" className="secondary compact" onClick={openTemplates}>
                テンプレート設定を開く
              </button>
            </aside>
          )}
          <ol className="ocr-step-progress" aria-label="読み取り手順">
            {OCR_STEPS.map((item) => (
              <li
                key={item.number}
                className={item.number < step ? "completed" : item.number === step ? "current" : ""}
                aria-current={item.number === step ? "step" : undefined}
              >
                <span>{item.number < step ? "✓" : item.number}</span>
                <div><strong>{item.title}</strong><small>{item.caption}</small></div>
              </li>
            ))}
          </ol>

          <section className="form-card ocr-step-card">
            {step === 1 && (
              <section className="ocr-step-panel" aria-labelledby="ocr-step-mother">
                <div className="ocr-step-heading">
                  <span>STEP 1</span>
                  <h2 id="ocr-step-mother">この繁殖台帳の母牛を指定</h2>
                  <p>読み取り対象を明確にするため、母牛の個体識別番号を手入力してください。</p>
                </div>
                <label className="field-label mother-id-field">
                  母牛の個体識別番号
                  <input
                    aria-label="母牛の個体識別番号"
                    value={motherIndividualId}
                    inputMode="numeric"
                    autoComplete="off"
                    maxLength={10}
                    pattern="[0-9]{10}"
                    placeholder="例: 1234567890"
                    aria-invalid={motherTouched && !motherIdValid}
                    onBlur={() => setMotherTouched(true)}
                    onChange={(event) => {
                      idempotencyKeyRef.current = null;
                      setMotherIndividualId(normalizeMotherIndividualId(event.target.value));
                      setMessage(null);
                    }}
                  />
                  <small>数字10桁で入力してください。11桁目以降は入力されません。</small>
                </label>
                {motherTouched && !motherIdValid && (
                  <p className="error-box">個体識別番号を10桁の数字で入力してください。</p>
                )}
                <aside className="notice compact mother-id-notice">
                  <strong>この番号を保存先の個体として使用します</strong>
                  <span>帳票上の番号をOCRした場合も、ここで入力した番号を初期値として優先します。確認画面では修正できます。</span>
                </aside>
                <div className="ocr-step-actions">
                  <span />
                  <button
                    type="button"
                    className="primary"
                    disabled={!motherIdValid}
                    onClick={() => {
                      setMotherTouched(true);
                      if (motherIdValid) setStep(2);
                    }}
                  >
                    テンプレート選択へ
                  </button>
                </div>
              </section>
            )}

            {step === 2 && (
              <section className="ocr-step-panel" aria-labelledby="ocr-step-template">
                <div className="ocr-step-heading">
                  <span>STEP 2</span>
                  <h2 id="ocr-step-template">使用するテンプレートを確認</h2>
                  <p>どの意味づけ済みテンプレートで読み取るかを選択します。</p>
                </div>
                <label className="field-label">
                  使用するテンプレート
                  <select value={versionId} onChange={(event) => {
                    idempotencyKeyRef.current = null;
                    setVersionId(event.target.value);
                  }}>
                    {versions.map(({ template, version }) => (
                      <option key={version.id} value={version.id}>
                        {template.name} — v{version.versionNumber} · {version.id.slice(0, 8)}
                      </option>
                    ))}
                    {unverifiedVersions.length > 0 && (
                      <optgroup label="設定確認が必要（選択できません）">
                        {unverifiedVersions.map(({ template, version }) => (
                          <option key={version.id} value={version.id} disabled>
                            {template.name} — v{version.versionNumber} · 記入欄の再検出が必要
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                </label>
                {activeSelection && (
                  <div className="template-selection-preview">
                    <div className="template-preview-head">
                      <div>
                        <span>選択中のテンプレート</span>
                        <strong>{activeSelection.template.name} · v{activeSelection.version.versionNumber}</strong>
                        <small>ID {activeSelection.version.id.slice(0, 8)} · {new Date(activeSelection.version.createdAt).toLocaleDateString("ja-JP")}</small>
                      </div>
                      <b>{mappingPreview?.length ?? "…"} 項目</b>
                    </div>
                    {mappingPreview === null ? mappingError ? (
                      <div className="mapping-load-error" role="alert">
                        <p className="inline-warning">{mappingError}</p>
                        <button
                          type="button"
                          className="secondary compact"
                          onClick={() => setMappingRetryToken((value) => value + 1)}
                        >
                          もう一度読み込む
                        </button>
                      </div>
                    ) : (
                      <p>セルの意味づけを読み込んでいます…</p>
                    ) : mappingPreview.length === 0 ? (
                      <p className="inline-warning">このテンプレートには読み取りセルの意味づけがありません。</p>
                    ) : (
                      <>
                        <p className="template-mapping-policy">
                          表示中の記入セル矩形だけをOpenAIへ送信し、mappingIdでJSON項目へ固定対応します。ラベルセルと帳票全体は送信しません。
                        </p>
                        <div className="template-mapping-chips">
                          {mappingPreview.slice(0, 12).map((mapping) => (
                            <span key={mapping.mappingId} title={mapping.targetPath}>
                              {mapping.label}<small>{mapping.targetPath} · {mapping.mappingId}</small>
                            </span>
                          ))}
                          {mappingPreview.length > 12 && <em>ほか {mappingPreview.length - 12} 項目</em>}
                        </div>
                        {mappingWarnings.map((warning) => (
                          <p className="inline-warning" key={warning}>{warning}</p>
                        ))}
                      </>
                    )}
                  </div>
                )}
                <div className="ocr-step-actions">
                  <button type="button" className="secondary" onClick={() => setStep(1)}>戻る</button>
                  <button
                    type="button"
                    className="primary"
                    disabled={!versionId || mappingPreview === null || !mappedReadingReady}
                    onClick={() => setStep(3)}
                  >
                    画像の準備へ
                  </button>
                </div>
              </section>
            )}

            {step === 3 && (
              <section className="ocr-step-panel" aria-labelledby="ocr-step-image">
                <div className="ocr-step-heading">
                  <span>STEP 3</span>
                  <h2 id="ocr-step-image">読み取る帳票画像を準備</h2>
                  <p>画像ファイルを選択するか、カメラで帳票を撮影してください。</p>
                </div>
                <div className="input-source-tabs">
                  <div><strong>帳票画像</strong><small>テスト段階では画像ファイルの利用を推奨します。</small></div>
                  <button type="button" className="secondary" onClick={() => setCameraOpen(true)}>カメラを起動</button>
                </div>
                <FilePicker multiple={false} onChange={(files) => {
                  idempotencyKeyRef.current = null;
                  setFile(files[0] ?? null);
                }} label="PNGなどの帳票画像を選択" />
                {file && <p className="selected-file">選択中: <strong>{file.name}</strong></p>}
                <label className="switch-row">
                  <span><strong>文字読み取りも実行する</strong><small>オフにすると画像処理と領域切り出しだけを行います。</small></span>
                  <input type="checkbox" checked={enabled} onChange={(event) => {
                    idempotencyKeyRef.current = null;
                    setEnabled(event.target.checked);
                  }} /><i />
                </label>
                {!enabled && <aside className="notice compact"><strong>画像処理のみ</strong><span>結果欄は空欄として作成され、手入力できます。</span></aside>}
                <div className="ocr-step-actions">
                  <button type="button" className="secondary" onClick={() => setStep(2)}>戻る</button>
                  <button type="button" className="primary" disabled={!file} onClick={() => setStep(4)}>内容確認へ</button>
                </div>
              </section>
            )}

            {step === 4 && (
              <section className="ocr-step-panel" aria-labelledby="ocr-step-confirm">
                <div className="ocr-step-heading">
                  <span>STEP 4</span>
                  <h2 id="ocr-step-confirm">読み取り内容を確認</h2>
                  <p>対象の母牛と使用するテンプレートを確認してから開始してください。</p>
                </div>
                <dl className="ocr-reading-summary">
                  <div><dt>母牛の個体識別番号</dt><dd>{normalizedMotherId}</dd></div>
                  <div><dt>テンプレート</dt><dd>{activeSelection ? `${activeSelection.template.name} · v${activeSelection.version.versionNumber}` : "未選択"}</dd></div>
                  <div><dt>帳票画像</dt><dd>{file?.name ?? "未選択"}</dd></div>
                  <div><dt>処理内容</dt><dd>{enabled ? "手書き文字を読み取る" : "画像処理のみ"}</dd></div>
                </dl>
                <aside className="notice compact">
                  <strong>{normalizedMotherId} の繁殖台帳として処理します</strong>
                  <span>読み取り後の確認・確定を経て、この個体識別番号に紐づけてDBへ保存します。</span>
                </aside>
                {message && <p className="error-box">{message}</p>}
                <div className="ocr-step-actions">
                  <button type="button" className="secondary" disabled={busy || Boolean(jobId)} onClick={() => setStep(3)}>戻る</button>
                  <button
                    className="primary"
                    disabled={!file || !versionId || !motherIdValid || busy || !mappedReadingReady || Boolean(jobId)}
                    onClick={async () => {
                      if (!file || !motherIdValid || creatingRef.current || jobId) return;
                      creatingRef.current = true;
                      const idempotencyKey = idempotencyKeyRef.current
                        ?? createIdempotencyKey("ocr");
                      idempotencyKeyRef.current = idempotencyKey;
                      setBusy(true);
                      setMessage(null);
                      try {
                        const created = await api.createOcr(
                          versionId,
                          normalizedMotherId,
                          file,
                          enabled,
                          idempotencyKey,
                        );
                        idempotencyKeyRef.current = null;
                        setJobId(created.jobId);
                        rememberJob(created.jobId);
                      } catch (error) {
                        setMessage(error instanceof ApiError ? error.message : "開始できませんでした。");
                      } finally {
                        creatingRef.current = false;
                        setBusy(false);
                      }
                    }}
                  >
                    {busy ? "送信中…" : jobId ? "読み取りを開始しました" : enabled ? "読み取りを開始" : "画像処理を開始"}
                  </button>
                </div>
              </section>
            )}
          </section>
        </>
      )}
      <JobProgress job={job} connectionLost={connectionLost} />
      {cameraOpen && <CameraCapture
        documentAspect={(activeVersion?.canonicalWidth ?? 1400) / (activeVersion?.canonicalHeight ?? 990)}
        onCapture={(captured) => {
          idempotencyKeyRef.current = null;
          setFile(captured);
          setCameraOpen(false);
        }}
        onClose={() => setCameraOpen(false)}
      />}
    </div>
  );
}
