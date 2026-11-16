// Mounts the real useAudioEngine hook with state driven from the test (window.__set)
// and exposes what each deck is doing (window.__snap). Used by e2e/audio-engine.mjs.
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useAudioEngine } from '../../src/hooks/useAudioEngine';
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
const createGain = AudioContext.prototype.createGain;
AudioContext.prototype.createGain = function () { const g = createGain.call(this); gains.push(g); return g; };
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
  useAudioEngine(
    s ? { id: s.id, title: s.id, artist: null, asset_type: 'music', category: null, duration: s.dur } : null,
    0, !!s, null, null, 2000, 0, 0, s?.cross ?? 0, 0, s?.nextId ?? null, 0, 0, null, null, s?.startedAt ?? null,
    s?.fade ?? null,
  );
  return null;
}
createRoot(document.getElementById('root')!).render(<Harness />);
