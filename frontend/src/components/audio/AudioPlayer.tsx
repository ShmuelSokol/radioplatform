import { usePlayerStore } from '../../stores/playerStore';
import { useAudioPlayer } from '../../hooks/useAudioPlayer';
import NowPlaying from './NowPlaying';

export default function AudioPlayer() {
  const { stationName, volume, stop, setVolume } = usePlayerStore();
  const { audioRef } = useAudioPlayer();
  const { stationId } = usePlayerStore();

  if (!stationId) return null;

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-gray-900 text-white border-t border-gray-700 p-3 z-50">
      <audio ref={audioRef} />
      <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
        <div className="flex items-center gap-4 flex-1 min-w-0">
          <NowPlaying stationId={stationId} />
          <span className="text-sm text-gray-400 truncate">{stationName}</span>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            className="w-24 accent-brand-500"
          />
          <button
            onClick={stop}
            className="bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded text-sm transition"
          >
            Stop
          </button>
        </div>
      </div>
    </div>
  );
}
