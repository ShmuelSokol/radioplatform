import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useState, useEffect, useCallback, useRef } from 'react';
import { getStation } from '../../api/stations';
import apiClient from '../../api/client';
import { submitSongRequest, SongRequestSubmitResponse } from '../../api/songRequests';
import { useListenerHeartbeat } from '../../hooks/useListeners';
import { useCrmAuth, useRateSong, useActiveRaffles, useEnterRaffle } from '../../hooks/useCrm';
import { useNowPlayingWS } from '../../hooks/useNowPlayingWS';
import { useAudioEngine } from '../../hooks/useAudioEngine';
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
  const { data: station, isLoading } = useQuery({
    queryKey: ['station', stationId],
    queryFn: () => getStation(stationId!),
    enabled: !!stationId,
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
  const { data: activeRaffles } = useActiveRaffles();
  const enterRaffleMutation = useEnterRaffle();
  const [crmPinInput, setCrmPinInput] = useState('');
  const [crmRegOpen, setCrmRegOpen] = useState(false);
  const [crmRegName, setCrmRegName] = useState('');
  const [crmRegPhone, setCrmRegPhone] = useState('');
  const [crmRegEmail, setCrmRegEmail] = useState('');
  const [crmRegResult, setCrmRegResult] = useState<string | null>(null);
  const [crmLoginError, setCrmLoginError] = useState('');
  const [crmRegError, setCrmRegError] = useState('');
  const [myRating, setMyRating] = useState(0);
  const [myFavorite, setMyFavorite] = useState(false);
  const [enteredRaffles, setEnteredRaffles] = useState<Set<string>>(new Set());

  // Reset rating and favorite when track changes
  useEffect(() => {
    setMyRating(0);
    setMyFavorite(false);
  }, [wsNowPlaying?.asset_id]);

  // Icecast stream: direct MP3 stream via <audio> element
  const streamUrl = wsNowPlaying?.stream_url || null;
  const [streamFailed, setStreamFailed] = useState(false);
  const useStream = !!streamUrl && !streamFailed;
  const streamAudioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const audio = streamAudioRef.current;
    if (!audio || !useStream || !userStarted) return;
    audio.src = streamUrl!;
    audio.play().catch(() => setStreamFailed(true));
    return () => { audio.pause(); audio.src = ''; };
  }, [useStream, userStarted, streamUrl]);

  // Listen for stream errors to trigger fallback
  useEffect(() => {
    const audio = streamAudioRef.current;
    if (!audio || !useStream) return;
    const onError = () => setStreamFailed(true);
    audio.addEventListener('error', onError);
    return () => audio.removeEventListener('error', onError);
  }, [useStream]);

  // Track listener session (heartbeat every 30s while listening)
  useListenerHeartbeat(stationId, userStarted);

  // Derive playback state from WS data
  const isWsPlaying = !!wsNowPlaying?.asset_id && !!wsNowPlaying?.asset;
  const wsAsset = wsNowPlaying?.asset;
  const wsNextAsset = wsNowPlaying?.next_asset;

  // Compute elapsed from started_at
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!wsNowPlaying?.started_at) { setElapsed(0); return; }
    const update = () => {
      const startMs = new Date(wsNowPlaying.started_at).getTime();
      setElapsed(Math.max(0, (Date.now() - startMs) / 1000));
    };
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [wsNowPlaying?.started_at]);

  // Build AssetInfo for audio engine
  const audioAsset: AssetInfo | null = wsNowPlaying?.asset_id && wsAsset ? {
    id: wsNowPlaying.asset_id,
    title: wsAsset.title,
    artist: wsAsset.artist ?? null,
    asset_type: 'music',
    category: null,
    duration: null,
  } : null;

  // Audio engine with crossfade support (fallback when HLS is not available)
  const {
    volume, setVolume, muted, toggleMute,
    audioReady, initAudio,
  } = useAudioEngine(
    userStarted && !useStream ? audioAsset : null,
    elapsed,
    userStarted && isWsPlaying && !useStream,
    null, null, 2000,
    // Crossfade params from WS
    wsAsset?.cue_in ?? 0,
    wsAsset?.cue_out ?? 0,
    wsAsset?.cross_start ?? 0,
    wsAsset?.replay_gain_db ?? 0,
    wsNextAsset?.id ?? null,
    wsNextAsset?.cue_in ?? 0,
    wsNextAsset?.replay_gain_db ?? 0,
    // Direct audio URLs from WS (no auth needed for public listeners)
    wsAsset?.audio_url ?? null,
    wsNextAsset?.audio_url ?? null,
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

  const handlePlay = async () => {
    setUserStarted(true);
    if (!useStream) {
      await initAudio();
    }
  };

  const handleStop = () => {
    setUserStarted(false);
    const audio = streamAudioRef.current;
    if (audio) { audio.pause(); audio.src = ''; }
  };

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
  if (!station) return <div className="text-center py-10">Station not found</div>;

  return (
    <div className="max-w-2xl mx-auto">
      {/* Hidden audio element for Icecast stream playback */}
      <audio ref={streamAudioRef} className="hidden" />
      <div className="bg-white shadow rounded-lg p-8">
        <div className="flex items-center gap-6 mb-8">
          <div className="w-24 h-24 bg-brand-50 rounded-lg flex items-center justify-center text-5xl text-brand-600">
            &#9835;
          </div>
          <div>
            <h1 className="text-3xl font-bold">{station.name}</h1>
            <p className="text-gray-500">{station.type} &middot; {station.timezone}</p>
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
                <p className="text-xs text-red-500 mt-1">Call-ins are open!</p>
              )}
            </div>
          </div>
        )}

        <div className="flex items-center gap-4 mb-6">
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
            isWsPlaying ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'
          }`}>
            {isWsPlaying ? 'On Air' : wsConnected ? 'Offline' : 'Connecting...'}
          </span>
          {userStarted && useStream && (
            <span className="px-2 py-0.5 rounded-full text-xs bg-blue-100 text-blue-700">LIVE</span>
          )}
        </div>

        {/* Volume control */}
        {userStarted && (audioReady || useStream) && (
          <div className="flex items-center gap-3 mb-6">
            <button onClick={toggleMute} className="text-gray-400 text-sm w-8">
              {muted ? 'Mute' : 'Vol'}
            </button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={volume}
              onChange={(e) => setVolume(Number(e.target.value))}
              className="flex-1 max-w-48"
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

        {!isWsPlaying && wsConnected && (
          <div className="border-t pt-6">
            <p className="text-gray-400">Nothing playing right now</p>
          </div>
        )}

        {/* Song Request Section */}
        <div className="border-t mt-6 pt-6">
          <button
            onClick={() => { setRequestOpen(!requestOpen); setReqResult(null); setReqError(''); }}
            className="flex items-center gap-2 text-brand-600 hover:text-brand-700 font-medium transition"
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
                <label className="block text-sm font-medium text-gray-700 mb-1">Your Name *</label>
                <input
                  type="text"
                  value={reqName}
                  onChange={e => setReqName(e.target.value)}
                  placeholder="Enter your name"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Song Title *</label>
                <input
                  type="text"
                  value={reqSong}
                  onChange={e => setReqSong(e.target.value)}
                  placeholder="What song would you like to hear?"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Artist (optional)</label>
                <input
                  type="text"
                  value={reqArtist}
                  onChange={e => setReqArtist(e.target.value)}
                  placeholder="Artist name"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Message (optional)</label>
                <textarea
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
                  } catch {
                    setReqError('Failed to submit request. Please try again.');
                  } finally {
                    setReqSubmitting(false);
                  }
                }}
                disabled={reqSubmitting}
                className="bg-brand-600 hover:bg-brand-700 text-white px-6 py-2 rounded-lg text-sm font-medium transition disabled:opacity-50"
              >
                {reqSubmitting ? 'Submitting...' : 'Submit Request'}
              </button>
            </div>
          )}
        </div>

        {/* ── CRM Panel ──────────────────────────────────── */}
        <div className="border-t mt-6 pt-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">My Radio Profile</h3>

          {!crm.isLoggedIn ? (
            <div className="space-y-3">
              {/* Login row */}
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  maxLength={6}
                  value={crmPinInput}
                  onChange={e => { setCrmPinInput(e.target.value.replace(/\D/g, '')); setCrmLoginError(''); }}
                  placeholder="Enter 6-digit PIN"
                  className="border border-gray-300 rounded-lg px-3 py-2 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-brand-500"
                />
                <button
                  onClick={async () => {
                    if (crmPinInput.length !== 6) { setCrmLoginError('PIN must be 6 digits'); return; }
                    try { await crm.login(crmPinInput); setCrmPinInput(''); setCrmLoginError(''); }
                    catch { setCrmLoginError('Invalid PIN'); }
                  }}
                  className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition"
                >
                  Login
                </button>
                <span className="text-gray-400 text-sm">or</span>
                <button
                  onClick={() => setCrmRegOpen(!crmRegOpen)}
                  className="text-brand-600 hover:text-brand-700 text-sm font-medium transition"
                >
                  Register
                </button>
              </div>
              {crmLoginError && <p className="text-red-600 text-sm">{crmLoginError}</p>}

              {/* Registration form */}
              {crmRegOpen && (
                <div className="bg-gray-50 rounded-lg p-4 space-y-3">
                  {crmRegResult && (
                    <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
                      Registered! Your PIN is: <strong className="text-lg">{crmRegResult}</strong>
                      <br /><span className="text-xs">Save this PIN — you'll need it to log in.</span>
                    </div>
                  )}
                  {crmRegError && <p className="text-red-600 text-sm">{crmRegError}</p>}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                    <input type="text" value={crmRegName} onChange={e => setCrmRegName(e.target.value)}
                      placeholder="Your name" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Phone (optional)</label>
                      <input type="text" value={crmRegPhone} onChange={e => setCrmRegPhone(e.target.value)}
                        placeholder="Phone" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Email (optional)</label>
                      <input type="email" value={crmRegEmail} onChange={e => setCrmRegEmail(e.target.value)}
                        placeholder="Email" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
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
                        setCrmRegName(''); setCrmRegPhone(''); setCrmRegEmail(''); setCrmRegError('');
                      } catch { setCrmRegError('Registration failed. Please try again.'); }
                    }}
                    className="bg-brand-600 hover:bg-brand-700 text-white px-6 py-2 rounded-lg text-sm font-medium transition"
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
                  <button onClick={crm.logout} className="text-xs text-gray-400 hover:text-red-500 transition">Logout</button>
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
                        onClick={() => {
                          setMyRating(star);
                          rateMutation.mutate(
                            { asset_id: wsNowPlaying.asset_id!, rating: star, is_favorite: myFavorite },
                            { onSuccess: () => crm.refreshProfile() }
                          );
                        }}
                        className={`text-xl transition ${star <= myRating ? 'text-yellow-400' : 'text-gray-300 hover:text-yellow-300'}`}
                      >
                        &#9733;
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={() => {
                      const newFav = !myFavorite;
                      setMyFavorite(newFav);
                      rateMutation.mutate(
                        { asset_id: wsNowPlaying.asset_id!, rating: myRating || 5, is_favorite: newFav },
                        { onSuccess: () => crm.refreshProfile() }
                      );
                    }}
                    className={`text-xl transition ${myFavorite ? 'text-red-500' : 'text-gray-300 hover:text-red-400'}`}
                    title={myFavorite ? 'Unfavorite' : 'Favorite'}
                  >
                    &#9829;
                  </button>
                  {rateMutation.isPending && <span className="text-xs text-gray-400">Saving...</span>}
                </div>
              )}

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
                        <button
                          onClick={() => {
                            enterRaffleMutation.mutate(raffle.id, {
                              onSuccess: () => setEnteredRaffles(prev => new Set(prev).add(raffle.id)),
                              onError: () => {},
                            });
                          }}
                          disabled={enterRaffleMutation.isPending}
                          className="bg-yellow-500 hover:bg-yellow-600 text-white px-4 py-1.5 rounded-lg text-sm font-medium transition disabled:opacity-50"
                        >
                          Enter Raffle
                        </button>
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
