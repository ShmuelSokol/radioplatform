import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getEmergencyLog, getEmergencyStatus, sendEmergencyTest, stopEmergencyAlert,
  type EmergencyAlertRow,
} from '../../api/emergency';
import { useMayChange } from '../../hooks/useMayChange';
import Spinner from '../../components/Spinner';

const KIND: Record<string, string> = {
  alert: 'Alert', test: 'Override test', rmt: 'Monthly test (RMT)', rwt: 'Weekly test (RWT)',
};
const SOURCE: Record<string, string> = {
  device: 'EAS box (API)', line: 'EAS box (audio line)', staff: 'Staff',
};
const STATUS_STYLE: Record<string, string> = {
  on_air: 'bg-red-700 text-white', sent: 'bg-amber-800 text-amber-100', done: 'bg-green-900 text-green-200',
  failed: 'bg-red-950 text-red-300', cancelled: 'bg-gray-700 text-gray-200',
};
const STATUS_LABEL: Record<string, string> = {
  on_air: 'ON AIR', sent: 'Sent', done: 'Played', failed: 'Failed', cancelled: 'Stopped',
};

function when(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString([], {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit',
  });
}

export default function EmergencyAlerts() {
  const mayChange = useMayChange();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ['emergency-status'], queryFn: getEmergencyStatus, refetchInterval: 3000 });
  const log = useQuery({ queryKey: ['emergency-log'], queryFn: () => getEmergencyLog(0, 100), refetchInterval: 10000 });
  const stations = status.data?.stations ?? [];
  const onAir = stations.filter((s) => s.on_air);
  const rows = log.data?.alerts ?? [];

  const [testStation, setTestStation] = useState('');
  const [title, setTitle] = useState('Test of the emergency override');
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const chosen = useMemo(
    () => testStation || stations.find((s) => /jerusalem/i.test(s.name))?.station_id || '',
    [testStation, stations],
  );

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['emergency-status'] });
    qc.invalidateQueries({ queryKey: ['emergency-log'] });
  };
  const send = useMutation({
    mutationFn: () => sendEmergencyTest([chosen], file as File, title.trim()),
    onSuccess: (res) => {
      setMessage(res.failed
        ? { ok: false, text: 'The station did not take the test. See the log below.' }
        : { ok: true, text: 'Sent. It goes on air within a second or two.' });
      setFile(null);
      if (fileInput.current) fileInput.current.value = '';
      refresh();
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setMessage({ ok: false, text: detail || 'The test could not be sent.' });
    },
  });
  const stop = useMutation({ mutationFn: stopEmergencyAlert, onSuccess: refresh });

  const stationName = stations.find((s) => s.station_id === chosen)?.name ?? 'the station';

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] min-h-[calc(100vh-4rem)] text-white p-4 space-y-4">
      <h1 className="text-xl font-bold text-cyan-300">Emergency Alerts</h1>

      {onAir.map((s) => (
        <div key={s.station_id} data-testid="emergency-on-air"
          className="flex flex-wrap items-center justify-between gap-2 rounded border-2 border-red-500 bg-red-900/80 px-3 py-2">
          <div>
            <div className="text-[15px] font-bold text-white">EMERGENCY ALERT ON AIR · {s.name}</div>
            <div className="text-red-100">
              {KIND[s.on_air!.kind] ?? s.on_air!.kind} from {SOURCE[s.on_air!.source] ?? s.on_air!.source}
              {s.on_air!.title ? ` · ${s.on_air!.title}` : ''}. Programming carries on when it ends.
            </div>
          </div>
          {mayChange && s.on_air!.source !== 'line' && (
            <button onClick={() => { if (confirm(`Stop the alert on ${s.name} now?`)) stop.mutate(s.on_air!.alert_id); }}
              className="min-h-[44px] rounded bg-white px-4 font-bold text-red-800 hover:bg-red-100">
              Stop alert
            </button>
          )}
        </div>
      ))}

      <section className="rounded border border-[#2a2a60] bg-[#0d0d2b] p-3">
        <h2 className="mb-2 font-bold text-cyan-200">Stations</h2>
        {status.isLoading ? <Spinner /> : (
          <table className="w-full text-left">
            <thead className="text-gray-400">
              <tr><th className="py-1 pr-3">Station</th><th className="pr-3">Plays through</th><th>Now</th></tr>
            </thead>
            <tbody>
              {stations.map((s) => (
                <tr key={s.station_id} className="border-t border-[#1d1d48]">
                  <td className="py-1.5 pr-3">{s.name}</td>
                  <td className="pr-3 text-gray-300">
                    {s.box ? 'FM box (alert line works offline)' : s.player === 'engine' ? 'Stream server' : 'Browser player'}
                  </td>
                  <td>{s.on_air
                    ? <span className="rounded bg-red-700 px-2 py-0.5 font-bold">ALERT ON AIR</span>
                    : <span className="text-green-300">Ready</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {mayChange && (
        <section className="rounded border border-[#2a2a60] bg-[#0d0d2b] p-3">
          <h2 className="mb-1 font-bold text-cyan-200">Test the override</h2>
          <p className="mb-3 text-gray-300">
            Plays a clip above everything on one station, then programming carries on. Use a plain
            spoken clip, such as “This is a test of the Kol B'ramah emergency override.” A clip with
            EAS tones is refused: airing them outside a real alert breaks FCC rules (47 CFR 11.45).
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-gray-400">Station</span>
              <select value={chosen} onChange={(e) => setTestStation(e.target.value)}
                className="min-h-[44px] rounded border border-[#2a2a60] bg-[#080820] px-2 text-white">
                {stations.map((s) => <option key={s.station_id} value={s.station_id}>{s.name}</option>)}
              </select>
            </label>
            <label className="flex min-w-[14rem] flex-1 flex-col gap-1">
              <span className="text-gray-400">Name in the log</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={255}
                className="min-h-[44px] rounded border border-[#2a2a60] bg-[#080820] px-2 text-white" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-400">Spoken clip</span>
              <input ref={fileInput} type="file" accept="audio/*" aria-label="Spoken clip"
                onChange={(e) => { setFile(e.target.files?.[0] ?? null); setMessage(null); }}
                className="min-h-[44px] text-gray-200" />
            </label>
            <button disabled={!file || !chosen || send.isPending}
              onClick={() => { if (confirm(`Put this clip on air on ${stationName} now, above everything?`)) send.mutate(); }}
              className="min-h-[44px] rounded bg-red-700 px-4 font-bold text-white hover:bg-red-600 disabled:opacity-40">
              {send.isPending ? 'Sending…' : 'Send test now'}
            </button>
          </div>
          {message && (
            <p role="status" className={`mt-2 ${message.ok ? 'text-green-300' : 'text-red-300'}`}>{message.text}</p>
          )}
        </section>
      )}

      <section className="rounded border border-[#2a2a60] bg-[#0d0d2b] p-3">
        <h2 className="mb-1 font-bold text-cyan-200">The EAS box</h2>
        <p className="text-gray-300">
          Real alerts come from the station's FCC-certified EAS box. Two ways in, and both take
          the air above live shows and time checks:
        </p>
        <ul className="ml-5 mt-1 list-disc space-y-1 text-gray-300">
          <li>Its audio output into the FM box's alert input: any sound there goes on air at once, with or without internet.</li>
          <li>
            Over the internet: <code className="break-all text-cyan-200">POST https://api.kbrlive.com/api/v1/emergency/device/activate</code>{' '}
            with the alert's audio as <code>file</code> and the station's emergency key.{' '}
            {status.data?.device_api
              ? <span className="text-green-300">The key is set up.</span>
              : <span className="text-amber-300">No key is set up yet, so only staff can send.</span>}
          </li>
        </ul>
      </section>

      <section className="rounded border border-[#2a2a60] bg-[#0d0d2b] p-3">
        <h2 className="mb-2 font-bold text-cyan-200">Log</h2>
        {log.isLoading ? <Spinner /> : rows.length === 0 ? (
          <p className="text-gray-400">No alerts yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-left">
              <thead className="text-gray-400">
                <tr>
                  <th className="py-1 pr-3">Sent</th><th className="pr-3">Station</th><th className="pr-3">What</th>
                  <th className="pr-3">From</th><th className="pr-3">On air</th><th className="pr-3">Length</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a: EmergencyAlertRow) => (
                  <tr key={a.id} className="border-t border-[#1d1d48] align-top">
                    <td className="py-1.5 pr-3 whitespace-nowrap">{when(a.requested_at)}</td>
                    <td className="pr-3">{a.station_name ?? '—'}</td>
                    <td className="pr-3">
                      {KIND[a.kind] ?? a.kind}{a.title ? ` · ${a.title}` : ''}
                      {a.has_tones && <span className="ml-1 text-amber-300">(EAS tones)</span>}
                    </td>
                    <td className="pr-3">{SOURCE[a.source] ?? a.source}{a.source === 'staff' && a.requested_by ? ` (${a.requested_by})` : ''}</td>
                    <td className="pr-3 whitespace-nowrap">{when(a.started_at)}</td>
                    <td className="pr-3">{a.seconds_on_air != null ? `${a.seconds_on_air.toFixed(1)} s` : '—'}</td>
                    <td>
                      <span className={`rounded px-2 py-0.5 ${STATUS_STYLE[a.status] ?? 'bg-gray-700'}`}>{STATUS_LABEL[a.status] ?? a.status}</span>
                      {a.error && <div className="text-red-300">{a.error}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
