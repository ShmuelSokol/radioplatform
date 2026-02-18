import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getStation, getStreamInfo } from '../../api/stations';
import { useNowPlaying } from '../../hooks/useNowPlaying';
import { usePlayerStore } from '../../stores/playerStore';

export default function Listen() {
  const { stationId } = useParams<{ stationId: string }>();
  const { data: station, isLoading } = useQuery({
    queryKey: ['station', stationId],
    queryFn: () => getStation(stationId!),
    enabled: !!stationId,
  });
  const { data: nowPlaying } = useNowPlaying(stationId ?? null);
  const { play, stationId: currentStationId, isPlaying, stop } = usePlayerStore();

  const isThisPlaying = currentStationId === stationId && isPlaying;

  const handlePlay = async () => {
    if (!stationId || !station) return;
    try {
      const streamInfo = await getStreamInfo(stationId);
      play(stationId, station.name, streamInfo.hls_url);
    } catch {
      // Stream not available
    }
  };

  if (isLoading) return <div className="text-center py-10">Loading...</div>;
  if (!station) return <div className="text-center py-10">Station not found</div>;

  return (
    <div className="max-w-2xl mx-auto">
      <div className="bg-white shadow rounded-lg p-8">
        <div className="flex items-center gap-6 mb-8">
          <div className="w-24 h-24 bg-brand-50 rounded-lg flex items-center justify-center text-5xl text-brand-600">
            &#9835;
          </div>
          <div>
            <h1 className="text-3xl font-bold">{station.name}</h1>
            <p className="text-gray-500">{station.type} &middot; {station.timezone}</p>
            {station.description && <p className="text-gray-400 mt-1">{station.description}</p>}
          </div>
        </div>

        <div className="flex items-center gap-4 mb-8">
          {isThisPlaying ? (
            <button
              onClick={stop}
              className="bg-red-600 hover:bg-red-700 text-white px-8 py-3 rounded-full text-lg font-medium transition"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={handlePlay}
              className="bg-brand-600 hover:bg-brand-700 text-white px-8 py-3 rounded-full text-lg font-medium transition"
            >
              Listen Live
            </button>
          )}
          <span className={`px-3 py-1 rounded-full text-sm font-medium ${
            station.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'
          }`}>
            {station.is_active ? 'On Air' : 'Offline'}
          </span>
        </div>

        {nowPlaying && (
          <div className="border-t pt-6">
            <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Now Playing</h3>
            {nowPlaying.now_playing ? (
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 bg-gray-100 rounded flex items-center justify-center text-2xl">
                  &#9835;
                </div>
                <div>
                  <p className="font-medium">{nowPlaying.now_playing.title}</p>
                  <p className="text-sm text-gray-500">{nowPlaying.state}</p>
                </div>
              </div>
            ) : (
              <p className="text-gray-400">Nothing playing right now</p>
            )}

            {nowPlaying.upcoming.length > 0 && (
              <div className="mt-6">
                <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Up Next</h3>
                <ul className="space-y-2">
                  {nowPlaying.upcoming.map((item, i) => (
                    <li key={i} className="flex items-center gap-3 text-sm text-gray-600">
                      <span className="text-gray-400">{i + 1}.</span>
                      {item.title}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
