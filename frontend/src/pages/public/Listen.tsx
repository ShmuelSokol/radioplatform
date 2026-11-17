import { Link, useParams } from 'react-router-dom';
import PlayoutBadge from '../../components/audio/PlayoutBadge';
import { startLiveStream, type LiveStream } from '../../lib/liveStream';
import { useQuery } from '@tanstack/react-query';
import { useState, useEffect, useCallback, useRef } from 'react';
import { getStation } from '../../api/stations';
import apiClient from '../../api/client';
import { submitSongRequest, SongRequestSubmitResponse } from '../../api/songRequests';
import { useListenerHeartbeat } from '../../hooks/useListeners';
import {
  useCrmAuth, useRateSong, useActiveRaffles, useEnterRaffle, useMyRatings,
  crmErrorStatus as httpStatus, crmErrorDetail, crmLoginErrorText,
} from '../../hooks/useCrm';
import { useNowPlayingWS } from '../../hooks/useNowPlayingWS';
import { useAudioEngine } from '../../hooks/useAudioEngine';
import { useLiveAudio } from '../../hooks/useLiveAudio';
import { holdUpdates } from '../../lib/appUpdate';
import { serverNow } from '../../lib/serverClock';
import { ResumePrompt, useResumeAfterRelease } from '../../components/audio/ResumeAfterRelease';
import type { AssetInfo } from '../../types';

interface ActiveShowData {
  active: boolean;
  show: {
    id: string;
    title: string;
    description: string | null;
    broadcast_mode: string;
    icecast_mount: string | null;
    actual_start: string | null;
    scheduled_end: string | null;
    calls_enabled: boolean;
  } | null;
}

export default function Listen() {
  const { stationId } = useParams<{ stationId: string }>();
  const { data: station, isLoading, error: stationError } = useQuery({
    queryKey: ['station', stationId],
    queryFn: () => getStation(stationId!),
    enabled: !!stationId,
    // A station that does not exist is final; a network error or a 5xx is tried
    // again twice, quickly, and then the page says it cannot reach the server
    retry: (count, err) => httpStatus(err) !== 404 && httpStatus(err) !== 422 && count < 2,
    retryDelay: 1000,
  });

  // Real-time now-playing via WebSocket (instant track changes, cue points, next track)
  const { nowPlaying: wsNowPlaying, isConnected: wsConnected } = useNowPlayingWS(stationId ?? '');

  const [activeShow, setActiveShow] = useState<ActiveShowData | null>(null);
  const [userStarted, setUserStarted] = useState(false);

  // Song request form state
  const [requestOpen, setRequestOpen] = useState(false);
  const [reqName, setReqName] = useState('');
  const [reqSong, setReqSong] = useState('');
  const [reqArtist, setReqArtist] = useState('');
  const [reqMessage, setReqMessage] = useState('');
  const [reqSubmitting, setReqSubmitting] = useState(false);
  const [reqResult, setReqResult] = useState<SongRequestSubmitResponse | null>(null);
  const [reqError, setReqError] = useState('');

  // CRM state
  const crm = useCrmAuth();
  const rateMutation = useRateSong();
  const { data: activeRaffles } = useActiveRaffles(stationId);
  const enterRaffleMutation = useEnterRaffle();
  const [crmPinInput, setCrmPinInput] = useState('');
  const [crmRegOpen, setCrmRegOpen] = useState(false);
  const [crmRegName, setCrmRegName] = useState('');
  const [crmRegPhone, setCrmRegPhone] = useState('');
  const [crmRegEmail, setCrmRegEmail] = useState('');
  const [crmRegResult, setCrmRegResult] = useState<string | null>(null);
  const [crmLoginError, setCrmLoginError] = useState('');
  const [crmRegError, setCrmRegError] = useState('');
  const [crmRegLoginFailed, setCrmRegLoginFailed] = useState(false);
  const [myRating, setMyRating] = useState(0);
  const [myFavorite, setMyFavorite] = useState(false);
  // Heart pressed on a song with no stars yet: saved with the first star picked
  // (a favourite alone used to be saved as a 5-star rating the listener never gave)
  const [favPending, setFavPending] = useState(false);
  const [rateError, setRateError] = useState('');
  const [raffleError, setRaffleError] = useState<{ id: string; text: string } | null>(null);
  const [enteredRaffles, setEnteredRaffles] = useState<Set<string>>(new Set());

  // The listener's own rating and favourite of the song on air, from their saved ratings
  const { data: myRatings } = useMyRatings(crm.isLoggedIn, crm.pin);
  const currentAssetId = wsNowPlaying?.asset_id;
  const savedRating = currentAssetId ? myRatings?.find((r) => String(r.asset_id) === String(currentAssetId)) : undefined;
  useEffect(() => {
    setMyRating(savedRating?.rating ?? 0);
    setMyFavorite(savedRating?.is_favorite ?? false);
    setFavPending(false);
    setRateError('');
  }, [currentAssetId, savedRating?.rating, savedRating?.is_favorite]);

  // Raffles this member entered, kept in this browser: the server's list does not
  // say, and after a reload the button offered "Enter Raffle" again
  const memberId = crm.profile?.id ?? null;
  const enteredKey = memberId ? `crm_raffles_entered:${memberId}` : null;
  useEffect(() => {
    if (!enteredKey) { setEnteredRaffles(new Set()); return; }
    try { setEnteredRaffles(new Set(JSON.parse(localStorage.getItem(enteredKey) || '[]') as string[])); }
    catch { setEnteredRaffles(new Set()); }
  }, [enteredKey]);
  const markEntered = (raffleId: string) => {
    setEnteredRaffles((prev) => {
      const next = new Set(prev).add(raffleId);
      if (enteredKey) { try { localStorage.setItem(enteredKey, JSON.stringify([...next])); } catch { /* storage off */ } }
      return next;
    });
  };

  const saveRating = (rating: number, isFavorite: boolean, before: { rating: number; favorite: boolean }) => {
    if (!currentAssetId) return;
    setRateError('');
    rateMutation.mutate(
      { asset_id: currentAssetId, rating, is_favorite: isFavorite },
      {
        onSuccess: () => { crm.refreshProfile().catch(() => {}); },
        onError: (err) => {
          // Put the stars back as they were and say so
          setMyRating(before.rating);
          setMyFavorite(before.favorite);
          setRateError(httpStatus(err) === 401 ? 'Please log in again to rate songs.' : 'Could not save your rating. Please try again.');
        },
      },
    );
  };

  // Icecast stream: direct MP3 stream via <audio> element
  const streamUrl = wsNowPlaying?.stream_url || null;
  const [streamFailed, setStreamFailed] = useState(false);
  const useStream = !!streamUrl && !streamFailed;
  const streamAudioRef = useRef<HTMLAudioElement>(null);
  // Sound really comes out of the stream element (Safari can refuse play() with no
  // error the page sees: "On Air" and Stop over silence)
  const [streamSounding, setStreamSounding] = useState(false);

  // The item on air; a stream that failed is tried again when it changes
  const itemKey = `${wsNowPlaying?.asset_id ?? ''}|${wsNowPlaying?.started_at ?? ''}|${streamUrl ?? ''}`;
  const itemKeyRef = useRef(itemKey);
  itemKeyRef.current = itemKey;
  const failedAtItem = useRef<string | null>(null);
  const onStreamFail = useCallback(() => {
    failedAtItem.current = itemKeyRef.current;
    setStreamFailed(true);
  }, []);

  // The stream playing now, and its URL (started inside the Listen click by handlePlay)
  const liveRef = useRef<{ url: string; stream: LiveStream } | null>(null);
  const stopLive = useCallback(() => {
    liveRef.current?.stream.stop();
    liveRef.current = null;
  }, []);
  const startLive = useCallback((url: string) => {
    const audio = streamAudioRef.current;
    if (!audio) return;
    stopLive();
    liveRef.current = { url, stream: startLiveStream(audio, url, onStreamFail) };
  }, [stopLive, onStreamFail]);

  // After a stream failure the built-in player carries on. When the item on air
  // changes, the stream is tried again, muted, beside it; once it really plays the
  // page goes back to it. (One engine or Icecast restart used to leave the listener
  // on the built-in player for the rest of the session, out of step with the stream.)
  const probeRef = useRef<{ url: string; stream: LiveStream } | null>(null);
  const stopProbe = useCallback(() => {
    probeRef.current?.stream.stop();
    probeRef.current = null;
    if (streamAudioRef.current) streamAudioRef.current.muted = false;
  }, []);
  useEffect(() => {
    if (!streamFailed || !userStarted || !streamUrl || probeRef.current) return;
    if (failedAtItem.current === itemKey) return;
    const audio = streamAudioRef.current;
    if (!audio) return;
    let done = false;
    const finish = () => { done = true; audio.removeEventListener('playing', onPlaying); };
    function onPlaying() {
      if (done) return;
      finish();
      liveRef.current = probeRef.current;
      probeRef.current = null;
      audio!.muted = false;
      failedAtItem.current = null;
      setStreamFailed(false);
    }
    failedAtItem.current = itemKey;
    audio.muted = true;
    audio.addEventListener('playing', onPlaying);
    probeRef.current = { url: streamUrl, stream: startLiveStream(audio, streamUrl, () => { if (!done) { finish(); stopProbe(); } }) };
    return () => { if (!done) { finish(); stopProbe(); } };
  }, [itemKey, streamFailed, userStarted, streamUrl, stopProbe]);

  useEffect(() => {
    if (!useStream || !userStarted || !streamUrl) { stopLive(); return; }
    if (liveRef.current?.url !== streamUrl) startLive(streamUrl);
  }, [useStream, userStarted, streamUrl, startLive, stopLive]);
  useEffect(() => stopLive, [stopLive]);

  // What the phone shows on the lock screen and in the car (CarPlay, Android Auto):
  // the item on air, kept current as it changes
  const nowTitle = wsNowPlaying?.asset?.title ?? null;
  const nowArtist = wsNowPlaying?.asset?.artist ?? null;
  useEffect(() => {
    if (!userStarted || !('mediaSession' in navigator) || typeof MediaMetadata === 'undefined') return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: nowTitle || station?.name || "Kol B'ramah",
        artist: nowArtist || station?.name || '',
        album: station?.name ?? '',
      });
    } catch { /* not supported */ }
  }, [userStarted, nowTitle, nowArtist, station?.name]);

  // Open the connection to the stream server before Listen is pressed (no audio is
  // fetched), so the press does not also wait for DNS and TLS
  useEffect(() => {
    if (!streamUrl) return;
    let origin: string;
    try { origin = new URL(streamUrl).origin; } catch { return; }
    const link = document.createElement('link');
    link.rel = 'preconnect';
    link.href = origin;
    document.head.appendChild(link);
    return () => { link.remove(); };
  }, [streamUrl]);

  // Listen for stream errors to trigger fallback
  useEffect(() => {
    const audio = streamAudioRef.current;
    if (!audio || !useStream) return;
    const onError = () => onStreamFail();
    audio.addEventListener('error', onError);
    return () => audio.removeEventListener('error', onError);
  }, [useStream, onStreamFail]);

  // Once Listen is pressed, a new release must not reload the page and silence it
  useEffect(() => {
    if (!userStarted) return;
    return holdUpdates();
  }, [userStarted]);

  // Track listener session (heartbeat every 30s while listening)
  useListenerHeartbeat(stationId, userStarted);

  // Derive playback state from WS data
  const isWsPlaying = !!wsNowPlaying?.asset_id && !!wsNowPlaying?.asset;
  // The station's stream is playing here: it is on air whatever the now-playing
  // record says (a lost record showed "Offline" over a playing stream, 2026-11-16)
  const streamPlaying = userStarted && useStream;
  const wsAsset = wsNowPlaying?.asset;
  const wsNextAsset = wsNowPlaying?.next_asset;

  // Compute elapsed from started_at (capped at ends_at)
  const [elapsed, setElapsed] = useState(0);
  const wsEndsAt = wsNowPlaying?.ends_at;
  useEffect(() => {
    if (!wsNowPlaying?.started_at) { setElapsed(0); return; }
    const startMs = new Date(wsNowPlaying.started_at).getTime();
    const maxDuration = wsEndsAt ? (new Date(wsEndsAt).getTime() - startMs) / 1000 : 0;
    const update = () => {
      const raw = Math.max(0, (serverNow() - startMs) / 1000);
      setElapsed(maxDuration > 0 ? Math.min(raw, maxDuration) : raw);
    };
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [wsNowPlaying?.started_at, wsEndsAt]);

  // Build AssetInfo for audio engine
  const audioAsset: AssetInfo | null = wsNowPlaying?.asset_id && wsAsset ? {
    id: wsNowPlaying.asset_id,
    title: wsAsset.title,
    artist: wsAsset.artist ?? null,
    asset_type: 'music',
    category: null,
    duration: null,
  } : null;

  // A live show on air: the host is heard instead of the music. The now-playing
  // message held when the show began is stale afterwards (the station's queue was
  // paused), so the music waits for a fresh one.
  const live = useLiveAudio(stationId ?? null, userStarted);
  const staleNowPlaying = useRef<typeof wsNowPlaying | undefined>(undefined);
  if (live.onAir && staleNowPlaying.current === undefined) staleNowPlaying.current = wsNowPlaying;
  if (!live.onAir && staleNowPlaying.current !== undefined && staleNowPlaying.current !== wsNowPlaying) {
    staleNowPlaying.current = undefined;
  }
  const musicAllowed = !live.onAir && staleNowPlaying.current === undefined;

  // Audio engine with crossfade support (fallback when HLS is not available)
  const {
    volume, setVolume, muted, toggleMute,
    audioReady, initAudio, soundRunning,
  } = useAudioEngine(
    userStarted && !useStream && musicAllowed ? audioAsset : null,
    elapsed,
    userStarted && isWsPlaying && !useStream && musicAllowed,
    // A hard timed item next (time check): pre-loaded and started here at its time
    useStream ? null : wsNextAsset?.cut_at ?? null,
    useStream ? null : (wsNextAsset?.cut_at ? wsNextAsset.id : null),
    2000,
    // Crossfade params from WS
    wsAsset?.cue_in ?? 0,
    wsAsset?.cue_out ?? 0,
    wsAsset?.cross_start ?? 0,
    wsAsset?.replay_gain_db ?? 0,
    // On the server stream the built-in player stays silent: it must not download the next item
    useStream ? null : wsNextAsset?.id ?? null,
    wsNextAsset?.cue_in ?? 0,
    wsNextAsset?.replay_gain_db ?? 0,
    // Direct audio URLs from WS (no auth needed for public listeners)
    wsAsset?.audio_url ?? null,
    useStream ? null : wsNextAsset?.audio_url ?? null,
    wsNowPlaying?.started_at ?? null,
    wsNowPlaying?.change_fade_seconds ?? null,
  );

  // Fetch active live show status (still uses polling — shows don't change often)
  const fetchActiveShow = useCallback(async () => {
    if (!stationId) return;
    try {
      const res = await apiClient.get<ActiveShowData>(`/live-shows/station/${stationId}/active`);
      setActiveShow(res.data);
    } catch {
      // Endpoint may not exist yet
    }
  }, [stationId]);

  useEffect(() => {
    if (!stationId) return;
    fetchActiveShow();
    const timer = setInterval(fetchActiveShow, 15000);
    return () => clearInterval(timer);
  }, [stationId, fetchActiveShow]);

  const setLiveLevel = live.setLevel;
  useEffect(() => { setLiveLevel(volume, muted); }, [setLiveLevel, volume, muted]);

  const handlePlay = async () => {
    live.unlock();
    // Start the stream inside the click: Safari refuses play() that a click did not
    // start, and the page then fell back to a player that was never switched on.
    // A stream that failed earlier is tried again on every press of Listen.
    if (streamUrl) {
      stopProbe();
      failedAtItem.current = null;
      setStreamFailed(false);
      startLive(streamUrl);
    }
    setUserStarted(true);
    // Always switch the built-in player on in the click too, so a fallback is heard
    await initAudio();
  };

  // An admin-pushed release reloaded this page while it played: play on. On the
  // server stream, what counts is that the stream element really plays: Safari
  // refuses a stream started outside a tap, and the page then showed Stop and
  // "On Air" over silence with no "Tap to keep listening".
  const resume = useResumeAfterRelease(handlePlay, useStream ? streamSounding : soundRunning);

  const handleStop = () => {
    setUserStarted(false);
    stopProbe();
    stopLive();
  };

  // The phone's own controls (lock screen, CarPlay, headphones) act like the page's
  // buttons. Left to the browser, their pause only paused the audio, and play then
  // went on from the moment it was paused instead of where the station is now.
  const playRef = useRef(handlePlay);
  const stopRef = useRef(handleStop);
  playRef.current = handlePlay;
  stopRef.current = handleStop;
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    const ms = navigator.mediaSession;
    const set = (action: MediaSessionAction, fn: MediaSessionActionHandler | null) => {
      try { ms.setActionHandler(action, fn); } catch { /* action not supported */ }
    };
    set('play', () => { void playRef.current(); });
    set('pause', () => stopRef.current());
    set('stop', () => stopRef.current());
    // A live station cannot be wound back or forward
    set('seekbackward', null);
    set('seekforward', null);
    set('seekto', null);
    return () => { set('play', null); set('pause', null); set('stop', null); };
  }, []);
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    try { navigator.mediaSession.playbackState = userStarted ? 'playing' : 'paused'; } catch { /* not supported */ }
  }, [userStarted]);

  // Cleanup on unmount
  useEffect(() => {
    return () => { /* useAudioEngine handles its own cleanup */ };
  }, []);

  // Sync volume to stream audio element
  useEffect(() => {
    if (streamAudioRef.current) {
      streamAudioRef.current.volume = muted ? 0 : volume;
    }
  }, [volume, muted]);

  if (isLoading) return <div className="text-center py-10">Loading...</div>;
  if (!station) {
    const status = httpStatus(stationError);
    // Only a station the server says does not exist is "not found"; a network
    // error or a server fault is not the listener's wrong link
    if (stationError && status !== 404 && status !== 422) {
      return (
        <div className="text-center py-10" role="alert">
          <p>Cannot reach the server right now.</p>
          <p className="text-sm text-gray-500 mt-1">Check your connection and reload the page.</p>
        </div>
      );
    }
    return <div className="text-center py-10">Station not found</div>;
  }

  return (
    <div className="max-w-2xl mx-auto">
      {/* Hidden audio element for Icecast stream playback */}
      <audio
        ref={streamAudioRef}
        className="hidden"
        onPlaying={() => setStreamSounding(true)}
        onPause={() => setStreamSounding(false)}
        onEmptied={() => setStreamSounding(false)}
        onEnded={() => setStreamSounding(false)}
        onError={() => setStreamSounding(false)}
      />
      {resume.asking && <ResumePrompt onTap={resume.tap} />}
      <div className="bg-white shadow rounded-lg p-4 sm:p-8">
        <div className="flex items-center gap-4 sm:gap-6 mb-8">
          <div className="w-24 h-24 flex-shrink-0 bg-brand-50 rounded-lg flex items-center justify-center text-5xl text-brand-600">
            &#9835;
          </div>
          <div className="min-w-0">
            <h1 className="text-3xl font-bold break-words">{station.name}</h1>
            <p className="text-gray-500">{station.type} &middot; {station.timezone}</p>
            <PlayoutBadge stationId={stationId} className="mt-1" />
            {station.description && <p className="text-gray-400 mt-1">{station.description}</p>}
          </div>
        </div>

        {/* Live Show Banner */}
        {activeShow?.active && activeShow.show && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-center gap-3">
            <span className="w-3 h-3 bg-red-500 rounded-full animate-pulse flex-shrink-0" />
            <div className="flex-1">
              <div className="font-bold text-red-800">LIVE: {activeShow.show.title}</div>
              {activeShow.show.description && (
                <p className="text-sm text-red-600 mt-0.5">{activeShow.show.description}</p>
              )}
              {activeShow.show.calls_enabled && (
                <p className="text-xs text-red-500 mt-1">
                  Call-ins are open!{' '}
                  <Link to={`/call/${stationId}`} className="underline font-semibold">Call in from here</Link>
                </p>
              )}
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-4 mb-6">
          {userStarted ? (
            <button
              onClick={handleStop}
              className="bg-red-600 hover:bg-red-700 text-white px-8 py-3 rounded-full text-lg font-medium transition"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={handlePlay}
              className="bg-brand-600 hover:bg-brand-700 text-white px-8 py-3 rounded-full text-lg font-medium transition"
            >
              Listen Live
            </button>
          )}
          <span className={`px-3 py-1 rounded-full text-sm font-medium ${
            isWsPlaying || streamPlaying ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'
          }`}>
            {isWsPlaying || streamPlaying ? 'On Air' : wsConnected ? 'Offline' : 'Connecting...'}
          </span>
          {userStarted && useStream && (
            <span className="px-2 py-0.5 rounded-full text-xs bg-blue-100 text-blue-700">LIVE</span>
          )}
        </div>

        {/* Volume control */}
        {userStarted && (audioReady || useStream) && (
          <div className="flex items-center gap-3 mb-6">
            <button onClick={toggleMute} className="text-gray-400 text-sm min-w-[44px] min-h-[44px]" aria-label={muted ? 'Unmute' : 'Mute'}>
              {muted ? 'Mute' : 'Vol'}
            </button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={volume}
              onChange={(e) => setVolume(Number(e.target.value))}
              aria-label="Volume"
              className="flex-1 max-w-48 h-11"
            />
            <span className="text-gray-400 text-sm w-10 text-right">{Math.round(volume * 100)}%</span>
          </div>
        )}

        {isWsPlaying && wsAsset && (
          <div className="border-t pt-6">
            <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Now Playing</h3>
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-gray-100 rounded flex items-center justify-center text-2xl">
                &#9835;
              </div>
              <div className="flex-1">
                <p className="font-medium">{wsAsset.title}</p>
                {wsAsset.artist && (
                  <p className="text-sm text-gray-500">{wsAsset.artist}</p>
                )}
                {wsAsset.album && (
                  <p className="text-xs text-gray-400">{wsAsset.album}</p>
                )}
              </div>
            </div>
          </div>
        )}

        {!isWsPlaying && !streamPlaying && wsConnected && (
          <div className="border-t pt-6">
            <p className="text-gray-400">Nothing playing right now</p>
          </div>
        )}

        {/* Song Request Section */}
        <div className="border-t mt-6 pt-6">
          <button
            onClick={() => { setRequestOpen(!requestOpen); setReqResult(null); setReqError(''); }}
            className="flex items-center gap-2 min-h-[44px] text-brand-600 hover:text-brand-700 font-medium transition"
          >
            <svg className={`w-4 h-4 transition-transform ${requestOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            Request a Song
          </button>

          {requestOpen && (
            <div className="mt-4 space-y-3" id="song-request-form">
              {reqResult && (
                <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
                  {reqResult.auto_approved ? (
                    <>
                      Your song has been added to the queue! Playing: <strong>{reqResult.matched_asset_title}</strong>
                      {reqResult.matched_asset_artist && <> by {reqResult.matched_asset_artist}</>}.
                      {reqResult.songs_ahead != null && reqResult.estimated_wait_minutes != null && (
                        <> Will play in ~{reqResult.songs_ahead} song{reqResult.songs_ahead !== 1 ? 's' : ''} (~{reqResult.estimated_wait_minutes} min).</>
                      )}
                    </>
                  ) : reqResult.matched_asset_title ? (
                    <>
                      We found: <strong>{reqResult.matched_asset_title}</strong>
                      {reqResult.matched_asset_artist && <> by {reqResult.matched_asset_artist}</>}.
                      The station manager will review it shortly.
                    </>
                  ) : (
                    <>Your request has been submitted for review.</>
                  )}
                </div>
              )}
              {reqError && (
                <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
                  {reqError}
                </div>
              )}

              <div>
                <label htmlFor="req-name" className="block text-sm font-medium text-gray-700 mb-1">Your Name *</label>
                <input
                  type="text"
                  id="req-name"
                  value={reqName}
                  onChange={e => setReqName(e.target.value)}
                  placeholder="Enter your name"
                  className="w-full min-h-[44px] border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
                />
              </div>
              <div>
                <label htmlFor="req-song" className="block text-sm font-medium text-gray-700 mb-1">Song Title *</label>
                <input
                  type="text"
                  id="req-song"
                  value={reqSong}
                  onChange={e => setReqSong(e.target.value)}
                  placeholder="What song would you like to hear?"
                  className="w-full min-h-[44px] border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
                />
              </div>
              <div>
                <label htmlFor="req-artist" className="block text-sm font-medium text-gray-700 mb-1">Artist (optional)</label>
                <input
                  type="text"
                  id="req-artist"
                  value={reqArtist}
                  onChange={e => setReqArtist(e.target.value)}
                  placeholder="Artist name"
                  className="w-full min-h-[44px] border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
                />
              </div>
              <div>
                <label htmlFor="req-message" className="block text-sm font-medium text-gray-700 mb-1">Message (optional)</label>
                <textarea
                  id="req-message"
                  value={reqMessage}
                  onChange={e => setReqMessage(e.target.value)}
                  placeholder="Dedication or message..."
                  rows={2}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 resize-none"
                />
              </div>
              <button
                onClick={async () => {
                  if (!reqName.trim() || !reqSong.trim()) {
                    setReqError('Name and song title are required.');
                    return;
                  }
                  setReqSubmitting(true);
                  setReqError('');
                  try {
                    const result = await submitSongRequest({
                      station_id: stationId!,
                      requester_name: reqName.trim(),
                      song_title: reqSong.trim(),
                      song_artist: reqArtist.trim() || undefined,
                      requester_message: reqMessage.trim() || undefined,
                    });
                    setReqResult(result);
                    setReqName('');
                    setReqSong('');
                    setReqArtist('');
                    setReqMessage('');
                  } catch (err) {
                    const status = httpStatus(err);
                    setReqError(
                      status === 429 ? 'Too many requests from here just now. Please wait a minute and try again.'
                        : status === 404 ? 'This station could not be found, so the request was not sent.'
                          : status === undefined ? 'Could not reach the server. Please check your connection and try again.'
                            : 'Failed to submit request. Please try again.',
                    );
                  } finally {
                    setReqSubmitting(false);
                  }
                }}
                disabled={reqSubmitting}
                className="min-h-[44px] bg-brand-600 hover:bg-brand-700 text-white px-6 py-2 rounded-lg text-sm font-medium transition disabled:opacity-50"
              >
                {reqSubmitting ? 'Submitting...' : 'Submit Request'}
              </button>
            </div>
          )}
        </div>

        {/* ── CRM Panel ──────────────────────────────────── */}
        <div className="border-t mt-6 pt-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">My Radio Profile</h3>

          {/* The new member's PIN. Registering also logs in, so this must stay outside
              the logged-out panel: inside it, it vanished in the same moment and the
              listener never saw their PIN. */}
          {crmRegResult && (
            <div role="status" data-testid="crm-new-pin" className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm mb-3">
              Registered! Your PIN is: <strong className="text-lg">{crmRegResult}</strong>
              <br /><span className="text-xs">Save this PIN — you'll need it to log in.</span>
              {crmRegLoginFailed && (
                <><br /><span className="text-xs">We could not sign you in just now. Press Login to sign in with this PIN.</span></>
              )}
              <div>
                <button
                  type="button"
                  onClick={() => setCrmRegResult(null)}
                  className="min-h-[44px] px-2 text-sm font-medium text-green-800 underline"
                >
                  I've saved my PIN
                </button>
              </div>
            </div>
          )}

          {crm.autoLoginPending ? (
            <p className="text-sm text-gray-500">Signing you in...</p>
          ) : !crm.isLoggedIn ? (
            <div className="space-y-3">
              {crm.autoLoginError && <p className="text-amber-700 text-sm">{crm.autoLoginError}</p>}
              {/* Login row */}
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  maxLength={6}
                  inputMode="numeric"
                  aria-label="Listener PIN"
                  value={crmPinInput}
                  onChange={e => { setCrmPinInput(e.target.value.replace(/\D/g, '')); setCrmLoginError(''); }}
                  placeholder="Enter 6-digit PIN"
                  className="min-h-[44px] border border-gray-300 rounded-lg px-3 py-2 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-brand-500"
                />
                <button
                  onClick={async () => {
                    if (crmPinInput.length !== 6) { setCrmLoginError('PIN must be 6 digits'); return; }
                    try { await crm.login(crmPinInput); setCrmPinInput(''); setCrmLoginError(''); }
                    catch (err) { setCrmLoginError(crmLoginErrorText(err)); }
                  }}
                  className="min-h-[44px] bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition"
                >
                  Login
                </button>
                <span className="text-gray-400 text-sm">or</span>
                <button
                  onClick={() => setCrmRegOpen(!crmRegOpen)}
                  className="inline-flex items-center min-h-[44px] px-2 text-brand-600 hover:text-brand-700 text-sm font-medium transition"
                >
                  Register
                </button>
              </div>
              {crmLoginError && <p className="text-red-600 text-sm">{crmLoginError}</p>}

              {/* Registration form */}
              {crmRegOpen && (
                <div className="bg-gray-50 rounded-lg p-4 space-y-3">
                  {crmRegError && <p className="text-red-600 text-sm">{crmRegError}</p>}
                  <div>
                    <label htmlFor="crm-name" className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                    <input id="crm-name" type="text" value={crmRegName} onChange={e => setCrmRegName(e.target.value)}
                      placeholder="Your name" className="w-full min-h-[44px] border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label htmlFor="crm-phone" className="block text-sm font-medium text-gray-700 mb-1">Phone (optional)</label>
                      <input id="crm-phone" type="text" value={crmRegPhone} onChange={e => setCrmRegPhone(e.target.value)}
                        placeholder="Phone" className="w-full min-h-[44px] border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
                    </div>
                    <div>
                      <label htmlFor="crm-email" className="block text-sm font-medium text-gray-700 mb-1">Email (optional)</label>
                      <input id="crm-email" type="email" value={crmRegEmail} onChange={e => setCrmRegEmail(e.target.value)}
                        placeholder="Email" className="w-full min-h-[44px] border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
                    </div>
                  </div>
                  <button
                    onClick={async () => {
                      if (!crmRegName.trim()) { setCrmRegError('Name is required'); return; }
                      try {
                        const res = await crm.register({
                          name: crmRegName.trim(),
                          phone: crmRegPhone.trim() || undefined,
                          email: crmRegEmail.trim() || undefined,
                        });
                        setCrmRegResult(res.pin);
                        setCrmRegLoginFailed(!res.loggedIn);
                        setCrmRegName(''); setCrmRegPhone(''); setCrmRegEmail(''); setCrmRegError('');
                        if (!res.loggedIn) { setCrmPinInput(res.pin); setCrmRegOpen(false); }
                      } catch { setCrmRegError('Registration failed. Please try again.'); }
                    }}
                    className="min-h-[44px] bg-brand-600 hover:bg-brand-700 text-white px-6 py-2 rounded-lg text-sm font-medium transition"
                  >
                    Register
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {/* Profile card */}
              <div className="bg-brand-50 rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-lg">Welcome, {crm.profile?.name}!</p>
                    <p className="text-brand-700 font-bold">&ldquo;{crm.profile?.taste_profile.label}&rdquo;</p>
                    <p className="text-sm text-brand-600">{crm.profile?.taste_profile.description}</p>
                  </div>
                  <button onClick={crm.logout} className="min-h-[44px] px-2 text-xs text-gray-400 hover:text-red-500 transition">Logout</button>
                </div>
                <div className="flex items-center gap-4 mt-2 text-sm text-gray-600">
                  <span>{crm.profile?.ratings_count} songs rated</span>
                  <span>{crm.profile?.favorites_count} favorites</span>
                </div>
              </div>

              {/* Rating widget — only when a song is playing */}
              {isWsPlaying && wsNowPlaying?.asset_id && (
                <div className="flex items-center gap-3 bg-gray-50 rounded-lg p-3">
                  <span className="text-sm text-gray-600 flex-shrink-0">Rate this song:</span>
                  <div className="flex items-center gap-1">
                    {[1, 2, 3, 4, 5].map(star => (
                      <button key={star}
                        aria-label={`Rate ${star} star${star > 1 ? 's' : ''}`}
                        aria-pressed={star <= myRating}
                        onClick={() => {
                          const before = { rating: myRating, favorite: favPending ? false : myFavorite };
                          setMyRating(star);
                          setFavPending(false);
                          saveRating(star, myFavorite, before);
                        }}
                        className={`min-w-[44px] min-h-[44px] text-xl transition ${star <= myRating ? 'text-yellow-400' : 'text-gray-300 hover:text-yellow-300'}`}
                      >
                        &#9733;
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={() => {
                      const newFav = !myFavorite;
                      setMyFavorite(newFav);
                      if (myRating > 0) {
                        // Saved with the listener's own rating, never a made-up one
                        saveRating(myRating, newFav, { rating: myRating, favorite: myFavorite });
                      } else {
                        // No stars yet: the server keeps a favourite only with a rating,
                        // so it is saved with the first star the listener picks
                        setFavPending(newFav);
                      }
                    }}
                    className={`min-w-[44px] min-h-[44px] text-xl transition ${myFavorite ? 'text-red-500' : 'text-gray-300 hover:text-red-400'}`}
                    title={myFavorite ? 'Unfavorite' : 'Favorite'}
                    aria-label={myFavorite ? 'Unfavorite' : 'Favorite'}
                    aria-pressed={myFavorite}
                  >
                    &#9829;
                  </button>
                  {rateMutation.isPending && <span className="text-xs text-gray-400">Saving...</span>}
                </div>
              )}
              {isWsPlaying && favPending && myRating === 0 && (
                <p className="text-xs text-gray-500" data-testid="fav-needs-stars">Pick a star rating to save this favourite.</p>
              )}
              {rateError && <p className="text-red-600 text-sm" role="alert">{rateError}</p>}

              {/* Active raffles */}
              {activeRaffles && activeRaffles.length > 0 && (
                <div className="space-y-2">
                  {activeRaffles.map(raffle => (
                    <div key={raffle.id} className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 flex items-center justify-between">
                      <div>
                        <p className="font-medium text-yellow-800">{raffle.title}</p>
                        {raffle.prize && <p className="text-sm text-yellow-600">Prize: {raffle.prize}</p>}
                        <p className="text-xs text-yellow-500">{raffle.entry_count} entries</p>
                      </div>
                      {enteredRaffles.has(raffle.id) ? (
                        <span className="text-green-600 text-sm font-medium">Entered!</span>
                      ) : (
                        <div className="flex flex-col items-end gap-1">
                          <button
                            onClick={() => {
                              setRaffleError(null);
                              enterRaffleMutation.mutate(raffle.id, {
                                onSuccess: () => markEntered(raffle.id),
                                onError: (err) => {
                                  const status = httpStatus(err);
                                  // Already entered (409): that is what the listener wanted
                                  if (status === 409) { markEntered(raffle.id); return; }
                                  setRaffleError({
                                    id: raffle.id,
                                    text: status === 401 ? 'Please log in again to enter.'
                                      : status === 400 || status === 404 ? (crmErrorDetail(err) ?? 'This raffle is not open right now.')
                                        : status === undefined ? 'Could not reach the server. Please try again.'
                                          : 'Could not enter the raffle. Please try again.',
                                  });
                                },
                              });
                            }}
                            disabled={enterRaffleMutation.isPending}
                            className="min-h-[44px] bg-yellow-500 hover:bg-yellow-600 text-white px-4 py-1.5 rounded-lg text-sm font-medium transition disabled:opacity-50"
                          >
                            Enter Raffle
                          </button>
                          {raffleError?.id === raffle.id && <p className="text-red-600 text-xs text-right" role="alert">{raffleError.text}</p>}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
