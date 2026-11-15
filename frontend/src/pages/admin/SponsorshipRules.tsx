import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getSponsorshipRules,
  getSponsorshipRulesHistory,
  saveSponsorshipRules,
} from '../../api/sponsorship';
import CopyReviewPanel from '../../components/CopyReviewPanel';
import Spinner from '../../components/Spinner';

export default function SponsorshipRules() {
  const queryClient = useQueryClient();
  const { data: rules, isLoading } = useQuery({
    queryKey: ['sponsorship-rules'],
    queryFn: getSponsorshipRules,
  });
  const { data: history } = useQuery({
    queryKey: ['sponsorship-rules-history'],
    queryFn: getSponsorshipRulesHistory,
  });

  const [text, setText] = useState('');
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    if (rules) setText(rules.rules_text);
  }, [rules]);

  const save = useMutation({
    mutationFn: () => saveSponsorshipRules(text),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sponsorship-rules'] });
      queryClient.invalidateQueries({ queryKey: ['sponsorship-rules-history'] });
    },
  });

  if (isLoading) return <div className="p-8">Loading...</div>;
  const changed = rules ? text.trim() !== rules.rules_text.trim() : false;

  return (
    <div className="w-full space-y-8">
      <div>
        <h1 className="text-3xl font-bold">Sponsorship Rules</h1>
        <p className="text-gray-600 mt-1">
          What a sponsorship announcement may and may not say. The wording check reads these rules
          every time, so a change here applies to the very next check.
        </p>
      </div>

      <div className="bg-white rounded-xl shadow p-6 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Rules in force</h2>
          <span className="text-sm text-gray-500">
            {rules?.is_default
              ? 'Starting rules (never edited)'
              : `Version ${rules?.version}, saved ${
                  rules?.updated_at ? new Date(rules.updated_at).toLocaleString() : ''
                }`}
          </span>
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={18}
          className="w-full px-3 py-2 border rounded-lg font-mono text-sm"
        />
        <div className="flex items-center gap-3">
          <button
            onClick={() => save.mutate()}
            disabled={!changed || save.isPending || text.trim().length < 20}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {save.isPending ? <Spinner /> : 'Save rules'}
          </button>
          {changed && (
            <button
              onClick={() => rules && setText(rules.rules_text)}
              className="px-4 py-2 border rounded-lg hover:bg-gray-50"
            >
              Undo changes
            </button>
          )}
          {save.isSuccess && !changed && <span className="text-sm text-green-700">Saved.</span>}
          {save.isError && <span className="text-sm text-red-600">Could not save.</span>}
        </div>

        {!!history?.length && (
          <div className="pt-2">
            <button
              onClick={() => setShowHistory((v) => !v)}
              className="text-sm text-blue-600 hover:underline"
            >
              {showHistory ? 'Hide' : 'Show'} earlier versions ({history.length})
            </button>
            {showHistory && (
              <ul className="mt-3 space-y-3">
                {history.map((h) => (
                  <li key={h.version} className="border rounded-lg p-3">
                    <div className="flex items-center justify-between text-sm text-gray-500 mb-1">
                      <span>
                        Version {h.version}
                        {h.updated_at ? `, ${new Date(h.updated_at).toLocaleString()}` : ''}
                      </span>
                      <button
                        onClick={() => setText(h.rules_text)}
                        className="text-blue-600 hover:underline"
                      >
                        Load into editor
                      </button>
                    </div>
                    <p className="text-xs text-gray-600 whitespace-pre-wrap line-clamp-4">
                      {h.rules_text}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl shadow p-6">
        <h2 className="text-lg font-semibold mb-1">Try the wording check</h2>
        <p className="text-sm text-gray-600 mb-4">
          This is exactly what a sponsor sees. It uses the saved rules, not unsaved edits above.
        </p>
        <CopyReviewPanel askSponsorName />
      </div>
    </div>
  );
}
