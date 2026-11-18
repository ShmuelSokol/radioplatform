import type { AirStatus } from '../../../api/liveShows';

/** What the control room's live-show badge says: LIVE only once listeners hear the
 *  host, and how far behind they hear them. Unknown status (not asked yet) = LIVE. */
export function liveShowBadge(air: AirStatus | null): { label: string; short: string; heard: boolean; hint: string } {
  if (!air) return { label: 'LIVE', short: 'LIVE', heard: true, hint: 'A live show is on. Open the host console.' };
  if (air.on_air) {
    const d = Math.round(air.delay);
    return {
      label: d >= 1 ? `LIVE, ${d} s delay` : 'LIVE, no delay', short: 'LIVE', heard: true,
      hint: d >= 1 ? `Listeners hear the host ${d} s later. Open the host console.` : 'Listeners hear the host at once. Open the host console.',
    };
  }
  if (air.mode === 'pre-roll') {
    const left = Math.max(0, Math.ceil(Math.max(air.target - air.delay, air.hold ?? 0)));
    return { label: `FILLING DELAY, ${left} s`, short: 'FILLING', heard: false, hint: 'The host is talking; listeners still hear the station. Open the host console.' };
  }
  return { label: 'SHOW OPEN, NO HOST', short: 'NO HOST', heard: false, hint: 'The show is started but no microphone is on air: the station plays on. Open the host console.' };
}
