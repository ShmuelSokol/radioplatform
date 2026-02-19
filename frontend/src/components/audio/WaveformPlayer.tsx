import { useRef, useCallback, useEffect, useState, useImperativeHandle, forwardRef } from 'react';
import WaveSurfer from 'wavesurfer.js';
import RegionsPlugin, { type Region } from 'wavesurfer.js/dist/plugins/regions.js';
import type { SilenceRegion } from '../../types';

export interface WaveformPlayerHandle {
  /** Play from start to end (seconds). If no args, play/pause toggle. */
  playRegion: (start: number, end: number) => void;
  play: () => void;
  pause: () => void;
  seekTo: (seconds: number) => void;
  getDuration: () => number;
  getCurrentTime: () => number;
  getWaveSurfer: () => WaveSurfer | null;
}

interface WaveformPlayerProps {
  url: string;
  silenceRegions?: SilenceRegion[];
  previewRegion?: { start: number; end: number } | null;
  onReady?: (duration: number) => void;
  onTimeUpdate?: (currentTime: number) => void;
  onRegionUpdate?: (region: { start: number; end: number; id: string }) => void;
}

const WaveformPlayer = forwardRef<WaveformPlayerHandle, WaveformPlayerProps>(
  ({ url, silenceRegions, previewRegion, onReady, onTimeUpdate, onRegionUpdate }, ref) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const wsRef = useRef<WaveSurfer | null>(null);
    const regionsRef = useRef<RegionsPlugin | null>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [zoom, setZoom] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Initialize wavesurfer
    useEffect(() => {
      if (!containerRef.current || !url) return;

      setLoading(true);
      setError(null);

      const regions = RegionsPlugin.create();
      regionsRef.current = regions;

      // Create an <audio> element for broader codec support (MP2, etc.)
      // wavesurfer v7+ uses Web Audio API by default which can't decode MP2
      const audio = new Audio();
      audio.crossOrigin = 'anonymous';
      audio.preload = 'auto';

      const ws = WaveSurfer.create({
        container: containerRef.current,
        waveColor: '#4a90d9',
        progressColor: '#1a5fb4',
        cursorColor: '#e74c3c',
        cursorWidth: 2,
        height: 128,
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
        normalize: true,
        media: audio,
        plugins: [regions],
      });

      ws.on('ready', () => {
        const dur = ws.getDuration();
        setDuration(dur);
        setLoading(false);
        onReady?.(dur);
      });

      ws.on('error', (err) => {
        console.error('WaveSurfer error:', err);
        setLoading(false);
        setError(typeof err === 'string' ? err : 'Failed to load audio — the file may be in an unsupported format');
      });

      ws.on('timeupdate', (time) => {
        setCurrentTime(time);
        onTimeUpdate?.(time);
      });

      ws.on('play', () => setIsPlaying(true));
      ws.on('pause', () => setIsPlaying(false));
      ws.on('finish', () => setIsPlaying(false));

      regions.on('region-updated', (region: Region) => {
        onRegionUpdate?.({ start: region.start, end: region.end, id: region.id });
      });

      ws.load(url).catch((err: unknown) => {
        console.error('WaveSurfer load failed:', err);
        setLoading(false);
        setError('Failed to load audio — the file may not exist or is in an unsupported format');
      });
      wsRef.current = ws;

      return () => {
        ws.destroy();
        wsRef.current = null;
        regionsRef.current = null;
      };
    }, [url]); // eslint-disable-line react-hooks/exhaustive-deps

    // Update silence regions
    useEffect(() => {
      const regions = regionsRef.current;
      if (!regions) return;
      // Clear existing silence regions
      regions.getRegions().forEach((r) => {
        if (r.id.startsWith('silence-')) r.remove();
      });
      // Add new ones
      silenceRegions?.forEach((sr, i) => {
        regions.addRegion({
          id: `silence-${i}`,
          start: sr.start,
          end: sr.end,
          color: 'rgba(220, 53, 69, 0.25)',
          drag: true,
          resize: true,
        });
      });
    }, [silenceRegions]);

    // Update preview region
    useEffect(() => {
      const regions = regionsRef.current;
      if (!regions) return;
      regions.getRegions().forEach((r) => {
        if (r.id === 'preview') r.remove();
      });
      if (previewRegion) {
        regions.addRegion({
          id: 'preview',
          start: previewRegion.start,
          end: previewRegion.end,
          color: 'rgba(59, 130, 246, 0.2)',
          drag: false,
          resize: false,
        });
      }
    }, [previewRegion]);

    // Zoom
    useEffect(() => {
      wsRef.current?.zoom(zoom);
    }, [zoom]);

    useImperativeHandle(ref, () => ({
      playRegion(start: number, end: number) {
        const ws = wsRef.current;
        if (!ws) return;
        ws.setTime(start);
        ws.play();
        const stopAt = () => {
          if (ws.getCurrentTime() >= end) {
            ws.pause();
            ws.un('timeupdate', stopAt);
          }
        };
        ws.on('timeupdate', stopAt);
      },
      play() { wsRef.current?.play(); },
      pause() { wsRef.current?.pause(); },
      seekTo(s: number) { wsRef.current?.setTime(s); },
      getDuration() { return duration; },
      getCurrentTime() { return currentTime; },
      getWaveSurfer() { return wsRef.current; },
    }));

    const togglePlay = useCallback(() => {
      wsRef.current?.playPause();
    }, []);

    const stop = useCallback(() => {
      wsRef.current?.stop();
    }, []);

    const formatTime = (s: number) => {
      const m = Math.floor(s / 60);
      const sec = Math.floor(s % 60);
      return `${m}:${sec.toString().padStart(2, '0')}`;
    };

    return (
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        {loading && !error && (
          <div className="flex items-center justify-center h-32 text-gray-400">
            Loading waveform...
          </div>
        )}
        {error && (
          <div className="flex items-center justify-center h-32 text-red-500 text-sm">
            {error}
          </div>
        )}
        <div ref={containerRef} className={loading || error ? 'hidden' : ''} />

        {/* Controls */}
        <div className="flex items-center gap-4 mt-3">
          <button
            onClick={togglePlay}
            className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-1.5 rounded text-sm transition"
          >
            {isPlaying ? 'Pause' : 'Play'}
          </button>
          <button
            onClick={stop}
            className="bg-gray-200 hover:bg-gray-300 text-gray-700 px-4 py-1.5 rounded text-sm transition"
          >
            Stop
          </button>

          <span className="text-sm text-gray-500 font-mono">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>

          <div className="flex items-center gap-2 ml-auto">
            <label className="text-xs text-gray-500">Zoom</label>
            <input
              type="range"
              min={1}
              max={200}
              value={zoom}
              onChange={(e) => setZoom(Number(e.target.value))}
              className="w-24"
            />
          </div>
        </div>
      </div>
    );
  }
);

WaveformPlayer.displayName = 'WaveformPlayer';
export default WaveformPlayer;
