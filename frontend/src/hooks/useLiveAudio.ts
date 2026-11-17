import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * A station's live show, heard in this page. The host's microphone reaches the
 * server as raw 16-bit mono PCM (useHostAudio) and is passed straight on to every
 * page listening to the station (no broadcast server needed). While a show is on
 * air the page's music player must be silent: `onAir` says so.
 */
const START_DELAY = 0.35; // seconds of buffer before the first frame plays (network jitter)
const MAX_LAG = 1.5;      // further behind than this, drop the backlog and catch up

function listenUrl(stationId: string): string {
  const wsBase = import.meta.env.VITE_WS_URL;
  const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
  const path = `/ws/live/station/${stationId}/listen`;
  if (wsBase) return `${wsBase}/api/v1${path}`;
  if (apiUrl.startsWith('http')) return apiUrl.replace(/^http/, 'ws') + path;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${apiUrl}${path}`;
}

export function useLiveAudio(stationId: string | null, enabled: boolean) {
  const [onAir, setOnAir] = useState(false);
  const ctxRef = useRef<AudioContext | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const nextTimeRef = useRef(0);
  const rateRef = useRef(24000);
  const levelRef = useRef(1);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const liveRef = useRef(false); // the server said a host is on air

  const ensureCtx = useCallback(() => {
    if (!ctxRef.current) {
      const ctx = new AudioContext();
      const gain = ctx.createGain();
      gain.gain.value = levelRef.current;
      gain.connect(ctx.destination);
      ctxRef.current = ctx;
      gainRef.current = gain;
    }
    return ctxRef.current;
  }, []);

  /** Call inside a click (Listen, the speaker button): browsers start audio only then. */
  const unlock = useCallback(() => {
    const ctx = ensureCtx();
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  }, [ensureCtx]);

  /** The page's volume and mute, applied to the live audio too */
  const setLevel = useCallback((volume: number, muted: boolean) => {
    levelRef.current = muted ? 0 : volume;
    if (gainRef.current) gainRef.current.gain.value = levelRef.current;
  }, []);

  useEffect(() => {
    if (!stationId) return;
    let closed = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;

    const play = (buf: ArrayBuffer) => {
      // Only while a show is on air: a frame still in flight after the host left
      // must not play over the music that comes back
      if (!enabledRef.current || !liveRef.current) return;
      const ctx = ensureCtx();
      if (ctx.state !== 'running') { ctx.resume().catch(() => {}); return; }
      const pcm = new Int16Array(buf);
      if (!pcm.length) return;
      const audio = ctx.createBuffer(1, pcm.length, rateRef.current);
      const data = audio.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) data[i] = pcm[i] / 32768;
      const now = ctx.currentTime;
      if (nextTimeRef.current < now || nextTimeRef.current > now + MAX_LAG) nextTimeRef.current = now + START_DELAY;
      const src = ctx.createBufferSource();
      src.buffer = audio;
      src.connect(gainRef.current!);
      src.start(nextTimeRef.current);
      nextTimeRef.current += audio.duration;
    };

    const connect = () => {
      if (closed) return;
      ws = new WebSocket(listenUrl(stationId));
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => { attempts = 0; };
      ws.onmessage = (e) => {
        if (typeof e.data === 'string') {
          try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'live_state') {
              if (msg.sample_rate) rateRef.current = msg.sample_rate;
              liveRef.current = !!msg.live;
              setOnAir(!!msg.live);
              nextTimeRef.current = 0;
            }
          } catch { /* not ours */ }
          return;
        }
        play(e.data as ArrayBuffer);
      };
      ws.onclose = () => {
        if (closed) return;
        liveRef.current = false;
        setOnAir(false);
        retry = setTimeout(connect, Math.min(1000 * 2 ** attempts++, 15000));
      };
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      if (ws) { ws.onclose = null; ws.close(); }
      setOnAir(false);
    };
  }, [stationId, ensureCtx]);

  useEffect(() => () => { ctxRef.current?.close().catch(() => {}); ctxRef.current = null; }, []);

  return { onAir, unlock, setLevel };
}
