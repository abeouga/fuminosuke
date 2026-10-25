/**
 * The on-screen keyboard on a phone. Closing it (the back button, the keyboard's own "hide" key,
 * a swipe) leaves the focus in the text field, so a page that lays itself out for typing would stay
 * that way with the keyboard's room left empty; iOS also leaves the page scrolled up by the
 * keyboard's height. When the keyboard goes away the field gives up the focus and the page is
 * scrolled back into range, for every text field at once.
 *
 * The keyboard is told from the visible height: Chrome for Android (interactive-widget=
 * resizes-content) shrinks the window, iOS Safari only the visual viewport.
 */

/** Less than this much lost height is the browser's own bars, not a keyboard. */
const KEYBOARD_MIN = 120;

export function isTextEntry(element: Element | null): boolean {
  if (!(element instanceof HTMLElement)) return false;
  if (element.isContentEditable || element instanceof HTMLTextAreaElement) return true;
  if (!(element instanceof HTMLInputElement)) return false;
  return !["button", "checkbox", "radio", "file", "range", "color", "submit", "reset", "image"].includes(element.type);
}

/** The height the page can be seen in right now, in CSS pixels; a pinch zoom does not count. */
export function visibleHeight(): number {
  const viewport = window.visualViewport;
  return viewport ? Math.min(window.innerHeight, viewport.height * viewport.scale) : window.innerHeight;
}

let fullHeight = 0;
let fullWidth = 0;
let open = false;

export function phoneKeyboardOpen(): boolean {
  return open;
}

function check() {
  const height = visibleHeight();
  // A rotation or a changed zoom gives the window a new full height.
  if (Math.abs(window.innerWidth - fullWidth) > 1) {
    fullWidth = window.innerWidth;
    fullHeight = height;
  }
  fullHeight = Math.max(fullHeight, height);
  const now = height < fullHeight - KEYBOARD_MIN;
  if (now === open) return;
  open = now;
  document.documentElement.classList.toggle("phone-keyboard", open);
  if (!open) {
    const active = document.activeElement;
    if (isTextEntry(active)) (active as HTMLElement).blur();
    // iOS keeps the page scrolled up by the keyboard's height after it has gone.
    window.requestAnimationFrame(() => {
      const most = Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
      if (window.scrollY > most) window.scrollTo(window.scrollX, most);
    });
  }
  window.dispatchEvent(new Event("phone-keyboard"));
}

export function installPhoneKeyboard() {
  fullWidth = window.innerWidth;
  fullHeight = visibleHeight();
  window.addEventListener("resize", check);
  window.visualViewport?.addEventListener("resize", check);
}
