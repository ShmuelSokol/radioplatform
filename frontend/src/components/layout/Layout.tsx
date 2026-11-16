import { Outlet, useLocation } from 'react-router-dom';
import Navbar from './Navbar';
import AudioPlayer from '../audio/AudioPlayer';
import ViewOnlyNotice from './ViewOnlyNotice';

// Every page uses the full window width (no centred max-width column); pages lay
// themselves out inside it. Keep the padding below as it is: the dark full-bleed
// pages (Users, Alerts, Rules, ...) cancel it with `-mx-4 sm:-mx-6 lg:-mx-8 -my-6`.
//
// Full-bleed "app" screens: fixed to the viewport, the nav stays on top and the
// page manages its own scrolling panes (no max-width, no page padding).
function isAppShell(pathname: string): boolean {
  return pathname === '/admin/dashboard';
}

export default function Layout() {
  const { pathname } = useLocation();

  if (isAppShell(pathname)) {
    return (
      <div className="app-shell flex flex-col overflow-hidden">
        <Navbar />
        <main className="flex-1 min-h-0">
          <Outlet />
        </main>
        <AudioPlayer />
        <ViewOnlyNotice />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col">
      <Navbar />
      <main className="flex-1 w-full min-w-0 px-4 sm:px-6 lg:px-8 py-6">
        <Outlet />
      </main>
      <AudioPlayer />
      <ViewOnlyNotice />
    </div>
  );
}
