import { useEffect, useRef, useState, useCallback } from 'react';
import { makeResampler, PcmPlayer } from '../lib/pcm';

/** What the meter shows. Levels are dB below full scale (0 = the loudest a recording can hold). */
export interface MicMeter {
  /** Average level now, about -60 (silence) to 0 */
  db: number;
  /** Loudest moment of the last second and a half */
  peakDb: number;
  /** Hit full scale in the last 2 s: the voice is distorting */
  clipping: boolean;
  /** Seconds since the microphone last gave any sound (0 while it does) */
  silentFor: number;
}

export type MicCheck = 'idle' | 'recording' | 'playing';
export const MIC_CHECK_SECONDS = 5;

interface UseHostAudioReturn {
  /** The microphone is open in this page (the meter moves). Nothing is sent yet. */
  micReady: boolean;
  /** Open the microphone, inside a click. Listeners hear nothing until goLive. */
  openMic: () => Promise<void>;
  /** Start sending to the station: this is the moment of going on air. */
  goLive: () => void;
  meter: MicMeter;
  /** Hear yourself in the headphones, as the station gets you (microphone and carts) */
  monitor: boolean;
  setMonitor: (on: boolean) => void;
  /** Record a few seconds and play them back; only before going live */
  micCheck: MicCheck;
  runMicCheck: () => void;
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
const RECONNECT_FOR_MS = 60000;   // then the host is told and must press GO LIVE

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
  const [micReady, setMicReady] = useState(false);
  const [meter, setMeter] = useState<MicMeter>({ db: -60, peakDb: -60, clipping: false, silentFor: 0 });
  const meterRef = useRef({ peak: 0, peakAt: 0, clipAt: 0, soundAt: 0, shownAt: 0 });
  const [monitor, setMonitorState] = useState(() => {
    try { return localStorage.getItem('kbr_host_monitor') === '1'; } catch { return false; }
  });
  const monitorRef = useRef(monitor);
  const monitorGainRef = useRef<GainNode | null>(null);
  const [micCheck, setMicCheck] = useState<MicCheck>('idle');
  const checkRef = useRef<{ chunks: Float32Array[]; length: number } | null>(null);
  const openRef = useRef<(() => void) | null>(null);
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
    let top = 0;
    for (let i = 0; i < data.length; i++) top = Math.max(top, Math.abs(data[i] - 128) / 128);
    const m = meterRef.current;
    const now = performance.now();
    if (top >= m.peak || now - m.peakAt > 1500) { m.peak = top; m.peakAt = now; }
    if (top >= 0.98) m.clipAt = now;
    if (rms > 0.004 || !m.soundAt) m.soundAt = now;
    if (now - m.shownAt > 60) {   // about 16 times a second is plenty for the eye
      m.shownAt = now;
      const toDb = (v: number) => Math.max(-60, 20 * Math.log10(Math.max(v, 1e-6)));
      setMeter({
        db: toDb(rms), peakDb: toDb(m.peak),
        clipping: !!m.clipAt && now - m.clipAt < 2000,
        silentFor: Math.max(0, Math.floor((now - m.soundAt) / 1000)),
      });
    }
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

  const openMic = useCallback(async () => {
    if (!showId || audioContextRef.current) return;
    setError(null);

    // Made inside the click, before anything is awaited: Safari leaves an audio
    // context made later suspended, and then nothing is captured and nothing heard
    const ctx = new AudioContext();
    ctx.resume().catch(() => {});
    audioContextRef.current = ctx;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (audioContextRef.current !== ctx) { stream.getTracks().forEach(t => t.stop()); return; }
      mediaStreamRef.current = stream;
      meterRef.current = { peak: 0, peakAt: 0, clipAt: 0, soundAt: 0, shownAt: 0 };
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
        const check = checkRef.current;
        if (check) {   // the mic check keeps what the station would get
          const input = e.inputBuffer.getChannelData(0);
          check.chunks.push(new Float32Array(input));
          check.length += input.length;
        }
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
      // Hear yourself: what goes to the station, in the headphones
      const monitorGain = ctx.createGain();
      monitorGain.gain.value = monitorRef.current ? 1 : 0;
      bus.connect(monitorGain);
      monitorGain.connect(ctx.destination);
      monitorGainRef.current = monitorGain;
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
          if (wsRef.current !== ws) return;   // the host left the air, or a newer socket
          // The server restarted or the network dropped: keep the microphone and try
          // again for a while, so the host is back on air without pressing anything
          // (a deploy during a show left 11 s of silence until End, 2026-11-18).
          // Not when the server said why: 4000 the station went back to music after
          // no sound came, 4001/4003 not signed in, 4004 no such show.
          // 4010: another show is live on this station
          const told = [4000, 4001, 4003, 4004, 4010].includes(e.code);
          if (!lostAt) lostAt = Date.now();
          if (everOpen && !told && Date.now() - lostAt < RECONNECT_FOR_MS) {
            setReconnecting(true);
            retryRef.current = setTimeout(open, RECONNECT_EVERY_MS);
            return;
          }
          stopRef.current();
          setError(e.code === 4010
            ? 'Another show is live on this station. You are not on air. End that show first, then press GO LIVE.'
            : everOpen
            ? 'The connection to the station was lost. Press GO LIVE to go back on air.'
            : 'Audio WebSocket connection failed');
        };
      };
      openRef.current = open;
      setMicReady(true);
    } catch (err) {
      if (audioContextRef.current === ctx) audioContextRef.current = null;
      ctx.close().catch(() => {});
      const name = (err as { name?: string })?.name;
      setError(name === 'NotAllowedError'
        ? 'The browser did not allow the microphone. Allow it for this site (the icon by the address) and press again.'
        : name === 'NotFoundError' ? 'No microphone was found on this computer.'
        : err instanceof Error ? err.message : 'Failed to access microphone');
    }
  }, [showId, connectAudioWS, updateLevel]);

  const goLive = useCallback(() => {
    if (!openRef.current || wsRef.current) return;
    checkRef.current = null;
    setMicCheck('idle');
    openRef.current();
  }, []);

  /** The old one-press path: microphone and on air together */
  const startRecording = useCallback(async () => {
    await openMic();
    goLive();
  }, [openMic, goLive]);

  const setMonitor = useCallback((on: boolean) => {
    monitorRef.current = on;
    setMonitorState(on);
    try { localStorage.setItem('kbr_host_monitor', on ? '1' : '0'); } catch { /* private mode */ }
    if (monitorGainRef.current) monitorGainRef.current.gain.value = on ? 1 : 0;
  }, []);

  const runMicCheck = useCallback(() => {
    const ctx = audioContextRef.current;
    if (!ctx || wsRef.current || checkRef.current || micCheck !== 'idle') return;
    const mine = { chunks: [] as Float32Array[], length: 0 };
    checkRef.current = mine;
    setMicCheck('recording');
    setTimeout(() => {
      if (checkRef.current !== mine) return;          // went live or stopped meanwhile
      checkRef.current = null;
      if (audioContextRef.current !== ctx || !mine.length) { setMicCheck('idle'); return; }
      const audio = ctx.createBuffer(1, mine.length, ctx.sampleRate);
      const data = audio.getChannelData(0);
      let at = 0;
      for (const chunk of mine.chunks) { data.set(chunk, at); at += chunk.length; }
      const source = ctx.createBufferSource();
      source.buffer = audio;
      source.connect(ctx.destination);
      source.onended = () => { source.disconnect(); setMicCheck('idle'); };
      setMicCheck('playing');
      source.start();
    }, MIC_CHECK_SECONDS * 1000);
  }, [micCheck]);

  const stopRef = useRef<() => void>(() => {});
  const stopRecording = useCallback(() => {
    stopCart();
    micGainRef.current = null;
    busRef.current = null;
    monitorGainRef.current = null;
    openRef.current = null;
    checkRef.current = null;
    setMicCheck('idle');
    setMicReady(false);
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

  return {
    isRecording, startRecording, stopRecording, audioLevel, error, muted, setMuted, playCart, stopCart, cartPlaying, reconnecting,
    micReady, openMic, goLive, meter, monitor, setMonitor, micCheck, runMicCheck,
  };
}
