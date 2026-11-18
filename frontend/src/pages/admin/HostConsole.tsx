import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useLiveShow, useEndLiveShow } from '../../hooks/useLiveShows';
import { useLiveShowWS } from '../../hooks/useLiveShowWS';
import { useHostAudio } from '../../hooks/useHostAudio';
import CartPanel from './live/CartPanel';
import TalkButton, { TalkNote } from './live/TalkButton';
import { useTalkback } from '../../hooks/useTalkback';
import { endCall, holdCall, nextCallerAsking, putCallerOnAirAsking } from '../../api/liveShows';
import DelayPanel from './live/DelayPanel';
import CallerNote from './live/CallerNote';

export default function HostConsole() {
  const { showId } = useParams<{ showId: string }>();
  const navigate = useNavigate();
  const { show, callers, secondsRemaining, isConnected, air, lastDump } = useLiveShowWS(showId);
  const { data: showData } = useLiveShow(showId);
  const endShowMut = useEndLiveShow();
  const { isRecording, startRecording, stopRecording, audioLevel, error: audioError, muted, setMuted, playCart, stopCart, cartPlaying, reconnecting } = useHostAudio(showId);
  // Cough: silent while the button (or the C key) is held. Mute: stays silent until pressed again.
  const [micOff, setMicOff] = useState(false);
  const [coughing, setCoughing] = useState(false);
  // Talking to a caller off the air: the host's microphone to listeners is closed meanwhile
  const [talking, setTalking] = useState(false);
  const talk = useTalkback(showId, setTalking);
  useEffect(() => { setMuted(micOff || coughing || talking); }, [micOff, coughing, talking, setMuted]);
  useEffect(() => { if (!isRecording) { setMicOff(false); setCoughing(false); } }, [isRecording]);
  useEffect(() => {
    if (!isRecording) return;
    const typing = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
    };
    const down = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'c' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && !typing(e)) setCoughing(true);
    };
    const up = (e: KeyboardEvent) => { if (e.key.toLowerCase() === 'c') setCoughing(false); };
    const away = () => setCoughing(false);
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', away);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', away);
    };
  }, [isRecording]);

  // Local countdown timer (decremented every 1s, re-synced via WS every 10s)
  const [localSeconds, setLocalSeconds] = useState<number | null>(null);
  const [hardStopped, setHardStopped] = useState(false);

  // Sync from WS
  useEffect(() => {
    if (secondsRemaining !== null) {
      setLocalSeconds(secondsRemaining);
    }
  }, [secondsRemaining]);

  // Local countdown
  useEffect(() => {
    if (localSeconds === null || localSeconds <= 0) return;
    const id = setInterval(() => {
      setLocalSeconds(prev => {
        if (prev === null) return null;
        return Math.max(0, prev - 1);
      });
    }, 1000);
    return () => clearInterval(id);
  }, [localSeconds !== null]);

  // Detect hard stop
  useEffect(() => {
    if (show?.status === 'ended' && !hardStopped) {
      setHardStopped(true);
    }
  }, [show?.status, hardStopped]);

  const currentShow = show || showData;
  const onAirCaller = callers.find(c => c.status === 'on_air');
  const approvedCallers = callers.filter(c => c.status === 'approved');

  const formatTime = (seconds: number | null): string => {
    if (seconds === null) return '--:--:--';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const isUrgent = localSeconds !== null && localSeconds < 300; // < 5 min

  const handleEndShow = () => {
    if (!showId) return;
    if (confirm('End this live show?')) {
      stopRecording();
      endShowMut.mutate(showId, {
        onSuccess: () => navigate('/admin/live'),
      });
    }
  };

  const handleEndCall = async (callId: string) => {
    if (!showId) return;
    await endCall(showId, callId);
  };

  const handleHold = async (callId: string) => {
    if (!showId) return;
    try { await holdCall(showId, callId); } catch (err: any) { alert(err?.response?.data?.detail || 'Could not hold the caller'); }
  };

  const handleNext = async () => {
    if (!showId) return;
    const problem = await nextCallerAsking(showId);
    if (problem) alert(problem);
  };

  const handlePutOnAir = async (callId: string) => {
    if (!showId) return;
    const problem = await putCallerOnAirAsking(showId, callId);
    if (problem) alert(problem);
  };

  if (hardStopped) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center z-50">
        <div className="text-center">
          <div className="text-6xl text-red-500 font-bold mb-4">SHOW ENDED</div>
          <p className="text-gray-400 text-lg mb-6">The live show has been automatically stopped.</p>
          <button
            onClick={() => navigate('/admin/live')}
            className="bg-cyan-700 hover:bg-cyan-600 text-white px-6 py-3 rounded-lg text-lg transition"
          >
            Back to Live Shows
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-3 sm:p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 sm:mb-6">
        <div className="min-w-0 flex-1">
          <h1 className="text-lg sm:text-xl font-bold text-cyan-300 truncate">{currentShow?.title || 'Host Console'}</h1>
          <div className="flex items-center gap-2 mt-1">
            <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-500'}`} />
            <span className="text-[11px] text-gray-400">
              {isConnected ? 'Connected' : 'Reconnecting...'}
            </span>
            {currentShow?.status === 'live' && (
              <span className="text-[10px] bg-red-700 text-white px-2 py-0.5 rounded-full animate-pulse uppercase font-bold">
                LIVE
              </span>
            )}
          </div>
        </div>
        <button
          onClick={handleEndShow}
          className="bg-red-800 hover:bg-red-700 text-red-200 px-3 sm:px-4 py-2 rounded text-sm font-bold transition flex-shrink-0 ml-2"
        >
          End Show
        </button>
      </div>

      <TalkNote

        name={talk.talkingTo ? (callers.find(c => c.id === talk.talkingTo)?.caller_name || "the caller") : null}

        error={talk.error} onStop={talk.stop} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left column: Countdown + Mic */}
        <div className="space-y-4">
          {/* Hard stop countdown */}
          <div className={`rounded-lg p-6 text-center border ${
            isUrgent ? 'bg-red-950 border-red-700' : 'bg-[#12123a] border-[#2a2a5e]'
          }`}>
            <div className="text-[11px] text-gray-400 uppercase mb-2">Time Remaining</div>
            <div className={`text-4xl sm:text-5xl font-mono font-bold ${isUrgent ? 'text-red-400' : 'text-white'}`}>
              {formatTime(localSeconds)}
            </div>
            {isUrgent && localSeconds !== null && localSeconds > 0 && (
              <div className="text-red-400 text-sm mt-2 font-bold">Less than 5 minutes!</div>
            )}
          </div>

          {/* Microphone controls */}
          <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4">
            <div className="text-[11px] text-gray-400 uppercase mb-3">Microphone</div>
            <div className="flex items-center gap-3 mb-3">
              {!isRecording ? (
                <button
                  onClick={startRecording}
                  className="bg-green-800 hover:bg-green-700 text-green-200 px-4 py-2 rounded text-sm font-bold transition flex-1"
                >
                  Start Broadcasting
                </button>
              ) : (
                <button
                  onClick={stopRecording}
                  className="bg-red-800 hover:bg-red-700 text-red-200 px-4 py-2 rounded text-sm font-bold transition flex-1"
                >
                  Stop Broadcasting
                </button>
              )}
            </div>

            {isRecording && (
              <div className="flex items-stretch gap-2 mb-3">
                <button
                  onPointerDown={e => { e.preventDefault(); setCoughing(true); }}
                  onPointerUp={() => setCoughing(false)}
                  onPointerLeave={() => setCoughing(false)}
                  onPointerCancel={() => setCoughing(false)}
                  onContextMenu={e => e.preventDefault()}
                  data-testid="cough-button"
                  title="Hold C"
                  className={`flex-1 px-3 py-3 rounded text-sm font-bold transition select-none touch-none ${
                    coughing ? 'bg-yellow-500 text-black' : 'bg-gray-700 hover:bg-gray-600 text-gray-200'}`}
                >
                  Cough
                  <span className="block text-[11px] font-normal">hold (or hold C)</span>
                </button>
                <button
                  onClick={() => setMicOff(v => !v)}
                  aria-pressed={micOff}
                  data-testid="mute-button"
                  className={`flex-1 px-3 py-3 rounded text-sm font-bold transition ${
                    micOff ? 'bg-yellow-500 text-black' : 'bg-gray-700 hover:bg-gray-600 text-gray-200'}`}
                >
                  {micOff ? 'Unmute mic' : 'Mute mic'}
                  <span className="block text-[11px] font-normal">{micOff ? 'you are not heard' : 'stays off until pressed again'}</span>
                </button>
              </div>
            )}
            {isRecording && muted && (
              <div className="text-yellow-400 text-sm mb-2 font-bold" role="status" data-testid="mic-muted-note">
                {talking
                  ? 'TALKING OFF AIR: only the caller hears you. Listeners do not. Press End talk to go back.'
                  : 'MIC OFF: listeners do not hear you. Callers on air are still heard.'}
              </div>
            )}

            {/* VU Meter */}
            <div className="h-4 bg-[#0a0a28] rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-75 rounded-full ${
                  audioLevel > 0.8 ? 'bg-red-500' : audioLevel > 0.5 ? 'bg-yellow-500' : 'bg-green-500'
                }`}
                style={{ width: `${(muted ? 0 : audioLevel) * 100}%` }}
              />
            </div>

            {reconnecting && (
              <div className="text-yellow-400 text-sm mt-2 font-bold" role="status" data-testid="reconnecting-note">
                Connection lost. Reconnecting… keep talking; you are back on air as soon as it returns.
              </div>
            )}
            {audioError && (
              <div className="text-red-400 text-[11px] mt-2">{audioError}</div>
            )}
            {isRecording && air?.mode === 'pre-roll' && (
              <div className="text-yellow-400 text-sm mt-2 font-bold" data-testid="preroll-note">
                {(air.hold ?? 0) > Math.max(0, air.target - air.delay)
                  ? `A timed item is on air first. Listeners hear you in ${Math.ceil(air.hold ?? 0)} s. Start talking now.`
                  : `Listeners hear you in ${Math.max(0, Math.ceil(air.target - air.delay))} s. Start talking now.`}
              </div>
            )}
            {isRecording && air?.on_air && !muted && (
              <div className="text-red-400 text-sm mt-2 font-bold" data-testid="heard-note">
                {air.delay > 0.5
                  ? `Listeners hear you ${air.delay.toFixed(0)} s later.`
                  : air.target > 0 ? 'Listeners hear you almost at once: the delay is building back.' : 'Listeners hear you now.'}
              </div>
            )}
          </div>

          {(show ?? showData)?.station_id && (
            <CartPanel stationId={String((show ?? showData)!.station_id)}
              live={isRecording} playing={cartPlaying} onPlay={playCart} onStop={stopCart} />
          )}

          <DelayPanel showId={showId} air={air} lastDump={lastDump} callerOnAir={!!callers.find(c => c.status === 'on_air')} />
        </div>

        {/* Center: On-air caller */}
        <div className="space-y-4">
          <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4">
            <div className="text-[11px] text-gray-400 uppercase mb-3">Caller On Air</div>
            {onAirCaller ? (
              <div className="bg-red-950 border border-red-800 rounded-lg p-4" data-testid="on-air-card">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-red-300 font-bold text-lg">
                    {onAirCaller.caller_name || onAirCaller.caller_phone}
                  </span>
                  <span className="text-[10px] bg-red-700 text-white px-2 py-0.5 rounded-full animate-pulse">
                    ON AIR
                  </span>
                </div>
                <div className="text-[11px] text-gray-500 mb-3">
                  {onAirCaller.caller_phone}
                  {onAirCaller.air_start && (
                    <span className="ml-2">
                      Air: {Math.floor((Date.now() - new Date(onAirCaller.air_start).getTime()) / 1000)}s
                    </span>
                  )}
                </div>
                <CallerNote showId={showId} caller={onAirCaller} big />
                <div className="flex gap-2">
                  <button
                    onClick={() => handleHold(onAirCaller.id)}
                    data-testid="hold-button"
                    title="Off the air, still connected: back to the approved queue"
                    className="flex-1 bg-amber-800 hover:bg-amber-700 text-amber-200 px-3 py-2 rounded text-sm transition"
                  >
                    Hold
                  </button>
                  <button
                    onClick={() => handleEndCall(onAirCaller.id)}
                    className="flex-1 bg-red-800 hover:bg-red-700 text-red-200 px-3 py-2 rounded text-sm transition"
                  >
                    End Call
                  </button>
                </div>
                {approvedCallers.length > 0 && (
                  <button
                    onClick={handleNext}
                    data-testid="next-caller-button"
                    title="Ends this call and puts the caller who waited longest on air"
                    className="mt-2 w-full bg-green-800 hover:bg-green-700 text-green-200 px-3 py-3 rounded text-sm font-bold transition"
                  >
                    Next caller: {approvedCallers[0].caller_name || approvedCallers[0].caller_phone}
                  </button>
                )}
              </div>
            ) : (
              <div className="text-gray-600 text-center py-8 text-sm">
                No caller on air
              </div>
            )}
          </div>
        </div>

        {/* Right: Approved queue */}
        <div className="space-y-4">
          <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4">
            <div className="text-[11px] text-gray-400 uppercase mb-3">
              Approved Queue ({approvedCallers.length})
            </div>
            {approvedCallers.length === 0 ? (
              <div className="text-gray-600 text-center py-8 text-sm">
                No approved callers
              </div>
            ) : (
              <div className="space-y-2">
                {approvedCallers.map(caller => (
                  <div key={caller.id} className="bg-[#0a0a28] border border-[#2a2a5e] rounded p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-white text-sm font-medium">
                        {caller.caller_name || caller.caller_phone}
                      </span>
                      <span className="text-[10px] bg-green-700 text-white px-2 py-0.5 rounded-full">
                        Approved
                      </span>
                    </div>
                    <div className="text-[11px] text-gray-500 mb-2">{caller.caller_phone}</div>
                    <CallerNote showId={showId} caller={caller} />
                    <div className="mb-2">
                      <TalkButton callId={caller.id} talkingTo={talk.talkingTo} onStart={talk.start} onStop={talk.stop} />
                    </div>
                    <button
                      onClick={() => handlePutOnAir(caller.id)}
                      disabled={!!onAirCaller}
                      className="w-full bg-green-800 hover:bg-green-700 text-green-200 px-3 py-1.5 rounded text-[11px] transition disabled:opacity-50"
                    >
                      Put On Air
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
