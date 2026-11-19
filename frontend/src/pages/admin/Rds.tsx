import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { rememberStation, stationToShow } from '../../lib/lastStation';
import { validZone } from '../../lib/stationTime';
import { useStations } from '../../hooks/useStations';
import { useMayChange } from '../../hooks/useMayChange';
import { listSponsors } from '../../api/sponsors';
import {
  checkRdsText,
  createRdsMessage,
  deleteRdsMessage,
  getRdsSettings,
  listRdsMessages,
  listRdsPlays,
  previewRds,
  rdsFeedUrls,
  saveRdsSettings,
  updateRdsMessage,
  type RdsCheck,
  type RdsMessage,
  type RdsMessageInput,
  type RdsSlot,
} from '../../api/rds';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const KIND_LABEL: Record<RdsSlot['kind'], string> = {
  now_playing: 'Now playing',
  message: 'Rotation',
  sponsor_on_air: 'Sponsor on air',
  fallback: 'Station line',
};
const TEMPLATES: { label: string; kind: RdsMessageInput['kind']; text: string }[] = [
  { label: 'Slogan', kind: 'text', text: "Kol B'ramah 107.9 FM Lakewood - kbrlive.com" },
  { label: 'Time', kind: 'time', text: "The time is {time} on Kol B'ramah" },
  { label: 'Weather', kind: 'weather', text: 'Lakewood weather: {temp}, {weather}' },
  { label: 'Sponsor', kind: 'sponsor', text: "Kol B'ramah sponsor " },
];

const emptyForm = (stationId: string): RdsMessageInput & { id?: string } => ({
  station_id: stationId,
  name: '',
  kind: 'text',
  text: '',
  ps_text: '',
  sponsor_id: null,
  weight: 1,
  days_of_week: '0,1,2,3,4,5,6',
  start_time: null,
  end_time: null,
  start_date: null,
  end_date: null,
  is_active: true,
});

function errorText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg || '').join(' ');
  return 'Could not save.';
}

/** Times on this page are the station's clock, to the second. */
function localTime(iso: string, tz?: string) {
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit', ...(validZone(tz) ? { timeZone: tz } : {}) });
}
function localDayTime(iso: string, tz?: string) {
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit', ...(validZone(tz) ? { timeZone: tz } : {}) });
}

function Counter({ n, max }: { n: number; max: number }) {
  return <span className={`text-xs tabular-nums ${n > max ? 'text-red-600 font-semibold' : 'text-gray-500'}`}>{n}/{max}</span>;
}

function CheckNotes({ check }: { check?: RdsCheck | null }) {
  if (!check) return null;
  return (
    <div className="text-xs space-y-0.5 mt-1">
      {check.errors.map((e) => <div key={e} className="text-red-600">{e}</div>)}
      {check.warnings.map((w) => <div key={w} className="text-amber-700">{w}</div>)}
    </div>
  );
}

function RadioDisplay({ slot }: { slot?: RdsSlot }) {
  return (
    <div className="rounded-lg bg-black text-green-300 font-mono p-3 min-w-0" aria-label="What radios show now">
      <div className="text-2xl tracking-[0.3em] whitespace-pre">{(slot?.ps ?? '').padEnd(8, ' ')}</div>
      <div className="text-sm mt-1 break-words" title={slot?.rt}>{slot?.rt ?? '…'}</div>
    </div>
  );
}

export default function Rds() {
  const qc = useQueryClient();
  const mayChange = useMayChange();
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const [chosen, setChosen] = useState('');
  const station = stationToShow(stations, chosen);
  const stationTz = stations.find((s) => s.id === station)?.timezone;

  const settingsQ = useQuery({ queryKey: ['rds-settings', station], queryFn: () => getRdsSettings(station), enabled: !!station });
  const messagesQ = useQuery({ queryKey: ['rds-messages', station], queryFn: () => listRdsMessages(station), enabled: !!station });
  const previewQ = useQuery({
    queryKey: ['rds-preview', station],
    queryFn: () => previewRds(station, 10),
    enabled: !!station,
    refetchInterval: 5000,
  });
  const playsQ = useQuery({ queryKey: ['rds-plays', station], queryFn: () => listRdsPlays(station, 7), enabled: !!station, refetchInterval: 30000 });
  const sponsorsQ = useQuery({ queryKey: ['sponsors'], queryFn: listSponsors, enabled: mayChange });

  // ── settings ──
  const [ps, setPs] = useState('');
  const [psMode, setPsMode] = useState<'static' | 'rotate'>('static');
  const [rotation, setRotation] = useState(15);
  const [npWeight, setNpWeight] = useState(2);
  const [spotTakeover, setSpotTakeover] = useState(true);
  const [fallback, setFallback] = useState('');
  const [settingsMsg, setSettingsMsg] = useState('');
  useEffect(() => {
    const s = settingsQ.data;
    if (!s) return;
    setPs(s.ps_text ?? '');
    setPsMode(s.ps_mode);
    setRotation(s.rotation_seconds);
    setNpWeight(s.now_playing_weight);
    setSpotTakeover(s.sponsor_while_spot_airs);
    setFallback(s.fallback_text ?? '');
    setSettingsMsg('');
  }, [settingsQ.data]);
  const saveSettings = useMutation({
    mutationFn: () =>
      saveRdsSettings(station, {
        ps_text: ps || null,
        ps_mode: psMode,
        rotation_seconds: rotation,
        now_playing_weight: npWeight,
        sponsor_while_spot_airs: spotTakeover,
        fallback_text: fallback || null,
      }),
    onSuccess: () => {
      setSettingsMsg('Saved.');
      qc.invalidateQueries({ queryKey: ['rds-settings', station] });
      qc.invalidateQueries({ queryKey: ['rds-preview', station] });
    },
    onError: (e) => setSettingsMsg(errorText(e)),
  });

  // ── message form ──
  const [form, setForm] = useState<(RdsMessageInput & { id?: string }) | null>(null);
  const [formError, setFormError] = useState('');
  const [check, setCheck] = useState<RdsCheck | null>(null);
  useEffect(() => {
    if (!form) { setCheck(null); return; }
    const t = setTimeout(() => {
      checkRdsText({ text: form.text, ps_text: form.ps_text || null, kind: form.kind, sponsor_id: form.sponsor_id })
        .then(setCheck)
        .catch(() => setCheck(null));
    }, 300);
    return () => clearTimeout(t);
  }, [form?.text, form?.ps_text, form?.kind, form?.sponsor_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ['rds-messages', station] });
    qc.invalidateQueries({ queryKey: ['rds-preview', station] });
  };
  const saveMessage = useMutation({
    mutationFn: async () => {
      if (!form) return;
      const { id, ...body } = form;
      const clean = { ...body, ps_text: body.ps_text || null, name: body.name || null };
      return id ? updateRdsMessage(id, clean) : createRdsMessage(clean);
    },
    onSuccess: () => { setForm(null); setFormError(''); refreshAll(); },
    onError: (e) => setFormError(errorText(e)),
  });
  const removeMessage = useMutation({ mutationFn: deleteRdsMessage, onSuccess: refreshAll });
  const toggleMessage = useMutation({
    mutationFn: (m: RdsMessage) => updateRdsMessage(m.id, { is_active: !m.is_active }),
    onSuccess: refreshAll,
  });

  const feeds = station ? rdsFeedUrls(station) : null;
  const [copied, setCopied] = useState('');
  const copy = (label: string, text: string) => {
    navigator.clipboard?.writeText(text).then(() => { setCopied(label); setTimeout(() => setCopied(''), 1500); }).catch(() => {});
  };

  const messages = messagesQ.data ?? [];
  const stationName = stations.find((s) => s.id === station)?.name ?? '';

  const edit = (m: RdsMessage) => {
    setFormError('');
    setForm({
      id: m.id, station_id: m.station_id, name: m.name ?? '', kind: m.kind, text: m.text, ps_text: m.ps_text ?? '',
      sponsor_id: m.sponsor_id, weight: m.weight, days_of_week: m.days_of_week, start_time: m.start_time,
      end_time: m.end_time, start_date: m.start_date, end_date: m.end_date, is_active: m.is_active,
    });
  };
  const days = new Set((form?.days_of_week ?? '').split(',').filter(Boolean));

  return (
    <div className="w-full space-y-6">
      <div className="flex flex-wrap justify-between items-start gap-4">
        <div className="min-w-0">
          <h1 className="text-3xl font-bold">RDS</h1>
          <p className="text-gray-600 mt-1">
            The text FM radios show: the station name (8 letters) and a rotating line of up to 64 characters.
          </p>
        </div>
        {stations.length > 1 && (
          <select
            aria-label="Station"
            value={station}
            onChange={(e) => { setChosen(e.target.value); rememberStation(e.target.value); setForm(null); }}
            className="px-3 py-2 border rounded-lg"
          >
            {stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
      </div>

      {/* On air now + coming */}
      <section className="bg-white rounded-lg shadow-md p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-2 min-w-0">
          <h2 className="text-lg font-semibold">On radios now</h2>
          <RadioDisplay slot={previewQ.data?.now} />
          {previewQ.data?.now && (
            <div className="text-xs text-gray-600">
              {KIND_LABEL[previewQ.data.now.kind]}
              {previewQ.data.now.sponsor_name ? ` · ${previewQ.data.now.sponsor_name}` : ''}
              {previewQ.data.now.changes_at ? ` · next line at ${localTime(previewQ.data.now.changes_at, stationTz)}` : ''}
            </div>
          )}
        </div>
        <div className="min-w-0">
          <h2 className="text-lg font-semibold mb-2">Next 10 minutes</h2>
          <div className="max-h-56 overflow-y-auto border rounded">
            <table className="w-full text-sm">
              <tbody>
                {(previewQ.data?.coming ?? []).map((r) => (
                  <tr key={r.at} className="border-b last:border-0">
                    <td className="px-2 py-1 whitespace-nowrap text-gray-500 tabular-nums">{localTime(r.at, stationTz)}</td>
                    <td className="px-2 py-1 font-mono break-words sm:truncate sm:max-w-0 w-full" title={r.rt}>{r.rt}</td>
                    <td className="px-2 py-1 whitespace-nowrap text-xs text-gray-500">{KIND_LABEL[r.kind]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Settings */}
      <section className="bg-white rounded-lg shadow-md p-4 space-y-4">
        <h2 className="text-lg font-semibold">Settings for {stationName}</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <label className="block text-sm">
            <span className="font-medium flex justify-between">Station name (PS) <Counter n={ps.length} max={8} /></span>
            <input
              value={ps}
              maxLength={8}
              disabled={!mayChange}
              onChange={(e) => setPs(e.target.value)}
              placeholder={settingsQ.data?.default_ps}
              className="w-full px-3 py-2 border rounded-lg font-mono uppercase"
            />
            <span className="text-xs text-gray-500">Stays fixed; this is how radios list the station.</span>
          </label>
          <label className="block text-sm">
            <span className="font-medium">Each line stays up for (seconds)</span>
            <input type="number" min={5} max={600} value={rotation} disabled={!mayChange}
              onChange={(e) => setRotation(Math.max(5, Math.min(600, Number(e.target.value) || 15)))}
              className="w-full px-3 py-2 border rounded-lg" />
          </label>
          <label className="block text-sm">
            <span className="font-medium">Song on air: turns per round</span>
            <input type="number" min={0} max={20} value={npWeight} disabled={!mayChange}
              onChange={(e) => setNpWeight(Math.max(0, Math.min(20, Number(e.target.value) || 0)))}
              className="w-full px-3 py-2 border rounded-lg" />
            <span className="text-xs text-gray-500">0 = never show the song. Hebrew titles are left out.</span>
          </label>
          <label className="block text-sm md:col-span-2">
            <span className="font-medium flex justify-between">Station line (when nothing else has text) <Counter n={fallback.length} max={64} /></span>
            <input value={fallback} maxLength={64} disabled={!mayChange} onChange={(e) => setFallback(e.target.value)}
              placeholder={settingsQ.data?.default_fallback} className="w-full px-3 py-2 border rounded-lg" />
          </label>
          <div className="space-y-2 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={spotTakeover} disabled={!mayChange} onChange={(e) => setSpotTakeover(e.target.checked)} />
              Show the sponsor's line while their spot airs
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={psMode === 'rotate'} disabled={!mayChange}
                onChange={(e) => setPsMode(e.target.checked ? 'rotate' : 'static')} />
              Let lines change the station name (not recommended)
            </label>
          </div>
        </div>
        {mayChange && (
          <div className="flex items-center gap-3">
            <button type="button" onClick={() => saveSettings.mutate()} disabled={saveSettings.isPending}
              className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700">Save settings</button>
            {settingsMsg && <span className="text-sm text-gray-600">{settingsMsg}</span>}
          </div>
        )}
      </section>

      {/* Rotation */}
      <section className="bg-white rounded-lg shadow-md p-4 space-y-3">
        <div className="flex flex-wrap justify-between items-center gap-2">
          <h2 className="text-lg font-semibold">Rotation lines</h2>
          {mayChange && !form && (
            <div className="flex flex-wrap gap-2">
              {TEMPLATES.map((t) => (
                <button key={t.label} type="button"
                  onClick={() => { setFormError(''); setForm({ ...emptyForm(station), kind: t.kind, text: t.text, name: t.label }); }}
                  className="border px-3 py-1.5 rounded-lg text-sm hover:bg-gray-50">+ {t.label}</button>
              ))}
            </div>
          )}
        </div>

        {form && (
          <form
            onSubmit={(e) => { e.preventDefault(); saveMessage.mutate(); }}
            className="border rounded-lg p-3 space-y-3 bg-gray-50"
          >
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <label className="block text-sm md:col-span-2">
                <span className="font-medium flex justify-between">Line <Counter n={check?.length ?? form.text.length} max={64} /></span>
                <input autoFocus value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })}
                  maxLength={200} className="w-full px-3 py-2 border rounded-lg font-mono" />
                <span className="text-xs text-gray-500">Fill-ins: {'{time} {date} {day} {temp} {weather} {station}'}</span>
                {check?.example && <div className="text-xs mt-1">Shows as: <span className="font-mono">{check.example}</span></div>}
                <CheckNotes check={check} />
              </label>
              <label className="block text-sm">
                <span className="font-medium">Name (for you)</span>
                <input value={form.name ?? ''} onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full px-3 py-2 border rounded-lg" />
              </label>
              <label className="block text-sm">
                <span className="font-medium">Kind</span>
                <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as RdsMessageInput['kind'] })}
                  className="w-full px-3 py-2 border rounded-lg">
                  <option value="text">Text</option>
                  <option value="sponsor">Sponsor</option>
                  <option value="time">Time</option>
                  <option value="weather">Weather</option>
                </select>
              </label>
              {form.kind === 'sponsor' && (
                <label className="block text-sm">
                  <span className="font-medium">Sponsor</span>
                  <select value={form.sponsor_id ?? ''} onChange={(e) => setForm({ ...form, sponsor_id: e.target.value || null })}
                    className="w-full px-3 py-2 border rounded-lg">
                    <option value="">Choose…</option>
                    {(sponsorsQ.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </label>
              )}
              <label className="block text-sm">
                <span className="font-medium">Turns per round</span>
                <input type="number" min={1} max={20} value={form.weight}
                  onChange={(e) => setForm({ ...form, weight: Math.max(1, Math.min(20, Number(e.target.value) || 1)) })}
                  className="w-full px-3 py-2 border rounded-lg" />
              </label>
              <label className="block text-sm">
                <span className="font-medium">Stations</span>
                <select value={form.station_id ?? ''} onChange={(e) => setForm({ ...form, station_id: e.target.value || null })}
                  className="w-full px-3 py-2 border rounded-lg">
                  <option value={station}>{stationName} only</option>
                  <option value="">Every station</option>
                </select>
              </label>
              <div className="text-sm md:col-span-3">
                <span className="font-medium block mb-1">Days</span>
                <div className="flex flex-wrap gap-1">
                  {DAYS.map((d, i) => (
                    <label key={d} className={`px-2 py-1 border rounded cursor-pointer ${days.has(String(i)) ? 'bg-blue-600 text-white font-semibold' : 'line-through text-gray-500'}`}>
                      <input type="checkbox" className="sr-only" checked={days.has(String(i))}
                        onChange={() => {
                          const next = new Set(days);
                          if (next.has(String(i))) next.delete(String(i)); else next.add(String(i));
                          setForm({ ...form, days_of_week: [...next].sort().join(',') });
                        }} />
                      {days.has(String(i)) ? `✓ ${d}` : d}
                    </label>
                  ))}
                </div>
              </div>
              <label className="block text-sm">
                <span className="font-medium">From (time of day)</span>
                <input type="time" value={form.start_time ?? ''} onChange={(e) => setForm({ ...form, start_time: e.target.value || null })}
                  className="w-full px-3 py-2 border rounded-lg" />
              </label>
              <label className="block text-sm">
                <span className="font-medium">Until (time of day)</span>
                <input type="time" value={form.end_time ?? ''} onChange={(e) => setForm({ ...form, end_time: e.target.value || null })}
                  className="w-full px-3 py-2 border rounded-lg" />
              </label>
              <label className="block text-sm">
                <span className="font-medium">Station name while showing (PS)</span>
                <input value={form.ps_text ?? ''} maxLength={8} onChange={(e) => setForm({ ...form, ps_text: e.target.value })}
                  disabled={psMode !== 'rotate'} placeholder={psMode === 'rotate' ? '' : 'Only when lines may change it'}
                  className="w-full px-3 py-2 border rounded-lg font-mono" />
              </label>
              <label className="block text-sm">
                <span className="font-medium">First day</span>
                <input type="date" value={form.start_date ?? ''} onChange={(e) => setForm({ ...form, start_date: e.target.value || null })}
                  className="w-full px-3 py-2 border rounded-lg" />
              </label>
              <label className="block text-sm">
                <span className="font-medium">Last day</span>
                <input type="date" value={form.end_date ?? ''} onChange={(e) => setForm({ ...form, end_date: e.target.value || null })}
                  className="w-full px-3 py-2 border rounded-lg" />
              </label>
            </div>
            {formError && <div className="text-sm text-red-600">{formError}</div>}
            <div className="flex gap-2">
              <button type="submit" disabled={saveMessage.isPending} className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700">
                {form.id ? 'Save line' : 'Add line'}
              </button>
              <button type="button" onClick={() => setForm(null)} className="border px-4 py-2 rounded-lg">Cancel</button>
            </div>
          </form>
        )}

        {messages.length === 0 ? (
          <p className="text-sm text-gray-500">No lines yet. Radios show the song on air and the station line.</p>
        ) : (
          <ul className="divide-y border rounded">
            {messages.map((m) => (
              <li key={m.id} className={`px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 ${m.is_active ? '' : 'opacity-50'}`}>
                <span className="font-mono text-sm break-words basis-full sm:basis-0 sm:truncate min-w-0 flex-1" title={m.check.example}>{m.text}</span>
                <span className="text-xs text-gray-500 whitespace-nowrap">
                  {m.kind === 'sponsor' ? `Sponsor: ${m.sponsor_name ?? '?'}` : m.name || m.kind}
                  {m.station_id ? '' : ' · every station'}
                  {m.weight > 1 ? ` · ${m.weight} turns` : ''}
                  {m.start_time || m.end_time ? ` · ${m.start_time ?? '00:00'}-${m.end_time ?? '24:00'}` : ''}
                  {m.days_of_week !== '0,1,2,3,4,5,6' ? ` · ${m.days_of_week.split(',').map((d) => DAYS[Number(d)]).join(' ')}` : ''}
                  {m.end_date ? ` · until ${m.end_date}` : ''}
                </span>
                {m.check.warnings.length > 0 && <span className="text-xs text-amber-700" title={m.check.warnings.join('\n')}>check wording</span>}
                {mayChange && (
                  <span className="flex gap-2 text-sm">
                    <button type="button" className="text-blue-600 hover:underline" onClick={() => edit(m)}>Edit</button>
                    <button type="button" className="text-gray-600 hover:underline" onClick={() => toggleMessage.mutate(m)}>
                      {m.is_active ? 'Pause' : 'Resume'}
                    </button>
                    <button type="button" className="text-red-600 hover:underline" onClick={() => removeMessage.mutate(m.id)}>Delete</button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Log */}
      <section className="bg-white rounded-lg shadow-md p-4 space-y-3">
        <h2 className="text-lg font-semibold">What the RDS encoder picked up (last 7 days)</h2>
        {(playsQ.data?.sponsor_totals ?? []).length > 0 && (
          <div className="flex flex-wrap gap-2">
            {playsQ.data!.sponsor_totals.map((t) => (
              <span key={t.sponsor_id} className="text-sm border rounded px-2 py-1">
                {t.sponsor_name}: {t.plays} times, {Math.round(t.seconds / 60)} min
              </span>
            ))}
          </div>
        )}
        {(playsQ.data?.plays ?? []).length === 0 ? (
          <p className="text-sm text-gray-500">Nothing yet. Lines are logged when the encoder reads the feed below.</p>
        ) : (
          <div className="max-h-72 overflow-y-auto border rounded">
            <table className="w-full text-sm">
              <thead><tr className="text-left"><th className="px-2 py-1">From</th><th className="px-2 py-1 hidden sm:table-cell">Until</th><th className="px-2 py-1">Line</th><th className="px-2 py-1">Kind</th></tr></thead>
              <tbody>
                {playsQ.data!.plays.map((p) => (
                  <tr key={p.id} className="border-t">
                    <td className="px-2 py-1 whitespace-nowrap tabular-nums">{localDayTime(p.first_seen_at, stationTz)}</td>
                    <td className="px-2 py-1 whitespace-nowrap tabular-nums hidden sm:table-cell">{localTime(p.last_seen_at, stationTz)}</td>
                    <td className="px-2 py-1 font-mono break-words sm:truncate sm:max-w-0 w-full" title={p.text}>{p.text}</td>
                    <td className="px-2 py-1 sm:whitespace-nowrap text-xs">{KIND_LABEL[p.kind]}{p.sponsor_name ? ` · ${p.sponsor_name}` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Feed */}
      {feeds && (
        <section className="bg-white rounded-lg shadow-md p-4 space-y-2">
          <h2 className="text-lg font-semibold">Feed for the RDS encoder (Magic RDS)</h2>
          <p className="text-sm text-gray-600">
            In Magic RDS: add a Text Source of type "Internet Website" with the RadioText address, Coding ANSI,
            Process "First line", Keep in cache {Math.min(rotation, 10)} seconds; send it to Radiotext. Keep the PS fixed
            at <span className="font-mono">{ps || settingsQ.data?.default_ps}</span> in the encoder.
          </p>
          {([['RadioText', feeds.radiotext], ['PS name', feeds.ps], ['Both (JSON)', feeds.json]] as const).map(([label, url]) => (
            <div key={label} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="w-28 font-medium">{label}</span>
              <code className="flex-1 min-w-0 truncate bg-gray-100 px-2 py-1 rounded" title={url}>{url}</code>
              <button type="button" onClick={() => copy(label, url)} className="border px-2 py-1 rounded">{copied === label ? 'Copied' : 'Copy'}</button>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
