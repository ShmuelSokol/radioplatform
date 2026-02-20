import { useEffect, useRef, useCallback } from 'react';
import type Hls from 'hls.js';
import { usePlayerStore } from '../stores/playerStore';

const loadHls = () => import('hls.js').then(m => m.default);

export function useAudioPlayer() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const hlsRef = useRef<Hls | null>(null);
  const { hlsUrl, isPlaying, volume, stop } = usePlayerStore();

  const attachHls = useCallback(async (url: string) => {
    const audio = audioRef.current;
    if (!audio) return;

    if (hlsRef.current) {
      hlsRef.current.destroy();
    }

    const HlsClass = await loadHls();

    if (HlsClass.isSupported()) {
      const hls = new HlsClass({ enableWorker: true });
      hls.loadSource(url);
      hls.attachMedia(audio);
      hls.on(HlsClass.Events.MANIFEST_PARSED, () => {
        audio.play().catch(() => {});
      });
      hls.on(HlsClass.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          stop();
        }
      });
      hlsRef.current = hls;
    } else if (audio.canPlayType('application/vnd.apple.mpegurl')) {
      audio.src = url;
      audio.play().catch(() => {});
    }
  }, [stop]);

  useEffect(() => {
    if (hlsUrl && isPlaying) {
      attachHls(hlsUrl);
    } else {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
      if (audioRef.current) {
        audioRef.current.pause();
      }
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [hlsUrl, isPlaying, attachHls]);

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = volume;
    }
  }, [volume]);

  return { audioRef };
}
