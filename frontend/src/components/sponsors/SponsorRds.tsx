import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createRdsMessage, deleteRdsMessage, listAllRdsMessages, updateRdsMessage, type RdsMessage } from '../../api/rds';
import { useStations } from '../../hooks/useStations';
import { useMayChange } from '../../hooks/useMayChange';
import Spinner from '../Spinner';

/**
 * A sponsor's radio text (RDS): the line FM radios show while their spot is on the air.
 * The lines are the RDS page's sponsor lines (RdsMessage with this sponsor_id), shown and
 * changed here from the sponsor's side. `useSponsorRds` gives "set / not set" to the
 * client list, the client page and the booking window.
 */

const MAX = 64; // RadioText holds 64 characters

/** Every sponsor's RDS lines, by sponsor id */
export function useSponsorRds() {
  const q = useQuery({ queryKey: ['rds-messages', 'all'], queryFn: listAllRdsMessages, staleTime: 30_000 });
  const bySponsor = new Map<string, RdsMessage[]>();
  for (const m of q.data ?? []) {
    if (!m.sponsor_id) continue;
    bySponsor.set(m.sponsor_id, [...(bySponsor.get(m.sponsor_id) ?? []), m]);
  }
  return {
    isLoading: q.isLoading,
    isError: q.isError,
    lines: (sponsorId: string) => bySponsor.get(sponsorId) ?? [],
    isSet: (sponsorId: string) => (bySponsor.get(sponsorId) ?? []).some((m) => m.is_active),
  };
}

/** "RDS set" / "RDS not set" */
export function RdsBadge({ set, className = '' }: { set: boolean; className?: string }) {
  return (
    <span
      data-testid="rds-badge"
      className={`inline-block text-xs px-2 py-0.5 rounded-full whitespace-nowrap ${set ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-700'} ${className}`}
    >
      {set ? 'RDS set' : 'RDS not set'}
    </span>
  );
}

function errorText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : 'That was not saved. Try again in a moment.';
}

export default function SponsorRds({ sponsorId, sponsorName }: { sponsorId: string; sponsorName: string }) {
  const queryClient = useQueryClient();
  const mayChange = useMayChange('/admin/rds');
  const rds = useSponsorRds();
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const lines = rds.lines(sponsorId);
  const stationName = (id: string | null) => (id ? stations.find((s) => String(s.id) === id)?.name ?? 'One station' : 'Every station');

  // The form: a new line, or the line being changed
  const [editing, setEditing] = useState<string | 'new' | null>(null);
  const [text, setText] = useState('');
  const [stationId, setStationId] = useState('');
  const [error, setError] = useState('');
  const open = (m?: RdsMessage) => {
    setEditing(m ? m.id : 'new');
    setText(m ? m.text : `Kol B'ramah sponsor ${sponsorName}`.slice(0, MAX));
    setStationId(m?.station_id ?? '');
    setError('');
  };
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['rds-messages'] });
    queryClient.invalidateQueries({ queryKey: ['rds-preview'] });
    setEditing(null);
    setError('');
  };
  const save = useMutation({
    mutationFn: () =>
      editing === 'new'
        ? createRdsMessage({
            station_id: stationId || null, name: sponsorName, kind: 'sponsor', text: text.trim(), ps_text: null,
            sponsor_id: sponsorId, weight: 1, days_of_week: '0,1,2,3,4,5,6', start_time: null, end_time: null,
            start_date: null, end_date: null, is_active: true,
          })
        : updateRdsMessage(editing!, { text: text.trim(), station_id: stationId || null }),
    onSuccess: done,
    onError: (e) => setError(errorText(e)),
  });
  const toggle = useMutation({
    mutationFn: (m: RdsMessage) => updateRdsMessage(m.id, { is_active: !m.is_active }),
    onSuccess: done,
    onError: (e) => setError(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (m: RdsMessage) => deleteRdsMessage(m.id),
    onSuccess: done,
    onError: (e) => setError(errorText(e)),
  });
  const busy = save.isPending || toggle.isPending || remove.isPending;

  if (rds.isLoading) return <Spinner />;
  if (rds.isError) return <p className="p-4 text-red-700">Could not load the radio text. Try again in a moment.</p>;

  return (
    <section className="bg-white rounded-lg shadow" data-testid="sponsor-rds">
      <div className="px-4 py-2 border-b flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold flex flex-wrap items-center gap-2">Radio text (RDS) <RdsBadge set={rds.isSet(sponsorId)} /></h2>
        <Link to="/admin/rds" className="text-sm text-blue-700 underline">Open the RDS page</Link>
      </div>
      <p className="px-4 pt-2 text-sm text-gray-700">
        The line a radio's display shows while this sponsor's spot is on the air, up to {MAX} letters. It also takes a turn between songs with the station's other lines.
        {!lines.length && ' Without one, the display shows the spot\'s title while it airs.'}
      </p>
      <p className="px-4 pt-1 text-xs text-gray-600">
        FM radios show it once the station's RDS program (Magic RDS) reads our feed; the RDS page shows what it picked up.
      </p>

      {lines.length > 0 && (
        <ul className="divide-y mt-2">
          {lines.map((m) => (
            <li key={m.id} className="px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-2" data-testid="rds-line">
              <span className="min-w-0 flex-1 basis-60">
                <span className="block font-mono break-words">{m.text}</span>
                <span className="block text-xs text-gray-600">
                  {stationName(m.station_id)} · {m.is_active ? 'On' : 'Switched off'}
                  {m.check && !m.check.ok ? ` · ${m.check.errors.join(' ')}` : ''}
                  {m.check?.ok && m.check.warnings.length ? ` · ${m.check.warnings.join(' ')}` : ''}
                </span>
              </span>
              {mayChange && (
                <span className="flex flex-wrap gap-2">
                  <button type="button" disabled={busy} className="min-h-[40px] px-3 border rounded-lg text-sm" onClick={() => open(m)}>Change</button>
                  <button type="button" disabled={busy} className="min-h-[40px] px-3 border rounded-lg text-sm" onClick={() => toggle.mutate(m)}>{m.is_active ? 'Switch off' : 'Switch on'}</button>
                  <button type="button" disabled={busy} className="min-h-[40px] px-3 border rounded-lg text-sm text-red-700"
                    onClick={() => { if (window.confirm(`Remove this radio text for ${sponsorName}?`)) remove.mutate(m); }}>Remove</button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {editing ? (
        <form className="p-4 space-y-3 border-t mt-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) save.mutate(); }} data-testid="rds-form">
          <label className="block text-sm">Text on the radio
            <input className="w-full min-w-0 px-3 py-2 border rounded-lg font-mono" value={text} maxLength={MAX} onChange={(e) => setText(e.target.value)} aria-label="Text on the radio" autoFocus />
          </label>
          <p className="text-xs text-gray-600">{text.length} of {MAX} letters. English letters and numbers only (radios cannot show Hebrew). Same wording rules as a sponsorship: no calls to action.</p>
          <label className="block text-sm">Station
            <select className="w-full min-w-0 px-3 py-2 border rounded-lg" value={stationId} onChange={(e) => setStationId(e.target.value)} aria-label="Station for this text">
              <option value="">Every station</option>
              {stations.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
            </select>
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={busy || !text.trim()} className="min-h-[44px] px-4 bg-green-600 text-white rounded-lg disabled:opacity-50">{save.isPending ? <Spinner /> : 'Save radio text'}</button>
            <button type="button" className="min-h-[44px] px-4 border rounded-lg" onClick={() => { setEditing(null); setError(''); }}>Cancel</button>
          </div>
        </form>
      ) : (
        mayChange && (
          <div className="p-4">
            <button type="button" className="min-h-[44px] px-4 bg-blue-600 text-white rounded-lg" onClick={() => open()} data-testid="rds-add">
              {lines.length ? 'Add another line' : 'Add radio text'}
            </button>
          </div>
        )
      )}
      {error && <p className="px-4 pb-3 text-sm text-red-700" role="alert">{error}</p>}
      {!mayChange && <p className="px-4 pb-3 text-xs text-gray-600">Only managers can change the radio text.</p>}
    </section>
  );
}
