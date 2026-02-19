import { useState, useEffect } from 'react';
import { usePreferences, useUpdatePreferences } from '../../hooks/usePreferences';
import type { WaveformPlayerHandle } from './WaveformPlayer';

interface PreviewControlsProps {
  waveformRef: React.RefObject<WaveformPlayerHandle | null>;
  duration: number | null;
}

export default function PreviewControls({ waveformRef, duration }: PreviewControlsProps) {
  const { data: prefs } = usePreferences();
  const updatePrefs = useUpdatePreferences();

  const [startSec, setStartSec] = useState(5);
  const [endSec, setEndSec] = useState(5);

  useEffect(() => {
    if (prefs) {
      setStartSec(prefs.preview_start_seconds);
      setEndSec(prefs.preview_end_seconds);
    }
  }, [prefs]);

  const playFirst = () => {
    const ws = waveformRef.current;
    if (!ws) return;
    ws.playRegion(0, Math.min(startSec, ws.getDuration()));
  };

  const playLast = () => {
    const ws = waveformRef.current;
    if (!ws || !duration) return;
    ws.playRegion(Math.max(0, duration - endSec), duration);
  };

  const saveDefaults = () => {
    updatePrefs.mutate({
      preview_start_seconds: startSec,
      preview_end_seconds: endSec,
    });
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3">Preview</h3>

      <div className="grid grid-cols-2 gap-3 mb-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">First N seconds</label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              max={60}
              value={startSec}
              onChange={(e) => setStartSec(Number(e.target.value))}
              className="w-16 border border-gray-300 rounded px-2 py-1 text-sm"
            />
            <button
              onClick={playFirst}
              className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded text-sm transition"
            >
              Play First {startSec}s
            </button>
          </div>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Last N seconds</label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              max={60}
              value={endSec}
              onChange={(e) => setEndSec(Number(e.target.value))}
              className="w-16 border border-gray-300 rounded px-2 py-1 text-sm"
            />
            <button
              onClick={playLast}
              className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded text-sm transition"
            >
              Play Last {endSec}s
            </button>
          </div>
        </div>
      </div>

      <button
        onClick={saveDefaults}
        disabled={updatePrefs.isPending}
        className="text-xs text-brand-600 hover:text-brand-800 disabled:opacity-50"
      >
        {updatePrefs.isPending ? 'Saving...' : 'Save as Default'}
      </button>
    </div>
  );
}
