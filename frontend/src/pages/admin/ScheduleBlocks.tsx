import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import apiClient from '../../api/client';
import { useAssets } from '../../hooks/useAssets';
import type { ScheduleBlock, CreateScheduleBlockData } from '../../hooks/useSchedules';
import { useCreateScheduleBlock, useUpdateScheduleBlock, useDeleteScheduleBlock, useCreatePlaylistEntry, useDeletePlaylistEntry } from '../../hooks/useSchedules';
import { usePlaylistTemplates } from '../../hooks/usePlaylists';
import AssetCategoryBadge from '../../components/AssetCategoryBadge';
import { useAuth } from '../../hooks/useAuth';
import { isManager } from '../../auth/access';
import { useStations } from '../../hooks/useStations';
import HolidayMixNote from '../../components/holiday/HolidayMixNote';

export default function ScheduleBlocks() {
  const { scheduleId } = useParams<{ scheduleId: string }>();
  const { user } = useAuth();
  // Only managers and admins may change blocks (the API refuses the rest); a DJ or viewer
  // was offered New Block, Edit, Delete, + Add song and Remove beside the "View only" badge
  const canEdit = isManager(user?.role);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: schedule } = useQuery({
    queryKey: ['schedules', scheduleId],
    queryFn: async () => { const r = await apiClient.get(`/schedules/${scheduleId}`); return r.data; },
    enabled: !!scheduleId,
  });

  const { data: blocks, isLoading } = useQuery<ScheduleBlock[]>({
    queryKey: ['schedule-blocks', scheduleId],
    queryFn: async () => { const r = await apiClient.get('/schedules/blocks', { params: { schedule_id: scheduleId } }); return r.data; },
    enabled: !!scheduleId,
  });

  const { data: stationsData } = useStations();
  const scheduleStation = (stationsData?.stations ?? []).find((s: any) => s.id === schedule?.station_id);

  const { data: assetsData } = useAssets();
  const assets = assetsData?.assets || [];

  const { data: playlistTemplates } = usePlaylistTemplates();
  const createBlock = useCreateScheduleBlock();
  const updateBlock = useUpdateScheduleBlock();
  const deleteBlock = useDeleteScheduleBlock();
  const createEntry = useCreatePlaylistEntry();
  const deleteEntry = useDeletePlaylistEntry();

  const [showBlockForm, setShowBlockForm] = useState(false);
  const [editingBlockId, setEditingBlockId] = useState<string | null>(null);
  const [expandedBlock, setExpandedBlock] = useState<string | null>(null);
  const emptyBlock = (): CreateScheduleBlockData => ({
    schedule_id: scheduleId || '',
    name: '',
    start_time: '08:00',
    end_time: '12:00',
    recurrence_type: 'daily',
    priority: 0,
    playback_mode: 'sequential',
  });
  const [blockForm, setBlockForm] = useState<CreateScheduleBlockData>(emptyBlock);

  const closeBlockForm = () => {
    setShowBlockForm(false);
    setEditingBlockId(null);
    setFormError('');
    setBlockForm(emptyBlock());
  };

  const startEdit = (block: ScheduleBlock) => {
    setEditingBlockId(block.id);
    setFormError('');
    setBlockForm({
      schedule_id: block.schedule_id,
      name: block.name,
      start_time: block.start_time?.slice(0, 5) || '08:00',
      end_time: block.end_time?.slice(0, 5) || '12:00',
      recurrence_type: block.recurrence_type,
      recurrence_pattern: block.recurrence_pattern || undefined,
      priority: block.priority,
      playback_mode: block.playback_mode,
      start_date: block.start_date ?? null,
      end_date: block.end_date ?? null,
      start_sun_event: block.start_sun_event ?? undefined,
      start_sun_offset: block.start_sun_offset ?? 0,
      end_sun_event: block.end_sun_event ?? undefined,
      end_sun_offset: block.end_sun_offset ?? 0,
      playlist_template_id: block.playlist_template_id ?? null,
      until_next: !!block.until_next,
    });
    setShowBlockForm(true);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const [addingEntryTo, setAddingEntryTo] = useState<string | null>(null);
  const [selectedAssetId, setSelectedAssetId] = useState('');

  const [formError, setFormError] = useState('');

  const handleCreateBlock = async (e: React.FormEvent) => {
    e.preventDefault();
    const weekly = blockForm.recurrence_type === 'weekly';
    if (weekly && !(blockForm.recurrence_pattern || []).length) {
      setFormError('Pick at least one day for a weekly block.');
      return;
    }
    setFormError('');
    const startOnly = !!blockForm.until_next;
    const payload = {
      ...blockForm,
      schedule_id: scheduleId || '',
      // days only mean something on a weekly block
      recurrence_pattern: weekly ? blockForm.recurrence_pattern : undefined,
      // a start-only block has no end; the server ignores it
      end_time: startOnly ? blockForm.start_time : blockForm.end_time,
      // null (not undefined) so an edit clears a sun time that was taken off
      start_sun_event: blockForm.start_sun_event || null,
      start_sun_offset: blockForm.start_sun_event ? blockForm.start_sun_offset ?? 0 : null,
      end_sun_event: startOnly ? null : blockForm.end_sun_event || null,
      end_sun_offset: startOnly || !blockForm.end_sun_event ? null : blockForm.end_sun_offset ?? 0,
    };
    try {
      if (editingBlockId) {
        const { schedule_id: _ignored, ...changes } = payload;
        void _ignored;
        await updateBlock.mutateAsync({
          id: editingBlockId,
          data: { ...changes, recurrence_pattern: weekly ? changes.recurrence_pattern : [] },
        });
      } else {
        await createBlock.mutateAsync(payload);
      }
      closeBlockForm();
    } catch (err) {
      console.error('Failed to create block:', err);
      setFormError('Could not save the block. Check the fields and try again.');
    }
  };

  const handleDeleteBlock = async (id: string) => {
    if (confirm('Delete this block and all its entries?')) {
      await deleteBlock.mutateAsync(id);
    }
  };

  const handleAddEntry = async (blockId: string) => {
    if (!selectedAssetId) return;
    const existing = blocks?.find(b => b.id === blockId)?.playlist_entries || [];
    await createEntry.mutateAsync({
      block_id: blockId,
      asset_id: selectedAssetId,
      position: existing.length,
    });
    setSelectedAssetId('');
    setAddingEntryTo(null);
    queryClient.invalidateQueries({ queryKey: ['schedule-blocks'] });
  };

  const handleDeleteEntry = async (entryId: string) => {
    await deleteEntry.mutateAsync(entryId);
    queryClient.invalidateQueries({ queryKey: ['schedule-blocks'] });
  };

  const sunEvents = [
    { value: '', label: 'Fixed time' },
    { value: 'sunrise', label: 'Sunrise' },
    { value: 'sunset', label: 'Sunset' },
    { value: 'dawn', label: 'Dawn' },
    { value: 'dusk', label: 'Dusk' },
  ];

  const weekDays = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  const dayShort = (d: string) => d.charAt(0).toUpperCase() + d.slice(1, 3);
  const toggleDay = (day: string) => {
    const current: string[] = blockForm.recurrence_pattern || [];
    const next = current.includes(day) ? current.filter(d => d !== day) : [...current, day];
    setBlockForm({ ...blockForm, recurrence_pattern: weekDays.filter(d => next.includes(d)) });
  };
  // Starts the studio program keys to the calendar. Candle lighting is 18 minutes before sunset.
  const startPresets = [
    {
      label: 'Friday, candle lighting + 1 min',
      apply: { recurrence_type: 'weekly' as const, recurrence_pattern: ['friday'], start_sun_event: 'sunset' as const, start_sun_offset: -17, until_next: true },
    },
    {
      label: '72 min after Shabbos',
      apply: { recurrence_type: 'weekly' as const, recurrence_pattern: ['saturday'], start_sun_event: 'sunset' as const, start_sun_offset: 72, until_next: true },
    },
  ];

  const sunText = (event?: string | null, offset?: number | null) => {
    if (!event) return '';
    const o = offset || 0;
    return o === 0 ? event : `${Math.abs(o)} min ${o < 0 ? 'before' : 'after'} ${event}`;
  };
  const clockText = (hhmm?: string) => {
    if (!hhmm) return '';
    const [h, m] = hhmm.split(':').map(Number);
    return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;
  };
  const startText = blockForm.start_sun_event
    ? sunText(blockForm.start_sun_event, blockForm.start_sun_offset)
    : clockText(blockForm.start_time);
  const endText = blockForm.until_next
    ? 'the next playlist starts'
    : blockForm.end_sun_event
      ? sunText(blockForm.end_sun_event, blockForm.end_sun_offset)
      : clockText(blockForm.end_time);

  if (isLoading) return <div className="p-8">Loading...</div>;

  return (
    <div className="w-full">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mb-3">
        <button onClick={() => navigate('/admin/schedules')} className="text-blue-600 hover:text-blue-800">&larr; Schedules</button>
        <h1 className="text-2xl sm:text-3xl font-bold">{schedule?.name || 'Schedule'}: blocks</h1>
        {canEdit && (
          <button onClick={() => (showBlockForm ? closeBlockForm() : setShowBlockForm(true))}
            className="ml-auto px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
            {showBlockForm ? 'Cancel' : 'New Block'}
          </button>
        )}
      </div>
      <p className="text-sm text-gray-600 mb-4 max-w-3xl">
        A block says when a playlist plays: which days, and what time it starts. Pick a playlist
        (a repeating order of song types, made on the Playlists page) and the station keeps playing
        it until the block ends, or until the next block starts.
      </p>
      {scheduleStation && <HolidayMixNote stations={[scheduleStation]} />}

      {showBlockForm && (
        <form onSubmit={handleCreateBlock} className="bg-white p-6 rounded-lg shadow-md mb-6 space-y-4">
          <h2 className="text-lg font-semibold">{editingBlockId ? `Edit "${blockForm.name}"` : 'New block'}</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Block Name</label>
              <input required type="text" value={blockForm.name}
                onChange={e => setBlockForm({ ...blockForm, name: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg" placeholder="Morning Show" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Repeats</label>
              <select value={blockForm.recurrence_type}
                onChange={e => setBlockForm({ ...blockForm, recurrence_type: e.target.value as any, recurrence_pattern: e.target.value === 'weekly' ? blockForm.recurrence_pattern : undefined })}
                className="w-full px-3 py-2 border rounded-lg">
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
                <option value="one_time">One time (dates)</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Playlist</label>
            <select value={blockForm.playlist_template_id || ''}
              onChange={e => setBlockForm({ ...blockForm, playlist_template_id: e.target.value || null })}
              className="w-full px-3 py-2 border rounded-lg">
              <option value="">None: I'll pick specific songs for this block</option>
              {playlistTemplates?.map(t => (
                <option key={t.id} value={t.id}>{t.name} ({t.slots.length} steps)</option>
              ))}
            </select>
          </div>
          {blockForm.recurrence_type === 'weekly' && (
            <div>
              <label className="block text-sm font-medium mb-1">Days</label>
              <div className="flex flex-wrap gap-2">
                {weekDays.map(day => {
                  const on = (blockForm.recurrence_pattern || []).includes(day);
                  return (
                    <button type="button" key={day} onClick={() => toggleDay(day)} aria-pressed={on}
                      className={`px-3 py-1.5 rounded-lg border text-sm ${on ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 hover:bg-gray-50'}`}>
                      {dayShort(day)}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" className="h-4 w-4" checked={!!blockForm.until_next}
                onChange={e => setBlockForm({ ...blockForm, until_next: e.target.checked })} />
              Runs until the next playlist starts (no end time)
            </label>
            <span className="text-sm text-gray-500">Quick starts:</span>
            {startPresets.map(preset => (
              <button type="button" key={preset.label}
                onClick={() => setBlockForm(f => ({ ...f, ...preset.apply }))}
                className="px-3 py-1.5 rounded-lg border text-sm hover:bg-gray-50">
                {preset.label}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Start Time</label>
              {blockForm.start_sun_event ? (
                <div className="w-full px-3 py-2 border rounded-lg bg-gray-50 text-gray-700 text-sm" data-testid="start-by-sun">
                  {startText}
                </div>
              ) : (
              <input type="time" value={blockForm.start_time}
                onChange={e => {
                  const v = e.target.value;
                  const updates: any = { start_time: v };
                  if (blockForm.end_time && blockForm.end_time < v) updates.end_time = v;
                  setBlockForm(f => ({ ...f, ...updates }));
                }}
                className="w-full px-3 py-2 border rounded-lg" />
              )}
            </div>
            <div className={blockForm.until_next ? 'invisible' : ''}>
              <label className="block text-sm font-medium mb-1">End Time</label>
              {blockForm.end_sun_event ? (
                <div className="w-full px-3 py-2 border rounded-lg bg-gray-50 text-gray-700 text-sm">
                  {endText}
                </div>
              ) : (
              <input type="time" value={blockForm.end_time}
                onChange={e => setBlockForm({ ...blockForm, end_time: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg" />
              )}
            </div>
            <div>
              <label className="block text-sm font-medium mb-1" title="When two blocks overlap, the higher number plays">Priority (higher wins)</label>
              <input type="number" value={blockForm.priority} min={0} max={100}
                onChange={e => setBlockForm({ ...blockForm, priority: parseInt(e.target.value) || 0 })}
                className="w-full px-3 py-2 border rounded-lg" />
            </div>
            <div className={blockForm.playlist_template_id ? 'hidden' : ''}>
              <label className="block text-sm font-medium mb-1">Order of songs</label>
              <select value={blockForm.playback_mode}
                onChange={e => setBlockForm({ ...blockForm, playback_mode: e.target.value as any })}
                className="w-full px-3 py-2 border rounded-lg">
                <option value="sequential">Sequential</option>
                <option value="shuffle">Shuffle</option>
                <option value="weighted">Weighted</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Or start by the sun</label>
              <select value={blockForm.start_sun_event || ''}
                onChange={e => setBlockForm({ ...blockForm, start_sun_event: e.target.value as any || undefined })}
                className="w-full px-3 py-2 border rounded-lg">
                {sunEvents.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Minutes after (− before)</label>
              <input type="number" value={blockForm.start_sun_offset || 0}
                onChange={e => setBlockForm({ ...blockForm, start_sun_offset: parseInt(e.target.value) || 0 })}
                className="w-full px-3 py-2 border rounded-lg" />
            </div>
            <div className={blockForm.until_next ? 'hidden' : ''}>
              <label className="block text-sm font-medium mb-1">Or end by the sun</label>
              <select value={blockForm.end_sun_event || ''}
                onChange={e => setBlockForm({ ...blockForm, end_sun_event: e.target.value as any || undefined })}
                className="w-full px-3 py-2 border rounded-lg">
                {sunEvents.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div className={blockForm.until_next ? 'hidden' : ''}>
              <label className="block text-sm font-medium mb-1">Minutes after (− before)</label>
              <input type="number" value={blockForm.end_sun_offset || 0}
                onChange={e => setBlockForm({ ...blockForm, end_sun_offset: parseInt(e.target.value) || 0 })}
                className="w-full px-3 py-2 border rounded-lg" />
            </div>
          </div>
          {blockForm.recurrence_type === 'one_time' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Start Date</label>
                <input type="date" value={blockForm.start_date || ''}
                  onChange={e => setBlockForm({ ...blockForm, start_date: e.target.value || null })}
                  className="w-full px-3 py-2 border rounded-lg" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">End Date</label>
                <input type="date" value={blockForm.end_date || ''}
                  onChange={e => setBlockForm({ ...blockForm, end_date: e.target.value || null })}
                  className="w-full px-3 py-2 border rounded-lg" />
              </div>
            </div>
          )}
          <p className="text-sm text-gray-700" data-testid="block-summary">
            This block runs from <strong>{startText}</strong> until <strong>{endText}</strong>
            {blockForm.recurrence_type === 'weekly'
              ? (blockForm.recurrence_pattern || []).length
                ? ` on ${(blockForm.recurrence_pattern || []).map((d: string) => dayShort(d)).join(', ')}`
                : ' (no days picked yet)'
              : blockForm.recurrence_type === 'daily' ? ' every day' : ''}
            {(() => {
              const tpl = playlistTemplates?.find(t => t.id === blockForm.playlist_template_id);
              return tpl ? <>, playing <strong>{tpl.name}</strong></> : '';
            })()}.
          </p>
          {formError && <p className="text-sm text-red-600" role="alert">{formError}</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={createBlock.isPending || updateBlock.isPending} className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
              {editingBlockId
                ? (updateBlock.isPending ? 'Saving…' : 'Save changes')
                : (createBlock.isPending ? 'Creating…' : 'Create Block')}
            </button>
            <button type="button" onClick={closeBlockForm} className="px-4 py-2 border rounded-lg hover:bg-gray-50">Cancel</button>
          </div>
        </form>
      )}

      <div className="space-y-4">
        {blocks?.length === 0 && <p className="text-gray-500 text-center py-8">No blocks yet. Create one to get started.</p>}
        {blocks?.map(block => (
          <div key={block.id} className="bg-white rounded-lg shadow-md overflow-hidden">
            <div className="p-4 flex justify-between items-center cursor-pointer hover:bg-gray-50"
              onClick={() => setExpandedBlock(expandedBlock === block.id ? null : block.id)}>
              <div>
                <h3 className="font-bold text-lg">{block.name}</h3>
                <p className="text-sm text-gray-600">
                  {(() => {
                    const from = block.start_sun_event
                      ? sunText(block.start_sun_event, block.start_sun_offset)
                      : clockText(block.start_time?.slice(0, 5));
                    if (block.until_next) return `Starts ${from}, runs until the next playlist`;
                    const to = block.end_sun_event
                      ? sunText(block.end_sun_event, block.end_sun_offset)
                      : clockText(block.end_time?.slice(0, 5));
                    return `${from} — ${to}`;
                  })()}
                  {' '}&middot; {block.recurrence_type === 'weekly' && block.recurrence_pattern?.length
                    ? block.recurrence_pattern.map((d: string) => dayShort(String(d))).join(', ')
                    : ({ daily: 'every day', weekly: 'weekly', monthly: 'monthly', one_time: 'one time' } as Record<string, string>)[block.recurrence_type] ?? block.recurrence_type}
                  {!block.playlist_template_id && <> &middot; {block.playback_mode}</>}
                  {' '}&middot; Priority {block.priority}
                  {block.start_sun_event && ` · Start: ${block.start_sun_event}${block.start_sun_offset ? ` (${block.start_sun_offset > 0 ? '+' : ''}${block.start_sun_offset}min)` : ''}`}
                  {block.end_sun_event && ` · End: ${block.end_sun_event}${block.end_sun_offset ? ` (${block.end_sun_offset > 0 ? '+' : ''}${block.end_sun_offset}min)` : ''}`}
                  {block.playlist_template_id && (() => {
                    const tpl = playlistTemplates?.find(t => t.id === block.playlist_template_id);
                    return tpl ? ` · Playlist: ${tpl.name}` : ' · Playlist assigned';
                  })()}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {!block.playlist_template_id && (
                  <span className="text-sm text-gray-400 hidden sm:inline">{block.playlist_entries?.length || 0} songs</span>
                )}
                {canEdit && (
                  <>
                    <button onClick={(e) => { e.stopPropagation(); startEdit(block); }}
                      className="px-2 py-1 bg-blue-100 text-blue-700 rounded text-sm hover:bg-blue-200">Edit</button>
                    <button onClick={(e) => { e.stopPropagation(); handleDeleteBlock(block.id); }}
                      className="px-2 py-1 bg-red-100 text-red-700 rounded text-sm hover:bg-red-200">Delete</button>
                  </>
                )}
                <span className="text-gray-400">{expandedBlock === block.id ? '▲' : '▼'}</span>
              </div>
            </div>

            {expandedBlock === block.id && (
              <div className="border-t p-4 bg-gray-50">
                {block.playlist_template_id ? (() => {
                  const tpl = playlistTemplates?.find(t => t.id === block.playlist_template_id);
                  return (
                    <div className="mb-3">
                      <h4 className="text-sm font-bold text-gray-700 uppercase mb-2">Playlist: {tpl?.name || 'Unknown'}</h4>
                      {tpl && tpl.slots.length > 0 && (
                        <div className="space-y-1">
                          {tpl.slots.sort((a, b) => a.position - b.position).map((slot, idx) => (
                            <div key={slot.id} className="flex items-center gap-3 bg-white p-2 rounded border">
                              <span className="text-xs text-gray-400 w-6 text-right">{idx + 1}</span>
                              <span className="text-sm font-medium">{slot.asset_type}</span>
                              {slot.category && <span className="text-xs text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">{slot.category}</span>}
                              {!slot.category && <span className="text-xs text-gray-400">(any)</span>}
                            </div>
                          ))}
                        </div>
                      )}
                      {(!tpl || tpl.slots.length === 0) && <p className="text-gray-400 text-sm">This playlist has no steps yet. Add them on the Playlists page.</p>}
                    </div>
                  );
                })() : (
                <>
                <div className="flex justify-between items-center mb-3">
                  <h4 className="text-sm font-bold text-gray-700 uppercase">Songs in this block</h4>
                  {canEdit && (
                    <button onClick={() => setAddingEntryTo(addingEntryTo === block.id ? null : block.id)}
                      className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700">
                      + Add song
                    </button>
                  )}
                </div>

                {addingEntryTo === block.id && (
                  <div className="flex gap-2 mb-3">
                    <select value={selectedAssetId} onChange={e => setSelectedAssetId(e.target.value)}
                      className="flex-1 px-3 py-2 border rounded-lg text-sm">
                      <option value="">Pick a song…</option>
                      {assets.map((a: any) => (
                        <option key={a.id} value={a.id}>{a.title} {a.artist ? `— ${a.artist}` : ''}</option>
                      ))}
                    </select>
                    <button onClick={() => handleAddEntry(block.id)}
                      disabled={!selectedAssetId}
                      className="px-3 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 disabled:opacity-50">
                      Add
                    </button>
                  </div>
                )}

                {(!block.playlist_entries || block.playlist_entries.length === 0) ? (
                  <p className="text-gray-400 text-sm">No entries yet</p>
                ) : (
                  <div className="space-y-1">
                    {block.playlist_entries
                      .sort((a, b) => a.position - b.position)
                      .map((entry, idx) => {
                        const asset = assets.find((a: any) => a.id === entry.asset_id);
                        return (
                          <div key={entry.id} className="flex items-center justify-between bg-white p-2 rounded border">
                            <div className="flex items-center gap-3">
                              <span className="text-xs text-gray-400 w-6 text-right">{idx + 1}</span>
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium">{asset?.title || entry.asset_id}</span>
                                {asset?.artist && <span className="text-xs text-gray-400">{asset.artist}</span>}
                                {asset && <AssetCategoryBadge assetId={asset.id} category={asset.category} compact />}
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-gray-400">
                                w:{entry.weight} · {entry.playback_mode}
                                {!entry.is_enabled && ' · disabled'}
                              </span>
                              {canEdit && (
                                <button onClick={() => handleDeleteEntry(entry.id)}
                                  className="text-red-500 hover:text-red-700 text-xs">Remove</button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                  </div>
                )}
                </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
