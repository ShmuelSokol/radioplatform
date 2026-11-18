import { useLayoutEffect, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { useEscape } from '../hooks/useEscape';

interface Props {
  /** The button the menu hangs from */
  anchor: RefObject<HTMLElement>;
  /** Menu width in px, used to keep it on screen */
  width: number;
  onClose: () => void;
  menuRef?: RefObject<HTMLDivElement>;
  className?: string;
  children: ReactNode;
}

/**
 * A drop-down drawn on top of the page (fixed, in <body>), so a table cell that clips its
 * content (truncate / overflow-hidden) cannot cut the menu off. It stays inside the window
 * and opens upward when there is no room below. Scrolling, resizing or Esc closes it.
 */
export default function FloatingMenu({ anchor, width, onClose, menuRef, className = '', children }: Props) {
  const [pos, setPos] = useState<{ top?: number; bottom?: number; left: number; maxHeight: number } | null>(null);

  useLayoutEffect(() => {
    const el = anchor.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 4;
    const edge = 8;
    const left = Math.max(edge, Math.min(r.left, window.innerWidth - width - edge));
    const below = window.innerHeight - r.bottom - gap - edge;
    const above = r.top - gap - edge;
    if (below >= 200 || below >= above) setPos({ top: r.bottom + gap, left, maxHeight: Math.max(120, below) });
    else setPos({ bottom: window.innerHeight - r.top + gap, left, maxHeight: Math.max(120, above) });

    const close = (e: Event) => {
      // scrolling inside the menu itself must not close it
      if (menuRef?.current && e.target instanceof Node && menuRef.current.contains(e.target)) return;
      onClose();
    };
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [anchor, width, onClose, menuRef]);

  useEscape(true, onClose);

  if (!pos) return null;
  return createPortal(
    <>
      <div className="fixed inset-0 z-[60]" onClick={(e) => { e.stopPropagation(); onClose(); }} />
      <div
        ref={menuRef}
        data-floating-menu
        className={`fixed z-[61] overflow-y-auto ${className}`}
        style={{ top: pos.top, bottom: pos.bottom, left: pos.left, width, maxHeight: pos.maxHeight }}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </>,
    document.body,
  );
}
