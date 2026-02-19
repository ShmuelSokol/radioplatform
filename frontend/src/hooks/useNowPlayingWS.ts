import { useEffect, useRef, useState } from 'react';

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
 * TODO: Backend WebSocket endpoint not yet implemented.
 * For now, this is a placeholder that polls the REST API.
 */
export const useNowPlayingWS = (stationId: string) => {
  const [nowPlaying, setNowPlaying] = useState<NowPlayingData | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // TODO: Replace with actual WebSocket connection when backend implements it
    // const wsUrl = `${import.meta.env.VITE_WS_URL}/ws/now-playing/${stationId}`;
    // const ws = new WebSocket(wsUrl);
    // ws.onopen = () => setIsConnected(true);
    // ws.onmessage = (event) => setNowPlaying(JSON.parse(event.data));
    // ws.onerror = () => setIsConnected(false);
    // ws.onclose = () => setIsConnected(false);
    // wsRef.current = ws;

    // For now, poll the REST API every 10 seconds
    const pollInterval = setInterval(async () => {
      try {
        const response = await fetch(
          `${import.meta.env.VITE_API_URL}/now-playing/${stationId}`
        );
        if (response.ok) {
          const data = await response.json();
          setNowPlaying(data);
          setIsConnected(true);
        }
      } catch (error) {
        console.error('Failed to fetch now-playing:', error);
        setIsConnected(false);
      }
    }, 10000);

    return () => {
      clearInterval(pollInterval);
      wsRef.current?.close();
    };
  }, [stationId]);

  return { nowPlaying, isConnected };
};
