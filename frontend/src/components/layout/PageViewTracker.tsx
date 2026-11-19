import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { reportPageView } from '../../api/activity';

// Names for the activity log ("Opened Playlists"). Longest prefix wins.
const TITLES: [string, string][] = [
  ['/admin/dashboard', 'Control room'],
  ['/admin/assets/upload', 'Upload'],
  ['/admin/assets', 'Library'],
  ['/admin/categories', 'Categories'],
  ['/admin/asset-types', 'Item types'],
  ['/admin/playlists', 'Playlists'],
  ['/admin/studio', 'Studio'],
  ['/admin/reviews', 'Reviews'],
  ['/admin/schedules', 'Schedules'],
  ['/admin/databank', 'Databank'],
  ['/admin/rds', 'RDS'],
  ['/admin/program-blocks', 'Program blocks'],
  ['/admin/holiday-music', 'Holiday music'],
  ['/admin/schedule-check', 'Schedule check'],
  ['/admin/rules', 'Rules'],
  ['/admin/holidays', 'Blackouts'],
  ['/admin/program-clock', 'Program clock'],
  ['/admin/live', 'Live shows'],
  ['/admin/listeners', 'Listeners'],
  ['/admin/analytics', 'Analytics'],
  ['/admin/requests', 'Song requests'],
  ['/admin/users', 'Users'],
  ['/admin/sponsors', 'Sponsors'],
  ['/admin/spot-schedule', 'Spot schedule'],
  ['/admin/spot-recordings', 'Record spots'],
  ['/admin/sponsorship-rules', 'Sponsorship rules'],
  ['/admin/alerts', 'Alerts'],
  ['/admin/bug-reports', 'Bug reports'],
  ['/admin/site-updates', 'Site updates'],
  ['/admin/stations', 'Stations'],
  ['/admin/crm', 'CRM'],
  ['/admin/activity', 'Activity'],
  ['/admin/archives', 'Archives (admin)'],
  ['/listen', 'Listen page'],
  ['/stations', 'Station list'],
  ['/guide', 'Program guide'],
];

export function pageTitle(path: string): string {
  if (/^\/admin\/live\/[^/]+\/host/.test(path)) return 'Host console';
  if (/^\/admin\/live\/[^/]+\/screen/.test(path)) return 'Call screener';
  if (/^\/admin\/schedules\/[^/]+\/blocks/.test(path)) return 'Schedule blocks';
  let best: [string, string] | null = null;
  for (const entry of TITLES) {
    if ((path === entry[0] || path.startsWith(entry[0] + '/')) && (!best || entry[0].length > best[0].length)) best = entry;
  }
  return best ? best[1] : path;
}

/** Records the screens a signed-in staff member opens, for the admin Activity page. */
export default function PageViewTracker() {
  const { pathname } = useLocation();
  const signedIn = useAuthStore((s) => s.isAuthenticated);
  const last = useRef<{ path: string; at: number } | null>(null);

  useEffect(() => {
    if (!signedIn || pathname === '/admin/login') return;
    const now = Date.now();
    // Same screen again within a minute (e.g. a re-render after sign-in) is not news
    if (last.current && last.current.path === pathname && now - last.current.at < 60_000) return;
    last.current = { path: pathname, at: now };
    reportPageView(pathname, pageTitle(pathname));
  }, [pathname, signedIn]);

  return null;
}
