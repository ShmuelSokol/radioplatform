import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Finding,
  getScheduleLookAhead,
  settleOverlap,
} from "../../api/scheduleLookAhead";
import { useStations } from "../../hooks/useStations";
import Spinner from "../../components/Spinner";

const DAY = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  month: "short",
  day: "numeric",
});

function clock(local: string) {
  // "YYYY-MM-DD HH:MM" in station time
  const [h, m] = local.slice(11).split(":").map(Number);
  const suffix = h >= 12 ? "PM" : "AM";
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${suffix}`;
}

function dayLabel(local: string) {
  return DAY.format(new Date(`${local.slice(0, 10)}T12:00:00`));
}

/** True when a finding ends on a later day (ending at midnight the next day does not count). */
function spansDays(f: Finding) {
  const startDay = f.local_start.slice(0, 10);
  const endDay = f.local_end.slice(0, 10);
  if (startDay === endDay) return false;
  const next = new Date(`${startDay}T12:00:00`);
  next.setDate(next.getDate() + 1);
  const nextDay = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
  return !(endDay === nextDay && f.local_end.slice(11) === "00:00");
}

/** The same overlap repeats every day it applies; show it once with its days. */
function group(findings: Finding[]) {
  const groups = new Map<string, { first: Finding; days: string[] }>();
  for (const f of findings) {
    // A span over several days is its own line; it does not repeat daily.
    const key = [
      f.kind,
      f.title,
      spansDays(f) ? f.local_start : f.local_start.slice(11),
      spansDays(f) ? f.local_end : f.local_end.slice(11),
    ].join("|");
    const g = groups.get(key);
    const day = dayLabel(f.local_start);
    if (g) {
      if (!g.days.includes(day)) g.days.push(day);
    } else {
      groups.set(key, { first: f, days: [day] });
    }
  }
  return [...groups.values()];
}

export default function ScheduleCheck() {
  const queryClient = useQueryClient();
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const [stationId, setStationId] = useState("");
  const activeStation = stationId || stations[0]?.id || "";

  const { data, isLoading, isError } = useQuery({
    queryKey: ["schedule-look-ahead", activeStation],
    queryFn: () => getScheduleLookAhead(activeStation, 7),
    enabled: !!activeStation,
  });

  const settle = useMutation({
    mutationFn: ({ winner, others }: { winner: string; others: string[] }) =>
      settleOverlap(winner, others),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["schedule-look-ahead"] }),
  });

  const { attention, resolved, offAir, gaps, plays } = useMemo(() => {
    const all = data?.findings ?? [];
    return {
      attention: group(all.filter((f) => f.needs_attention)),
      resolved: group(
        all.filter((f) => f.kind === "overlap" && !f.needs_attention),
      ),
      offAir: all.filter((f) => f.kind === "off_air"),
      gaps: group(all.filter((f) => f.kind === "gap")),
      plays: group(all.filter((f) => f.kind === "plays")),
    };
  }, [data]);

  const when = (g: { first: Finding; days: string[] }) =>
    spansDays(g.first)
      ? `${dayLabel(g.first.local_start)} ${clock(g.first.local_start)} to ${dayLabel(
          g.first.local_end,
        )} ${clock(g.first.local_end)}`
      : `${clock(g.first.local_start)} to ${clock(g.first.local_end)}, ${
      g.days.length > 3
        ? `${g.days.length} days starting ${g.days[0]}`
        : g.days.join(", ")
    }`;

  return (
    <div className="w-full space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Schedule Check</h1>
          <p className="text-gray-600 text-sm">
            The next 7 days: where schedule blocks collide, what the station
            will do, and anything that needs you. Times are station time.
          </p>
        </div>
        <select
          value={activeStation}
          onChange={(e) => setStationId(e.target.value)}
          className="border rounded-lg px-3 py-2"
        >
          {stations.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <Spinner />}
      {isError && (
        <p className="text-red-700">Could not load the schedule check.</p>
      )}

      {data && (
        <>
          {data.spots_off && (
            <p className="rounded-lg p-4 border bg-amber-50 border-amber-200 text-amber-900 text-sm" data-testid="spots-off-note">
              Spots mode is off on this station (control room), so no sponsor spots are planned and none are checked here.
            </p>
          )}
          <div
            className={`rounded-lg p-4 border ${
              attention.length
                ? "bg-amber-50 border-amber-200 text-amber-900"
                : "bg-green-50 border-green-200 text-green-900"
            }`}
          >
            <p className="font-semibold">
              {attention.length
                ? `${attention.length} thing${attention.length === 1 ? "" : "s"} to look at`
                : "Nothing needs you"}
            </p>
            <p className="text-sm">
              {resolved.length} overlap{resolved.length === 1 ? "" : "s"}{" "}
              settled by priority.
            </p>
          </div>

          {!!attention.length && (
            <section className="space-y-3">
              <h2 className="font-semibold text-gray-900">Needs a look</h2>
              {attention.map((g, i) => (
                <div
                  key={i}
                  className="bg-white border border-amber-200 rounded-lg p-4"
                >
                  <p className="font-medium text-gray-900">{g.first.title}</p>
                  <p className="text-sm text-gray-600">{when(g)}</p>
                  <p className="text-sm text-gray-800 mt-2">{g.first.detail}</p>
                  {g.first.kind === "overlap" ? (
                    <div className="mt-3">
                      <p className="text-sm text-gray-600 mb-2">
                        Right now: {g.first.resolution} If that is wrong, choose
                        which one plays:
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {g.first.blocks.map((b) => (
                          <button
                            key={b.id}
                            type="button"
                            disabled={settle.isPending}
                            onClick={() =>
                              settle.mutate({
                                winner: b.id,
                                others: g.first.blocks
                                  .filter((o) => o.id !== b.id)
                                  .map((o) => o.id),
                              })
                            }
                            className={`px-3 py-2 rounded-lg text-sm border ${
                              b.wins
                                ? "bg-orange-600 text-white border-orange-600"
                                : "bg-white text-gray-800 border-gray-300 hover:bg-gray-50"
                            }`}
                          >
                            {b.wins
                              ? `Keep "${b.name}"`
                              : `Play "${b.name}" instead`}
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <p className="text-sm text-gray-600 mt-2">
                      {g.first.resolution}{" "}
                      <Link
                        to="/admin/spot-schedule"
                        className="text-orange-700 underline"
                      >
                        Open Spot Schedule
                      </Link>
                    </p>
                  )}
                </div>
              ))}
            </section>
          )}

          {!!resolved.length && (
            <section className="space-y-2">
              <h2 className="font-semibold text-gray-900">
                Settled automatically
              </h2>
              {resolved.map((g, i) => (
                <div key={i} className="bg-white border rounded-lg p-4">
                  <p className="font-medium text-gray-900">{g.first.title}</p>
                  <p className="text-sm text-gray-600">{when(g)}</p>
                  <p className="text-sm text-gray-800 mt-1">{g.first.detail}</p>
                </div>
              ))}
            </section>
          )}

          {!!offAir.length && (
            <section className="space-y-2">
              <h2 className="font-semibold text-gray-900">Off the air</h2>
              {offAir.map((f, i) => (
                <div
                  key={i}
                  className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm"
                >
                  <span className="font-medium">
                    {f.title.replace("Off the air: ", "")}
                  </span>
                  : {dayLabel(f.local_start)} {clock(f.local_start)} to{" "}
                  {dayLabel(f.local_end)} {clock(f.local_end)}
                </div>
              ))}
            </section>
          )}

          {!!plays.length && (
            <section className="space-y-2">
              <h2 className="font-semibold text-gray-900">What plays</h2>
              <p className="text-sm text-gray-600">
                The block on the air at each time. When you change a block
                or a playlist, the change takes over at its start time.
              </p>
              <ul className="bg-white border rounded-lg p-4 text-sm text-gray-800 space-y-1">
                {plays.map((g, i) => (
                  <li key={i}>
                    <span className="font-medium">{g.first.title.replace(/^"|"$/g, "")}</span>
                    : {when(g)}. <span className="text-gray-500">{g.first.detail}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {!!gaps.length && (
            <section className="space-y-2">
              <h2 className="font-semibold text-gray-900">Nothing scheduled</h2>
              <p className="text-sm text-gray-600">
                No block covers these times, so the station plays from the
                automatic queue.
              </p>
              <ul className="bg-white border rounded-lg p-4 text-sm text-gray-800 list-disc list-inside">
                {gaps.map((g, i) => (
                  <li key={i}>{when(g)}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
