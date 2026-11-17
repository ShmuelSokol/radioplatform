// Mounts the real useAudioEngine hook with state driven from the test (window.__set)
// and exposes what each deck is doing (window.__snap). Used by e2e/audio-engine.mjs.
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useAudioEngine } from '../../src/hooks/useAudioEngine';
import { nextCut } from '../../src/pages/admin/controlroom/timing';
import { updatesHeld } from '../../src/lib/appUpdate';

const audios: HTMLAudioElement[] = [];
const gains: GainNode[] = [];
const w = window as any;
const NativeAudio = window.Audio;
w.__seeks = [] as { src: string; to: number; at: number }[];
w.Audio = function (...args: any[]) {
  const a = new (NativeAudio as any)(...args);
  audios.push(a);
  a.addEventListener('seeking', () => w.__seeks.push({
    src: (a.src.split('/').pop() || '').replace('.wav', ''), to: +a.currentTime.toFixed(2), at: Date.now(),
  }));
  return a;
};
// Each deck's gain is the gain made right after its media source (other gains, such
// as the output bus, are not decks)
let deckGainNext = false;
const createSource = AudioContext.prototype.createMediaElementSource;
AudioContext.prototype.createMediaElementSource = function (el: HTMLMediaElement) {
  deckGainNext = true;
  return createSource.call(this, el);
};
const createGain = AudioContext.prototype.createGain;
AudioContext.prototype.createGain = function () {
  const g = createGain.call(this);
  if (deckGainNext) { gains.push(g); deckGainNext = false; }
  return g;
};
// The engine's left and right meters (the first two analysers it makes)
const analysers: AnalyserNode[] = [];
const createAnalyser = AudioContext.prototype.createAnalyser;
AudioContext.prototype.createAnalyser = function () { const n = createAnalyser.call(this); analysers.push(n); return n; };
w.__levels = () => analysers.slice(0, 2).map((n) => {
  const d = new Float32Array(n.fftSize);
  n.getFloatTimeDomainData(d);
  return +Math.sqrt(d.reduce((sum, v) => sum + v * v, 0) / d.length).toFixed(4);
});
w.__held = () => updatesHeld();
w.__snap = () => audios.map((a, i) => ({
  src: (a.src.split('/').pop() || '').replace('.wav', ''),
  playing: !a.paused && !a.ended,
  t: +a.currentTime.toFixed(2),
  gain: +(gains[i]?.gain.value ?? 0).toFixed(2),
}));

function Harness() {
  const [s, set] = useState<any>(null);
  w.__set = set;
  // Timed items in the queue, picked the way the control room picks them
  // (or the server's next_asset.cut_at, as the Listen page passes it: s.cut)
  const cut = s?.cut !== undefined ? s.cut : s?.queue ? nextCut(s.queue) : null;
  const engine = useAudioEngine(
    s ? { id: s.id, title: s.id, artist: null, asset_type: 'music', category: null, duration: s.dur } : null,
    0, !!s, cut?.preempt_at ?? null, cut?.asset_id ?? null, 2000, s?.cueIn ?? 0, 0, s?.cross ?? 0, s?.rg ?? 0,
    s?.nextId ?? null, s?.nextCueIn ?? 0, s?.nextRg ?? 0, null, null, s?.startedAt ?? null,
    s?.fade ?? null,
  );
  w.__listen = engine.initAudio;
  w.__nextNow = (id: string, fade: number | null) =>
    engine.playNextNow({ id, title: id, artist: null, asset_type: 'music', category: null, duration: null }, fade);
  return null;
}
createRoot(document.getElementById('root')!).render(<Harness />);
