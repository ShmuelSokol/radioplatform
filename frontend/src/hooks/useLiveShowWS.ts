import { useEffect, useRef, useState, useCallback } from 'react';
import type { LiveShow, CallInRequest } from '../api/liveShows';

interface LiveShowWSState {
  show: LiveShow | null;
  callers: CallInRequest[];
  secondsRemaining: number | null;
  isConnected: boolean;
}

/**
 * WebSocket hook for real-time live show updates.
 * Connects via WebSocket with automatic reconnection.
 * Falls back to REST polling if WebSocket fails repeatedly.
 */
export function useLiveShowWS(showId: string | undefined): LiveShowWSState {
  const [show, setShow] = useState<LiveShow | null>(null);
  const [callers, setCallers] = useState<CallInRequest[]>([]);
  const [secondsRemaining, setSecondsRemaining] = useState<number | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const maxReconnectAttempts = 5;

  const clearTimers = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const startPollingFallback = useCallback(() => {
    if (pollTimer.current || !showId) return;

    const poll = async () => {
      try {
        const token = localStorage.getItem('access_token');
        const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
        const headers: Record<string, string> = {};
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const [showRes, callsRes] = await Promise.all([
          fetch(`${apiUrl}/live-shows/${showId}`, { headers }),
          fetch(`${apiUrl}/live-shows/${showId}/calls`, { headers }),
        ]);
        if (showRes.ok) {
          const showData = await showRes.json();
          setShow(showData);
        }
        if (callsRes.ok) {
          const callsData = await callsRes.json();
          setCallers(callsData.calls || []);
        }
        setIsConnected(true);
      } catch {
        setIsConnected(false);
      }
    };

    poll();
    pollTimer.current = setInterval(poll, 5000);
  }, [showId]);

  const connectWebSocket = useCallback(() => {
    if (!showId) return;

    const wsBase = import.meta.env.VITE_WS_URL;
    const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
    const token = localStorage.getItem('access_token') || '';
    let wsUrl: string;

    if (wsBase) {
      wsUrl = `${wsBase}/api/v1/ws/live/${showId}/events?token=${token}`;
    } else if (apiUrl.startsWith('http')) {
      wsUrl = apiUrl.replace(/^http/, 'ws') + `/ws/live/${showId}/events?token=${token}`;
    } else {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${protocol}//${window.location.host}${apiUrl}/ws/live/${showId}/events?token=${token}`;
    }

    try {
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setIsConnected(true);
        reconnectAttempts.current = 0;
        if (pollTimer.current) {
          clearInterval(pollTimer.current);
          pollTimer.current = null;
        }
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          switch (message.type) {
            case 'show_state':
              setShow(message.data.show);
              setCallers(message.data.callers || []);
              setSecondsRemaining(message.data.seconds_remaining ?? null);
              break;
            case 'time_remaining':
              setSecondsRemaining(message.data.seconds ?? null);
              break;
            case 'caller_queued':
              setCallers(prev => [...prev, message.data]);
              break;
            case 'caller_updated':
              setCallers(prev =>
                prev.map(c =>
                  c.id === message.data.call_id
                    ? { ...c, ...message.data }
                    : c
                )
              );
              break;
            case 'caller_removed':
              setCallers(prev => prev.filter(c => c.id !== message.data.call_id));
              break;
            case 'show_started':
              setShow(prev => prev ? { ...prev, status: 'live' } : prev);
              break;
            case 'show_ended':
            case 'show_hard_stopped':
              setShow(prev => prev ? { ...prev, status: 'ended' } : prev);
              break;
            case 'ping':
              ws.send('pong');
              break;
          }
        } catch {
          // Ignore malformed messages
        }
      };

      ws.onerror = () => setIsConnected(false);

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;

        if (reconnectAttempts.current < maxReconnectAttempts) {
          const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 30000);
          reconnectAttempts.current++;
          reconnectTimer.current = setTimeout(connectWebSocket, delay);
        } else {
          startPollingFallback();
        }
      };

      wsRef.current = ws;
    } catch {
      startPollingFallback();
    }
  }, [showId, startPollingFallback]);

  useEffect(() => {
    if (!showId) return;

    connectWebSocket();

    return () => {
      clearTimers();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [showId, connectWebSocket, clearTimers]);

  return { show, callers, secondsRemaining, isConnected };
}
