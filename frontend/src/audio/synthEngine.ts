// ── Web Audio Synthesizer Engine ────────────────────────────────────
//
// Audio graph:
//   [Voice Oscillators] → [Voice Filter] → [Track Gain] → [Analyser] → [Master Gain] → destination
//
// Each track gets a deterministic sound derived from hashing its metadata.

import {
  noteFrequency, buildChord, scaleNoteToMidi,
  MAJOR_PROGRESSIONS, MINOR_PROGRESSIONS,
} from './musicTheory';
import type { AssetInfo } from '../types';

// ── Deterministic hash ──────────────────────────────────────────────

function hashString(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  }
  return h >>> 0; // unsigned 32-bit
}

/** Seeded pseudo-random (xorshift32) */
class SeededRandom {
  private state: number;
  constructor(seed: number) {
    this.state = seed || 1;
  }
  next(): number {
    let x = this.state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    this.state = x >>> 0;
    return this.state / 0xFFFFFFFF;
  }
  /** Integer in [min, max) */
  int(min: number, max: number): number {
    return min + Math.floor(this.next() * (max - min));
  }
}

// ── Track parameters derived from hash ──────────────────────────────

interface TrackParams {
  rootNote: number;     // 0-11
  mode: 'major' | 'minor';
  tempo: number;        // BPM
  progression: number[]; // scale degrees
  assetType: string;
}

function deriveTrackParams(asset: AssetInfo): TrackParams {
  const seed = hashString(
    `${asset.id}|${asset.title}|${asset.artist ?? ''}|${asset.asset_type}|${asset.category ?? ''}`
  );
  const rng = new SeededRandom(seed);

  const rootNote = rng.int(0, 12);
  const mode = rng.next() > 0.5 ? 'major' : 'minor';
  const tempo = 60 + rng.int(0, 81);  // 60-140 BPM
  const progs = mode === 'major' ? MAJOR_PROGRESSIONS : MINOR_PROGRESSIONS;
  const progression = progs[rng.int(0, progs.length)];

  return { rootNote, mode, tempo, progression, assetType: asset.asset_type };
}

// ── Active voice state ──────────────────────────────────────────────

interface ActiveVoice {
  nodes: AudioNode[];
  gainNode: GainNode;
  schedulerTimer: number | null;
}

// ── Synth Engine ────────────────────────────────────────────────────

const CROSSFADE_TIME = 1.5;

export class SynthEngine {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private analyserData: Uint8Array<ArrayBuffer> | null = null;
  private activeVoice: ActiveVoice | null = null;
  private volume = 0.7;
  private muted = false;

  // ── Lifecycle ───────────────────────────────────────────────────

  async init(): Promise<void> {
    if (this.ctx) return;
    this.ctx = new AudioContext();
    if (this.ctx.state === 'suspended') {
      await this.ctx.resume();
    }

    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyserData = new Uint8Array(this.analyser.frequencyBinCount) as Uint8Array<ArrayBuffer>;

    this.masterGain = this.ctx.createGain();
    this.masterGain.gain.value = this.muted ? 0 : this.volume;
    this.analyser.connect(this.masterGain);
    this.masterGain.connect(this.ctx.destination);
  }

  get ready(): boolean {
    return this.ctx !== null && this.ctx.state === 'running';
  }

  destroy(): void {
    this.stopVoice(this.activeVoice);
    this.activeVoice = null;
    if (this.ctx) {
      this.ctx.close().catch(() => {});
      this.ctx = null;
    }
    this.masterGain = null;
    this.analyser = null;
    this.analyserData = null;
  }

  // ── Volume ──────────────────────────────────────────────────────

  setVolume(v: number): void {
    this.volume = Math.max(0, Math.min(1, v));
    if (this.masterGain && !this.muted) {
      this.masterGain.gain.setTargetAtTime(this.volume, this.ctx!.currentTime, 0.02);
    }
  }

  setMuted(m: boolean): void {
    this.muted = m;
    if (this.masterGain) {
      this.masterGain.gain.setTargetAtTime(
        m ? 0 : this.volume,
        this.ctx!.currentTime, 0.02
      );
    }
  }

  // ── Analyser ────────────────────────────────────────────────────

  /** Returns [left, right] VU levels, 0-1. Simulates stereo from mono analyser. */
  getAnalyserLevels(): [number, number] {
    if (!this.analyser || !this.analyserData) return [0, 0];
    this.analyser.getByteFrequencyData(this.analyserData);

    const len = this.analyserData.length;
    const half = len >> 1;
    let sumL = 0, sumR = 0;
    for (let i = 0; i < half; i++) sumL += this.analyserData[i];
    for (let i = half; i < len; i++) sumR += this.analyserData[i];

    const l = sumL / (half * 255);
    const r = sumR / (half * 255);
    return [Math.min(1, l * 2.5), Math.min(1, r * 2.5)];
  }

  // ── Play / Stop ─────────────────────────────────────────────────

  playTrack(asset: AssetInfo, elapsedSeconds: number = 0): void {
    if (!this.ctx || !this.analyser) return;

    const params = deriveTrackParams(asset);
    const oldVoice = this.activeVoice;

    // Create new voice
    const trackGain = this.ctx.createGain();
    trackGain.gain.value = 0; // start silent for crossfade
    trackGain.connect(this.analyser);

    const voice = this.createVoice(params, trackGain, elapsedSeconds);
    this.activeVoice = voice;

    // Crossfade
    const now = this.ctx.currentTime;
    trackGain.gain.setValueAtTime(0, now);
    trackGain.gain.linearRampToValueAtTime(0.35, now + CROSSFADE_TIME);

    if (oldVoice) {
      oldVoice.gainNode.gain.setValueAtTime(oldVoice.gainNode.gain.value, now);
      oldVoice.gainNode.gain.linearRampToValueAtTime(0, now + CROSSFADE_TIME);
      setTimeout(() => this.stopVoice(oldVoice), CROSSFADE_TIME * 1000 + 200);
    }
  }

  stop(): void {
    if (this.activeVoice && this.ctx) {
      const now = this.ctx.currentTime;
      this.activeVoice.gainNode.gain.setValueAtTime(this.activeVoice.gainNode.gain.value, now);
      this.activeVoice.gainNode.gain.linearRampToValueAtTime(0, now + 0.3);
      const voice = this.activeVoice;
      this.activeVoice = null;
      setTimeout(() => this.stopVoice(voice), 500);
    }
  }

  // ── Voice creation by asset type ────────────────────────────────

  private createVoice(params: TrackParams, trackGain: GainNode, elapsed: number): ActiveVoice {
    const nodes: AudioNode[] = [];
    let schedulerTimer: number | null = null;

    switch (params.assetType) {
      case 'music':
        this.createMusicVoice(params, trackGain, nodes, elapsed);
        schedulerTimer = this.startChordSequencer(params, trackGain, nodes, elapsed);
        break;
      case 'jingle':
        this.createJingleVoice(params, trackGain, nodes, elapsed);
        schedulerTimer = this.startChordSequencer(params, trackGain, nodes, elapsed);
        break;
      case 'spot':
        this.createSpotVoice(params, trackGain, nodes, elapsed);
        break;
      case 'shiur':
        this.createShiurVoice(params, trackGain, nodes, elapsed);
        break;
      case 'zmanim':
        this.createZmanimVoice(params, trackGain, nodes, elapsed);
        schedulerTimer = this.startChimeSequencer(params, trackGain, nodes, elapsed);
        break;
      default:
        this.createMusicVoice(params, trackGain, nodes, elapsed);
        schedulerTimer = this.startChordSequencer(params, trackGain, nodes, elapsed);
        break;
    }

    return { nodes, gainNode: trackGain, schedulerTimer };
  }

  /** Music: Warm analog pad — 2 detuned sawtooth + sub-sine through LP filter */
  private createMusicVoice(
    params: TrackParams, dest: AudioNode, nodes: AudioNode[], elapsed: number
  ): void {
    const ctx = this.ctx!;
    const chord = this.getChordAtTime(params, elapsed);

    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = 800;
    filter.Q.value = 1;
    filter.connect(dest);
    nodes.push(filter);

    // Slow LFO on filter
    const lfo = ctx.createOscillator();
    const lfoGain = ctx.createGain();
    lfo.type = 'sine';
    lfo.frequency.value = 0.3;
    lfoGain.gain.value = 200;
    lfo.connect(lfoGain);
    lfoGain.connect(filter.frequency);
    lfo.start();
    nodes.push(lfo, lfoGain);

    for (const midi of chord) {
      const freq = noteFrequency(midi);

      // Two detuned sawtooth oscillators
      const osc1 = ctx.createOscillator();
      osc1.type = 'sawtooth';
      osc1.frequency.value = freq * 1.003;
      const g1 = ctx.createGain();
      g1.gain.value = 0.12;
      osc1.connect(g1);
      g1.connect(filter);
      osc1.start();
      nodes.push(osc1, g1);

      const osc2 = ctx.createOscillator();
      osc2.type = 'sawtooth';
      osc2.frequency.value = freq * 0.997;
      const g2 = ctx.createGain();
      g2.gain.value = 0.12;
      osc2.connect(g2);
      g2.connect(filter);
      osc2.start();
      nodes.push(osc2, g2);

      // Sub sine
      const sub = ctx.createOscillator();
      sub.type = 'sine';
      sub.frequency.value = freq / 2;
      const gs = ctx.createGain();
      gs.gain.value = 0.08;
      sub.connect(gs);
      gs.connect(filter);
      sub.start();
      nodes.push(sub, gs);
    }
  }

  /** Jingle: Bright bell — FM synthesis */
  private createJingleVoice(
    params: TrackParams, dest: AudioNode, nodes: AudioNode[], elapsed: number
  ): void {
    const ctx = this.ctx!;
    const chord = this.getChordAtTime(params, elapsed);

    for (const midi of chord) {
      const carrierFreq = noteFrequency(midi);
      const modFreq = carrierFreq * 3.5;

      // Modulator
      const mod = ctx.createOscillator();
      mod.type = 'sine';
      mod.frequency.value = modFreq;
      const modGain = ctx.createGain();
      modGain.gain.value = carrierFreq * 2;
      mod.connect(modGain);

      // Carrier
      const carrier = ctx.createOscillator();
      carrier.type = 'sine';
      carrier.frequency.value = carrierFreq;
      modGain.connect(carrier.frequency);

      // Envelope
      const env = ctx.createGain();
      env.gain.value = 0.15;
      carrier.connect(env);
      env.connect(dest);

      mod.start();
      carrier.start();
      nodes.push(mod, modGain, carrier, env);
    }
  }

  /** Spot: Voice-like texture — white noise through narrow bandpass */
  private createSpotVoice(
    params: TrackParams, dest: AudioNode, nodes: AudioNode[], _elapsed: number
  ): void {
    const ctx = this.ctx!;
    const rootFreq = noteFrequency(scaleNoteToMidi(params.rootNote, params.mode, 0, 3));

    // White noise buffer
    const bufferSize = ctx.sampleRate * 2;
    const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) data[i] = Math.random() * 2 - 1;

    const noise = ctx.createBufferSource();
    noise.buffer = buffer;
    noise.loop = true;

    // Narrow bandpass
    const bp = ctx.createBiquadFilter();
    bp.type = 'bandpass';
    bp.frequency.value = rootFreq * 2;
    bp.Q.value = 8;

    // LFO on center frequency
    const lfo = ctx.createOscillator();
    lfo.type = 'sine';
    lfo.frequency.value = 0.5;
    const lfoGain = ctx.createGain();
    lfoGain.gain.value = rootFreq * 0.5;
    lfo.connect(lfoGain);
    lfoGain.connect(bp.frequency);

    const outGain = ctx.createGain();
    outGain.gain.value = 0.25;

    noise.connect(bp);
    bp.connect(outGain);
    outGain.connect(dest);
    lfo.start();
    noise.start();

    nodes.push(noise, bp, lfo, lfoGain, outGain);
  }

  /** Shiur: Gentle drone — sine root + quiet fifth, slow tremolo */
  private createShiurVoice(
    params: TrackParams, dest: AudioNode, nodes: AudioNode[], _elapsed: number
  ): void {
    const ctx = this.ctx!;
    const rootFreq = noteFrequency(scaleNoteToMidi(params.rootNote, params.mode, 0, 3));
    const fifthFreq = rootFreq * 1.5;

    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = 400;
    filter.connect(dest);
    nodes.push(filter);

    // Tremolo LFO
    const tremolo = ctx.createOscillator();
    tremolo.type = 'sine';
    tremolo.frequency.value = 0.15;
    const tremoloGain = ctx.createGain();
    tremoloGain.gain.value = 0.06;

    const vca = ctx.createGain();
    vca.gain.value = 0.2;
    tremolo.connect(tremoloGain);
    tremoloGain.connect(vca.gain);
    vca.connect(filter);
    tremolo.start();
    nodes.push(tremolo, tremoloGain, vca);

    // Root
    const osc1 = ctx.createOscillator();
    osc1.type = 'sine';
    osc1.frequency.value = rootFreq;
    osc1.connect(vca);
    osc1.start();
    nodes.push(osc1);

    // Quiet fifth
    const osc2 = ctx.createOscillator();
    osc2.type = 'sine';
    osc2.frequency.value = fifthFreq;
    const g5 = ctx.createGain();
    g5.gain.value = 0.5;
    osc2.connect(g5);
    g5.connect(vca);
    osc2.start();
    nodes.push(osc2, g5);
  }

  /** Zmanim: Chime pattern — triad sines with exponential decay */
  private createZmanimVoice(
    params: TrackParams, dest: AudioNode, nodes: AudioNode[], _elapsed: number
  ): void {
    // Initial chime triggered immediately; sequencer handles repeats
    this.triggerChime(params, dest, nodes);
  }

  private triggerChime(params: TrackParams, dest: AudioNode, nodes: AudioNode[]): void {
    const ctx = this.ctx!;
    const chord = buildChord(params.rootNote, params.mode, params.progression[0], 5);
    const now = ctx.currentTime;

    chord.slice(0, 3).forEach((midi, i) => {
      const osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.value = noteFrequency(midi);

      const env = ctx.createGain();
      const onset = now + i * 0.15;
      env.gain.setValueAtTime(0, onset);
      env.gain.linearRampToValueAtTime(0.2, onset + 0.01);
      env.gain.exponentialRampToValueAtTime(0.001, onset + 2.0);

      osc.connect(env);
      env.connect(dest);
      osc.start(onset);
      osc.stop(onset + 2.5);
      nodes.push(osc, env);
    });
  }

  // ── Chord sequencer (uses AudioContext.currentTime) ─────────────

  private getChordAtTime(params: TrackParams, elapsedSec: number): number[] {
    const beatsPerChord = 4;
    const secPerBeat = 60 / params.tempo;
    const secPerChord = beatsPerChord * secPerBeat;
    const chordIndex = Math.floor(elapsedSec / secPerChord) % params.progression.length;
    const degree = params.progression[chordIndex];
    return buildChord(params.rootNote, params.mode, degree, 3);
  }

  /** Schedule chord changes for pad-type voices.
   *  Returns a setInterval timer ID. */
  private startChordSequencer(
    params: TrackParams, trackGain: GainNode, nodes: AudioNode[], elapsed: number
  ): number {
    const beatsPerChord = 4;
    const secPerBeat = 60 / params.tempo;
    const secPerChord = beatsPerChord * secPerBeat;

    // Time until next chord change
    const intoChord = elapsed % secPerChord;
    const untilNext = secPerChord - intoChord;

    let chordCounter = Math.floor(elapsed / secPerChord);

    const changeChord = () => {
      chordCounter++;
      const degree = params.progression[chordCounter % params.progression.length];
      const chord = buildChord(params.rootNote, params.mode, degree, 3);

      // Simple approach: adjust frequency of first N oscillators
      const tunable = nodes.filter(
        (n): n is OscillatorNode =>
          n instanceof OscillatorNode &&
          (n.type === 'sawtooth' || (n.type === 'sine' && n.frequency.value > 80))
      );

      // Group oscillators per note (for music: 3 per note — 2 sawtooth + 1 sub)
      let oscIdx = 0;
      for (const midi of chord) {
        const freq = noteFrequency(midi);
        const ctx = this.ctx;
        if (!ctx) return;
        const t = ctx.currentTime;

        // Retune available oscillators
        for (let j = 0; j < 3 && oscIdx < tunable.length; j++, oscIdx++) {
          const osc = tunable[oscIdx];
          let target = freq;
          if (osc.type === 'sawtooth') {
            target = freq * (j === 0 ? 1.003 : 0.997);
          } else {
            target = freq / 2; // sub
          }
          osc.frequency.setTargetAtTime(target, t, 0.1);
        }
      }
    };

    // First change after remaining time, then on interval
    const firstTimer = window.setTimeout(() => {
      changeChord();
      // Ongoing changes
      const ongoing = window.setInterval(changeChord, secPerChord * 1000);
      // Stash ongoing timer so we can clear it
      (trackGain as any).__ongoingTimer = ongoing;
    }, untilNext * 1000);

    return firstTimer;
  }

  /** Chime sequencer for zmanim — retrigger chimes periodically */
  private startChimeSequencer(
    params: TrackParams, trackGain: GainNode, nodes: AudioNode[], elapsed: number
  ): number {
    const secPerChime = (60 / params.tempo) * 8; // every 8 beats
    const untilNext = secPerChime - (elapsed % secPerChime);

    const trigger = () => this.triggerChime(params, trackGain, nodes);

    const firstTimer = window.setTimeout(() => {
      trigger();
      const ongoing = window.setInterval(trigger, secPerChime * 1000);
      (trackGain as any).__ongoingTimer = ongoing;
    }, untilNext * 1000);

    return firstTimer;
  }

  // ── Cleanup ─────────────────────────────────────────────────────

  private stopVoice(voice: ActiveVoice | null): void {
    if (!voice) return;

    if (voice.schedulerTimer !== null) {
      window.clearTimeout(voice.schedulerTimer);
    }
    if ((voice.gainNode as any).__ongoingTimer) {
      window.clearInterval((voice.gainNode as any).__ongoingTimer);
    }

    for (const node of voice.nodes) {
      try {
        if (node instanceof OscillatorNode) node.stop();
        else if (node instanceof AudioBufferSourceNode) node.stop();
        node.disconnect();
      } catch {
        // already stopped/disconnected
      }
    }

    try {
      voice.gainNode.disconnect();
    } catch {
      // already disconnected
    }
  }
}
