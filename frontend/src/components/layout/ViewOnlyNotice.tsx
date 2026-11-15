import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { canAccess, canChange } from '../../auth/access';
import { FORBIDDEN_EVENT } from '../../api/client';

/**
 * Tells a login that may look at a screen but not change it, before they press
 * anything, and says so plainly whenever the server refuses an action. Without
 * this a refused button simply appears to do nothing.
 */
export default function ViewOnlyNotice() {
  const { pathname } = useLocation();
  const { user } = useAuth();
  const [refused, setRefused] = useState(false);

  useEffect(() => {
    let timer: number | undefined;
    const onRefused = () => {
      setRefused(true);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setRefused(false), 5000);
    };
    window.addEventListener(FORBIDDEN_EVENT, onRefused);
    return () => {
      window.removeEventListener(FORBIDDEN_EVENT, onRefused);
      window.clearTimeout(timer);
    };
  }, []);

  if (!user) return null;
  const viewOnly = canAccess(user.role, pathname) && !canChange(user.role, pathname);

  return (
    <>
      {viewOnly && (
        <div
          data-testid="view-only"
          title="Your login can look at this screen but not change anything on it. Ask an admin if you need more."
          className="fixed left-3 z-[55] bottom-[calc(5rem+env(safe-area-inset-bottom))] lg:bottom-3 px-3 py-1.5 rounded-full bg-amber-100 border border-amber-400 text-amber-900 text-xs font-semibold shadow"
        >
          View only
        </div>
      )}
      {refused && (
        <div
          role="alert"
          data-testid="not-allowed"
          className="fixed left-1/2 -translate-x-1/2 z-[70] bottom-[calc(8rem+env(safe-area-inset-bottom))] lg:bottom-24 w-[calc(100%-1.5rem)] max-w-md px-4 py-3 rounded-lg bg-red-900 border border-red-500 text-red-50 text-sm shadow-xl"
        >
          Your login ({user.role}) is not allowed to do that. Nothing was changed.
        </div>
      )}
    </>
  );
}
