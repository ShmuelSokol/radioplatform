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

function fmtTime(sec: number): string {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function ListenV2() {
  const { stationId } = useParams<{ stationId: string }>();
  const { data: station, isLoading } = useQuery({
    queryKey: ['station', stationId],
    queryFn: () => getStation(stationId!),
    enabled: !!stationId,
  });

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

  useEffect(() => {
    const audio = streamAudioRef.current;
    if (!audio || !useStream) return;
    const onError = () => setStreamFailed(true);
    audio.addEventListener('error', onError);
    return () => audio.removeEventListener('error', onError);
  }, [useStream]);

  useListenerHeartbeat(stationId, userStarted);

  const isWsPlaying = !!wsNowPlaying?.asset_id && !!wsNowPlaying?.asset;
  const wsAsset = wsNowPlaying?.asset;
  const wsNextAsset = wsNowPlaying?.next_asset;

  // Elapsed (capped at ends_at)
  const [elapsed, setElapsed] = useState(0);
  const wsEndsAt = wsNowPlaying?.ends_at;
  useEffect(() => {
    if (!wsNowPlaying?.started_at) { setElapsed(0); return; }
    const startMs = new Date(wsNowPlaying.started_at).getTime();
    const maxDuration = wsEndsAt ? (new Date(wsEndsAt).getTime() - startMs) / 1000 : 0;
    const update = () => {
      const raw = Math.max(0, (Date.now() - startMs) / 1000);
      setElapsed(maxDuration > 0 ? Math.min(raw, maxDuration) : raw);
    };
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [wsNowPlaying?.started_at, wsEndsAt]);

  const trackDuration = wsEndsAt && wsNowPlaying?.started_at
    ? Math.max(0, (new Date(wsEndsAt).getTime() - new Date(wsNowPlaying.started_at).getTime()) / 1000)
    : 0;
  const progressPct = trackDuration > 0 ? Math.min(100, (elapsed / trackDuration) * 100) : 0;

  const audioAsset: AssetInfo | null = isWsPlaying && wsNowPlaying?.asset_id && wsAsset ? {
    id: wsNowPlaying.asset_id,
    title: wsAsset.title,
    artist: wsAsset.artist ?? null,
    asset_type: 'music',
    category: null,
    duration: null,
  } : null;

  const {
    volume, setVolume, muted, toggleMute,
    audioReady, initAudio,
  } = useAudioEngine(
    userStarted && !useStream ? audioAsset : null,
    elapsed,
    userStarted && isWsPlaying && !useStream,
    null, null, 2000,
    wsAsset?.cue_in ?? 0,
    wsAsset?.cue_out ?? 0,
    wsAsset?.cross_start ?? 0,
    wsAsset?.replay_gain_db ?? 0,
    wsNextAsset?.id ?? null,
    wsNextAsset?.cue_in ?? 0,
    wsNextAsset?.replay_gain_db ?? 0,
    wsAsset?.audio_url ?? null,
    wsNextAsset?.audio_url ?? null,
  );

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

  // Sync volume to stream audio element
  useEffect(() => {
    if (streamAudioRef.current) {
      streamAudioRef.current.volume = muted ? 0 : volume;
    }
  }, [volume, muted]);

  if (isLoading) return <div className="text-center py-20 text-gray-400">Tuning in…</div>;
  if (!station) return <div className="text-center py-20 text-gray-400">Station not found</div>;

  const inputClass = 'w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-violet-500/60 focus:border-violet-400/40 transition';

  return (
    <div className="max-w-3xl mx-auto">
      <audio ref={streamAudioRef} className="hidden" />

      {/* ── Hero player ─────────────────────────────────── */}
      <div className="v2-glass-strong rounded-3xl p-8 sm:p-10 relative overflow-hidden">
        {/* Ambient glow behind disc */}
        <div className="absolute -top-24 left-1/2 -translate-x-1/2 w-96 h-96 rounded-full bg-violet-600/20 blur-3xl pointer-events-none" />

        {/* Live Show Banner */}
        {activeShow?.active && activeShow.show && (
          <div className="relative mb-8 v2-glass rounded-2xl p-4 border-rose-400/30 flex items-center gap-3">
            <span className="w-3 h-3 bg-rose-500 rounded-full animate-pulse flex-shrink-0 v2-pulse-ring" />
            <div className="flex-1 min-w-0">
              <div className="font-bold text-rose-300">LIVE: {activeShow.show.title}</div>
              {activeShow.show.description && (
                <p className="text-sm text-rose-200/70 mt-0.5 truncate">{activeShow.show.description}</p>
              )}
              {activeShow.show.calls_enabled && (
                <p className="text-xs text-rose-300/60 mt-1">Call-ins are open!</p>
              )}
            </div>
          </div>
        )}

        <div className="relative flex flex-col items-center text-center">
          {/* Spinning disc */}
          <div className={`relative w-44 h-44 sm:w-52 sm:h-52 mb-6 ${userStarted && isWsPlaying ? 'v2-spin-slow' : ''}`}>
            <div className="absolute inset-0 rounded-full bg-gradient-to-br from-violet-500 via-fuchsia-500 to-violet-700 p-[3px]">
              <div className="w-full h-full rounded-full bg-[#0b0b22] flex items-center justify-center">
                <div className="text-6xl text-violet-300">&#9835;</div>
              </div>
            </div>
            <div className="absolute inset-1/2 w-5 h-5 -ml-2.5 -mt-2.5 rounded-full bg-[#07071a] border-2 border-violet-400/50" />
          </div>

          <h1 className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight">{station.name}</h1>
          <p className="text-gray-400 mt-1 text-sm">{station.type} &middot; {station.timezone}</p>
          {station.description && <p className="text-gray-500 mt-2 max-w-md text-sm">{station.description}</p>}

          {/* Status row */}
          <div className="flex items-center gap-2 mt-4">
            <span className={`px-3 py-1 rounded-full text-xs font-bold tracking-wide ${
              isWsPlaying
                ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-400/30'
                : 'bg-white/5 text-gray-400 border border-white/10'
            }`}>
              {isWsPlaying ? '● ON AIR' : wsConnected ? 'OFFLINE' : 'CONNECTING…'}
            </span>
            {userStarted && useStream && (
              <span className="px-3 py-1 rounded-full text-xs font-bold bg-sky-500/15 text-sky-300 border border-sky-400/30">LIVE STREAM</span>
            )}
          </div>

          {/* Play / Stop */}
          <div className="mt-8">
            {userStarted ? (
              <button
                onClick={handleStop}
                className="group w-20 h-20 rounded-full bg-gradient-to-br from-rose-500 to-rose-700 text-white shadow-[0_8px_30px_rgba(244,63,94,0.4)] hover:shadow-[0_8px_40px_rgba(244,63,94,0.6)] hover:scale-105 active:scale-95 transition-all duration-200 flex items-center justify-center"
                title="Stop"
              >
                <span className="block w-6 h-6 bg-white rounded-sm group-hover:scale-110 transition-transform" />
              </button>
            ) : (
              <button
                onClick={handlePlay}
                className="group w-20 h-20 rounded-full bg-gradient-to-br from-violet-500 to-fuchsia-600 text-white shadow-[0_8px_30px_rgba(139,92,246,0.4)] hover:shadow-[0_8px_40px_rgba(139,92,246,0.6)] hover:scale-105 active:scale-95 transition-all duration-200 flex items-center justify-center"
                title="Listen Live"
              >
                <svg className="w-9 h-9 ml-1 group-hover:scale-110 transition-transform" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </button>
            )}
          </div>

          {/* Volume */}
          {userStarted && (audioReady || useStream) && (
            <div className="flex items-center gap-3 mt-6 w-full max-w-xs">
              <button onClick={toggleMute} className="text-gray-400 hover:text-white text-lg w-8 transition" title={muted ? 'Unmute' : 'Mute'}>
                {muted ? (
                  <svg className="w-5 h-5 mx-auto" fill="currentColor" viewBox="0 0 24 24"><path d="M16.5 12a4.5 4.5 0 00-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51A8.94 8.94 0 0021 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.26c-.67.52-1.42.93-2.25 1.18v2.06a8.99 8.99 0 003.69-1.81L19.73 21 21 19.73 4.27 3zM12 4L9.91 6.09 12 8.18V4z" /></svg>
                ) : (
                  <svg className="w-5 h-5 mx-auto" fill="currentColor" viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 00-2.5-4.03v8.05A4.47 4.47 0 0016.5 12zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" /></svg>
                )}
              </button>
              <input
                type="range" min={0} max={1} step={0.01} value={volume}
                onChange={(e) => setVolume(Number(e.target.value))}
                className="v2-range flex-1"
              />
              <span className="text-gray-500 text-xs w-10 text-right tabular-nums">{Math.round(volume * 100)}%</span>
            </div>
          )}
        </div>

        {/* Now Playing card */}
        {isWsPlaying && wsAsset && (
          <div className="relative mt-8 v2-glass rounded-2xl p-5">
            <div className="flex items-center gap-4">
              {/* Equalizer */}
              <div className="flex items-end gap-[3px] h-8 w-7 flex-shrink-0">
                {[0, 1, 2, 3].map(i => (
                  <span
                    key={i}
                    className={`w-[5px] rounded-full bg-gradient-to-t from-violet-500 to-fuchsia-400 ${userStarted ? 'v2-eq-bar' : ''}`}
                    style={{ height: userStarted ? undefined : '30%', animationDelay: `${i * 0.18}s` }}
                  />
                ))}
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-bold text-white truncate">{wsAsset.title}</p>
                {wsAsset.artist && <p className="text-sm text-gray-400 truncate">{wsAsset.artist}</p>}
                {wsAsset.album && <p className="text-xs text-gray-600 truncate">{wsAsset.album}</p>}
              </div>
              {trackDuration > 0 && (
                <span className="text-xs text-gray-500 tabular-nums flex-shrink-0">
                  {fmtTime(elapsed)} / {fmtTime(trackDuration)}
                </span>
              )}
            </div>
            {/* Progress bar */}
            {trackDuration > 0 && (
              <div className="mt-3 h-1.5 rounded-full bg-white/10 overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-violet-500 to-fuchsia-500 transition-[width] duration-1000 ease-linear"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            )}
            {/* Next up */}
            {wsNextAsset?.title && (
              <p className="mt-3 text-xs text-gray-500">
                <span className="text-gray-600 uppercase font-semibold tracking-wide mr-1.5">Up next</span>
                {wsNextAsset.title}{wsNextAsset.artist && <span className="text-gray-600"> — {wsNextAsset.artist}</span>}
              </p>
            )}
          </div>
        )}

        {!isWsPlaying && wsConnected && (
          <div className="relative mt-8 text-center text-gray-500 text-sm">Nothing playing right now</div>
        )}
      </div>

      {/* ── Song Request ─────────────────────────────────── */}
      <div className="v2-glass rounded-3xl p-6 mt-6">
        <button
          onClick={() => { setRequestOpen(!requestOpen); setReqResult(null); setReqError(''); }}
          className="flex items-center gap-2 text-violet-300 hover:text-violet-200 font-semibold transition w-full"
        >
          <svg className={`w-4 h-4 transition-transform duration-300 ${requestOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          Request a Song
        </button>

        {requestOpen && (
          <div className="mt-5 space-y-3 v2-fade-up">
            {reqResult && (
              <div className="bg-emerald-500/10 border border-emerald-400/30 text-emerald-300 rounded-xl px-4 py-3 text-sm">
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
              <div className="bg-rose-500/10 border border-rose-400/30 text-rose-300 rounded-xl px-4 py-3 text-sm">{reqError}</div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <input type="text" value={reqName} onChange={e => setReqName(e.target.value)} placeholder="Your name *" className={inputClass} />
              <input type="text" value={reqSong} onChange={e => setReqSong(e.target.value)} placeholder="Song title *" className={inputClass} />
            </div>
            <input type="text" value={reqArtist} onChange={e => setReqArtist(e.target.value)} placeholder="Artist (optional)" className={inputClass} />
            <textarea
              value={reqMessage} onChange={e => setReqMessage(e.target.value)}
              placeholder="Dedication or message… (optional)" rows={2}
              className={`${inputClass} resize-none`}
            />
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
                  setReqName(''); setReqSong(''); setReqArtist(''); setReqMessage('');
                } catch {
                  setReqError('Failed to submit request. Please try again.');
                } finally {
                  setReqSubmitting(false);
                }
              }}
              disabled={reqSubmitting}
              className="bg-gradient-to-r from-violet-600 to-fuchsia-600 hover:from-violet-500 hover:to-fuchsia-500 text-white px-6 py-2.5 rounded-xl text-sm font-semibold transition-all duration-200 hover:shadow-[0_4px_20px_rgba(139,92,246,0.4)] disabled:opacity-50"
            >
              {reqSubmitting ? 'Submitting…' : 'Submit Request'}
            </button>
          </div>
        )}
      </div>

      {/* ── My Radio Profile (CRM) ───────────────────────── */}
      <div className="v2-glass rounded-3xl p-6 mt-6 mb-8">
        <h3 className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-4">My Radio Profile</h3>

        {!crm.isLoggedIn ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="text" maxLength={6} value={crmPinInput}
                onChange={e => { setCrmPinInput(e.target.value.replace(/\D/g, '')); setCrmLoginError(''); }}
                placeholder="6-digit PIN"
                className={`${inputClass} !w-36 text-center tracking-[0.3em]`}
              />
              <button
                onClick={async () => {
                  if (crmPinInput.length !== 6) { setCrmLoginError('PIN must be 6 digits'); return; }
                  try { await crm.login(crmPinInput); setCrmPinInput(''); setCrmLoginError(''); }
                  catch { setCrmLoginError('Invalid PIN'); }
                }}
                className="bg-gradient-to-r from-violet-600 to-fuchsia-600 hover:from-violet-500 hover:to-fuchsia-500 text-white px-5 py-2 rounded-xl text-sm font-semibold transition"
              >
                Login
              </button>
              <span className="text-gray-600 text-sm">or</span>
              <button
                onClick={() => setCrmRegOpen(!crmRegOpen)}
                className="text-violet-300 hover:text-violet-200 text-sm font-semibold transition"
              >
                Register
              </button>
            </div>
            {crmLoginError && <p className="text-rose-400 text-sm">{crmLoginError}</p>}

            {crmRegOpen && (
              <div className="v2-glass rounded-2xl p-4 space-y-3 v2-fade-up">
                {crmRegResult && (
                  <div className="bg-emerald-500/10 border border-emerald-400/30 text-emerald-300 rounded-xl px-4 py-3 text-sm">
                    Registered! Your PIN is: <strong className="text-lg tracking-widest">{crmRegResult}</strong>
                    <br /><span className="text-xs opacity-75">Save this PIN — you'll need it to log in.</span>
                  </div>
                )}
                {crmRegError && <p className="text-rose-400 text-sm">{crmRegError}</p>}
                <input type="text" value={crmRegName} onChange={e => setCrmRegName(e.target.value)} placeholder="Name *" className={inputClass} />
                <div className="grid grid-cols-2 gap-3">
                  <input type="text" value={crmRegPhone} onChange={e => setCrmRegPhone(e.target.value)} placeholder="Phone (optional)" className={inputClass} />
                  <input type="email" value={crmRegEmail} onChange={e => setCrmRegEmail(e.target.value)} placeholder="Email (optional)" className={inputClass} />
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
                  className="bg-gradient-to-r from-violet-600 to-fuchsia-600 hover:from-violet-500 hover:to-fuchsia-500 text-white px-6 py-2 rounded-xl text-sm font-semibold transition"
                >
                  Register
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {/* Profile card */}
            <div className="rounded-2xl p-4 bg-gradient-to-br from-violet-600/20 to-fuchsia-600/10 border border-violet-400/20">
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-bold text-lg text-white">Welcome, {crm.profile?.name}!</p>
                  <p className="text-violet-300 font-bold">&ldquo;{crm.profile?.taste_profile.label}&rdquo;</p>
                  <p className="text-sm text-violet-200/60">{crm.profile?.taste_profile.description}</p>
                </div>
                <button onClick={crm.logout} className="text-xs text-gray-500 hover:text-rose-400 transition">Logout</button>
              </div>
              <div className="flex items-center gap-4 mt-2 text-sm text-gray-400">
                <span>{crm.profile?.ratings_count} songs rated</span>
                <span>{crm.profile?.favorites_count} favorites</span>
              </div>
            </div>

            {/* Rating widget */}
            {isWsPlaying && wsNowPlaying?.asset_id && (
              <div className="flex items-center gap-3 v2-glass rounded-2xl p-3">
                <span className="text-sm text-gray-400 flex-shrink-0">Rate this song:</span>
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
                      className={`text-2xl transition-all duration-150 hover:scale-125 ${star <= myRating ? 'text-amber-400' : 'text-gray-600 hover:text-amber-300'}`}
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
                  className={`text-2xl transition-all duration-150 hover:scale-125 ${myFavorite ? 'text-rose-500' : 'text-gray-600 hover:text-rose-400'}`}
                  title={myFavorite ? 'Unfavorite' : 'Favorite'}
                >
                  &#9829;
                </button>
                {rateMutation.isPending && <span className="text-xs text-gray-500">Saving…</span>}
              </div>
            )}

            {/* Active raffles */}
            {activeRaffles && activeRaffles.length > 0 && (
              <div className="space-y-2">
                {activeRaffles.map(raffle => (
                  <div key={raffle.id} className="rounded-2xl p-4 bg-gradient-to-br from-amber-500/15 to-amber-600/5 border border-amber-400/25 flex items-center justify-between">
                    <div>
                      <p className="font-bold text-amber-300">{raffle.title}</p>
                      {raffle.prize && <p className="text-sm text-amber-200/70">Prize: {raffle.prize}</p>}
                      <p className="text-xs text-amber-200/40">{raffle.entry_count} entries</p>
                    </div>
                    {enteredRaffles.has(raffle.id) ? (
                      <span className="text-emerald-400 text-sm font-bold">Entered!</span>
                    ) : (
                      <button
                        onClick={() => {
                          enterRaffleMutation.mutate(raffle.id, {
                            onSuccess: () => setEnteredRaffles(prev => new Set(prev).add(raffle.id)),
                            onError: () => {},
                          });
                        }}
                        disabled={enterRaffleMutation.isPending}
                        className="bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-400 hover:to-amber-500 text-white px-4 py-1.5 rounded-xl text-sm font-bold transition disabled:opacity-50"
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
  );
}
