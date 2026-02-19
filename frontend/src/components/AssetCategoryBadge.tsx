import { useState, useRef, useEffect } from 'react';
import { useUpdateAsset } from '../hooks/useAssets';

const CATEGORIES = [
  { value: 'lively', label: 'Lively', color: 'bg-red-100 text-red-700', darkColor: 'text-red-300' },
  { value: 'med_fast', label: 'Medium', color: 'bg-cyan-100 text-cyan-700', darkColor: 'text-cyan-300' },
  { value: 'relax', label: 'Relax', color: 'bg-green-100 text-green-700', darkColor: 'text-green-300' },
  { value: 'do_not_play', label: 'Do Not Play', color: 'bg-gray-800 text-red-400', darkColor: 'text-red-500' },
];

interface Props {
  assetId: string;
  category: string | null;
  /** Use dark theme colors (for Dashboard dark UI) */
  dark?: boolean;
  /** Compact mode for tight table rows */
  compact?: boolean;
}

export default function AssetCategoryBadge({ assetId, category, dark, compact }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const updateAsset = useUpdateAsset();

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const current = CATEGORIES.find((c) => c.value === category);
  const label = current?.label ?? (category || '--');

  const handleSelect = (value: string) => {
    const newCat = value === '' ? null : value;
    updateAsset.mutate({ id: assetId, data: { category: newCat } });
    setOpen(false);
  };

  const badgeClass = dark
    ? `cursor-pointer ${current?.darkColor ?? 'text-gray-600'} hover:underline`
    : `cursor-pointer inline-block text-xs px-1.5 py-0.5 rounded-full ${current?.color ?? 'bg-gray-100 text-gray-600'} hover:ring-1 hover:ring-gray-300`;

  return (
    <div ref={ref} className="relative inline-block">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className={badgeClass}
        title="Change category"
      >
        {compact ? (category === 'do_not_play' ? 'DNP' : label) : label}
        {updateAsset.isPending && <span className="ml-1 animate-pulse">...</span>}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className={`absolute z-20 mt-1 ${dark ? 'right-0' : 'left-0'} w-36 rounded shadow-lg py-1 border ${dark ? 'bg-[#1a1a4e] border-[#3a3a7e]' : 'bg-white border-gray-200'}`}>
            {CATEGORIES.map((cat) => (
              <button
                key={cat.value}
                onClick={(e) => { e.stopPropagation(); handleSelect(cat.value); }}
                className={`block w-full text-left px-3 py-1.5 text-sm ${
                  dark ? 'text-gray-200 hover:bg-[#2a2a5e]' : 'text-gray-700 hover:bg-gray-100'
                } ${category === cat.value ? 'font-bold' : ''}`}
              >
                <span className={`inline-block w-2 h-2 rounded-full mr-2 ${cat.color.split(' ')[0]}`} />
                {cat.label}
              </button>
            ))}
            {category && (
              <button
                onClick={(e) => { e.stopPropagation(); handleSelect(''); }}
                className={`block w-full text-left px-3 py-1.5 text-sm border-t ${
                  dark ? 'text-gray-400 hover:bg-[#2a2a5e] border-[#3a3a7e]' : 'text-gray-500 hover:bg-gray-100 border-gray-100'
                }`}
              >
                Clear category
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
