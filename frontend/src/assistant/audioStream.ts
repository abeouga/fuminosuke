/** Raw audio stays in memory. One stateful 16k -> 24k resampler per utterance. */
export class Pcm24k {
  private pending: number[] = [];
  private position = 0;
  push(frame: Float32Array): ArrayBuffer {
    this.pending.push(...frame);
    const values: number[] = [];
    while (this.position + 1 < this.pending.length) {
      const index = Math.floor(this.position), fraction = this.position - index;
      const value = this.pending[index] * (1 - fraction) + this.pending[index + 1] * fraction;
      values.push(Math.round(Math.max(-1, Math.min(1, value)) * (value < 0 ? 32768 : 32767)));
      this.position += 2 / 3;
    }
    const consumed = Math.floor(this.position);
    this.pending.splice(0, consumed); this.position -= consumed;
    const output = new ArrayBuffer(values.length * 2), view = new DataView(output);
    values.forEach((value, index) => view.setInt16(index * 2, value, true));
    return output;
  }
}
export function correction(text: string): boolean {
  return /^(?:いや[、,\s]*|違う|ちがう|間違|まちが|今の(?:を)?(?:戻|もど|取消|取り消|中止)|中止|キャンセル)/.test(text.trim());
}
export function cancelOnly(text: string): boolean {
  return /^(?:違うよ?|ちがうよ?|中止|キャンセル|今の(?:を)?(?:戻して|もどして|取り消して|取消して))[。！!、\s]*$/.test(text.trim());
}
const compact = (text: string) => text.normalize("NFKC").replace(/[\s\p{P}\p{S}]/gu, "").toLowerCase();
export function likelyPlaybackEcho(text: string, spoken: string): boolean {
  const a = compact(text), b = compact(spoken);
  if (a.length < 6 || b.length < 6 || correction(text) || /(?:に変更|にして|訂正|を設定)/.test(text)) return false;
  const grams = (value: string) => new Set(Array.from({ length: value.length - 1 }, (_, index) => value.slice(index, index + 2)));
  const aa = grams(a), bb = grams(b), common = [...aa].filter(value => bb.has(value)).length;
  return common / Math.max(aa.size, bb.size) >= 0.82;
}

export async function acquireMicrophone(stillCurrent: () => boolean): Promise<MediaStream> {
  let expired = false, timer: ReturnType<typeof setTimeout> | undefined;
  const request = navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  void request.then(stream => { if (expired || !stillCurrent()) stream.getTracks().forEach(track => track.stop()); }).catch(() => undefined);
  try {
    return await Promise.race([request, new Promise<never>((_, reject) => {
      timer = setTimeout(() => { expired = true; reject(new Error("マイク許可の待機を終了しました。")); }, 15000);
    })]);
  } finally { if (timer) clearTimeout(timer); }
}
