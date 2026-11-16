import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useCallback } from 'react';
import {
  getLiveListeners,
  getTodayStats,
  getListenerHistory,
  getListenerRegions,
  sendHeartbeat,
  sendDisconnect,
  sendLeaveBeacon,
  listenersSocketUrl,
} from '../api/listeners';

// ── Admin hooks ──

// The server says "listeners changed" the moment someone joins or leaves, and
// this refetches the counts at once. Polling stays as a slow fallback (and for
// listeners whose phone just went away without saying so: they drop out after
// 90 s without a heartbeat).
export function useListenersPush() {
  const qc = useQueryClient();
  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 1_000;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    const refresh = () => {
      qc.invalidateQueries({ queryKey: ['listeners-live'] });
      qc.invalidateQueries({ queryKey: ['listeners-today'] });
    };
    const connect = () => {
      try { ws = new WebSocket(listenersSocketUrl()); } catch { return; }
      ws.onopen = () => { retry = 1_000; refresh(); };
      ws.onmessage = (e) => {
        try { if (JSON.parse(e.data).type === 'listeners') refresh(); } catch { /* ignore */ }
      };
      ws.onclose = () => {
        if (closed) return;
        timer = setTimeout(connect, retry);
        retry = Math.min(retry * 2, 30_000);
      };
    };
    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [qc]);
}

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

    // Closing the tab or the phone browser: an ordinary request is cut off, a
    // beacon still arrives, so the listener leaves the live count at once.
    // Coming back from the back/forward cache counts as joining again.
    const onHide = () => sendLeaveBeacon(stationId, sessionKeyRef.current);
    const onShow = (e: PageTransitionEvent) => { if (e.persisted) doHeartbeat(); };
    window.addEventListener('pagehide', onHide);
    window.addEventListener('pageshow', onShow);

    return () => {
      window.removeEventListener('pagehide', onHide);
      window.removeEventListener('pageshow', onShow);
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      doDisconnect();
    };
  }, [isListening, stationId, doHeartbeat, doDisconnect]);
}
