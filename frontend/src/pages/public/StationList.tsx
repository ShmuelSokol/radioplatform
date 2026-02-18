import { Link } from 'react-router-dom';
import { useStations } from '../../hooks/useStations';

export default function StationList() {
  const { data, isLoading, isError } = useStations();

  return (
    <div>
      <h1 className="text-3xl font-bold mb-8">Live Radio Stations</h1>

      {isLoading && (
        <div className="text-center py-10 text-gray-500">Loading stations...</div>
      )}

      {isError && (
        <div className="text-center py-10">
          <p className="text-gray-500 mb-2">Unable to connect to the server.</p>
          <p className="text-sm text-gray-400">The backend API is not yet configured.</p>
        </div>
      )}

      {!isLoading && !isError && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {data?.stations?.map((station) => (
            <Link
              key={station.id}
              to={`/listen/${station.id}`}
              className="bg-white shadow rounded-lg p-6 hover:shadow-lg transition group"
            >
              <div className="flex items-center gap-4">
                <div className="w-16 h-16 bg-blue-50 rounded-lg flex items-center justify-center text-3xl text-blue-600 group-hover:bg-blue-100 transition">
                  &#9835;
                </div>
                <div>
                  <h2 className="text-lg font-semibold group-hover:text-blue-600 transition">
                    {station.name}
                  </h2>
                  <p className="text-sm text-gray-500">{station.type} &middot; {station.timezone}</p>
                  {station.description && (
                    <p className="text-sm text-gray-400 mt-1 line-clamp-2">{station.description}</p>
                  )}
                </div>
              </div>
              <div className="mt-4">
                <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                  station.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'
                }`}>
                  {station.is_active ? 'On Air' : 'Offline'}
                </span>
              </div>
            </Link>
          ))}
          {data?.stations?.length === 0 && (
            <p className="text-gray-500 col-span-full text-center py-10">
              No stations available yet
            </p>
          )}
        </div>
      )}
    </div>
  );
}
