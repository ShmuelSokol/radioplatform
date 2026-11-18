import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import PlayoutBadge from '../../../components/audio/PlayoutBadge';
import { Link } from 'react-router-dom';
import { assetLabel, fmtDur } from './shared';
import { serverNow } from '../../../lib/serverClock';
import './classic.css';
import type { CtlAsset } from './shared';

interface StationOption { id: string; name: string }

export interface TransportAudio {
  /** Sound really comes out of this device (Listen was pressed and the browser lets it play). */
  audioReady: boolean;
  /** Listen was pressed and the sound is on its way (the server stream is connecting). */
  starting?: boolean;
  /** The Listen button: switch sound on here. Must run inside the tap. */
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
  /** When a hard timed item will cut the item on air before its end (ISO time); Remaining then counts to it. */
  cutAt?: string | null;
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
  /** Phone held sideways: the compact left column (classic.css "phone landscape"). */
  landscape?: boolean;
  /** Sideways only: HS, Spots and Panic, in the button grid under Go next. */
  modeButtons?: ReactNode;
  /** Sideways only: the pre-listen deck, under the buttons (so the queue keeps its rows). */
  pflDeck?: ReactNode;
}

function Clock({ tz }: { tz?: string }) {
  // The station's time, on the server's clock (a wrong device clock showed the wrong time)
  const [now, setNow] = useState(() => new Date(serverNow()));
  useEffect(() => {
    const t = setInterval(() => setNow(new Date(serverNow())), 1000);
    return () => clearInterval(t);
  }, []);
  const opts: Intl.DateTimeFormatOptions = { hour12: true, hour: 'numeric', minute: '2-digit', second: '2-digit' };
  if (tz) opts.timeZone = tz;
  const [time, ampm] = now.toLocaleTimeString('en-US', opts).split(' ');
  return <>{time}<span className="text-[0.45em] ml-1 not-italic">{ampm}</span></>;
}

/** A level meter in the style operators know: cyan segments, red near the top. */
function VuBar({ level, label }: { level: number; label: string }) {
  return (
    <div className="flex items-center gap-1" title="Station level (RMS), -40 to 0 dBFS; red is above -8 dBFS">
      <span className="w-9 text-[9px] italic text-cyan-200 leading-none">{label}</span>
      <div className="flex-1 h-2.5 flex gap-px bg-black">
        {Array.from({ length: 30 }).map((_, i) => (
          <div key={i} className={`flex-1 ${
            i < level ? (i < 24 ? 'bg-cyan-400' : 'bg-red-500') : (i < 24 ? 'bg-cyan-950' : 'bg-red-950')
          }`} />
        ))}
      </div>
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
      className="qn-btn hidden lg:flex h-8 w-8 items-center justify-center"
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

/** Loop, Go next, Fade, Soft fade: the transport buttons, stacked on a desktop, in a row on a phone. */
export function TransportButtons({ p, stacked, grid = false }: { p: Props; stacked: boolean; grid?: boolean }) {
  // grid: the phone-sideways button grid (Go next two cells wide first, then Fade,
  // Soft fade, and Loop on the next row); the parent is the 4-column grid.
  const btn = grid ? 'qn-btn h-11 px-1 text-xs min-w-0'
    : `qn-btn shrink-0 ${stacked ? 'w-full h-9 [@media(max-height:800px)]:h-8 text-sm' : 'h-11 px-2.5 text-xs'}`;
  return (
    <div className={grid ? 'contents' : stacked ? 'flex flex-col gap-1' : 'flex items-center gap-1 shrink-0'} role="group" aria-label="Go to the next item">
      <button type="button" onClick={p.onLoop} disabled={!p.isPlaying || p.loopBusy}
        data-testid="loop" aria-pressed={p.looping}
        title={p.looping ? 'Loop is on: this item repeats. Press to switch it off.' : 'Repeat this item until you switch Loop off or press Next'}
        className={`${btn} ${grid ? 'order-4' : ''} ${p.looping ? '!bg-fuchsia-300' : ''}`}>
        Loop
      </button>
      <button type="button" onClick={() => p.onNext('hard')} disabled={!p.isPlaying || p.nextBusy}
        title="Go to the next item now, with no fade (N)" data-testid="go-next"
        className={`${btn} ${grid ? 'order-1 col-span-2 !text-base' : stacked ? '!h-11 [@media(max-height:800px)]:!h-9 !text-base' : '!px-3 !text-sm'} font-bold`}>
        {p.nextBusy ? '…' : <><u>G</u>o next</>}
      </button>
      <button type="button" onClick={() => p.onNext('fade')} disabled={!p.isPlaying || p.nextBusy}
        data-testid="next-fade" title="Fade this item out quickly, then go to the next" className={`${btn} ${grid ? 'order-2' : ''}`}>
        Fade
      </button>
      <button type="button" onClick={() => p.onNext('soft')} disabled={!p.isPlaying || p.nextBusy}
        data-testid="next-soft" title="Fade this item out slowly (3 seconds), then go to the next" className={`${btn} ${grid ? 'order-3' : ''}`}>
        Soft fade
      </button>
    </div>
  );
}

export default function TransportBar(p: Props) {
  // With a cut coming, the bar fills up to the cut (elapsed + remaining), not to the file's end.
  const barTotal = p.cutAt ? p.elapsed + p.remaining : p.duration;
  const progress = barTotal > 0 ? Math.min(100, (p.elapsed / barTotal) * 100) : 0;
  const cutClock = p.cutAt ? new Date(p.cutAt).toLocaleTimeString('en-US', {
    hour: 'numeric', minute: '2-digit', ...(p.stationTz ? { timeZone: p.stationTz } : {}),
  }) : null;
  const ending = p.isPlaying && p.remaining > 0 && p.remaining <= 10;
  const { audio } = p;

  const status = p.isPlaying ? (
    <span className="text-red-500 font-bold text-xs tracking-wide">PLAYING</span>
  ) : (
    <button type="button" onClick={p.onStart} disabled={p.starting || !p.stationId}
      className="qn-btn h-8 px-3 text-sm font-bold shrink-0">
      {p.starting ? 'Starting…' : '▶ Start'}
    </button>
  );

  const stationSelect = p.stations.length > 1 ? (
    <select
      aria-label="Station"
      value={p.stationId ?? ''}
      onChange={(e) => p.onStation(e.target.value)}
      className="qn-field h-7 max-w-[60vw] lg:max-w-[200px] text-sm px-1.5"
    >
      {p.stations.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
    </select>
  ) : (
    <span className="text-sm font-bold text-[var(--qn-digit)] truncate max-w-[42vw] lg:max-w-[200px]">
      {p.stations[0]?.name ?? 'No station'}
    </span>
  );

  const remainingText = p.isPlaying ? fmtCountdown(p.remaining) : '0:00';
  const remainingCls = ending ? '!text-red-500 animate-pulse' : !p.isPlaying ? '!text-green-900' : '';
  const intro = p.introSeconds > 0 ? (
    <div className="leading-none shrink-0" data-testid="intro-countdown" title="Time left before the vocal starts">
      <div className="qn-lcd-label">Intro</div>
      <div className="qn-digits text-2xl lg:text-3xl !text-yellow-300">{fmtCountdown(Math.ceil(p.introSeconds))}</div>
    </div>
  ) : (
    <div className="hidden lg:block leading-none shrink-0 w-14" aria-hidden="true">
      <div className="qn-lcd-label">Intro</div>
      <div className="qn-digits text-3xl !text-green-900">&nbsp;</div>
    </div>
  );

  // Sound on this device. Off (or remembered but not yet allowed by the browser): one
  // big button that says Listen, on a phone too. It used to be a 32 px speaker icon
  // with no word on a phone, and with listening remembered the Mute control showed
  // over silence (Shmuel, 2026-11-17: "where do I hit play?").
  const soundControl = (size: 'desk' | 'phone' | 'land') => {
    const big = size !== 'desk';
    if (!audio.audioReady) {
      return (
        <button type="button" onClick={audio.initAudio} title="Hear the station on this device"
          data-testid={audio.starting ? 'listen-starting' : 'listen-here'} data-listen-button
          className={`qn-btn cr-listen shrink-0 font-bold ${big ? 'h-11 min-w-[128px] px-3 text-base' : 'h-8 px-2.5 text-xs'}`}>
          {audio.starting ? <>🔊 Starting…<span className="sr-only"> Listen</span></> : '🔊 Listen'}
        </button>
      );
    }
    return (
      <div className="flex items-center gap-1.5 shrink-0">
        <button type="button" onClick={audio.toggleMute} aria-label={audio.muted ? 'Unmute' : 'Mute'}
          data-testid="sound-control"
          title={audio.muted ? 'Muted on this device. Press to hear the station again.' : 'The station is playing on this device. Press to mute it here.'}
          className={`qn-btn flex items-center justify-center gap-1 ${big ? 'h-11 px-2.5 text-sm font-bold' : 'h-8 w-8 text-base'} ${audio.muted && big ? 'cr-listen' : ''}`}>
          <span aria-hidden="true">{audio.muted ? '🔇' : audio.volume > 0.5 ? '🔊' : '🔉'}</span>
          {big && <span>{audio.muted ? 'Muted' : 'Sound on'}</span>}
        </button>
        {!big && (
          <input type="range" min="0" max="1" step="0.01" value={audio.volume} aria-label="Monitor volume"
            onChange={e => audio.setVolume(parseFloat(e.target.value))}
            className="hidden 2xl:block w-24 h-1 accent-cyan-400 cursor-pointer" />
        )}
      </div>
    );
  };

  const nowType = p.nowAsset?.category || p.nowAsset?.asset_type || '';
  const nextType = p.nextAsset?.category || p.nextAsset?.asset_type || '';

  if (p.landscape) {
    // Phone held sideways: a compact left column that fits a 300 px tall screen.
    // One LCD row (status, remaining, clock), on air and next, then the buttons in a
    // grid of 44 px cells: Go next first, Panic always on screen, Listen and station last.
    return (
      <header className="shrink-0 qn-lcd cr-land" data-testid="transport-landscape">
        <div className="flex items-center gap-1.5 px-1.5 py-1 bg-black min-w-0">
          <div className="flex flex-col items-start gap-0.5 shrink-0 leading-none">
            {p.isPlaying ? status : (
              <button type="button" onClick={p.onStart} disabled={p.starting || !p.stationId}
                className="qn-btn h-11 px-3 text-sm font-bold shrink-0">
                {p.starting ? 'Starting…' : '▶ Start'}
              </button>
            )}
            {p.isPlaying && (
              <span className={`qn-digits text-[13px] ${p.isPlaying ? '' : '!text-green-900'}`} title="On air: elapsed / length">
                {fmtDur(p.elapsed)}<span className="opacity-70"> / {fmtDur(p.duration)}</span>
              </span>
            )}
          </div>
          <div className="leading-none shrink-0"
            title={cutClock ? `This item fades at ${cutClock} for a timed item` : undefined}>
            <div className="qn-lcd-label" data-testid="remaining-label">
              {cutClock ? <span className="!text-yellow-300">Fades {cutClock}</span> : 'Remaining'}
            </div>
            <div className={`qn-digits text-[28px] ${remainingCls}`} data-testid="remaining">{remainingText}</div>
          </div>
          {p.introSeconds > 0 && intro}
          <div className="flex-1" />
          <div className="leading-none shrink-0 text-right">
            <div className="qn-lcd-label">Clock</div>
            <div className="qn-digits text-base"><Clock tz={p.stationTz} /></div>
          </div>
        </div>
        <div className="px-1 pb-1 pt-0.5 space-y-[3px] bg-[#1c1c1c]">
          <div className="bg-black qn-sunken px-2 h-8 flex items-center" title={nowType}>
            <span className="truncate text-base font-bold text-[var(--qn-digit)] leading-none">
              {p.nowAsset ? assetLabel(p.nowAsset) : (p.statusMessage ?? 'Stopped. Press Start to begin.')}
            </span>
          </div>
          <div className="bg-black qn-sunken px-2 h-7 flex items-center" title={nextType}>
            <span className="truncate text-sm font-bold text-[var(--qn-yellow)] leading-none">
              <span className="sr-only">Next: </span>{p.nextAsset ? assetLabel(p.nextAsset) : 'nothing queued'}
            </span>
          </div>
        </div>
        <div className="h-1 bg-black" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}>
          <div className={`h-full ${ending ? 'bg-red-500' : 'bg-[var(--qn-digit)]'}`} style={{ width: `${progress}%` }} />
        </div>
        <div className="bg-[#c0c0c0] p-1 space-y-1 text-black">
          <div className="grid grid-cols-4 gap-1" data-testid="landscape-buttons">
            <TransportButtons p={p} stacked={false} grid />
            {p.modeButtons}
          </div>
          {p.pflDeck}
          <div className="flex items-center gap-1">
            {soundControl('land')}
            {p.liveShow && (
              <Link to={`/admin/live/${p.liveShow.id}/host`}
                className="inline-flex items-center gap-1 h-11 px-2 bg-red-700 text-white text-xs font-bold shrink-0 max-w-[110px]"
                title="A live show is on. Open the host console.">
                <span className="w-2 h-2 rounded-full bg-white animate-pulse shrink-0" />
                <span className="truncate">LIVE</span>
              </Link>
            )}
            {p.stations.length > 1 ? (
              <select
                aria-label="Station"
                value={p.stationId ?? ''}
                onChange={(e) => p.onStation(e.target.value)}
                className="qn-field h-11 flex-1 min-w-0 text-sm px-1.5"
              >
                {p.stations.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            ) : (
              <span className="flex-1 min-w-0 truncate text-sm font-bold">{p.stations[0]?.name ?? 'No station'}</span>
            )}
          </div>
          <PlayoutBadge stationId={p.stationId} className="!text-black" />
        </div>
      </header>
    );
  }

  return (
    <header className="shrink-0 qn-lcd border-b-2 border-[#808080]">
      {/* LCD strip: status, remaining, intro, on air, levels, clock */}
      <div className="flex items-center gap-3 lg:gap-5 px-2 lg:px-3 py-1.5 bg-black">
        <div className="flex flex-col items-start gap-1 shrink-0 min-w-[70px]">
          {status}
          <span className="hidden lg:block">{stationSelect}</span>
          <PlayoutBadge stationId={p.stationId} dark className="hidden lg:inline-flex" />
        </div>
        <div className="leading-none shrink-0"
          title={cutClock ? `This item fades at ${cutClock} for a timed item` : undefined}>
          <div className="qn-lcd-label" data-testid="remaining-label">
            Remaining{cutClock && <span className="!text-yellow-300"> · fades {cutClock}</span>}
          </div>
          <div className={`qn-digits text-3xl lg:text-[44px] ${remainingCls}`} data-testid="remaining">{remainingText}</div>
        </div>
        {intro}
        <div className="hidden sm:block leading-none shrink-0">
          <div className="qn-lcd-label">On air</div>
          <div className={`qn-digits text-2xl lg:text-[34px] ${p.isPlaying ? '' : '!text-green-900'}`}>{fmtDur(p.elapsed)}<span className="text-base lg:text-xl opacity-70"> / {fmtDur(p.duration)}</span></div>
        </div>
        <div className="hidden lg:flex flex-1 min-w-[160px] max-w-[420px] flex-col gap-1" aria-hidden="true"
          title={audio.audioReady ? 'Level of what you are hearing' : 'Press Listen to see levels'}>
          <VuBar level={audio.vuL} label="LEFT" />
          <VuBar level={audio.vuR} label="RIGHT" />
          <div className="flex justify-between pl-10 text-[9px] italic text-cyan-200 leading-none">
            <span>-40</span><span>-30</span><span>-24</span><span>-18</span><span>-12</span><span>-9</span><span>-6</span><span>-3</span><span>0</span>
          </div>
        </div>
        <div className="flex-1 lg:hidden" />
        {p.liveShow && (
          <Link to={`/admin/live/${p.liveShow.id}/host`}
            className="hidden md:inline-flex items-center gap-1.5 h-8 px-2 bg-red-700 text-white text-xs font-bold shrink-0 max-w-[180px]"
            title="A live show is on. Open the host console.">
            <span className="w-2 h-2 rounded-full bg-white animate-pulse shrink-0" />
            <span className="truncate">LIVE: {p.liveShow.title}</span>
          </Link>
        )}
        <div className="leading-none shrink-0 text-right">
          <div className="qn-lcd-label">Clock</div>
          <div className="qn-digits text-xl lg:text-[34px]"><Clock tz={p.stationTz} /></div>
        </div>
        <div className="hidden lg:flex items-center gap-1.5 shrink-0">
          {soundControl('desk')}
          <FullscreenButton />
        </div>
      </div>

      {/* On air and next, as on the old program */}
      <div className="px-1 lg:px-2 pb-1 space-y-[3px] bg-[#1c1c1c] pt-1">
        <div className="flex gap-[3px]">
          <div className="flex-1 min-w-0 bg-black qn-sunken px-2 h-8 lg:h-9 flex items-center">
            <span className="truncate text-lg lg:text-2xl font-bold text-[var(--qn-digit)] leading-none">
              {p.nowAsset ? assetLabel(p.nowAsset) : (p.statusMessage ?? 'Stopped. Press Start to begin.')}
            </span>
          </div>
          <div className="hidden sm:flex w-32 lg:w-44 shrink-0 bg-black qn-sunken px-2 items-center">
            <span className="truncate text-base lg:text-xl font-bold text-[var(--qn-digit)]">{nowType}</span>
          </div>
        </div>
        <div className="flex gap-[3px]">
          <div className="flex-1 min-w-0 bg-black qn-sunken px-2 h-7 lg:h-8 flex items-center">
            <span className="truncate text-base lg:text-xl font-bold text-[var(--qn-yellow)] leading-none">
              <span className="sr-only">Next: </span>{p.nextAsset ? assetLabel(p.nextAsset) : 'nothing queued'}
            </span>
          </div>
          <div className="hidden sm:flex w-32 lg:w-44 shrink-0 bg-black qn-sunken px-2 items-center">
            <span className="truncate text-sm lg:text-lg font-bold text-[var(--qn-yellow)]">{nextType}</span>
          </div>
        </div>
      </div>

      {/* Progress */}
      <div className="h-1 bg-black" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}>
        <div className={`h-full ${ending ? 'bg-red-500' : 'bg-[var(--qn-digit)]'}`} style={{ width: `${progress}%` }} />
      </div>

      {/* Phones and tablets: buttons and sound under the titles */}
      <div className="lg:hidden flex flex-wrap items-center gap-1.5 px-2 py-1.5 bg-[#c0c0c0]">
        {soundControl('phone')}
        <PlayoutBadge stationId={p.stationId} className="!text-black" />
        <div className="flex-1" />
        <TransportButtons p={p} stacked={false} />
      </div>
      {p.stations.length > 1 && (
        <div className="lg:hidden px-2 py-1 bg-[#c0c0c0] border-t border-[#808080] flex items-center gap-2 text-black">
          <span className="text-[11px]">Station</span>
          {stationSelect}
        </div>
      )}
    </header>
  );
}

export type { Props as TransportProps };
