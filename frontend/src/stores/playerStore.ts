import { create } from 'zustand';

/**
 * The station a listener is listening to right now. The Listen page owns the sound
 * (components/audio/ListenHost keeps it alive while the listener looks at other
 * listener pages); this store only says what is playing so the bar at the bottom
 * (components/audio/AudioPlayer) can show it and stop it.
 */
interface PlayerState {
  stationId: string | null;
  stationName: string | null;
  isPlaying: boolean;
  currentTrack: { title: string; artist?: string } | null;
  /** Set by the Listen page: what Stop does there. */
  stopFn: (() => void) | null;
  /** Set by the Listen page: starts its sound (called inside a tap on the bar). */
  playFn: (() => void) | null;
  /** Sound really comes out (an iPhone can refuse sound a tap did not start). */
  sounding: boolean;
  /** The control room was giving sound and was left: the Listen page takes over this
   *  station (ListenHost keeps one, hidden) and plays on, or the bar offers one tap. */
  handoff: string | null;
  listening: (stationId: string, stationName: string, stop: () => void, play?: () => void) => void;
  setSounding: (sounding: boolean) => void;
  handOver: (stationId: string, stationName: string, stop: () => void) => void;
  takeHandoff: (stationId: string) => boolean;
  setTrack: (track: { title: string; artist?: string } | null) => void;
  /** The listener pressed Stop (in the bar or anywhere else). */
  stop: () => void;
  /** The Listen page stopped or went away. */
  clear: (stationId?: string) => void;
}

export const usePlayerStore = create<PlayerState>((set, get) => ({
  stationId: null,
  stationName: null,
  isPlaying: false,
  currentTrack: null,
  stopFn: null,
  playFn: null,
  sounding: false,
  handoff: null,
  listening: (stationId, stationName, stop, play) => set({ stationId, stationName, isPlaying: true, stopFn: stop, playFn: play ?? null }),
  setSounding: (sounding) => { if (get().sounding !== sounding) set({ sounding }); },
  handOver: (stationId, stationName, stop) => set({
    stationId, stationName, isPlaying: true, stopFn: stop, playFn: null, sounding: false, handoff: stationId,
  }),
  takeHandoff: (stationId) => {
    if (get().handoff !== stationId) return false;
    set({ handoff: null });
    return true;
  },
  setTrack: (track) => set({ currentTrack: track }),
  stop: () => {
    const fn = get().stopFn;
    set({ stationId: null, stationName: null, isPlaying: false, currentTrack: null, stopFn: null, playFn: null, sounding: false, handoff: null });
    try { fn?.(); } catch { /* the page is already gone */ }
  },
  clear: (stationId) => {
    if (stationId && get().stationId !== stationId) return;   // another station took over
    if (get().handoff) return;   // handed to the next page, which has not taken it yet
    set({ stationId: null, stationName: null, isPlaying: false, currentTrack: null, stopFn: null, playFn: null, sounding: false, handoff: null });
  },
}));
