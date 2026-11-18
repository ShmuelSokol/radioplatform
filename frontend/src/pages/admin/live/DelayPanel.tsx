import { useCallback, useEffect, useRef, useState } from 'react';
import { dumpShow, getAirStatus, getDumpAudio, getShowDumps, setShowDelay } from '../../../api/liveShows';
import type { AirStatus, DumpRecord } from '../../../api/liveShows';

/**
 * The broadcast delay of a live show: how many seconds are waiting between the
 * host's mouth and the listeners, and the Dump button that throws them away.
 * Used on the Host Console and the Call Screener (a producer often holds the
 * Dump). The server pushes `air` twice a second; until the first push arrives
 * the panel asks once. See wiki/live-shows.md, "Broadcast delay".
 */
const LENGTHS = [0, 5, 7, 10, 15, 20, 30];

const STATE: Record<string, { label: string; tone: string; help: string }> = {
  off: { label: 'NOT ON AIR', tone: 'bg-gray-700 text-gray-200', help: 'Press the green button to go on air.' },
  'pre-roll': { label: 'FILLING', tone: 'bg-yellow-600 text-white', help: 'The music is still playing. Listeners hear you when the delay is full.' },
  steady: { label: 'SAFE', tone: 'bg-green-700 text-white', help: 'Dump cuts what was just said before listeners hear it.' },
  building: { label: 'BUILDING', tone: 'bg-yellow-600 text-white', help: 'The delay is filling back up while you talk. Until it is full, Dump cuts less.' },
  shrinking: { label: 'CATCHING UP', tone: 'bg-green-700 text-white', help: 'The delay is a little long and is coming back to its length.' },
  exiting: { label: 'EXITING DELAY', tone: 'bg-orange-600 text-white', help: 'Winding down to really live. Dump cuts only what is left.' },
  'no delay': { label: 'LIVE, NO DELAY', tone: 'bg-red-700 text-white', help: 'Listeners hear you at once. Nothing can be cut.' },
  insert: { label: 'TIME CHECK ON AIR', tone: 'bg-blue-700 text-white', help: 'Keep talking. Listeners hear it, then carry on with you where you were.' },
};

function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export default function DelayPanel({ showId, air: pushed, lastDump, callerOnAir, compact = false }: {
  showId: string | undefined;
  air: AirStatus | null;
  lastDump: { seconds: number; by: string; at: number } | null;
  callerOnAir: boolean;
  compact?: boolean;
}) {
  const [fetched, setFetched] = useState<AirStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; bad: boolean } | null>(null);
  const [flash, setFlash] = useState(false);
  const [length, setLength] = useState<number | null>(null);
  const messageTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The dump log: every Dump of this show, with a recording of what was cut
  const [dumps, setDumps] = useState<DumpRecord[]>([]);
  const [playing, setPlaying] = useState<string | null>(null);
  const [logNote, setLogNote] = useState<string | null>(null);
  const player = useRef<{ audio: HTMLAudioElement; url: string } | null>(null);
  const loadDumps = useCallback(() => {
    if (showId) getShowDumps(showId).then(setDumps).catch(() => {});
  }, [showId]);
  const dumpAt = lastDump?.at;
  useEffect(() => {
    loadDumps();
    // The recording is stored a moment after the cut
    const id = setTimeout(loadDumps, 1500);
    return () => clearTimeout(id);
  }, [loadDumps, dumpAt]);
  const stopPlaying = useCallback(() => {
    if (player.current) {
      player.current.audio.pause();
      URL.revokeObjectURL(player.current.url);
      player.current = null;
    }
    setPlaying(null);
  }, []);
  useEffect(() => stopPlaying, [stopPlaying]);
  const playDump = async (d: DumpRecord) => {
    if (!showId) return;
    if (playing === d.id) { stopPlaying(); return; }
    stopPlaying();
    setLogNote(null);
    try {
      const url = URL.createObjectURL(await getDumpAudio(showId, d.id));
      const audio = new Audio(url);
      player.current = { audio, url };
      audio.onended = stopPlaying;
      setPlaying(d.id);
      await audio.play();
    } catch {
      stopPlaying();
      setLogNote('That recording could not be played. Try again in a moment.');
    }
  };

  // The server pushes while on air; before that (and if a push is missed) ask
  useEffect(() => {
    if (!showId) return;
    let stop = false;
    const ask = () => getAirStatus(showId).then(a => { if (!stop) setFetched(a); }).catch(() => {});
    ask();
    const id = setInterval(ask, 5000);
    return () => { stop = true; clearInterval(id); };
  }, [showId]);
  useEffect(() => { if (pushed) setFetched(null); }, [pushed]);

  const air = pushed ?? fetched;
  const mode = air?.mode ?? 'off';
  const state = STATE[mode] ?? STATE.off;
  const configured = air?.configured ?? 0;
  const delay = air?.delay ?? 0;
  const onAir = !!air && mode !== 'off';
  const canDump = onAir && delay >= 0.5;
  const shown = (n: number) => (n < 2 ? n.toFixed(1) : n.toFixed(0));
  const scale = Math.max(configured, air?.target ?? 0, delay, 1);

  const say = useCallback((text: string, bad = false) => {
    setMessage({ text, bad });
    if (messageTimer.current) clearTimeout(messageTimer.current);
    messageTimer.current = setTimeout(() => setMessage(null), 8000);
  }, []);
  useEffect(() => () => { if (messageTimer.current) clearTimeout(messageTimer.current); }, []);

  const dump = useCallback(async (dropCaller: boolean) => {
    if (!showId || busy) return;
    setBusy(true);
    try {
      const r = await dumpShow(showId, dropCaller);
      setFetched(r.air);
      setFlash(true);
      setTimeout(() => setFlash(false), 1200);
      loadDumps();
      setTimeout(loadDumps, 1500);
      say(`Dumped ${r.dumped_seconds.toFixed(1)} s. Listeners never heard it.${r.dropped_callers.length ? ' The caller was taken off.' : ''}`);
    } catch (err: any) {
      say(err?.response?.data?.detail || 'Dump failed', true);
    } finally {
      setBusy(false);
    }
  }, [showId, busy, say, loadDumps]);

  // Shift+D dumps from anywhere on the page (not while typing in a box)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)) return;
      if (e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && e.key.toLowerCase() === 'd' && !e.repeat) {
        e.preventDefault();
        if (canDump) dump(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [canDump, dump]);

  const change = async (action: 'exit' | 'build', seconds?: number) => {
    if (!showId || busy) return;
    setBusy(true);
    try {
      const a = await setShowDelay(showId, action, seconds);
      setFetched(a);
      say(action === 'exit'
        ? 'Exiting the delay: you will be really live in a moment.'
        : `Building the delay to ${a.target} s while you talk.`);
    } catch (err: any) {
      say(err?.response?.data?.detail || 'Could not change the delay', true);
    } finally {
      setBusy(false);
    }
  };

  const chosen = length ?? configured;
  const dumpWords = !air || air.dump_size <= 0 || air.dump_size >= delay
    ? `cuts all ${shown(delay)} s waiting`
    : `cuts the last ${air.dump_size.toFixed(0)} s`;

  return (
    <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4" data-testid="delay-panel" data-mode={mode}>
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="text-[11px] text-gray-400 uppercase">Broadcast Delay</div>
        <span className={`text-[11px] px-2 py-0.5 rounded-full font-bold ${state.tone}`} data-testid="delay-state">
          {state.label}
        </span>
      </div>

      <div className="flex items-end justify-between gap-3">
        <div>
          <span className="text-4xl font-mono font-bold text-white" data-testid="delay-seconds">{delay.toFixed(1)}</span>
          <span className="text-sm text-gray-400 ml-1">s of {(air?.target ?? configured).toFixed(0)} s</span>
        </div>
        {air && air.dumps > 0 && (
          <div className="text-[11px] text-gray-400" data-testid="dump-count">Dumps this show: {air.dumps}</div>
        )}
      </div>
      <div className="h-3 bg-[#0a0a28] rounded-full overflow-hidden mt-2" aria-hidden="true">
        <div
          className={`h-full rounded-full transition-all duration-300 ${air?.safe ? 'bg-green-500' : delay > 0 ? 'bg-yellow-500' : 'bg-red-500'}`}
          style={{ width: `${Math.min(100, (delay / scale) * 100)}%` }}
          data-testid="delay-meter"
        />
      </div>
      <div className="text-[11px] text-gray-400 mt-2" data-testid="delay-help">{state.help}</div>

      {air?.insert && (
        <div className="mt-2 text-sm bg-blue-900 text-blue-200 rounded px-2 py-1" data-testid="insert-now">
          On air now: {air.insert.title} ({clock(air.insert.remaining)} left)
        </div>
      )}
      {!air?.insert && air?.next_insert && air.next_insert.in < 90 && (
        <div className="mt-2 text-sm bg-blue-900 text-blue-200 rounded px-2 py-1" data-testid="insert-next">
          {air.next_insert.title} airs in {clock(air.next_insert.in)}. Keep talking: nothing you say is lost.
        </div>
      )}
      {air?.auto_exit && mode !== 'off' && (
        <div className="mt-2 text-sm bg-orange-900 text-orange-200 rounded px-2 py-1" data-testid="auto-exit">
          The show ends soon: the delay is winding down so your last words air on time.
        </div>
      )}

      <button
        onClick={() => dump(false)}
        disabled={!canDump || busy}
        title="Shift+D"
        data-testid="dump-button"
        className={`mt-3 w-full px-4 py-4 rounded text-xl font-bold transition disabled:opacity-50 ${
          flash ? 'bg-yellow-500 text-black' : 'bg-red-700 hover:bg-red-600 text-white'}`}
      >
        DUMP
        <span className="block text-[11px] font-normal">
          {canDump ? `${dumpWords} (Shift+D)` : onAir ? 'nothing waiting to cut' : 'not on air'}
        </span>
      </button>
      {callerOnAir && (
        <button
          onClick={() => dump(true)}
          disabled={!canDump || busy}
          data-testid="dump-drop-button"
          className="mt-2 w-full px-3 py-2 rounded text-sm font-bold bg-red-800 hover:bg-red-700 text-red-200 transition disabled:opacity-50"
        >
          Dump and drop the caller
        </button>
      )}

      {message && (
        <div className={`mt-2 text-sm ${message.bad ? 'text-red-400' : 'text-green-400'}`} role="status" data-testid="delay-message">
          {message.text}
        </div>
      )}
      {lastDump && !message && (
        <div className="mt-2 text-[11px] text-gray-400" data-testid="last-dump">
          Last dump: {lastDump.seconds.toFixed(1)} s by {lastDump.by} at {new Date(lastDump.at).toLocaleTimeString()}
        </div>
      )}

      {dumps.length > 0 && (
        <div className="mt-3 pt-3 border-t border-[#2a2a5e]" data-testid="dump-log">
          <div className="text-[11px] text-gray-400 uppercase mb-1">Dump log ({dumps.length})</div>
          <ul className="space-y-1 max-h-40 overflow-y-auto">
            {dumps.map(d => (
              <li key={d.id} className="flex items-center gap-2 text-[11px] text-gray-300" data-testid="dump-row">
                <button
                  onClick={() => playDump(d)}
                  disabled={!d.has_audio}
                  data-testid="dump-play"
                  aria-label={playing === d.id ? 'Stop the recording' : 'Play what was cut'}
                  title={`Private: ${d.heard_seconds.toFixed(0)} s that listeners heard, a pause, then the ${d.cut_seconds.toFixed(1)} s that were cut`}
                  className="bg-cyan-800 hover:bg-cyan-700 text-cyan-200 px-2 py-1 rounded min-w-[52px] transition disabled:opacity-50"
                >
                  {playing === d.id ? 'Stop' : 'Play'}
                </button>
                <span className="min-w-0 flex-1 truncate">
                  {d.dumped_at ? new Date(d.dumped_at).toLocaleTimeString() : ''} · {d.cut_seconds.toFixed(1)} s cut · {d.by || 'someone'}
                  {d.caller_name ? ` · dropped ${d.caller_name}` : ''}
                </span>
              </li>
            ))}
          </ul>
          <div className="text-[11px] text-gray-500 mt-1">
            Play is private to this computer. You hear the last seconds that aired, then what was cut.
          </div>
          {logNote && <div className="text-[11px] text-red-400 mt-1" role="status">{logNote}</div>}
        </div>
      )}

      {!compact && (
        <div className="mt-3 pt-3 border-t border-[#2a2a5e] flex flex-wrap items-center gap-2">
          <label className="text-[11px] text-gray-400 flex items-center gap-1">
            Delay length
            <select
              value={chosen}
              onChange={e => setLength(Number(e.target.value))}
              disabled={!onAir || busy}
              data-testid="delay-length"
              className="bg-[#0a0a28] border border-[#2a2a5e] text-white text-sm rounded px-2 py-1"
            >
              {Array.from(new Set([...LENGTHS, configured])).sort((a, b) => a - b).map(n => (
                <option key={n} value={n}>{n === 0 ? 'None (live)' : `${n} s`}</option>
              ))}
            </select>
          </label>
          <button
            onClick={() => (chosen === 0 ? change('exit') : change('build', chosen))}
            disabled={!onAir || busy || (chosen === (air?.target ?? configured))}
            data-testid="delay-apply"
            className="bg-cyan-800 hover:bg-cyan-700 text-cyan-200 px-3 py-1.5 rounded text-[11px] transition disabled:opacity-50"
          >
            Set
          </button>
          {onAir && (air?.target ?? 0) > 0 ? (
            <button
              onClick={() => change('exit')}
              disabled={busy}
              data-testid="delay-exit"
              className="bg-gray-700 hover:bg-gray-600 text-gray-200 px-3 py-1.5 rounded text-[11px] transition disabled:opacity-50"
            >
              Exit delay
            </button>
          ) : (
            <button
              onClick={() => change('build', configured > 0 ? configured : 15)}
              disabled={!onAir || busy}
              data-testid="delay-build"
              className="bg-gray-700 hover:bg-gray-600 text-gray-200 px-3 py-1.5 rounded text-[11px] transition disabled:opacity-50"
            >
              Build delay
            </button>
          )}
        </div>
      )}
    </div>
  );
}
