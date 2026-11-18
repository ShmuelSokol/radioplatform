import { useEffect, useRef, useState } from 'react';
import { getShowChat, sayInShowChat } from '../../../api/liveShows';
import type { ChatMessage } from '../../../api/liveShows';
import { useAuth } from '../../../hooks/useAuth';

/**
 * The staff chat of a live show: the host and the screener write to each other and
 * nothing of it goes on air. Lines arrive on the show's socket (`lastChat`); the list
 * is also read again every 10 s, so a line missed while the socket was down shows up.
 */
export default function ChatPanel({ showId, lastChat }: { showId: string | undefined; lastChat: ChatMessage | null }) {
  const { user } = useAuth();
  const [lines, setLines] = useState<ChatMessage[]>([]);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState<string | null>(null);   // a line from someone else, lit for a while
  const list = useRef<HTMLDivElement | null>(null);

  const merge = (more: ChatMessage[]) => setLines(prev => {
    const seen = new Set(prev.map(m => m.id));
    const added = more.filter(m => !seen.has(m.id));
    return added.length ? [...prev, ...added].sort((a, b) => (a.at || '').localeCompare(b.at || '')) : prev;
  });

  useEffect(() => {
    if (!showId) return;
    let stop = false;
    const read = () => getShowChat(showId).then(m => { if (!stop) merge(m); }).catch(() => {});
    read();
    const timer = setInterval(read, 10000);
    return () => { stop = true; clearInterval(timer); };
  }, [showId]);

  useEffect(() => {
    if (!lastChat) return;
    merge([lastChat]);
    if (lastChat.user_id !== (user?.id ?? null)) {
      setFresh(lastChat.id);
      const t = setTimeout(() => setFresh(f => (f === lastChat.id ? null : f)), 8000);
      return () => clearTimeout(t);
    }
  }, [lastChat, user?.id]);

  useEffect(() => { const el = list.current; if (el) el.scrollTop = el.scrollHeight; }, [lines.length]);

  const send = async () => {
    const t = text.trim();
    if (!showId || !t || sending) return;
    setSending(true);
    setError(null);
    try {
      merge([await sayInShowChat(showId, t)]);
      setText('');
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'The message was not sent. Try again.');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className={`bg-[#12123a] border rounded-lg p-4 ${fresh ? 'border-yellow-400' : 'border-[#2a2a5e]'}`} data-testid="chat-panel">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] text-gray-400 uppercase">Staff chat</div>
        <div className="text-[11px] text-gray-400">off the air</div>
      </div>
      <div ref={list} className="max-h-48 overflow-y-auto space-y-1 mb-2" data-testid="chat-lines" aria-live="polite">
        {lines.length === 0 ? (
          <p className="text-gray-400 text-sm">No messages yet. The host and the screener can write to each other here.</p>
        ) : lines.map(m => (
          <div key={m.id} data-testid="chat-line"
            className={`text-sm rounded px-2 py-1 break-words ${m.id === fresh ? 'bg-yellow-500 text-black font-bold' : 'text-white'}`}>
            <span className="text-[11px] opacity-80 mr-2">
              {m.at ? new Date(m.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''} {m.by}
            </span>
            {m.text}
          </div>
        ))}
      </div>
      <form className="flex gap-2" onSubmit={e => { e.preventDefault(); send(); }}>
        <input
          value={text}
          onChange={e => setText(e.target.value)}
          maxLength={500}
          aria-label="Message to the show's staff"
          placeholder="Message the host or screener"
          className="flex-1 min-w-0 min-h-[44px] bg-[#0a0a28] border border-[#2a2a5e] text-white text-sm rounded px-3 py-2"
        />
        <button type="submit" disabled={sending || !text.trim()} data-testid="chat-send"
          className="min-h-[44px] bg-cyan-700 hover:bg-cyan-600 text-white px-4 rounded text-sm font-bold disabled:opacity-50">
          Send
        </button>
      </form>
      {error && <div className="text-red-400 text-[11px] mt-2" role="alert">{error}</div>}
    </div>
  );
}
