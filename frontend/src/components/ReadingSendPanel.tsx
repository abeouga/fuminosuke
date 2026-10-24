import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FiCheck } from "react-icons/fi";

/** What was just handed to the reading queue: up to 3 images (null = Excel) and how many in all. */
export type SentReading = { id: number; count: number; images: (string | null)[] };

// The stacked papers lean a little apart, like a small pile.
const REST = [
  { x: 0, y: 0, r: 0 },
  { x: 10, y: -8, r: 5 },
  { x: -10, y: -12, r: -6 },
];

function transform(x: number, y: number, s: number, r: number) {
  return `translate(${x}px, ${y}px) scale(${s}) rotate(${r}deg)`;
}

function Paper({ image }: { image: string | null }) {
  return image ? <img src={image} alt="" draggable={false} /> : <span className="reading-send-xlsx">Excel</span>;
}

/**
 * Step 3 for the queued reading: the papers lift off and fly into 「読み取り状況」 at the top right,
 * then a check confirms the hand-off. Calls onDone when it is over (about 2.3 seconds).
 */
export function ReadingSendPanel({ sent, onDone }: { sent: SentReading; onDone: () => void }) {
  const slot = useRef<HTMLDivElement | null>(null);
  const flyers = useRef<(HTMLDivElement | null)[]>([]);
  const [origin, setOrigin] = useState<DOMRect | null>(null);
  const [arrived, setArrived] = useState(false);
  const done = useRef(onDone);
  done.current = onDone;
  const papers = sent.images.slice(0, REST.length);

  useLayoutEffect(() => {
    if (slot.current) setOrigin(slot.current.getBoundingClientRect());
  }, []);

  useEffect(() => {
    if (!origin) return;
    let cancelled = false;
    const timers: number[] = [];
    const animations: Animation[] = [];
    const finish = () => {
      if (cancelled) return;
      setArrived(true);
      timers.push(window.setTimeout(() => { if (!cancelled) done.current(); }, 1100));
    };
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const pill = document.querySelector<HTMLElement>(".reading-float-pill");
    if (reduced || typeof Element.prototype.animate !== "function") {
      finish();
    } else {
      const target = pill?.getBoundingClientRect();
      const tx = target ? target.left + target.width / 2 : window.innerWidth - 90;
      const ty = target ? target.top + target.height / 2 : 28;
      const dx = tx - (origin.left + origin.width / 2);
      const dy = ty - (origin.top + origin.height / 2);
      const end = Math.max(0.08, 30 / origin.width);
      flyers.current.slice(0, papers.length).forEach((element, index) => {
        if (!element) return;
        const rest = REST[index];
        const animation = element.animate([
          { transform: transform(rest.x, rest.y + 18, 0.9, rest.r), opacity: 0, easing: "cubic-bezier(.2,.8,.3,1)" },
          { transform: transform(rest.x, rest.y, 1, rest.r), opacity: 1, offset: 0.24, easing: "ease-in-out" },
          // Wind-up: a short dip before the throw, like Outlook's send.
          { transform: transform(rest.x, rest.y + 12, 0.95, rest.r + 2), opacity: 1, offset: 0.38, easing: "cubic-bezier(.5,0,.8,.4)" },
          { transform: transform(dx * 0.4, dy * 0.4 - 80, (1 + end) / 2, -12), opacity: 1, offset: 0.7, easing: "cubic-bezier(.3,0,.9,.6)" },
          { transform: transform(dx, dy, end, -20), opacity: 0, offset: 1 },
        ], { duration: 1200, delay: index * 90, fill: "forwards" });
        animations.push(animation);
      });
      const last = animations[animations.length - 1];
      if (!last) finish();
      else last.finished.then(() => {
        if (cancelled) return;
        pill?.animate([
          { transform: "scale(1)" }, { transform: "scale(1.14)" }, { transform: "scale(1)" },
        ], { duration: 380, easing: "ease-out" });
        finish();
      }, () => undefined);
    }
    return () => {
      cancelled = true;
      timers.forEach((timer) => window.clearTimeout(timer));
      animations.forEach((animation) => animation.cancel());
    };
    // The flight is played once for this hand-off.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [origin]);

  const extra = sent.count - papers.length;
  return (
    <section className="form-card ocr-step-card reading-send-panel" aria-live="polite">
      <div className="reading-send-stage">
        <div ref={slot} className="reading-send-slot" aria-hidden="true" />
        {arrived && <span className="reading-send-check" aria-hidden="true"><FiCheck /></span>}
      </div>
      <div className="reading-send-text">
        <strong>{arrived ? "読み取りに送りました" : "読み取りに送っています…"}</strong>
        <small className={arrived ? "is-shown" : undefined}>
          {sent.count}件 ·右上の「読み取り状況」で進み具合を確認できます
        </small>
      </div>
      {origin && createPortal(
        <div className="reading-send-flight" aria-hidden="true">
          {papers.map((image, index) => (
            <div key={index} ref={(element) => { flyers.current[index] = element; }}
              className="reading-send-paper"
              style={{
                left: origin.left, top: origin.top, width: origin.width, height: origin.height,
                zIndex: REST.length - index, opacity: 0,
              }}>
              <Paper image={image} />
              {index === 0 && extra > 0 && <span className="reading-send-more">+{extra}</span>}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </section>
  );
}
