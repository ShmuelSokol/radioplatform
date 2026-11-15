import { memo, useState } from 'react';
import { useAssets } from '../../../hooks/useAssets';
import { fmtDur, typeStyle } from './shared';
import type { CtlAsset, FireMode, SendFn } from './shared';

interface Props {
  pins: CtlAsset[];
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
  /** Grid columns differ between the narrow desktop sidebar and the phone tab. */
  wide: boolean;
}

function Wall({ title, items, p, editing }: { title: string; items: CtlAsset[]; p: Props; editing: boolean }) {
  if (items.length === 0) return null;
  const how = p.inserting ? 'insert' : p.fireMode;
  return (
    <div>
      <div className="px-1 pb-1 text-[11px] font-bold uppercase tracking-wider text-gray-500">{title}</div>
      <div className={`grid gap-2 ${p.wide ? 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4' : 'grid-cols-2'}`}>
        {items.map(item => {
          const busy = p.busyId === item.id;
          return (
            <div key={item.id} className="relative">
              <button
                type="button"
                data-testid="hot-button"
                disabled={busy || p.disabled || editing}
                onClick={() => p.onSend(item, how)}
                className={`w-full min-h-[64px] px-2.5 py-2 rounded-lg border text-left transition-colors active:scale-[0.98] disabled:opacity-60
                  ${typeStyle(item.asset_type).button} ${busy ? 'ring-2 ring-white animate-pulse' : ''}`}
              >
                <span className="block text-sm font-bold leading-snug line-clamp-2 break-words">{item.title}</span>
                <span className="block text-[11px] opacity-70 tabular-nums mt-0.5">{fmtDur(item.duration)}</span>
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
  const { data: jingleData } = useAssets({ asset_type: 'jingle', skip: 0, limit: 60 });
  const { data: spotData } = useAssets({ asset_type: 'spot', skip: 0, limit: 60 });
  const pinnedIds = new Set(p.pins.map(x => x.id));
  const jingles = (jingleData?.assets ?? []).filter(a => !pinnedIds.has(a.id));
  const spots = (spotData?.assets ?? []).filter(a => !pinnedIds.has(a.id));
  const nothing = p.pins.length === 0 && jingles.length === 0 && spots.length === 0;

  return (
    <section className="flex flex-col min-h-0 h-full bg-[#0a0a28]" aria-label="Instant buttons">
      <div className="shrink-0 p-2 border-b border-[#2a2a5e] space-y-2">
        {p.inserting ? (
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
          {p.inserting
            ? 'Tap a button to drop it into the queue at the marked spot.'
            : p.fireMode === 'now'
              ? 'A tap cuts to that item immediately.'
              : 'A tap plays that item when the current one ends.'}
        </p>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-2 space-y-4">
        {p.pins.length > 0 && (
          <div className="flex justify-end -mb-3">
            <button type="button" onClick={() => setEditing(e => !e)}
              className="h-7 px-2 rounded text-[11px] font-semibold text-gray-400 hover:text-white hover:bg-white/10">
              {editing ? 'Done editing' : 'Edit my buttons'}
            </button>
          </div>
        )}
        <Wall title="My buttons" items={p.pins} p={p} editing={editing} />
        <Wall title="Jingles" items={jingles} p={p} editing={false} />
        <Wall title="Sponsor spots" items={spots} p={p} editing={false} />

        {nothing && (
          <div className="text-center text-gray-500 py-8 px-3 text-sm">
            No instant buttons yet. Tap the ☆ next to anything in the library to put it here.
          </div>
        )}
        {!nothing && p.pins.length === 0 && (
          <p className="px-1 text-[11px] text-gray-500">Tap the ☆ next to anything in the library to add your own buttons.</p>
        )}

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
