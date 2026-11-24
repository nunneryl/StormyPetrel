'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * Whether a horizontally scrollable element has more to its right (`more`: its content is wider
 * than it and it is not scrolled to the end), and whether the reader has scrolled it sideways yet
 * (`swiped`). Re-checked on scroll and resize.
 */
export function useMoreToTheRight(ref: React.RefObject<HTMLElement | null>): { more: boolean; swiped: boolean } {
  const [more, setMore] = useState(false);
  const [swiped, setSwiped] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      setMore(el.scrollWidth - el.clientWidth - el.scrollLeft > 4);
      if (el.scrollLeft > 4) setSwiped(true);
    };
    update();
    el.addEventListener('scroll', update, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null;
    ro?.observe(el);
    window.addEventListener('resize', update);
    return () => {
      el.removeEventListener('scroll', update);
      ro?.disconnect();
      window.removeEventListener('resize', update);
    };
  }, [ref]);
  return { more, swiped };
}

export const SWIPE_HINT = 'Swipe to see all →';

/** The fade and the words that say a box scrolls sideways; render inside a `relative` parent.
 *  The fade stays while there is more to the right; the words only until the reader's first
 *  swipe, since they cover what is under them. `at` puts the words at the top (a table) or the
 *  bottom (a diagram). */
export function MoreToTheRight({ state, at = 'bottom' }: { state: { more: boolean; swiped: boolean }; at?: 'top' | 'bottom' }) {
  if (!state.more) return null;
  return (
    <>
      <div aria-hidden className="pointer-events-none absolute top-0 bottom-0 right-0 w-10 bg-gradient-to-l from-white to-transparent" />
      {!state.swiped && <div className={`absolute ${at === 'top' ? 'top-1.5' : 'bottom-1'} right-2 z-30 rounded bg-white/90 px-1.5 py-0.5 text-[11px] font-medium text-text-secondary shadow-sm`} data-swipe-hint>
        {SWIPE_HINT}
      </div>}
    </>
  );
}

/**
 * A diagram that keeps at least `minWidth` px on a narrow screen, so its labels stay readable, and
 * scrolls sideways there, saying so while there is more to see. On a wide screen it is just the
 * diagram at full width.
 */
export function WideScroll({ minWidth, children }: { minWidth: number; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const scroll = useMoreToTheRight(ref);
  return (
    <div className="relative">
      <div ref={ref} className="overflow-x-auto" data-wide-scroll>
        <div style={{ minWidth }}>{children}</div>
      </div>
      <MoreToTheRight state={scroll} />
    </div>
  );
}
