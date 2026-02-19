import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';

export default function Navbar() {
  const { isAuthenticated, user, logout } = useAuthStore();
  const [menuOpen, setMenuOpen] = useState(false);

  const navLinks = isAuthenticated ? [
    { to: '/stations', label: 'Stations', color: 'hover:text-white' },
    { to: '/admin/dashboard', label: 'Dashboard', color: 'hover:text-yellow-300' },
    { to: '/admin/schedules', label: 'Schedules', color: 'hover:text-orange-300' },
    { to: '/admin/playlists', label: 'Playlists', color: 'hover:text-indigo-300' },
    { to: '/admin/rules', label: 'Rules', color: 'hover:text-purple-300' },
    { to: '/admin/users', label: 'Users', color: 'hover:text-green-300' },
    { to: '/admin/holidays', label: 'Blackouts', color: 'hover:text-red-300' },
    { to: '/admin/sponsors', label: 'Sponsors', color: 'hover:text-yellow-300' },
    { to: '/admin/analytics', label: 'Analytics', color: 'hover:text-pink-300' },
    { to: '/admin/assets', label: 'Assets', color: 'hover:text-cyan-300' },
    { to: '/admin/reviews', label: 'Reviews', color: 'hover:text-emerald-300' },
    { to: '/admin/stations', label: 'Manage Stations', color: 'hover:text-cyan-300' },
  ] : [
    { to: '/stations', label: 'Stations', color: 'hover:text-white' },
  ];

  return (
    <nav className="bg-[#0a0a28] text-white border-b border-[#2a2a5e]">
      <div className="px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-12">
          {/* Logo + desktop links */}
          <div className="flex items-center gap-4 text-sm">
            <Link to="/" className="text-lg font-bold tracking-tight text-cyan-300">
              KBR Studio
            </Link>
            {/* Desktop nav - hidden on mobile */}
            <div className="hidden md:flex items-center gap-4">
              {navLinks.map(link => (
                <Link key={link.to} to={link.to}
                  className={`text-gray-400 ${link.color} transition`}>
                  {link.label}
                </Link>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-3">
            {isAuthenticated ? (
              <>
                <span className="text-[11px] text-gray-500 hidden sm:inline">{user?.email}</span>
                <button onClick={logout}
                  className="text-[11px] bg-red-900 hover:bg-red-800 text-red-300 px-2 py-0.5 rounded transition hidden md:inline-block">
                  Logout
                </button>
              </>
            ) : (
              <Link to="/admin/login"
                className="text-[11px] bg-[#2a2a5e] hover:bg-[#3a3a7e] text-cyan-300 px-2 py-0.5 rounded transition">
                Admin Login
              </Link>
            )}
            {/* Hamburger button - shown on mobile */}
            {isAuthenticated && (
              <button onClick={() => setMenuOpen(!menuOpen)}
                className="md:hidden text-gray-400 hover:text-white p-1">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  {menuOpen ? (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  ) : (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                  )}
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Mobile dropdown menu */}
      {menuOpen && isAuthenticated && (
        <div className="md:hidden border-t border-[#2a2a5e] bg-[#0c0c30]">
          <div className="px-4 py-2 space-y-1">
            {navLinks.map(link => (
              <Link key={link.to} to={link.to}
                onClick={() => setMenuOpen(false)}
                className={`block py-2 px-3 text-sm text-gray-400 ${link.color} transition rounded hover:bg-[#1a1a4e]`}>
                {link.label}
              </Link>
            ))}
            <div className="border-t border-[#2a2a5e] pt-2 mt-2">
              <span className="block px-3 text-[11px] text-gray-500 mb-1">{user?.email}</span>
              <button onClick={() => { logout(); setMenuOpen(false); }}
                className="w-full text-left py-2 px-3 text-sm text-red-400 hover:bg-[#1a1a4e] rounded transition">
                Logout
              </button>
            </div>
          </div>
        </div>
      )}
    </nav>
  );
}
