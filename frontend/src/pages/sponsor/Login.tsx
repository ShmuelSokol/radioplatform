import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { login } from '../../api/auth';
import { useAuthStore } from '../../stores/authStore';
import Spinner from '../../components/Spinner';

export default function SponsorLogin() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const authLogin = useAuthStore((s) => s.login);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const tokens = await login(email, password);
      authLogin(tokens.access_token, tokens.refresh_token);
      navigate('/sponsor/dashboard');
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || 'Login failed';
      setError(typeof msg === 'string' ? msg : 'Invalid email or password');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-50 to-purple-50">
      <div className="w-full max-w-md">
        <h1 className="text-3xl font-bold mb-2 text-center text-indigo-700">Sponsor Portal</h1>
        <p className="text-center text-gray-500 mb-6">Sign in to manage your campaigns</p>
        <form onSubmit={handleSubmit} className="bg-white shadow-lg rounded-xl p-8 space-y-5">
          {error && (
            <div className="bg-red-50 text-red-600 p-3 rounded text-sm">{error}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input
              type="text"
              autoCapitalize="none"
              autoCorrect="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              required
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2.5 rounded-lg transition disabled:opacity-50"
          >
            {loading ? <><Spinner className="mr-2" />Signing in...</> : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}
