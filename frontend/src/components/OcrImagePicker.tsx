import { useEffect, useRef, useState, type ReactNode } from "react";
import { FiCamera, FiImage } from "react-icons/fi";
import { usePhoneDevice } from "../hooks/usePhoneDevice";
import { prepareUploadImage } from "./phonePhoto";
import { Spinner } from "./Spinner";

const ACCEPT = "image/png,image/jpeg,image/webp,image/tiff";
const LABEL = "帳票画像を選択またはドロップ";
/** Any image: Android's Chrome then opens its photo picker (it has no camera; the sheet offers that). */
const PHOTO_ACCEPT = "image/*";
/** One batch at most; the server's job queue holds 100. */
export const MAX_BATCH_FILES = 30;

/**
 * Picks the filled-in forms to read. With `multiple`, every chosen or dropped file is added to the
 * list (each becomes its own document); without it, a new choice replaces the one file.
 */
export function OcrImagePicker({ files, onChange, allowXlsx = false, multiple = false }: {
  files: File[];
  onChange: (files: File[]) => void;
  allowXlsx?: boolean;
  multiple?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const phoneDevice = usePhoneDevice();
  const [preparing, setPreparing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const [sheet, setSheet] = useState(false);
  const previews = useObjectUrls(files);
  const single = files.length === 1 ? files[0] : null;
  const accepted = (next: File) => (allowXlsx && /\.xlsx$/i.test(next.name))
    || ACCEPT.split(",").includes(next.type.toLowerCase()) || /\.(png|jpe?g|webp|tiff?)$/i.test(next.name);
  const select = (chosen: File[]) => {
    if (chosen.length === 0) return;
    if (!multiple && chosen.length !== 1) { setError("帳票画像は1件ずつ指定してください。"); return; }
    const usable = chosen.filter(accepted);
    if (usable.length === 0) {
      setError("対応していないファイル形式です。PNG、JPEG、WebP、TIFFを指定してください。");
      return;
    }
    const base = multiple ? files : [];
    const fresh = usable.filter((next) => !base.some((kept) => sameFile(kept, next)));
    const room = MAX_BATCH_FILES - base.length;
    const skipped = chosen.length - usable.length;
    setError([
      skipped > 0 ? `対応していない形式の${skipped}件は除きました。` : "",
      fresh.length > room ? `一度に読み取れるのは${MAX_BATCH_FILES}件までです。` : "",
    ].join(""));
    const adding = fresh.slice(0, Math.max(0, room));
    if (adding.length === 0) return;
    setPreparing(true);
    void Promise.all(adding.map((next) => allowXlsx && /\.xlsx$/i.test(next.name)
      ? Promise.resolve(next)
      : prepareUploadImage(next).catch(() => next)))
      .then((prepared) => onChange([...base, ...prepared]))
      .finally(() => setPreparing(false));
  };
  const remove = (target: File) => {
    setError("");
    onChange(files.filter((item) => item !== target));
    inputRef.current?.focus();
  };
  const input = (
    <input ref={inputRef} type="file" accept={allowXlsx ? ACCEPT + ",.xlsx" : ACCEPT} multiple={multiple}
      aria-label={files.length === 0 ? LABEL : multiple ? "帳票画像を追加" : "帳票画像を置き換える"}
      onChange={(event) => {
        select(Array.from(event.target.files ?? []));
        event.target.value = "";
      }} />
  );
  /**
   * On a computer the box is the file input itself. On a phone it opens a small sheet: the camera,
   * or the phone's own photo picker (no single system screen offers both on Android).
   */
  const target = (children: ReactNode, label: string) => phoneDevice
    ? <button type="button" className="ocr-image-target" aria-label={label} aria-haspopup="dialog" onClick={() => setSheet(true)}>{children}</button>
    : <label className="ocr-image-target">{input}{children}</label>;
  const chosen = (event: React.ChangeEvent<HTMLInputElement>) => {
    setSheet(false);
    select(Array.from(event.target.files ?? []));
    event.target.value = "";
  };
  return (
    <div className="ocr-image-picker">
      <div className={`ocr-image-surface${files.length > 0 ? " has-file" : ""}${files.length > 1 ? " has-files" : ""}${dragging ? " dragging" : ""}`}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault(); event.stopPropagation(); setDragging(false);
          select(Array.from(event.dataTransfer.files ?? []));
        }}>
        {files.length > 1 ? (
          <ul className="ocr-image-grid" aria-label="読み取る画像">
            {files.map((item, index) => (
              <li key={`${item.name}:${item.size}:${item.lastModified}:${index}`}>
                {/\.xlsx$/i.test(item.name)
                  ? <span className="ocr-image-grid-xlsx">Excel</span>
                  : previews.get(item) && <img src={previews.get(item)} alt={item.name} />}
                <small title={item.name}>{index + 1}. {item.name}</small>
                <button type="button" className="ocr-image-grid-remove" aria-label={`「${item.name}」を外す`}
                  onClick={() => remove(item)}><span aria-hidden="true">×</span></button>
              </li>
            ))}
            {files.length < MAX_BATCH_FILES && (
              <li className="ocr-image-grid-add">
                {target(<>
                  <span className="drop-icon" aria-hidden="true">＋</span>
                  <strong>追加</strong>
                </>, "帳票画像を追加")}
              </li>
            )}
          </ul>
        ) : (
          target(<>
            {single ? (/\.xlsx$/i.test(single.name)
              ? <><strong>記入済みExcel</strong><small>登録したテンプレートと同じセル配置のファイルを読み取ります。</small></>
              : previews.get(single) && <img src={previews.get(single)} alt="選択した帳票画像" />) : <>
              <span className="drop-icon" aria-hidden="true">＋</span>
              <strong>{phoneDevice ? "タップして撮影または写真を選ぶ" : allowXlsx ? "帳票画像または記入済みExcelを選択" : LABEL}</strong>
              {!phoneDevice && <small>PNG / JPEG / WebP / TIFF{allowXlsx ? " / XLSX" : ""}・最大20 MB{multiple ? `・まとめて${MAX_BATCH_FILES}件まで` : ""}</small>}
            </>}
          </>, files.length === 0 ? "撮影または写真を選ぶ" : "帳票画像を置き換える")
        )}
        {single && <button type="button" className="ocr-image-remove" aria-label="画像の選択を取り消す"
          onClick={() => {
            setError(""); setDragging(false); onChange([]);
            if (inputRef.current) { inputRef.current.value = ""; inputRef.current.focus(); }
          }}><span aria-hidden="true">×</span></button>}
      </div>
      {phoneDevice && sheet && <PhotoSourceSheet multiple={multiple} onChosen={chosen} onClose={() => setSheet(false)} />}
      {preparing && <p className="ocr-photo-tips" role="status"><Spinner />画像を準備しています…</p>}
      {single && <small className="ocr-image-name" title={single.name}>{single.name}</small>}
      {files.length > 1 && <small className="ocr-image-name">{files.length}件の書類を選択中（1枚が1件の書類として読み取られます）</small>}
      {error && <p className="file-picker-error" role="alert">{error}</p>}
    </div>
  );
}

/** 「カメラで撮影」 or 「写真を選ぶ」, rising from the bottom of the phone screen. */
function PhotoSourceSheet({ multiple, onChosen, onClose }: {
  multiple: boolean;
  onChosen: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);
  return (
    <div className="photo-source-backdrop" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="photo-source-sheet" role="dialog" aria-modal="true" aria-label="画像の選び方">
        <label className="photo-source-option">
          <input type="file" accept={ACCEPT} capture="environment" onChange={onChosen} />
          <FiCamera aria-hidden="true" />カメラで撮影
        </label>
        <label className="photo-source-option">
          <input type="file" accept={PHOTO_ACCEPT} multiple={multiple} onChange={onChosen} />
          <FiImage aria-hidden="true" />写真を選ぶ
        </label>
        <button type="button" className="photo-source-cancel" onClick={onClose}>やめる</button>
      </section>
    </div>
  );
}

function sameFile(first: File, second: File) {
  return first.name === second.name && first.size === second.size && first.lastModified === second.lastModified;
}

/** Object URLs for the image files; a file that stays in the list keeps its URL, a removed one is released. */
function useObjectUrls(files: File[]) {
  const cache = useRef(new Map<File, string>());
  const [urls, setUrls] = useState<Map<File, string>>(new Map());
  useEffect(() => {
    const kept = new Set(files);
    for (const [item, url] of cache.current) {
      if (!kept.has(item)) { URL.revokeObjectURL(url); cache.current.delete(item); }
    }
    for (const item of files) {
      if (!cache.current.has(item) && !/\.xlsx$/i.test(item.name)) cache.current.set(item, URL.createObjectURL(item));
    }
    setUrls(new Map(cache.current));
  }, [files]);
  useEffect(() => () => {
    cache.current.forEach((url) => URL.revokeObjectURL(url));
    cache.current.clear();
  }, []);
  return urls;
}
