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
  const { data: timing } = useQuery({
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
    </div>
  );
}
