import { useState, useRef, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useUpdateAsset } from '../hooks/useAssets';
import { listSponsors } from '../api/sponsors';

interface Props {
  assetId: string;
  sponsorId: string | null;
  sponsorName: string | null;
}

export default function AssetSponsorBadge({ assetId, sponsorId, sponsorName }: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const updateAsset = useUpdateAsset();

  const { data: sponsors } = useQuery({
    queryKey: ['sponsors'],
    queryFn: listSponsors,
    enabled: open,
  });

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const filtered = useMemo(() => {
    if (!sponsors) return [];
    if (!search) return sponsors;
    const q = search.toLowerCase();
    return sponsors.filter((s) => s.name.toLowerCase().includes(q));
  }, [sponsors, search]);

  const handleSelect = (id: string | null) => {
    updateAsset.mutate({ id: assetId, data: { sponsor_id: id } });
    setOpen(false);
    setSearch('');
  };

  return (
    <div ref={ref} className="relative inline-block">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className={`cursor-pointer inline-block text-xs px-1.5 py-0.5 rounded-full hover:ring-1 hover:ring-gray-300 ${
          sponsorId ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-500'
        }`}
        title={sponsorId ? 'Change sponsor' : 'Set sponsor'}
      >
        {sponsorName || 'Set sponsor'}
        {updateAsset.isPending && <span className="ml-1 animate-pulse">...</span>}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute z-20 mt-1 left-0 w-48 bg-white border border-gray-200 rounded shadow-lg py-1">
            <div className="px-2 pb-1">
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search sponsors..."
                className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400"
                autoFocus
                onClick={(e) => e.stopPropagation()}
              />
            </div>
            {filtered.map((s) => (
              <button
                key={s.id}
                onClick={(e) => { e.stopPropagation(); handleSelect(s.id); }}
                className={`block w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 ${
                  sponsorId === s.id ? 'font-bold' : ''
                }`}
              >
                {s.name}
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="px-3 py-2 text-xs text-gray-400">No sponsors found</div>
            )}
            {sponsorId && (
              <button
                onClick={(e) => { e.stopPropagation(); handleSelect(null); }}
                className="block w-full text-left px-3 py-1.5 text-sm text-gray-500 hover:bg-gray-100 border-t border-gray-100"
              >
                Clear sponsor
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
