import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoiceCapture, silenceDelay } from "./voiceCapture";
import type { WorkbenchStore } from "./workbenchStore";
import type { ContextResult } from "./v2.generated";

const mocks = vi.hoisted(() => ({ options: null as any, stop: vi.fn(), destroy: vi.fn() }));
vi.mock("@ricky0123/vad-web", () => ({ MicVAD: { new: vi.fn(async (options: unknown) => { mocks.options = options; return { start: async () => {}, destroy: mocks.destroy }; }) } }));
class Socket {
  static OPEN = 1; static current: Socket; readyState = 1; bufferedAmount = 0; sent: any[] = [];
  onopen?: () => void; onmessage?: (event: { data: string }) => void; onerror?: () => void; onclose?: () => void;
  constructor() { Socket.current = this; queueMicrotask(() => this.onopen?.()); }
  event(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }); }
  send(value: string | ArrayBuffer) {
    const v = typeof value === "string" ? JSON.parse(value) : value; this.sent.push(v);
    if (v.type === "auth") queueMicrotask(() => this.event({ type: "ready" }));
    if (v.type === "utterance.begin") queueMicrotask(() => this.event({ type: "potentialContinuation", utteranceId: v.utteranceId }));
  }
  close() { this.readyState = 3; this.onclose?.(); }
}
const context = { scopeContext: { resourceRevision: 1, contextRevision: 1, interactionEpoch: 0, catalogRevision: "catalog", scopeDigest: "scope" } } as ContextResult;
const store = { audioContext: async () => context, refreshContext: async () => context, getOwner: () => ({ sessionId: "session", token: "owned-token" }), acceptCommand: vi.fn() } as unknown as WorkbenchStore;
let capture: VoiceCapture;
const frames = (count: number) => { for (let i = 0; i < count; i++) mocks.options.onFrameProcessed({}, new Float32Array(512).fill(.1)); };
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const controls = (type: string) => Socket.current.sent.filter(v => v.type === type);
beforeEach(() => {
  vi.useFakeTimers(); vi.stubGlobal("WebSocket", Socket);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: mocks.stop }] })) } });
  capture = new VoiceCapture(store); mocks.stop.mockClear(); mocks.destroy.mockClear();
});
afterEach(async () => { await capture.stop(); vi.useRealTimers(); vi.unstubAllGlobals(); });
it("streams a long instruction in ordered segments but seals it only once", async () => {
  await capture.start("continuous", { mode: "all", pageId: null }, [], null);
  mocks.options.onSpeechStart(); await settle(); frames(820); mocks.options.onSpeechEnd();
  expect(controls("segment.end")).toHaveLength(2);
  expect(controls("utterance.end")).toHaveLength(0);
  await vi.advanceTimersByTimeAsync(1300);
  expect(controls("utterance.end")).toHaveLength(1);
  expect(controls("utterance.end")[0].manifest).toEqual(controls("segment.begin").map(v => v.segmentId));
  expect(controls("utterance.end")[0].reason).toBe("silence");
  frames(20); Socket.current.event({ type: "command.accepted", result: { requestId: "r", commandId: "c" } }); await settle();
  expect(controls("utterance.begin")).toHaveLength(1); // Idle pre-roll cannot become another instruction.
});
it("retains a second speech onset while the first transcript is finalizing", async () => {
  await capture.start("continuous", { mode: "all", pageId: null }, [], null);
  mocks.options.onSpeechStart(); await settle(); frames(12); mocks.options.onSpeechEnd(); await vi.advanceTimersByTimeAsync(1300);
  mocks.options.onSpeechStart(); frames(12); mocks.options.onSpeechEnd();
  expect(controls("utterance.begin")).toHaveLength(1);
  Socket.current.event({ type: "command.accepted", result: { requestId: "r", commandId: "c" } }); await settle();
  expect(controls("utterance.begin")).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(1300);
  expect(controls("utterance.end")).toHaveLength(2);
});
it("local recording waits for its explicit finish and releases the microphone", async () => {
  await capture.start("local", { mode: "all", pageId: null }, ["cell"], null);
  mocks.options.onSpeechStart(); await settle(); frames(12); mocks.options.onSpeechEnd(); await vi.advanceTimersByTimeAsync(4000);
  expect(controls("utterance.end")).toHaveLength(0);
  capture.finishLocal(); expect(controls("utterance.end")[0].reason).toBe("local_stop"); expect(mocks.stop).toHaveBeenCalled();
});
it("uses the incomplete-tail delay without interpreting an edit value", () => {
  expect(silenceDelay("体重を、")).toBe(3000); expect(silenceDelay("500ではなく")).toBe(3000);
  expect(silenceDelay("体重を500にして")).toBe(1200);
});
it("waits for the full silence boundary when a browser timer fires early", async () => {
  let elapsed = 0;
  const clock = vi.spyOn(performance, "now").mockImplementation(() => elapsed);
  try {
    await capture.start("continuous", { mode: "all", pageId: null }, [], null);
    mocks.options.onSpeechStart(); await settle(); frames(12); mocks.options.onSpeechEnd();
    elapsed = 1199.4;
    await vi.advanceTimersByTimeAsync(1200);
    expect(controls("utterance.end")).toHaveLength(0);
    elapsed = 1250;
    await vi.advanceTimersByTimeAsync(50);
    expect(controls("utterance.end")).toHaveLength(1);
    expect(controls("utterance.end")[0].silenceMs).toBeGreaterThanOrEqual(1200);
    expect(capture.getSnapshot().mode).toBe("continuous");
  } finally { clock.mockRestore(); }
});
