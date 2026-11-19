import { useEffect, useState } from 'react';
import { METER_FLOOR_DB, METER_SEGMENTS } from './useAudioEngine';

/**
 * The station's real on-air level for the control room meters, measured by the playout
 * engine after its limiter and pushed over the now-playing socket once a second
 * ({"type":"levels"}: ten RMS values per side, 0..1). Played back value by value, so
 * the meters move like a hardware meter, about a second behind the air.
 *
 * The page needs no sound and no tap for this (Shmuel, 2026-11-19: "It should always be
 * showing the levels, even if you didn't hit listen, because that's part of running a
 * station"). Dead air shows as flat meters. `fresh` is false when nothing has come for
 * a few seconds: a browser-player station (no engine), or an engine that does not send
 * levels yet; the page then falls back to its own player's meters.
 *
 * Returns levels in meter steps (0..1 in 1/METER_SEGMENTS) like useAudioEngine.vuLevels.
 */

// Nothing for this long: not fresh any more (ms)
export const LEVELS_STALE_MS = 3500;
// Values held back at most (a late burst is not played back slowly)
const MAX_QUEUE = 25;
const FALL_DB_PER_STEP = 2.0; // ~20 dB/s at 10 steps a second, like the local meters

interface LevelsMessage { stationId?: string; l?: number[]; r?: number[]; step?: number }

const toDb = (rms: number) => (rms > 0 ? Math.max(-100, 20 * Math.log10(rms)) : -100);
const toStep = (db: number) =>
  Math.round(Math.min(1, Math.max(0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB)) * METER_SEGMENTS) / METER_SEGMENTS;

export function useOnAirLevels(stationId: string | null | undefined): { levels: [number, number]; fresh: boolean } {
  const [levels, setLevels] = useState<[number, number]>([0, 0]);
  const [fresh, setFresh] = useState(false);

  useEffect(() => {
    setLevels([0, 0]);
    setFresh(false);
    if (!stationId) return;
    const queue: [number, number][] = [];
    let step = 100;
    let lastAt = 0;
    let shown: [number, number] = [-100, -100];
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = () => {
      timer = null;
      const next = queue.shift();
      if (next) {
        // Quick rise, steady fall
        shown = next.map((db, i) => (db >= shown[i] ? db : Math.max(db, shown[i] - FALL_DB_PER_STEP))) as [number, number];
        const out: [number, number] = [toStep(shown[0]), toStep(shown[1])];
        setLevels((v) => (v[0] === out[0] && v[1] === out[1] ? v : out));
      }
      if (queue.length) timer = setTimeout(tick, step);
    };
    const onLevels = (e: Event) => {
      const d = (e as CustomEvent<LevelsMessage>).detail;
      if (!d || d.stationId !== stationId || !Array.isArray(d.l) || !Array.isArray(d.r)) return;
      step = Math.max(20, Math.min(1000, (d.step ?? 0.1) * 1000));
      const n = Math.min(d.l.length, d.r.length);
      for (let i = 0; i < n; i++) queue.push([toDb(Number(d.l[i]) || 0), toDb(Number(d.r[i]) || 0)]);
      if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);
      lastAt = Date.now();
      setFresh(true);
      if (!timer) tick();
    };
    const watch = setInterval(() => {
      if (lastAt && Date.now() - lastAt > LEVELS_STALE_MS) {
        lastAt = 0;
        queue.length = 0;
        shown = [-100, -100];
        setLevels([0, 0]);
        setFresh(false);
      }
    }, 1000);
    window.addEventListener('kbr-levels', onLevels);
    return () => {
      window.removeEventListener('kbr-levels', onLevels);
      clearInterval(watch);
      if (timer) clearTimeout(timer);
    };
  }, [stationId]);

  return { levels, fresh };
}
