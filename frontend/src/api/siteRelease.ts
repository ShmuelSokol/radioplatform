import apiClient from './client';
import type { ReleasePolicy } from '../lib/appUpdate';

export interface OpenTab {
  client_id: string | null;
  version: string | null;
  page: string | null;
  playing: boolean;
  browser: string | null;
  user: string | null;
  waiting: string | null;
  connected_seconds: number;
}

export const getReleasePolicy = () =>
  apiClient.get<ReleasePolicy>('/site-release').then((r) => r.data);

export const setReleaseMode = (mode: 'auto' | 'manual') =>
  apiClient.put<ReleasePolicy>('/site-release', { mode }).then((r) => r.data);

export const pushRelease = (body: { version: string | null; at: string | null; include_playing: boolean }) =>
  apiClient.post<ReleasePolicy>('/site-release/push', body).then((r) => r.data);

export const cancelReleasePush = () =>
  apiClient.delete<ReleasePolicy>('/site-release/push').then((r) => r.data);

export const getOpenTabs = () =>
  apiClient.get<{ tabs: OpenTab[] }>('/site-release/tabs').then((r) => r.data.tabs);

/** The release kbrlive.com serves right now. */
export async function getPublishedVersion(): Promise<string | null> {
  try {
    const res = await fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) return null;
    const body = await res.json();
    return typeof body?.version === 'string' ? body.version : null;
  } catch {
    return null;
  }
}
