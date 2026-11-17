/**
 * The server's clock, as this browser sees it. Start times, due times and end
 * times all come from the server; comparing them with this device's own clock put
 * a PC whose clock was 20 s fast 20 s into every song and faded the song for the
 * hourly time check 20 s early. The offset is measured from the server time sent
 * with the release policy (lib/appUpdate.ts, on connect and every minute).
 */
let offsetMs = 0;

/** Note the server's time (ISO) as received now. */
export function noteServerTime(iso: string | null | undefined): void {
  if (!iso) return;
  const t = new Date(iso).getTime();
  if (isFinite(t)) offsetMs = t - Date.now();
}

/** Milliseconds: server clock minus this device's clock. */
export function serverOffsetMs(): number {
  return offsetMs;
}

/** Now, on the server's clock (ms since the epoch). */
export function serverNow(): number {
  return Date.now() + offsetMs;
}
