import { useCallback, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getClientAccount } from '../../api/sponsors';
import { TIME_SLOTS, WIDE_DAYPARTS, updateSpotSchedule, type Daypart, type SpotScheduleInput, type TimingMode } from '../../api/spotSchedules';
import { useStations } from '../../hooks/useStations';
import { useEscape } from '../../hooks/useEscape';
import Spinner from '../Spinner';

/**
 * Edit a running (or past) contract: which spot, which station, which days,
 * when in the day it airs, how it honours its time, and its dates. Opened from
 * the client page's Contracts tab and from the Spot Schedule list. Saves with
 * PUT /spot-schedules/{id}, which re-plans the station's waiting plays.
 */

export interface EditableContract {
  id: string;
  sponsor_id?: string;
  asset_id: string;
  station_id: string | null;
  days_of_week: string;
  dayparts: string;
  plays_per_daypart: number;
  fixed_times: string | null;
  timing_mode: TimingMode | null;
  start_date: string | null;
  end_date: string | null;
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

function clock(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;
}

const chip = (on: boolean) =>
  `min-h-[40px] px-3 rounded-lg border text-sm ${on ? 'bg-blue-600 text-white border-blue-600' : 'bg-white'}`;
const field = 'w-full min-w-0 px-3 py-2 border rounded-lg';

export default function ContractEditor({ contract, sponsorId, onClose }: {
  contract: EditableContract;
  sponsorId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const close = useCallback(() => onClose(), [onClose]);
  useEscape(true, close);
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const { data: account } = useQuery({ queryKey: ['sponsor-account', sponsorId], queryFn: () => getClientAccount(sponsorId) });
  // The client's own audio: what can air (the current one stays in the list even if off the air)
  // Off-the-air copies (stand-ins, rejected audio) are not offered, except the one the contract uses now
  // (only the real voice airs on sponsorships, so a stand-in copy is never offered either)
  const spots = (account?.spots ?? []).filter(
    (s) => s.asset_id && (s.asset_id === contract.asset_id || (s.stage !== 'rejected' && !/stand-?in/i.test(s.title))),
  );

  const [assetId, setAssetId] = useState(contract.asset_id);
  const [stationId, setStationId] = useState(contract.station_id ?? '');
  const [days, setDays] = useState(contract.days_of_week.split(',').filter(Boolean).map(Number));
  const [mode, setMode] = useState<'spread' | 'exact'>(contract.fixed_times ? 'exact' : 'spread');
  const [dayparts, setDayparts] = useState(contract.dayparts.split(',').filter(Boolean) as Daypart[]);
  const [perPart, setPerPart] = useState(contract.plays_per_daypart);
  const [times, setTimes] = useState(contract.fixed_times ? contract.fixed_times.split(',') : []);
  const [newTime, setNewTime] = useState('');
  const [timing, setTiming] = useState<'' | TimingMode>(contract.timing_mode ?? '');
  const [start, setStart] = useState(contract.start_date ?? '');
  const [end, setEnd] = useState(contract.end_date ?? '');
  const [error, setError] = useState('');
  const [warnings, setWarnings] = useState<string[] | null>(null);

  const save = useMutation({
    mutationFn: (data: Partial<SpotScheduleInput>) => updateSpotSchedule(contract.id, data),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ['sponsor-account', sponsorId] });
      queryClient.invalidateQueries({ queryKey: ['sponsor-clients'] });
      queryClient.invalidateQueries({ queryKey: ['spot-schedules'] });
      queryClient.invalidateQueries({ queryKey: ['spot-plan'] });
      // Always say it saved; with nothing to warn about, close by itself shortly after
      setWarnings(saved.warnings ?? []);
      if (!saved.warnings?.length) window.setTimeout(onClose, 1500);
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? `Could not save: ${detail}` : 'Could not save. Check the fields and try again.');
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!days.length) return setError('Choose at least one day.');
    const allTimes = [...new Set([...times, ...(newTime ? [newTime] : [])])].sort();
    if (mode === 'exact' && !allTimes.length) return setError('Add at least one time.');
    if (mode === 'spread' && !dayparts.length) return setError('Choose at least one time slot.');
    if (start && end && start > end) return setError('The last day is before the first day.');
    const data: Partial<SpotScheduleInput> = {
      asset_id: assetId,
      days_of_week: [...days].sort().join(','),
      fixed_times: mode === 'exact' ? allTimes.join(',') : null,
      timing_mode: timing || null,
      start_date: start || null,
      end_date: end || null,
    };
    if (mode === 'spread') {
      data.dayparts = dayparts.join(',');
      data.plays_per_daypart = perPart;
    }
    // Only send the station when it changed: an every-station contract needs every station's rights
    if ((stationId || null) !== contract.station_id) data.station_id = stationId || null;
    save.mutate(data);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-2 sm:p-6" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <form
        onSubmit={submit}
        role="dialog"
        aria-label="Edit contract"
        data-testid="contract-editor"
        className="bg-white rounded-xl shadow-xl w-full max-w-2xl p-4 sm:p-6 space-y-4 my-auto"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-xl font-semibold">Edit contract</h2>
          <button type="button" onClick={onClose} className="min-h-[40px] px-3 border rounded-lg text-sm">Close</button>
        </div>

        {warnings ? (
          <div className="space-y-3">
            <p className="text-green-700 font-medium" role="status" data-testid="contract-saved">Saved. The coming plays were planned again.</p>
            {warnings.length > 0 && <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-3 text-sm">
              <p className="font-medium mb-1">Not everything fits in the coming days:</p>
              <ul className="list-disc ml-5">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>}
            <button type="button" onClick={onClose} className="min-h-[44px] px-4 bg-blue-600 text-white rounded-lg">OK</button>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="block text-sm">Spot
                <select className={field} value={assetId} onChange={(e) => setAssetId(e.target.value)} aria-label="Spot">
                  {!spots.some((s) => s.asset_id === assetId) && <option value={assetId}>Current spot</option>}
                  {spots.map((s) => <option key={s.key} value={s.asset_id!}>{s.title}{s.stage === 'rejected' ? ' (off the air)' : ''}</option>)}
                </select>
              </label>
              <label className="block text-sm">Station
                <select className={field} value={stationId} onChange={(e) => setStationId(e.target.value)} aria-label="Station">
                  <option value="">All stations</option>
                  {stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </label>
            </div>

            <fieldset>
              <legend className="text-sm font-medium mb-1">Days</legend>
              <div className="flex flex-wrap gap-2">
                {DAYS.map((d, i) => (
                  <button key={d} type="button" aria-pressed={days.includes(i)} className={chip(days.includes(i))} onClick={() => setDays(toggle(days, i))}>{d}</button>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="text-sm font-medium mb-1">When in the day</legend>
              <div className="flex flex-wrap gap-2 mb-2">
                <button type="button" aria-pressed={mode === 'spread'} className={chip(mode === 'spread')} onClick={() => setMode('spread')}>Spread over time slots</button>
                <button type="button" aria-pressed={mode === 'exact'} className={chip(mode === 'exact')} onClick={() => setMode('exact')}>At set times</button>
              </div>
              {mode === 'spread' ? (
                <div className="space-y-2">
                  <p className="text-xs text-gray-500">Plays go anywhere in the slot, to the break with the fewest sponsorships so far.</p>
                  <div className="flex flex-wrap gap-2" data-testid="editor-time-slots">
                    {/* A whole morning/afternoon/evening shows only on an older contract that has it */}
                    {[...TIME_SLOTS, ...WIDE_DAYPARTS.filter((w) => dayparts.includes(w.key))].map((p) => (
                      <button key={p.key} type="button" aria-pressed={dayparts.includes(p.key)} className={chip(dayparts.includes(p.key))} onClick={() => setDayparts(toggle(dayparts, p.key))}>
                        {p.label} <span className="text-xs opacity-75">{p.hours}</span>
                      </button>
                    ))}
                  </div>
                  <label className="flex items-center gap-2 text-sm">Plays in each slot
                    <input type="number" min={1} max={12} value={perPart} onChange={(e) => setPerPart(Math.max(1, Math.min(12, Number(e.target.value) || 1)))} className="w-20 px-2 py-1 border rounded-lg" />
                  </label>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="flex flex-wrap gap-2">
                    {times.map((t) => (
                      <span key={t} className="inline-flex items-center gap-1 px-2 py-1 border rounded-lg text-sm">
                        {clock(t)}
                        <button type="button" aria-label={`Remove ${clock(t)}`} className="px-1 text-red-600" onClick={() => setTimes(times.filter((x) => x !== t))}>×</button>
                      </span>
                    ))}
                    {!times.length && <span className="text-sm text-gray-500">No times yet.</span>}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <input type="time" value={newTime} onChange={(e) => setNewTime(e.target.value)} aria-label="Time to add" className="px-2 py-1 border rounded-lg" />
                    <button type="button" className="min-h-[40px] px-3 border rounded-lg text-sm" disabled={!newTime}
                      onClick={() => { if (newTime) { setTimes([...new Set([...times, newTime])].sort()); setNewTime(''); } }}>Add time</button>
                  </div>
                  <p className="text-xs text-gray-500">The station's own clock.</p>
                </div>
              )}
            </fieldset>

            <label className="block text-sm">How it keeps its time
              <select className={field} value={timing} onChange={(e) => setTiming(e.target.value as '' | TimingMode)} aria-label="How it keeps its time">
                <option value="">The station's usual choice for that slot</option>
                <option value="hard">On the second (the song fades out)</option>
                <option value="soft">After the song finishes</option>
              </select>
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">First day
                <input type="date" className={field} value={start} onChange={(e) => setStart(e.target.value)} />
              </label>
              <label className="block text-sm">Last day
                <input type="date" className={field} value={end} onChange={(e) => setEnd(e.target.value)} />
              </label>
            </div>
            <p className="text-xs text-gray-500 -mt-2">Leave a date empty for no limit.</p>

            {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
            <div className="flex flex-wrap gap-2">
              <button type="submit" disabled={save.isPending} className="min-h-[44px] px-4 bg-green-600 text-white rounded-lg disabled:opacity-50">
                {save.isPending ? <Spinner /> : 'Save changes'}
              </button>
              <button type="button" onClick={onClose} className="min-h-[44px] px-4 border rounded-lg">Cancel</button>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
