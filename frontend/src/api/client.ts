import axios from 'axios';
import { newRequestId, recordRequest } from '../lib/diagnostics';
import { markDeleted } from '../lib/navMemory';

const API_BASE = import.meta.env.VITE_API_URL || '/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
});

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // The server logs each request under this id; a bug report carries the ids
  // of recent calls so the two logs can be matched (see lib/diagnostics.ts).
  const rid = newRequestId();
  config.headers['X-Request-ID'] = rid;
  // A file upload must go as multipart. With the default JSON content type axios
  // turns a FormData into JSON and the server answers 422 (Record Spots "Upload a
  // recording" never worked). Let the browser set multipart with its boundary.
  if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
    config.headers.delete('Content-Type');
  }
  (config as unknown as { _diag: { rid: string; t0: number } })._diag = { rid, t0: performance.now() };
  return config;
});

function recordAxios(config: unknown, status?: number, error?: string) {
  const c = config as { method?: string; url?: string; _diag?: { rid: string; t0: number } } | undefined;
  if (!c?._diag) return;
  recordRequest({
    method: (c.method || 'get').toUpperCase(),
    url: (c.url || '').slice(0, 200),
    status,
    ms: Math.round(performance.now() - c._diag.t0),
    rid: c._diag.rid,
    error,
  });
}

/**
 * One sign-in renewal at a time: when several requests fail with 401 together
 * (a page loading five lists), they all wait for the same /auth/refresh call
 * instead of each sending its own.
 */
let renewing: Promise<string> | null = null;
/** A change (not a read) was refused when the renewal failed: the login page says it was not done. */
let lostChange = false;
function renewSession(refreshToken: string): Promise<string> {
  if (!renewing) {
    renewing = axios
      .post(`${API_BASE}/auth/refresh`, { refresh_token: refreshToken })
      .then((res) => {
        localStorage.setItem('access_token', res.data.access_token);
        localStorage.setItem('refresh_token', res.data.refresh_token);
        return res.data.access_token as string;
      })
      .finally(() => {
        renewing = null;
      });
  }
  return renewing;
}

/** Fired on window when the server refuses an action this login may not take. */
export const FORBIDDEN_EVENT = 'kbr:forbidden';

apiClient.interceptors.response.use(
  (response) => {
    recordAxios(response.config, response.status);
    // Back never returns to a page about something just deleted (lib/navMemory.ts)
    if (response.config.method?.toLowerCase() === 'delete' && response.config.url) markDeleted(response.config.url);
    return response;
  },
  async (error) => {
    const originalRequest = error.config;
    recordAxios(originalRequest, error.response?.status, error.response ? undefined : String(error.message || error));
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;
      // Sent with a token that has since been renewed by another request: just retry
      const current = localStorage.getItem('access_token');
      const sent = originalRequest.headers?.Authorization;
      if (current && sent && sent !== `Bearer ${current}`) return apiClient(originalRequest);
      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken) {
        try {
          const accessToken = await renewSession(refreshToken);
          originalRequest.headers.Authorization = `Bearer ${accessToken}`;
          return apiClient(originalRequest);
        } catch {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          // Redirect to appropriate login based on current path, saying why: the
          // renewal has expired too (open overnight). A change (not a read) was not
          // done and is never sent later, so the login page says that as well.
          const isSponsor = window.location.pathname.startsWith('/sponsor');
          // Reads polling in the background fail at the same moment; whichever sends the
          // page last, a refused change still shows.
          if ((originalRequest.method ?? 'get').toLowerCase() !== 'get') lostChange = true;
          window.location.href = `${isSponsor ? '/sponsor/login' : '/admin/login'}?expired=${lostChange ? 'action' : '1'}`;
        }
      }
    }
    if (error.response?.status === 403 && (originalRequest?.method ?? 'get').toLowerCase() !== 'get') {
      window.dispatchEvent(new CustomEvent(FORBIDDEN_EVENT));
    }
    return Promise.reject(error);
  }
);

export default apiClient;
