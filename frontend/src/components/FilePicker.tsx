import { useEffect, useState } from "react";

export function FilePicker({
  multiple,
  onChange,
  label = "画像を選択",
}: {
  multiple: boolean;
  onChange: (files: File[]) => void;
  label?: string;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);
  useEffect(() => {
    const urls = files.map((file) => URL.createObjectURL(file));
    setPreviews(urls);
    return () => urls.forEach(URL.revokeObjectURL);
  }, [files]);
  return (
    <div>
      <label className="drop-zone">
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp,image/tiff"
          capture={!multiple ? "environment" : undefined}
          multiple={multiple}
          onChange={(event) => {
            const selected = Array.from(event.target.files ?? []);
            setFiles(selected);
            onChange(selected);
          }}
        />
        <span className="drop-icon" aria-hidden="true">＋</span>
        <strong>{label}</strong>
        <small>PNG / JPEG / WebP / TIFF・最大20 MB</small>
      </label>
      {previews.length > 0 && (
        <div className="thumb-grid">
          {previews.map((preview, index) => (
            <figure key={preview}>
              <img src={preview} alt={`選択画像 ${index + 1}`} />
              <figcaption>{files[index]?.name}</figcaption>
            </figure>
          ))}
        </div>
      )}
    </div>
  );
}
