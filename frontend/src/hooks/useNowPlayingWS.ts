import { useEffect, useRef, useState } from 'react';

export interface NowPlayingData {
  station_id: string;
  asset_id?: string;
  started_at: string;
  ends_at?: string;
  listener_count?: number;
  stream_url?: string;
  /** Present only on the message announcing an operator's Next: fade length of the outgoing item */
  change_fade_seconds?: number;
  asset?: {
    title: string;
    artist?: string;
    album?: string;
    album_art_path?: string;
    audio_url?: string;
    cue_in?: number;
    /** Intro length in seconds, counted from where the item starts on air (cue in) */
    intro_seconds?: number | null;
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
// Back from the background after this long: re-read now-playing and reconnect
const STALE_AFTER_HIDDEN_MS = 5_000;
// An item still shown this long after its end means the updates stopped
const OVERDUE_AFTER_MS = 6_000;
const OVERDUE_CHECK_MS = 4_000;
// Same default as the API client: without VITE_API_URL these reads went to
// "undefined/now-playing/..." and the polling fall-back, back-from-background and
// overdue re-reads never reached the server.
const API_BASE = import.meta.env.VITE_API_URL || '/api/v1';

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
  // Bumped on every station change. A socket, poll or reconnect timer from an
  // earlier station checks it and stops: the old socket's close handler used to
  // reconnect to the OLD station a second after a switch, so the control room got
  // both stations' now-playing and played both (heard live, 2026-11-16).
  const generation = useRef(0);

  // Keep connectFnRef up-to-date without causing effect re-runs
  connectFnRef.current = () => {
    if (!stationId) return;
    const myGeneration = generation.current;
    const current = () => generation.current === myGeneration;

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
        if (!current()) return;
        try {
          const response = await fetch(
            `${API_BASE}/now-playing/${stationId}`
          );
          if (response.ok) {
            const data = await response.json();
            if (!current()) return;
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
      if (!current()) return;
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
          if (!current()) { ws.close(); return; }
          setIsConnected(true);
          reconnectAttempts.current = 0;
          usingPolling.current = false;
          if (pollTimer.current) {
            clearInterval(pollTimer.current);
            pollTimer.current = null;
          }
        };

        ws.onmessage = (event) => {
          if (!current()) return;
          try {
            const message = JSON.parse(event.data);
            if (message.type === 'now_playing' && message.data) {
              setNowPlaying(message.data);
            }
            if (message.type === 'mix_over' && message.data) {
              // A jingle to play on top of what is on air; the audio engine picks it up
              window.dispatchEvent(new CustomEvent('kbr-mix-over', { detail: message.data }));
            }
            if (message.type === 'ping') {
              ws.send('pong');
            }
          } catch {
            // Ignore malformed messages
          }
        };

        ws.onerror = () => {
          if (current()) setIsConnected(false);
        };

        ws.onclose = () => {
          if (!current()) return; // a socket of a station no longer shown
          setIsConnected(false);
          if (wsRef.current === ws) wsRef.current = null;

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

    generation.current++;
    reconnectAttempts.current = 0;
    connectFnRef.current();

    return () => {
      generation.current++;
      // What was on air on the previous station is not on air on the next one
      setNowPlaying(null);
      setIsConnected(false);
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [stationId]); // Only re-run when stationId changes

  // A tab left in the background (screen locked, another tab in front) has its
  // timers throttled and its socket may have died without a close event. When it
  // comes back after more than a few seconds, read what is playing at once and
  // open a fresh connection, so it never shows an item that ended long ago.
  useEffect(() => {
    if (!stationId) return;
    let hiddenAt = document.hidden ? Date.now() : 0;
    const onVisibility = () => {
      if (document.hidden) { hiddenAt = Date.now(); return; }
      const away = hiddenAt ? Date.now() - hiddenAt : 0;
      hiddenAt = 0;
      if (away < STALE_AFTER_HIDDEN_MS) return;
      const gen = generation.current;
      fetch(`${API_BASE}/now-playing/${stationId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => { if (data && gen === generation.current) setNowPlaying(data); })
        .catch(() => {});
      const old = wsRef.current;
      if (old) { old.onclose = null; old.close(); wsRef.current = null; }
      reconnectAttempts.current = 0;
      connectFnRef.current();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [stationId]);

  // The item shown should have ended a while ago and nothing new came in: the socket
  // may have died without a close event (a phone in a car, screen locked: the
  // listener page kept showing the weather long after it ended). Read what is on
  // air and reconnect. Checked every few seconds while the item is overdue.
  const endsAt = nowPlaying?.ends_at;
  useEffect(() => {
    if (!stationId || !endsAt) return;
    const end = new Date(endsAt).getTime();
    if (!isFinite(end)) return;
    const gen = generation.current;
    const check = () => {
      if (Date.now() < end + OVERDUE_AFTER_MS || gen !== generation.current) return;
      fetch(`${API_BASE}/now-playing/${stationId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!data || gen !== generation.current) return;
          setNowPlaying((prev) => (prev && prev.started_at === data.started_at && prev.asset_id === data.asset_id ? prev : data));
        })
        .catch(() => {});
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        if (ws) { ws.onclose = null; ws.close(); wsRef.current = null; }
        reconnectAttempts.current = 0;
        connectFnRef.current();
      }
    };
    const timer = setInterval(check, OVERDUE_CHECK_MS);
    return () => clearInterval(timer);
  }, [stationId, endsAt]);

  return { nowPlaying, isConnected };
};
