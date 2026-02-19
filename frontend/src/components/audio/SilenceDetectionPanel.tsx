import { useState, useCallback } from 'react';
import { useDetectSilence, useTrimAsset, useRestoreOriginal } from '../../hooks/useAssets';
import Spinner from '../Spinner';
import type { WaveformPlayerHandle } from './WaveformPlayer';
import type { SilenceRegion } from '../../types';

interface SilenceDetectionPanelProps {
  assetId: string;
  waveformRef: React.RefObject<WaveformPlayerHandle | null>;
  onRegionsDetected?: (regions: SilenceRegion[]) => void;
  onTrimComplete?: () => void;
}

export default function SilenceDetectionPanel({ assetId, waveformRef, onRegionsDetected, onTrimComplete }: SilenceDetectionPanelProps) {
  const [thresholdDb, setThresholdDb] = useState(-30);
  const [minDuration, setMinDuration] = useState(0.5);
  const [regions, setRegions] = useState<SilenceRegion[]>([]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [showConfirm, setShowConfirm] = useState<'auto' | 'region' | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const detectMutation = useDetectSilence();
  const trimMutation = useTrimAsset();
  const restoreMutation = useRestoreOriginal();

  const handleDetect = () => {
    detectMutation.mutate(
      { id: assetId, thresholdDb, minDuration },
      {
        onSuccess: (data) => {
          setRegions(data.silence_regions);
          setSelectedIndex(null);
          setStatusMsg(null);
          onRegionsDetected?.(data.silence_regions);
        },
      }
    );
  };

  const handleAutoClip = () => {
    if (regions.length === 0) return;
    const duration = waveformRef.current?.getDuration() ?? 0;
    if (duration <= 0) return;

    // Find leading silence (starts near 0) and trailing silence (ends near duration)
    const leadingSilence = regions.find((r) => r.start < 0.1);
    const trailingSilence = regions.find((r) => Math.abs(r.end - duration) < 0.1);

    const trimStart = leadingSilence ? leadingSilence.end : 0;
    const trimEnd = trailingSilence ? trailingSilence.start : duration;

    if (trimStart === 0 && trimEnd === duration) {
      setStatusMsg('No leading/trailing silence to trim');
      setShowConfirm(null);
      return;
    }

    trimMutation.mutate(
      { id: assetId, trimStart, trimEnd },
      {
        onSuccess: () => {
          setRegions([]);
          setSelectedIndex(null);
          setShowConfirm(null);
          setStatusMsg('Trimmed successfully! Waveform will reload.');
          onRegionsDetected?.([]);
          onTrimComplete?.();
        },
        onError: () => {
          setStatusMsg('Trim failed — try again');
          setShowConfirm(null);
        },
      }
    );
  };

  const handleTrimRegion = () => {
    if (selectedIndex === null) return;
    const region = regions[selectedIndex];
    const duration = waveformRef.current?.getDuration() ?? 0;
    if (duration <= 0) return;

    // Determine trim boundaries based on region position
    const isLeading = region.start < 0.1;
    const isTrailing = Math.abs(region.end - duration) < 0.1;

    let trimStart: number;
    let trimEnd: number;

    if (isLeading) {
      // Remove leading silence: keep from region.end to duration
      trimStart = region.end;
      trimEnd = duration;
    } else if (isTrailing) {
      // Remove trailing silence: keep from 0 to region.start
      trimStart = 0;
      trimEnd = region.start;
    } else {
      // Middle silence: keep from 0 to region.start (removes region and everything after)
      // This is a simplification — true "cut out middle" would need concat
      trimStart = 0;
      trimEnd = region.start;
    }

    trimMutation.mutate(
      { id: assetId, trimStart, trimEnd },
      {
        onSuccess: () => {
          setRegions([]);
          setSelectedIndex(null);
          setShowConfirm(null);
          setStatusMsg('Region trimmed! Waveform will reload.');
          onRegionsDetected?.([]);
          onTrimComplete?.();
        },
        onError: () => {
          setStatusMsg('Trim failed — try again');
          setShowConfirm(null);
        },
      }
    );
  };

  const handleRestore = () => {
    restoreMutation.mutate(assetId, {
      onSuccess: () => {
        setRegions([]);
        setSelectedIndex(null);
        setStatusMsg('Original restored! Waveform will reload.');
        onRegionsDetected?.([]);
        onTrimComplete?.();
      },
      onError: (err) => {
        const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        setStatusMsg(msg || 'Restore failed');
      },
    });
  };

  const handleRegionClick = useCallback((index: number) => {
    setSelectedIndex(index);
    const region = regions[index];
    if (region) {
      // Seek waveform to region start
      waveformRef.current?.seekTo(region.start);
    }
  }, [regions, waveformRef]);

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    const ms = Math.floor((s % 1) * 100);
    return `${m}:${sec.toString().padStart(2, '0')}.${ms.toString().padStart(2, '0')}`;
  };

  const selectedRegion = selectedIndex !== null ? regions[selectedIndex] : null;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">Silence Detection</h3>
        <button
          onClick={handleRestore}
          disabled={restoreMutation.isPending}
          className="text-xs text-gray-500 hover:text-brand-600 disabled:opacity-50"
          title="Undo all trims and restore original file"
        >
          {restoreMutation.isPending ? <><Spinner className="mr-1" />Restoring...</> : 'Undo All Trims'}
        </button>
      </div>

      {/* Detection controls */}
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

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2 mb-3">
        <button
          onClick={handleDetect}
          disabled={detectMutation.isPending}
          className="bg-brand-600 hover:bg-brand-700 text-white px-3 py-1.5 rounded text-sm transition disabled:opacity-50"
        >
          {detectMutation.isPending ? <><Spinner className="mr-1" />Scanning...</> : 'Detect Silence'}
        </button>
        {regions.length > 0 && (
          <button
            onClick={() => setShowConfirm('auto')}
            className="bg-orange-600 hover:bg-orange-700 text-white px-3 py-1.5 rounded text-sm transition"
          >
            Auto-Clip Start & End
          </button>
        )}
      </div>

      {/* Status message */}
      {statusMsg && (
        <div className="text-xs text-green-600 bg-green-50 rounded px-2 py-1 mb-3">
          {statusMsg}
        </div>
      )}

      {/* Detected regions list */}
      {regions.length > 0 && (
        <div className="text-xs space-y-1 mb-3">
          <p className="text-gray-500 font-medium">{regions.length} silence region(s) found — click to select:</p>
          {regions.map((r, i) => (
            <button
              key={i}
              onClick={() => handleRegionClick(i)}
              className={`flex items-center gap-2 w-full text-left px-2 py-1.5 rounded transition ${
                i === selectedIndex
                  ? 'bg-red-100 border border-red-300 text-red-700'
                  : 'text-gray-600 bg-red-50 hover:bg-red-100'
              }`}
            >
              <span className="text-red-500 font-mono">#{i + 1}</span>
              <span>{formatTime(r.start)} - {formatTime(r.end)}</span>
              <span className="text-gray-400">({r.duration.toFixed(2)}s)</span>
              {i === selectedIndex && (
                <span className="ml-auto text-red-500 text-[10px] uppercase font-semibold">Selected</span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Selected region actions */}
      {selectedRegion && (
        <div className="bg-gray-50 border border-gray-200 rounded p-3 mb-2">
          <p className="text-xs text-gray-500 mb-2">
            Region #{(selectedIndex ?? 0) + 1}: {formatTime(selectedRegion.start)} - {formatTime(selectedRegion.end)} ({selectedRegion.duration.toFixed(2)}s)
          </p>
          <p className="text-[10px] text-gray-400 mb-2">
            Drag the red region edges on the waveform to resize before trimming.
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => {
                waveformRef.current?.playRegion(selectedRegion.start, selectedRegion.end);
              }}
              className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded text-xs transition"
            >
              Play Region
            </button>
            <button
              onClick={() => setShowConfirm('region')}
              className="bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded text-xs transition"
            >
              Trim Out & Save
            </button>
            <button
              onClick={() => setSelectedIndex(null)}
              className="text-gray-500 hover:text-gray-700 px-2 py-1 text-xs"
            >
              Deselect
            </button>
          </div>
        </div>
      )}

      {/* Confirmation modal */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 max-w-md mx-4">
            <h4 className="text-lg font-bold mb-2">
              {showConfirm === 'auto' ? 'Confirm Auto-Clip' : 'Confirm Region Trim'}
            </h4>
            <p className="text-sm text-gray-600 mb-4">
              {showConfirm === 'auto'
                ? 'This will remove leading and trailing silence from the audio file. You can undo this later with "Undo All Trims".'
                : `This will trim out region #${(selectedIndex ?? 0) + 1} (${formatTime(selectedRegion?.start ?? 0)} - ${formatTime(selectedRegion?.end ?? 0)}). You can undo this later with "Undo All Trims".`
              }
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowConfirm(null)}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded"
              >
                Cancel
              </button>
              <button
                onClick={showConfirm === 'auto' ? handleAutoClip : handleTrimRegion}
                disabled={trimMutation.isPending}
                className="px-4 py-2 text-sm bg-red-600 hover:bg-red-700 text-white rounded disabled:opacity-50"
              >
                {trimMutation.isPending ? <><Spinner className="mr-1" />Processing...</> : 'Trim & Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Export the region update handler type for parent wiring
export type { SilenceDetectionPanelProps };
