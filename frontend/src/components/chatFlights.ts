// Motion for images sent from the chat, matching the reading screen's send animation:
// attached images fly from the message box into the conversation, and once a reading is in the
// reading queue a small copy is thrown in an arc into 「読み取り状況」 at the top right.

type Source = { rect: DOMRect; src: string };

// A hidden tab pauses frames, which would keep the sent image hidden; it then simply appears.
const reduced = () => document.hidden || (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);

function layer() {
  let root = document.querySelector<HTMLElement>(".chat-flight-layer");
  if (!root) {
    root = document.createElement("div");
    root.className = "chat-flight-layer";
    root.setAttribute("aria-hidden", "true");
    document.body.appendChild(root);
  }
  return root;
}

function paper(src: string, rect: { left: number; top: number; width: number; height: number }) {
  const element = document.createElement("div");
  element.className = "chat-flight-paper";
  Object.assign(element.style, {
    left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`,
  });
  const image = document.createElement("img");
  image.src = src;
  image.alt = "";
  image.draggable = false;
  element.appendChild(image);
  layer().appendChild(element);
  return element;
}

/** Where each attached image is in the message box, taken just before the message is sent. */
export function captureAttachments(): Map<string, Source> {
  const found = new Map<string, Source>();
  document.querySelectorAll<HTMLElement>(".ask-attachment[data-attachment-id]").forEach((item) => {
    const image = item.querySelector("img");
    if (image) found.set(item.dataset.attachmentId!, { rect: image.getBoundingClientRect(), src: image.src });
  });
  return found;
}

const ease = (t: number) => 1 - Math.pow(1 - t, 3);

/**
 * Flies each sent image from the message box to its place in the conversation. The target is read
 * on every frame, so the conversation may scroll while the image is in the air.
 */
export function flyIntoConversation(sources: Map<string, Source>) {
  if (sources.size === 0 || reduced()) return;
  let index = 0;
  sources.forEach((source, id) => {
    const target = document.querySelector<HTMLElement>(`[data-reading-image="${CSS.escape(id)}"]`);
    if (!target) return;
    // An image sent with text hides only itself; the text bubble beside it stays.
    const bubble = target.closest<HTMLElement>(".ask-turn-image") ?? target.closest<HTMLElement>(".ask-msg") ?? target;
    bubble.classList.add("is-flying-in");
    const flyer = paper(source.src, source.rect);
    const delay = index++ * 70;
    const duration = 460;
    let begin = 0;
    const step = (now: number) => {
      if (!begin) begin = now + delay;
      const t = Math.min(Math.max((now - begin) / duration, 0), 1);
      const to = target.getBoundingClientRect();
      if (!target.isConnected) { flyer.remove(); return; }
      const e = ease(t);
      const left = source.rect.left + (to.left - source.rect.left) * e;
      const top = source.rect.top + (to.top - source.rect.top) * e - Math.sin(Math.PI * t) * 36;
      Object.assign(flyer.style, {
        left: `${left}px`, top: `${top}px`,
        width: `${source.rect.width + (to.width - source.rect.width) * e}px`,
        height: `${source.rect.height + (to.height - source.rect.height) * e}px`,
      });
      if (t < 1) { requestAnimationFrame(step); return; }
      bubble.classList.remove("is-flying-in");
      flyer.remove();
    };
    requestAnimationFrame(step);
  });
}

function transform(x: number, y: number, s: number, r: number) {
  return `translate(${x}px, ${y}px) scale(${s}) rotate(${r}deg)`;
}

/** Throws a copy of the image into 「読み取り状況」, then the button bounces as it lands. */
export function flyToReadingQueue(image: HTMLImageElement) {
  if (reduced() || typeof Element.prototype.animate !== "function") return;
  const from = image.getBoundingClientRect();
  if (from.width === 0) return;
  const pill = document.querySelector<HTMLElement>(".reading-float-pill");
  const target = pill?.getBoundingClientRect();
  const tx = target ? target.left + target.width / 2 : window.innerWidth - 90;
  const ty = target ? target.top + target.height / 2 : 28;
  const dx = tx - (from.left + from.width / 2);
  const dy = ty - (from.top + from.height / 2);
  const end = Math.max(0.08, 26 / from.width);
  const flyer = paper(image.src, from);
  const animation = flyer.animate([
    { transform: transform(0, 0, 1, 0), opacity: 0.95, easing: "ease-in-out" },
    // Wind-up: a short dip before the throw, like the reading screen (and Outlook's send).
    { transform: transform(0, 10, 0.94, 2), opacity: 1, offset: 0.18, easing: "cubic-bezier(.5,0,.8,.4)" },
    { transform: transform(dx * 0.4, dy * 0.4 - 70, (1 + end) / 2, -12), opacity: 1, offset: 0.62, easing: "cubic-bezier(.3,0,.9,.6)" },
    { transform: transform(dx, dy, end, -20), opacity: 0, offset: 1 },
  ], { duration: 1000, fill: "forwards" });
  animation.finished.then(() => {
    flyer.remove();
    pill?.animate([{ transform: "scale(1)" }, { transform: "scale(1.14)" }, { transform: "scale(1)" }],
      { duration: 380, easing: "ease-out" });
  }, () => flyer.remove());
}
