import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getPlaylistGeneration,
  regeneratePlaylist,
  savePlaylistGeneration,
} from "../../api/playlistGeneration";

function length(seconds: number) {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Generate the playlist ahead of time from each schedule block's sequence of
 * song types, with the station-wide rules (artist distance, hours ahead).
 */
export default function PlaylistGenerationPanel({
  stationId,
}: {
  stationId: string;
}) {
  const queryClient = useQueryClient();
  const key = ["playlist-generation", stationId];
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: key,
    queryFn: () => getPlaylistGeneration(stationId),
    enabled: !!stationId,
  });

  const [enabled, setEnabled] = useState(false);
  const [hours, setHours] = useState(6);
  const [distance, setDistance] = useState(2);
  const [minVote, setMinVote] = useState(0);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!data) return;
    setEnabled(data.enabled);
    setHours(data.hours);
    setDistance(data.artist_distance);
    setMinVote(data.min_vote ?? 0);
  }, [data]);

  const save = useMutation({
    mutationFn: () =>
      savePlaylistGeneration({
        station_id: stationId,
        enabled,
        hours,
        artist_distance: distance,
        min_vote: minVote,
      }),
    onSuccess: () => {
      setMessage("Saved.");
      queryClient.invalidateQueries({ queryKey: key });
    },
    onError: () => setMessage("Could not save. Try again."),
  });

  const regenerate = useMutation({
    mutationFn: () => regeneratePlaylist(stationId),
    onSuccess: (result) => {
      setMessage(
        `Replaced ${result.removed} queued songs. The queue now reaches ${result.queued_hours} hours ahead.`,
      );
      queryClient.invalidateQueries({ queryKey: key });
    },
    onError: () => setMessage("Could not regenerate. Try again."),
  });

  if (!stationId) {
    return (
      <section className="bg-white border rounded-lg p-5 mb-6 text-sm text-gray-600">
        Choose a station above to set how its playlist is generated ahead of
        time.
      </section>
    );
  }

  const dirty =
    !!data &&
    (enabled !== data.enabled ||
      hours !== data.hours ||
      distance !== data.artist_distance ||
      minVote !== (data.min_vote ?? 0));

  return (
    <section className="bg-white border rounded-lg p-5 mb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Generate the playlist ahead</h2>
          <p className="text-sm text-gray-600 max-w-2xl">
            Each schedule block plays its template as a repeating sequence of
            song types. Songs that have rested longest are drawn first.
          </p>
        </div>
        {data && (
          <span className="text-sm text-gray-600">
            Queue reaches {data.queued_hours} hours ahead
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-4 mt-4">
        <label className="flex items-center gap-2 text-sm font-medium">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-4 w-4"
          />
          Generate from sequences
        </label>
        <label className="text-sm">
          <span className="block font-medium mb-1">Generate for (hours)</span>
          <input
            type="number"
            min={1}
            max={48}
            value={hours}
            onChange={(e) => setHours(Number(e.target.value) || 1)}
            className="w-24 px-3 py-2 border rounded-lg"
          />
        </label>
        <label className="text-sm">
          <span className="block font-medium mb-1">
            Artist distance (songs)
          </span>
          <input
            type="number"
            min={0}
            max={20}
            value={distance}
            onChange={(e) => setDistance(Number(e.target.value) || 0)}
            className="w-24 px-3 py-2 border rounded-lg"
          />
        </label>
        <label className="text-sm">
          <span className="block font-medium mb-1">Minimum vote</span>
          <input
            type="number"
            min={0}
            max={100}
            value={minVote}
            onChange={(e) => setMinVote(Math.max(0, Number(e.target.value) || 0))}
            title="Only songs with at least this vote are drawn. 0 plays everything. Set a song's vote on its page in the library."
            className="w-24 px-3 py-2 border rounded-lg"
          />
        </label>
        <button
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-40"
        >
          Save
        </button>
        {data?.enabled && (
          <button
            onClick={() => regenerate.mutate()}
            disabled={regenerate.isPending || dirty}
            title="Replaces the automatically queued songs that have not started. Keeps what people queued, requests, sponsor spots, time and weather."
            className="px-4 py-2 border rounded-lg hover:bg-gray-50 disabled:opacity-40"
          >
            {regenerate.isPending ? "Regenerating…" : "Regenerate now"}
          </button>
        )}
        {message && <span className="text-sm text-gray-600">{message}</span>}
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-700">Could not load the preview.</p>
      )}

      {data && data.notes.length > 0 && (
        <ul className="mt-4 text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-1">
          {data.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}

      <h3 className="mt-5 mb-2 text-sm font-semibold text-gray-700">
        {data?.enabled
          ? "What would be queued next"
          : "What it would play from now"}
        {isFetching && !isLoading ? " (updating…)" : ""}
      </h3>
      {isLoading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : data && data.preview.length > 0 ? (
        <div className="max-h-80 overflow-auto border rounded-lg">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600 sticky top-0">
              <tr>
                <th className="px-3 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Artist</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Block</th>
                <th className="px-3 py-2 font-medium text-right">Length</th>
              </tr>
            </thead>
            <tbody>
              {data.preview.map((item, i) => (
                <tr key={`${item.asset_id}-${i}`} className="border-t">
                  <td className="px-3 py-1.5 whitespace-nowrap tabular-nums">
                    {item.local_time}
                  </td>
                  <td className="px-3 py-1.5">{item.title}</td>
                  <td className="px-3 py-1.5">
                    {item.artist}
                    {item.artist_rule_relaxed && (
                      <span
                        className="ml-2 text-xs text-amber-700"
                        title="Every song of this type was by a recent artist, so the artist rule gave way."
                      >
                        artist repeats
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 whitespace-nowrap">
                    {item.category || item.asset_type}
                  </td>
                  <td className="px-3 py-1.5 whitespace-nowrap">
                    {item.block}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {length(item.duration)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-gray-500">
          Nothing to generate yet. Give a schedule block a template with a
          sequence.
        </p>
      )}
    </section>
  );
}
