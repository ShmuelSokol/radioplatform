import { useEffect, useRef, useState } from 'react';
import { listAssets } from '../../api/assets';
import type { Asset } from '../../types';

/** "Match to a song": search the library and point a song request at the item picked.
 *  A request already approved is queued as soon as it is matched (server side). */
export default function MatchToSong({
  initial,
  busy,
  onPick,
}: {
  initial: string;
  busy?: boolean;
  onPick: (asset: Asset) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(initial);
  const [results, setResults] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const q = text.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    const mine = ++seq.current;
    setLoading(true);
    const t = window.setTimeout(() => {
      listAssets({ search: q, asset_type: 'music', limit: 8 })
        .then((r) => { if (mine === seq.current) setResults(r.assets ?? []); })
        .catch(() => { if (mine === seq.current) setResults([]); })
        .finally(() => { if (mine === seq.current) setLoading(false); });
    }, 200);
    return () => window.clearTimeout(t);
  }, [text, open]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={busy}
        className="text-cyan-400 hover:text-cyan-300 text-[11px] disabled:opacity-50 inline-flex items-center min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0"
        data-testid="match-to-song"
      >
        Match
      </button>
    );
  }

  return (
    <div className="relative" data-testid="match-box">
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}
          placeholder="Search the library"
          aria-label="Search the library for the requested song"
          className="w-44 max-w-[60vw] bg-[#0a0a28] border border-[#2a2a5e] rounded px-2 py-1 text-[16px] sm:text-[12px] text-white"
        />
        <button type="button" onClick={() => setOpen(false)} className="text-gray-400 text-[11px] min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0" aria-label="Close search">
          ✕
        </button>
      </div>
      <div className="absolute z-20 mt-1 w-72 max-w-[80vw] bg-[#12123a] border border-[#2a2a5e] rounded shadow-lg">
        {loading && results.length === 0 && <p className="px-2 py-1.5 text-gray-500 text-[11px]">Searching…</p>}
        {!loading && text.trim().length >= 2 && results.length === 0 && (
          <p className="px-2 py-1.5 text-gray-500 text-[11px]">No songs found</p>
        )}
        {results.map((a) => (
          <button
            key={a.id}
            type="button"
            onClick={() => { onPick(a); setOpen(false); }}
            className="block w-full text-left px-2 py-1.5 min-h-[44px] sm:min-h-0 text-[12px] hover:bg-[#1e1e50] border-b border-[#1a1a3e] last:border-0"
          >
            <span className="text-white">{a.title}</span>
            {a.artist && <span className="text-gray-400"> — {a.artist}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}
