import { Link } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';

export default function Navbar() {
  const { isAuthenticated, user, logout } = useAuthStore();

  return (
    <nav className="bg-blue-900 text-white shadow-lg">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <div className="flex items-center gap-6">
            <Link to="/" className="text-xl font-bold tracking-tight">
              Studio Kol Bramah
            </Link>
            <Link to="/stations" className="hover:text-blue-200 transition">
              Stations
            </Link>
            {isAuthenticated && (
              <>
                <Link to="/admin/dashboard" className="hover:text-blue-200 transition">
                  Dashboard
                </Link>
                <Link to="/admin/stations" className="hover:text-blue-200 transition">
                  Manage Stations
                </Link>
                <Link to="/admin/assets" className="hover:text-blue-200 transition">
                  Assets
                </Link>
              </>
            )}
          </div>
          <div className="flex items-center gap-4">
            {isAuthenticated ? (
              <>
                <span className="text-sm text-blue-200">{user?.email}</span>
                <button
                  onClick={logout}
                  className="text-sm bg-red-600 hover:bg-red-700 px-3 py-1 rounded transition"
                >
                  Logout
                </button>
              </>
            ) : (
              <Link
                to="/admin/login"
                className="text-sm bg-blue-600 hover:bg-blue-700 px-3 py-1 rounded transition"
              >
                Admin Login
              </Link>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
}
