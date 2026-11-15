import { lazy, Suspense, useEffect, useRef } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/layout/Layout';
import SponsorLayout from './components/layout/SponsorLayout';
import ErrorBoundary from './components/ErrorBoundary';
import { useAuth } from './hooks/useAuth';
import { canAccess, homeFor } from './auth/access';
import { applyUpdateOnNavigation } from './lib/appUpdate';
import { useUiStore } from './stores/uiStore';

// Auth pages (eager - fast initial load)
import Login from './pages/admin/Login';
import SponsorLogin from './pages/sponsor/Login';

// Public pages (lazy-loaded)
const StationList = lazy(() => import('./pages/public/StationList'));
const Listen = lazy(() => import('./pages/public/Listen'));
const StationListV2 = lazy(() => import('./pages/public/StationListV2'));
const ListenV2 = lazy(() => import('./pages/public/ListenV2'));

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
const SpotSchedules = lazy(() => import('./pages/admin/SpotSchedules'));
const SpotRecordings = lazy(() => import('./pages/admin/SpotRecordings'));
const ProgramBlocks = lazy(() => import('./pages/admin/ProgramBlocks'));
const ScheduleCheck = lazy(() => import('./pages/admin/ScheduleCheck'));
const SponsorshipRules = lazy(() => import('./pages/admin/SponsorshipRules'));
const Analytics = lazy(() => import('./pages/admin/Analytics'));
const AssetDetail = lazy(() => import('./pages/admin/AssetDetail'));
const ReviewQueues = lazy(() => import('./pages/admin/ReviewQueues'));
const ReviewFlow = lazy(() => import('./pages/admin/ReviewFlow'));
const Playlists = lazy(() => import('./pages/admin/Playlists'));
const Categories = lazy(() => import('./pages/admin/Categories'));
const AssetTypes = lazy(() => import('./pages/admin/AssetTypes'));
const Alerts = lazy(() => import('./pages/admin/Alerts'));
const LiveShows = lazy(() => import('./pages/admin/LiveShows'));
const HostConsole = lazy(() => import('./pages/admin/HostConsole'));
const CallScreener = lazy(() => import('./pages/admin/CallScreener'));
const SongRequests = lazy(() => import('./pages/admin/SongRequests'));
const AdminArchives = lazy(() => import('./pages/admin/Archives'));
const RecordingStudio = lazy(() => import('./pages/admin/RecordingStudio'));
const AudioEnhance = lazy(() => import('./pages/admin/AudioEnhance'));
const LiveListeners = lazy(() => import('./pages/admin/LiveListeners'));
const CrmDashboard = lazy(() => import('./pages/admin/CrmDashboard'));
const ProgramClock = lazy(() => import('./pages/admin/ProgramClock'));

// Public pages (lazy-loaded)
const ProgramGuide = lazy(() => import('./pages/public/ProgramGuide'));
const Hosts = lazy(() => import('./pages/public/Hosts'));
const Archives = lazy(() => import('./pages/public/Archives'));

// Sponsor pages (lazy-loaded)
const SponsorDashboard = lazy(() => import('./pages/sponsor/Dashboard'));
const SponsorCampaigns = lazy(() => import('./pages/sponsor/Campaigns'));
const SponsorCampaignDetail = lazy(() => import('./pages/sponsor/CampaignDetail'));
const SponsorBilling = lazy(() => import('./pages/sponsor/Billing'));
const SponsorCheckWording = lazy(() => import('./pages/sponsor/CheckWording'));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false },
  },
});

/**
 * Gate for signed-in screens. Sends anonymous visitors to the right login page
 * and sends a signed-in user who lacks the role for this screen to their own
 * home, instead of showing a screen that can only fail.
 */
function RoleRoute({ children, loginPath }: { children: React.ReactNode; loginPath: string }) {
  const { user, isLoading, isAuthenticated, refetch } = useAuth();
  const { pathname } = useLocation();
  if (!isAuthenticated) return <Navigate to={loginPath} replace />;
  if (!user) {
    if (isLoading) return <div className="text-center py-10 text-gray-400">Loading...</div>;
    // Signed in but the account lookup failed (server unreachable). Do not
    // throw a working operator back to the login page over a network blip.
    return (
      <div className="text-center py-10 text-gray-400">
        <p>Could not reach the server to confirm your account.</p>
        <button onClick={() => refetch()} className="mt-3 px-4 py-2 rounded bg-indigo-600 text-white hover:bg-indigo-500">
          Try again
        </button>
      </div>
    );
  }
  if (!canAccess(user.role, pathname)) return <Navigate to={homeFor(user.role)} replace />;
  return <>{children}</>;
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  return <RoleRoute loginPath="/admin/login">{children}</RoleRoute>;
}

function SponsorProtectedRoute({ children }: { children: React.ReactNode }) {
  return <RoleRoute loginPath="/sponsor/login">{children}</RoleRoute>;
}

/** Loads a newly published release when the person changes screens. */
function UpdateOnNavigation() {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    applyUpdateOnNavigation();
  }, [pathname]);
  return null;
}

const Loading = () => <div className="text-center py-10 text-gray-400">Loading...</div>;

/** Renders the V2 page when the V2 toggle is on, otherwise the V1 page. */
function Versioned({ v1, v2 }: { v1: React.ReactNode; v2: React.ReactNode }) {
  const isV2 = useUiStore((s) => s.version) === 'v2';
  return <>{isV2 ? v2 : v1}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <UpdateOnNavigation />
        <ErrorBoundary>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route element={<Layout />}>
              {/* Public */}
              <Route path="/" element={<Navigate to="/stations" replace />} />
              <Route path="/stations" element={<Versioned v1={<StationList />} v2={<StationListV2 />} />} />
              <Route path="/listen/:stationId" element={<Versioned v1={<Listen />} v2={<ListenV2 />} />} />
              <Route path="/guide" element={<ProgramGuide />} />
              <Route path="/hosts" element={<Hosts />} />
              <Route path="/archives" element={<Archives />} />

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
              <Route path="/admin/spot-schedule" element={<ProtectedRoute><SpotSchedules /></ProtectedRoute>} />
              <Route path="/admin/spot-recordings" element={<ProtectedRoute><SpotRecordings /></ProtectedRoute>} />
              <Route path="/admin/program-blocks" element={<ProtectedRoute><ProgramBlocks /></ProtectedRoute>} />
              <Route path="/admin/schedule-check" element={<ProtectedRoute><ScheduleCheck /></ProtectedRoute>} />
              <Route path="/admin/sponsorship-rules" element={<ProtectedRoute><SponsorshipRules /></ProtectedRoute>} />
              <Route path="/admin/playlists" element={<ProtectedRoute><Playlists /></ProtectedRoute>} />
              <Route path="/admin/categories" element={<ProtectedRoute><Categories /></ProtectedRoute>} />
              <Route path="/admin/asset-types" element={<ProtectedRoute><AssetTypes /></ProtectedRoute>} />
              <Route path="/admin/alerts" element={<ProtectedRoute><Alerts /></ProtectedRoute>} />
              <Route path="/admin/analytics" element={<ProtectedRoute><Analytics /></ProtectedRoute>} />
              <Route path="/admin/live" element={<ProtectedRoute><LiveShows /></ProtectedRoute>} />
              <Route path="/admin/live/:showId/host" element={<ProtectedRoute><HostConsole /></ProtectedRoute>} />
              <Route path="/admin/live/:showId/screen" element={<ProtectedRoute><CallScreener /></ProtectedRoute>} />
              <Route path="/admin/requests" element={<ProtectedRoute><SongRequests /></ProtectedRoute>} />
              <Route path="/admin/archives" element={<ProtectedRoute><AdminArchives /></ProtectedRoute>} />
              <Route path="/admin/audio-enhance/:assetId" element={<ProtectedRoute><AudioEnhance /></ProtectedRoute>} />
              <Route path="/admin/studio" element={<ProtectedRoute><RecordingStudio /></ProtectedRoute>} />
              <Route path="/admin/listeners" element={<ProtectedRoute><LiveListeners /></ProtectedRoute>} />
              <Route path="/admin/crm" element={<ProtectedRoute><CrmDashboard /></ProtectedRoute>} />
              <Route path="/admin/program-clock" element={<ProtectedRoute><ProgramClock /></ProtectedRoute>} />
            </Route>

            {/* Sponsor portal login (no layout) */}
            <Route path="/sponsor/login" element={<SponsorLogin />} />

            {/* Sponsor portal (own layout, protected) */}
            <Route element={<SponsorProtectedRoute><SponsorLayout /></SponsorProtectedRoute>}>
              <Route path="/sponsor/dashboard" element={<SponsorDashboard />} />
              <Route path="/sponsor/campaigns" element={<SponsorCampaigns />} />
              <Route path="/sponsor/campaigns/:id" element={<SponsorCampaignDetail />} />
              <Route path="/sponsor/billing" element={<SponsorBilling />} />
              <Route path="/sponsor/wording" element={<SponsorCheckWording />} />
            </Route>
          </Routes>
        </Suspense>
        </ErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
