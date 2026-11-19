import { useSearchParams } from 'react-router-dom';

/**
 * Shown on a login page reached because the sign-in ran out (api/client.ts adds
 * ?expired=1, or ?expired=action when the press that found it out was a change).
 */
export default function SignInExpiredNotice() {
  const [params] = useSearchParams();
  const expired = params.get('expired');
  if (!expired) return null;
  return (
    <div role="status" data-testid="sign-in-expired" className="bg-amber-50 text-amber-800 border border-amber-200 p-3 rounded text-sm">
      Your sign-in has expired. Please sign in again.
      {expired === 'action' && ' Your last action was not done, so do it again after signing in.'}
    </div>
  );
}
