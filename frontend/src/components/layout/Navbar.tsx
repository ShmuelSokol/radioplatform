import { useState, useRef, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { useUnresolvedCount, useAlerts } from '../../hooks/useAlerts';

interface NavLink {
  to: string;
  label: string;
  color: string;
}

interface NavGroup {
  label: string;
  color: string;       // text color for the group label
  hoverColor: string;  // hover highlight
  links: NavLink[];
}

export default function Navbar() {
  const { isAuthenticated, user, logout } = useAuthStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const bellRef = useRef<HTMLDivElement>(null);
  const dropdownTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();

  const { data: unresolvedCount } = useUnresolvedCount(isAuthenticated);
  const { data: recentAlerts } = useAlerts(isAuthenticated ? { limit: 5, is_resolved: false } : null);

  // Close bell dropdown on click outside
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) {
        setBellOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Close nav dropdowns on click outside
  useEffect(() => {
    function handleClick() { setOpenDropdown(null); }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const publicLinks: NavLink[] = [
    { to: '/stations', label: 'Stations', color: 'hover:text-white' },
    { to: '/guide', label: 'Program Guide', color: 'hover:text-orange-300' },
    { to: '/hosts', label: 'DJs', color: 'hover:text-purple-300' },
    { to: '/archives', label: 'Archives', color: 'hover:text-amber-300' },
  ];

  const standaloneLinks: NavLink[] = [
    { to: '/admin/dashboard', label: 'Dashboard', color: 'hover:text-yellow-300' },
  ];

  const navGroups: NavGroup[] = [
    {
      label: 'Content',
      color: 'text-cyan-400',
      hoverColor: 'hover:text-cyan-300',
      links: [
        { to: '/admin/assets', label: 'Library', color: 'hover:text-cyan-300' },
        { to: '/admin/categories', label: 'Categories', color: 'hover:text-teal-300' },
        { to: '/admin/playlists', label: 'Playlists', color: 'hover:text-indigo-300' },
        { to: '/admin/studio', label: 'Studio', color: 'hover:text-rose-300' },
        { to: '/admin/reviews', label: 'Reviews', color: 'hover:text-emerald-300' },
      ],
    },
    {
      label: 'Broadcast',
      color: 'text-orange-400',
      hoverColor: 'hover:text-orange-300',
      links: [
        { to: '/admin/schedules', label: 'Schedules', color: 'hover:text-orange-300' },
        { to: '/admin/rules', label: 'Rules', color: 'hover:text-purple-300' },
        { to: '/admin/holidays', label: 'Blackouts', color: 'hover:text-red-300' },
        { to: '/admin/live', label: 'Live Shows', color: 'hover:text-red-400' },
      ],
    },
    {
      label: 'Audience',
      color: 'text-green-400',
      hoverColor: 'hover:text-green-300',
      links: [
        { to: '/admin/listeners', label: 'Listeners', color: 'hover:text-green-300' },
        { to: '/admin/analytics', label: 'Analytics', color: 'hover:text-pink-300' },
        { to: '/admin/requests', label: 'Requests', color: 'hover:text-lime-300' },
        { to: '/guide', label: 'Program Guide', color: 'hover:text-orange-300' },
        { to: '/archives', label: 'Archives', color: 'hover:text-amber-300' },
        { to: '/hosts', label: 'DJs', color: 'hover:text-purple-300' },
      ],
    },
    {
      label: 'Settings',
      color: 'text-purple-400',
      hoverColor: 'hover:text-purple-300',
      links: [
        { to: '/admin/users', label: 'Users', color: 'hover:text-green-300' },
        { to: '/admin/sponsors', label: 'Sponsors', color: 'hover:text-yellow-300' },
        { to: '/admin/alerts', label: 'Alerts', color: 'hover:text-rose-300' },
        { to: '/admin/stations', label: 'Manage Stations', color: 'hover:text-cyan-300' },
        { to: '/admin/crm', label: 'CRM', color: 'hover:text-pink-300' },
      ],
    },
  ];

  const handleDropdownEnter = (label: string) => {
    if (dropdownTimeout.current) clearTimeout(dropdownTimeout.current);
    setOpenDropdown(label);
  };

  const handleDropdownLeave = () => {
    dropdownTimeout.current = setTimeout(() => setOpenDropdown(null), 150);
  };

  // Chevron down icon
  const ChevronDown = () => (
    <svg className="w-3 h-3 ml-0.5 opacity-60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
    </svg>
  );

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
            <div className="hidden md:flex items-center gap-3">
              {isAuthenticated ? (
                <>
                  {/* Standalone links */}
                  {standaloneLinks.map(link => (
                    <Link key={link.to} to={link.to}
                      className={`text-gray-400 ${link.color} transition`}>
                      {link.label}
                    </Link>
                  ))}

                  {/* Dropdown groups */}
                  {navGroups.map(group => (
                    <div key={group.label} className="relative"
                      onMouseEnter={() => handleDropdownEnter(group.label)}
                      onMouseLeave={handleDropdownLeave}>
                      <button
                        className={`flex items-center text-gray-400 ${group.hoverColor} transition`}
                        onClick={() => setOpenDropdown(openDropdown === group.label ? null : group.label)}
                      >
                        {group.label}
                        <ChevronDown />
                      </button>
                      {openDropdown === group.label && (
                        <div className="absolute left-0 top-full mt-1 min-w-[160px] bg-[#12123a] border border-[#2a2a5e] rounded-lg shadow-xl z-50 py-1 overflow-hidden">
                          {group.links.map(link => (
                            <Link key={link.to} to={link.to}
                              onClick={() => setOpenDropdown(null)}
                              className={`block px-4 py-1.5 text-[13px] text-gray-400 ${link.color} hover:bg-[#1a1a4e] transition`}>
                              {link.label}
                            </Link>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}

                  {/* Stations (public) */}
                  <Link to="/stations" className="text-gray-400 hover:text-white transition">
                    Stations
                  </Link>
                </>
              ) : (
                <>
                  {publicLinks.map(link => (
                    <Link key={link.to} to={link.to}
                      className={`text-gray-400 ${link.color} transition`}>
                      {link.label}
                    </Link>
                  ))}
                </>
              )}
            </div>
          </div>

          <div className="flex items-center gap-3">
            {isAuthenticated ? (
              <>
                {/* Alert Bell */}
                <div className="relative" ref={bellRef}>
                  <button onClick={() => setBellOpen(!bellOpen)}
                    className="relative text-gray-400 hover:text-white p-1 transition">
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                    </svg>
                    {(unresolvedCount ?? 0) > 0 && (
                      <span className="absolute -top-1 -right-1 bg-red-600 text-white text-[9px] font-bold w-4 h-4 flex items-center justify-center rounded-full">
                        {unresolvedCount! > 9 ? '9+' : unresolvedCount}
                      </span>
                    )}
                  </button>

                  {bellOpen && (
                    <div className="absolute right-0 top-full mt-1 w-80 bg-[#12123a] border border-[#2a2a5e] rounded-lg shadow-xl z-50 overflow-hidden">
                      <div className="px-3 py-2 border-b border-[#2a2a5e] flex items-center justify-between">
                        <span className="text-[11px] text-gray-400 font-bold uppercase">Alerts</span>
                        {(unresolvedCount ?? 0) > 0 && (
                          <span className="text-[10px] bg-red-700 text-white px-1.5 py-0.5 rounded-full">{unresolvedCount} unresolved</span>
                        )}
                      </div>
                      {(recentAlerts?.alerts?.length ?? 0) === 0 ? (
                        <div className="px-3 py-4 text-center text-gray-600 text-[11px]">No unresolved alerts</div>
                      ) : (
                        <div className="max-h-64 overflow-y-auto">
                          {recentAlerts?.alerts?.map(alert => (
                            <button key={alert.id}
                              onClick={() => { setBellOpen(false); navigate('/admin/alerts'); }}
                              className="w-full text-left px-3 py-2 hover:bg-[#1a1a4e] border-b border-[#1a1a3e] last:border-0 transition">
                              <div className="flex items-center gap-2">
                                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                                  alert.severity === 'critical' ? 'bg-red-400' :
                                  alert.severity === 'warning' ? 'bg-amber-400' : 'bg-blue-400'
                                }`} />
                                <span className="text-[12px] text-cyan-300 truncate flex-1">{alert.title}</span>
                                <span className="text-[10px] text-gray-600 flex-shrink-0">
                                  {(() => {
                                    const d = Date.now() - new Date(alert.created_at).getTime();
                                    const m = Math.floor(d / 60000);
                                    if (m < 1) return 'now';
                                    if (m < 60) return `${m}m`;
                                    const h = Math.floor(m / 60);
                                    if (h < 24) return `${h}h`;
                                    return `${Math.floor(h / 24)}d`;
                                  })()}
                                </span>
                              </div>
                            </button>
                          ))}
                        </div>
                      )}
                      <button onClick={() => { setBellOpen(false); navigate('/admin/alerts'); }}
                        className="w-full px-3 py-2 text-center text-[11px] text-cyan-400 hover:bg-[#1a1a4e] border-t border-[#2a2a5e] transition">
                        View All Alerts
                      </button>
                    </div>
                  )}
                </div>

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
            {/* Standalone links */}
            {standaloneLinks.map(link => (
              <Link key={link.to} to={link.to}
                onClick={() => setMenuOpen(false)}
                className={`block py-2 px-3 text-sm text-gray-400 ${link.color} transition rounded hover:bg-[#1a1a4e]`}>
                {link.label}
              </Link>
            ))}

            {/* Grouped links */}
            {navGroups.map(group => (
              <div key={group.label}>
                <div className={`px-3 pt-3 pb-1 text-[10px] font-bold uppercase tracking-wider ${group.color}`}>
                  {group.label}
                </div>
                {group.links.map(link => (
                  <Link key={link.to} to={link.to}
                    onClick={() => setMenuOpen(false)}
                    className={`block py-2 px-3 pl-5 text-sm text-gray-400 ${link.color} transition rounded hover:bg-[#1a1a4e]`}>
                    {link.label}
                  </Link>
                ))}
              </div>
            ))}

            {/* Stations */}
            <Link to="/stations"
              onClick={() => setMenuOpen(false)}
              className="block py-2 px-3 text-sm text-gray-400 hover:text-white transition rounded hover:bg-[#1a1a4e]">
              Stations
            </Link>

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
