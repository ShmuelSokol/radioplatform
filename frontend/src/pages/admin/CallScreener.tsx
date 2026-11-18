import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useLiveShow } from '../../hooks/useLiveShows';
import { useLiveShowWS } from '../../hooks/useLiveShowWS';
import { approveCall, rejectCall, putCallerOnAirAsking, endCall, holdCall, nextCallerAsking, updateCallInfo } from '../../api/liveShows';
import type { CallInRequest } from '../../api/liveShows';
import DelayPanel from './live/DelayPanel';
import CallerNote from './live/CallerNote';
import CallerHistory from './live/CallerHistory';
import MarkedCallers from './live/MarkedCallers';
import ChatPanel from './live/ChatPanel';
import TalkButton, { TalkNote } from './live/TalkButton';
import { useTalkback } from '../../hooks/useTalkback';

const MAX_ON_AIR = 4; // the server's limit (live_show_service.MAX_CALLERS_ON_AIR)

export default function CallScreener() {
  const { showId } = useParams<{ showId: string }>();
  const { show, callers, secondsRemaining, isConnected, air, lastDump, lastChat } = useLiveShowWS(showId);
  const { data: showData } = useLiveShow(showId);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [nameValue, setNameValue] = useState('');
  const talk = useTalkback(showId);

  const currentShow = show || showData;

  // Priority callers first; among equals, who waited longest (the order they arrived in)
  const waitingCallers = callers
    .filter(c => c.status === 'waiting' || c.status === 'screening')
    .map((c, i) => ({ c, i }))
    .sort((a, b) => Number(b.c.history?.standing === 'priority') - Number(a.c.history?.standing === 'priority') || a.i - b.i)
    .map(x => x.c);
  const standings = callers.map(c => `${c.id}:${c.history?.standing ?? ''}`).join(',');
  const approvedCallers = callers.filter(c => c.status === 'approved');
  // Up to MAX_ON_AIR callers can be on air together; each hears the host and the others
  const onAirCallers = callers.filter(c => c.status === 'on_air');
  const anyOnAir = onAirCallers.length > 0;

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
    const problem = await putCallerOnAirAsking(showId, callId);
    if (problem) alert(problem);
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
    <div className="w-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-purple-300">Call Screener</h1>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-sm text-gray-400">{currentShow?.title}</span>
            <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-500'}`} />
            {currentShow?.status === 'live' && (
              <span className="text-[10px] bg-red-700 text-white px-2 py-0.5 rounded-full animate-pulse uppercase font-bold">
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

      <div className="mb-6 grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        <DelayPanel showId={showId} air={air} lastDump={lastDump} callerOnAir={anyOnAir} compact />
        <ChatPanel showId={showId} lastChat={lastChat} />
      </div>

      <TalkNote

        name={talk.talkingTo ? (callers.find(c => c.id === talk.talkingTo)?.caller_name || "the caller") : null}

        error={talk.error} onStop={talk.stop} />

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
                    <span className="text-[10px] bg-amber-800 text-white px-2 py-0.5 rounded-full">
                      Hold
                    </span>
                  </div>

                  <CallerNote showId={showId} caller={caller} />
                  <CallerHistory showId={showId} caller={caller} />

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
                    <TalkButton callId={caller.id} talkingTo={talk.talkingTo} onStart={talk.start} onStop={talk.stop} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: On Air + Approved */}
        <div>
          {/* On Air */}
          {anyOnAir && (
            <div className="mb-6">
              <h2 className="text-sm font-bold text-red-400 uppercase mb-3">On Air{onAirCallers.length > 1 ? ` (${onAirCallers.length} together)` : ''}</h2>
              {onAirCallers.map(onAirCaller => (
              <div key={onAirCaller.id} className="bg-red-950 border border-red-800 rounded-lg p-4 mb-2" data-testid="on-air-card">
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
                <CallerNote showId={showId} caller={onAirCaller} />
                <CallerHistory showId={showId} caller={onAirCaller} />
                <div className="flex gap-2">
                  <button
                    onClick={() => handleHold(onAirCaller.id)}
                    data-testid="hold-button"
                    title="Off the air, still connected: back to Approved"
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
              </div>
              ))}
            </div>
          )}

          {/* Approved Queue */}
          <div className="flex items-center justify-between gap-2 mb-3">
            <h2 className="text-sm font-bold text-gray-400 uppercase">
              Approved ({approvedCallers.length})
            </h2>
            {approvedCallers.length > 0 && anyOnAir && (
              <button
                onClick={handleNext}
                data-testid="next-caller-button"
                title="Ends every call on air and puts the caller who waited longest on air"
                className="bg-green-800 hover:bg-green-700 text-green-200 px-3 py-1.5 rounded text-[11px] font-bold transition"
              >
                Next caller
              </button>
            )}
          </div>
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
                    <span className="text-[10px] bg-green-700 text-white px-2 py-0.5 rounded-full">
                      Approved
                    </span>
                  </div>
                  <CallerNote showId={showId} caller={caller} />
                  <CallerHistory showId={showId} caller={caller} />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handlePutOnAir(caller.id)}
                      disabled={onAirCallers.length >= MAX_ON_AIR}
                      title={anyOnAir ? (onAirCallers.length >= MAX_ON_AIR ? `${MAX_ON_AIR} callers are on air already` : 'Goes on air together with the caller already there') : undefined}
                      className="flex-1 bg-green-800 hover:bg-green-700 text-green-200 px-3 py-1.5 rounded text-[11px] transition disabled:opacity-50"
                    >
                      Put On Air
                    </button>
                    <TalkButton callId={caller.id} talkingTo={talk.talkingTo} onStart={talk.start} onStop={talk.stop} />
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

      <MarkedCallers refresh={standings} />
    </div>
  );
}
