/**
 * Live show audio: raw 16-bit little-endian mono PCM at LIVE_RATE, the format the
 * host's microphone, web callers' microphones and the listener relay all use
 * (see wiki/live-shows.md).
 */
export const LIVE_RATE = 24000;

/** Mono float samples at `from` Hz to 16-bit samples at LIVE_RATE (linear interpolation). */
export function makeResampler(from: number) {
  const step = from / LIVE_RATE;
  let pos = 0; // position in the current input block, carried across blocks
  return (input: Float32Array): Int16Array => {
    const out: number[] = [];
    while (pos < input.length) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const a = input[i];
      const b = i + 1 < input.length ? input[i + 1] : input[i];
      const v = Math.max(-1, Math.min(1, a + (b - a) * frac));
      out.push(v < 0 ? v * 0x8000 : v * 0x7fff);
      pos += step;
    }
    pos -= input.length;
    return Int16Array.from(out);
  };
}

const START_DELAY = 0.25; // seconds buffered before the first frame plays (network jitter)
const MAX_LAG = 1.2;      // further behind than this, drop the backlog and catch up

/** Plays PCM frames as they arrive, back to back, a little behind for jitter. */
export class PcmPlayer {
  private next = 0;
  readonly gain: GainNode;
  constructor(private ctx: AudioContext, private rate = LIVE_RATE) {
    this.gain = ctx.createGain();
    this.gain.connect(ctx.destination);
  }

  push(buf: ArrayBuffer) {
    const pcm = new Int16Array(buf, 0, Math.floor(buf.byteLength / 2));
    if (!pcm.length || this.ctx.state !== 'running') return;
    const audio = this.ctx.createBuffer(1, pcm.length, this.rate);
    const data = audio.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) data[i] = pcm[i] / 32768;
    const now = this.ctx.currentTime;
    if (this.next < now || this.next > now + MAX_LAG) this.next = now + START_DELAY;
    const src = this.ctx.createBufferSource();
    src.buffer = audio;
    src.connect(this.gain);
    src.start(this.next);
    this.next += audio.duration;
  }

  stop() {
    this.gain.disconnect();
  }
}

export function apiWsUrl(path: string): string {
  const wsBase = import.meta.env.VITE_WS_URL;
  const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
  if (wsBase) return `${wsBase}/api/v1${path}`;
  if (apiUrl.startsWith('http')) return apiUrl.replace(/^http/, 'ws') + path;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${apiUrl}${path}`;
}
