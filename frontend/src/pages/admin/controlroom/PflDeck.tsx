import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAsset } from '../../../api/assets';
import type { Pfl } from './pfl';

/**
 * The pre-listen (PFL) deck: shows the item heard in this browser only, with a
 * waveform you can click or drag to jump anywhere, the start / intro / mix / end
 * points, and keyboard control. Nothing here reaches the station.
 */

// Waveforms are drawn from the file itself. Long items (shiurim, stories) would
// take hundreds of MB to decode, so above this length the bar is a plain ruler.
const WAVEFORM_MAX_SECONDS = 10 * 60;
const BINS = 900;
const peaksCache = new Map<string, Float32Array | null>();

async function loadPeaks(url: string): Promise<Float32Array | null> {
  const res = await fetch(url);
  if (!res.ok) return null;
  const buf = await res.arrayBuffer();
  type OfflineCtor = typeof OfflineAudioContext;
  const Ctor: OfflineCtor | undefined = window.OfflineAudioContext
    ?? (window as unknown as { webkitOfflineAudioContext?: OfflineCtor }).webkitOfflineAudioContext;
  if (!Ctor) return null;
  // A low rate keeps the decoded copy small; the peaks look the same
  const ctx = new Ctor(1, 1, 22050);
  const audio = await new Promise<AudioBuffer>((resolve, reject) => {
    const p = ctx.decodeAudioData(buf, resolve, reject);
    if (p && typeof p.then === 'function') p.then(resolve, reject);
  });
  const peaks = new Float32Array(BINS);
  const per = Math.max(1, Math.floor(audio.length / BINS));
  for (let c = 0; c < audio.numberOfChannels; c++) {
    const data = audio.getChannelData(c);
    for (let b = 0; b < BINS; b++) {
      let max = 0;
      const start = b * per;
      const end = Math.min(data.length, start + per);
      // Every 4th sample is plenty for a peak picture
      for (let i = start; i < end; i += 4) {
        const v = Math.abs(data[i]);
        if (v > max) max = v;
      }
      if (max > peaks[b]) peaks[b] = max;
    }
  }
  let top = 0;
  for (let b = 0; b < BINS; b++) top = Math.max(top, peaks[b]);
  if (top > 0) for (let b = 0; b < BINS; b++) peaks[b] /= top;
  return peaks;
}

function fmt(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  const t = Math.floor((sec * 10) % 10);
  return `${m}:${s.toString().padStart(2, '0')}.${t}`;
}

interface Marker { key: string; at: number; label: string; color: string; title: string }

function markersOf(asset: { intro_seconds?: number | null; metadata_extra?: Record<string, unknown> | null } | undefined, duration: number): Marker[] {
  if (!asset || !duration) return [];
  const a = (asset.metadata_extra?.audio_analysis ?? null) as Record<string, number> | null;
  const out: Marker[] = [];
  const cueIn = typeof a?.cue_in_seconds === 'number' ? a.cue_in_seconds : 0;
  if (cueIn > 0.2) out.push({ key: 'cue', at: cueIn, label: 'START', color: '#00e5ff', title: 'Where it starts on air (silence before it is skipped)' });
  if (asset.intro_seconds && asset.intro_seconds > 0) {
    out.push({ key: 'intro', at: cueIn + asset.intro_seconds, label: 'INTRO', color: '#ffff40', title: 'End of the intro (talk-over time)' });
  }
  if (typeof a?.cross_start_seconds === 'number' && a.cross_start_seconds > 0 && a.cross_start_seconds < duration) {
    out.push({ key: 'mix', at: a.cross_start_seconds, label: 'MIX', color: '#ff4dff', title: 'Where the next item starts mixing in' });
  }
  if (typeof a?.cue_out_seconds === 'number' && a.cue_out_seconds > 0 && a.cue_out_seconds < duration - 0.2) {
    out.push({ key: 'end', at: a.cue_out_seconds, label: 'END', color: '#ff5050', title: 'Where it ends on air (silence after it is cut)' });
  }
  return out.filter(m => m.at >= 0 && m.at <= duration);
}

export function PflDeck({ pfl, compact = false }: { pfl: Pfl; compact?: boolean }) {
  const id = pfl.loadedId;
  const { data: asset } = useQuery({
    queryKey: ['pfl-asset', id],
    queryFn: () => getAsset(id as string),
    enabled: !!id,
    staleTime: 5 * 60_000,
  });

  const [duration, setDuration] = useState(0);
  const [time, setTime] = useState(0);
  const [peaks, setPeaks] = useState<Float32Array | null | undefined>(undefined);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const [dragAt, setDragAt] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const dragging = useRef(false);

  // Follow the audio element: smooth playhead while it plays
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const a = pfl.getAudio();
      if (a) {
        if (Number.isFinite(a.duration) && a.duration > 0) setDuration(d => (Math.abs(d - a.duration) > 0.01 ? a.duration : d));
        setTime(t => (Math.abs(t - a.currentTime) > 0.03 ? a.currentTime : t));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [pfl]);

  useEffect(() => { setDuration(0); setTime(0); setPeaks(undefined); }, [id]);

  // Waveform: from cache, or decoded once per item (songs and spots only)
  const url = pfl.url;
  useEffect(() => {
    if (!id || !url || !duration) return;
    if (peaksCache.has(id)) { setPeaks(peaksCache.get(id) ?? null); return; }
    if (duration > WAVEFORM_MAX_SECONDS) { setPeaks(null); return; }
    let live = true;
    loadPeaks(url)
      .catch(() => null)
      .then(p => { peaksCache.set(id, p); if (live) setPeaks(p); });
    return () => { live = false; };
  }, [id, url, duration]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, [id]);

  const markers = markersOf(asset, duration);
  const shown = dragAt ?? time;
  const frac = duration ? Math.min(1, shown / duration) : 0;

  // Draw: mirrored bars, played part bright, the rest dim; ruler for long items
  useEffect(() => {
    const c = canvasRef.current;
    if (!c || !width) return;
    const h = c.clientHeight || 44;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(width * dpr)) c.width = Math.round(width * dpr);
    if (c.height !== Math.round(h * dpr)) c.height = Math.round(h * dpr);
    const g = c.getContext('2d');
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, width, h);
    const mid = h / 2;
    const playedX = frac * width;
    if (peaks) {
      const step = 3;
      const n = Math.floor(width / step);
      for (let i = 0; i < n; i++) {
        const x = i * step;
        const p = peaks[Math.min(BINS - 1, Math.floor((i / n) * BINS))];
        const bh = Math.max(1, p * (mid - 3));
        g.fillStyle = x < playedX ? '#2bff4a' : 'rgba(43,255,74,0.28)';
        g.fillRect(x, mid - bh, 2, bh * 2);
      }
    } else {
      // Loading or long item: a time ruler with the played part lit
      g.fillStyle = 'rgba(43,255,74,0.22)';
      g.fillRect(0, mid - 3, width, 6);
      g.fillStyle = '#2bff4a';
      g.fillRect(0, mid - 3, playedX, 6);
      if (duration) {
        const every = duration > 1800 ? 300 : duration > 600 ? 60 : duration > 120 ? 30 : 10;
        g.fillStyle = 'rgba(208,208,208,0.55)';
        for (let s = every; s < duration; s += every) {
          const x = (s / duration) * width;
          g.fillRect(x, mid - 8, 1, 16);
        }
      }
      if (peaks === undefined && duration && duration <= WAVEFORM_MAX_SECONDS) {
        // Shimmer while the waveform is being read
        const t = (performance.now() / 900) % 1;
        const grad = g.createLinearGradient((t - 0.3) * width, 0, (t + 0.3) * width, 0);
        grad.addColorStop(0, 'rgba(43,255,74,0)');
        grad.addColorStop(0.5, 'rgba(43,255,74,0.25)');
        grad.addColorStop(1, 'rgba(43,255,74,0)');
        g.fillStyle = grad;
        g.fillRect(0, 4, width, h - 8);
      }
    }
    // Markers
    if (duration) {
      for (const m of markers) {
        const x = Math.round((m.at / duration) * width) + 0.5;
        g.strokeStyle = m.color;
        g.globalAlpha = 0.85;
        g.lineWidth = 1;
        g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
        g.globalAlpha = 1;
        g.fillStyle = m.color;
        g.beginPath(); g.moveTo(x - 4, 0); g.lineTo(x + 4, 0); g.lineTo(x, 5); g.closePath(); g.fill();
      }
    }
    // Hover ghost
    if (hoverX !== null && dragAt === null) {
      g.fillStyle = 'rgba(255,255,255,0.35)';
      g.fillRect(Math.round(hoverX), 0, 1, h);
    }
    // Playhead with a soft glow
    g.save();
    g.shadowColor = '#2bff4a';
    g.shadowBlur = 8;
    g.fillStyle = '#ffffff';
    g.fillRect(Math.round(playedX) - 1, 0, 2, h);
    g.restore();
  }, [width, peaks, frac, duration, markers, hoverX, dragAt, compact, time]);

  const xToTime = useCallback((clientX: number) => {
    const el = wrapRef.current;
    if (!el || !duration) return 0;
    const r = el.getBoundingClientRect();
    return Math.min(duration, Math.max(0, ((clientX - r.left) / r.width) * duration));
  }, [duration]);

  const onDown = (e: React.PointerEvent) => {
    if (!duration) return;
    dragging.current = true;
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    setDragAt(xToTime(e.clientX));
    wrapRef.current?.focus();
  };
  const onMove = (e: React.PointerEvent) => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (r) setHoverX(Math.min(r.width, Math.max(0, e.clientX - r.left)));
    if (dragging.current) setDragAt(xToTime(e.clientX));
  };
  const onUp = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    dragging.current = false;
    const at = xToTime(e.clientX);
    pfl.seek(at);
    setTime(at);
    setDragAt(null);
  };

  const jump = (by: number) => { const at = Math.max(0, Math.min(duration || 0, time + by)); pfl.seek(at); setTime(at); };
  const jumpTo = (at: number) => { pfl.seek(at); setTime(at); };

  const onKey = (e: React.KeyboardEvent) => {
    const big = e.shiftKey ? 15 : 5;
    let handled = true;
    if (e.key === 'ArrowRight' || e.key === 'l' || e.key === 'L') jump(e.key === 'ArrowRight' ? big : 10);
    else if (e.key === 'ArrowLeft' || e.key === 'j' || e.key === 'J') jump(e.key === 'ArrowLeft' ? -big : -10);
    else if (e.key === ' ' || e.key === 'k' || e.key === 'K') void pfl.playPause();
    else if (e.key === 'Home') jumpTo(0);
    else if (e.key === 'End') jumpTo(Math.max(0, duration - 10));
    else if (e.key === 'Escape') pfl.stop();
    else if (/^[0-9]$/.test(e.key) && duration) jumpTo((Number(e.key) / 10) * duration);
    else handled = false;
    if (handled) { e.preventDefault(); e.stopPropagation(); }
  };

  if (!id) return null;
  const playing = !pfl.paused && pfl.playingId === id;
  const title = asset?.title ?? 'Loading…';
  const artist = asset?.artist;
  const hoverTime = hoverX !== null && width ? (hoverX / width) * duration : null;
  const remaining = Math.max(0, duration - shown);

  const jumps: { label: string; at: number; title: string }[] = [
    ...markers.map(m => ({ label: m.label === 'START' ? 'Start' : m.label === 'INTRO' ? 'Intro end' : m.label === 'MIX' ? 'Mix' : 'End', at: m.key === 'end' || m.key === 'mix' ? Math.max(0, m.at - 5) : m.at, title: m.key === 'end' || m.key === 'mix' ? `5 s before ${m.label.toLowerCase()}` : m.title })),
    ...(duration > 30 ? [{ label: 'Last 15 s', at: Math.max(0, duration - 15), title: 'Hear how it ends' }] : []),
  ];

  return (
    <section data-testid="pfl-deck" aria-label="Pre-listen player (only you hear it)"
      className="shrink-0 bg-black border-t-2 border-[#2bff4a] text-[#d0d0d0] px-2 py-1.5 select-none">
      <div className={`flex items-center gap-2 ${compact ? 'flex-wrap' : ''}`}>
        <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-bold bg-[#2bff4a] text-black rounded-sm" title="Pre-listen: only you hear it, not on air">PFL</span>
        <button type="button" onClick={() => void pfl.playPause()} data-testid="pfl-playpause"
          aria-label={playing ? 'Pause pre-listen' : 'Play pre-listen'}
          className="qn-btn shrink-0 w-11 h-11 text-lg leading-none">{playing ? '❚❚' : '▶'}</button>
        <button type="button" onClick={() => jump(-10)} aria-label="Back 10 seconds" title="Back 10 s (J)"
          className="qn-btn shrink-0 h-11 px-2 text-xs">−10</button>
        <button type="button" onClick={() => jump(10)} aria-label="Forward 10 seconds" title="Forward 10 s (L)"
          className="qn-btn shrink-0 h-11 px-2 text-xs">+10</button>
        <div className={`min-w-0 leading-tight ${compact ? 'flex-1' : 'w-56 xl:w-72 shrink-0'}`}>
          <div className="text-[#ffff40] font-bold text-sm truncate" title={title}>{title}</div>
          <div className="text-[11px] truncate opacity-80">{artist || 'Only you hear this, not on air'}</div>
        </div>
        <div className="shrink-0 leading-none text-right" aria-live="off">
          <div className="qn-digits text-xl" data-testid="pfl-elapsed">{fmt(shown)}</div>
          <div className="qn-digits text-xs !text-[#ffa64d]" data-testid="pfl-remaining">-{fmt(remaining)}</div>
        </div>
        {!compact && (
          <Scrub {...{ wrapRef, canvasRef, onDown, onMove, onUp, onKey, duration, shown, hoverX, hoverTime, dragAt, markers, setHoverX }} />
        )}
        <button type="button" onClick={pfl.stop} aria-label="Close pre-listen" title="Stop and close (Esc)"
          className="qn-btn shrink-0 w-9 h-11 text-sm">✕</button>
      </div>
      {compact && (
        <div className="mt-1.5 flex">
          <Scrub {...{ wrapRef, canvasRef, onDown, onMove, onUp, onKey, duration, shown, hoverX, hoverTime, dragAt, markers, setHoverX }} />
        </div>
      )}
      {jumps.length > 0 && (
        <div className="mt-1 flex items-center gap-1 overflow-x-auto text-[11px]" data-testid="pfl-jumps">
          <span className="opacity-60 shrink-0">Jump to</span>
          {jumps.map(j => (
            <button key={j.label} type="button" onClick={() => jumpTo(j.at)} title={j.title}
              className="shrink-0 px-2 h-7 rounded-sm border border-[#2bff4a]/40 text-[#2bff4a] hover:bg-[#2bff4a] hover:text-black">
              {j.label}
            </button>
          ))}
          <span className="ml-auto shrink-0 opacity-50 hidden lg:inline">Click or drag the wave · ←/→ 5 s · Space play/pause · 0–9 jump · Esc close</span>
        </div>
      )}
    </section>
  );
}

/** Marker labels that fit: one too close to the last shown one is left as a line only. */
function labelled(markers: Marker[], duration: number) {
  const out: { m: Marker; f: number }[] = [];
  const sorted = [...markers].sort((a, b) => a.at - b.at);
  for (const m of sorted) {
    const f = duration ? m.at / duration : 0;
    const last = out[out.length - 1];
    if (last && f - last.f < 0.06) {
      // INTRO says more than START (silence trimmed at the top), so it wins the space
      if (last.m.key === 'cue') out[out.length - 1] = { m, f };
      continue;
    }
    out.push({ m, f });
  }
  return out;
}

function Scrub(p: {
  wrapRef: React.RefObject<HTMLDivElement>;
  canvasRef: React.RefObject<HTMLCanvasElement>;
  onDown: (e: React.PointerEvent) => void;
  onMove: (e: React.PointerEvent) => void;
  onUp: (e: React.PointerEvent) => void;
  onKey: (e: React.KeyboardEvent) => void;
  duration: number;
  shown: number;
  hoverX: number | null;
  hoverTime: number | null;
  dragAt: number | null;
  markers: Marker[];
  setHoverX: (x: number | null) => void;
}) {
  const bubbleAt = p.dragAt !== null && p.duration ? null : p.hoverX;
  return (
    <div ref={p.wrapRef} role="slider" tabIndex={0} data-testid="pfl-scrub"
      aria-label="Position in the pre-listen. Click or drag to jump; arrow keys move 5 seconds"
      aria-valuemin={0} aria-valuemax={Math.round(p.duration)} aria-valuenow={Math.round(p.shown)}
      aria-valuetext={fmt(p.shown)}
      onPointerDown={p.onDown} onPointerMove={p.onMove} onPointerUp={p.onUp} onPointerCancel={p.onUp}
      onPointerLeave={() => p.setHoverX(null)} onKeyDown={p.onKey}
      className="relative flex-1 min-w-[160px] cursor-pointer touch-none rounded-sm bg-[#061206] ring-1 ring-[#2bff4a]/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#2bff4a]">
      <canvas ref={p.canvasRef} className="block w-full h-11 lg:h-[52px]" />
      {labelled(p.markers, p.duration).map(({ m, f }) => (
        <span key={m.key} title={m.title}
          className="absolute bottom-0 text-[9px] font-bold px-0.5 leading-tight pointer-events-none whitespace-nowrap"
          style={f > 0.85
            ? { right: `calc(${(1 - f) * 100}% + 2px)`, color: m.color }
            : { left: `calc(${f * 100}% + 2px)`, color: m.color }}>{m.label}</span>
      ))}
      {bubbleAt !== null && p.hoverTime !== null && (
        <span className="absolute -top-6 -translate-x-1/2 px-1.5 py-0.5 text-[11px] bg-black border border-[#2bff4a] text-[#2bff4a] rounded-sm pointer-events-none qn-digits"
          style={{ left: bubbleAt }}>{fmt(p.hoverTime)}</span>
      )}
      {p.dragAt !== null && (
        <span className="absolute -top-6 -translate-x-1/2 px-1.5 py-0.5 text-[11px] bg-[#2bff4a] text-black rounded-sm pointer-events-none font-bold"
          style={{ left: `${(p.dragAt / (p.duration || 1)) * 100}%` }}>{fmt(p.dragAt)}</span>
      )}
    </div>
  );
}
