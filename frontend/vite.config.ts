import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const vadDirectory = dirname(require.resolve("@ricky0123/vad-web"));
const ortDirectory = dirname(require.resolve("onnxruntime-web/wasm"));
const voiceAssets = {
  "silero_vad_v5.onnx": join(vadDirectory, "silero_vad_v5.onnx"),
  "vad.worklet.bundle.min.js": join(vadDirectory, "vad.worklet.bundle.min.js"),
  "ort-wasm-simd-threaded.mjs": join(ortDirectory, "ort-wasm-simd-threaded.mjs"),
  "ort-wasm-simd-threaded.wasm": join(ortDirectory, "ort-wasm-simd-threaded.wasm"),
  "THIRD_PARTY_NOTICES.txt": fileURLToPath(new URL("./src/assistant/THIRD_PARTY_NOTICES.txt", import.meta.url)),
};

const runtime = globalThis as typeof globalThis & {
  process?: { env?: Record<string, string | undefined> };
};
const backendUrl =
  runtime.process?.env?.LLM_OCR_BACKEND_URL ?? "http://127.0.0.1:8000";

// Origins allowed to embed the /embed page (Agorack's screen), separated by spaces or commas.
// The dev server allows the local Agorack by default; a build allows none unless it is set.
const LOCAL_EMBED_ORIGINS = "http://127.0.0.1:5174 http://localhost:5174";
function embedOrigins(command: string): string[] {
  const listed = runtime.process?.env?.FUMINOSUKE_EMBED_ORIGINS ?? (command === "serve" ? LOCAL_EMBED_ORIGINS : "");
  return listed.split(/[\s,]+/).filter((origin) => /^https?:\/\/[^/\s]+$/.test(origin));
}

/** Only /embed may be framed, and only by the allowed origins; every other page by no one. */
function frameHeaders(origins: string[]) {
  return (request: { url?: string }, response: { setHeader: (name: string, value: string) => void }, next: () => void) => {
    const path = request.url?.split("?")[0];
    if (path === "/embed") {
      response.setHeader("Content-Security-Policy", `frame-ancestors ${origins.length ? origins.join(" ") : "'none'"}`);
    } else {
      response.setHeader("Content-Security-Policy", "frame-ancestors 'none'");
      response.setHeader("X-Frame-Options", "DENY");
    }
    next();
  };
}

export default defineConfig(({ command }) => ({
  define: { __EMBED_ORIGINS__: JSON.stringify(embedOrigins(command)) },
  plugins: [react(), {
    name: "frame-headers",
    configureServer(server) { server.middlewares.use(frameHeaders(embedOrigins(command))); },
    configurePreviewServer(server) { server.middlewares.use(frameHeaders(embedOrigins(command))); },
  }, {
    name: "fixed-local-voice-assets",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const name = request.url?.split("?")[0].replace(/^\/voice-assets\//, "");
        if (!request.url?.startsWith("/voice-assets/") || !name || !Object.prototype.hasOwnProperty.call(voiceAssets, name)) return next();
        response.setHeader("Content-Type", name.endsWith(".wasm") ? "application/wasm" : name.endsWith(".mjs") || name.endsWith(".js") ? "text/javascript" : "application/octet-stream");
        response.end(readFileSync(voiceAssets[name as keyof typeof voiceAssets]));
      });
    },
    generateBundle() {
      for (const [name, path] of Object.entries(voiceAssets)) this.emitFile({ type: "asset", fileName: `voice-assets/${name}`, source: readFileSync(path) });
    },
  }],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: backendUrl, ws: true } },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
}));
