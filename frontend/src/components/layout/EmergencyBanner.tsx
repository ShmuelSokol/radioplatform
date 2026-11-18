import { useQuery } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import { getEmergencyActive } from '../../api/emergency';
import { useAuth } from '../../hooks/useAuth';

/** A red bar on every staff page while an emergency alert is on the air somewhere, so a
 * host or operator knows why the station is not playing what they expect. */
export default function EmergencyBanner() {
  const { pathname } = useLocation();
  const { user } = useAuth();
  const staff = !!user && user.role !== 'sponsor' && pathname.startsWith('/admin');
  const { data } = useQuery({
    queryKey: ['emergency-active'],
    queryFn: getEmergencyActive,
    enabled: staff,
    refetchInterval: 5000,
    retry: false,
  });
  const count = Object.keys(data?.on_air ?? {}).length;
  if (!staff || count === 0 || pathname === '/admin/emergency') return null;
  return (
    <div role="alert" data-testid="emergency-banner" className="bg-red-700 px-4 py-1.5 text-center text-[13px] font-bold text-white">
      EMERGENCY ALERT ON AIR{count > 1 ? ` on ${count} stations` : ''}. Programming carries on when it ends.{' '}
      <Link to="/admin/emergency" className="underline">Details</Link>
    </div>
  );
}
