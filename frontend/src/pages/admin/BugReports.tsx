import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../../api/client';
import Spinner from '../../components/Spinner';
import { useAuthStore } from '../../stores/authStore';

/** Bug reports sent from the "Report a bug" button. See wiki/bug-reports.md. */

interface BugReport {
  id: string;
  number: number;
  status: string;
  created_at: string | null;
  reporter_name: string | null;
  station_id: string | null;
  description: string;
  expected: string | null;
  page_url: string | null;
  has_screenshot: boolean;
  github_url: string | null;
  fix_url: string | null;
  mirror_error: string | null;
}

const STATUS_LABEL: Record<string, [string, string]> = {
  new: ['New', 'bg-gray-700 text-gray-200'],
  mirrored: ['Being looked at', 'bg-blue-900 text-blue-200'],
  fix_ready: ['Fix ready', 'bg-emerald-900 text-emerald-200'],
  needs_owner: ['Needs you', 'bg-amber-900 text-amber-200'],
  fixed: ['Fixed', 'bg-emerald-800 text-emerald-100'],
  closed: ['Closed', 'bg-gray-800 text-gray-400'],
};

function Screenshot({ id }: { id: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let revoke: string | null = null;
    apiClient
      .get(`/bug-reports/${id}/screenshot`, { responseType: 'blob' })
      .then((r) => {
        revoke = URL.createObjectURL(r.data);
        setUrl(revoke);
      })
      .catch(() => setUrl(null));
    return () => { if (revoke) URL.revokeObjectURL(revoke); };
  }, [id]);
  if (!url) return <div className="h-24 w-40 rounded bg-white/5" />;
  return (
    <a href={url} target="_blank" rel="noreferrer">
      <img src={url} alt="Screenshot" className="w-full sm:w-56 max-h-48 object-cover object-top rounded ring-1 ring-white/10" />
    </a>
  );
}

export default function BugReports() {
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['bug-reports'],
    queryFn: async () => (await apiClient.get<{ reports: BugReport[] }>('/bug-reports')).data.reports,
  });
  const detail = useQuery({
    queryKey: ['bug-report', open],
    enabled: !!open,
    queryFn: async () => (await apiClient.get(`/bug-reports/${open}`)).data,
  });
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => apiClient.patch(`/bug-reports/${id}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['bug-reports'] }),
  });

  return (
    <div className="text-gray-100 bg-gray-950 rounded-2xl p-4 sm:p-6 min-h-[60vh]">
      <h1 className="text-2xl font-bold mb-1">Bug Reports</h1>
      <p className="text-sm text-gray-400 mb-4">
        Sent with the 🐞 button at the bottom left of every page. Each one keeps a screenshot, what the player was doing, and the server log from those minutes.
      </p>
      {isLoading ? (
        <Spinner />
      ) : !data?.length ? (
        <p className="text-gray-400">No reports yet.</p>
      ) : (
        <ul className="space-y-3">
          {data.map((r) => {
            const [label, cls] = STATUS_LABEL[r.status] ?? [r.status, 'bg-gray-700'];
            return (
              <li key={r.id} className="rounded-xl bg-gray-900 ring-1 ring-white/10 p-3 sm:p-4">
                <div className="flex flex-col sm:flex-row gap-3">
                  {r.has_screenshot && <Screenshot id={r.id} />}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-semibold">Bug {r.number}</span>
                      <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{label}</span>
                      <span className="text-gray-400">
                        {r.created_at ? new Date(r.created_at).toLocaleString() : ''} · {r.reporter_name || 'listener'}
                      </span>
                    </div>
                    <p className="mt-1 whitespace-pre-wrap break-words">{r.description}</p>
                    {r.expected && <p className="mt-1 text-sm text-gray-400">Expected: {r.expected}</p>}
                    {r.page_url && <p className="mt-1 text-xs text-gray-500 break-all">{r.page_url}</p>}
                    <div className="mt-2 flex flex-wrap gap-3 text-sm">
                      {r.fix_url && <a className="text-emerald-300 underline" href={r.fix_url} target="_blank" rel="noreferrer">See the fix</a>}
                      {r.github_url && <a className="text-blue-300 underline" href={r.github_url} target="_blank" rel="noreferrer">Plan and discussion</a>}
                      <button type="button" className="text-gray-300 underline" onClick={() => setOpen(open === r.id ? null : r.id)}>
                        {open === r.id ? 'Hide details' : 'Details'}
                      </button>
                      {isAdmin && !['fixed', 'closed'].includes(r.status) && (
                        <button type="button" className="text-gray-400 underline" onClick={() => setStatus.mutate({ id: r.id, status: 'closed' })}>
                          Close
                        </button>
                      )}
                    </div>
                    {r.mirror_error && <p className="mt-1 text-xs text-amber-400">Not sent for triage: {r.mirror_error.slice(0, 160)}</p>}
                    {open === r.id && (
                      <pre className="mt-3 max-h-96 overflow-auto rounded bg-black/40 p-2 text-[11px] leading-snug text-gray-300">
                        {detail.isLoading ? 'Loading…' : JSON.stringify(detail.data?.client_context, null, 1)?.slice(0, 60000)}
                      </pre>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
