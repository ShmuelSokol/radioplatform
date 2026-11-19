import { Suspense } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import ErrorBoundary from '../ErrorBoundary';
import Navbar from './Navbar';
import AudioPlayer from '../audio/AudioPlayer';
import ListenHost from '../audio/ListenHost';
import ViewOnlyNotice from './ViewOnlyNotice';
import PageViewTracker from './PageViewTracker';
import EmergencyBanner from './EmergencyBanner';
import { BackBar, NavTracker } from './BackNav';

// Every page uses the full window width (no centred max-width column); pages lay
// themselves out inside it. Keep the padding below as it is: the dark full-bleed
// pages (Users, Alerts, Rules, ...) cancel it with `-mx-4 sm:-mx-6 lg:-mx-8 -my-6`.
//
// Full-bleed "app" screens: fixed to the viewport, the nav stays on top and the
// page manages its own scrolling panes (no max-width, no page padding).
function isAppShell(pathname: string): boolean {
  return pathname === '/admin/dashboard' || pathname === '/admin/databank';
}

// The KBR Studio ("qnet") look, shared by every signed-in staff page (styles/qnet-site.css).
// Listener pages keep their own look.
export function isQnetPage(pathname: string): boolean {
  return pathname.startsWith('/admin');
}

export default function Layout() {
  const { pathname } = useLocation();
  const shell = isAppShell(pathname);

  // One tree for every page, so moving between the full-window screens and the others
  // never re-creates ListenHost (that ended the sound). The page itself has its own
  // error boundary and loading state: a screen that fails or loads late never takes
  // the player down with it.
  const outer = shell
    // cr-shell: the control room's own phone-sideways rules (controlroom/classic.css),
    // set here rather than found with :has() so older Safari and Firefox get them too
    ? `app-shell flex flex-col overflow-hidden ${pathname === '/admin/dashboard' ? 'cr-shell' : ''}`
    : `min-h-screen flex flex-col ${isQnetPage(pathname) ? 'qn-site' : ''}`;
  return (
    <div className={outer}>
      <Navbar />
      <EmergencyBanner />
      {/* "‹ Back to …" on every staff page but the control room (BackNav.tsx) */}
      <BackBar />
      {/* Phone: room under the page so its last controls scroll clear of the floating bug button */}
      <main className={shell ? 'flex-1 min-h-0' : 'flex-1 w-full min-w-0 px-4 sm:px-6 lg:px-8 pt-6 pb-36 sm:pb-6'}>
        <ErrorBoundary resetKey={pathname}>
          <Suspense fallback={<div className="text-center py-10 text-gray-500">Loading...</div>}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
        <ListenHost />
      </main>
      <AudioPlayer />
      <ViewOnlyNotice />
      <PageViewTracker />
      <NavTracker />
    </div>
  );
}
