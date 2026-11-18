import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { useAuth } from '../../hooks/useAuth';

const navItems = [
  { to: '/sponsor/dashboard', label: 'Dashboard' },
  { to: '/sponsor/campaigns', label: 'Campaigns' },
  { to: '/sponsor/wording', label: 'Check Wording' },
  { to: '/sponsor/billing', label: 'Billing' },
];

export default function SponsorLayout() {
  const { user } = useAuth();
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/sponsor/login');
  };

  return (
    <div className="qn-site min-h-screen flex flex-col bg-gray-50">
      {/* Top nav */}
      <nav className="bg-indigo-700 text-white shadow-md">
        {/* Wraps on a phone: in one row the links ran off the right edge (Billing and Logout unreachable) */}
        <div className="px-4 sm:px-6 lg:px-8 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2 min-h-14">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 min-w-0">
            <span className="font-bold text-lg text-indigo-200">Sponsor Portal</span>
            <div className="flex flex-wrap gap-1">
              {navItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded text-sm font-medium whitespace-nowrap transition ${
                      isActive
                        ? 'bg-indigo-800 text-white'
                        : 'text-indigo-100 hover:bg-indigo-600'
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <span className="hidden sm:inline text-sm text-indigo-200">{user?.display_name || user?.email}</span>
            <button
              onClick={handleLogout}
              className="text-sm text-indigo-200 hover:text-white transition"
            >
              Logout
            </button>
          </div>
        </div>
      </nav>

      {/* Content */}
      <main className="flex-1 w-full min-w-0 px-4 sm:px-6 lg:px-8 py-6">
        <Outlet />
      </main>
    </div>
  );
}
