import { create } from 'zustand';

interface PlayerState {
  stationId: string | null;
  stationName: string | null;
  hlsUrl: string | null;
  isPlaying: boolean;
  currentTrack: { title: string; artist?: string; artUrl?: string } | null;
  volume: number;
  play: (stationId: string, stationName: string, hlsUrl: string) => void;
  stop: () => void;
  setTrack: (track: { title: string; artist?: string; artUrl?: string } | null) => void;
  setVolume: (volume: number) => void;
  setIsPlaying: (playing: boolean) => void;
}

export const usePlayerStore = create<PlayerState>((set) => ({
  stationId: null,
  stationName: null,
  hlsUrl: null,
  isPlaying: false,
  currentTrack: null,
  volume: 0.8,
  play: (stationId, stationName, hlsUrl) =>
    set({ stationId, stationName, hlsUrl, isPlaying: true }),
  stop: () =>
    set({ stationId: null, stationName: null, hlsUrl: null, isPlaying: false, currentTrack: null }),
  setTrack: (track) => set({ currentTrack: track }),
  setVolume: (volume) => set({ volume }),
  setIsPlaying: (playing) => set({ isPlaying: playing }),
}));
