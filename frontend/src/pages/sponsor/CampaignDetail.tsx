import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useCampaign, useDrafts, useCreateDraft, useComments, useCreateComment } from '../../hooks/useCampaigns';
import Spinner from '../../components/Spinner';

const statusColors: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-700',
  in_review: 'bg-yellow-100 text-yellow-700',
  approved: 'bg-green-100 text-green-700',
  active: 'bg-blue-100 text-blue-700',
  paused: 'bg-orange-100 text-orange-700',
  completed: 'bg-purple-100 text-purple-700',
};

export default function CampaignDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: campaign, isLoading } = useCampaign(id!);
  const { data: drafts, isLoading: draftsLoading } = useDrafts(id!);
  const { data: comments, isLoading: commentsLoading } = useComments(id!);

  const createDraft = useCreateDraft();
  const createComment = useCreateComment();

  const [showDraftForm, setShowDraftForm] = useState(false);
  const [scriptText, setScriptText] = useState('');
  const [draftNotes, setDraftNotes] = useState('');
  const [commentBody, setCommentBody] = useState('');

  if (isLoading) return <div className="text-center py-10"><Spinner /></div>;
  if (!campaign) return <div className="text-center py-10 text-gray-400">Campaign not found</div>;

  const handleCreateDraft = async (e: React.FormEvent) => {
    e.preventDefault();
    await createDraft.mutateAsync({
      campaignId: id!,
      data: { script_text: scriptText || undefined, notes: draftNotes || undefined },
    });
    setScriptText('');
    setDraftNotes('');
    setShowDraftForm(false);
  };

  const handleAddComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!commentBody.trim()) return;
    await createComment.mutateAsync({
      campaignId: id!,
      data: { body: commentBody },
    });
    setCommentBody('');
  };

  return (
    <div className="space-y-6">
      {/* Campaign Header */}
      <div className="bg-white rounded-xl shadow p-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-800">{campaign.name}</h1>
            {campaign.description && (
              <p className="text-gray-500 mt-1">{campaign.description}</p>
            )}
          </div>
          <span className={`px-3 py-1 rounded-full text-xs font-medium ${statusColors[campaign.status] || 'bg-gray-100'}`}>
            {campaign.status.replace('_', ' ')}
          </span>
        </div>
        <div className="flex gap-6 mt-4 text-sm text-gray-500">
          {campaign.start_date && <span>Start: {campaign.start_date}</span>}
          {campaign.end_date && <span>End: {campaign.end_date}</span>}
          {campaign.budget_cents != null && (
            <span>Budget: ${(campaign.budget_cents / 100).toFixed(2)}</span>
          )}
        </div>
      </div>

      {/* Drafts */}
      <div className="bg-white rounded-xl shadow">
        <div className="px-6 py-4 border-b flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-800">Ad Drafts</h2>
          <button
            onClick={() => setShowDraftForm(!showDraftForm)}
            className="px-3 py-1.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm"
          >
            {showDraftForm ? 'Cancel' : 'New Draft'}
          </button>
        </div>

        {showDraftForm && (
          <form onSubmit={handleCreateDraft} className="p-6 border-b space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Script / Ad Copy</label>
              <textarea
                value={scriptText}
                onChange={(e) => setScriptText(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500"
                rows={4}
                placeholder="Write your ad script here..."
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
              <input
                type="text"
                value={draftNotes}
                onChange={(e) => setDraftNotes(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500"
                placeholder="Any notes for the team..."
              />
            </div>
            <button
              type="submit"
              disabled={createDraft.isPending}
              className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm disabled:opacity-50"
            >
              {createDraft.isPending ? <><Spinner className="mr-2" />Saving...</> : 'Submit Draft'}
            </button>
          </form>
        )}

        {draftsLoading ? (
          <div className="p-6 text-center"><Spinner /></div>
        ) : !drafts?.length ? (
          <div className="p-6 text-center text-gray-400">No drafts yet</div>
        ) : (
          <div className="divide-y">
            {drafts.map((d) => (
              <div key={d.id} className="p-6">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-indigo-600">Version {d.version}</span>
                  <span className="text-xs text-gray-400">
                    by {d.user_display_name || d.user_email || 'Unknown'} &middot;{' '}
                    {new Date(d.created_at).toLocaleDateString()}
                  </span>
                </div>
                {d.script_text && (
                  <div className="bg-gray-50 rounded-lg p-4 text-sm text-gray-700 whitespace-pre-wrap mb-2">
                    {d.script_text}
                  </div>
                )}
                {d.audio_file_path && (
                  <div className="text-sm text-gray-500">
                    Audio: <span className="text-indigo-600">{d.audio_file_path}</span>
                  </div>
                )}
                {d.notes && (
                  <p className="text-sm text-gray-500 mt-1">Notes: {d.notes}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Comments */}
      <div className="bg-white rounded-xl shadow">
        <div className="px-6 py-4 border-b">
          <h2 className="text-lg font-semibold text-gray-800">Comments</h2>
        </div>

        {commentsLoading ? (
          <div className="p-6 text-center"><Spinner /></div>
        ) : !comments?.length ? (
          <div className="p-6 text-center text-gray-400">No comments yet</div>
        ) : (
          <div className="divide-y">
            {comments.map((c) => (
              <div key={c.id} className="p-4 px-6">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-medium text-gray-700">
                    {c.user_display_name || c.user_email || 'Unknown'}
                  </span>
                  <span className="text-xs text-gray-400">
                    {new Date(c.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="text-sm text-gray-600">{c.body}</p>
              </div>
            ))}
          </div>
        )}

        {/* Add comment */}
        <form onSubmit={handleAddComment} className="p-4 px-6 border-t flex gap-3">
          <input
            type="text"
            value={commentBody}
            onChange={(e) => setCommentBody(e.target.value)}
            className="flex-1 border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
            placeholder="Add a comment..."
            required
          />
          <button
            type="submit"
            disabled={createComment.isPending}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm disabled:opacity-50"
          >
            {createComment.isPending ? <Spinner /> : 'Send'}
          </button>
        </form>
      </div>
    </div>
  );
}
