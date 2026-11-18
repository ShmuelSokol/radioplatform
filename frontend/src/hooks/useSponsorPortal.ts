import { useQuery } from '@tanstack/react-query';
import { getPlayHistory, getUpcomingSchedule, getSponsorStats, getMySpots, getMissedPlays } from '../api/sponsorPortal';

export function usePlayHistory(page = 1, limit = 25) {
  return useQuery({
    queryKey: ['sponsor-play-history', page, limit],
    queryFn: () => getPlayHistory(page, limit),
  });
}

export function useUpcomingSchedule() {
  return useQuery({
    queryKey: ['sponsor-upcoming-schedule'],
    queryFn: getUpcomingSchedule,
  });
}

export function useSponsorStats() {
  return useQuery({
    queryKey: ['sponsor-stats'],
    queryFn: getSponsorStats,
  });
}

export function useMySpots() {
  return useQuery({
    queryKey: ['sponsor-my-spots'],
    queryFn: getMySpots,
  });
}

export function useMissedPlays() {
  return useQuery({
    queryKey: ['sponsor-missed-plays'],
    queryFn: getMissedPlays,
  });
}
