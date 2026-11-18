import { useLocation } from 'react-router-dom';
import { useAuth } from './useAuth';
import { canChange } from '../auth/access';

/**
 * May the signed-in person change things on the screen they are on? The one rule
 * every screen uses to hide or switch off its change controls (New, Edit, Delete,
 * Save, the control room's live buttons): `canChange` in auth/access.ts, which
 * mirrors the server's write guards and also drives the "View only" badge. Pass a
 * path to ask about another screen's rule (a shared panel shown on several).
 */
export function useMayChange(path?: string): boolean {
  const { pathname } = useLocation();
  const { user } = useAuth();
  return canChange(user?.role, path ?? pathname);
}
