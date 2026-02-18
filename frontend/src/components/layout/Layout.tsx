import { Outlet } from 'react-router-dom';
import Navbar from './Navbar';
import AudioPlayer from '../audio/AudioPlayer';

export default function Layout() {
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
