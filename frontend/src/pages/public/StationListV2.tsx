import { Link } from 'react-router-dom';
import { useStations } from '../../hooks/useStations';

export default function StationListV2() {
  const { data, isLoading, isError } = useStations();

  return (
    <div className="py-4">
      <div className="mb-10 text-center">
        <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight bg-gradient-to-r from-violet-200 via-white to-fuchsia-200 bg-clip-text text-transparent">
          Kol Bramah Radio
        </h1>
        <p className="mt-3 text-gray-400 text-lg">Live Jewish music, Torah &amp; community — around the clock</p>
      </div>

      {isLoading && (
        <div className="text-center py-16 text-gray-500">Tuning in…</div>
      )}

      {isError && (
        <div className="v2-glass rounded-2xl text-center py-16">
          <p className="text-gray-300 mb-1">Unable to connect to the server.</p>
          <p className="text-sm text-gray-500">Please try again in a moment.</p>
        </div>
      )}

      {!isLoading && !isError && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {data?.stations?.map((station, i) => (
            <Link
              key={station.id}
              to={`/listen/${station.id}`}
              style={{ animationDelay: `${i * 70}ms` }}
              className="v2-glass v2-fade-up group rounded-2xl p-6 transition-all duration-300 hover:-translate-y-1 hover:shadow-[0_12px_40px_rgba(139,92,246,0.25)] hover:border-violet-400/40"
            >
              <div className="flex items-center gap-4">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-violet-600/40 to-fuchsia-600/30 flex items-center justify-center text-3xl text-violet-200 group-hover:scale-105 transition-transform duration-300">
                  &#9835;
                </div>
                <div className="min-w-0">
                  <h2 className="text-lg font-bold text-white truncate group-hover:text-violet-200 transition-colors">
                    {station.name}
                  </h2>
                  <p className="text-sm text-gray-400">{station.type} &middot; {station.timezone}</p>
                </div>
              </div>
              {station.description && (
                <p className="text-sm text-gray-400 mt-4 line-clamp-2">{station.description}</p>
              )}
              <div className="mt-5 flex items-center justify-between">
                {station.is_active ? (
                  <span className="flex items-center gap-2 text-xs font-semibold text-emerald-300">
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                    ON AIR
                  </span>
                ) : (
                  <span className="text-xs font-medium text-gray-500">Offline</span>
                )}
                <span className="text-xs font-semibold text-violet-300 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
                  Listen now &rarr;
                </span>
              </div>
            </Link>
          ))}
          {data?.stations?.length === 0 && (
            <p className="text-gray-500 col-span-full text-center py-16">
              No stations available yet
            </p>
          )}
        </div>
      )}
    </div>
  );
}
