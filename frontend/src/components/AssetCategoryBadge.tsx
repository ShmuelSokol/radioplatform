import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { useUpdateAsset } from '../hooks/useAssets';
import { useCategories } from '../hooks/useCategories';
import FloatingMenu from './FloatingMenu';

const PALETTE = [
  { color: 'bg-red-100 text-red-700', darkColor: 'text-red-300' },
  { color: 'bg-cyan-100 text-cyan-700', darkColor: 'text-cyan-300' },
  { color: 'bg-green-100 text-green-700', darkColor: 'text-green-300' },
  { color: 'bg-purple-100 text-purple-700', darkColor: 'text-purple-300' },
  { color: 'bg-yellow-100 text-yellow-700', darkColor: 'text-yellow-300' },
  { color: 'bg-blue-100 text-blue-700', darkColor: 'text-blue-300' },
  { color: 'bg-orange-100 text-orange-700', darkColor: 'text-orange-300' },
];

const DNP_STYLE = { color: 'bg-gray-800 text-red-400', darkColor: 'text-red-500' };

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
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeMenu = useCallback(() => setOpen(false), []);
  const updateAsset = useUpdateAsset();
  const { data: dynamicCategories } = useCategories();

  // Build styled categories from dynamic list
  const CATEGORIES = useMemo(() => {
    if (!dynamicCategories) return [];
    return dynamicCategories.map((cat, i) => {
      const style = cat.name === 'do_not_play' ? DNP_STYLE : PALETTE[i % PALETTE.length];
      return { value: cat.name, label: cat.name, ...style };
    });
  }, [dynamicCategories]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current && !ref.current.contains(t) && !menuRef.current?.contains(t)) setOpen(false);
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
    ? `cursor-pointer block max-w-full truncate ${current?.darkColor ?? 'text-gray-600'} hover:underline`
    : `cursor-pointer block max-w-full truncate text-xs px-1.5 py-0.5 rounded-full ${current?.color ?? 'bg-gray-100 text-gray-600'} hover:ring-1 hover:ring-gray-300`;

  return (
    <div ref={ref} className="relative inline-block max-w-full align-middle">
      <button
        ref={buttonRef}
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className={badgeClass}
        title={category ? `${label} (change category)` : 'Set category'}
      >
        {compact ? (category === 'do_not_play' ? 'DNP' : label) : label}
        {updateAsset.isPending && <span className="ml-1 animate-pulse">...</span>}
      </button>
      {open && (
        <FloatingMenu anchor={buttonRef} width={160} onClose={closeMenu} menuRef={menuRef}
          className={`rounded shadow-lg py-1 border ${dark ? 'bg-[#1a1a4e] border-[#3a3a7e]' : 'bg-white border-gray-200'}`}>
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
        </FloatingMenu>
      )}
    </div>
  );
}
