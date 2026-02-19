import { Link } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';

export default function Navbar() {
  const { isAuthenticated, user, logout } = useAuthStore();

  return (
    <nav className="bg-[#0a0a28] text-white border-b border-[#2a2a5e]">
      <div className="px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-12">
          <div className="flex items-center gap-4 text-sm">
            <Link to="/" className="text-lg font-bold tracking-tight text-cyan-300">
              KBR Studio
            </Link>
            <Link to="/stations" className="text-gray-400 hover:text-white transition">
              Stations
            </Link>
            {isAuthenticated && (
              <>
                <Link to="/admin/dashboard" className="text-gray-400 hover:text-yellow-300 transition">
                  Dashboard
                </Link>
                <Link to="/admin/schedules" className="text-gray-400 hover:text-orange-300 transition">
                  Schedules
                </Link>
                <Link to="/admin/rules" className="text-gray-400 hover:text-purple-300 transition">
                  Rules
                </Link>
                <Link to="/admin/users" className="text-gray-400 hover:text-green-300 transition">
                  Users
                </Link>
                <Link to="/admin/assets" className="text-gray-400 hover:text-cyan-300 transition">
                  Assets
                </Link>
                <Link to="/admin/stations" className="text-gray-400 hover:text-cyan-300 transition">
                  Stations
                </Link>
              </>
            )}
          </div>
          <div className="flex items-center gap-3">
            {isAuthenticated ? (
              <>
                <span className="text-[11px] text-gray-500">{user?.email}</span>
                <button onClick={logout}
                  className="text-[11px] bg-red-900 hover:bg-red-800 text-red-300 px-2 py-0.5 rounded transition">
                  Logout
                </button>
              </>
            ) : (
              <Link to="/admin/login"
                className="text-[11px] bg-[#2a2a5e] hover:bg-[#3a3a7e] text-cyan-300 px-2 py-0.5 rounded transition">
                Admin Login
              </Link>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
}
