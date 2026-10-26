import { useId, type ReactNode } from "react";
import { FiInfo } from "react-icons/fi";

/**
 * A small 「i」 beside a heading: the explanation that used to sit under it opens on hover or
 * keyboard focus. `align` is the side the bubble lines up with (`end` under a right-aligned
 * title); `side` is where it opens (`above` for a bar at the bottom of the screen).
 */
export function InfoTip({ text, align = "start", side = "below" }: {
  text: ReactNode;
  align?: "start" | "end";
  side?: "below" | "above";
}) {
  const id = useId();
  return (
    <span className={`info-tip is-${align} is-${side}`}>
      <button type="button" className="info-tip-button" aria-label="説明" aria-describedby={id}>
        <FiInfo aria-hidden="true" focusable="false" />
      </button>
      <span className="info-tip-bubble" id={id} role="tooltip">{text}</span>
    </span>
  );
}
