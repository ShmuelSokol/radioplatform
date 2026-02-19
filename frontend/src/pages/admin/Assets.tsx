import { Link } from 'react-router-dom';
import { useAssets, useDeleteAsset } from '../../hooks/useAssets';
import { downloadAsset } from '../../api/assets';

function formatDuration(seconds: number | null): string {
  if (!seconds) return '—';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function Assets() {
  const { data, isLoading } = useAssets();
  const deleteMutation = useDeleteAsset();

  if (isLoading) return <div className="text-center py-10">Loading...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Assets</h1>
        <Link
          to="/admin/assets/upload"
          className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded transition"
        >
          Upload Asset
        </Link>
      </div>

      <div className="bg-white shadow rounded-lg overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Title</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Artist</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Album</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Duration</th>
              <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {data?.assets.map((asset) => (
              <tr key={asset.id}>
                <td className="px-6 py-4 whitespace-nowrap font-medium">{asset.title}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{asset.artist ?? '—'}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{asset.album ?? '—'}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{formatDuration(asset.duration)}</td>
                <td className="px-6 py-4 whitespace-nowrap text-right space-x-3">
                  <button
                    onClick={() => downloadAsset(asset.id, asset.title)}
                    className="text-blue-600 hover:text-blue-800 text-sm"
                  >
                    Download
                  </button>
                  <button
                    onClick={() => deleteMutation.mutate(asset.id)}
                    className="text-red-600 hover:text-red-800 text-sm"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {data?.assets.length === 0 && (
              <tr>
                <td colSpan={5} className="px-6 py-10 text-center text-gray-500">
                  No assets uploaded yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
