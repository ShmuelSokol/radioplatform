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
  listening: (stationId: string, stationName: string, stop: () => void) => void;
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
  listening: (stationId, stationName, stop) => set({ stationId, stationName, isPlaying: true, stopFn: stop }),
  setTrack: (track) => set({ currentTrack: track }),
  stop: () => {
    const fn = get().stopFn;
    set({ stationId: null, stationName: null, isPlaying: false, currentTrack: null, stopFn: null });
    try { fn?.(); } catch { /* the page is already gone */ }
  },
  clear: (stationId) => {
    if (stationId && get().stationId !== stationId) return;   // another station took over
    set({ stationId: null, stationName: null, isPlaying: false, currentTrack: null, stopFn: null });
  },
}));
