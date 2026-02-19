import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/layout/Layout';
import StationList from './pages/public/StationList';
import Listen from './pages/public/Listen';
import Login from './pages/admin/Login';
import Dashboard from './pages/admin/Dashboard';
import Stations from './pages/admin/Stations';
import Assets from './pages/admin/Assets';
import AssetUpload from './pages/admin/AssetUpload';
import Users from './pages/admin/Users';
import Rules from './pages/admin/Rules';
import Schedules from './pages/admin/Schedules';
import Holidays from './pages/admin/Holidays';
import Sponsors from './pages/admin/Sponsors';
import Analytics from './pages/admin/Analytics';
import { useAuthStore } from './stores/authStore';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (!isAuthenticated) return <Navigate to="/admin/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            {/* Public */}
            <Route path="/" element={<Navigate to="/stations" replace />} />
            <Route path="/stations" element={<StationList />} />
            <Route path="/listen/:stationId" element={<Listen />} />

            {/* Auth */}
            <Route path="/admin/login" element={<Login />} />

            {/* Admin (protected) */}
            <Route path="/admin/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
            <Route path="/admin/stations" element={<ProtectedRoute><Stations /></ProtectedRoute>} />
            <Route path="/admin/assets" element={<ProtectedRoute><Assets /></ProtectedRoute>} />
            <Route path="/admin/assets/upload" element={<ProtectedRoute><AssetUpload /></ProtectedRoute>} />
            <Route path="/admin/users" element={<ProtectedRoute><Users /></ProtectedRoute>} />
            <Route path="/admin/rules" element={<ProtectedRoute><Rules /></ProtectedRoute>} />
            <Route path="/admin/schedules" element={<ProtectedRoute><Schedules /></ProtectedRoute>} />
            <Route path="/admin/holidays" element={<ProtectedRoute><Holidays /></ProtectedRoute>} />
            <Route path="/admin/sponsors" element={<ProtectedRoute><Sponsors /></ProtectedRoute>} />
            <Route path="/admin/analytics" element={<ProtectedRoute><Analytics /></ProtectedRoute>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
