import type { MicVAD } from "@ricky0123/vad-web";
import { acquireMicrophone, Pcm24k } from "../assistant/audioStream";
import { LevelMeter } from "../components/VoiceMeter";
import { bindingOf, type WorkbenchStore } from "./workbenchStore";
import type { CommandResult, PageScope } from "./v2.generated";

type State = { mode: "stopped" | "starting" | "continuous" | "local"; message: string; caption: string; error: string; utterance: boolean };
type Utterance = { id: string; frames: ArrayBuffer[]; ready: boolean; samples: number; segmentSamples: number; segment: string | null; manifest: string[]; pcm: Pcm24k; sealed: boolean; started: number };
// Same incomplete-tail policy as the server. A delta can extend, never shorten, a boundary.
export function silenceDelay(text: string) { return /(?:それから|あと|ではなく|じゃなく|違う|えーと|と、?|を、?)\s*$/.test(text) ? 3000 : 1200; }

/** PCM acquisition only. The server owns transcripts, instruction ordering and writes. */
export class VoiceCapture {
  private state: State = { mode: "stopped", message: "", caption: "", error: "", utterance: false };
  private listeners = new Set<() => void>();
  readonly meter = new LevelMeter();
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
  private deadline: ReturnType<typeof setTimeout> | null = null;
  private localFinishing = false;
  constructor(private store: WorkbenchStore) {}
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.state;
  private set(patch: Partial<State>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(fn => fn()); }
  private send(value: object | ArrayBuffer) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > 262144) throw new Error("音声の送信が追いついていません。再開してください。");
    this.socket.send(value instanceof ArrayBuffer ? value : JSON.stringify(value));
  }
  // The reason a capture ended stays visible until the next start.
  private fail(error: unknown) { void this.stop(error instanceof Error ? error.message : "音声取得を停止しました。"); }
  async start(mode: "continuous" | "local", page: PageScope, entities: string[], table: string | null) {
    await this.stop(); const epoch = this.epoch;
    this.set({ mode: "starting", message: "音声接続を準備しています。", caption: "", error: "" });
    try {
      await this.store.audioContext(mode, page, entities, table);
      if (epoch !== this.epoch) return;
      const mic = await acquireMicrophone(() => epoch === this.epoch);
      if (epoch !== this.epoch) { mic.getTracks().forEach(t => t.stop()); return; }
      this.mic = mic;
      const { MicVAD } = await import("@ricky0123/vad-web");
      if (epoch !== this.epoch) return;
      const vad = await MicVAD.new({ model: "v5", startOnLoad: false, redemptionMs: 600, minSpeechMs: 250, preSpeechPadMs: 256,
        baseAssetPath: "/voice-assets/", onnxWASMBasePath: "/voice-assets/", processorType: "AudioWorklet",
        getStream: async () => mic, pauseStream: async s => { s.getTracks().forEach(t => t.stop()); },
        resumeStream: async () => { throw new Error("音声入力を再開してください。"); },
        ortConfig: ort => { ort.env.logLevel = "error"; ort.env.wasm.numThreads = 1; ort.env.wasm.proxy = false; },
        onFrameProcessed: (_p, frame) => { if (epoch === this.epoch) try { this.meter.push(frame); this.frame(frame); } catch (e) { this.fail(e); } },
        onSpeechStart: () => { if (epoch === this.epoch) void this.speechStart(epoch).catch(e => this.fail(e)); },
        onSpeechEnd: () => { if (epoch === this.epoch) this.speechEnd(); },
        onVADMisfire: () => { if (epoch === this.epoch) this.speechEnd(); },
      });
      if (epoch !== this.epoch) { await vad.destroy(); return; }
      this.vad = vad; await this.connect(epoch);
      if (epoch !== this.epoch) return;
      this.set({ mode, message: "音声入力中です。" }); await vad.start();
    } catch (error) { if (epoch === this.epoch) this.fail(error); }
  }
  private async connect(epoch: number) {
    const owner = this.store.getOwner(), url = new URL(`/api/v2/editing/sessions/${owner.sessionId}/voice`, location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url); this.socket = socket;
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => { reject(new Error("音声接続の準備が期限を超えました。")); }, 15000);
      socket.onopen = () => { if (epoch === this.epoch) socket.send(JSON.stringify({ type: "auth", token: owner.token })); else socket.close(); };
      socket.onmessage = e => {
        if (epoch !== this.epoch) return;
        try {
          const event = JSON.parse(String(e.data));
          if (event.type === "ready") { clearTimeout(timeout); resolve(); }
          else if (event.type === "error") { throw new Error(event.message ?? "音声取得に失敗しました。"); }
          else this.event(event, epoch);
        } catch (error) { clearTimeout(timeout); reject(error); this.fail(error); }
      };
      socket.onerror = () => { clearTimeout(timeout); reject(new Error("音声接続に失敗しました。")); };
      socket.onclose = () => { clearTimeout(timeout); reject(new Error("音声接続が終了しました。")); if (epoch === this.epoch) this.fail(new Error("音声接続が終了しました。再開してください。")); };
    });
  }
  private event(event: { type: string; utteranceId?: string; delta?: string; text?: string; message?: string; result?: CommandResult }, epoch: number) {
    const item = this.active;
    const finished = event.type === "command.accepted" && event.result
      || ((event.type === "utterance.ignored" || event.type === "command.rejected") && item && event.utteranceId === item.id);
    if (finished) {
      if (event.type === "command.accepted" && event.result) this.store.acceptCommand(event.result);
      this.active = null; this.set({ utterance: false });
      if (this.deadline) clearTimeout(this.deadline); this.deadline = null;
      if (this.localFinishing) { void this.stop(event.type === "command.accepted" ? "" : event.message ?? ""); return; }
      // Frames captured during final transcription are retained, not submitted twice.
      this.set(event.type === "command.accepted" ? { message: "音声入力中です。" } : { message: event.message ?? "この指示は実行できませんでした。", caption: "" });
      if (this.waitingSpeech) {
        const speaking = this.speaking; this.waitingSpeech = false;
        void this.speechStart(epoch).then(() => { if (!speaking) this.speechEnd(); }).catch(e => this.fail(e));
      } else this.ring = [];
      return;
    }
    if (!item || event.utteranceId !== item.id) return;
    if (event.type === "potentialContinuation") {
      item.ready = true;
      for (const frame of item.frames.splice(0)) this.sendPcm(item, frame);
      if (!this.speaking) this.commitSegment(item);
      if (this.localFinishing) this.seal();
    } else if (event.type === "transcript.delta") {
      this.set({ caption: (this.state.caption + (event.delta ?? "")).slice(-20000) }); this.scheduleBoundary();
    } else if (event.type === "transcript.completed") this.set({ caption: event.text ?? "" });
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
    if (item.samples > 24_000 * 180) throw new Error("1回の指示は3分以内にしてください。");
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
  private async speechStart(epoch: number) {
    this.speaking = true; if (this.timer) clearTimeout(this.timer); this.timer = null;
    if (this.active) {
      if (this.active.sealed) this.waitingSpeech = true;
      else for (const frame of this.ring.splice(0)) this.frame(frame);
      return;
    }
    const item: Utterance = { id: crypto.randomUUID(), frames: [], ready: false, samples: 0, segmentSamples: 0, segment: null, manifest: [], pcm: new Pcm24k(), sealed: false, started: performance.now() };
    this.active = item; this.set({ caption: "", message: "聞き取り中…", utterance: true });
    const pad = this.ring.splice(0); for (const frame of pad) this.frame(frame);
    const context = await this.store.refreshContext();
    if (epoch !== this.epoch || item !== this.active) return;
    this.send({ type: "utterance.begin", utteranceId: item.id, binding: bindingOf(context) });
    this.deadline = setTimeout(() => this.fail(new Error("1回の指示は3分以内にしてください。")), 179000);
  }
  private speechEnd() {
    this.speaking = false; this.stoppedAt = performance.now();
    if (!this.active || this.active.sealed) return;
    try { if (this.active.ready) this.commitSegment(this.active); this.send({ type: "speech.stopped" }); } catch (error) { this.fail(error); return; }
    this.set({ message: "音声を受信・指示を整理中" }); this.scheduleBoundary();
  }
  private scheduleBoundary() {
    if (this.state.mode !== "continuous" || this.speaking || !this.active || this.active.sealed) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      // Browser timers can fire before the fractional delay has fully elapsed.
      // Recheck the clock instead of claiming more silence than was observed.
      if (!this.active?.ready || performance.now() - this.stoppedAt < silenceDelay(this.state.caption)) { this.scheduleBoundary(); return; }
      try { this.seal(); } catch (error) { this.fail(error); }
    }, Math.max(50, Math.ceil(silenceDelay(this.state.caption) - (performance.now() - this.stoppedAt))));
  }
  private seal() {
    const item = this.active; if (!item || !item.ready || item.sealed) return;
    if (!item.samples) { this.send({ type: "utterance.cancel" }); this.active = null; this.set({ utterance: false, message: "音声入力中です。" }); return; }
    this.commitSegment(item); item.sealed = true; this.ring = [];
    this.send({ type: "utterance.end", manifest: item.manifest, reason: this.state.mode === "local" ? "local_stop" : "silence", silenceMs: this.state.mode === "local" ? 0 : Math.min(30000, Math.floor(performance.now() - this.stoppedAt)) });
  }
  clearError() { if (this.state.error) this.set({ error: "" }); }
  finishLocal() { if (this.state.mode !== "local") return; this.localFinishing = true; this.mic?.getTracks().forEach(t => t.stop()); try { if (!this.active) void this.stop(); else this.seal(); } catch (e) { this.fail(e); } }
  async stop(reason = "") {
    this.epoch++; if (this.timer) clearTimeout(this.timer); if (this.deadline) clearTimeout(this.deadline);
    this.timer = null; this.deadline = null; this.waitingSpeech = false; this.mic?.getTracks().forEach(t => t.stop()); this.mic = null;
    const socket = this.socket; this.socket = null;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "capture.stop" })); socket?.close();
    const vad = this.vad; this.vad = null; this.active = null; this.ring = []; this.speaking = false; this.localFinishing = false;
    this.set({ mode: "stopped", message: "", caption: "", utterance: false, error: reason || (this.state.mode === "stopped" ? this.state.error : "") }); await vad?.destroy();
  }
}
