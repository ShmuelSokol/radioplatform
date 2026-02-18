// ── Music Theory Constants & Utilities ──────────────────────────────

/** A4 = 440 Hz, equal temperament */
const A4 = 440;
const A4_MIDI = 69;

/** Convert MIDI note number to frequency in Hz */
export function noteFrequency(midi: number): number {
  return A4 * Math.pow(2, (midi - A4_MIDI) / 12);
}

/** Note names for display / hashing */
export const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'] as const;

/** Scale intervals (semitones from root) */
export const SCALES = {
  major:     [0, 2, 4, 5, 7, 9, 11],
  minor:     [0, 2, 3, 5, 7, 8, 10],
} as const;

/** Chord quality: intervals from root in semitones */
export const CHORD_TYPES = {
  major:  [0, 4, 7],
  minor:  [0, 3, 7],
  dim:    [0, 3, 6],
  sus4:   [0, 5, 7],
  maj7:   [0, 4, 7, 11],
  min7:   [0, 3, 7, 10],
} as const;

type ChordQuality = keyof typeof CHORD_TYPES;

/** Scale degree → chord quality mapping */
const MAJOR_DEGREE_QUALITY: ChordQuality[] = ['major', 'minor', 'minor', 'major', 'major', 'minor', 'dim'];
const MINOR_DEGREE_QUALITY: ChordQuality[] = ['minor', 'dim', 'major', 'minor', 'minor', 'major', 'major'];

/** Chord progressions as 0-indexed scale degrees */
export const MAJOR_PROGRESSIONS = [
  [0, 4, 5, 3],   // I-V-vi-IV
  [0, 3, 4, 3],   // I-IV-V-IV
  [1, 4, 0, 0],   // ii-V-I-I
  [0, 5, 3, 4],   // I-vi-IV-V
  [0, 3, 1, 4],   // I-IV-ii-V
  [0, 3, 0, 4],   // I-IV-I-V
];

export const MINOR_PROGRESSIONS = [
  [0, 3, 4, 4],   // i-iv-v-v
  [0, 5, 2, 4],   // i-VI-III-v
  [0, 6, 5, 4],   // i-VII-VI-v
  [0, 3, 6, 4],   // i-iv-VII-v
  [0, 2, 5, 4],   // i-III-VI-v
  [0, 4, 3, 6],   // i-v-iv-VII
];

/** Convert a scale degree to a MIDI note in a given key and octave */
export function scaleNoteToMidi(
  rootNote: number,   // 0-11 (C=0)
  mode: 'major' | 'minor',
  degree: number,     // 0-indexed scale degree
  octave: number = 4,
): number {
  const scale = SCALES[mode];
  const octaveOffset = Math.floor(degree / scale.length);
  const degInScale = ((degree % scale.length) + scale.length) % scale.length;
  return rootNote + (octave * 12) + 12 + scale[degInScale] + (octaveOffset * 12);
}

/** Build a chord as MIDI note array */
export function buildChord(
  rootNote: number,   // 0-11
  mode: 'major' | 'minor',
  degree: number,     // 0-indexed scale degree
  octave: number = 3,
): number[] {
  const scale = SCALES[mode];
  const degInScale = ((degree % scale.length) + scale.length) % scale.length;
  const chordRoot = rootNote + (octave * 12) + 12 + scale[degInScale];
  const qualities = mode === 'major' ? MAJOR_DEGREE_QUALITY : MINOR_DEGREE_QUALITY;
  const quality = qualities[degInScale];
  const intervals = CHORD_TYPES[quality];
  return intervals.map(i => chordRoot + i);
}
