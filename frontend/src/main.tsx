import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthBoundary } from "./auth/AuthBoundary";
import "./styles.css";
import "./candidate-editor.css";
import "./workbench.css";
import "./assistant/assistant.css";
import "./mobile.css";

createRoot(document.getElementById("root")!).render(<StrictMode><AuthBoundary><App /></AuthBoundary></StrictMode>);
