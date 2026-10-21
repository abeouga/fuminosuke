import { useEffect, useRef, useState } from "react";
import { usePhoneDevice } from "../hooks/usePhoneDevice";
import { prepareUploadImage } from "./phonePhoto";
import { Spinner } from "./Spinner";

const ACCEPT = "image/png,image/jpeg,image/webp,image/tiff";
const LABEL = "帳票画像を選択またはドロップ";

export function OcrImagePicker({ file, onChange, allowXlsx = false }: {
  file: File | null;
  onChange: (file: File | null) => void;
  allowXlsx?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const phoneDevice = usePhoneDevice();
  const [preparing, setPreparing] = useState(false);
  const [preview, setPreview] = useState<{ file: File; url: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!file || /\.xlsx$/i.test(file.name)) { setPreview(null); return; }
    const url = URL.createObjectURL(file);
    setPreview({ file, url });
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const select = (files: File[]) => {
    if (files.length === 0) return;
    if (files.length !== 1) { setError("帳票画像は1件ずつ指定してください。"); return; }
    const next = files[0];
    if (!(allowXlsx && /\.xlsx$/i.test(next.name)) && !ACCEPT.split(",").includes(next.type.toLowerCase()) && !/\.(png|jpe?g|webp|tiff?)$/i.test(next.name)) {
      setError("対応していないファイル形式です。PNG、JPEG、WebP、TIFFを指定してください。");
      return;
    }
    setError("");
    if (allowXlsx && /.xlsx$/i.test(next.name)) { onChange(next); return; }
    setPreparing(true);
    void prepareUploadImage(next).then(onChange, () => onChange(next)).finally(() => setPreparing(false));
  };
  return (
    <div className="ocr-image-picker">
      <div className={`ocr-image-surface${file ? " has-file" : ""}${dragging ? " dragging" : ""}`}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault(); event.stopPropagation(); setDragging(false);
          select(Array.from(event.dataTransfer.files ?? []));
        }}>
        <label className="ocr-image-target">
          <input ref={inputRef} type="file" accept={allowXlsx ? ACCEPT + ",.xlsx" : ACCEPT}
            aria-label={file ? "帳票画像を置き換える" : LABEL}
            onChange={(event) => {
              select(Array.from(event.target.files ?? []));
              event.target.value = "";
            }} />
          {file ? (/\.xlsx$/i.test(file.name) ? <><strong>記入済みExcel</strong><small>登録したテンプレートと同じセル配置のファイルを読み取ります。</small></> : preview?.file === file && <img src={preview.url} alt="選択した帳票画像" />) : <>
            <span className="drop-icon" aria-hidden="true">＋</span>
            <strong>{allowXlsx ? "帳票画像または記入済みExcelを選択" : LABEL}</strong>
            <small>PNG / JPEG / WebP / TIFF{allowXlsx ? " / XLSX" : ""}・最大20 MB</small>
          </>}
        </label>
        {file && <button type="button" className="ocr-image-remove" aria-label="画像の選択を取り消す"
          onClick={() => {
            setError(""); setDragging(false); onChange(null);
            if (inputRef.current) { inputRef.current.value = ""; inputRef.current.focus(); }
          }}><span aria-hidden="true">×</span></button>}
      </div>
      {phoneDevice && <label className="secondary ocr-camera-button">
        <input ref={cameraRef} type="file" accept={ACCEPT} capture="environment" aria-label="カメラで撮影"
          onChange={(event) => {
            select(Array.from(event.target.files ?? []));
            event.target.value = "";
          }} />
        カメラで撮影
      </label>}
      {phoneDevice && <p className="ocr-photo-tips">書類全体が写るように、なるべく真上から撮影してください。少し暗い・斜め・傾いた写真でも読み取れます。</p>}
      {preparing && <p className="ocr-photo-tips" role="status"><Spinner />画像を準備しています…</p>}
      {file && <small className="ocr-image-name" title={file.name}>{file.name}</small>}
      {error && <p className="file-picker-error" role="alert">{error}</p>}
    </div>
  );
}
