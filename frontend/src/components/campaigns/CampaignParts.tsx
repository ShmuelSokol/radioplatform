import { useState } from 'react';
import { useDrafts, useCreateDraft, useComments, useCreateComment } from '../../hooks/useCampaigns';
import type { Campaign } from '../../api/campaigns';
import Spinner from '../Spinner';

/** Shared by the sponsor portal and the admin Campaigns screen. */

export const statusColors: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-700',
  in_review: 'bg-yellow-100 text-yellow-700',
  approved: 'bg-green-100 text-green-700',
  active: 'bg-blue-100 text-blue-700',
  paused: 'bg-orange-100 text-orange-700',
  completed: 'bg-purple-100 text-purple-700',
};

export const statusLabel = (s: string) => s.replace(/_/g, ' ');

export function StatusBadge({ status }: { status: string }) {
  return (
    <span data-testid="campaign-status" className={`px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap ${statusColors[status] || 'bg-gray-100 text-gray-600'}`}>
      {statusLabel(status)}
    </span>
  );
}

export const money = (cents: number) => `$${(cents / 100).toFixed(2)}`;

/** Start / end / budget line under a campaign's name. */
/** "Apr 1, 2027" from "2027-04-01" (a calendar day, no time zone involved) */
function niceDay(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function CampaignFacts({ c, className = '' }: { c: Campaign; className?: string }) {
  return (
    <div className={`flex flex-wrap gap-x-4 gap-y-1 ${className}`}>
      {c.start_date && <span>Start: {niceDay(c.start_date)}</span>}
      {c.end_date && <span>End: {niceDay(c.end_date)}</span>}
      {c.budget_cents != null && <span>Budget: {money(c.budget_cents)}</span>}
    </div>
  );
}

/** Versioned script drafts. Without canChange the list is shown with no New Draft button. */
export function CampaignDrafts({ campaignId, canChange }: { campaignId: string; canChange: boolean }) {
  const { data: drafts, isLoading } = useDrafts(campaignId);
  const createDraft = useCreateDraft();
  const [open, setOpen] = useState(false);
  const [scriptText, setScriptText] = useState('');
  const [notes, setNotes] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createDraft.mutateAsync({
      campaignId,
      data: { script_text: scriptText || undefined, notes: notes || undefined },
    });
    setScriptText('');
    setNotes('');
    setOpen(false);
  };

  return (
    <div className="bg-white rounded-xl shadow">
      <div className="px-4 sm:px-6 py-3 border-b flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-gray-800">Drafts</h2>
        {canChange && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            className="min-h-[44px] px-4 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm"
          >
            {open ? 'Cancel' : 'New Draft'}
          </button>
        )}
      </div>

      {canChange && open && (
        <form onSubmit={submit} className="p-4 sm:p-6 border-b space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Script</label>
            <textarea
              value={scriptText}
              onChange={(e) => setScriptText(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500"
              rows={4}
              placeholder="Write the sponsorship script here..."
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="w-full min-h-[44px] border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500"
              placeholder="Any notes for the team..."
            />
          </div>
          <button
            type="submit"
            disabled={createDraft.isPending}
            className="min-h-[44px] px-4 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm disabled:opacity-50"
          >
            {createDraft.isPending ? <><Spinner className="mr-2" />Saving...</> : 'Submit Draft'}
          </button>
        </form>
      )}

      {isLoading ? (
        <div className="p-6 text-center"><Spinner /></div>
      ) : !drafts?.length ? (
        <div className="p-6 text-center text-gray-400">No drafts yet</div>
      ) : (
        <div className="divide-y">
          {drafts.map((d) => (
            <div key={d.id} className="p-4 sm:p-6">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                <span className="text-sm font-medium text-indigo-600">Version {d.version}</span>
                <span className="text-xs text-gray-500">
                  by {d.user_display_name || d.user_email || 'Unknown'} &middot;{' '}
                  {new Date(d.created_at).toLocaleDateString()}
                </span>
              </div>
              {d.script_text && (
                <div className="bg-gray-50 rounded-lg p-4 text-sm text-gray-700 whitespace-pre-wrap break-words mb-2">
                  {d.script_text}
                </div>
              )}
              {d.audio_file_path && (
                <div className="text-sm text-gray-500 break-all">
                  Audio: <span className="text-indigo-600">{d.audio_file_path}</span>
                </div>
              )}
              {d.notes && <p className="text-sm text-gray-500 mt-1">Notes: {d.notes}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Comment thread. Without canChange there is no comment box. */
export function CampaignComments({ campaignId, canChange }: { campaignId: string; canChange: boolean }) {
  const { data: comments, isLoading } = useComments(campaignId);
  const createComment = useCreateComment();
  const [body, setBody] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!body.trim()) return;
    await createComment.mutateAsync({ campaignId, data: { body } });
    setBody('');
  };

  return (
    <div className="bg-white rounded-xl shadow">
      <div className="px-4 sm:px-6 py-3 border-b">
        <h2 className="text-lg font-semibold text-gray-800">Comments</h2>
      </div>

      {isLoading ? (
        <div className="p-6 text-center"><Spinner /></div>
      ) : !comments?.length ? (
        <div className="p-6 text-center text-gray-400">No comments yet</div>
      ) : (
        <div className="divide-y">
          {comments.map((c) => (
            <div key={c.id} className="p-4 sm:px-6">
              <div className="flex flex-wrap items-center gap-2 mb-1">
                <span className="text-sm font-medium text-gray-700">
                  {c.user_display_name || c.user_email || 'Unknown'}
                </span>
                <span className="text-xs text-gray-500">{new Date(c.created_at).toLocaleString()}</span>
              </div>
              <p className="text-sm text-gray-600 whitespace-pre-wrap break-words">{c.body}</p>
            </div>
          ))}
        </div>
      )}

      {canChange && (
        <form onSubmit={submit} className="p-4 sm:px-6 border-t flex gap-3">
          <input
            type="text"
            aria-label="Comment"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            className="flex-1 min-w-0 min-h-[44px] border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
            placeholder="Add a comment..."
            required
          />
          <button
            type="submit"
            disabled={createComment.isPending}
            className="min-h-[44px] px-4 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm disabled:opacity-50"
          >
            {createComment.isPending ? <Spinner /> : 'Send'}
          </button>
        </form>
      )}
    </div>
  );
}
