/**
 * "Very quiet recording": measured so far under the station's level (-18 LUFS) that the
 * largest boost the players give (+12 dB, backend `replay_gain_for`) cannot bring it up,
 * so it airs quieter than everything else (old tape transfers at -31 to -45 LUFS). The
 * file is not changed and the boost is not widened (it would lift the tape hiss and the
 * in-browser player has no limiter); the mark lets a person decide what to do with it.
 */
export const VERY_QUIET_LUFS = -30;

export function veryQuietBy(metadataExtra: Record<string, unknown> | null | undefined): number | null {
  const analysis = (metadataExtra as { audio_analysis?: { loudness_lufs?: unknown } } | null | undefined)?.audio_analysis;
  const lufs = analysis?.loudness_lufs;
  if (typeof lufs !== 'number' || !Number.isFinite(lufs) || lufs >= VERY_QUIET_LUFS) return null;
  return Math.round((-18 - 12 - lufs) * 10) / 10; // how far under the rest it still plays, in LU
}
