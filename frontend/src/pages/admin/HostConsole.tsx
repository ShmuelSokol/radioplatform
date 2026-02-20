import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useLiveShow, useEndLiveShow } from '../../hooks/useLiveShows';
import { useLiveShowWS } from '../../hooks/useLiveShowWS';
import { useHostAudio } from '../../hooks/useHostAudio';
import { endCall, putCallerOnAir } from '../../api/liveShows';

export default function HostConsole() {
  const { showId } = useParams<{ showId: string }>();
  const navigate = useNavigate();
  const { show, callers, secondsRemaining, isConnected } = useLiveShowWS(showId);
  const { data: showData } = useLiveShow(showId);
  const endShowMut = useEndLiveShow();
  const { isRecording, startRecording, stopRecording, audioLevel, error: audioError } = useHostAudio(showId);

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

  const handlePutOnAir = async (callId: string) => {
    if (!showId) return;
    try {
      await putCallerOnAir(showId, callId);
    } catch (err: any) {
      alert(err?.response?.data?.detail || 'Failed to put caller on air');
    }
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
    <div className="min-h-screen bg-black text-white p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-cyan-300">{currentShow?.title || 'Host Console'}</h1>
          <div className="flex items-center gap-2 mt-1">
            <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-500'}`} />
            <span className="text-[11px] text-gray-500">
              {isConnected ? 'Connected' : 'Reconnecting...'}
            </span>
            {currentShow?.status === 'live' && (
              <span className="text-[10px] bg-red-700 text-red-200 px-2 py-0.5 rounded-full animate-pulse uppercase font-bold">
                LIVE
              </span>
            )}
          </div>
        </div>
        <button
          onClick={handleEndShow}
          className="bg-red-800 hover:bg-red-700 text-red-200 px-4 py-2 rounded text-sm font-bold transition"
        >
          End Show
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left column: Countdown + Mic */}
        <div className="space-y-4">
          {/* Hard stop countdown */}
          <div className={`rounded-lg p-6 text-center border ${
            isUrgent ? 'bg-red-950 border-red-700' : 'bg-[#12123a] border-[#2a2a5e]'
          }`}>
            <div className="text-[11px] text-gray-400 uppercase mb-2">Time Remaining</div>
            <div className={`text-5xl font-mono font-bold ${isUrgent ? 'text-red-400' : 'text-white'}`}>
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

            {/* VU Meter */}
            <div className="h-4 bg-[#0a0a28] rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-75 rounded-full ${
                  audioLevel > 0.8 ? 'bg-red-500' : audioLevel > 0.5 ? 'bg-yellow-500' : 'bg-green-500'
                }`}
                style={{ width: `${audioLevel * 100}%` }}
              />
            </div>

            {audioError && (
              <div className="text-red-400 text-[11px] mt-2">{audioError}</div>
            )}
          </div>
        </div>

        {/* Center: On-air caller */}
        <div className="space-y-4">
          <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4">
            <div className="text-[11px] text-gray-400 uppercase mb-3">Caller On Air</div>
            {onAirCaller ? (
              <div className="bg-red-950 border border-red-800 rounded-lg p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-red-300 font-bold text-lg">
                    {onAirCaller.caller_name || onAirCaller.caller_phone}
                  </span>
                  <span className="text-[10px] bg-red-700 text-red-200 px-2 py-0.5 rounded-full animate-pulse">
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
                <button
                  onClick={() => handleEndCall(onAirCaller.id)}
                  className="w-full bg-red-800 hover:bg-red-700 text-red-200 px-3 py-2 rounded text-sm transition"
                >
                  End Call
                </button>
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
                      <span className="text-[10px] bg-green-800 text-green-200 px-2 py-0.5 rounded-full">
                        Approved
                      </span>
                    </div>
                    <div className="text-[11px] text-gray-500 mb-2">{caller.caller_phone}</div>
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
