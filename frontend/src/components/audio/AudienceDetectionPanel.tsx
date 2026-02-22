import { useState, useCallback } from 'react';
import { useDetectAudience } from '../../hooks/useAssets';
import Spinner from '../Spinner';
import type { WaveformPlayerHandle } from './WaveformPlayer';
import type { AudienceSegment } from '../../api/assets';

interface AudienceDetectionPanelProps {
  assetId: string;
  waveformRef: React.RefObject<WaveformPlayerHandle | null>;
  onSegmentsDetected?: (segments: AudienceSegment[]) => void;
}

export default function AudienceDetectionPanel({ assetId, waveformRef, onSegmentsDetected }: AudienceDetectionPanelProps) {
  const [quietThreshold, setQuietThreshold] = useState(-25);
  const [silenceThreshold, setSilenceThreshold] = useState(-45);
  const [minDuration, setMinDuration] = useState(1.0);
  const [segments, setSegments] = useState<AudienceSegment[]>([]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const detectMutation = useDetectAudience();

  const handleDetect = () => {
    detectMutation.mutate(
      {
        id: assetId,
        quietThresholdDb: quietThreshold,
        silenceThresholdDb: silenceThreshold,
        minDuration,
      },
      {
        onSuccess: (data) => {
          setSegments(data.audience_segments);
          setSelectedIndex(null);
          onSegmentsDetected?.(data.audience_segments);
        },
      }
    );
  };

  const handleSegmentClick = useCallback((index: number) => {
    setSelectedIndex(index);
    const seg = segments[index];
    if (seg) {
      waveformRef.current?.seekTo(seg.start);
    }
  }, [segments, waveformRef]);

  const handlePlaySegment = () => {
    if (selectedIndex === null) return;
    const seg = segments[selectedIndex];
    waveformRef.current?.playRegion(seg.start, seg.end);
  };

  const handlePlayWithContext = () => {
    if (selectedIndex === null) return;
    const seg = segments[selectedIndex];
    // Play 3 seconds before to hear the context (rabbi's statement before the question)
    const start = Math.max(0, seg.start - 3);
    const end = seg.end + 2;
    waveformRef.current?.playRegion(start, end);
  };

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    const ms = Math.floor((s % 1) * 100);
    return `${m}:${sec.toString().padStart(2, '0')}.${ms.toString().padStart(2, '0')}`;
  };

  const selectedSegment = selectedIndex !== null ? segments[selectedIndex] : null;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3">Audience / Student Questions</h3>
      <p className="text-xs text-gray-400 mb-3">
        Detects quiet speech segments — typically students asking questions from a distance in a shiur recording.
      </p>

      {/* Detection controls */}
      <div className="grid grid-cols-3 gap-3 mb-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Quiet Threshold</label>
          <input
            type="range"
            min={-40}
            max={-15}
            value={quietThreshold}
            onChange={(e) => setQuietThreshold(Number(e.target.value))}
            className="w-full"
          />
          <span className="text-xs text-gray-400">{quietThreshold} dB</span>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Silence Threshold</label>
          <input
            type="range"
            min={-60}
            max={-30}
            value={silenceThreshold}
            onChange={(e) => setSilenceThreshold(Number(e.target.value))}
            className="w-full"
          />
          <span className="text-xs text-gray-400">{silenceThreshold} dB</span>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Min Duration</label>
          <input
            type="range"
            min={0.5}
            max={5}
            step={0.5}
            value={minDuration}
            onChange={(e) => setMinDuration(Number(e.target.value))}
            className="w-full"
          />
          <span className="text-xs text-gray-400">{minDuration}s</span>
        </div>
      </div>

      {/* Detect button */}
      <button
        onClick={handleDetect}
        disabled={detectMutation.isPending}
        className="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1.5 rounded text-sm transition disabled:opacity-50 mb-3"
      >
        {detectMutation.isPending ? <><Spinner className="mr-1" />Scanning...</> : 'Detect Questions'}
      </button>

      {/* Results */}
      {segments.length > 0 && (
        <div className="text-xs space-y-1 mb-3">
          <p className="text-gray-500 font-medium">{segments.length} audience segment(s) found — click to review:</p>
          <div className="max-h-48 overflow-y-auto space-y-1">
            {segments.map((seg, i) => (
              <button
                key={i}
                onClick={() => handleSegmentClick(i)}
                className={`flex items-center gap-2 w-full text-left px-2 py-1.5 rounded transition ${
                  i === selectedIndex
                    ? 'bg-purple-100 border border-purple-300 text-purple-700'
                    : 'text-gray-600 bg-purple-50 hover:bg-purple-100'
                }`}
              >
                <span className="text-purple-500 font-mono">#{i + 1}</span>
                <span>{formatTime(seg.start)} - {formatTime(seg.end)}</span>
                <span className="text-gray-400">({seg.duration.toFixed(1)}s)</span>
                {i === selectedIndex && (
                  <span className="ml-auto text-purple-500 text-[10px] uppercase font-semibold">Selected</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {segments.length === 0 && detectMutation.isSuccess && (
        <p className="text-xs text-gray-400 mt-2">No audience segments detected. Try adjusting the thresholds.</p>
      )}

      {/* Selected segment actions */}
      {selectedSegment && (
        <div className="bg-gray-50 border border-gray-200 rounded p-3">
          <p className="text-xs text-gray-500 mb-2">
            Segment #{(selectedIndex ?? 0) + 1}: {formatTime(selectedSegment.start)} - {formatTime(selectedSegment.end)} ({selectedSegment.duration.toFixed(1)}s)
          </p>
          <div className="flex gap-2">
            <button
              onClick={handlePlaySegment}
              className="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1 rounded text-xs transition"
            >
              Play Segment
            </button>
            <button
              onClick={handlePlayWithContext}
              className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded text-xs transition"
            >
              Play with Context
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
    </div>
  );
}
