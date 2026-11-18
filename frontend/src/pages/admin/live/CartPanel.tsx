import { useState } from 'react';
import { getAssetAudioUrl } from '../../../api/assets';
import { CART_PAGES, fmtDur, useHotButtons } from '../controlroom/shared';
import type { CtlAsset } from '../controlroom/shared';

interface Props {
  stationId: string | null;
  /** The microphone is open: only then can a cart go out. */
  live: boolean;
  playing: string | null;
  onPlay: (id: string, url: string) => Promise<void>;
  onStop: () => void;
}

/**
 * Carts on the Host Console: the station's instant buttons (the same sets and pages
 * as the control room, kept in this browser), played into the show. A cart goes out
 * with the microphone, so it passes the broadcast delay and a Dump cuts it too.
 */
export default function CartPanel({ stationId, live, playing, onPlay, onStop }: Props) {
  const { pins, cart, selectPage, selectSet } = useHotButtons(stationId);
  const [failed, setFailed] = useState<string | null>(null);

  const press = async (item: CtlAsset) => {
    setFailed(null);
    if (playing === item.id) { onStop(); return; }
    try {
      await onPlay(item.id, await getAssetAudioUrl(item.id));
    } catch {
      setFailed(`"${item.title}" could not be loaded.`);
    }
  };

  const any = cart.sets.some(s => s.pages.some(p => p.length > 0));
  return (
    <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4" data-testid="cart-panel">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-[11px] text-gray-400 uppercase">Carts</div>
        {playing && (
          <button onClick={onStop} data-testid="cart-stop"
            className="bg-red-800 hover:bg-red-700 text-white px-3 py-1 rounded text-xs font-bold">
            Stop cart
          </button>
        )}
      </div>
      {!any ? (
        <p className="text-gray-400 text-sm" data-testid="cart-empty">
          No instant buttons yet. Add jingles and effects to the instant buttons in the control room, in this browser, and they show here.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-1 mb-2">
            {cart.sets.length > 1 && (
              <select value={cart.set} onChange={e => selectSet(Number(e.target.value))} aria-label="Cart set"
                className="bg-[#0a0a28] border border-[#2a2a5e] text-white text-sm rounded px-2 py-1 mr-1">
                {cart.sets.map((s, i) => <option key={i} value={i}>{s.name}</option>)}
              </select>
            )}
            {Array.from({ length: CART_PAGES }, (_, i) => {
              const filled = cart.sets[cart.set].pages[i].length > 0;
              if (!filled && i !== cart.page) return null;
              return (
                <button key={i} onClick={() => selectPage(i)} aria-pressed={i === cart.page} aria-label={`Cart page ${i + 1}`}
                  className={`min-w-[2rem] px-2 py-1 rounded text-xs font-bold ${
                    i === cart.page ? 'bg-cyan-700 text-white' : 'bg-gray-700 hover:bg-gray-600 text-white'}`}>
                  {i + 1}
                </button>
              );
            })}
          </div>
          {pins.length === 0 ? (
            <p className="text-gray-400 text-sm">This page is empty.</p>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {pins.map(item => {
                const on = playing === item.id;
                return (
                  <button key={item.id} onClick={() => press(item)} disabled={!live} aria-pressed={on}
                    data-testid="cart-button"
                    title={live ? (on ? 'Stop' : 'Play into the show') : 'Open your microphone first'}
                    className={`text-left px-3 py-2 rounded text-sm font-bold transition min-h-[44px] disabled:opacity-50 ${
                      on ? 'bg-yellow-500 text-black' : 'bg-gray-700 hover:bg-gray-600 text-white'}`}>
                    <span className="block truncate">{on ? '■ ' : '▶ '}{item.title}</span>
                    <span className="block text-[11px] font-normal">{on ? 'playing, press to stop' : fmtDur(item.duration)}</span>
                  </button>
                );
              })}
            </div>
          )}
          <p className="text-gray-400 text-[11px] mt-2">
            {live
              ? 'Listeners hear a cart with you, through the delay. Mute and Cough silence only your microphone.'
              : 'Open your microphone to use the carts.'}
          </p>
        </>
      )}
      {failed && <div className="text-red-400 text-[11px] mt-2" role="alert">{failed}</div>}
    </div>
  );
}
