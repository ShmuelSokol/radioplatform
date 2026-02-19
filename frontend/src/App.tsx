import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/layout/Layout';
import ErrorBoundary from './components/ErrorBoundary';
import { useAuthStore } from './stores/authStore';

// Public pages (eager - fast initial load)
import StationList from './pages/public/StationList';
import Listen from './pages/public/Listen';
import Login from './pages/admin/Login';

// Admin pages (lazy-loaded - code split)
const Dashboard = lazy(() => import('./pages/admin/Dashboard'));
const Stations = lazy(() => import('./pages/admin/Stations'));
const Assets = lazy(() => import('./pages/admin/Assets'));
const AssetUpload = lazy(() => import('./pages/admin/AssetUpload'));
const Users = lazy(() => import('./pages/admin/Users'));
const Rules = lazy(() => import('./pages/admin/Rules'));
const Schedules = lazy(() => import('./pages/admin/Schedules'));
const ScheduleBlocks = lazy(() => import('./pages/admin/ScheduleBlocks'));
const Holidays = lazy(() => import('./pages/admin/Holidays'));
const Sponsors = lazy(() => import('./pages/admin/Sponsors'));
const Analytics = lazy(() => import('./pages/admin/Analytics'));
const AssetDetail = lazy(() => import('./pages/admin/AssetDetail'));
const ReviewQueues = lazy(() => import('./pages/admin/ReviewQueues'));
const ReviewFlow = lazy(() => import('./pages/admin/ReviewFlow'));

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

const Loading = () => <div className="text-center py-10 text-gray-400">Loading...</div>;

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ErrorBoundary>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route element={<Layout />}>
              {/* Public */}
              <Route path="/" element={<Navigate to="/stations" replace />} />
              <Route path="/stations" element={<StationList />} />
              <Route path="/listen/:stationId" element={<Listen />} />

              {/* Auth */}
              <Route path="/admin/login" element={<Login />} />

              {/* Admin (protected, lazy-loaded) */}
              <Route path="/admin/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
              <Route path="/admin/stations" element={<ProtectedRoute><Stations /></ProtectedRoute>} />
              <Route path="/admin/assets" element={<ProtectedRoute><Assets /></ProtectedRoute>} />
              <Route path="/admin/assets/upload" element={<ProtectedRoute><AssetUpload /></ProtectedRoute>} />
              <Route path="/admin/assets/:assetId" element={<ProtectedRoute><AssetDetail /></ProtectedRoute>} />
              <Route path="/admin/reviews" element={<ProtectedRoute><ReviewQueues /></ProtectedRoute>} />
              <Route path="/admin/reviews/:queueId" element={<ProtectedRoute><ReviewFlow /></ProtectedRoute>} />
              <Route path="/admin/users" element={<ProtectedRoute><Users /></ProtectedRoute>} />
              <Route path="/admin/rules" element={<ProtectedRoute><Rules /></ProtectedRoute>} />
              <Route path="/admin/schedules" element={<ProtectedRoute><Schedules /></ProtectedRoute>} />
              <Route path="/admin/schedules/:scheduleId/blocks" element={<ProtectedRoute><ScheduleBlocks /></ProtectedRoute>} />
              <Route path="/admin/holidays" element={<ProtectedRoute><Holidays /></ProtectedRoute>} />
              <Route path="/admin/sponsors" element={<ProtectedRoute><Sponsors /></ProtectedRoute>} />
              <Route path="/admin/analytics" element={<ProtectedRoute><Analytics /></ProtectedRoute>} />
            </Route>
          </Routes>
        </Suspense>
        </ErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
