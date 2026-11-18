import { useCallback, useEffect, useRef, useState } from 'react';
import { apiWsUrl } from '../lib/pcm';

/**
 * The host's camera for people watching on the website (the radio stays audio
 * only). The camera is encoded in the browser (WebCodecs: VP8, else H.264) and sent
 * frame by frame on `/ws/live/{show}/video`; the server holds each frame in the
 * broadcast delay beside its sound, so a Dump cuts the picture too. The server asks
 * for a keyframe at the start and after a Dump. See wiki/live-shows.md.
 */
const WIDTH = 640, HEIGHT = 360, FPS = 15, KEY_EVERY = FPS * 2, BITRATE = 500_000;
const CODECS = ['vp8', 'avc1.42E01F'];

export function hostVideoSupported(): boolean {
  return typeof window !== 'undefined' && 'VideoEncoder' in window && 'VideoFrame' in window;
}

export function useHostVideo(showId: string | undefined) {
  const [on, setOn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const live = useRef<{ stop: () => void } | null>(null);

  const stop = useCallback(() => {
    const cur = live.current;
    live.current = null;
    if (cur) cur.stop();
    setOn(false);
    setStream(null);
  }, []);

  const start = useCallback(async () => {
    if (!showId || live.current) return;
    setError(null);
    if (!hostVideoSupported()) {
      setError('This browser cannot send video. Use a current Chrome, Edge or Safari.');
      return;
    }
    let cam: MediaStream;
    try {
      cam = await navigator.mediaDevices.getUserMedia({ video: { width: WIDTH, height: HEIGHT, frameRate: FPS } });
    } catch {
      setError('The camera could not be opened. Allow it for this site to show video.');
      return;
    }
    const settings = cam.getVideoTracks()[0]?.getSettings() ?? {};
    // Even numbers, never larger than asked for (some encoders refuse odd sizes)
    const scale = Math.min(1, WIDTH / (settings.width || WIDTH));
    const width = Math.max(16, Math.round(((settings.width || WIDTH) * scale) / 2) * 2);
    const height = Math.max(16, Math.round(((settings.height || HEIGHT) * scale) / 2) * 2);
    let codec: string | null = null;
    for (const c of CODECS) {
      try {
        const ok = await VideoEncoder.isConfigSupported({ codec: c, width, height, bitrate: BITRATE, framerate: FPS });
        if (ok.supported) { codec = c; break; }
      } catch { /* try the next */ }
    }
    if (!codec) {
      cam.getTracks().forEach(t => t.stop());
      setError('This browser cannot encode video for the show.');
      return;
    }
    const view = document.createElement('video');
    view.muted = true;
    view.playsInline = true;
    view.srcObject = cam;
    view.play().catch(() => {});
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const pen = canvas.getContext('2d');

    const ws = new WebSocket(apiWsUrl(`/ws/live/${showId}/video`));
    ws.binaryType = 'arraybuffer';
    let over = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    let wantKey = true;
    let count = 0;
    const encoder = new VideoEncoder({
      output: (chunk) => {
        if (over || ws.readyState !== WebSocket.OPEN) return;
        if (ws.bufferedAmount > 1_500_000) { wantKey = true; return; }   // a slow line: skip, then restart clean
        const out = new Uint8Array(1 + chunk.byteLength);
        out[0] = chunk.type === 'key' ? 1 : 0;
        chunk.copyTo(out.subarray(1));
        ws.send(out.buffer);
      },
      error: () => { setError('The video stopped: the browser could not encode it.'); me.stop(); setOn(false); setStream(null); live.current = null; },
    });
    const me = {
      stop: () => {
        if (over) return;
        over = true;
        if (timer) clearInterval(timer);
        cam.getTracks().forEach(t => t.stop());
        view.srcObject = null;
        try { if (encoder.state !== 'closed') encoder.close(); } catch { /* closed already */ }
        ws.onclose = null;
        ws.close();
      },
    };
    live.current = me;
    setStream(cam);
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token: localStorage.getItem('access_token') || '' }));
      ws.send(JSON.stringify({ type: 'config', codec, width, height }));
      encoder.configure({
        codec: codec!, width, height, bitrate: BITRATE, framerate: FPS, latencyMode: 'realtime',
        ...(codec!.startsWith('avc1') ? { avc: { format: 'annexb' as const } } : {}),
      });
      setOn(true);
      timer = setInterval(() => {
        if (over || !pen || view.readyState < 2 || encoder.state !== 'configured') return;
        if (encoder.encodeQueueSize > 2) return;      // the encoder is behind: drop this frame
        pen.drawImage(view, 0, 0, width, height);
        const frame = new VideoFrame(canvas, { timestamp: Math.round(performance.now() * 1000) });
        const key = wantKey || count % KEY_EVERY === 0;
        wantKey = false;
        count += 1;
        try { encoder.encode(frame, { keyFrame: key }); } finally { frame.close(); }
      }, 1000 / FPS);
    };
    ws.onmessage = (e) => {
      if (typeof e.data === 'string' && e.data.includes('"keyframe"')) wantKey = true;
    };
    ws.onclose = (e) => {
      if (live.current !== me) return;
      me.stop();
      live.current = null;
      setOn(false);
      setStream(null);
      if (e.code === 4009) setError('Go on air first: the video goes out with your microphone.');
      else if (e.code === 4000) setError(null);       // the show ended, or the camera moved to another page
      else if (e.code === 4001 || e.code === 4003) setError('You are not signed in for this show.');
      else setError('The video connection was lost. Start the camera again.');
    };
  }, [showId]);

  useEffect(() => () => { live.current?.stop(); live.current = null; }, []);

  return { on, error, stream, start, stop, supported: hostVideoSupported() };
}
