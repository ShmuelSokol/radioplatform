import { useEffect, useRef, useState, useCallback } from 'react';

interface UseHostAudioReturn {
  isRecording: boolean;
  startRecording: () => void;
  stopRecording: () => void;
  audioLevel: number;
  error: string | null;
}

/**
 * Hook for capturing host microphone audio and streaming it via WebSocket.
 * Uses MediaRecorder to capture audio as webm/opus chunks and sends binary frames.
 */
export function useHostAudio(showId: string | undefined): UseHostAudioReturn {
  const [isRecording, setIsRecording] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const mediaStreamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
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
    const token = localStorage.getItem('access_token') || '';
    let wsUrl: string;

    if (wsBase) {
      wsUrl = `${wsBase}/api/v1/ws/live/${showId}/audio?token=${token}`;
    } else if (apiUrl.startsWith('http')) {
      wsUrl = apiUrl.replace(/^http/, 'ws') + `/ws/live/${showId}/audio?token=${token}`;
    } else {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${protocol}//${window.location.host}${apiUrl}/ws/live/${showId}/audio?token=${token}`;
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
        // Start MediaRecorder
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
          ? 'audio/webm;codecs=opus'
          : 'audio/webm';
        const recorder = new MediaRecorder(stream, {
          mimeType,
          audioBitsPerSecond: 128000,
        });

        recorder.ondataavailable = (e) => {
          if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
            e.data.arrayBuffer().then(buf => ws.send(buf));
          }
        };

        recorder.start(250); // 250ms chunks
        mediaRecorderRef.current = recorder;
        setIsRecording(true);
      };

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
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    mediaRecorderRef.current = null;

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
