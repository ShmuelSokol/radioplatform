import { useState } from 'react';
import { useAddComment } from '../../hooks/useReviews';

interface CommentBoxProps {
  assetId: string;
}

export default function CommentBox({ assetId }: CommentBoxProps) {
  const [comment, setComment] = useState('');
  const addComment = useAddComment();

  const handleSubmit = () => {
    if (!comment.trim()) return;
    addComment.mutate(
      { assetId, comment: comment.trim() },
      { onSuccess: () => setComment('') }
    );
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">Add Comment</h3>
      <textarea
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder="Write a comment..."
        className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
        rows={2}
      />
      <button
        onClick={handleSubmit}
        disabled={!comment.trim() || addComment.isPending}
        className="mt-1 bg-brand-600 hover:bg-brand-700 text-white px-3 py-1 rounded text-xs transition disabled:opacity-50"
      >
        {addComment.isPending ? 'Posting...' : 'Post Comment'}
      </button>
    </div>
  );
}
