import { useState } from 'react';
import { updateCallInfo } from '../../../api/liveShows';
import type { CallInRequest } from '../../../api/liveShows';

/**
 * What the caller wants to say (the topic they typed when calling in) and the
 * screener's notes for the host: shown on every caller card of the Call Screener and
 * the Host Console, and editable in place. Before this the topic was stored and
 * shown nowhere, so the host put callers on air blind.
 */
export default function CallerNote({ showId, caller, big = false }: {
  showId: string | undefined;
  caller: CallInRequest;
  big?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const save = async () => {
    if (!showId || saving) return;
    setSaving(true);
    setFailed(false);
    try {
      await updateCallInfo(showId, caller.id, { notes: text.trim() });
      setEditing(false);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <div className="mb-2" data-testid="caller-note-edit">
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows={2}
          maxLength={1000}
          autoFocus
          aria-label="Note about this caller"
          placeholder="Topic, where they are calling from, anything the host should know"
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); save(); }
            if (e.key === 'Escape') { e.stopPropagation(); setEditing(false); }
          }}
          className="w-full bg-[#0a0a28] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
        />
        <div className="flex items-center gap-2 mt-1">
          <button onClick={save} disabled={saving} data-testid="caller-note-save"
            className="bg-cyan-800 hover:bg-cyan-700 text-cyan-200 px-3 py-1.5 rounded text-[11px] transition disabled:opacity-50">
            Save note
          </button>
          <button onClick={() => setEditing(false)}
            className="bg-gray-700 hover:bg-gray-600 text-gray-200 px-3 py-1.5 rounded text-[11px] transition">
            Cancel
          </button>
          {failed && <span className="text-[11px] text-red-400" role="status">Not saved. Try again.</span>}
        </div>
      </div>
    );
  }
  return (
    <div className="mb-2 flex items-start gap-2" data-testid="caller-note">
      <div className={`min-w-0 flex-1 whitespace-pre-wrap break-words ${big ? 'text-base' : 'text-sm'} ${caller.notes ? 'text-yellow-300' : 'text-gray-500'}`}>
        {caller.notes || 'No note'}
      </div>
      <button
        onClick={() => { setText(caller.notes || ''); setFailed(false); setEditing(true); }}
        data-testid="caller-note-button"
        className="text-[11px] text-gray-400 hover:text-cyan-400 underline shrink-0 px-1 py-1"
      >
        {caller.notes ? 'edit note' : 'add note'}
      </button>
    </div>
  );
}
