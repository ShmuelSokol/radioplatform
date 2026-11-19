import { useCallback, useState } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';
import { currentIdx, dropState, readState, stateKey, writeState } from '../lib/navMemory';

/**
 * useState that comes back with Back. The value is kept with this page's place in the
 * tab's history, so going Back (the Back bar, the browser's back, an iPhone swipe) finds
 * the page as it was left: same filters, search, open panel. Opening the page afresh
 * (a link, the menu) starts from `initial`, as before. See wiki/back-navigation.md.
 *
 * Only for small, plain values (they are stored as JSON in sessionStorage).
 */
export function useNavState<T>(name: string, initial: T | (() => T)): [T, (v: T | ((prev: T) => T)) => void] {
  const { pathname } = useLocation();
  const type = useNavigationType();
  const [key] = useState(() => stateKey(currentIdx(), pathname, name));
  const [value, setValue] = useState<T>(() => {
    const init = () => (typeof initial === 'function' ? (initial as () => T)() : initial);
    if (type === 'PUSH') {
      // A fresh visit: nothing old applies (an older visit at this place in history).
      dropState(key);
      return init();
    }
    const got = readState<T>(key);
    return got.found ? (got.value as T) : init();
  });
  const set = useCallback((v: T | ((prev: T) => T)) => {
    setValue((prev) => {
      const next = typeof v === 'function' ? (v as (p: T) => T)(prev) : v;
      writeState(key, next === undefined ? null : next);
      return next;
    });
  }, [key]);
  return [value, set];
}
