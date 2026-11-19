import { useCallback, useRef } from 'react';
import type { CSSProperties, RefObject } from 'react';
import { useStored } from './shared';

/**
 * Columns whose widths the operator sets, like Excel (Shmuel 2026-11-19, looking at the
 * station queue: "double click in between Time and Length ... to make it the maximum
 * width ... so it's a little bit more tight"). Drag the border at a heading's right edge
 * to change that column; double-click the border to fit the column to its widest
 * entry on screen. Widths are kept in this browser, the same on every station.
 */
export interface ColumnDef {
  id: string;
  label: string;
  /** Width in px on a first visit */
  width: number;
  min?: number;
  /** Gives up width (down to its min) when the panel is too narrow for every column */
  shrink?: boolean;
  /** Tooltip on the heading */
  title?: string;
  align?: 'right';
}

export type Widths = Record<string, number>;

const MAX_W = 900;

export function useColumnWidths(storeKey: string, cols: ColumnDef[]) {
  const [saved, setSaved] = useStored<Widths>(storeKey, {});
  const widths: Widths = {};
  for (const c of cols) {
    const v = Number(saved?.[c.id]);
    widths[c.id] = Number.isFinite(v) && v > 0 ? Math.max(c.min ?? 32, Math.min(MAX_W, v)) : c.width;
  }
  const savedRef = useRef(saved);
  savedRef.current = saved;
  const setWidth = useCallback((id: string, w: number) => {
    setSaved({ ...(savedRef.current ?? {}), [id]: Math.round(w) });
  }, [setSaved]);
  const reset = useCallback(() => setSaved({}), [setSaved]);
  const customised = !!saved && Object.keys(saved).length > 0;
  /** Style for one cell (heading or row) of a column */
  const cell = (c: ColumnDef): CSSProperties => c.shrink
    ? { flex: `0 1 ${widths[c.id]}px`, minWidth: c.min ?? 80 }
    : { width: widths[c.id], flexShrink: 0 };
  return { widths, setWidth, reset, customised, cell };
}

/** How wide an element's content is with nothing cut: a hidden copy laid out at its own width. */
function contentWidth(el: HTMLElement): number {
  const host = el.parentElement;
  if (!host) return el.scrollWidth;
  const copy = el.cloneNode(true) as HTMLElement;
  copy.removeAttribute('data-col');
  copy.setAttribute('aria-hidden', 'true');
  for (const c of [copy, ...Array.from(copy.querySelectorAll<HTMLElement>('*'))]) c.removeAttribute('data-testid');
  Object.assign(copy.style, {
    position: 'absolute', visibility: 'hidden', pointerEvents: 'none', left: '0', top: '0',
    width: 'max-content', maxWidth: 'none', minWidth: '0', flex: 'none', whiteSpace: 'nowrap',
  });
  host.appendChild(copy);
  const w = copy.getBoundingClientRect().width;
  copy.remove();
  return w;
}

/** The widest thing in a column: every cell marked data-col=<id> inside `scope`, and its heading. */
function fitWidth(scope: HTMLElement | null, head: HTMLElement | null, id: string): number {
  let w = 0;
  scope?.querySelectorAll<HTMLElement>(`[data-col="${id}"]`).forEach((el) => { w = Math.max(w, contentWidth(el)); });
  const label = head?.querySelector(`[data-col-label="${id}"]`) as HTMLElement | null;
  if (label) w = Math.max(w, label.getBoundingClientRect().width + 14);
  return Math.ceil(w + 2);
}

interface HeadProps {
  cols: ColumnDef[];
  api: ReturnType<typeof useColumnWidths>;
  /** Where the rows are, for double-click to fit */
  rowsRef: RefObject<HTMLElement | null>;
  headRef?: RefObject<HTMLDivElement | null>;
  /** What the operator calls this table, in the tooltips */
  name: string;
}

/** The headings with a draggable border at the right edge of each, and a reset button. */
export function ColumnHeads({ cols, api, rowsRef, headRef, name }: HeadProps) {
  const ownRef = useRef<HTMLDivElement>(null);
  const ref = headRef ?? ownRef;
  const drag = useRef<{ id: string; x: number; w: number; pointer: number } | null>(null);

  const onDown = (c: ColumnDef) => (e: React.PointerEvent<HTMLSpanElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const th = (e.currentTarget.parentElement as HTMLElement);
    drag.current = { id: c.id, x: e.clientX, w: th.getBoundingClientRect().width, pointer: e.pointerId };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* old browsers */ }
  };
  const onMove = (c: ColumnDef) => (e: React.PointerEvent<HTMLSpanElement>) => {
    const d = drag.current;
    if (!d || d.id !== c.id || d.pointer !== e.pointerId) return;
    api.setWidth(c.id, Math.max(c.min ?? 32, Math.min(MAX_W, d.w + e.clientX - d.x)));
  };
  const onUp = (e: React.PointerEvent<HTMLSpanElement>) => {
    if (drag.current?.pointer === e.pointerId) drag.current = null;
  };
  const fit = (c: ColumnDef) => (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const w = fitWidth(rowsRef.current, ref.current, c.id);
    if (w > 0) api.setWidth(c.id, Math.max(c.min ?? 32, Math.min(MAX_W, w)));
  };

  return (
    <>
      {cols.map((c) => (
        <span key={c.id} role="columnheader" className={`relative min-w-0 ${c.align === 'right' ? 'text-right' : ''}`}
          style={api.cell(c)} title={c.title} data-testid={`col-${c.id}`}>
          <span className="block truncate"><span data-col-label={c.id}>{c.label}</span></span>
          <span
            role="separator" aria-orientation="vertical" aria-label={`${c.label} column width: drag, or double-click to fit`}
            title={`Drag to change the ${c.label} column of the ${name}. Double-click to fit it to its widest entry.`}
            data-testid={`col-resize-${c.id}`}
            onPointerDown={onDown(c)} onPointerMove={onMove(c)} onPointerUp={onUp} onPointerCancel={onUp}
            onDoubleClick={fit(c)}
            className="absolute top-0 -right-[4px] z-10 h-full w-[8px] cursor-col-resize touch-none hover:bg-[#00ffff]/60 !shadow-none !p-0"
          />
        </span>
      ))}
      <span className="flex-1 min-w-0 flex justify-end !shadow-none">
        {api.customised && (
          <button type="button" onClick={api.reset} data-testid="col-reset"
            title={`Put the ${name} columns back to their first widths`}
            className="px-1 text-[10px] leading-[16px] hover:underline">
            Reset widths
          </button>
        )}
      </span>
    </>
  );
}
