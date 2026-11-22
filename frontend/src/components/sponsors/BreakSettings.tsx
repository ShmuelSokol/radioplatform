import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getTimingDefaults, setTimingDefaults, type TimingDefaults, type TimingMode } from '../../api/spotSchedules';
import { useMayChange } from '../../hooks/useMayChange';

/**
 * One station's break settings: whether what is due on the hour, on the half hour and
 * at :15/:45 plays on the second (the song fades) or after the song, and the half-hour
 * time check. A contract can still choose its own. On the Sponsorship board and on the
 * contracts page. Saves with PUT /spot-schedules/timing-defaults.
 */
export default function BreakSettings({ stationId }: { stationId: string }) {
  const queryClient = useQueryClient();
  const mayChange = useMayChange('/admin/spot-schedule');
  const { data: timing, isError } = useQuery({
    queryKey: ['spot-timing-defaults', stationId],
    queryFn: () => getTimingDefaults(stationId),
    enabled: !!stationId,
  });
  const save = useMutation({
    mutationFn: (d: TimingDefaults) => setTimingDefaults(stationId, d),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['spot-timing-defaults'] });
      queryClient.invalidateQueries({ queryKey: ['spot-plan'] });
      queryClient.invalidateQueries({ queryKey: ['slot-room'] });
    },
  });
  if (isError) return <p className="text-sm text-red-700" data-testid="break-settings">Could not load the break settings. Try again in a moment.</p>;
  if (!timing) return null;
  const busy = save.isPending || !mayChange;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm border rounded-lg p-3" id="break-settings" data-testid="break-settings">
      <span className="font-medium">Unless a contract says otherwise:</span>
      {([
        ['on_the_hour', 'On the hour, time, weather and sponsorships play'],
        ['half_hour', 'On the half hour they play'],
        ['other', 'At :15 and :45 sponsorships play'],
      ] as const).map(([key, label]) => (
        <label key={key} className="flex items-center gap-2">
          {label}
          <select
            value={timing[key]}
            disabled={busy}
            onChange={(e) => save.mutate({ ...timing, [key]: e.target.value as TimingMode })}
            className="px-2 py-1 border rounded-lg"
          >
            <option value="hard">on the second (song fades out)</option>
            <option value="soft">after the song finishes</option>
          </select>
        </label>
      ))}
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={timing.half_hour_time_check}
          disabled={busy}
          onChange={(e) => save.mutate({ ...timing, half_hour_time_check: e.target.checked })}
          data-testid="half-hour-time-check"
        />
        Time check on the half hour ("The time is 10:30") before the weather
      </label>
      <span className="flex flex-wrap items-center gap-2" data-testid="sponsor-quiet-hours">
        No sponsorships from
        <input
          type="time"
          aria-label="No sponsorships from"
          defaultValue={timing.sponsor_quiet_start ?? ''}
          key={`qs-${timing.sponsor_quiet_start}`}
          disabled={busy}
          onBlur={(e) => {
            const start = e.target.value;
            const end = timing.sponsor_quiet_end || (start ? '06:00' : '');
            if (start !== (timing.sponsor_quiet_start ?? '')) {
              save.mutate({ ...timing, sponsor_quiet_start: start, sponsor_quiet_end: start ? end : '' });
            }
          }}
          className="px-2 py-1 border rounded-lg"
        />
        to
        <input
          type="time"
          aria-label="No sponsorships until"
          defaultValue={timing.sponsor_quiet_end ?? ''}
          key={`qe-${timing.sponsor_quiet_end}`}
          disabled={busy || !timing.sponsor_quiet_start}
          onBlur={(e) => {
            const end = e.target.value;
            if (end && end !== (timing.sponsor_quiet_end ?? '')) {
              save.mutate({ ...timing, sponsor_quiet_end: end });
            }
          }}
          className="px-2 py-1 border rounded-lg"
        />
        <span className="text-xs text-gray-600">(clear the first time for none)</span>
      </span>
      <label className="flex items-center gap-2">
        Sponsorships stop
        <input
          type="number"
          min={0}
          max={180}
          defaultValue={timing.sponsors_stop_before_blackout_minutes ?? 0}
          key={`sb-${timing.sponsors_stop_before_blackout_minutes}`}
          disabled={busy}
          onBlur={(e) => {
            const minutes = Math.max(0, Math.min(180, Math.round(Number(e.target.value) || 0)));
            if (minutes !== (timing.sponsors_stop_before_blackout_minutes ?? 0)) {
              save.mutate({ ...timing, sponsors_stop_before_blackout_minutes: minutes });
            }
          }}
          data-testid="sponsors-stop-before"
          className="w-20 px-2 py-1 border rounded-lg"
        />
        minutes before Shabbos and Yom Tov
      </label>
      {save.isError && <p className="w-full text-red-700" role="alert">That change was not saved. Try again in a moment.</p>}
      {!mayChange && <p className="w-full text-xs text-gray-600">Only managers can change these.</p>}
    </div>
  );
}
