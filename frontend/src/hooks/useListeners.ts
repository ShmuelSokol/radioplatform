import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useCallback } from 'react';
import {
  getLiveListeners,
  getTodayStats,
  getListenerHistory,
  getListenerRegions,
  sendHeartbeat,
  sendDisconnect,
} from '../api/listeners';

// ── Admin hooks ──

export function useLiveListeners(refetchInterval = 10_000) {
  return useQuery({
    queryKey: ['listeners-live'],
    queryFn: getLiveListeners,
    refetchInterval,
  });
}

export function useTodayStats(refetchInterval = 30_000) {
  return useQuery({
    queryKey: ['listeners-today'],
    queryFn: getTodayStats,
    refetchInterval,
  });
}

export function useListenerHistory(days = 30) {
  return useQuery({
    queryKey: ['listeners-history', days],
    queryFn: () => getListenerHistory(days),
  });
}

export function useListenerRegions(days = 7) {
  return useQuery({
    queryKey: ['listeners-regions', days],
    queryFn: () => getListenerRegions(days),
  });
}

// ── Public heartbeat hook ──

function generateSessionKey(): string {
  return `ls_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

export function useListenerHeartbeat(stationId: string | undefined, isListening: boolean) {
  const sessionKeyRef = useRef<string>('');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Generate session key once per component mount
  if (!sessionKeyRef.current) {
    sessionKeyRef.current = generateSessionKey();
  }

  const doHeartbeat = useCallback(() => {
    if (stationId && sessionKeyRef.current) {
      sendHeartbeat(stationId, sessionKeyRef.current).catch(() => {});
    }
  }, [stationId]);

  const doDisconnect = useCallback(() => {
    if (stationId && sessionKeyRef.current) {
      sendDisconnect(stationId, sessionKeyRef.current).catch(() => {});
    }
  }, [stationId]);

  useEffect(() => {
    if (!isListening || !stationId) {
      // Stop heartbeat and notify disconnect
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      doDisconnect();
      return;
    }

    // Start heartbeating
    doHeartbeat(); // immediate first heartbeat
    intervalRef.current = setInterval(doHeartbeat, 30_000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      doDisconnect();
    };
  }, [isListening, stationId, doHeartbeat, doDisconnect]);
}
