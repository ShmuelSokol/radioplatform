import { useAuth } from '../../hooks/useAuth';
import { useStations } from '../../hooks/useStations';
import { useAssets } from '../../hooks/useAssets';

export default function Dashboard() {
  const { user } = useAuth();
  const { data: stationsData } = useStations();
  const { data: assetsData } = useAssets();

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="bg-white shadow rounded-lg p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase">Stations</h3>
          <p className="text-3xl font-bold mt-2">{stationsData?.total ?? 0}</p>
        </div>
        <div className="bg-white shadow rounded-lg p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase">Assets</h3>
          <p className="text-3xl font-bold mt-2">{assetsData?.total ?? 0}</p>
        </div>
        <div className="bg-white shadow rounded-lg p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase">Role</h3>
          <p className="text-3xl font-bold mt-2 capitalize">{user?.role ?? '—'}</p>
        </div>
      </div>
    </div>
  );
}
