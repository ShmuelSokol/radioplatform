import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useLiveShow } from '../../hooks/useLiveShows';
import { useLiveShowWS } from '../../hooks/useLiveShowWS';
import { approveCall, rejectCall, putCallerOnAir, endCall, updateCallInfo } from '../../api/liveShows';
import type { CallInRequest } from '../../api/liveShows';

export default function CallScreener() {
  const { showId } = useParams<{ showId: string }>();
  const { show, callers, secondsRemaining, isConnected } = useLiveShowWS(showId);
  const { data: showData } = useLiveShow(showId);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [nameValue, setNameValue] = useState('');

  const currentShow = show || showData;

  const waitingCallers = callers.filter(c => c.status === 'waiting' || c.status === 'screening');
  const approvedCallers = callers.filter(c => c.status === 'approved');
  const onAirCaller = callers.find(c => c.status === 'on_air');

  const handleApprove = async (callId: string) => {
    if (!showId) return;
    await approveCall(showId, callId);
  };

  const handleReject = async (callId: string) => {
    if (!showId) return;
    await rejectCall(showId, callId);
  };

  const handlePutOnAir = async (callId: string) => {
    if (!showId) return;
    try {
      await putCallerOnAir(showId, callId);
    } catch (err: any) {
      alert(err?.response?.data?.detail || 'Failed — another caller may be on air');
    }
  };

  const handleEndCall = async (callId: string) => {
    if (!showId) return;
    await endCall(showId, callId);
  };

  const handleSaveName = async (callId: string) => {
    if (!showId) return;
    await updateCallInfo(showId, callId, { caller_name: nameValue });
    setEditingName(null);
  };

  const getWaitTime = (caller: CallInRequest): string => {
    if (!caller.hold_start) return '--';
    const seconds = Math.floor((Date.now() - new Date(caller.hold_start).getTime()) / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${seconds % 60}s`;
  };

  return (
    <div className="max-w-7xl mx-auto py-6 px-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-purple-300">Call Screener</h1>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-sm text-gray-400">{currentShow?.title}</span>
            <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-500'}`} />
            {currentShow?.status === 'live' && (
              <span className="text-[10px] bg-red-700 text-red-200 px-2 py-0.5 rounded-full animate-pulse uppercase font-bold">
                LIVE
              </span>
            )}
          </div>
        </div>
        {secondsRemaining !== null && (
          <div className="text-right">
            <div className="text-[11px] text-gray-500 uppercase">Time Left</div>
            <div className={`text-xl font-mono font-bold ${secondsRemaining < 300 ? 'text-red-400' : 'text-white'}`}>
              {Math.floor(secondsRemaining / 60)}:{(Math.floor(secondsRemaining) % 60).toString().padStart(2, '0')}
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Waiting callers */}
        <div>
          <h2 className="text-sm font-bold text-gray-400 uppercase mb-3">
            Waiting ({waitingCallers.length})
          </h2>
          {waitingCallers.length === 0 ? (
            <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-8 text-center text-gray-600 text-sm">
              No callers waiting
            </div>
          ) : (
            <div className="space-y-2">
              {waitingCallers.map(caller => (
                <div key={caller.id} className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex-1 min-w-0">
                      {editingName === caller.id ? (
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            value={nameValue}
                            onChange={e => setNameValue(e.target.value)}
                            className="bg-[#0a0a28] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white w-32"
                            placeholder="Caller name"
                            autoFocus
                            onKeyDown={e => {
                              if (e.key === 'Enter') handleSaveName(caller.id);
                              if (e.key === 'Escape') setEditingName(null);
                            }}
                          />
                          <button
                            onClick={() => handleSaveName(caller.id)}
                            className="text-[10px] text-cyan-400 hover:text-cyan-300"
                          >
                            Save
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2">
                          <span className="text-white font-medium truncate">
                            {caller.caller_name || caller.caller_phone}
                          </span>
                          <button
                            onClick={() => {
                              setEditingName(caller.id);
                              setNameValue(caller.caller_name || '');
                            }}
                            className="text-[10px] text-gray-500 hover:text-cyan-400"
                          >
                            edit
                          </button>
                        </div>
                      )}
                      <div className="text-[11px] text-gray-500">
                        {caller.caller_phone} &middot; waiting {getWaitTime(caller)}
                      </div>
                    </div>
                    <span className="text-[10px] bg-amber-800 text-amber-200 px-2 py-0.5 rounded-full">
                      Hold
                    </span>
                  </div>

                  <div className="flex gap-2">
                    <button
                      onClick={() => handleApprove(caller.id)}
                      className="flex-1 bg-green-800 hover:bg-green-700 text-green-200 px-3 py-1.5 rounded text-[11px] transition"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => handleReject(caller.id)}
                      className="flex-1 bg-red-900 hover:bg-red-800 text-red-300 px-3 py-1.5 rounded text-[11px] transition"
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: On Air + Approved */}
        <div>
          {/* On Air */}
          {onAirCaller && (
            <div className="mb-6">
              <h2 className="text-sm font-bold text-red-400 uppercase mb-3">On Air</h2>
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
            </div>
          )}

          {/* Approved Queue */}
          <h2 className="text-sm font-bold text-gray-400 uppercase mb-3">
            Approved ({approvedCallers.length})
          </h2>
          {approvedCallers.length === 0 ? (
            <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-8 text-center text-gray-600 text-sm">
              No approved callers
            </div>
          ) : (
            <div className="space-y-2">
              {approvedCallers.map(caller => (
                <div key={caller.id} className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <span className="text-white font-medium">
                        {caller.caller_name || caller.caller_phone}
                      </span>
                      <div className="text-[11px] text-gray-500">{caller.caller_phone}</div>
                    </div>
                    <span className="text-[10px] bg-green-800 text-green-200 px-2 py-0.5 rounded-full">
                      Approved
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handlePutOnAir(caller.id)}
                      disabled={!!onAirCaller}
                      className="flex-1 bg-green-800 hover:bg-green-700 text-green-200 px-3 py-1.5 rounded text-[11px] transition disabled:opacity-50"
                    >
                      Put On Air
                    </button>
                    <button
                      onClick={() => handleEndCall(caller.id)}
                      className="bg-red-900 hover:bg-red-800 text-red-300 px-3 py-1.5 rounded text-[11px] transition"
                    >
                      Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
