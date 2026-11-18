import { useEffect, useRef, useState, useCallback } from 'react';
import { makeResampler, PcmPlayer } from '../lib/pcm';

interface UseHostAudioReturn {
  isRecording: boolean;
  startRecording: () => void;
  stopRecording: () => void;
  audioLevel: number;
  error: string | null;
  /** Cough button: while true the microphone is sent as silence (callers still go out) */
  muted: boolean;
  setMuted: (on: boolean) => void;
  /** Carts: play a recording into the show (it goes out with the microphone, through
   *  the delay, and the host hears it). `id` names it for `cartPlaying`. */
  playCart: (id: string, url: string) => Promise<void>;
  stopCart: () => void;
  /** The cart sounding now, or loading; null when none. */
  cartPlaying: string | null;
  /** The connection dropped and is being opened again; the microphone is kept. */
  reconnecting: boolean;
}

/**
 * Hook for capturing host microphone audio and streaming it via WebSocket.
 * Sends raw 16-bit mono PCM at LIVE_RATE: the server passes it straight on to
 * every page listening to the station (useLiveAudio), which plays it with Web
 * Audio. That works in every browser, Safari on iPhone included; the earlier
 * webm/opus chunks could only have been played through a broadcast server.
 */
// The format and resampler are shared with web callers (lib/pcm.ts). The server
// sends the callers on air back down this socket, so the host hears them.
const RECONNECT_EVERY_MS = 1000;
const RECONNECT_FOR_MS = 60000;   // then the host is told and must press Start Broadcasting

export function useHostAudio(showId: string | undefined): UseHostAudioReturn {
  const [isRecording, setIsRecording] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const [muted, setMutedState] = useState(false);
  const mutedRef = useRef(false);
  // The microphone and the carts meet in `bus`, which is what goes to the station.
  // Mute and Cough close the microphone's own gain, so a cart still goes out.
  const micGainRef = useRef<GainNode | null>(null);
  const busRef = useRef<GainNode | null>(null);
  const cartRef = useRef<{ id: string; source: AudioBufferSourceNode | null } | null>(null);
  const [cartPlaying, setCartPlaying] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const authedRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const setMuted = useCallback((on: boolean) => {
    mutedRef.current = on;
    setMutedState(on);
    const g = micGainRef.current;
    if (g) g.gain.value = on ? 0 : 1;
  }, []);

  const stopCart = useCallback(() => {
    const cart = cartRef.current;
    cartRef.current = null;
    setCartPlaying(null);
    if (cart?.source) {
      cart.source.onended = null;
      try { cart.source.stop(); } catch { /* not started */ }
      cart.source.disconnect();
    }
  }, []);

  const playCart = useCallback(async (id: string, url: string) => {
    const ctx = audioContextRef.current;
    const bus = busRef.current;
    if (!ctx || !bus) return;
    stopCart();
    const mine = { id, source: null as AudioBufferSourceNode | null };
    cartRef.current = mine;
    setCartPlaying(id);
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(String(res.status));
      const audio = await ctx.decodeAudioData(await res.arrayBuffer());
      if (cartRef.current !== mine || audioContextRef.current !== ctx) return; // stopped or replaced meanwhile
      const source = ctx.createBufferSource();
      source.buffer = audio;
      source.connect(bus);              // to listeners, with the microphone
      source.connect(ctx.destination);  // and to the host's own ears
      source.onended = () => {
        if (cartRef.current === mine) { cartRef.current = null; setCartPlaying(null); }
        source.disconnect();
      };
      mine.source = source;
      source.start();
    } catch {
      if (cartRef.current === mine) {
        cartRef.current = null;
        setCartPlaying(null);
        setError("That recording could not be played. Its file may be missing or in a format browsers don't play.");
      }
    }
  }, [stopCart]);

  const updateLevel = useCallback(() => {
    if (!analyserRef.current) return;
    const data = new Uint8Array(analyserRef.current.fftSize);
    analyserRef.current.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / data.length);
    setAudioLevel(Math.min(1, rms * 3));
    rafRef.current = requestAnimationFrame(updateLevel);
  }, []);

  const connectAudioWS = useCallback(() => {
    if (!showId) return null;

    const wsBase = import.meta.env.VITE_WS_URL;
    const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
    let wsUrl: string;

    if (wsBase) {
      wsUrl = `${wsBase}/api/v1/ws/live/${showId}/audio`;
    } else if (apiUrl.startsWith('http')) {
      wsUrl = apiUrl.replace(/^http/, 'ws') + `/ws/live/${showId}/audio`;
    } else {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${protocol}//${window.location.host}${apiUrl}/ws/live/${showId}/audio`;
    }

    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    return ws;
  }, [showId]);

  const startRecording = useCallback(async () => {
    if (!showId) return;
    setError(null);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      // Set up analyser for VU meter
      const ctx = new AudioContext();
      audioContextRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;
      rafRef.current = requestAnimationFrame(updateLevel);

      // The microphone and the carts meet in `bus`; a processor turns the bus into
      // frames for whichever socket is open (about 85 ms each). Built once: the
      // socket can be opened again without touching the microphone.
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      const resample = makeResampler(ctx.sampleRate);
      processor.onaudioprocess = (e) => {
        // Muted: frames still go (they are the clock that carries the callers and
        // the carts); the microphone's gain is closed, so they hold no microphone
        const pcm = resample(e.inputBuffer.getChannelData(0));
        const ws = wsRef.current;
        if (ws && authedRef.current === ws && ws.readyState === WebSocket.OPEN && pcm.length) ws.send(pcm.buffer);
      };
      const micGain = ctx.createGain();
      micGain.gain.value = mutedRef.current ? 0 : 1;
      const bus = ctx.createGain();
      source.connect(micGain);
      micGain.connect(bus);
      bus.connect(processor);
      micGainRef.current = micGain;
      busRef.current = bus;
      processor.connect(ctx.destination); // runs only when connected; its output is silent
      processorRef.current = processor;

      // Callers on air, heard by the host (use headphones, or they go out again on the mic)
      const callers = new PcmPlayer(ctx);

      let lostAt = 0;       // when the connection was lost (0 = it is up)
      let everOpen = false;
      const open = () => {
        if (audioContextRef.current !== ctx) return;   // stopped meanwhile
        const ws = connectAudioWS();
        if (!ws) return;
        wsRef.current = ws;
        ws.onopen = () => {
          // The server reads a text auth message first, then audio. The token used to go
          // in the address instead: the server dropped the socket at once (no host
          // audio ever arrived) and the address, token included, was written to the logs.
          ws.send(JSON.stringify({ type: 'auth', token: localStorage.getItem('access_token') || '' }));
          authedRef.current = ws;
          everOpen = true;
          lostAt = 0;
          setReconnecting(false);
          setError(null);
          setIsRecording(true);
        };
        ws.onmessage = (e) => { if (e.data instanceof ArrayBuffer) callers.push(e.data); };
        ws.onclose = (e) => {
          if (wsRef.current !== ws) return;   // Stop Broadcasting, or a newer socket
          // The server restarted or the network dropped: keep the microphone and try
          // again for a while, so the host is back on air without pressing anything
          // (a deploy during a show left 11 s of silence until End, 2026-11-18).
          // Not when the server said why: 4000 the station went back to music after
          // no sound came, 4001/4003 not signed in, 4004 no such show.
          const told = [4000, 4001, 4003, 4004].includes(e.code);
          if (!lostAt) lostAt = Date.now();
          if (everOpen && !told && Date.now() - lostAt < RECONNECT_FOR_MS) {
            setReconnecting(true);
            retryRef.current = setTimeout(open, RECONNECT_EVERY_MS);
            return;
          }
          stopRef.current();
          setError(everOpen
            ? 'The connection to the station was lost. Press Start Broadcasting to go back on air.'
            : 'Audio WebSocket connection failed');
        };
      };
      open();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to access microphone');
    }
  }, [showId, connectAudioWS, updateLevel]);

  const stopRef = useRef<() => void>(() => {});
  const stopRecording = useCallback(() => {
    stopCart();
    micGainRef.current = null;
    busRef.current = null;
    if (processorRef.current) {
      processorRef.current.onaudioprocess = null;
      processorRef.current.disconnect();
      processorRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(t => t.stop());
      mediaStreamRef.current = null;
    }

    if (retryRef.current) { clearTimeout(retryRef.current); retryRef.current = null; }
    authedRef.current = null;
    setReconnecting(false);
    if (wsRef.current) {
      const ws = wsRef.current;
      wsRef.current = null;   // first: its onclose then knows this was meant
      ws.close();
    }

    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }

    analyserRef.current = null;
    setIsRecording(false);
    setAudioLevel(0);
    mutedRef.current = false;
    setMutedState(false);
  }, [stopCart]);

  stopRef.current = stopRecording;

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopRecording();
    };
  }, [stopRecording]);

  return { isRecording, startRecording, stopRecording, audioLevel, error, muted, setMuted, playCart, stopCart, cartPlaying, reconnecting };
}
