import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import apiClient from '../../api/client';
import { useAssets } from '../../hooks/useAssets';
import type { ScheduleBlock, CreateScheduleBlockData } from '../../hooks/useSchedules';
import { useCreateScheduleBlock, useDeleteScheduleBlock, useCreatePlaylistEntry, useDeletePlaylistEntry } from '../../hooks/useSchedules';
import { usePlaylistTemplates } from '../../hooks/usePlaylists';
import AssetCategoryBadge from '../../components/AssetCategoryBadge';

export default function ScheduleBlocks() {
  const { scheduleId } = useParams<{ scheduleId: string }>();
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

  const { data: assetsData } = useAssets();
  const assets = assetsData?.assets || [];

  const { data: playlistTemplates } = usePlaylistTemplates();
  const createBlock = useCreateScheduleBlock();
  const deleteBlock = useDeleteScheduleBlock();
  const createEntry = useCreatePlaylistEntry();
  const deleteEntry = useDeletePlaylistEntry();

  const [showBlockForm, setShowBlockForm] = useState(false);
  const [expandedBlock, setExpandedBlock] = useState<string | null>(null);
  const [blockForm, setBlockForm] = useState<CreateScheduleBlockData>({
    schedule_id: scheduleId || '',
    name: '',
    start_time: '08:00',
    end_time: '12:00',
    recurrence_type: 'daily',
    priority: 0,
    playback_mode: 'sequential',
  });

  const [addingEntryTo, setAddingEntryTo] = useState<string | null>(null);
  const [selectedAssetId, setSelectedAssetId] = useState('');

  const handleCreateBlock = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createBlock.mutateAsync({ ...blockForm, schedule_id: scheduleId || '' });
      setShowBlockForm(false);
      setBlockForm({ schedule_id: scheduleId || '', name: '', start_time: '08:00', end_time: '12:00', recurrence_type: 'daily', priority: 0, playback_mode: 'sequential' });
    } catch (err) {
      console.error('Failed to create block:', err);
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

  if (isLoading) return <div className="p-8">Loading...</div>;

  return (
    <div className="max-w-5xl mx-auto p-8">
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => navigate('/admin/schedules')} className="text-blue-600 hover:text-blue-800">&larr; Schedules</button>
        <h1 className="text-3xl font-bold">{schedule?.name || 'Schedule'} — Blocks</h1>
        <button onClick={() => setShowBlockForm(!showBlockForm)}
          className="ml-auto px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
          {showBlockForm ? 'Cancel' : 'New Block'}
        </button>
      </div>

      {showBlockForm && (
        <form onSubmit={handleCreateBlock} className="bg-white p-6 rounded-lg shadow-md mb-6 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Block Name</label>
              <input required type="text" value={blockForm.name}
                onChange={e => setBlockForm({ ...blockForm, name: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg" placeholder="Morning Show" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Recurrence</label>
              <select value={blockForm.recurrence_type}
                onChange={e => setBlockForm({ ...blockForm, recurrence_type: e.target.value as any })}
                className="w-full px-3 py-2 border rounded-lg">
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
                <option value="one_time">One-Time</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Start Time</label>
              <input type="time" value={blockForm.start_time}
                onChange={e => setBlockForm({ ...blockForm, start_time: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">End Time</label>
              <input type="time" value={blockForm.end_time}
                onChange={e => setBlockForm({ ...blockForm, end_time: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Priority</label>
              <input type="number" value={blockForm.priority}
                onChange={e => setBlockForm({ ...blockForm, priority: parseInt(e.target.value) || 0 })}
                className="w-full px-3 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Playback Mode</label>
              <select value={blockForm.playback_mode}
                onChange={e => setBlockForm({ ...blockForm, playback_mode: e.target.value as any })}
                className="w-full px-3 py-2 border rounded-lg">
                <option value="sequential">Sequential</option>
                <option value="shuffle">Shuffle</option>
                <option value="weighted">Weighted</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Playlist Template (optional — overrides specific assets)</label>
            <select value={blockForm.playlist_template_id || ''}
              onChange={e => setBlockForm({ ...blockForm, playlist_template_id: e.target.value || null })}
              className="w-full px-3 py-2 border rounded-lg">
              <option value="">Use specific assets</option>
              {playlistTemplates?.map(t => (
                <option key={t.id} value={t.id}>{t.name} ({t.slots.length} slots)</option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Start Sun Event</label>
              <select value={blockForm.start_sun_event || ''}
                onChange={e => setBlockForm({ ...blockForm, start_sun_event: e.target.value as any || undefined })}
                className="w-full px-3 py-2 border rounded-lg">
                {sunEvents.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Start Offset (min)</label>
              <input type="number" value={blockForm.start_sun_offset || 0}
                onChange={e => setBlockForm({ ...blockForm, start_sun_offset: parseInt(e.target.value) || 0 })}
                className="w-full px-3 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">End Sun Event</label>
              <select value={blockForm.end_sun_event || ''}
                onChange={e => setBlockForm({ ...blockForm, end_sun_event: e.target.value as any || undefined })}
                className="w-full px-3 py-2 border rounded-lg">
                {sunEvents.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">End Offset (min)</label>
              <input type="number" value={blockForm.end_sun_offset || 0}
                onChange={e => setBlockForm({ ...blockForm, end_sun_offset: parseInt(e.target.value) || 0 })}
                className="w-full px-3 py-2 border rounded-lg" />
            </div>
          </div>
          {blockForm.recurrence_type === 'one_time' && (
            <div className="grid grid-cols-2 gap-4">
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
          <button type="submit" className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Create Block</button>
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
                  {block.start_time?.slice(0, 5)} — {block.end_time?.slice(0, 5)}
                  {' '}&middot; {block.recurrence_type}
                  {' '}&middot; {block.playback_mode}
                  {' '}&middot; Priority {block.priority}
                  {block.start_sun_event && ` · Start: ${block.start_sun_event}${block.start_sun_offset ? ` (${block.start_sun_offset > 0 ? '+' : ''}${block.start_sun_offset}min)` : ''}`}
                  {block.end_sun_event && ` · End: ${block.end_sun_event}${block.end_sun_offset ? ` (${block.end_sun_offset > 0 ? '+' : ''}${block.end_sun_offset}min)` : ''}`}
                  {block.playlist_template_id && (() => {
                    const tpl = playlistTemplates?.find(t => t.id === block.playlist_template_id);
                    return tpl ? ` · Template: ${tpl.name}` : ' · Template assigned';
                  })()}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-400">{block.playlist_entries?.length || 0} entries</span>
                <button onClick={(e) => { e.stopPropagation(); handleDeleteBlock(block.id); }}
                  className="px-2 py-1 bg-red-100 text-red-700 rounded text-sm hover:bg-red-200">Delete</button>
                <span className="text-gray-400">{expandedBlock === block.id ? '▲' : '▼'}</span>
              </div>
            </div>

            {expandedBlock === block.id && (
              <div className="border-t p-4 bg-gray-50">
                {block.playlist_template_id ? (() => {
                  const tpl = playlistTemplates?.find(t => t.id === block.playlist_template_id);
                  return (
                    <div className="mb-3">
                      <h4 className="text-sm font-bold text-gray-700 uppercase mb-2">Template: {tpl?.name || 'Unknown'}</h4>
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
                      {(!tpl || tpl.slots.length === 0) && <p className="text-gray-400 text-sm">Template has no slots</p>}
                    </div>
                  );
                })() : (
                <>
                <div className="flex justify-between items-center mb-3">
                  <h4 className="text-sm font-bold text-gray-700 uppercase">Playlist Entries</h4>
                  <button onClick={() => setAddingEntryTo(addingEntryTo === block.id ? null : block.id)}
                    className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700">
                    + Add Asset
                  </button>
                </div>

                {addingEntryTo === block.id && (
                  <div className="flex gap-2 mb-3">
                    <select value={selectedAssetId} onChange={e => setSelectedAssetId(e.target.value)}
                      className="flex-1 px-3 py-2 border rounded-lg text-sm">
                      <option value="">Select an asset...</option>
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
                              <button onClick={() => handleDeleteEntry(entry.id)}
                                className="text-red-500 hover:text-red-700 text-xs">Remove</button>
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
