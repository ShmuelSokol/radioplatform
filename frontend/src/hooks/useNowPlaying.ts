import { useQuery } from '@tanstack/react-query';
import { getNowPlaying } from '../api/stations';

export function useNowPlaying(stationId: string | null) {
  return useQuery({
    queryKey: ['now-playing', stationId],
    queryFn: () => getNowPlaying(stationId!),
    enabled: !!stationId,
    refetchInterval: 5000,
  });
}
