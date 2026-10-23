/** A small turning ring shown while a button or a status waits (「作成中…」「保存中…」…). */
export function Spinner() {
  return <span className="busy-spinner" aria-hidden="true" />;
}

/** A larger ring centered over an image area while the picture loads (or when it cannot be shown). */
export function ImageSpinner({ label }: { label?: string }) {
  return <span className="image-spinner" {...(label ? { role: "status", "aria-label": label } : { "aria-hidden": true })} />;
}
