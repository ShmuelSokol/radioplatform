import { useCallback, useEffect, useRef, useState } from 'react';
import { apiWsUrl, makeResampler, PcmPlayer } from '../lib/pcm';

/**
 * Co-host: join a live show from another place. Own microphone capture and socket
 * (`/ws/live/{show}/cohost`): the server mixes this microphone into the show like a
 * caller on air (through the delay) and sends back the host, the callers and the
 * other co-hosts, never one's own voice. The host's microphone carries the show: a
 * co-host is heard only while a host is broadcasting. See wiki/live-shows.md.
 */
export function useCohost(showId: string | undefined) {
  const [on, setOn] = useState(false);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = useRef<{ stop: () => void } | null>(null);
  const mutedRef = useRef(false);
  mutedRef.current = muted;

  const stop = useCallback(() => {
    const cur = live.current;
    live.current = null;
    if (cur) cur.stop();
    setOn(false);
    setMuted(false);
  }, []);

  const start = useCallback(async () => {
    if (!showId || live.current) return;
    setError(null);
    let mic: MediaStream;
    try {
      // Echo cancellation: the co-host hears the show from this same page
      mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch {
      setError('The microphone could not be opened. Allow it for this site to join the show.');
      return;
    }
    const ctx = new AudioContext();
    ctx.resume().catch(() => {});
    const source = ctx.createMediaStreamSource(mic);
    const processor = ctx.createScriptProcessor(2048, 1, 1);
    const resample = makeResampler(ctx.sampleRate);
    const show = new PcmPlayer(ctx);
    const ws = new WebSocket(apiWsUrl(`/ws/live/${showId}/cohost`));
    ws.binaryType = 'arraybuffer';
    let over = false;
    const me = {
      stop: () => {
        if (over) return;
        over = true;
        processor.onaudioprocess = null;
        processor.disconnect();
        mic.getTracks().forEach(t => t.stop());
        ws.onclose = null;
        ws.close();
        show.stop();
        ctx.close().catch(() => {});
      },
    };
    live.current = me;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token: localStorage.getItem('access_token') || '' }));
      processor.onaudioprocess = (e) => {
        const pcm = resample(e.inputBuffer.getChannelData(0));
        if (mutedRef.current) pcm.fill(0);   // silence still goes, so the line stays in step
        if (ws.readyState === WebSocket.OPEN && pcm.length) ws.send(pcm.buffer);
      };
      source.connect(processor);
      processor.connect(ctx.destination); // runs only when connected; its output is silent
    };
    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) show.push(e.data);
      else if (typeof e.data === 'string' && e.data.includes('"cohost"')) setOn(true);
    };
    ws.onclose = (e) => {
      if (live.current !== me) return;
      stop();
      if (e.code === 4009) setError('The show is not live, so there is nothing to join yet.');
      else if (e.code === 4008) setError('Three co-hosts are on already.');
      else if (e.code === 4001 || e.code === 4003) setError('You are not allowed to join this show. A manager, or the show\'s own host, can.');
      else if (e.code === 4000) setError('The show ended, or you joined from another page.');
      else setError('The connection to the show was lost. Join again.');
    };
  }, [showId, stop]);

  useEffect(() => () => { const cur = live.current; live.current = null; cur?.stop(); }, []);

  return { on, muted, setMuted, start, stop, error };
}
