import { useEffect, useRef, useState, useCallback } from 'react';

interface NowPlayingData {
  station_id: string;
  asset_id?: string;
  started_at: string;
  ends_at?: string;
  listener_count?: number;
  stream_url?: string;
  asset?: {
    title: string;
    artist?: string;
    album?: string;
    album_art_path?: string;
  };
}

/**
 * WebSocket hook for real-time now-playing updates.
 * Connects via WebSocket with automatic reconnection.
 * Falls back to REST polling if WebSocket fails repeatedly.
 */
export const useNowPlayingWS = (stationId: string) => {
  const [nowPlaying, setNowPlaying] = useState<NowPlayingData | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const maxReconnectAttempts = 5;
  const usingPolling = useRef(false);

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
    if (pollTimer.current) return;
    usingPolling.current = true;

    const poll = async () => {
      try {
        const response = await fetch(
          `${import.meta.env.VITE_API_URL}/now-playing/${stationId}`
        );
        if (response.ok) {
          const data = await response.json();
          setNowPlaying(data);
          setIsConnected(true);
        }
      } catch {
        setIsConnected(false);
      }
    };

    poll();
    pollTimer.current = setInterval(poll, 10000);
  }, [stationId]);

  const connectWebSocket = useCallback(() => {
    if (!stationId) return;

    // Derive WS URL from the API URL
    const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
    let wsUrl: string;

    if (apiUrl.startsWith('http')) {
      // Absolute URL — convert http(s) to ws(s)
      wsUrl = apiUrl.replace(/^http/, 'ws') + '/ws/now-playing/' + stationId;
    } else {
      // Relative URL — use current host
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${protocol}//${window.location.host}${apiUrl}/ws/now-playing/${stationId}`;
    }

    try {
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setIsConnected(true);
        reconnectAttempts.current = 0;
        usingPolling.current = false;
        // Stop polling if we were using it as fallback
        if (pollTimer.current) {
          clearInterval(pollTimer.current);
          pollTimer.current = null;
        }
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'now_playing' && message.data) {
            setNowPlaying(message.data);
          }
          // Respond to server pings
          if (message.type === 'ping') {
            ws.send('pong');
          }
        } catch {
          // Ignore malformed messages
        }
      };

      ws.onerror = () => {
        setIsConnected(false);
      };

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;

        // Attempt reconnection with exponential backoff
        if (reconnectAttempts.current < maxReconnectAttempts) {
          const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 30000);
          reconnectAttempts.current++;
          reconnectTimer.current = setTimeout(connectWebSocket, delay);
        } else {
          // Fall back to REST polling after max reconnect attempts
          startPollingFallback();
        }
      };

      wsRef.current = ws;
    } catch {
      // WebSocket constructor failed — fall back to polling
      startPollingFallback();
    }
  }, [stationId, startPollingFallback]);

  useEffect(() => {
    if (!stationId) return;

    connectWebSocket();

    return () => {
      clearTimers();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [stationId, connectWebSocket, clearTimers]);

  return { nowPlaying, isConnected };
};
