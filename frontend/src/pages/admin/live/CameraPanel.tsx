import { useEffect, useRef } from 'react';

type Camera = { on: boolean; error: string | null; stream: MediaStream | null; start: () => void; stop: () => void; supported: boolean };

/**
 * The host's camera: live video for people on the website, while the radio stays
 * audio only. The picture goes through the broadcast delay with the sound, so a
 * Dump cuts it too. Only while the microphone is on the show.
 */
export default function CameraPanel({ cam, hosting, delay }: { cam: Camera; hosting: boolean; delay: number }) {
  const preview = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const el = preview.current;
    if (!el) return;
    el.srcObject = cam.stream;
    if (cam.stream) el.play().catch(() => {});
  }, [cam.stream]);

  return (
    <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4" data-testid="camera-panel">
      <div className="text-[11px] text-gray-400 uppercase mb-2">Camera</div>
      {cam.stream && (
        <video ref={preview} muted playsInline className="w-full rounded bg-black mb-2" style={{ aspectRatio: '16 / 9' }}
          data-testid="camera-preview" aria-label="Your camera" />
      )}
      {cam.on ? (
        <>
          <div className="text-sm font-bold text-red-400 mb-2" role="status" data-testid="camera-on">
            Your camera is on the website{delay > 0.5 ? `, ${delay.toFixed(0)} s later with your voice` : ''}. The radio carries sound only.
          </div>
          <button onClick={cam.stop} data-testid="camera-stop"
            className="w-full bg-red-800 hover:bg-red-700 text-red-200 px-3 py-3 rounded text-sm font-bold transition">
            Stop camera
          </button>
          <div className="text-[11px] text-gray-500 mt-2">Dump cuts the picture with the sound.</div>
        </>
      ) : (
        <>
          <button onClick={cam.start} disabled={!hosting || !cam.supported} data-testid="camera-start"
            title={!hosting ? 'Go on air first: the video goes out with your microphone' : undefined}
            className="w-full bg-gray-700 hover:bg-gray-600 text-gray-200 px-3 py-3 rounded text-sm font-bold transition disabled:opacity-50">
            Start camera
          </button>
          <div className="text-[11px] text-gray-500 mt-2" data-testid="camera-help">
            {!cam.supported
              ? 'This browser cannot send video. Use a current Chrome, Edge or Safari.'
              : hosting
                ? 'People listening on the website can watch you. The radio carries sound only.'
                : 'Go on air first. People on the website can then watch you; the radio carries sound only.'}
          </div>
        </>
      )}
      {cam.error && <div className="text-sm text-red-400 mt-2" role="alert" data-testid="camera-error">{cam.error}</div>}
    </div>
  );
}
