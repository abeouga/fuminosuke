import "./insecureContext";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthBoundary } from "./auth/AuthBoundary";
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

// Before the first paint, so a zoomed or large-text phone never shows the page at the wrong size.
if (isPhoneDevice()) {
  document.documentElement.classList.add("is-phone");
  installPhoneViewport();
  installPhoneKeyboard();
}

installChunkReload();
installVersionWatch();
createRoot(document.getElementById("root")!).render(<StrictMode><AuthBoundary><App /></AuthBoundary></StrictMode>);
