import { Outlet, useLocation } from 'react-router-dom';
import Navbar from './Navbar';
import AudioPlayer from '../audio/AudioPlayer';
import { useUiStore } from '../../stores/uiStore';

// Routes that have native V2 (dark) designs — rendered full-bleed in the V2 shell.
// All other pages keep their V1 light styling inside a light "sheet" card.
function isV2Native(pathname: string): boolean {
  return (
    pathname === '/stations' ||
    pathname.startsWith('/listen/') ||
    pathname === '/admin/dashboard'
  );
}

export default function Layout() {
  const isV2 = useUiStore((s) => s.version) === 'v2';
  const { pathname } = useLocation();

  if (!isV2) {
    return (
      <div className="min-h-screen flex flex-col">
        <Navbar />
        <main className="flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-6">
          <Outlet />
        </main>
        <AudioPlayer />
      </div>
    );
  }

  const native = isV2Native(pathname);
  return (
    <div className="v2-root min-h-screen flex flex-col">
      <Navbar />
      <main
        key={pathname}
        className={`v2-fade-up flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-6`}
      >
        {native ? (
          <Outlet />
        ) : (
          <div className="v2-sheet p-4 sm:p-6">
            <Outlet />
          </div>
        )}
      </main>
      <AudioPlayer />
    </div>
  );
}
