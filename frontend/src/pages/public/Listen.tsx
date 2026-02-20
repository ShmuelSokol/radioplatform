import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useState, useRef, useEffect, useCallback } from 'react';
import { getStation } from '../../api/stations';
import apiClient from '../../api/client';
import { submitSongRequest } from '../../api/songRequests';

interface LiveAudioData {
  playing: boolean;
  asset_id?: string;
  title?: string;
  artist?: string;
  album?: string;
  duration?: number;
  elapsed?: number;
  started_at?: string;
  audio_url?: string;
}

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

const POLL_INTERVAL = 4000;

export default function Listen() {
  const { stationId } = useParams<{ stationId: string }>();
  const { data: station, isLoading } = useQuery({
    queryKey: ['station', stationId],
    queryFn: () => getStation(stationId!),
    enabled: !!stationId,
  });

  const [liveData, setLiveData] = useState<LiveAudioData | null>(null);
  const [activeShow, setActiveShow] = useState<ActiveShowData | null>(null);
  const [userStarted, setUserStarted] = useState(false);
  const [volume, setVolume] = useState(0.7);

  // Song request form state
  const [requestOpen, setRequestOpen] = useState(false);
  const [reqName, setReqName] = useState('');
  const [reqSong, setReqSong] = useState('');
  const [reqArtist, setReqArtist] = useState('');
  const [reqMessage, setReqMessage] = useState('');
  const [reqSubmitting, setReqSubmitting] = useState(false);
  const [reqSuccess, setReqSuccess] = useState(false);
  const [reqError, setReqError] = useState('');
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const currentAssetRef = useRef<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch live-audio data
  const fetchLiveAudio = useCallback(async () => {
    if (!stationId) return;
    try {
      const res = await apiClient.get<LiveAudioData>(`/stations/${stationId}/live-audio`);
      setLiveData(res.data);
    } catch {
      // Silently fail — station may be offline
    }
  }, [stationId]);

  // Fetch active live show status
  const fetchActiveShow = useCallback(async () => {
    if (!stationId) return;
    try {
      const res = await apiClient.get<ActiveShowData>(`/live-shows/station/${stationId}/active`);
      setActiveShow(res.data);
    } catch {
      // Endpoint may not exist yet
    }
  }, [stationId]);

  // Poll for live-audio data and live show status
  useEffect(() => {
    if (!stationId) return;
    fetchLiveAudio();
    fetchActiveShow();
    pollRef.current = setInterval(() => {
      fetchLiveAudio();
      fetchActiveShow();
    }, POLL_INTERVAL);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [stationId, fetchLiveAudio, fetchActiveShow]);

  // Play/switch audio when live data changes
  useEffect(() => {
    if (!userStarted || !liveData?.playing || !liveData.audio_url) return;

    const audio = audioRef.current;
    if (!audio) return;

    // New track — load and seek
    if (liveData.asset_id !== currentAssetRef.current) {
      currentAssetRef.current = liveData.asset_id ?? null;
      audio.src = liveData.audio_url;
      audio.currentTime = Math.max(0, liveData.elapsed ?? 0);
      audio.play().catch(() => {});
    }
  }, [liveData?.asset_id, liveData?.playing, liveData?.audio_url, liveData?.elapsed, userStarted]);

  // Stop audio when nothing is playing
  useEffect(() => {
    if (userStarted && liveData && !liveData.playing) {
      const audio = audioRef.current;
      if (audio && !audio.paused) {
        audio.pause();
        currentAssetRef.current = null;
      }
    }
  }, [liveData?.playing, userStarted]);

  // Volume sync
  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = volume;
  }, [volume]);

  const handlePlay = () => {
    if (!audioRef.current) {
      const audio = new Audio();
      audio.crossOrigin = 'anonymous';
      audio.volume = volume;
      audioRef.current = audio;
    }
    setUserStarted(true);
    currentAssetRef.current = null; // Force reload on next effect
    fetchLiveAudio();
  };

  const handleStop = () => {
    setUserStarted(false);
    currentAssetRef.current = null;
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.src = '';
    }
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      audioRef.current = null;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  if (isLoading) return <div className="text-center py-10">Loading...</div>;
  if (!station) return <div className="text-center py-10">Station not found</div>;

  return (
    <div className="max-w-2xl mx-auto">
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
            liveData?.playing ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'
          }`}>
            {liveData?.playing ? 'On Air' : 'Offline'}
          </span>
        </div>

        {/* Volume control */}
        {userStarted && (
          <div className="flex items-center gap-3 mb-6">
            <span className="text-gray-400 text-sm">Vol</span>
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

        {liveData?.playing && liveData.title && (
          <div className="border-t pt-6">
            <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Now Playing</h3>
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-gray-100 rounded flex items-center justify-center text-2xl">
                &#9835;
              </div>
              <div className="flex-1">
                <p className="font-medium">{liveData.title}</p>
                {liveData.artist && (
                  <p className="text-sm text-gray-500">{liveData.artist}</p>
                )}
                {liveData.album && (
                  <p className="text-xs text-gray-400">{liveData.album}</p>
                )}
              </div>
            </div>
          </div>
        )}

        {liveData && !liveData.playing && (
          <div className="border-t pt-6">
            <p className="text-gray-400">Nothing playing right now</p>
          </div>
        )}

        {/* Song Request Section */}
        <div className="border-t mt-6 pt-6">
          <button
            onClick={() => { setRequestOpen(!requestOpen); setReqSuccess(false); setReqError(''); }}
            className="flex items-center gap-2 text-brand-600 hover:text-brand-700 font-medium transition"
          >
            <svg className={`w-4 h-4 transition-transform ${requestOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            Request a Song
          </button>

          {requestOpen && (
            <div className="mt-4 space-y-3">
              {reqSuccess && (
                <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
                  Your song request has been submitted! The station manager will review it.
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
                    await submitSongRequest({
                      station_id: stationId!,
                      requester_name: reqName.trim(),
                      song_title: reqSong.trim(),
                      song_artist: reqArtist.trim() || undefined,
                      requester_message: reqMessage.trim() || undefined,
                    });
                    setReqSuccess(true);
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
      </div>
    </div>
  );
}
