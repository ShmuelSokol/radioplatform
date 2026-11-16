import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  cancelReleasePush,
  getOpenTabs,
  getPublishedVersion,
  getReleasePolicy,
  pushRelease,
  setReleaseMode,
} from '../../api/siteRelease';
import type { OpenTab } from '../../api/siteRelease';
import { APP_VERSION, onPolicy } from '../../lib/appUpdate';
import type { ReleasePolicy } from '../../lib/appUpdate';

const short = (v: string | null | undefined) => (v ? (v.length > 10 ? v.slice(0, 7) : v) : '—');

function pageName(path: string | null): string {
  if (!path) return '—';
  if (path === '/admin/dashboard') return 'Control room';
  if (path.startsWith('/listen/')) return 'Listen page';
  if (path === '/stations' || path === '/') return 'Station list';
  if (path.startsWith('/admin/')) return path.slice(7).replace(/-/g, ' ');
  return path;
}

function waitingText(tab: OpenTab, latest: string | null): string {
  if (latest && tab.version === latest) return 'Up to date';
  switch (tab.waiting) {
    case 'audio': return 'Waits until its audio stops';
    case 'next item': return 'Reloads at the next song change';
    case 'typing': return 'Waits until typing ends';
    case 'idle': return 'Reloads when idle';
    case 'admin push': return 'Waits for your push';
    default: return 'Older release';
  }
}

/** A datetime-local value for "now + minutes", in this browser's time zone. */
function localInput(minutesAhead: number): string {
  const d = new Date(Date.now() + minutesAhead * 60_000);
  d.setSeconds(0, 0);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function SiteUpdates() {
  const queryClient = useQueryClient();
  const { data: policy } = useQuery({ queryKey: ['site-release'], queryFn: getReleasePolicy, refetchInterval: 30_000 });
  const { data: latest } = useQuery({ queryKey: ['site-release-published'], queryFn: getPublishedVersion, refetchInterval: 20_000 });
  const { data: tabs = [] } = useQuery({ queryKey: ['site-release-tabs'], queryFn: getOpenTabs, refetchInterval: 5_000 });

  // The tab socket hears changes made elsewhere (another admin, a Claude thread)
  useEffect(() => onPolicy((p: ReleasePolicy) => queryClient.setQueryData(['site-release'], p)), [queryClient]);

  const [when, setWhen] = useState<'now' | 'later'>('now');
  const [at, setAt] = useState(() => localInput(60));
  const [includePlaying, setIncludePlaying] = useState(false);

  const refresh = (p: ReleasePolicy) => {
    queryClient.setQueryData(['site-release'], p);
    void queryClient.invalidateQueries({ queryKey: ['site-release-tabs'] });
  };
  const mode = useMutation({ mutationFn: setReleaseMode, onSuccess: refresh });
  const push = useMutation({
    mutationFn: () => pushRelease({
      version: latest ?? null,
      at: when === 'now' ? null : new Date(at).toISOString(),
      include_playing: includePlaying,
    }),
    onSuccess: refresh,
  });
  const cancel = useMutation({ mutationFn: cancelReleasePush, onSuccess: refresh });

  const counts = useMemo(() => {
    const old = tabs.filter((t) => latest && t.version !== latest);
    return {
      total: tabs.length,
      old: old.length,
      oldPlaying: old.filter((t) => t.playing).length,
      oldSafari: old.filter((t) => t.playing && /Safari|iPhone/.test(t.browser || '')).length,
    };
  }, [tabs, latest]);

  const activePush = policy?.push ?? null;
  const pushTime = activePush?.at ? new Date(activePush.at) : null;
  const pushFuture = pushTime ? pushTime.getTime() > Date.now() + 5_000 : false;
  const pushStale = !!(activePush?.version && latest && activePush.version !== latest);
  const error = (mode.error || push.error || cancel.error) as Error | null;

  return (
    <div className="w-full space-y-6" data-testid="site-updates">
      <div>
        <h1 className="text-3xl font-bold">Site Updates</h1>
        <p className="text-gray-600 mt-1">
          Choose when open browsers move onto the newest version of the site. Nobody has to refresh by hand.
          A tab playing the station is never cut off in the middle of a song.
        </p>
      </div>

      <div className="bg-white rounded-xl shadow p-5 grid gap-4 sm:grid-cols-3">
        <div>
          <div className="text-xs uppercase text-gray-500">Newest version published</div>
          <div className="text-xl font-mono" data-testid="published-version">{short(latest)}</div>
          <div className="text-xs text-gray-500">This tab runs {short(APP_VERSION)}{latest && APP_VERSION === latest ? ' (newest)' : ''}</div>
        </div>
        <div>
          <div className="text-xs uppercase text-gray-500">Open tabs</div>
          <div className="text-xl" data-testid="tab-count">{counts.total}</div>
          <div className="text-xs text-gray-500">{counts.old} on an older version, {counts.oldPlaying} of them playing</div>
        </div>
        <div>
          <div className="text-xs uppercase text-gray-500">Updates happen</div>
          <div className="flex gap-2 mt-1">
            {(['auto', 'manual'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => mode.mutate(m)}
                disabled={mode.isPending}
                data-testid={`mode-${m}`}
                className={`px-3 py-2 rounded-lg border text-sm min-h-[44px] ${policy?.mode === m ? 'bg-blue-600 text-white border-blue-600' : 'hover:bg-gray-50'}`}
              >
                {m === 'auto' ? 'Automatically' : 'Only when I push'}
              </button>
            ))}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {policy?.mode === 'manual'
              ? 'Tabs stay on their version until you push.'
              : 'Each tab updates itself when it is quiet: no audio, nobody typing.'}
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl shadow p-5 space-y-4">
        <h2 className="text-lg font-semibold">Push the newest version to everyone</h2>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 min-h-[44px]">
            <input type="radio" checked={when === 'now'} onChange={() => setWhen('now')} /> Now
          </label>
          <label className="flex items-center gap-2 min-h-[44px]">
            <input type="radio" checked={when === 'later'} onChange={() => setWhen('later')} /> At
          </label>
          <input
            type="datetime-local"
            value={at}
            onChange={(e) => { setAt(e.target.value); setWhen('later'); }}
            className="px-3 py-2 border rounded-lg min-h-[44px]"
          />
        </div>
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-1" checked={includePlaying} onChange={(e) => setIncludePlaying(e.target.checked)} data-testid="include-playing" />
          <span>
            Also tabs that are playing the station
            <span className="block text-sm text-gray-500">
              They reload right after the next song change (or within 5 minutes during a long shiur) and play on from where the
              station is, with a second or two of quiet. Chrome and Edge carry on by themselves; Safari and iPhone show
              "Tap to keep listening" until the person taps. Left off, playing tabs update when they stop.
              {counts.oldSafari > 0 && ` Right now ${counts.oldSafari} playing tab${counts.oldSafari === 1 ? ' is' : 's are'} on Safari or iPhone.`}
            </span>
          </span>
        </label>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => push.mutate()}
            disabled={push.isPending || !latest}
            data-testid="push-release"
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 min-h-[44px]"
          >
            {when === 'now' ? `Push ${short(latest)} now` : `Push ${short(latest)} at that time`}
          </button>
          {!latest && <span className="text-sm text-gray-500">Reading the published version…</span>}
        </div>

        {activePush && (
          <div className={`rounded-lg p-3 text-sm ${pushStale ? 'bg-gray-50' : 'bg-blue-50'}`} data-testid="active-push">
            <div>
              {pushFuture ? 'Scheduled' : 'Last push'}: version {short(activePush.version) === '—' ? 'newest' : short(activePush.version)}
              {pushTime && `, ${pushFuture ? 'at' : 'from'} ${pushTime.toLocaleString()}`}
              {activePush.include_playing ? ', including tabs that are playing' : ', playing tabs when they stop'}
              {activePush.pushed_by && ` (by ${activePush.pushed_by})`}.
              {pushStale && ' A newer version has been published since.'}
            </div>
            <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}
              className="mt-2 px-3 py-1 border rounded-lg hover:bg-white min-h-[44px]" data-testid="cancel-push">
              {pushFuture ? 'Cancel this push' : 'Clear'}
            </button>
          </div>
        )}
        {error && <div className="text-sm text-red-600">Could not save: {error.message}</div>}
      </div>

      <div className="bg-white rounded-xl shadow p-5">
        <h2 className="text-lg font-semibold mb-3">Open tabs</h2>
        {tabs.length === 0 ? (
          <p className="text-gray-500 text-sm">No open tabs right now.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="tabs-table">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="py-2 pr-3">Screen</th>
                  <th className="py-2 pr-3">Who</th>
                  <th className="py-2 pr-3">Browser</th>
                  <th className="py-2 pr-3">Version</th>
                  <th className="py-2 pr-3">Playing</th>
                  <th className="py-2">Update</th>
                </tr>
              </thead>
              <tbody>
                {tabs.map((t, i) => (
                  <tr key={`${t.client_id}-${i}`} className="border-b last:border-0">
                    <td className="py-2 pr-3 first-letter:uppercase">{pageName(t.page)}</td>
                    <td className="py-2 pr-3">{t.user ?? 'Listener'}</td>
                    <td className="py-2 pr-3">{t.browser ?? '—'}</td>
                    <td className={`py-2 pr-3 font-mono ${latest && t.version !== latest ? 'text-amber-700' : ''}`}>{short(t.version)}</td>
                    <td className="py-2 pr-3">{t.playing ? '🔊 Yes' : 'No'}</td>
                    <td className="py-2">{waitingText(t, latest ?? null)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
