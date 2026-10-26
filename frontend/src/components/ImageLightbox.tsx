import { useEffect, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { FiX } from "react-icons/fi";

export type LightboxBox = { key: string; left: number; top: number; width: number; height: number; active: boolean };

/** What the lightbox shows: an image, and the answer's cells marked on it (in % of the image). */
export type LightboxImage = { src: string; alt: string; boxes?: LightboxBox[] };

/** The image over the whole screen, above the top bar and the tabs; the × or the backdrop closes it. */
export function ImageLightbox({ image, onClose }: { image: LightboxImage; onClose: () => void }) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", close, true);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", close, true); document.body.style.overflow = overflow; };
  }, [onClose]);
  return createPortal(
    <div className="image-lightbox" role="dialog" aria-modal="true" aria-label={image.alt} onClick={onClose}>
      <div className="image-lightbox-stage" onClick={(event) => event.stopPropagation()}>
        <img src={image.src} alt={image.alt} draggable={false} />
        {image.boxes?.map((box) => <span key={box.key} aria-hidden="true" className={`image-lightbox-box${box.active ? " active" : ""}`}
          style={{ left: `${box.left}%`, top: `${box.top}%`, width: `${box.width}%`, height: `${box.height}%` } as CSSProperties} />)}
      </div>
      <button type="button" className="image-lightbox-close" aria-label="閉じる" onClick={onClose}><FiX aria-hidden="true" /></button>
    </div>,
    document.body,
  );
}
