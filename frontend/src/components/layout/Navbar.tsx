import { Link } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';

export default function Navbar() {
  const { isAuthenticated, user, logout } = useAuthStore();

  return (
    <nav className="bg-brand-900 text-white shadow-lg">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <div className="flex items-center gap-6">
            <Link to="/" className="text-xl font-bold tracking-tight">
              RadioPlatform
            </Link>
            <Link to="/stations" className="hover:text-brand-50 transition">
              Stations
            </Link>
            {isAuthenticated && (
              <>
                <Link to="/admin/dashboard" className="hover:text-brand-50 transition">
                  Dashboard
                </Link>
                <Link to="/admin/stations" className="hover:text-brand-50 transition">
                  Manage Stations
                </Link>
                <Link to="/admin/assets" className="hover:text-brand-50 transition">
                  Assets
                </Link>
              </>
            )}
          </div>
          <div className="flex items-center gap-4">
            {isAuthenticated ? (
              <>
                <span className="text-sm text-brand-50">{user?.email}</span>
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
                className="text-sm bg-brand-600 hover:bg-brand-700 px-3 py-1 rounded transition"
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
