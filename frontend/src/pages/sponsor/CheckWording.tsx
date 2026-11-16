import { useQuery } from '@tanstack/react-query';
import { getSponsorshipRules } from '../../api/sponsorship';
import CopyReviewPanel from '../../components/CopyReviewPanel';
import Spinner from '../../components/Spinner';

export default function CheckWording() {
  const { data: rules, isLoading } = useQuery({
    queryKey: ['sponsorship-rules'],
    queryFn: getSponsorshipRules,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-800">Check your wording</h1>
        <p className="text-gray-600 mt-1">
          Kol B'ramah airs sponsorship announcements, not advertisements. Write what you would like
          said and we will show you what can stay, what has to change, and why.
        </p>
      </div>

      <div className="bg-white rounded-xl shadow p-5">
        <CopyReviewPanel askSponsorName />
      </div>

      <div className="bg-white rounded-xl shadow p-5">
        <h2 className="text-lg font-semibold text-gray-800 mb-2">The station's rules</h2>
        {isLoading ? (
          <Spinner />
        ) : (
          <p className="text-sm text-gray-700 whitespace-pre-wrap">{rules?.rules_text}</p>
        )}
      </div>
    </div>
  );
}
