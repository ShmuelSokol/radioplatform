import { memo, useState } from 'react';
import { useAssets } from '../../../hooks/useAssets';
import { CART_PAGES, fmtDur } from './shared';
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
  /** Laid out as a strip along the bottom of a desktop, as on the old program. */
  bar?: boolean;
}

function Wall({ title, items, p, editing, pflOn, emptyHint }: { title: string; items: CtlAsset[]; p: Props; editing: boolean; pflOn: boolean; emptyHint?: string }) {
  if (items.length === 0) {
    return emptyHint ? (
      <div>
        <div className="px-0.5 pb-0.5 text-[11px] text-black">{title}</div>
        <p className="px-0.5 text-xs text-[#404040]" data-testid="wall-empty">{emptyHint}</p>
      </div>
    ) : null;
  }
  const how = p.inserting ? 'insert' : p.fireMode;
  const cols = p.bar
    ? 'grid-cols-3 xl:grid-cols-5 2xl:grid-cols-7'
    : p.wide ? 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4' : 'grid-cols-2';
  return (
    <div>
      <div className="px-0.5 pb-0.5 text-[11px] text-black">{title}</div>
      <div className={`grid gap-1 ${cols}`}>
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
                className={`qn-btn w-full text-left flex items-center gap-2 ${p.bar ? 'h-9 px-2' : 'min-h-[56px] px-2.5 py-1.5'}
                  ${busy ? 'animate-pulse !bg-yellow-200' : ''} ${listening ? '!bg-[#2bff4a]' : ''}`}
              >
                <span className={`flex-1 min-w-0 text-sm font-bold leading-tight ${p.bar ? 'truncate' : 'line-clamp-2 break-words'}`}>{item.title}</span>
                <span className="shrink-0 text-[11px] font-normal tabular-nums">{fmtDur(item.duration)}{listening ? ' PFL' : ''}</span>
              </button>
              {editing && (
                <button type="button" onClick={() => p.onTogglePin(item)}
                  aria-label={`Remove ${item.title} from instant buttons`}
                  className="absolute -top-1.5 -right-1.5 w-6 h-6 rounded-full bg-red-600 text-white text-xs font-bold shadow">
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
  const { data: jingleData } = useAssets({ asset_type: 'jingle', skip: 0, limit: 60, hide_generated: true });
  const { data: spotData } = useAssets({ asset_type: 'spot', skip: 0, limit: 60, hide_generated: true });
  const pinnedIds = new Set(p.pins.map(x => x.id));
  const jingles = (jingleData?.assets ?? []).filter(a => !pinnedIds.has(a.id));
  const spots = (spotData?.assets ?? []).filter(a => !pinnedIds.has(a.id));
  const set = p.sets[p.setIndex] ?? p.sets[0];

  const pageButton = (i: number) => {
    const filled = (set.pages[i]?.length ?? 0) > 0;
    const on = i === p.pageIndex;
    return (
      <button key={i} type="button" role="tab" aria-selected={on} data-testid="cart-page"
        title={`Page ${i + 1}${filled ? `: ${set.pages[i].length} buttons` : ' (empty)'}`}
        onClick={() => p.onSelectPage(i)}
        className={`qn-btn h-8 ${p.bar ? 'w-7' : ''} text-sm tabular-nums ${filled ? '' : '!font-normal !text-[#606060]'} ${on ? '!bg-[#ffff80]' : ''}`}>
        {i + 1}
      </button>
    );
  };
  const setSelect = (
    <select aria-label="Button set" value={p.setIndex} onChange={(e) => p.onSelectSet(Number(e.target.value))}
      className={`qn-field h-8 text-sm px-1.5 ${p.bar ? 'w-40' : 'flex-1 min-w-0'}`}>
      {p.sets.map((s, i) => <option key={i} value={i}>{s.name}</option>)}
    </select>
  );
  const editButton = (
    <button type="button" onClick={() => setEditing(e => !e)} aria-pressed={editing}
      className="qn-btn h-8 px-3 text-sm shrink-0">
      {editing ? 'Done' : 'Edit'}
    </button>
  );
  const pflButton = (
    <button type="button" onClick={togglePfl} aria-pressed={pflOn} data-testid="pfl-toggle"
      title="Pre-listen: hear a button in your headphones without putting it on air"
      className={`qn-btn h-8 px-3 text-sm shrink-0 ${pflOn ? '!bg-[#2bff4a]' : ''}`}>
      PFL
    </button>
  );
  const modeHelp = pflOn
    ? 'Nothing you tap here goes on air. Tap again to stop.'
    : p.inserting
    ? 'Tap a button to drop it into the queue at the marked spot.'
    : p.fireMode === 'now'
      ? 'A tap cuts to that item immediately.'
      : p.fireMode === 'mix'
        ? 'A tap plays that item on top of what is on air. The song carries on.'
        : 'A tap plays that item when the current one ends.';
  const mode = pflOn ? (
    <div className="flex items-center gap-2 px-2 h-8 bg-[#2bff4a] text-black text-xs" data-testid="pfl-banner">
      <span className="flex-1 font-bold">PFL: buttons play in your headphones only</span>
      <button type="button" onClick={togglePfl} className="qn-btn h-6 px-2.5 text-xs">Done</button>
    </div>
  ) : p.inserting ? (
    <div className="flex items-center gap-2 px-2 h-8 bg-[#00ffff] text-black text-xs">
      <span className="flex-1 font-bold">Buttons insert at the marked spot</span>
      <button type="button" onClick={p.onCancelInsert} className="qn-btn h-6 px-2.5 text-xs">Done</button>
    </div>
  ) : (
    <div className="flex gap-1" role="radiogroup" aria-label="When a button is pressed" title={modeHelp}>
      <button type="button" role="radio" aria-checked={p.fireMode === 'now'} onClick={() => p.onFireMode('now')}
        className={`qn-btn flex-1 h-8 px-2.5 text-sm whitespace-nowrap ${p.fireMode === 'now' ? '!bg-[#ff8080]' : ''}`}>
        Play now
      </button>
      <button type="button" role="radio" aria-checked={p.fireMode === 'next'} onClick={() => p.onFireMode('next')}
        className={`qn-btn flex-1 h-8 px-2.5 text-sm whitespace-nowrap ${p.fireMode === 'next' ? '!bg-[#ffd060]' : ''}`}>
        Play next
      </button>
      <button type="button" role="radio" aria-checked={p.fireMode === 'mix'} onClick={() => p.onFireMode('mix')}
        title="Play a button on top of the item on air, which carries on"
        className={`qn-btn flex-1 h-8 px-2.5 text-sm whitespace-nowrap ${p.fireMode === 'mix' ? '!bg-[#d0a0ff]' : ''}`}>
        Mix over
      </button>
    </div>
  );
  const outputPicker = pflOn && pfl.canChooseOutput ? (
    pfl.outputs.length > 0 ? (
      <select aria-label="Headphones output" value={pfl.outputId}
        onChange={(e) => pfl.chooseOutput(e.target.value)}
        className="qn-field h-8 text-sm px-1.5 w-full lg:w-56">
        <option value="">Same output as everything else</option>
        {pfl.outputs.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    ) : (
      <button type="button" onClick={pfl.loadOutputs} className="qn-btn h-8 px-2.5 text-xs w-full lg:w-auto">
        Choose the headphones output
      </button>
    )
  ) : null;
  const setTools = editing ? (
    <div className="flex flex-wrap gap-1">
      <button type="button" className="qn-btn h-8 px-2.5 text-xs"
        onClick={() => { const n = window.prompt('Name for the new set of buttons'); if (n?.trim()) p.onAddSet(n.trim()); }}>
        New set
      </button>
      <button type="button" className="qn-btn h-8 px-2.5 text-xs"
        onClick={() => { const n = window.prompt('New name for this set', set.name); if (n?.trim()) p.onRenameSet(n.trim()); }}>
        Rename
      </button>
      {p.sets.length > 1 && (
        <button type="button" className="qn-btn h-8 px-2.5 text-xs !text-red-700"
          onClick={() => { if (window.confirm(`Delete the set "${set.name}" and all its buttons?`)) p.onDeleteSet(); }}>
          Delete set
        </button>
      )}
    </div>
  ) : null;
  const weather = (
    <button type="button" onClick={p.onPreviewWeather} disabled={p.weatherState !== 'idle' || p.disabled}
      title="Only you hear this"
      className={`qn-btn h-8 px-2.5 text-xs shrink-0 ${p.weatherState === 'playing' ? 'animate-pulse' : ''}`}>
      {p.weatherState === 'loading' ? 'Loading…' : p.weatherState === 'playing' ? 'Playing preview…' : 'Preview time & weather'}
    </button>
  );
  const walls = (
    <>
      <Wall title={`${set.name} · page ${p.pageIndex + 1}`} items={p.pins} p={p} editing={editing} pflOn={pflOn} />
      {p.pins.length === 0 && (
        <p className="px-0.5 text-[11px] text-black">Page {p.pageIndex + 1} is empty. Tap the ☆ next to anything in the library to put a button here.</p>
      )}
      <Wall title="Jingles" items={jingles} p={p} editing={false} pflOn={pflOn}
        emptyHint="No station IDs or sweepers yet. Upload them in Assets with the type Jingle and they appear here." />
      <Wall title="Sponsor spots" items={spots} p={p} editing={false} pflOn={pflOn} />
    </>
  );

  if (p.bar) {
    // Along the bottom of a desktop, like the cart on the old program
    return (
      <section className="flex flex-col h-full min-h-0 bg-[#c0c0c0] text-black border-t-2 border-white" aria-label="Instant buttons">
        <div className="shrink-0 flex flex-wrap items-center gap-1 px-1.5 pt-1.5 pb-1" data-testid="cart">
          <div className="flex gap-0.5" role="tablist" aria-label="Button page">
            {Array.from({ length: 5 }, (_, i) => pageButton(i))}
          </div>
          {setSelect}
          <div className="flex gap-0.5" role="tablist" aria-label="Button page, 6 to 10">
            {Array.from({ length: 5 }, (_, i) => pageButton(i + 5))}
          </div>
          {editButton}
          <span className="w-2" />
          {mode}
          {pflButton}
          {weather}
          {outputPicker}
          {setTools}
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-1.5 pb-1.5 space-y-1.5">{walls}</div>
      </section>
    );
  }

  return (
    <section className="flex flex-col min-h-0 h-full bg-[#c0c0c0] text-black" aria-label="Instant buttons">
      <div className="shrink-0 p-1.5 border-b border-[#808080] space-y-1.5">
        {mode}
        <p className="px-0.5 text-[11px] leading-snug">{modeHelp}</p>
        {outputPicker}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-1.5 space-y-3">
        <div className="space-y-1.5" data-testid="cart">
          <div className="flex items-center gap-1">{setSelect}{editButton}{pflButton}</div>
          {setTools}
          <div className="grid grid-cols-10 gap-0.5" role="tablist" aria-label="Button page">
            {Array.from({ length: CART_PAGES }, (_, i) => pageButton(i))}
          </div>
        </div>
        {walls}
        <div>{weather}</div>
      </div>
    </section>
  );
}

export default memo(HotButtons);
