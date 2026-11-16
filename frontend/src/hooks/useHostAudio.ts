import { useEffect, useRef, useState, useCallback } from 'react';
import { makeResampler, PcmPlayer } from '../lib/pcm';

interface UseHostAudioReturn {
  isRecording: boolean;
  startRecording: () => void;
  stopRecording: () => void;
  audioLevel: number;
  error: string | null;
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

      // Connect audio WebSocket
      const ws = connectAudioWS();
      if (!ws) return;

      ws.onopen = () => {
        // The server reads a text auth message first, then audio. The token used to go
        // in the address instead: the server dropped the socket at once (no host
        // audio ever arrived) and the address, token included, was written to the logs.
        ws.send(JSON.stringify({ type: 'auth', token: localStorage.getItem('access_token') || '' }));
        // About 85 ms of microphone per frame
        const processor = ctx.createScriptProcessor(4096, 1, 1);
        const resample = makeResampler(ctx.sampleRate);
        processor.onaudioprocess = (e) => {
          const pcm = resample(e.inputBuffer.getChannelData(0));
          if (ws.readyState === WebSocket.OPEN && pcm.length) ws.send(pcm.buffer);
        };
        source.connect(processor);
        processor.connect(ctx.destination); // runs only when connected; its output is silent
        processorRef.current = processor;
        setIsRecording(true);
      };

      // Callers on air, heard by the host (use headphones, or they go out again on the mic)
      const callers = new PcmPlayer(ctx);
      ws.onmessage = (e) => { if (e.data instanceof ArrayBuffer) callers.push(e.data); };

      ws.onerror = () => setError('Audio WebSocket connection failed');
      ws.onclose = () => {
        if (isRecording) {
          setError('Audio WebSocket disconnected');
        }
      };

      wsRef.current = ws;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to access microphone');
    }
  }, [showId, connectAudioWS, updateLevel, isRecording]);

  const stopRecording = useCallback(() => {
    if (processorRef.current) {
      processorRef.current.onaudioprocess = null;
      processorRef.current.disconnect();
      processorRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(t => t.stop());
      mediaStreamRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
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
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopRecording();
    };
  }, [stopRecording]);

  return { isRecording, startRecording, stopRecording, audioLevel, error };
}
