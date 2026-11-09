import "./insecureContext";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthBoundary } from "./auth/AuthBoundary";
import { EmbedPage } from "./embed/EmbedPage";
import { installVersionWatch } from "./appVersion";
import { installChunkReload } from "./components/PageErrorBoundary";
import { isPhoneDevice } from "./hooks/usePhoneDevice";
import { installPhoneKeyboard } from "./phone/phoneKeyboard";
import { installPhoneViewport } from "./phone/phoneViewport";
import "./styles.css";
import "./candidate-editor.css";
import "./workbench.css";
import "./ask.css";
import "./shelf.css";
import "./assistant/assistant.css";
import "./mobile.css";
import "./phone/phone.css";
import "./embed/embed.css";

// The page shown inside another application's frame: the document viewer only, none of the app around it.
const embedded = window.location.pathname === "/embed";

// Before the first paint, so a zoomed or large-text phone never shows the page at the wrong size.
if (embedded) {
  document.documentElement.classList.add("embed-root");
} else if (isPhoneDevice()) {
  document.documentElement.classList.add("is-phone");
  installPhoneViewport();
  installPhoneKeyboard();
}

installChunkReload();
if (!embedded) installVersionWatch();
createRoot(document.getElementById("root")!).render(<StrictMode><AuthBoundary>{embedded ? <EmbedPage /> : <App />}</AuthBoundary></StrictMode>);
