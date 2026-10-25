/**
 * The page size on a phone, set through the viewport meta tag.
 *
 * Large-text phones (らくらくスマートフォン and Android phones with a large font) enlarge pages in
 * two ways, measured on Chrome 145 for Android:
 * - Chrome's "既定のズーム" (Settings > Accessibility) zooms the whole page; the layout gets
 *   narrower (133% on a 412px phone leaves 309px). A page cannot turn this off with CSS.
 * - The Android font size does not change web pages at all; it is only readable as
 *   `env(preferred-text-scale)`.
 *
 * Here both become one display scale: the user's larger of the two (the font size capped), and
 * never laid out narrower than MIN_LAYOUT. `width=<n>` in the meta tag is interpreted in zoomed
 * pixels, so a width of layout × zoom keeps the layout at `layout` CSS pixels and lets Chrome fit
 * it to the screen. CSS `zoom` on the root was tried and rejected: vh/vw units and media queries
 * stop agreeing with the layout.
 */

/** The narrowest layout the phone screens are laid out at; wider zoom is shrunk back to this. */
export const MIN_LAYOUT = 340;
/** The most the Android font size enlarges the pages. */
export const MAX_TEXT_SCALE = 1.3;

export type PhoneDisplay = {
  /** Chrome's page zoom (1 = 100%). */
  pageZoom: number;
  /** The Android font size (1 = standard). */
  textScale: number;
  /** CSS pixels the page is laid out at. */
  layoutWidth: number;
  /** How much larger than standard the page appears. */
  scale: number;
};

let current: PhoneDisplay | null = null;
let expectedScale = 1;
let lastWidth = 0;

export function phoneDisplay(): PhoneDisplay | null {
  return current;
}

/** The Android (or iOS) text size the user chose, 1 when the browser does not report it. */
export function osTextScale(): number {
  const probe = document.createElement("div");
  probe.style.cssText = "position:absolute;visibility:hidden;width:calc(100px * env(preferred-text-scale, 1));height:0";
  document.body.appendChild(probe);
  const width = probe.getBoundingClientRect().width;
  probe.remove();
  const scale = width / 100;
  return Number.isFinite(scale) && scale > 0.5 && scale < 4 ? scale : 1;
}

function metaTag(): HTMLMetaElement {
  let meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "viewport";
    document.head.appendChild(meta);
  }
  return meta;
}

function apply() {
  const viewport = window.visualViewport;
  // The width the screen shows at scale 1, in zoomed CSS pixels (does not depend on our meta tag).
  const visible = window.innerWidth * (viewport?.scale ?? 1);
  const screenWidth = window.screen.width;
  if (!(visible > 0) || !(screenWidth > 0)) return;
  const pageZoom = screenWidth / visible;
  const textScale = osTextScale();
  // A zoom below 100% is the user's choice too; the font size only ever enlarges.
  const wanted = textScale > 1.01 ? Math.max(pageZoom, Math.min(textScale, MAX_TEXT_SCALE)) : pageZoom;
  const layoutWidth = Math.round(Math.max(screenWidth / wanted, Math.min(MIN_LAYOUT, screenWidth)));
  const width = Math.round(layoutWidth * pageZoom);
  // Laid out at the width the screen shows anyway: the ordinary tag.
  const plain = Math.abs(layoutWidth - visible) < 2;
  const content = (plain ? "width=device-width, initial-scale=1" : `width=${width}`)
    + ", viewport-fit=cover, interactive-widget=resizes-content";
  const meta = metaTag();
  if (meta.content !== content) meta.content = content;
  expectedScale = visible / layoutWidth;
  lastWidth = visible;
  current = { pageZoom, textScale, layoutWidth, scale: screenWidth / layoutWidth };
  const root = document.documentElement;
  root.dataset.phoneZoom = pageZoom.toFixed(2);
  root.dataset.phoneTextScale = textScale.toFixed(2);
  root.dataset.phoneLayout = String(layoutWidth);
}

/** Sets the page size now and again whenever the zoom, font size or orientation changes. */
export function installPhoneViewport() {
  apply();
  const recheck = () => {
    const viewport = window.visualViewport;
    const scale = viewport?.scale ?? 1;
    // A pinch zoom changes the scale; only a changed zoom setting or rotation moves the width.
    if (Math.abs(scale - expectedScale) > 0.03) return;
    if (Math.abs(window.innerWidth * scale - lastWidth) < 8) return;
    apply();
  };
  window.addEventListener("resize", recheck);
  window.addEventListener("orientationchange", () => window.setTimeout(apply, 300));
  // The Android font size is changed in the system settings, away from the page.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) apply(); });
}
