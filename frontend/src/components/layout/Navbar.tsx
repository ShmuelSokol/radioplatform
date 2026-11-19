import { useState, useRef, useEffect } from 'react';
import { REPORT_BUG_EVENT, BugTopButton } from '../BugReporter';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { useUnresolvedCount, useAlerts } from '../../hooks/useAlerts';
import { useAuth } from '../../hooks/useAuth';
import { canAccess, isManager } from '../../auth/access';

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
  const { isAuthenticated, logout } = useAuthStore();
  const { user } = useAuth();
  const role = user?.role;
  // Alerts are a manager screen; other roles would only get refusals.
  const showAlerts = isAuthenticated && isManager(role);
  const [menuOpen, setMenuOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const bellRef = useRef<HTMLDivElement>(null);
  const dropdownTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();
  // Staff pages wear the KBR Studio look (styles/qnet-site.css); listener pages keep the dark bar.
  const qnet = useLocation().pathname.startsWith('/admin');

  const { data: unresolvedCount } = useUnresolvedCount(showAlerts);
  const { data: recentAlerts } = useAlerts(showAlerts ? { limit: 5, is_resolved: false } : null);

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
    { to: '/guide', label: 'Schedule', color: 'hover:text-orange-300' },
    { to: '/hosts', label: 'DJs', color: 'hover:text-purple-300' },
    { to: '/archives', label: 'Archives', color: 'hover:text-amber-300' },
  ];

  // Only offer links to screens this person's role can open.
  const allowed = (link: NavLink) => canAccess(role, link.to);

  const standaloneLinks: NavLink[] = [
    { to: '/admin/dashboard', label: 'Dashboard', color: 'hover:text-yellow-300' },
    // The portal is the sponsors' own screen; staff never get it (it only sent them back to the Dashboard).
    ...(role === 'sponsor' ? [{ to: '/sponsor/dashboard', label: 'Sponsor Portal', color: 'hover:text-indigo-300' }] : []),
  ].filter(allowed);

  const allNavGroups: NavGroup[] = [
    {
      // Its own menu, first after Dashboard: Shmuel looked for the client list
      // under Settings among 14 other entries and could not find it (2026-11-18).
      label: 'Sponsors',
      color: 'text-yellow-400',
      hoverColor: 'hover:text-yellow-300',
      links: [
        { to: '/admin/sponsors', label: 'All Clients', color: 'hover:text-yellow-300' },
        { to: '/admin/spot-schedule', label: 'Spot Schedule', color: 'hover:text-yellow-300' },
        { to: '/admin/spot-recordings', label: 'Record Spots', color: 'hover:text-yellow-300' },
        { to: '/admin/campaigns', label: 'Campaigns', color: 'hover:text-yellow-300' },
        { to: '/admin/sponsorship-rules', label: 'Sponsorship Rules', color: 'hover:text-yellow-300' },
      ],
    },
    {
      label: 'Content',
      color: 'text-cyan-400',
      hoverColor: 'hover:text-cyan-300',
      links: [
        { to: '/admin/assets', label: 'Library', color: 'hover:text-cyan-300' },
        { to: '/admin/databank', label: 'Databank', color: 'hover:text-yellow-300' },
        { to: '/admin/categories', label: 'Categories', color: 'hover:text-teal-300' },
        { to: '/admin/asset-types', label: 'Types', color: 'hover:text-sky-300' },
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
        { to: '/admin/program-blocks', label: 'Program Blocks', color: 'hover:text-amber-300' },
        { to: '/admin/holiday-music', label: 'Holiday Music', color: 'hover:text-amber-300' },
        { to: '/admin/schedule-check', label: 'Schedule Check', color: 'hover:text-amber-300' },
        { to: '/admin/rules', label: 'Rules', color: 'hover:text-purple-300' },
        { to: '/admin/holidays', label: 'Blackouts', color: 'hover:text-red-300' },
        { to: '/admin/program-clock', label: 'Program Clock', color: 'hover:text-cyan-300' },
        { to: '/admin/rds', label: 'RDS', color: 'hover:text-green-300' },
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
        // "Public Schedule": beside Broadcast's Schedules, a plain "Schedule" would be mistaken for it
        { to: '/guide', label: 'Public Schedule', color: 'hover:text-orange-300' },
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
        { to: '/admin/activity', label: 'Activity', color: 'hover:text-green-300' },
        { to: '/admin/alerts', label: 'Alerts', color: 'hover:text-rose-300' },
        { to: '/admin/emergency', label: 'Emergency Alerts', color: 'hover:text-rose-300' },
        { to: '/admin/bug-reports', label: 'Bug Reports', color: 'hover:text-rose-300' },
        { to: '/admin/site-updates', label: 'Site Updates', color: 'hover:text-cyan-300' },
        { to: '/admin/stations', label: 'Manage Stations', color: 'hover:text-cyan-300' },
        { to: '/admin/crm', label: 'CRM', color: 'hover:text-pink-300' },
      ],
    },
  ];

  const navGroups = allNavGroups
    .map(g => ({ ...g, links: g.links.filter(allowed) }))
    .filter(g => g.links.length > 0);

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
    <nav data-site-nav className={`sticky top-0 z-50 bg-[#0a0a28] text-white border-b border-[#2a2a5e] ${qnet ? 'qn-site' : ''}`}>
      <div className="px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-12">
          {/* Logo + desktop links */}
          <div className="flex items-center gap-4 text-sm">
            <Link to="/" className="qn-brand inline-flex items-center min-h-[44px] text-lg font-bold tracking-tight text-cyan-300">
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
                        <div
                          onMouseDown={(e) => e.stopPropagation()}
                          className="absolute left-0 top-full mt-1 min-w-[160px] bg-[#12123a] border border-[#2a2a5e] rounded-lg shadow-xl z-50 py-1 overflow-hidden">
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
                {/* Phones: report a bug, one tap, same place on every staff page */}
                {/* an open menu closes first, so the screenshot shows the page */}
                <span className="contents" onClickCapture={() => setMenuOpen(false)}><BugTopButton className="text-gray-300" /></span>
                {/* Alert Bell */}
                {showAlerts && <div className="relative" ref={bellRef}>
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
                </div>}

                <span className="text-[11px] text-gray-500 hidden sm:inline">{user?.email}</span>
                <button onClick={logout}
                  className="text-[11px] bg-red-900 hover:bg-red-800 text-red-300 px-2 py-0.5 rounded transition hidden md:inline-block">
                  Logout
                </button>
              </>
            ) : (
              <Link to="/admin/login"
                className="inline-flex items-center min-h-[44px] text-xs bg-[#2a2a5e] hover:bg-[#3a3a7e] text-cyan-300 px-3 rounded transition">
                Admin Login
              </Link>
            )}
            {/* Hamburger button - shown on mobile (also to visitors: the public links are hidden on phones) */}
            {(
              <button onClick={() => setMenuOpen(!menuOpen)} aria-label="Menu"
                className="md:hidden inline-flex items-center justify-center min-w-[44px] min-h-[44px] text-gray-400 hover:text-white p-1">
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
        <div className="md:hidden border-t border-[#2a2a5e] bg-[#0c0c30] max-h-[calc(100dvh-3rem)] overflow-y-auto">
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

            <button type="button"
              onClick={() => { setMenuOpen(false); window.dispatchEvent(new CustomEvent(REPORT_BUG_EVENT)); }}
              className="w-full min-h-[44px] flex items-center text-left py-2 px-3 text-sm text-gray-400 hover:text-white transition rounded hover:bg-[#1a1a4e]">
              🐞 Report a bug
            </button>

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

      {/* Mobile menu for visitors who are not signed in */}
      {menuOpen && !isAuthenticated && (
        <div className="md:hidden border-t border-[#2a2a5e] bg-[#0c0c30]">
          <div className="px-4 py-2 space-y-1">
            {publicLinks.map(link => (
              <Link key={link.to} to={link.to}
                onClick={() => setMenuOpen(false)}
                className={`block py-2 px-3 text-sm text-gray-400 ${link.color} transition rounded hover:bg-[#1a1a4e]`}>
                {link.label}
              </Link>
            ))}
          </div>
        </div>
      )}
    </nav>
  );
}
