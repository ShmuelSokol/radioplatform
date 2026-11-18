import { useCallback, useEffect, useRef, useState } from 'react';
import { apiWsUrl } from '../lib/pcm';

/**
 * Watch a station's live show: the host's camera, through the same delay as the
 * sound. The socket (`/ws/live/station/{id}/watch`) says whether there is a picture
 * and how to decode it, then sends one encoded frame per message (first byte 1 for a
 * keyframe). Frames are decoded with WebCodecs and drawn on the canvas given to
 * `attach`. The socket is open only while `enabled` (watching costs data).
 */
export function liveVideoSupported(): boolean {
  return typeof window !== 'undefined' && 'VideoDecoder' in window && 'EncodedVideoChunk' in window;
}

export function useLiveVideo(stationId: string | null, enabled: boolean) {
  const [showing, setShowing] = useState(false);      // frames are being drawn
  const [failed, setFailed] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const attach = useCallback((el: HTMLCanvasElement | null) => { canvasRef.current = el; }, []);

  useEffect(() => {
    if (!stationId || !enabled) return;
    setFailed(null);
    if (!liveVideoSupported()) {
      setFailed('This browser cannot show the video. The sound plays as usual.');
      return;
    }
    let closed = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let decoder: VideoDecoder | null = null;
    let needKey = true;

    const drop = () => {
      try { if (decoder && decoder.state !== 'closed') decoder.close(); } catch { /* closed */ }
      decoder = null;
      needKey = true;
      setShowing(false);
    };
    const configure = async (msg: { codec: string; width: number; height: number }) => {
      drop();
      const config = { codec: msg.codec, codedWidth: msg.width, codedHeight: msg.height, optimizeForLatency: true };
      try {
        const ok = await VideoDecoder.isConfigSupported(config);
        if (!ok.supported) { setFailed('This browser cannot show this video. The sound plays as usual.'); return; }
      } catch { setFailed('This browser cannot show this video. The sound plays as usual.'); return; }
      if (closed) return;
      const made = new VideoDecoder({
        output: (frame) => {
          const canvas = canvasRef.current;
          if (canvas) {
            if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
              canvas.width = frame.displayWidth;
              canvas.height = frame.displayHeight;
            }
            canvas.getContext('2d')?.drawImage(frame, 0, 0);
            setShowing(true);
          }
          frame.close();
        },
        // A broken frame: start again from the next keyframe (one comes every 2 s)
        error: () => { if (decoder === made) { decoder = null; needKey = true; configure(msg); } },
      });
      made.configure(config);
      decoder = made;
      setFailed(null);
    };

    const connect = () => {
      if (closed) return;
      ws = new WebSocket(apiWsUrl(`/ws/live/station/${stationId}/watch`));
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => { attempts = 0; };
      ws.onmessage = (e) => {
        if (typeof e.data === 'string') {
          try {
            const msg = JSON.parse(e.data);
            if (msg.type !== 'video') return;
            if (msg.on && msg.codec) configure(msg); else drop();
          } catch { /* not ours */ }
          return;
        }
        const data = new Uint8Array(e.data as ArrayBuffer);
        if (!decoder || decoder.state !== 'configured' || data.length < 2) return;
        const key = (data[0] & 1) === 1;
        if (needKey && !key) return;
        if (decoder.decodeQueueSize > 8 && !key) { needKey = true; return; }   // too far behind: skip to a keyframe
        needKey = false;
        try {
          decoder.decode(new EncodedVideoChunk({
            type: key ? 'key' : 'delta', timestamp: Math.round(performance.now() * 1000), data: data.subarray(1),
          }));
        } catch { needKey = true; }
      };
      ws.onclose = (e) => {
        if (closed) return;
        drop();
        if (e.code === 4008) { setFailed('Too many people are watching right now. The sound plays as usual.'); return; }
        retry = setTimeout(connect, Math.min(1000 * 2 ** attempts++, 15000));
      };
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      if (ws) { ws.onclose = null; ws.close(); }
      drop();
    };
  }, [stationId, enabled]);

  return { showing, failed, attach, supported: liveVideoSupported() };
}
