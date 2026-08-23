import { useEffect, useState } from "react";

export function FilePicker({
  multiple,
  onChange,
  label = "画像を選択",
  accept = "image/png,image/jpeg,image/webp,image/tiff",
  help = "PNG / JPEG / WebP / TIFF・最大20 MB",
}: {
  multiple: boolean;
  onChange: (files: File[]) => void;
  label?: string;
  accept?: string;
  help?: string;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<Array<{ index: number; url: string }>>([]);
  useEffect(() => {
    const next = files.flatMap((file, index) => (
      file.type.startsWith("image/") ? [{ index, url: URL.createObjectURL(file) }] : []
    ));
    setPreviews(next);
    return () => next.forEach((preview) => URL.revokeObjectURL(preview.url));
  }, [files]);
  return (
    <div>
      <label className="drop-zone">
        <input
          type="file"
          accept={accept}
          capture={!multiple && accept.startsWith("image/") ? "environment" : undefined}
          multiple={multiple}
          onChange={(event) => {
            const selected = Array.from(event.target.files ?? []);
            setFiles(selected);
            onChange(selected);
          }}
        />
        <span className="drop-icon" aria-hidden="true">＋</span>
        <strong>{label}</strong>
        <small>{help}</small>
      </label>
      {files.length > 0 && previews.length === 0 && (
        <div className="selected-file-list" aria-label="選択したファイル">
          {files.map((file) => <span key={`${file.name}:${file.size}`}>{file.name}</span>)}
        </div>
      )}
      {previews.length > 0 && (
        <div className="thumb-grid">
          {previews.map((preview) => (
            <figure key={preview.url}>
              <img src={preview.url} alt={`選択画像 ${preview.index + 1}`} />
              <figcaption>{files[preview.index]?.name}</figcaption>
            </figure>
          ))}
        </div>
      )}
    </div>
  );
}
