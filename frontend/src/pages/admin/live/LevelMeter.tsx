import type { MicMeter } from '../../../hooks/useHostAudio';

/** Where a level sits on the bar: -60 dB at the left end, 0 dB (full scale) at the right */
const at = (db: number) => Math.max(0, Math.min(100, ((db + 60) / 60) * 100));

/**
 * The host's own level, big enough to read while talking: a bar with a green zone
 * for a good speaking level, yellow near the top and red at full scale, the loudest
 * recent moment as a line, and one plain sentence under it saying what to do.
 */
export default function LevelMeter({ meter, on, muted }: { meter: MicMeter; on: boolean; muted: boolean }) {
  const level = on && !muted ? meter.db : -60;
  const peak = on && !muted ? meter.peakDb : -60;
  let word = 'Microphone is off';
  let tone = 'text-gray-400';
  if (on && muted) { word = 'Microphone muted'; tone = 'text-yellow-400'; }
  else if (on && meter.silentFor >= 3) {
    word = 'No sound from your microphone. Is it plugged in, switched on and the right one chosen in the browser?';
    tone = 'text-red-400';
  } else if (on && meter.clipping) { word = 'TOO LOUD: your voice is distorting. Move back or turn the microphone down.'; tone = 'text-red-400'; }
  else if (on && peak > -6) { word = 'Loud. A little further from the microphone is safer.'; tone = 'text-yellow-400'; }
  else if (on && peak < -30) { word = 'Quiet. Come closer or turn the microphone up.'; tone = 'text-yellow-400'; }
  else if (on) { word = 'Good level'; tone = 'text-green-400'; }
  return (
    <div data-testid="level-meter" data-level={level.toFixed(0)}>
      <div className="relative h-7 rounded bg-[#0a0a28] overflow-hidden border border-[#2a2a5e]"
        role="meter" aria-label="Microphone level" aria-valuemin={-60} aria-valuemax={0} aria-valuenow={Math.round(level)}>
        {/* the zones, dim, so the bar reads against them */}
        <div className="absolute inset-y-0 bg-green-900/40" style={{ left: `${at(-30)}%`, width: `${at(-9) - at(-30)}%` }} />
        <div className="absolute inset-y-0 bg-yellow-900/40" style={{ left: `${at(-9)}%`, width: `${at(-3) - at(-9)}%` }} />
        <div className="absolute inset-y-0 bg-red-900/50" style={{ left: `${at(-3)}%`, right: 0 }} />
        <div
          className={`absolute inset-y-0 left-0 transition-[width] duration-75 ${
            level > -3 ? 'bg-red-500' : level > -9 ? 'bg-yellow-400' : 'bg-green-500'}`}
          style={{ width: `${at(level)}%` }}
        />
        <div className={`absolute inset-y-0 w-1 ${meter.clipping && on && !muted ? 'bg-red-400' : 'bg-white'}`}
          style={{ left: `calc(${at(peak)}% - 2px)`, opacity: on && !muted ? 1 : 0 }} />
      </div>
      <div className="flex justify-between text-[10px] text-gray-500 mt-0.5 select-none" aria-hidden>
        <span>quiet</span><span>good</span><span>loud</span>
      </div>
      <div className={`text-sm font-bold mt-1 ${tone}`} role="status" data-testid="level-word">{word}</div>
    </div>
  );
}
