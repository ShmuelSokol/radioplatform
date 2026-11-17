import type { User } from '../types';

export type Role = User['role'];

const ADMIN: Role[] = ['admin'];
const MANAGERS: Role[] = ['admin', 'manager'];
const ON_AIR: Role[] = ['admin', 'manager', 'dj'];
const STAFF: Role[] = ['admin', 'manager', 'dj', 'viewer'];
const SPONSOR: Role[] = ['sponsor'];

/**
 * Which roles may open which screen. Mirrors the backend guards in
 * backend/app/core/dependencies.py (require_admin / require_manager /
 * require_dj_or_manager / require_sponsor) so nobody is shown a screen whose
 * data the API would refuse them. Longest matching prefix wins.
 */
const ROUTE_ROLES: [prefix: string, roles: Role[]][] = [
  ['/admin/users', ADMIN],
  ['/admin/site-updates', ADMIN],
  ['/admin/activity', ADMIN],
  ['/admin/dashboard', STAFF],
  ['/admin/assets/upload', MANAGERS],
  ['/admin/assets', STAFF],
  ['/admin/databank', STAFF],
  ['/admin/reviews', STAFF],
  ['/admin/schedules', STAFF],
  ['/admin/rules', STAFF],
  ['/admin/program-clock', STAFF],
  ['/admin/campaigns', STAFF],
  ['/admin/live', ON_AIR],
  ['/admin/studio', ON_AIR],
  ['/admin', MANAGERS],
  ['/sponsor', SPONSOR],
];

export function canAccess(role: Role | undefined | null, path: string): boolean {
  if (!role) return false;
  let best: [string, Role[]] | null = null;
  for (const entry of ROUTE_ROLES) {
    const [prefix] = entry;
    if (path === prefix || path.startsWith(prefix + '/')) {
      if (!best || prefix.length > best[0].length) best = entry;
    }
  }
  return best ? best[1].includes(role) : true;
}

/**
 * Which roles may CHANGE things on a screen they can open. Mirrors the write
 * guards on the backend routes behind each screen. Screens not listed can be
 * changed by everyone who can open them.
 */
const WRITE_ROLES: [prefix: string, roles: Role[]][] = [
  ['/admin/dashboard', ON_AIR],
  ['/admin/assets', MANAGERS],
  ['/admin/databank', MANAGERS],
  ['/admin/schedules', MANAGERS],
  ['/admin/rules', ADMIN],
  ['/admin/program-clock', MANAGERS],
  ['/admin/campaigns', MANAGERS],
  ['/admin/bug-reports', ADMIN],
];

export function canChange(role: Role | undefined | null, path: string): boolean {
  if (!role || !canAccess(role, path)) return false;
  const entry = WRITE_ROLES.find(([prefix]) => path === prefix || path.startsWith(prefix + '/'));
  return entry ? entry[1].includes(role) : true;
}

/** Where a signed-in user of this role lands. */
export function homeFor(role: Role | undefined | null): string {
  if (role === 'sponsor') return '/sponsor/dashboard';
  return '/admin/dashboard';
}

export function isManager(role: Role | undefined | null): boolean {
  return role === 'admin' || role === 'manager';
}
