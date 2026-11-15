import { memo, useState } from 'react';
import { useAssets } from '../../../hooks/useAssets';
import { CART_PAGES, fmtDur, typeStyle } from './shared';
import type { CartSet, CtlAsset, FireMode, SendFn } from './shared';
import type { Pfl } from './pfl';

interface Props {
  pins: CtlAsset[];
  /** Named sets of ten pages; `pins` is the page on screen. */
  sets: CartSet[];
  setIndex: number;
  pageIndex: number;
  onSelectSet: (i: number) => void;
  onSelectPage: (i: number) => void;
  onAddSet: (name: string) => void;
  onRenameSet: (name: string) => void;
  onDeleteSet: () => void;
  onTogglePin: (a: CtlAsset) => void;
  onSend: SendFn;
  busyId: string | null;
  fireMode: FireMode;
  onFireMode: (m: FireMode) => void;
  inserting: boolean;
  onCancelInsert: () => void;
  disabled: boolean;
  onPreviewWeather: () => void;
  weatherState: 'idle' | 'loading' | 'playing';
  pfl: Pfl;
  /** Grid columns differ between the narrow desktop sidebar and the phone tab. */
  wide: boolean;
}

function Wall({ title, items, p, editing, pflOn }: { title: string; items: CtlAsset[]; p: Props; editing: boolean; pflOn: boolean }) {
  if (items.length === 0) return null;
  const how = p.inserting ? 'insert' : p.fireMode;
  return (
    <div>
      <div className="px-1 pb-1 text-[11px] font-bold uppercase tracking-wider text-gray-500">{title}</div>
      <div className={`grid gap-2 ${p.wide ? 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4' : 'grid-cols-2'}`}>
        {items.map(item => {
          const busy = p.busyId === item.id;
          const listening = p.pfl.playingId === item.id;
          return (
            <div key={item.id} className="relative">
              <button
                type="button"
                data-testid="hot-button"
                disabled={busy || editing || (p.disabled && !pflOn)}
                aria-pressed={pflOn ? listening : undefined}
                onClick={() => (pflOn ? p.pfl.toggle(item.id) : p.onSend(item, how))}
                className={`w-full min-h-[64px] px-2.5 py-2 rounded-lg border text-left transition-colors active:scale-[0.98] disabled:opacity-60
                  ${typeStyle(item.asset_type).button} ${busy ? 'ring-2 ring-white animate-pulse' : ''} ${listening ? 'ring-2 ring-emerald-300' : ''}`}
              >
                <span className="block text-sm font-bold leading-snug line-clamp-2 break-words">{item.title}</span>
                <span className="block text-[11px] opacity-70 tabular-nums mt-0.5">{fmtDur(item.duration)}{listening ? ' · in headphones' : ''}</span>
              </button>
              {editing && (
                <button type="button" onClick={() => p.onTogglePin(item)}
                  aria-label={`Remove ${item.title} from instant buttons`}
                  className="absolute -top-1.5 -right-1.5 w-7 h-7 rounded-full bg-red-600 text-white text-sm font-bold shadow">
                  ✕
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HotButtons(p: Props) {
  const [editing, setEditing] = useState(false);
  const [pflOn, setPflOn] = useState(false);
  const pfl = p.pfl;
  const togglePfl = () => {
    if (pflOn) pfl.stop();
    else if (pfl.canChooseOutput && pfl.outputs.length === 0 && pfl.outputId) pfl.loadOutputs();
    setPflOn(v => !v);
  };
  const { data: jingleData } = useAssets({ asset_type: 'jingle', skip: 0, limit: 60 });
  const { data: spotData } = useAssets({ asset_type: 'spot', skip: 0, limit: 60 });
  const pinnedIds = new Set(p.pins.map(x => x.id));
  const jingles = (jingleData?.assets ?? []).filter(a => !pinnedIds.has(a.id));
  const spots = (spotData?.assets ?? []).filter(a => !pinnedIds.has(a.id));
  const set = p.sets[p.setIndex] ?? p.sets[0];

  return (
    <section className="flex flex-col min-h-0 h-full bg-[#0a0a28]" aria-label="Instant buttons">
      <div className="shrink-0 p-2 border-b border-[#2a2a5e] space-y-2">
        {pflOn ? (
          <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-emerald-500/20 border border-emerald-400 text-emerald-50 text-xs" data-testid="pfl-banner">
            <span className="flex-1 font-bold">PFL: buttons play in your headphones only</span>
            <button type="button" onClick={togglePfl} className="h-8 px-2.5 rounded bg-white/10 hover:bg-white/20 font-bold">Done</button>
          </div>
        ) : p.inserting ? (
          <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-cyan-500/20 border border-cyan-400 text-cyan-50 text-xs">
            <span className="flex-1 font-bold">Buttons insert at the marked spot</span>
            <button type="button" onClick={p.onCancelInsert} className="h-8 px-2.5 rounded bg-white/10 hover:bg-white/20 font-bold">Done</button>
          </div>
        ) : (
          <div className="flex rounded-lg overflow-hidden border border-[#3a3a7e]" role="radiogroup" aria-label="When a button is pressed">
            <button type="button" role="radio" aria-checked={p.fireMode === 'now'} onClick={() => p.onFireMode('now')}
              className={`flex-1 h-10 text-sm font-bold ${p.fireMode === 'now' ? 'bg-red-600 text-white' : 'bg-transparent text-gray-400 hover:bg-white/5'}`}>
              Play now
            </button>
            <button type="button" role="radio" aria-checked={p.fireMode === 'next'} onClick={() => p.onFireMode('next')}
              className={`flex-1 h-10 text-sm font-bold ${p.fireMode === 'next' ? 'bg-amber-500 text-black' : 'bg-transparent text-gray-400 hover:bg-white/5'}`}>
              Play next
            </button>
          </div>
        )}
        <p className="px-1 text-[11px] leading-snug text-gray-500">
          {pflOn
            ? 'Nothing you tap here goes on air. Tap again to stop.'
            : p.inserting
            ? 'Tap a button to drop it into the queue at the marked spot.'
            : p.fireMode === 'now'
              ? 'A tap cuts to that item immediately.'
              : 'A tap plays that item when the current one ends.'}
        </p>
        {pflOn && pfl.canChooseOutput && (
          pfl.outputs.length > 0 ? (
            <select aria-label="Headphones output" value={pfl.outputId}
              onChange={(e) => pfl.chooseOutput(e.target.value)}
              className="w-full h-9 bg-[#14143c] border border-[#3a3a7e] text-gray-100 text-sm px-2 rounded-md focus:outline-none focus:border-emerald-400">
              <option value="">Same output as everything else</option>
              {pfl.outputs.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          ) : (
            <button type="button" onClick={pfl.loadOutputs}
              className="w-full h-9 rounded-md bg-white/10 hover:bg-white/20 text-xs font-semibold text-gray-100">
              Choose the headphones output
            </button>
          )
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-2 space-y-4">
        {/* The cart: named sets, ten pages each */}
        <div className="space-y-2" data-testid="cart">
          <div className="flex items-center gap-1.5">
            <select
              aria-label="Button set"
              value={p.setIndex}
              onChange={(e) => p.onSelectSet(Number(e.target.value))}
              className="flex-1 min-w-0 h-9 bg-[#14143c] border border-[#3a3a7e] text-gray-100 text-sm px-2 rounded-md focus:outline-none focus:border-cyan-500"
            >
              {p.sets.map((s, i) => <option key={i} value={i}>{s.name}</option>)}
            </select>
            <button type="button" onClick={() => setEditing(e => !e)}
              className={`h-9 px-2.5 rounded-md text-xs font-semibold shrink-0 ${editing ? 'bg-cyan-600 text-white' : 'bg-white/10 text-gray-200 hover:bg-white/20'}`}>
              {editing ? 'Done' : 'Edit'}
            </button>
            <button type="button" onClick={togglePfl} aria-pressed={pflOn} data-testid="pfl-toggle"
              title="Pre-listen: hear a button in your headphones without putting it on air"
              className={`h-9 px-2.5 rounded-md text-xs font-bold shrink-0 ${pflOn ? 'bg-emerald-500 text-black' : 'bg-white/10 text-gray-200 hover:bg-white/20'}`}>
              PFL
            </button>
          </div>
          {editing && (
            <div className="flex flex-wrap gap-1.5">
              <button type="button" className="h-9 px-2.5 rounded-md bg-white/10 hover:bg-white/20 text-xs font-semibold text-gray-100"
                onClick={() => { const n = window.prompt('Name for the new set of buttons'); if (n?.trim()) p.onAddSet(n.trim()); }}>
                New set
              </button>
              <button type="button" className="h-9 px-2.5 rounded-md bg-white/10 hover:bg-white/20 text-xs font-semibold text-gray-100"
                onClick={() => { const n = window.prompt('New name for this set', set.name); if (n?.trim()) p.onRenameSet(n.trim()); }}>
                Rename
              </button>
              {p.sets.length > 1 && (
                <button type="button" className="h-9 px-2.5 rounded-md bg-red-900/60 hover:bg-red-800 text-xs font-semibold text-red-100"
                  onClick={() => { if (window.confirm(`Delete the set "${set.name}" and all its buttons?`)) p.onDeleteSet(); }}>
                  Delete set
                </button>
              )}
            </div>
          )}
          <div className="grid grid-cols-10 gap-1" role="tablist" aria-label="Button page">
            {Array.from({ length: CART_PAGES }, (_, i) => {
              const filled = (set.pages[i]?.length ?? 0) > 0;
              const on = i === p.pageIndex;
              return (
                <button key={i} type="button" role="tab" aria-selected={on} data-testid="cart-page"
                  title={`Page ${i + 1}${filled ? `: ${set.pages[i].length} buttons` : ' (empty)'}`}
                  onClick={() => p.onSelectPage(i)}
                  className={`h-9 rounded-md text-sm font-bold tabular-nums border
                    ${on ? 'bg-cyan-500 border-cyan-300 text-black' : filled ? 'bg-white/10 border-white/20 text-gray-100 hover:bg-white/20' : 'bg-transparent border-white/10 text-gray-600 hover:bg-white/5'}`}>
                  {i + 1}
                </button>
              );
            })}
          </div>
        </div>
        <Wall title={`${set.name} · page ${p.pageIndex + 1}`} items={p.pins} p={p} editing={editing} pflOn={pflOn} />
        {p.pins.length === 0 && (
          <p className="px-1 text-[11px] text-gray-500">This page is empty. Tap the ☆ next to anything in the library to put a button here.</p>
        )}
        <Wall title="Jingles" items={jingles} p={p} editing={false} pflOn={pflOn} />
        <Wall title="Sponsor spots" items={spots} p={p} editing={false} pflOn={pflOn} />


        <div>
          <div className="px-1 pb-1 text-[11px] font-bold uppercase tracking-wider text-gray-500">Off air</div>
          <button
            type="button"
            onClick={p.onPreviewWeather}
            disabled={p.weatherState !== 'idle' || p.disabled}
            className={`w-full min-h-[52px] px-2.5 py-2 rounded-lg border text-left text-sm font-bold disabled:opacity-70
              ${p.weatherState === 'playing' ? 'bg-blue-800/60 border-blue-400 text-blue-50 animate-pulse' : 'bg-blue-900/40 border-blue-700 text-blue-100 hover:bg-blue-800/60'}`}
          >
            {p.weatherState === 'loading' ? 'Loading…' : p.weatherState === 'playing' ? 'Playing preview…' : 'Preview time and weather'}
            <span className="block text-[11px] font-normal opacity-70">Only you hear this</span>
          </button>
        </div>
      </div>
    </section>
  );
}

export default memo(HotButtons);
