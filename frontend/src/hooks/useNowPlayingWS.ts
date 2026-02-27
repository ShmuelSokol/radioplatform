import { useEffect, useRef, useState } from 'react';

export interface NowPlayingData {
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
    audio_url?: string;
    cue_in?: number;
    cue_out?: number;
    cross_start?: number;
    replay_gain_db?: number;
  };
  next_asset?: {
    id: string;
    title?: string;
    artist?: string;
    audio_url?: string;
    cue_in?: number;
    replay_gain_db?: number;
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
  const usingPolling = useRef(false);
  // Stable ref to the connect function — updated each render but only called on stationId change
  const connectFnRef = useRef<() => void>(() => {});
  const maxReconnectAttempts = 5;

  // Keep connectFnRef up-to-date without causing effect re-runs
  connectFnRef.current = () => {
    if (!stationId) return;

    const clearTimers = () => {
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
    };

    const startPollingFallback = () => {
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
      pollTimer.current = setInterval(poll, 3000);
    };

    const connect = () => {
      // Use dedicated WS URL if set, otherwise derive from API URL
      const wsBase = import.meta.env.VITE_WS_URL;
      const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
      let wsUrl: string;

      if (wsBase) {
        wsUrl = `${wsBase}/api/v1/ws/now-playing/${stationId}`;
      } else if (apiUrl.startsWith('http')) {
        wsUrl = apiUrl.replace(/^http/, 'ws') + '/ws/now-playing/' + stationId;
      } else {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        wsUrl = `${protocol}//${window.location.host}${apiUrl}/ws/now-playing/${stationId}`;
      }

      try {
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          setIsConnected(true);
          reconnectAttempts.current = 0;
          usingPolling.current = false;
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

          if (reconnectAttempts.current < maxReconnectAttempts) {
            const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 30000);
            reconnectAttempts.current++;
            reconnectTimer.current = setTimeout(connect, delay);
          } else {
            startPollingFallback();
          }
        };

        wsRef.current = ws;
      } catch {
        startPollingFallback();
      }
    };

    clearTimers();
    connect();
  };

  useEffect(() => {
    if (!stationId) return;

    reconnectAttempts.current = 0;
    connectFnRef.current();

    return () => {
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [stationId]); // Only re-run when stationId changes

  return { nowPlaying, isConnected };
};
