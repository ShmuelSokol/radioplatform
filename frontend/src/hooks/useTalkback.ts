import { useCallback, useEffect, useRef, useState } from 'react';
import { apiWsUrl, makeResampler, PcmPlayer } from '../lib/pcm';

/**
 * Talkback: the screener or the host talks to one web caller off the air. A second
 * microphone capture and its own socket (`/ws/live/{show}/talk/{call}`): what is
 * said goes to that caller only and the caller's voice comes back here. Nothing of
 * it reaches listeners. The server ends it when the caller goes on air or leaves.
 * `onChange` tells the page (the Host Console mutes the host's on-air microphone
 * meanwhile). See wiki/live-shows.md.
 */
export function useTalkback(showId: string | undefined, onChange?: (talking: boolean) => void) {
  const [talkingTo, setTalkingTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = useRef<{ stop: () => void } | null>(null);
  const changed = useRef(onChange);
  changed.current = onChange;

  const stop = useCallback(() => {
    const cur = live.current;
    live.current = null;
    if (cur) cur.stop();
    setTalkingTo(prev => { if (prev !== null) changed.current?.(false); return null; });
  }, []);

  const start = useCallback(async (callId: string) => {
    if (!showId) return;
    stop();
    setError(null);
    let mic: MediaStream;
    try {
      mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError('The microphone could not be opened. Allow it for this site to talk to a caller.');
      return;
    }
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(mic);
    const processor = ctx.createScriptProcessor(2048, 1, 1);
    const resample = makeResampler(ctx.sampleRate);
    const caller = new PcmPlayer(ctx);
    const ws = new WebSocket(apiWsUrl(`/ws/live/${showId}/talk/${callId}`));
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
        caller.stop();
        ctx.close().catch(() => {});
      },
    };
    live.current = me;
    setTalkingTo(callId);
    changed.current?.(true);
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token: localStorage.getItem('access_token') || '' }));
      processor.onaudioprocess = (e) => {
        const pcm = resample(e.inputBuffer.getChannelData(0));
        if (ws.readyState === WebSocket.OPEN && pcm.length) ws.send(pcm.buffer);
      };
      source.connect(processor);
      processor.connect(ctx.destination); // runs only when connected; its output is silent
    };
    ws.onmessage = (e) => { if (e.data instanceof ArrayBuffer) caller.push(e.data); };
    ws.onclose = (e) => {
      if (live.current !== me) return;
      stop();
      if (e.code === 4004) setError('That caller is not connected from the website any more, so you cannot talk to them here.');
      else if (e.code === 4009) setError('That caller is on air. Hold them first to talk off the air.');
      else if (e.code === 4001 || e.code === 4003) setError('You are not allowed to talk to callers of this show.');
      // 4000: the caller went on air or someone else took over. Nothing to say.
    };
  }, [showId, stop]);

  useEffect(() => () => { const cur = live.current; live.current = null; cur?.stop(); }, []);

  return { talkingTo, start, stop, error };
}
