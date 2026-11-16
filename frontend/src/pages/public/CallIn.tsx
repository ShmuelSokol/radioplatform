import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import apiClient from '../../api/client';
import { apiWsUrl, makeResampler, PcmPlayer } from '../../lib/pcm';

/**
 * Call the live show from the browser (no phone): the caller's microphone goes to
 * the server, the screener sees them in the queue, and when put on air their voice
 * is mixed with the host's for every listener. Meanwhile they hear the show here.
 * See wiki/live-shows.md.
 */
type Status = 'idle' | 'connecting' | 'waiting' | 'screening' | 'approved' | 'on_air' | 'ended' | 'rejected' | 'error';

interface ActiveShow {
  active: boolean;
  show: { id: string; title: string; calls_enabled: boolean } | null;
}

const STATUS_TEXT: Record<Status, string> = {
  idle: '',
  connecting: 'Connecting…',
  waiting: "You're in line. You can hear the show while you wait.",
  screening: "You're in line. You can hear the show while you wait.",
  approved: 'Approved. The host will bring you on shortly.',
  on_air: "You're on the air! Everyone listening can hear you.",
  ended: 'Your call has ended. Thank you for calling!',
  rejected: "The show can't take your call right now. Thank you for calling.",
  error: '',
};

export default function CallIn() {
  const { stationId } = useParams<{ stationId: string }>();
  const [active, setActive] = useState<ActiveShow | null>(null);
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const hangUpRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (!stationId) return;
    let stop = false;
    const load = () => apiClient.get<ActiveShow>(`/live-shows/station/${stationId}/active`)
      .then((r) => { if (!stop) setActive(r.data); }).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => { stop = true; clearInterval(t); };
  }, [stationId]);

  useEffect(() => () => hangUpRef.current(), []);

  const callIn = async () => {
    if (!stationId || !name.trim()) return;
    setError(null);
    setStatus('connecting');
    // Everything audio starts inside the click (Safari allows sound and the mic only then)
    const ctx = new AudioContext();
    ctx.resume().catch(() => {});
    let mic: MediaStream;
    try {
      mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      ctx.close().catch(() => {});
      setStatus('error');
      setError('Please allow the microphone to call in.');
      return;
    }
    let call: { call_id: string; token: string };
    try {
      call = (await apiClient.post(`/call-in/${stationId}`, { name: name.trim(), note: note.trim() || null })).data;
    } catch (e: unknown) {
      mic.getTracks().forEach((t) => t.stop());
      ctx.close().catch(() => {});
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setStatus('error');
      setError(detail || 'Could not reach the show. Please try again.');
      return;
    }

    const show = new PcmPlayer(ctx);
    const source = ctx.createMediaStreamSource(mic);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    const processor = ctx.createScriptProcessor(2048, 1, 1);
    const resample = makeResampler(ctx.sampleRate);
    source.connect(processor);
    processor.connect(ctx.destination); // its output is silent; it only runs when connected

    let ws: WebSocket | null = null;
    let ended = false;
    let attempts = 0;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const meter = setInterval(() => {
      const d = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(d);
      let sum = 0;
      for (const v of d) sum += ((v - 128) / 128) ** 2;
      setLevel(Math.min(1, Math.sqrt(sum / d.length) * 3));
    }, 100);

    const finish = (final: Status) => {
      if (ended) return;
      ended = true;
      if (retry) clearTimeout(retry);
      clearInterval(meter);
      processor.onaudioprocess = null;
      processor.disconnect();
      mic.getTracks().forEach((t) => t.stop());
      if (ws) { ws.onclose = null; ws.close(); }
      show.stop();
      ctx.close().catch(() => {});
      setLevel(0);
      setStatus(final);
    };
    hangUpRef.current = () => finish('ended');

    processor.onaudioprocess = (e) => {
      const pcm = resample(e.inputBuffer.getChannelData(0));
      if (ws?.readyState === WebSocket.OPEN && pcm.length) ws.send(pcm.buffer);
    };

    const connect = () => {
      if (ended) return;
      ws = new WebSocket(apiWsUrl(`/ws/live/call/${call.call_id}`));
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => {
        attempts = 0;
        ws!.send(JSON.stringify({ type: 'auth', token: call.token }));
      };
      ws.onmessage = (e) => {
        if (e.data instanceof ArrayBuffer) { show.push(e.data); return; }
        try {
          const msg = JSON.parse(e.data);
          if (msg.type !== 'call_status') return;
          if (msg.status === 'completed' || msg.status === 'abandoned') finish('ended');
          else if (msg.status === 'rejected') finish('rejected');
          else setStatus(msg.status as Status);
        } catch { /* not ours */ }
      };
      ws.onclose = (e) => {
        if (ended) return;
        if (e.code === 4001 || e.code === 4004) { finish('ended'); return; }
        // A dropped connection (a tunnel, a network change): try again, the call is held a little while
        if (attempts++ < 5) retry = setTimeout(connect, 1000 * attempts);
        else finish('ended');
      };
    };
    connect();
  };

  const inCall = ['connecting', 'waiting', 'screening', 'approved', 'on_air'].includes(status);
  const show = active?.show;

  return (
    <div className="max-w-xl mx-auto px-4">
      <div className="bg-white shadow rounded-lg p-6 sm:p-8">
        <h1 className="text-2xl font-bold mb-1">Call in to the show</h1>
        {!active ? (
          <p className="text-gray-500">Loading…</p>
        ) : !active.active || !show ? (
          <p className="text-gray-600">There is no live show on right now. Please try again when the show is on.</p>
        ) : !show.calls_enabled ? (
          <p className="text-gray-600"><b>{show.title}</b> is not taking calls right now.</p>
        ) : (
          <>
            <p className="text-gray-600 mb-5">Live now: <b>{show.title}</b></p>
            {!inCall && status !== 'ended' && status !== 'rejected' && (
              <div className="space-y-3">
                <label className="block">
                  <span className="text-sm text-gray-700">Your name</span>
                  <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100}
                    className="mt-1 w-full border rounded px-3 py-2" placeholder="Name" />
                </label>
                <label className="block">
                  <span className="text-sm text-gray-700">What would you like to talk about? (optional)</span>
                  <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300}
                    className="mt-1 w-full border rounded px-3 py-2" />
                </label>
                <p className="text-xs text-gray-500">Headphones work best: the show is heard on this page while you wait. Turn off any radio playing nearby.</p>
                <button onClick={callIn} disabled={!name.trim()}
                  className="w-full bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-6 py-3 rounded-full text-lg font-medium">
                  Call in
                </button>
                {error && <p className="text-sm text-red-600">{error}</p>}
              </div>
            )}
            {inCall && (
              <div className="space-y-4">
                <div className={`rounded-lg p-4 font-medium ${status === 'on_air' ? 'bg-red-50 text-red-800 border border-red-200' : 'bg-gray-50 text-gray-800'}`}>
                  {status === 'on_air' && <span className="inline-block w-3 h-3 bg-red-500 rounded-full animate-pulse mr-2 align-middle" />}
                  {STATUS_TEXT[status]}
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm text-gray-500 w-20">Your mic</span>
                  <div className="flex-1 h-2 bg-gray-200 rounded">
                    <div className="h-2 bg-green-500 rounded" style={{ width: `${Math.round(level * 100)}%` }} />
                  </div>
                </div>
                <button onClick={() => hangUpRef.current()}
                  className="w-full bg-red-600 hover:bg-red-700 text-white px-6 py-3 rounded-full text-lg font-medium">
                  Hang up
                </button>
              </div>
            )}
            {(status === 'ended' || status === 'rejected') && (
              <div className="space-y-3">
                <p className="text-gray-700">{STATUS_TEXT[status]}</p>
                <Link to={`/listen/${stationId}`} className="text-brand-600 underline">Back to listening</Link>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
