import { useState } from 'react';
import { useDetectSilence, useTrimAsset } from '../../hooks/useAssets';
import type { WaveformPlayerHandle } from './WaveformPlayer';
import type { SilenceRegion } from '../../types';

interface SilenceDetectionPanelProps {
  assetId: string;
  waveformRef: React.RefObject<WaveformPlayerHandle | null>;
}

export default function SilenceDetectionPanel({ assetId, waveformRef }: SilenceDetectionPanelProps) {
  const [thresholdDb, setThresholdDb] = useState(-30);
  const [minDuration, setMinDuration] = useState(0.5);
  const [regions, setRegions] = useState<SilenceRegion[]>([]);
  const [showConfirm, setShowConfirm] = useState(false);

  const detectMutation = useDetectSilence();
  const trimMutation = useTrimAsset();

  const handleDetect = () => {
    detectMutation.mutate(
      { id: assetId, thresholdDb, minDuration },
      {
        onSuccess: (data) => {
          setRegions(data.silence_regions);
          // Push regions to waveform
          const ws = waveformRef.current?.getWaveSurfer();
          if (ws) {
            // WaveformPlayer handles regions via props — we update parent state
          }
        },
      }
    );
  };

  const handleAutoTrim = () => {
    if (regions.length === 0) return;
    const duration = waveformRef.current?.getDuration() ?? 0;
    if (duration <= 0) return;

    // Find leading and trailing silence
    const leadingSilence = regions.find((r) => r.start < 0.1);
    const trailingSilence = regions.find((r) => Math.abs(r.end - duration) < 0.1);

    const trimStart = leadingSilence ? leadingSilence.end : 0;
    const trimEnd = trailingSilence ? trailingSilence.start : duration;

    trimMutation.mutate(
      { id: assetId, trimStart: trimStart, trimEnd: trimEnd },
      {
        onSuccess: () => {
          setRegions([]);
          setShowConfirm(false);
        },
      }
    );
  };

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    const ms = Math.floor((s % 1) * 100);
    return `${m}:${sec.toString().padStart(2, '0')}.${ms.toString().padStart(2, '0')}`;
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3">Silence Detection</h3>

      <div className="grid grid-cols-2 gap-3 mb-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Threshold (dB)</label>
          <input
            type="range"
            min={-60}
            max={-10}
            value={thresholdDb}
            onChange={(e) => setThresholdDb(Number(e.target.value))}
            className="w-full"
          />
          <span className="text-xs text-gray-400">{thresholdDb} dB</span>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Min Duration (s)</label>
          <input
            type="range"
            min={0.1}
            max={5}
            step={0.1}
            value={minDuration}
            onChange={(e) => setMinDuration(Number(e.target.value))}
            className="w-full"
          />
          <span className="text-xs text-gray-400">{minDuration}s</span>
        </div>
      </div>

      <div className="flex gap-2 mb-3">
        <button
          onClick={handleDetect}
          disabled={detectMutation.isPending}
          className="bg-brand-600 hover:bg-brand-700 text-white px-3 py-1.5 rounded text-sm transition disabled:opacity-50"
        >
          {detectMutation.isPending ? 'Detecting...' : 'Detect Silence'}
        </button>
        {regions.length > 0 && (
          <button
            onClick={() => setShowConfirm(true)}
            className="bg-red-600 hover:bg-red-700 text-white px-3 py-1.5 rounded text-sm transition"
          >
            Auto-Trim
          </button>
        )}
      </div>

      {regions.length > 0 && (
        <div className="text-xs space-y-1">
          <p className="text-gray-500 font-medium">{regions.length} silence region(s) found:</p>
          {regions.map((r, i) => (
            <div key={i} className="flex items-center gap-2 text-gray-600 bg-red-50 px-2 py-1 rounded">
              <span className="text-red-500">#{i + 1}</span>
              <span>{formatTime(r.start)} - {formatTime(r.end)}</span>
              <span className="text-gray-400">({r.duration.toFixed(2)}s)</span>
            </div>
          ))}
        </div>
      )}

      {/* Confirmation modal */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 max-w-md mx-4">
            <h4 className="text-lg font-bold mb-2">Confirm Auto-Trim</h4>
            <p className="text-sm text-gray-600 mb-4">
              This will trim leading/trailing silence from the audio file. The original file
              will be preserved and can be restored.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded"
              >
                Cancel
              </button>
              <button
                onClick={handleAutoTrim}
                disabled={trimMutation.isPending}
                className="px-4 py-2 text-sm bg-red-600 hover:bg-red-700 text-white rounded disabled:opacity-50"
              >
                {trimMutation.isPending ? 'Trimming...' : 'Trim'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
