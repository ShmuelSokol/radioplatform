interface Props {
  callId: string;
  talkingTo: string | null;
  onStart: (callId: string) => void;
  onStop: () => void;
}

/** Talk to a caller off the air (talkback). Lit while talking; press again to end. */
export default function TalkButton({ callId, talkingTo, onStart, onStop }: Props) {
  const on = talkingTo === callId;
  return (
    <button
      onClick={() => (on ? onStop() : onStart(callId))}
      aria-pressed={on}
      data-testid="talk-button"
      title={on ? 'End the private talk' : 'Talk to this caller off the air. Listeners hear nothing of it.'}
      className={`px-3 py-1 rounded text-xs font-bold transition ${
        on ? 'bg-yellow-500 text-black' : 'bg-gray-700 hover:bg-gray-600 text-white'}`}
    >
      {on ? 'End talk' : 'Talk off air'}
    </button>
  );
}

/** The line across the screen while a private talk is open, and why one could not start. */
export function TalkNote({ name, error, onStop }: { name: string | null; error: string | null; onStop: () => void }) {
  if (!name && !error) return null;
  return (
    <div className="mb-4" data-testid="talk-banner">
      {name && (
        <div className="bg-yellow-500 text-black rounded-lg px-4 py-3 flex flex-wrap items-center justify-between gap-2 font-bold" role="status">
          <span>Talking off air to {name}. Listeners hear nothing of this.</span>
          <button onClick={onStop} className="bg-black text-white px-3 py-1 rounded text-sm font-bold">End talk</button>
        </div>
      )}
      {error && <div className="text-red-400 text-sm mt-2" role="alert">{error}</div>}
    </div>
  );
}
