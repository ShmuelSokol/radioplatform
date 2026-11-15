import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { reviewCopy, CopyReview } from '../api/sponsorship';
import Spinner from './Spinner';

/** Type proposed sponsorship wording, get back what has to change and why. */
export default function CopyReviewPanel({
  sponsorName,
  askSponsorName = false,
}: {
  sponsorName?: string;
  askSponsorName?: boolean;
}) {
  const [text, setText] = useState('');
  const [name, setName] = useState(sponsorName ?? '');
  const [result, setResult] = useState<CopyReview | null>(null);

  const review = useMutation({
    mutationFn: () => reviewCopy(text, name),
    onSuccess: setResult,
  });

  const useSuggestion = () => {
    if (result?.suggested_script) {
      setText(result.suggested_script);
      setResult(null);
    }
  };

  return (
    <div className="space-y-4">
      {askSponsorName && (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Business name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full sm:w-80 px-3 py-2 border rounded-lg"
            placeholder="Burger Bar"
          />
        </div>
      )}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          What would you like the announcement to say?
        </label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          className="w-full px-3 py-2 border rounded-lg"
          placeholder="Write it the way you would say it. We will show you what needs to change."
        />
      </div>
      <button
        onClick={() => review.mutate()}
        disabled={review.isPending || text.trim().length < 3}
        className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
      >
        {review.isPending ? <Spinner /> : 'Check my wording'}
      </button>
      {review.isError && (
        <p className="text-sm text-red-600">
          The check could not be run. Please try again in a minute.
        </p>
      )}

      {result && (
        <div className="space-y-4 border-t pt-4">
          <div
            className={`rounded-lg px-4 py-3 ${
              result.verdict === 'ok'
                ? 'bg-green-50 text-green-800 border border-green-200'
                : 'bg-amber-50 text-amber-900 border border-amber-200'
            }`}
          >
            <p className="font-semibold">
              {result.verdict === 'ok' ? 'This wording is fine.' : 'Some wording needs to change.'}
            </p>
            <p className="text-sm mt-1">{result.summary}</p>
            <p className="text-xs mt-2 opacity-80">
              About {Math.round(result.estimated_seconds)} seconds when read aloud
              {result.length_ok ? '.' : ', which is too long.'}{' '}
              {result.reviewed_by === 'ai'
                ? 'Reviewed by the AI assistant.'
                : 'Basic automatic check only (the AI review was not available).'}
            </p>
          </div>

          {result.issues.length > 0 && (
            <ul className="space-y-3">
              {result.issues.map((issue, i) => (
                <li key={i} className="bg-white border rounded-lg p-4">
                  {issue.quote && (
                    <p className="text-sm font-mono bg-red-50 text-red-800 inline-block px-2 py-0.5 rounded">
                      “{issue.quote}”
                    </p>
                  )}
                  <p className="text-sm text-gray-800 mt-2">{issue.problem}</p>
                  <p className="text-sm text-gray-600 mt-1">
                    <span className="font-medium">Instead: </span>
                    {issue.suggestion}
                  </p>
                </li>
              ))}
            </ul>
          )}

          {result.suggested_script && (
            <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4">
              <p className="text-sm font-semibold text-indigo-900 mb-2">Suggested wording</p>
              <p className="text-gray-800 whitespace-pre-wrap">{result.suggested_script}</p>
              <button
                onClick={useSuggestion}
                className="mt-3 text-sm px-3 py-1 border border-indigo-300 rounded-lg text-indigo-700 hover:bg-indigo-100"
              >
                Use this wording
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
