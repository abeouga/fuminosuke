import { useEffect, useRef, useState } from "react";

export function FilePicker({
  multiple,
  onChange,
  label = "画像を選択",
  accept = "image/png,image/jpeg,image/webp,image/tiff",
  help = "PNG / JPEG / WebP / TIFF・最大20 MB",
  enableDrop = false,
  hideWhenChosen = false,
}: {
  multiple: boolean;
  onChange: (files: File[]) => void;
  label?: string;
  accept?: string;
  help?: string;
  enableDrop?: boolean;
  /** Once a file is chosen the picking area goes away; 「選び直す」 opens the file dialog again. */
  hideWhenChosen?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<Array<{ index: number; url: string }>>([]);
  const [dragging, setDragging] = useState(false);
  const [dropError, setDropError] = useState("");
  const selectFiles = (selected: File[]) => {
    setFiles(selected);
    setDropError("");
    onChange(selected);
  };
  const acceptsFile = (file: File) => {
    const rules = accept.split(",").map((rule) => rule.trim().toLowerCase()).filter(Boolean);
    const mediaType = file.type.toLowerCase();
    const fileName = file.name.toLowerCase();
    const knownExtensions: Record<string, string[]> = {
      "image/png": [".png"],
      "image/jpeg": [".jpg", ".jpeg"],
      "image/webp": [".webp"],
      "image/tiff": [".tif", ".tiff"],
    };
    return rules.length === 0 || rules.some((rule) => {
      if (rule.startsWith(".")) return fileName.endsWith(rule);
      if (rule.endsWith("/*")) return mediaType.startsWith(rule.slice(0, -1));
      return mediaType === rule || knownExtensions[rule]?.some((extension) => fileName.endsWith(extension));
    });
  };
  const handleDrop = (event: React.DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setDragging(false);
    const dropped = Array.from(event.dataTransfer.files ?? []);
    if (!multiple && dropped.length !== 1) {
      setDropError("帳票画像は1件ずつドロップしてください。");
      return;
    }
    if (dropped.length === 0) return;
    if (dropped.some((file) => !acceptsFile(file))) {
      setDropError("対応していないファイル形式です。PNG、JPEG、WebP、TIFFを指定してください。");
      return;
    }
    selectFiles(dropped);
  };
  useEffect(() => {
    const next = files.flatMap((file, index) => (
      file.type.startsWith("image/") ? [{ index, url: URL.createObjectURL(file) }] : []
    ));
    setPreviews(next);
    return () => next.forEach((preview) => URL.revokeObjectURL(preview.url));
  }, [files]);
  return (
    <div>
      <label
        className={`drop-zone${dragging ? " dragging" : ""}`}
        hidden={hideWhenChosen && files.length > 0}
        onDragEnter={enableDrop ? (event) => {
          event.preventDefault();
          setDragging(true);
        } : undefined}
        onDragOver={enableDrop ? (event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
        } : undefined}
        onDragLeave={enableDrop ? (event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
        } : undefined}
        onDrop={enableDrop ? handleDrop : undefined}
      >
        <input
          ref={input}
          type="file"
          accept={accept}
          capture={!multiple && accept.startsWith("image/") ? "environment" : undefined}
          multiple={multiple}
          onChange={(event) => {
            const selected = Array.from(event.target.files ?? []);
            selectFiles(selected);
          }}
        />
        <span className="drop-icon" aria-hidden="true">＋</span>
        <strong>{label}</strong>
        <small>{help}</small>
      </label>
      {dropError && <p className="file-picker-error" role="alert">{dropError}</p>}
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
      {hideWhenChosen && files.length > 0 && (
        <button type="button" className="secondary compact file-picker-again" onClick={() => input.current?.click()}>
          {multiple ? "選び直す" : "別のファイルを選ぶ"}
        </button>
      )}
    </div>
  );
}
