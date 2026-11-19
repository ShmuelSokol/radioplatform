import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TIME_SLOTS } from '../../api/spotSchedules';
import { fullness, getSlotRoom, weekday, type Fullness, type RoomCell } from '../../api/sponsorRoom';
import { useStations } from '../../hooks/useStations';
import { useMayChange } from '../../hooks/useMayChange';
import { rememberStation, stationToShow } from '../../lib/lastStation';
import Spinner from '../../components/Spinner';
import BreakSettings from '../../components/sponsors/BreakSettings';
import BookSponsorship, { FULLNESS_TEXT, RoomBar, type BookingStart } from '../../components/sponsors/BookSponsorship';

/**
 * The Sponsorship board: the coming week as a grid of three-hour slots against days,
 * like a traffic program's "avails" screen. Each box says how many sponsorships are
 * booked against the room in that slot (green, yellow, red; grey is off the air).
 * Tap a box for who is in it and to book into it. On a phone, one day as a list.
 * Data: GET /spot-schedules/room (services/sponsor_room.py).
 */

// Inline colours: the qnet skin turns white and pressed buttons grey, which would hide the meaning
const CELL: Record<Fullness, { background: string; color: string }> = {
  empty: { background: '#ffffff', color: '#000' },
  room: { background: '#cfe8c9', color: '#000' },
  nearly: { background: '#f3dc9a', color: '#000' },
  full: { background: '#f0a39c', color: '#000' },
  off: { background: '#8f8f8f', color: '#fff' },
};

function dayHead(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  if (date.getDay() === 6) return `Shabbos ${d}`;
  return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
}

function dayLong(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  if (date.getDay() === 6) return `Shabbos, ${date.toLocaleDateString(undefined, { month: 'long', day: 'numeric' })}`;
  return date.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
}

function cellText(c: RoomCell): string {
  if (c.off_air) return 'Off the air';
  if (c.room === 0 && c.booked === 0) return 'No room';
  const base = c.room > 0 ? `${c.booked} of ${c.room}` : `${c.booked}`;
  // plays the planner wanted here and had no place for
  return c.no_room > 0 ? `${base}, ${c.no_room} over` : base;
}

export default function SponsorshipBoard() {
  const mayBook = useMayChange('/admin/spot-schedule');
  const [params, setParams] = useSearchParams();
  const { data: stationData } = useStations();
  const stations = useMemo(() => stationData?.stations ?? [], [stationData]);
  const stationId = stationToShow(stations, params.get('station'));
  const [picked, setPicked] = useState<{ date: string; slot: string } | null>(null);
  const [dayIndex, setDayIndex] = useState(0);
  const [booking, setBooking] = useState<BookingStart | null>(null);
  const [showSettings, setShowSettings] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['slot-room', stationId],
    queryFn: () => getSlotRoom(stationId, 7),
    enabled: !!stationId,
  });
  const dates = useMemo(() => [...new Set((data?.cells ?? []).map((c) => c.date))].sort(), [data]);
  const cellAt = (date: string, slot: string) => data?.cells.find((c) => c.date === date && c.slot === slot);
  // Overnight shows only when anything could air there
  const slots = TIME_SLOTS.filter((s) => s.key !== 'overnight' || (data?.cells ?? []).some((c) => c.slot === 'overnight' && (c.room > 0 || c.booked > 0)));
  const chosen = picked ? cellAt(picked.date, picked.slot) : undefined;
  const slotLabel = (key: string) => TIME_SLOTS.find((s) => s.key === key)?.hours ?? key;
  const today = dates[Math.min(dayIndex, Math.max(0, dates.length - 1))];

  const pickStation = (id: string) => {
    rememberStation(id);
    const next = new URLSearchParams(params);
    next.set('station', id);
    setParams(next, { replace: true });
    setPicked(null);
  };

  const detail = chosen && (
    <div className="bg-white rounded-lg shadow p-3 space-y-2" data-testid="board-detail" aria-live="polite">
      <h2 className="font-semibold">
        {dayLong(chosen.date)}, {slotLabel(chosen.slot)}: {chosen.off_air ? 'off the air' : chosen.room > 0 ? `${chosen.booked} of ${chosen.room} places` : `${chosen.booked} booked`}
      </h2>
      {chosen.no_room > 0 && (
        <p className="text-sm text-red-700">{chosen.no_room} {chosen.no_room === 1 ? 'play has' : 'plays have'} no room here. Book fewer, or add sponsorship places to a block in this slot (Program Blocks).</p>
      )}
      {chosen.sponsors.length ? (
        <ul className="divide-y border bg-white text-sm">
          {chosen.sponsors.map((s) => (
            <li key={s.schedule_id} className="px-2 py-2 flex flex-wrap gap-x-3 gap-y-1">
              <Link className="text-blue-700 underline font-medium" to={`/admin/sponsors/${s.sponsor_id}?tab=contracts`}>{s.sponsor_name}</Link>
              <span>{s.contract || 'Unnamed contract'}</span>
              <span className="text-gray-700">{s.plays} {s.plays === 1 ? 'play' : 'plays'} here</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-gray-600">{chosen.off_air ? 'Shabbos or Yom Tov: nothing airs.' : 'Nothing booked here yet.'}</p>
      )}
      {mayBook && !chosen.off_air && (
        <button
          className="min-h-[44px] px-4 bg-blue-600 text-white rounded-lg"
          onClick={() => setBooking({ stationId, day: weekday(chosen.date), slot: chosen.slot })}
        >
          Book into this slot
        </button>
      )}
    </div>
  );

  return (
    <div className="w-full space-y-4">
      {booking && <BookSponsorship start={booking} onClose={() => setBooking(null)} />}
      <div className="flex flex-wrap justify-between items-start gap-3">
        <div className="min-w-0">
          <h1 className="text-3xl font-bold">Sponsorship board</h1>
          <p className="text-sm text-gray-600 mt-1">The next 7 days on one station. Each box shows how many sponsorships are booked against the room in that time slot. Tap a box to see who is in it.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {mayBook && <button className="px-4 py-2 bg-blue-600 text-white rounded-lg" onClick={() => setBooking({ stationId })}>Book a sponsorship</button>}
          <button className="px-3 py-2 border rounded-lg text-sm" aria-expanded={showSettings} onClick={() => setShowSettings(!showSettings)}>Break settings</button>
        </div>
      </div>

      <label className="flex flex-wrap items-center gap-2 text-sm">Station
        <select className="px-3 py-2 border rounded-lg min-w-0" value={stationId} onChange={(e) => pickStation(e.target.value)} aria-label="Station">
          {stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>

      {showSettings && stationId && <BreakSettings stationId={stationId} />}
      {data?.spots_off && <p className="text-sm bg-yellow-50 border border-yellow-300 rounded-lg p-2">Spots mode is off on this station in the control room, so no sponsorships are planned until it is switched on.</p>}
      {isLoading && <Spinner />}
      {isError && <p className="text-red-700">Could not load the board. Try again in a moment.</p>}

      {data && (
        <>
          {/* Desktop and tablet: the week as a grid */}
          <div className="hidden md:block overflow-x-auto" data-testid="board-grid">
            <table className="w-full text-sm border-separate" style={{ borderSpacing: 3 }}>
              <thead>
                <tr>
                  <th className="text-left px-2 py-1 w-28">Time slot</th>
                  {dates.map((d) => <th key={d} className="px-2 py-1">{dayHead(d)}</th>)}
                </tr>
              </thead>
              <tbody>
                {slots.map((s) => (
                  <tr key={s.key}>
                    <th className="text-left px-2 py-1 font-normal whitespace-nowrap">{s.hours.replace(' to ', ' - ')}</th>
                    {dates.map((d) => {
                      const c = cellAt(d, s.key);
                      if (!c) return <td key={d} />;
                      const state = fullness(c);
                      const on = picked?.date === d && picked.slot === s.key;
                      return (
                        <td key={d} className="p-0">
                          <button
                            type="button"
                            aria-current={on ? 'true' : undefined}
                            aria-label={`${dayLong(d)}, ${s.hours}: ${cellText(c)}${state === 'nearly' || state === 'full' ? `, ${FULLNESS_TEXT[state].toLowerCase()}` : ''}`}
                            data-cell={`${d}|${s.key}`}
                            onClick={() => setPicked(on ? null : { date: d, slot: s.key })}
                            style={{ ...CELL[state], border: on ? '3px solid #000080' : '1px solid #9a9a9a' }}
                            className="qn-plain w-full min-h-[40px] px-1 text-center"
                          >
                            {cellText(c)}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Phone: one day as a list of slots */}
          <div className="md:hidden space-y-2" data-testid="board-day">
            <div className="flex items-center justify-between gap-2">
              <button className="min-h-[44px] px-4 border rounded-lg" disabled={dayIndex === 0} onClick={() => { setDayIndex(dayIndex - 1); setPicked(null); }} aria-label="Day before">‹</button>
              <span className="font-semibold text-center">{today ? dayLong(today) : ''}</span>
              <button className="min-h-[44px] px-4 border rounded-lg" disabled={dayIndex >= dates.length - 1} onClick={() => { setDayIndex(dayIndex + 1); setPicked(null); }} aria-label="Next day">›</button>
            </div>
            {today && slots.map((s) => {
              const c = cellAt(today, s.key);
              if (!c) return null;
              const state = fullness(c);
              const on = picked?.date === today && picked.slot === s.key;
              return (
                <div key={s.key} className="space-y-2">
                  <button
                    type="button"
                    aria-current={on ? 'true' : undefined}
                    data-cell={`${today}|${s.key}`}
                    onClick={() => setPicked(on ? null : { date: today, slot: s.key })}
                    style={{ border: on ? '3px solid #000080' : undefined }}
                    className="w-full bg-white border rounded-lg p-3 text-left flex flex-col items-stretch gap-1"
                  >
                    <span className="flex w-full justify-between gap-2"><b className="whitespace-nowrap">{s.hours}</b><span className={state === 'full' ? 'text-red-700' : ''}>{cellText(c)}</span></span>
                    {!c.off_air && c.room > 0 && <RoomBar booked={c.booked + c.no_room} room={c.room} state={state} />}
                    {c.sponsors.length > 0 && (
                      <span className="block text-xs text-gray-700">
                        {c.sponsors.slice(0, 3).map((x) => x.sponsor_name).join(', ')}{c.sponsors.length > 3 ? ` and ${c.sponsors.length - 3} more` : ''}
                      </span>
                    )}
                  </button>
                  {on && detail}
                </div>
              );
            })}
          </div>

          <div className="flex flex-wrap gap-3 text-xs" aria-label="What the colours mean">
            {(['empty', 'room', 'nearly', 'full', 'off'] as Fullness[]).map((f) => (
              <span key={f} className="flex items-center gap-1">
                <span className="inline-block w-4 h-4 border border-gray-500" style={CELL[f]} />
                {f === 'off' ? 'Off the air or no room' : f === 'full' ? 'Full or over' : FULLNESS_TEXT[f]}
              </span>
            ))}
          </div>
          {data.mode === 'clock' && (
            <p className="text-xs text-gray-600">This station has no program blocks with sponsorship places, so the room is its clock slots (:15, :30, :45), one sponsorship each.</p>
          )}
          <div className="hidden md:block">{detail}</div>
        </>
      )}
    </div>
  );
}
