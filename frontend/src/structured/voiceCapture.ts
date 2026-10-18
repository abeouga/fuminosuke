import type { MicVAD } from "@ricky0123/vad-web";
import { acquireMicrophone, Pcm24k } from "../assistant/audioStream";

export type VoiceState = { mode: "stopped" | "starting" | "listening"; message: string; caption: string };
type Utterance = { id: string; frames: ArrayBuffer[]; ready: boolean; samples: number; segmentSamples: number; segment: string | null; manifest: string[]; pcm: Pcm24k; sealed: boolean };
const SILENCE_MS = 1200;

/** PCM acquisition for questions. Completed transcripts are handed to the caller as text. */
export class QuestionVoiceCapture {
  private state: VoiceState = { mode: "stopped", message: "", caption: "" };
  private listeners = new Set<() => void>();
  private epoch = 0;
  private mic: MediaStream | null = null;
  private vad: MicVAD | null = null;
  private socket: WebSocket | null = null;
  private active: Utterance | null = null;
  private ring: Float32Array[] = [];
  private speaking = false;
  private waitingSpeech = false;
  private stoppedAt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  constructor(private onTranscript: (text: string) => void) {}
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.state;
  private set(patch: Partial<VoiceState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(fn => fn()); }
  private send(value: object | ArrayBuffer) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > 262144) throw new Error("音声の送信が追いついていません。再開してください。");
    this.socket.send(value instanceof ArrayBuffer ? value : JSON.stringify(value));
  }
  private fail(error: unknown) {
    void this.stop();
    const denied = error instanceof Error && (error.name === "NotAllowedError" || /permission denied/i.test(error.message));
    const missing = error instanceof Error && error.name === "NotFoundError";
    this.set({ message: denied ? "マイクの使用が許可されていません。ブラウザーの設定で許可してください。" : missing ? "マイクが見つかりません。" : error instanceof Error ? error.message : "音声取得を停止しました。" });
  }
  async start() {
    await this.stop(); const epoch = this.epoch;
    this.set({ mode: "starting", message: "音声接続を準備しています。", caption: "" });
    try {
      const mic = await acquireMicrophone(() => epoch === this.epoch);
      if (epoch !== this.epoch) { mic.getTracks().forEach(t => t.stop()); return; }
      this.mic = mic;
      const { MicVAD } = await import("@ricky0123/vad-web");
      if (epoch !== this.epoch) return;
      const vad = await MicVAD.new({ model: "v5", startOnLoad: false, redemptionMs: 256, minSpeechMs: 250, preSpeechPadMs: 256,
        baseAssetPath: "/voice-assets/", onnxWASMBasePath: "/voice-assets/", processorType: "AudioWorklet",
        getStream: async () => mic, pauseStream: async s => { s.getTracks().forEach(t => t.stop()); },
        resumeStream: async () => { throw new Error("音声入力を再開してください。"); },
        ortConfig: ort => { ort.env.logLevel = "error"; ort.env.wasm.numThreads = 1; ort.env.wasm.proxy = false; },
        onFrameProcessed: (_p, frame) => { if (epoch === this.epoch) try { this.frame(frame); } catch (e) { this.fail(e); } },
        onSpeechStart: () => { if (epoch === this.epoch) try { this.speechStart(); } catch (e) { this.fail(e); } },
        onSpeechEnd: () => { if (epoch === this.epoch) this.speechEnd(); },
        onVADMisfire: () => { if (epoch === this.epoch) this.speechEnd(); },
      });
      if (epoch !== this.epoch) { await vad.destroy(); return; }
      this.vad = vad; await this.connect(epoch);
      if (epoch !== this.epoch) return;
      this.set({ mode: "listening", message: "質問を話してください。" }); await vad.start();
    } catch (error) { if (epoch === this.epoch) this.fail(error); }
  }
  private async connect(epoch: number) {
    const url = new URL("/api/v2/structured/voice", location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url); this.socket = socket;
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => { reject(new Error("音声接続の準備が期限を超えました。")); }, 15000);
      socket.onmessage = e => {
        if (epoch !== this.epoch) return;
        try {
          const event = JSON.parse(String(e.data));
          if (event.type === "ready") { clearTimeout(timeout); resolve(); }
          else if (event.type === "error") { throw new Error(event.message ?? "音声取得に失敗しました。"); }
          else this.event(event);
        } catch (error) { clearTimeout(timeout); reject(error); this.fail(error); }
      };
      socket.onerror = () => { clearTimeout(timeout); reject(new Error("音声接続に失敗しました。")); };
      socket.onclose = () => { clearTimeout(timeout); reject(new Error("音声接続が終了しました。")); if (epoch === this.epoch) this.fail(new Error("音声接続が終了しました。再開してください。")); };
    });
  }
  private event(event: { type: string; utteranceId?: string; delta?: string; text?: string }) {
    const item = this.active;
    if (!item || event.utteranceId !== item.id) return;
    if (event.type === "utterance.ready") {
      item.ready = true;
      for (const frame of item.frames.splice(0)) this.sendPcm(item, frame);
      if (!this.speaking) this.commitSegment(item);
    } else if (event.type === "transcript.delta") {
      this.set({ caption: (this.state.caption + (event.delta ?? "")).slice(-20000) }); this.scheduleBoundary();
    } else if (event.type === "transcript.completed") {
      this.active = null; this.set({ caption: "", message: "質問を話してください。" });
      const text = (event.text ?? "").trim();
      if (text) this.onTranscript(text);
      if (this.waitingSpeech) {
        const speaking = this.speaking; this.waitingSpeech = false;
        try { this.speechStart(); if (!speaking) this.speechEnd(); } catch (e) { this.fail(e); }
      } else this.ring = [];
    }
  }
  private frame(frame: Float32Array) {
    const item = this.active;
    if (!item || item.sealed || !this.speaking) {
      this.ring.push(frame.slice());
      // Only a speech onset may extend the pre-roll into the bounded waiting buffer.
      const limit = item?.sealed && this.waitingSpeech ? 16000 * 15 : 4608;
      while (this.ring.reduce((n, f) => n + f.length, 0) > limit) {
        if (limit > 4608) throw new Error("認識が遅延しています。音声入力を再開してください。");
        this.ring.shift();
      }
      return;
    }
    const pcm = item.pcm.push(frame); item.samples += pcm.byteLength / 2;
    if (item.samples > 24_000 * 180) throw new Error("1回の質問は3分以内にしてください。");
    if (item.ready) this.sendPcm(item, pcm);
    else { item.frames.push(pcm); if (item.samples > 24000 * 5) throw new Error("音声の受付が遅延しています。"); }
  }
  private sendPcm(item: Utterance, pcm: ArrayBuffer) {
    if (!pcm.byteLength) return;
    if (!item.segment) { item.segment = crypto.randomUUID(); item.manifest.push(item.segment); item.segmentSamples = 0; this.send({ type: "segment.begin", segmentId: item.segment, sequence: item.manifest.length }); }
    this.send(pcm); item.segmentSamples += pcm.byteLength / 2;
    if (item.segmentSamples >= 24_000 * 25) this.commitSegment(item);
  }
  private commitSegment(item: Utterance) {
    if (!item.segment) return;
    // Upstream commits require at least 100 ms, including the last short tail.
    if (item.segmentSamples < 2400) { this.send(new ArrayBuffer((2400 - item.segmentSamples) * 2)); item.segmentSamples = 2400; }
    this.send({ type: "segment.end", segmentId: item.segment }); item.segment = null;
  }
  private speechStart() {
    this.speaking = true; if (this.timer) clearTimeout(this.timer); this.timer = null;
    if (this.active) {
      if (this.active.sealed) this.waitingSpeech = true;
      else for (const frame of this.ring.splice(0)) this.frame(frame);
      return;
    }
    const item: Utterance = { id: crypto.randomUUID(), frames: [], ready: false, samples: 0, segmentSamples: 0, segment: null, manifest: [], pcm: new Pcm24k(), sealed: false };
    this.active = item; this.set({ caption: "", message: "聞き取り中…" });
    for (const frame of this.ring.splice(0)) this.frame(frame);
    this.send({ type: "utterance.begin", utteranceId: item.id });
  }
  private speechEnd() {
    this.speaking = false; this.stoppedAt = performance.now();
    if (!this.active || this.active.sealed) return;
    try { if (this.active.ready) this.commitSegment(this.active); this.send({ type: "speech.stopped" }); } catch (error) { this.fail(error); return; }
    this.set({ message: "音声を整理中…" }); this.scheduleBoundary();
  }
  private scheduleBoundary() {
    if (this.state.mode !== "listening" || this.speaking || !this.active || this.active.sealed) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      if (!this.active?.ready || performance.now() - this.stoppedAt < SILENCE_MS) { this.scheduleBoundary(); return; }
      try { this.seal(); } catch (error) { this.fail(error); }
    }, Math.max(50, Math.ceil(SILENCE_MS - (performance.now() - this.stoppedAt))));
  }
  private seal() {
    const item = this.active; if (!item || !item.ready || item.sealed) return;
    if (!item.samples) { this.send({ type: "utterance.cancel" }); this.active = null; return; }
    this.commitSegment(item); item.sealed = true; this.ring = [];
    this.send({ type: "utterance.end", manifest: item.manifest });
  }
  async stop() {
    this.epoch++; if (this.timer) clearTimeout(this.timer); this.timer = null; this.waitingSpeech = false;
    this.mic?.getTracks().forEach(t => t.stop()); this.mic = null;
    const socket = this.socket; this.socket = null;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "capture.stop" })); socket?.close();
    const vad = this.vad; this.vad = null; this.active = null; this.ring = []; this.speaking = false;
    this.set({ mode: "stopped", message: "", caption: "" }); await vad?.destroy();
  }
}
