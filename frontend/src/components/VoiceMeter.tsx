import { useEffect, useRef } from "react";
import "./VoiceMeter.css";

const BARS = 18;

/** Live input level while voice input is on. Drawn outside React state to stay cheap. */
export function VoiceMeter({ read, active, speaking = false }: { read: () => number; active: boolean; speaking?: boolean }) {
  const bars = useRef<Array<HTMLSpanElement | null>>([]);
  useEffect(() => {
    if (!active) return;
    const history = new Array<number>(BARS).fill(0);
    let last = 0, frame = 0;
    const tick = (now: number) => {
      if (now - last >= 60) {
        last = now; history.shift(); history.push(read());
        history.forEach((value, i) => { const bar = bars.current[i]; if (bar) bar.style.transform = `scaleY(${(0.15 + 0.85 * value).toFixed(3)})`; });
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [active, read]);
  if (!active) return null;
  return <span className={speaking ? "voice-meter is-speaking" : "voice-meter"} aria-hidden="true">
    {Array.from({ length: BARS }, (_, i) => <span key={i} ref={el => { bars.current[i] = el; }} />)}
  </span>;
}

/** Peak-hold level (0..1) from 16 kHz PCM frames, decaying smoothly between words. */
export class LevelMeter {
  private value = 0;
  push(frame: Float32Array) {
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    const rms = Math.sqrt(sum / Math.max(1, frame.length));
    this.value = Math.max(Math.min(1, Math.sqrt(rms) * 2.4), this.value * 0.82);
  }
  read = () => { const v = this.value; this.value *= 0.92; return v; };
  reset() { this.value = 0; }
}
