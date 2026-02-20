import { useCallback, useEffect, useRef, useState } from 'react';

interface UseStudioRecorderReturn {
  isRecording: boolean;
  isPaused: boolean;
  duration: number;
  audioLevel: number;
  error: string | null;
  startRecording: () => Promise<void>;
  pauseRecording: () => void;
  resumeRecording: () => void;
  stopRecording: () => Promise<File | null>;
  cancelRecording: () => void;
}

/**
 * High-quality browser audio recording hook.
 * Captures 48kHz mono PCM audio and exports as 16-bit WAV — lossless broadcast quality.
 * Uses ScriptProcessorNode to accumulate raw Float32 samples.
 */
export function useStudioRecorder(): UseStudioRecorderReturn {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [duration, setDuration] = useState(0);
  const [audioLevel, setAudioLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const samplesRef = useRef<Float32Array[]>([]);
  const sampleRateRef = useRef(48000);
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef(0);
  const pausedDurationRef = useRef(0);
  const isPausedRef = useRef(false);

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

  const cleanup = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(t => t.stop());
      mediaStreamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    analyserRef.current = null;
    setIsRecording(false);
    setIsPaused(false);
    setAudioLevel(0);
    setDuration(0);
    isPausedRef.current = false;
    pausedDurationRef.current = 0;
  }, []);

  const startRecording = useCallback(async () => {
    setError(null);
    samplesRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 48000,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      mediaStreamRef.current = stream;

      const ctx = new AudioContext({ sampleRate: 48000 });
      audioContextRef.current = ctx;
      sampleRateRef.current = ctx.sampleRate;

      const source = ctx.createMediaStreamSource(stream);

      // Analyser for VU meter
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      // ScriptProcessor to capture raw PCM
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = (e) => {
        if (isPausedRef.current) return;
        const input = e.inputBuffer.getChannelData(0);
        samplesRef.current.push(new Float32Array(input));
      };
      source.connect(processor);
      processor.connect(ctx.destination);
      processorRef.current = processor;

      // Start VU meter
      rafRef.current = requestAnimationFrame(updateLevel);

      // Duration timer
      startTimeRef.current = Date.now();
      pausedDurationRef.current = 0;
      timerRef.current = setInterval(() => {
        if (!isPausedRef.current) {
          const elapsed = (Date.now() - startTimeRef.current - pausedDurationRef.current) / 1000;
          setDuration(elapsed);
        }
      }, 100);

      setIsRecording(true);
      setIsPaused(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to access microphone');
    }
  }, [updateLevel]);

  const pauseRecording = useCallback(() => {
    isPausedRef.current = true;
    setIsPaused(true);
  }, []);

  const resumeRecording = useCallback(() => {
    isPausedRef.current = false;
    setIsPaused(false);
  }, []);

  const stopRecording = useCallback(async (): Promise<File | null> => {
    if (!isRecording) return null;

    // Collect all samples
    const chunks = samplesRef.current;
    const sampleRate = sampleRateRef.current;

    // Cleanup streams/context
    cleanup();

    if (chunks.length === 0) return null;

    // Merge all Float32 chunks
    const totalLength = chunks.reduce((acc, c) => acc + c.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }

    // Encode as 16-bit PCM WAV
    const wavBuffer = encodeWAV(merged, sampleRate);
    const blob = new Blob([wavBuffer], { type: 'audio/wav' });
    const file = new File([blob], `recording-${Date.now()}.wav`, { type: 'audio/wav' });
    return file;
  }, [isRecording, cleanup]);

  const cancelRecording = useCallback(() => {
    samplesRef.current = [];
    cleanup();
  }, [cleanup]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cleanup();
    };
  }, [cleanup]);

  return {
    isRecording,
    isPaused,
    duration,
    audioLevel,
    error,
    startRecording,
    pauseRecording,
    resumeRecording,
    stopRecording,
    cancelRecording,
  };
}

/** Encode Float32 samples as 16-bit PCM WAV. */
function encodeWAV(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const numChannels = 1;
  const bitsPerSample = 16;
  const bytesPerSample = bitsPerSample / 8;
  const dataLength = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);

  // RIFF header
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataLength, true);
  writeString(view, 8, 'WAVE');

  // fmt chunk
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true); // chunk size
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * numChannels * bytesPerSample, true); // byte rate
  view.setUint16(32, numChannels * bytesPerSample, true); // block align
  view.setUint16(34, bitsPerSample, true);

  // data chunk
  writeString(view, 36, 'data');
  view.setUint32(40, dataLength, true);

  // Write PCM samples (clamp to [-1, 1] then scale to 16-bit)
  let offset2 = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    offset2 += 2;
  }

  return buffer;
}

function writeString(view: DataView, offset: number, str: string) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}
