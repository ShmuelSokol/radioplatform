import { useState, useRef } from 'react';
import { useArchives } from '../../hooks/useArchives';
import { useStations } from '../../hooks/useStations';
import type { ShowArchive } from '../../api/archives';

const API_BASE = import.meta.env.VITE_API_URL || '/api/v1';

function formatDuration(seconds: number | null): string {
  if (!seconds) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export default function Archives() {
  const [stationFilter, setStationFilter] = useState<string>('');
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const { data: stationsData } = useStations();
  const { data, isLoading, isError } = useArchives(
    stationFilter ? { station_id: stationFilter } : undefined
  );

  const stations = stationsData?.stations ?? [];
  const archives = data?.archives ?? [];

  const handlePlay = (archive: ShowArchive) => {
    if (playingId === archive.id) {
      // Toggle pause/play
      if (audioRef.current) {
        if (audioRef.current.paused) {
          audioRef.current.play();
        } else {
          audioRef.current.pause();
        }
      }
      return;
    }
    // Play new track
    if (audioRef.current) {
      audioRef.current.pause();
    }
    const audio = new Audio(archive.audio_url);
    audio.play();
    audio.addEventListener('ended', () => setPlayingId(null));
    audioRef.current = audio;
    setPlayingId(archive.id);
  };

  const stationName = (id: string) => stations.find(s => s.id === id)?.name ?? 'Unknown';

  const rssUrl = stationFilter
    ? `${API_BASE}/archives/station/${stationFilter}/rss`
    : null;

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-3xl font-bold">Show Archives</h1>
          <p className="text-gray-500 mt-1">Browse and listen to past shows and recordings</p>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={stationFilter}
            onChange={e => setStationFilter(e.target.value)}
            className="bg-white border border-gray-300 text-gray-700 px-3 py-2 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          >
            <option value="">All Stations</option>
            {stations.map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          {rssUrl && (
            <a
              href={rssUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 bg-orange-100 text-orange-700 hover:bg-orange-200 px-3 py-2 rounded-lg text-sm font-medium transition"
              title="Subscribe to podcast RSS feed"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6.18 15.64a2.18 2.18 0 0 1 2.18 2.18C8.36 19 7.38 20 6.18 20C5 20 4 19 4 17.82a2.18 2.18 0 0 1 2.18-2.18M4 4.44A15.56 15.56 0 0 1 19.56 20h-2.83A12.73 12.73 0 0 0 4 7.27V4.44m0 5.66a9.9 9.9 0 0 1 9.9 9.9h-2.83A7.07 7.07 0 0 0 4 12.93V10.1Z" />
              </svg>
              RSS Feed
            </a>
          )}
        </div>
      </div>

      {isLoading && (
        <div className="text-center py-16 text-gray-500">Loading archives...</div>
      )}

      {isError && (
        <div className="text-center py-16">
          <p className="text-gray-500 mb-2">Unable to load archives.</p>
          <p className="text-sm text-gray-400">The server may be unavailable.</p>
        </div>
      )}

      {!isLoading && !isError && archives.length === 0 && (
        <div className="text-center py-16">
          <svg className="w-16 h-16 mx-auto mb-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
          <p className="text-gray-500 text-lg">No archived shows yet</p>
          <p className="text-gray-400 text-sm mt-1">Check back later for past show recordings</p>
        </div>
      )}

      {!isLoading && !isError && archives.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {archives.map(archive => (
            <div
              key={archive.id}
              className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden hover:shadow-md transition group"
            >
              {/* Cover image or placeholder */}
              <div className="relative h-40 bg-gradient-to-br from-indigo-500 to-purple-600 overflow-hidden">
                {archive.cover_image_url ? (
                  <img
                    src={archive.cover_image_url}
                    alt={archive.title}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center">
                    <svg className="w-16 h-16 text-white/30" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z" />
                    </svg>
                  </div>
                )}
                {/* Play button overlay */}
                <button
                  onClick={() => handlePlay(archive)}
                  className="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/30 transition"
                >
                  <span className="w-14 h-14 rounded-full bg-white/90 shadow-lg flex items-center justify-center opacity-0 group-hover:opacity-100 transform scale-75 group-hover:scale-100 transition-all">
                    {playingId === archive.id ? (
                      <svg className="w-6 h-6 text-indigo-600" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                      </svg>
                    ) : (
                      <svg className="w-6 h-6 text-indigo-600 ml-0.5" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M8 5v14l11-7z" />
                      </svg>
                    )}
                  </span>
                </button>
                {/* Duration badge */}
                {archive.duration_seconds && (
                  <span className="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-2 py-0.5 rounded">
                    {formatDuration(archive.duration_seconds)}
                  </span>
                )}
              </div>

              {/* Content */}
              <div className="p-4">
                <h3 className="font-semibold text-gray-900 line-clamp-2 leading-snug">
                  {archive.title}
                </h3>
                <div className="flex items-center gap-2 mt-2 text-sm text-gray-500">
                  {archive.host_name && (
                    <>
                      <span className="flex items-center gap-1">
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                        </svg>
                        {archive.host_name}
                      </span>
                      <span className="text-gray-300">|</span>
                    </>
                  )}
                  <span>{stationName(archive.station_id)}</span>
                </div>
                {archive.recorded_at && (
                  <p className="text-xs text-gray-400 mt-1">{formatDate(archive.recorded_at)}</p>
                )}
                {archive.description && (
                  <p className="text-sm text-gray-500 mt-2 line-clamp-2">{archive.description}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {data && data.total > archives.length && (
        <p className="text-center text-gray-400 text-sm mt-6">
          Showing {archives.length} of {data.total} archives
        </p>
      )}
    </div>
  );
}
