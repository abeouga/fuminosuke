import { useEffect, useRef, useState } from "react";
import { FiMic, FiSquare, FiVolume2 } from "react-icons/fi";
import { contextKey, type AssistantPreview, type AssistantPreviewProvider, type AssistantReceipt, type AssistantSnapshot, type VoiceStatus } from "./contracts";

type Props = {
  provider: AssistantPreviewProvider; snapshot: AssistantSnapshot; preview: AssistantPreview | null;
  challenge: string | null; disabled: boolean;
  receipt: AssistantReceipt | null;
  onProposal: (proposal: AssistantPreview) => void;
  onApprove: (turnId: string) => void;
  onBusy: (busy: boolean) => void;
  registerStop: (stop: () => void) => void;
};
type Phase = "idle" | "permission" | "recording" | "transcribing" | "proposing" | "speech-loading" | "speaking";
const PHASE_LABEL: Record<Phase, string> = { idle: "", permission: "マイクの使用許可を待っています。", recording: "録音中。終了して送信、または中止を選択してください。", transcribing: "音声を認識しています。", proposing: "変更案を作成しています。", "speech-loading": "読み上げ音声を取得しています。", speaking: "読み上げています。" };

/** Recording, playback and buffers live here, outside editor state and business receipts. */
export function VoiceControls(props: Props) {
  const { provider, snapshot, preview, challenge, disabled } = props;
  const [settings, setSettings] = useState<VoiceStatus | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [playedSpeech, setPlayedSpeech] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const epoch = useRef(0), mounted = useRef(true), busy = useRef(false);
  const latest = useRef(props); latest.current = props;
  const abort = useRef<AbortController | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const playback = useRef<HTMLAudioElement | null>(null), audioUrl = useRef<string | null>(null);
  const turn = useRef<string | null>(null), speakingTurn = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null), ticker = useRef<ReturnType<typeof setInterval> | null>(null);
  const chunks = useRef<Blob[]>([]);
  function setActivity(next: Phase) {
    busy.current = next !== "idle"; setPhase(next); latest.current.onBusy(busy.current);
  }
  function releaseRecording() {
    if (timer.current) clearTimeout(timer.current);
    if (ticker.current) clearInterval(ticker.current);
    timer.current = null; ticker.current = null;
    stream.current?.getTracks().forEach(track => track.stop()); stream.current = null;
    recorder.current = null;
  }
  function releasePlayback() {
    if (playback.current) { playback.current.onended = null; playback.current.onerror = null; playback.current.pause(); playback.current.removeAttribute("src"); playback.current.load(); }
    playback.current = null;
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    audioUrl.current = null;
  }
  function stop() {
    epoch.current += 1; abort.current?.abort(); abort.current = null;
    if (recorder.current) { recorder.current.onstop = null; recorder.current.ondataavailable = null; recorder.current.onerror = null; if (recorder.current.state !== "inactive") recorder.current.stop(); }
    releaseRecording(); chunks.current = [];
    releasePlayback();
    if (turn.current) void provider.voiceCancel?.(turn.current).catch(() => undefined);
    if (speakingTurn.current) void provider.voiceEnded?.(speakingTurn.current, false).catch(() => undefined);
    turn.current = null; speakingTurn.current = null;
    busy.current = false; latest.current.onBusy(false);
    if (mounted.current) { setPhase("idle"); setPlayedSpeech(null); }
  }
  function refreshStatus() { void provider.voiceStatus?.().then(value => { if (mounted.current) setSettings(value); }).catch(() => { if (mounted.current) setMessage("音声設定を取得できません。手入力を使用してください。"); }); }
  useEffect(() => {
    mounted.current = true; refreshStatus(); props.registerStop(stop);
    return () => { mounted.current = false; stop(); props.registerStop(() => {}); };
  }, [provider]);
  useEffect(() => { stop(); setMessage(null); setInstruction(""); }, [contextKey(snapshot.context), preview?.id, challenge, disabled]);

  async function textProposal() {
    if (busy.current || disabled || !provider.voiceText || !instruction.trim()) return;
    stop(); const current = epoch.current, captured = structuredClone(snapshot);
    const control = new AbortController(); abort.current = control;
    setMessage(null); setActivity("proposing");
    try {
      const result = await provider.voiceText(instruction, captured, control.signal);
      if (current !== epoch.current) return;
      if (result.proposal) latest.current.onProposal(result.proposal);
      else setMessage(result.message ?? "変更案を作成できませんでした。");
    } catch (error) { if (current === epoch.current) setMessage(error instanceof Error ? error.message : "変更案を作成できませんでした。"); }
    finally { if (current === epoch.current) { abort.current = null; setActivity("idle"); } refreshStatus(); }
  }
  async function record(approval: boolean) {
    if (busy.current || disabled || !settings?.recordingAvailable || !provider.voiceStart || !provider.voiceAudio) return;
    const approved = approval && preview && challenge && playedSpeech ? { preview, challenge, speechId: playedSpeech } : undefined;
    if (approval && !approved) return;
    const supported = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find(mime => MediaRecorder.isTypeSupported(mime));
    if (!supported) { setMessage("このブラウザーでは録音形式に対応していません。手入力を使用してください。"); return; }
    stop(); const current = epoch.current, captured = structuredClone(snapshot);
    const control = new AbortController(); abort.current = control;
    setMessage(null); setSeconds(0); setActivity("permission");
    try {
      let acquisitionExpired = false;
      const acquisition = navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      // A permission prompt may resolve after cancellation/timeout: release those late tracks.
      void acquisition.then(value => { if (acquisitionExpired || current !== epoch.current) value.getTracks().forEach(track => track.stop()); }).catch(() => undefined);
      let permissionTimer: ReturnType<typeof setTimeout>;
      const obtained = await Promise.race([acquisition, new Promise<never>((_, reject) => { permissionTimer = setTimeout(() => { acquisitionExpired = true; reject(new Error("マイク許可の待機を終了しました。録り直すか手入力を使用してください。")); }, 15000); })]).finally(() => clearTimeout(permissionTimer!));
      if (current !== epoch.current) { obtained.getTracks().forEach(track => track.stop()); return; }
      stream.current = obtained;
      const id = await provider.voiceStart(captured, control.signal, approved);
      if (current !== epoch.current) { void provider.voiceCancel?.(id); return; }
      turn.current = id;
      const active = new MediaRecorder(obtained, { mimeType: supported }); recorder.current = active;
      chunks.current = []; let bytes = 0;
      active.ondataavailable = event => {
        bytes += event.data.size;
        if (bytes > settings.maxAudioBytes) { stop(); setMessage("録音サイズの上限に達したため中止しました。短い指示で録り直してください。"); }
        else if (event.data.size) chunks.current.push(event.data);
      };
      active.onerror = () => { stop(); setMessage("録音に失敗しました。マイクを確認するか手入力を使用してください。"); };
      active.onstop = () => {
        const blob = new Blob(chunks.current, { type: active.mimeType }); chunks.current = [];
        releaseRecording(); setActivity("transcribing");
        void provider.voiceAudio!(id, blob, captured, control.signal).then(result => {
          if (current !== epoch.current) return;
          turn.current = null;
          if (approved && result.approved) { setActivity("idle"); latest.current.onApprove(result.turnId); }
          else if (result.proposal) latest.current.onProposal(result.proposal);
          else setMessage(result.message ?? "変更指示を認識できませんでした。");
        }).catch(error => { if (current === epoch.current) setMessage(error instanceof Error ? error.message : "音声を処理できませんでした。"); })
          .finally(() => { if (current === epoch.current) { turn.current = null; abort.current = null; setActivity("idle"); } refreshStatus(); });
      };
      active.start(250); setActivity("recording");
      timer.current = setTimeout(() => { if (active.state === "recording") active.stop(); }, Math.max(300, (settings.maxRecordSeconds - 1) * 1000));
      ticker.current = setInterval(() => setSeconds(value => value + 1), 1000);
    } catch (error) {
      if (current === epoch.current) { stop(); setMessage(error instanceof Error ? error.message : "マイクを開始できませんでした。手入力を使用してください。"); }
    }
  }
  async function speak(receiptMode = false) {
    if (busy.current || disabled || !provider.voiceEnded) return;
    if (receiptMode ? !props.receipt || !provider.voiceReceiptSpeech : !preview || !challenge || !provider.voiceSpeech) return;
    stop(); const current = epoch.current;
    const control = new AbortController(); abort.current = control;
    setMessage(null); setActivity("speech-loading");
    try {
      const result = receiptMode ? await provider.voiceReceiptSpeech!(props.receipt!, control.signal) : await provider.voiceSpeech!(preview!, challenge!, control.signal);
      if (current !== epoch.current) return;
      speakingTurn.current = result.speechId;
      const bytes = Uint8Array.from(atob(result.audio), value => value.charCodeAt(0));
      audioUrl.current = URL.createObjectURL(new Blob([bytes], { type: result.mime }));
      const audio = new Audio(audioUrl.current); playback.current = audio;
      audio.onended = () => {
        releasePlayback(); setActivity("speech-loading");
        void provider.voiceEnded!(result.speechId, true).then(() => { if (current === epoch.current) { speakingTurn.current = null; if (!receiptMode) setPlayedSpeech(result.speechId); setActivity("idle"); } })
          .catch(error => { if (current === epoch.current) { stop(); setMessage(error instanceof Error ? error.message : "読み上げ終了を確認できません。適用ボタンを使用してください。"); } });
      };
      audio.onerror = () => { stop(); setMessage("音声を再生できません。画面で案を確認してください。"); };
      await audio.play(); if (current === epoch.current) setActivity("speaking");
    } catch (error) { if (current === epoch.current) { stop(); setMessage(error instanceof Error ? error.message : "読み上げに失敗しました。画面で案を確認してください。"); } }
    finally { refreshStatus(); }
  }
  const canRecord = Boolean(settings?.recordingAvailable && typeof navigator.mediaDevices?.getUserMedia === "function" && typeof MediaRecorder !== "undefined");
  return <section className="assistant-voice" aria-label="音声と文章による指示">
    <label>変更指示<input aria-label="変更指示" placeholder="例：選択した欄を00123に変更" maxLength={1000} value={instruction} disabled={disabled || phase !== "idle"} onChange={event => setInstruction(event.target.value)} /></label>
    <div className="assistant-actions">
      <button type="button" className="secondary compact" disabled={disabled || phase !== "idle" || !settings?.configured || !settings?.budget.remaining || !instruction.trim()} onClick={() => void textProposal()}>指示を送る</button>
      <button type="button" className="secondary compact" disabled={disabled || phase !== "idle" || !canRecord || !settings?.budget.remaining} onClick={() => void record(false)}><FiMic aria-hidden="true" />音声入力</button>
      {phase === "recording" && <button type="button" className="primary compact" onClick={() => recorder.current?.stop()}>録音を終了して送信</button>}
      {phase !== "idle" && <button type="button" className="ghost compact" onClick={() => { stop(); setMessage("音声処理を中止しました。"); }}><FiSquare aria-hidden="true" />{phase === "speaking" ? "読み上げ停止" : "中止"}</button>}
    </div>
    {preview && challenge && <div className="assistant-actions">
      <button type="button" className="secondary compact" disabled={disabled || phase !== "idle" || !settings?.configured || !settings?.budget.remaining} onClick={() => void speak()}><FiVolume2 aria-hidden="true" />変更案を読み上げ</button>
      <button type="button" className="secondary compact" disabled={disabled || phase !== "idle" || !canRecord || !playedSpeech || !settings?.budget.remaining} onClick={() => void record(true)}>音声で承認</button>
    </div>}
    {playedSpeech && <p>承認する場合は「{preview?.undoOfId ? "取り消しを適用" : "変更を適用"}」と話してください。</p>}
    {props.receipt && <button type="button" className="secondary compact" disabled={disabled || phase !== "idle" || !settings?.configured || !settings?.budget.remaining} onClick={() => void speak(true)}>保存結果を読み上げ</button>}
    <p className="assistant-voice-disclosure">音声と選択項目の指示をOpenAIへ送信します。読み上げはAI音声です。{settings && ` API ${settings.budget.used} / ${settings.budget.limit}回。`}</p>
    {(phase !== "idle" || message) && <p role="status">{phase === "recording" ? `${PHASE_LABEL[phase]} ${seconds}秒 / ${settings?.maxRecordSeconds ?? 60}秒` : PHASE_LABEL[phase] || message}</p>}
  </section>;
}
