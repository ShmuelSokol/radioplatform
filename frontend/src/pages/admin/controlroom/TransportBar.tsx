import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { assetLabel, fmtDur, typeStyle } from './shared';
import type { CtlAsset } from './shared';

interface StationOption { id: string; name: string }

export interface TransportAudio {
  audioReady: boolean;
  initAudio: () => void;
  volume: number;
  setVolume: (v: number) => void;
  muted: boolean;
  toggleMute: () => void;
  vuL: number; // 0..30
  vuR: number;
}

interface Props {
  stations: StationOption[];
  stationId: string | null;
  onStation: (id: string) => void;
  isPlaying: boolean;
  nowAsset: CtlAsset | null;
  nextAsset: CtlAsset | null;
  elapsed: number;
  remaining: number;
  duration: number;
  /** Seconds of intro left on the item on air; 0 when there is none or it has passed. */
  introSeconds: number;
  onStart: () => void;
  starting: boolean;
  statusMessage: string | null;
  /** Go to the next item. No argument is the plain Next (cut); 'fade' and 'soft' fade the item out first. */
  onNext: (fade?: 'hard' | 'fade' | 'soft') => void;
  nextBusy: boolean;
  /** The item on air is set to repeat. */
  looping: boolean;
  onLoop: () => void;
  loopBusy: boolean;
  audio: TransportAudio;
  stationTz?: string;
  liveShow: { id: string; title: string } | null;
}

function Clock({ tz }: { tz?: string }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const opts: Intl.DateTimeFormatOptions = { hour12: true, hour: 'numeric', minute: '2-digit', second: '2-digit' };
  if (tz) opts.timeZone = tz;
  return <>{now.toLocaleTimeString('en-US', opts)}</>;
}

function VuBar({ level }: { level: number }) {
  return (
    <div className="flex-1 h-1.5 bg-black/60 rounded-sm overflow-hidden flex">
      {Array.from({ length: 30 }).map((_, i) => (
        <div key={i} className={`flex-1 mx-px rounded-sm ${
          i < level
            ? (i < 18 ? 'bg-green-500' : i < 24 ? 'bg-yellow-500' : 'bg-red-500')
            : (i < 18 ? 'bg-green-900/30' : i < 24 ? 'bg-yellow-900/30' : 'bg-red-900/30')
        }`} />
      ))}
    </div>
  );
}

function FullscreenButton() {
  const supported = typeof document !== 'undefined' && !!document.fullscreenEnabled;
  const [on, setOn] = useState(false);
  useEffect(() => {
    const h = () => setOn(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', h);
    return () => document.removeEventListener('fullscreenchange', h);
  }, []);
  if (!supported) return null;
  return (
    <button
      type="button"
      onClick={() => {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        else document.documentElement.requestFullscreen().catch(() => {});
      }}
      title={on ? 'Exit full screen' : 'Full screen'}
      aria-label={on ? 'Exit full screen' : 'Full screen'}
      className="hidden lg:flex h-9 w-9 items-center justify-center rounded-md text-gray-400 hover:text-white hover:bg-white/10"
    >
      <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        {on
          ? <path strokeLinecap="round" strokeLinejoin="round" d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
          : <path strokeLinecap="round" strokeLinejoin="round" d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />}
      </svg>
    </button>
  );
}

function fmtCountdown(sec: number): string {
  if (!sec || sec <= 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function TransportBar(p: Props) {
  const progress = p.duration > 0 ? Math.min(100, (p.elapsed / p.duration) * 100) : 0;
  const ending = p.isPlaying && p.remaining > 0 && p.remaining <= 10;
  const { audio } = p;

  const onAir = p.isPlaying ? (
    <span className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md bg-red-600 text-white text-xs font-extrabold tracking-wider shrink-0">
      <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
      ON AIR
    </span>
  ) : (
    <button
      type="button"
      onClick={p.onStart}
      disabled={p.starting || !p.stationId}
      className="h-9 px-4 rounded-md bg-green-600 hover:bg-green-500 text-white text-sm font-bold shrink-0 disabled:opacity-50"
    >
      {p.starting ? 'Starting…' : '▶ Start'}
    </button>
  );

  const stationSelect = p.stations.length > 1 ? (
    <select
      aria-label="Station"
      value={p.stationId ?? ''}
      onChange={(e) => p.onStation(e.target.value)}
      className="h-8 lg:h-9 max-w-[60vw] lg:max-w-[220px] bg-[#1a1a4e] border border-[#3a3a7e] text-cyan-100 text-sm px-2 rounded-md focus:outline-none focus:border-cyan-500"
    >
      {p.stations.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
    </select>
  ) : (
    <span className="text-sm font-semibold text-cyan-200 truncate max-w-[42vw] lg:max-w-[220px]">
      {p.stations[0]?.name ?? 'No station'}
    </span>
  );

  const fadeBtn = 'h-11 lg:h-12 px-2.5 lg:px-3 rounded-lg border border-amber-500/60 bg-amber-500/10 hover:bg-amber-500/25 active:bg-amber-500/40 text-amber-200 text-xs lg:text-sm font-bold shrink-0 disabled:opacity-40 disabled:cursor-not-allowed';
  const nextButton = (
    <div className="flex items-center gap-1.5 shrink-0" role="group" aria-label="Go to the next item">
      <button type="button" onClick={p.onLoop} disabled={!p.isPlaying || p.loopBusy}
        data-testid="loop" aria-pressed={p.looping}
        title={p.looping ? 'Loop is on: this item repeats. Press to switch it off.' : 'Repeat this item until you switch Loop off or press Next'}
        className={`h-11 lg:h-12 px-2.5 lg:px-3 rounded-lg border text-xs lg:text-sm font-bold shrink-0 disabled:opacity-40 disabled:cursor-not-allowed
          ${p.looping ? 'bg-fuchsia-500 border-fuchsia-300 text-white animate-pulse' : 'border-fuchsia-400/50 bg-fuchsia-500/10 hover:bg-fuchsia-500/25 text-fuchsia-200'}`}>
        Loop
      </button>
      <button type="button" onClick={() => p.onNext('fade')} disabled={!p.isPlaying || p.nextBusy}
        data-testid="next-fade" title="Fade this item out quickly, then go to the next" className={fadeBtn}>
        Fade
      </button>
      <button type="button" onClick={() => p.onNext('soft')} disabled={!p.isPlaying || p.nextBusy}
        data-testid="next-soft" title="Fade this item out slowly (3 seconds), then go to the next" className={fadeBtn}>
        <span className="sm:hidden">Soft</span><span className="hidden sm:inline">Soft fade</span>
      </button>
      <button
        type="button"
        onClick={() => p.onNext('hard')}
        disabled={!p.isPlaying || p.nextBusy}
        title="Go to the next item now, with no fade (N)"
        className="h-11 lg:h-12 px-3 sm:px-4 lg:px-5 rounded-lg bg-amber-500 hover:bg-amber-400 active:bg-amber-600 text-black text-base font-extrabold tracking-wide shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {p.nextBusy ? '…' : <>NEXT<span className="hidden sm:inline"> ⏭</span></>}
      </button>
    </div>
  );
  const intro = p.introSeconds > 0 ? (
    <div className="text-center leading-none shrink-0" data-testid="intro-countdown" title="Time left before the vocal starts">
      <div className="text-2xl lg:text-3xl xl:text-4xl font-extrabold tabular-nums text-emerald-300">{fmtCountdown(Math.ceil(p.introSeconds))}</div>
      <div className="text-[10px] lg:text-[11px] font-bold uppercase tracking-wider text-emerald-500 mt-0.5">Intro</div>
    </div>
  ) : null;

  const sound = !audio.audioReady ? (
    <button
      type="button"
      onClick={audio.initAudio}
      title="Hear the station in this browser"
      className="h-9 px-3 rounded-md bg-cyan-800 hover:bg-cyan-700 text-cyan-50 text-xs font-semibold shrink-0"
    >
      🔊<span className="hidden sm:inline"> Listen</span>
    </button>
  ) : (
    <div className="flex items-center gap-2 shrink-0">
      <button
        type="button"
        onClick={audio.toggleMute}
        aria-label={audio.muted ? 'Unmute' : 'Mute'}
        className="h-9 w-9 flex items-center justify-center rounded-md text-lg hover:bg-white/10"
      >
        {audio.muted ? '🔇' : audio.volume > 0.5 ? '🔊' : '🔉'}
      </button>
      <input
        type="range" min="0" max="1" step="0.01" value={audio.volume}
        aria-label="Monitor volume"
        onChange={e => audio.setVolume(parseFloat(e.target.value))}
        className="hidden sm:block xl:hidden 2xl:block w-20 2xl:w-28 h-1 accent-cyan-400 cursor-pointer"
      />
    </div>
  );

  return (
    <header className="shrink-0 bg-[#0c0c2c] border-b border-[#2a2a5e] text-gray-200">
      {/* Row 1: status, title, clock */}
      <div className="flex items-center gap-2 lg:gap-4 px-3 lg:px-4 pt-2 xl:py-2">
        {onAir}
        <div className="hidden lg:block shrink-0">{stationSelect}</div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="truncate text-base lg:text-xl font-bold text-yellow-300 leading-tight">
              {p.nowAsset ? assetLabel(p.nowAsset) : (p.statusMessage ?? 'Stopped. Press Start to begin.')}
            </span>
            {p.nowAsset?.asset_type && (
              <span className={`hidden sm:inline text-[11px] px-1.5 py-0.5 rounded shrink-0 ${typeStyle(p.nowAsset.asset_type).chip}`}>
                {p.nowAsset.asset_type}
              </span>
            )}
          </div>
          <div className="truncate text-xs lg:text-sm text-gray-400 leading-tight">
            <span className="text-gray-500">Next: </span>
            <span className="text-green-300">{p.nextAsset ? assetLabel(p.nextAsset) : 'nothing queued'}</span>
          </div>
        </div>

        {p.liveShow && (
          <Link
            to={`/admin/live/${p.liveShow.id}/host`}
            className="hidden md:inline-flex items-center gap-1.5 h-9 px-3 rounded-md bg-red-900/70 border border-red-500 text-red-100 text-xs font-bold shrink-0 max-w-[200px]"
            title="A live show is on. Open the host console."
          >
            <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse shrink-0" />
            <span className="truncate">LIVE: {p.liveShow.title}</span>
          </Link>
        )}

        <div className="hidden xl:flex items-center gap-3">
          {intro}
          <div className="text-right leading-none">
            <div className={`text-3xl xl:text-4xl font-extrabold tabular-nums ${ending ? 'text-red-400 animate-pulse' : 'text-white'}`}>
              -{fmtCountdown(p.remaining)}
            </div>
            <div className="text-[11px] text-gray-500 tabular-nums mt-0.5">
              {fmtDur(p.elapsed)} / {fmtDur(p.duration)}
            </div>
          </div>
          {nextButton}
          {sound}
          <div className="hidden xl:flex w-32 2xl:w-56 flex-col gap-1" aria-hidden="true" title={audio.audioReady ? 'Level of what you are hearing' : 'Press Listen to see levels'}>
            <VuBar level={audio.vuL} />
            <VuBar level={audio.vuR} />
          </div>
        </div>

        <div className="text-right shrink-0 leading-none">
          <div className="text-base lg:text-2xl font-bold tabular-nums text-red-300"><Clock tz={p.stationTz} /></div>
          {p.stationTz && (
            <div className="hidden lg:block text-[10px] text-gray-500 mt-0.5">
              {p.stationTz.split('/').pop()?.replace(/_/g, ' ')}
            </div>
          )}
        </div>
        <FullscreenButton />
      </div>

      {/* Row 2 (phones and tablets): countdown, Next, sound */}
      <div className="flex xl:hidden flex-wrap items-center gap-x-2 gap-y-1.5 px-3 lg:px-4 py-2">
        <div className={`text-3xl font-extrabold tabular-nums leading-none ${ending ? 'text-red-400 animate-pulse' : 'text-white'}`}>
          -{fmtCountdown(p.remaining)}
        </div>
        <div className="hidden sm:block text-[11px] text-gray-500 tabular-nums leading-tight">
          <div>{fmtDur(p.elapsed)}</div>
          <div>{fmtDur(p.duration)}</div>
        </div>
        {intro}
        <div className="flex-1" />
        {sound}
        {nextButton}
      </div>

      {/* Progress */}
      <div className="h-1.5 bg-black/60" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}>
        <div
          className={`h-full ${ending ? 'bg-red-500' : 'bg-gradient-to-r from-green-500 to-yellow-400'}`}
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Station picker on small screens, only when there is a choice */}
      {p.stations.length > 1 && (
        <div className="lg:hidden px-3 py-1 border-t border-[#1e1e4a] flex items-center gap-2">
          <span className="text-[11px] text-gray-500">Station</span>
          {stationSelect}
        </div>
      )}
    </header>
  );
}
