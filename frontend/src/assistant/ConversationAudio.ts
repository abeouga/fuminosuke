import { editingClient, editingRequest, EditingError, type EditingOwner } from "../editing/client";
import type { PageScope } from "../editing/generated";
import type { EditingStore, SavedResource } from "../editing/store";
import { EditingConversation, type AudioCapture, type CapturedTurn } from "./conversation";
import { acquireMicrophone, cancelOnly, correction, Pcm24k } from "./audioStream";
import type { MicVAD } from "@ricky0123/vad-web";

type Item = { id: string; sequence: number; capture: AudioCapture | null; text: string | null; caption: string;
  classified: boolean; cancelled: boolean; finished: boolean; ready: boolean; frames: ArrayBuffer[];
  resampler: Pcm24k; samples: number; started: Promise<void>; deadline: ReturnType<typeof setTimeout> | null };
export type AudioState = { mode: "stopped" | "starting" | "continuous" | "local" | "processing"; message: string; caption: string };
type Ack = { resolve: () => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> };
const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
const base = (owner: EditingOwner) => `/sessions/${owner.sessionId}`;

/** Mic capture and serial mutations have independent lifecycles; feedback is visual. */
export class ConversationAudio {
  readonly conversation: EditingConversation;
  private epoch = 0;
  private state: AudioState = { mode: "stopped", message: "", caption: "" };
  private listeners = new Set<() => void>();
  private mic: MediaStream | null = null;
  private vad: MicVAD | null = null;
  private socket: WebSocket | null = null;
  private recorder: MediaRecorder | null = null;
  private localTurn: CapturedTurn | null = null;
  private recordingChunks: Blob[] = [];
  private recordingBytes = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private speaking = false;
  private active: Item | null = null;
  private items: Item[] = [];
  private processing = false;
  private sequence = 0;
  private acks = new Map<string, Ack>();
  private currentItem: Item | null = null;
  private disposed = false;
  private mounts = 0;
  private selectionKey: string | null = null;

  constructor(readonly store: EditingStore, saved: (value: SavedResource) => void | Promise<void>) {
    this.conversation = new EditingConversation(store, saved, () => this.waitCapture());
  }
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.state;
  mount() { this.mounts += 1; }
  unmount() { this.mounts -= 1; queueMicrotask(() => { if (this.mounts === 0) void this.dispose(); }); }
  private set(mode: AudioState["mode"], message = this.state.message, caption = this.state.caption) {
    if (this.disposed) return;
    this.state = { mode, message, caption }; this.listeners.forEach(fn => fn());
  }
  private current(epoch: number) { return !this.disposed && epoch === this.epoch; }
  private fail(error: unknown) {
    const message = error instanceof Error ? error.message : "音声取得を停止しました。";
    const stopped = this.stop(), epoch = this.epoch;
    void stopped.finally(() => { if (this.current(epoch)) this.set("stopped", message, ""); });
  }
  async selected(entities: string[], selectionEpoch: number) {
    const epoch = this.epoch;
    await this.conversation.capture();
    if (!this.current(epoch)) return;
    const context = this.conversation.getContext(), owner = this.conversation.getOwner();
    const found = await editingRequest<{ targets: Array<{ handle: string }> }>(`${base(owner)}/ui-targets`, owner,
      { contextRevision: context.contextRevision, resourceRevision: context.resourceRevision, entityIds: entities, handles: [] }, "POST");
    if (!this.current(epoch)) return;
    const boundary = await this.store.conversationBoundary();
    if (!this.current(epoch)) return;
    await this.conversation.updateContext({ pageScope: context.pageScope, selectedHandles: found.targets.map(target => target.handle),
      localEditEpoch: boundary.context.localEditEpoch, selectionEpoch: Math.max(selectionEpoch, context.selectionEpoch + 1) });
    if (this.current(epoch)) this.selectionKey = JSON.stringify([entities, selectionEpoch]);
  }
  async startContinuous(scope: PageScope, entities: string[], selectionEpoch: number) {
    const stopped = this.stop(), epoch = this.epoch;
    await stopped;
    if (!this.current(epoch)) return;
    this.set("starting", "音声接続を準備しています。", "");
    try {
      await this.conversation.open("continuous", scope);
      if (!this.current(epoch)) return;
      await this.selected(entities, selectionEpoch);
      if (!this.current(epoch)) return;
      const mic = await acquireMicrophone(() => this.current(epoch));
      if (!this.current(epoch)) { mic.getTracks().forEach(track => track.stop()); return; }
      this.mic = mic;
      const { MicVAD } = await import("@ricky0123/vad-web");
      if (!this.current(epoch)) return;
      const vad = await MicVAD.new({ model: "v5", startOnLoad: false, redemptionMs: 1200, preSpeechPadMs: 250, minSpeechMs: 250,
        baseAssetPath: "/voice-assets/", onnxWASMBasePath: "/voice-assets/", processorType: "AudioWorklet",
        getStream: async () => mic, pauseStream: async stream => { stream.getTracks().forEach(track => track.stop()); },
        resumeStream: async () => { throw new Error("音声取得の自動再開はできません。"); },
        ortConfig: ort => { ort.env.logLevel = "error"; ort.env.wasm.numThreads = 1; ort.env.wasm.proxy = false; },
        onFrameProcessed: (_probabilities, frame) => { if (this.current(epoch)) this.frame(frame); },
        onSpeechStart: () => { if (this.current(epoch)) this.speechStart(); },
        onSpeechEnd: () => { if (this.current(epoch)) void this.speechEnd(); },
        onVADMisfire: () => { if (this.current(epoch)) void this.misfire(); },
      });
      if (!this.current(epoch)) { await vad.destroy(); return; }
      this.vad = vad;
      await this.connect(epoch);
      if (!this.current(epoch)) return;
      await vad.start();
      if (this.current(epoch)) this.set("continuous", "音声入力中です。", "");
    } catch (error) { if (this.current(epoch)) this.fail(error); }
  }
  private async connect(epoch: number) {
    const owner = this.conversation.getOwner();
    const lease = await editingRequest<{ streamId: string; seconds: number }>(`${base(owner)}/voice/streams`, owner, {}, "POST");
    if (!this.current(epoch)) return;
    const url = new URL(`/api/v1/editing${base(owner)}/voice/streams/${lease.streamId}`, location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url); this.socket = socket;
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("音声接続の準備が期限を超えました。")), 15000);
      socket.onopen = () => { if (this.current(epoch)) socket.send(JSON.stringify({ type: "auth", token: owner.token })); else socket.close(); };
      socket.onmessage = event => {
        if (!this.current(epoch)) return;
        try {
          const message = JSON.parse(String(event.data)) as Record<string, unknown>;
          if (message.type === "ready") { clearTimeout(timeout); resolve(); }
          else if (message.type === "error") { clearTimeout(timeout); const error = new Error(String(message.message)); reject(error); this.fail(error); }
          else this.event(message);
        } catch (error) { reject(error); this.fail(error); }
      };
      socket.onerror = () => { clearTimeout(timeout); reject(new Error("音声接続に失敗しました。")); };
      socket.onclose = () => { clearTimeout(timeout); reject(new Error("音声接続が終了しました。")); if (this.current(epoch)) this.fail(new Error("音声接続が終了しました。再開には音声入力を押してください。")); };
    });
    if (this.current(epoch)) this.timer = setTimeout(() => this.fail(new Error("音声接続の5分枠が終了しました。")), lease.seconds * 1000);
  }
  private control(type: string, audioId: string): Promise<void> {
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return Promise.reject(new Error("音声接続が終了しています。"));
    const key = `${type}:${audioId}`;
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => { this.acks.delete(key); reject(new Error("音声制御の応答期限を超えました。")); }, 5000);
      this.acks.set(key, { resolve, reject, timer });
      socket.send(JSON.stringify({ type, audioId }));
    });
  }
  private event(message: Record<string, unknown>) {
    const id = String(message.audioId ?? "");
    if (message.type === "audio.started" || message.type === "audio.ack") {
      const key = `${message.type === "audio.started" ? "audio.begin" : String(message.action)}:${id}`, ack = this.acks.get(key);
      if (ack) { clearTimeout(ack.timer); this.acks.delete(key); ack.resolve(); }
      return;
    }
    const item = this.items.find(item => item.id === id);
    if (!item || item.finished) return;
    if (message.type === "transcript.delta") {
      if (item.cancelled) return;
      item.caption = (item.caption + String(message.delta)).slice(-2000);
      this.set(this.state.mode, "", item.caption);
    } else if (message.type === "transcript.completed") {
      if (Number(message.sequence) !== item.sequence || item.text !== null) throw new Error("音声の順序・IDが一致しません。");
      item.text = String(message.text);
      const epoch = this.epoch;
      void this.classify(item).catch(error => { if (this.current(epoch)) this.fail(error); });
    }
  }
  private ring: Float32Array[] = [];
  private frame(frame: Float32Array) {
    if (this.active) this.append(this.active, frame);
    else { this.ring.push(frame.slice()); while (this.ring.reduce((sum, frame) => sum + frame.length, 0) > 4512) this.ring.shift(); }
  }
  private speechStart() {
    this.speaking = true;
    if (this.items.filter(item => !item.finished).length >= 2) { this.fail(new Error("未処理の発話が2件に達しました。再指示してください。")); return; }
    const item: Item = { id: crypto.randomUUID().replaceAll("-", ""), sequence: ++this.sequence, capture: null, text: null, caption: "",
      classified: false, cancelled: false, finished: false, ready: false, frames: [], resampler: new Pcm24k(), samples: 0, started: Promise.resolve(), deadline: null };
    this.active = item; this.items.push(item);
    const epoch = this.epoch;
    item.deadline = setTimeout(() => this.fail(new Error("1発話の30秒上限に達しました。")), 30000);
    item.started = this.conversation.capture().then(async capture => {
      if (!this.current(epoch)) return;
      item.capture = capture;
      await this.control("audio.begin", item.id);
      if (!this.current(epoch)) return;
      item.ready = true;
      item.frames.splice(0).forEach(frame => this.sendFrame(frame));
    });
    void item.started.catch(error => { if (this.current(epoch)) this.fail(error); });
    const pad = new Float32Array(this.ring.reduce((sum, frame) => sum + frame.length, 0));
    let offset = 0; this.ring.forEach(frame => { pad.set(frame, offset); offset += frame.length; });
    this.ring = []; this.append(item, pad.slice(-4000));
  }
  private append(item: Item, frame: Float32Array) {
    const pcm = item.resampler.push(frame); item.samples += pcm.byteLength / 2;
    if (item.samples > 720000) { this.fail(new Error("1発話の30秒上限に達しました。")); return; }
    if (item.ready) this.sendFrame(pcm);
    else { item.frames.push(pcm); if (item.frames.reduce((sum, frame) => sum + frame.byteLength, 0) > 96000) this.fail(new Error("音声接続が遅延しています。取得を停止しました。")); }
  }
  private sendFrame(frame: ArrayBuffer) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > 262144) { this.fail(new Error("音声の送信が追いついていません。取得を停止しました。")); return; }
    if (frame.byteLength) this.socket.send(frame);
  }
  private async speechEnd() {
    const item = this.active, epoch = this.epoch; this.active = null; this.speaking = false;
    if (!item) return;
    if (item.deadline) clearTimeout(item.deadline);
    try { await item.started; if (this.current(epoch)) await this.control("audio.end", item.id); }
    catch (error) { if (this.current(epoch)) this.fail(error); }
  }
  private async misfire() {
    const item = this.active, epoch = this.epoch; this.active = null; this.speaking = false;
    if (!item) return;
    item.cancelled = true; item.classified = true;
    if (item.deadline) clearTimeout(item.deadline);
    try {
      await item.started;
      if (!this.current(epoch)) return;
      await this.control("audio.cancel", item.id);
      if (!this.current(epoch)) return;
      item.finished = true; this.items = this.items.filter(entry => entry !== item);
    } catch (error) { if (this.current(epoch)) this.fail(error); }
  }
  private async classify(item: Item) {
    const text = item.text!, epoch = this.epoch;
    if (!item.cancelled && correction(text)) {
      const previous = [...this.items].reverse().find(entry => entry.sequence < item.sequence && !entry.finished && !entry.cancelled);
      if (previous) {
        const cancelled = previous === this.currentItem ? await this.conversation.cancelUncommitted() : true;
        if (!this.current(epoch)) return;
        if (cancelled) {
          previous.cancelled = true;
          if (cancelOnly(text)) { item.cancelled = true; this.set(this.state.mode, "保存前の指示を中止しました。", text); }
          else {
            const deadline = Date.now() + 10000;
            while (previous.text === null && this.current(epoch) && Date.now() < deadline) await sleep(25);
            if (!this.current(epoch)) return;
            if (previous.text === null) throw new Error("先行指示の認識が未完了です。指示全体を言い直してください。");
            item.text = `先行指示「${previous.text}」を保存せず、次の訂正を反映してください。「${text}」`;
          }
        }
      }
    }
    if (!this.current(epoch)) return;
    await this.control("audio.release", item.id);
    if (!this.current(epoch)) return;
    item.classified = true;
    this.set(this.state.mode, this.state.message, item.text ?? "");
    void this.drain();
  }
  private async waitCapture() {
    const epoch = this.epoch, deadline = Date.now() + 60000;
    while (this.speaking || this.items.some(item => !item.finished && !item.classified)) {
      if (!this.current(epoch) || Date.now() > deadline) throw new EditingError("発話を中止しました。", "VOICE_CONTEXT_STALE", 409);
      await sleep(25);
    }
  }
  private async drain() {
    if (this.processing) return;
    this.processing = true; const epoch = this.epoch;
    try {
      while (this.current(epoch)) {
        const item = this.items.find(item => !item.finished);
        if (!item || !item.classified) break;
        this.currentItem = item;
        try {
          if (!item.cancelled && item.capture) {
            const deadline = Date.now() + 60000;
            while (this.current(epoch)) {
              try { await this.store.conversationBoundary(); break; }
              catch (error) { if (!(error instanceof EditingError) || error.code !== "MANUAL_EDIT_ACTIVE" || Date.now() >= deadline) throw error; await sleep(50); }
            }
            if (!this.current(epoch) || item.cancelled) break;
            const turn = await this.conversation.begin(item.id, item.capture);
            await this.conversation.resolve(turn, item.text!);
            if (this.current(epoch)) this.conversation.notificationFinished();
          }
        } catch (error) {
          if (this.current(epoch) && !item.cancelled) this.set(this.state.mode, error instanceof Error ? error.message : "再指示してください。");
          if (this.conversation.getSnapshot().state === "RECOVERING" || !["ready", "saving"].includes(this.store.getSnapshot().state)) { this.fail(error); break; }
        } finally {
          if (this.current(epoch)) { await this.control("audio.done", item.id); item.finished = true; this.items = this.items.filter(entry => entry !== item); }
          this.currentItem = null;
        }
      }
    } catch (error) { if (this.current(epoch)) this.fail(error); }
    finally {
      this.processing = false;
      // A later completed utterance must wait for the earliest item. Looking
      // for any completed item re-entered this loop while the first was pending.
      if (!this.disposed && this.items.find(item => !item.finished)?.classified) queueMicrotask(() => { void this.drain(); });
    }
  }
  async startLocal(scope: PageScope, entities: string[], selectionEpoch: number) {
    const reuse = this.state.mode === "stopped" && this.conversation.getQuestionId() &&
      this.conversation.getContext().mode === "local" && JSON.stringify(this.conversation.getContext().pageScope) === JSON.stringify(scope) &&
      this.selectionKey === JSON.stringify([entities, selectionEpoch]);
    const stopped = reuse ? null : this.stop();
    if (reuse) this.epoch += 1;
    const epoch = this.epoch;
    await stopped;
    if (!this.current(epoch)) return;
    this.set("starting", "マイクの使用許可を待っています。", "");
    try {
      if (!entities.length) throw new Error("局所録音では対象の欄を選択してください。");
      const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find(value => MediaRecorder.isTypeSupported(value));
      if (!mime) throw new Error("このブラウザーの録音形式は未対応です。");
      if (!reuse) {
        await this.conversation.open("local", scope);
        if (!this.current(epoch)) return;
        await this.selected(entities, selectionEpoch);
        if (!this.current(epoch)) return;
      }
      const turn = await this.conversation.begin();
      if (!this.current(epoch)) return;
      this.localTurn = turn;
      const mic = await acquireMicrophone(() => this.current(epoch));
      if (!this.current(epoch)) { mic.getTracks().forEach(track => track.stop()); return; }
      this.mic = mic; this.recordingChunks = []; this.recordingBytes = 0;
      const recorder = new MediaRecorder(mic, { mimeType: mime }); this.recorder = recorder;
      recorder.ondataavailable = event => {
        if (this.current(epoch) && event.data.size) {
          this.recordingBytes += event.data.size;
          if (this.recordingBytes > 10485760) { this.fail(new Error("録音の10MiB上限に達しました。")); return; }
          this.recordingChunks.push(event.data);
        }
      };
      recorder.onerror = () => this.fail(new Error("録音に失敗しました。"));
      recorder.start(250); this.set("local", "選択欄の指示を録音しています。", "");
      this.timer = setTimeout(() => this.fail(new Error("録音の30秒上限に達しました。送信は行っていません。")), 30000);
    } catch (error) { if (this.current(epoch)) this.fail(error); }
  }
  async finishLocal() {
    const recorder = this.recorder, turn = this.localTurn, epoch = this.epoch;
    if (!recorder || !turn || recorder.state !== "recording") return;
    if (this.timer) clearTimeout(this.timer); this.timer = null;
    // Keep completion independent of handlers detached by stop(). The stop
    // event also settles a recording interrupted before its final chunk.
    await new Promise<void>(resolve => { recorder.addEventListener("stop", () => resolve(), { once: true }); recorder.stop(); });
    if (!this.current(epoch)) return;
    this.mic?.getTracks().forEach(track => track.stop()); this.mic = null; this.recorder = null;
    const audio = new Blob(this.recordingChunks, { type: recorder.mimeType }); this.recordingChunks = [];
    this.set("processing", "音声を認識しています。");
    try { await this.conversation.resolveAudio(turn, audio); if (this.current(epoch)) this.conversation.notificationFinished(); }
    catch (error) { if (this.current(epoch)) this.set("stopped", error instanceof Error ? error.message : "音声を認識できませんでした。"); }
    finally { if (this.current(epoch)) this.set("stopped", this.conversation.getSnapshot().message); }
  }
  async stop() {
    const epoch = ++this.epoch; this.speaking = false;
    if (this.timer) clearTimeout(this.timer); this.timer = null;
    for (const item of this.items) if (item.deadline) clearTimeout(item.deadline);
    this.items = []; this.active = null; this.ring = [];
    for (const ack of this.acks.values()) { clearTimeout(ack.timer); ack.reject(new Error("音声取得を終了しました。")); } this.acks.clear();
    if (this.recorder) { this.recorder.onstop = null; this.recorder.ondataavailable = null; this.recorder.onerror = null; if (this.recorder.state !== "inactive") this.recorder.stop(); } this.recorder = null;
    this.mic?.getTracks().forEach(track => track.stop()); this.mic = null; this.recordingChunks = []; this.localTurn = null;
    const vad = this.vad; this.vad = null;
    if (this.socket) { this.socket.onmessage = null; this.socket.onclose = null; this.socket.onerror = null; this.socket.close(); this.socket = null; }
    this.sequence = 0; this.selectionKey = null;
    // Invalidate the owner now, before asynchronous VAD disposal can overlap
    // with another start. An older stop must never close a newer session.
    const stopped = this.conversation.stop().catch(() => undefined);
    if (vad) await vad.destroy().catch(() => undefined);
    await stopped;
    if (this.current(epoch)) this.set("stopped", "", "");
  }
  async dispose() { this.disposed = true; await this.stop(); await this.conversation.dispose(); this.listeners.clear(); }
}
