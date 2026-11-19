import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useStations } from '../../hooks/useStations';
import {
  usePlaylistTemplates,
  useCreatePlaylistTemplate,
  useDeletePlaylistTemplate,
  useCreateTemplateSlot,
  useDeleteTemplateSlot,
  useUpdateTemplateSlot,
  useAssetTypes,
} from '../../hooks/usePlaylists';
import PlaylistGenerationPanel from './PlaylistGenerationPanel';
import HolidayMixNote from '../../components/holiday/HolidayMixNote';

export default function Playlists() {
  const { data: stationsData } = useStations();
  const stations = stationsData?.stations || [];

  const [filterStationId, setFilterStationId] = useState<string>('');
  const { data: templates, isLoading } = usePlaylistTemplates(filterStationId || undefined);
  const { data: assetTypes } = useAssetTypes();

  const createTemplate = useCreatePlaylistTemplate();
  const deleteTemplate = useDeletePlaylistTemplate();
  const createSlot = useCreateTemplateSlot();
  const deleteSlot = useDeleteTemplateSlot();
  const updateSlot = useUpdateTemplateSlot();

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formStation, setFormStation] = useState('');
  // ?open=<playlist>&step=<step>: opened from "Why it is in the queue" in the control room,
  // so that playlist is open, scrolled to and the step the song filled is lit
  const [params] = useSearchParams();
  const openId = params.get('open');
  const stepId = params.get('step');
  const [expandedTemplate, setExpandedTemplate] = useState<string | null>(openId);
  useEffect(() => {
    if (!openId || !templates?.some(t => t.id === openId)) return;
    const el = document.querySelector(`[data-template-id="${openId}"]`);
    el?.scrollIntoView?.({ block: 'start' });
  }, [openId, templates]);

  // Slot add form state
  const [addingSlotTo, setAddingSlotTo] = useState<string | null>(null);
  const [slotAssetType, setSlotAssetType] = useState('music');
  const [slotCategory, setSlotCategory] = useState('');

  const uniqueAssetTypes = [...new Set(assetTypes?.map(a => a.asset_type) || [])];
  const filteredCategories = assetTypes?.filter(a => a.asset_type === slotAssetType).map(a => a.category).filter(Boolean) || [];

  const handleCreateTemplate = async (e: React.FormEvent) => {
    e.preventDefault();
    await createTemplate.mutateAsync({
      name: formName,
      description: formDesc || undefined,
      station_id: formStation || null,
    });
    setFormName('');
    setFormDesc('');
    setFormStation('');
    setShowForm(false);
  };

  const handleDeleteTemplate = async (id: string) => {
    if (confirm('Delete this template and all its slots?')) {
      await deleteTemplate.mutateAsync(id);
    }
  };

  const handleAddSlot = (templateId: string) => {
    const existing = templates?.find(t => t.id === templateId)?.slots || [];
    createSlot.mutate({
      template_id: templateId,
      position: existing.length ? Math.max(...existing.map(x => x.position)) + 1 : 0,
      asset_type: slotAssetType,
      category: slotCategory || null,
    });
    // keep the form open with the same kind, so a sequence can be typed in quickly
  };

  // Move a step up or down; renumbers every step so positions stay 0..n-1
  // Edits show at once and save in the background (see usePlaylists).
  const moveSlot = (templateId: string, index: number, delta: number) => {
    const slots = [...(templates?.find(t => t.id === templateId)?.slots || [])].sort((a, b) => a.position - b.position);
    const to = index + delta;
    if (to < 0 || to >= slots.length) return;
    [slots[index], slots[to]] = [slots[to], slots[index]];
    slots.forEach((slot, i) => {
      if (slot.position !== i) updateSlot.mutate({ id: slot.id, data: { position: i } });
    });
  };

  const handleDeleteSlot = (slotId: string) => {
    deleteSlot.mutate(slotId);
  };

  if (isLoading) return <div className="text-center py-10">Loading...</div>;

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Playlists</h1>
        <button onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">
          {showForm ? 'Cancel' : 'New playlist'}
        </button>
      </div>
      <p className="text-sm text-gray-600 mb-4 max-w-3xl">
        A playlist is an order of song types that repeats, for example Lively, Medium fast,
        Station ID, Relax. The station draws a song of each type in turn. Choose when each
        playlist plays on the Schedules page.
      </p>

      {/* Station filter */}
      <div className="mb-4">
        <select value={filterStationId} onChange={e => setFilterStationId(e.target.value)}
          className="px-3 py-2 border rounded-lg text-sm">
          <option value="">All Stations</option>
          {stations.map((s: any) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </div>

      <HolidayMixNote stations={filterStationId ? stations.filter((s: any) => s.id === filterStationId) : stations} />

      <PlaylistGenerationPanel stationId={filterStationId} />

      {/* Create form */}
      {showForm && (
        <form onSubmit={handleCreateTemplate} className="bg-white p-6 rounded-lg shadow-md mb-6 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Playlist name</label>
              <input required type="text" value={formName} onChange={e => setFormName(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg" placeholder="Morning Rotation" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Station (optional)</label>
              <select value={formStation} onChange={e => setFormStation(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg">
                <option value="">Any Station</option>
                {stations.map((s: any) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Description</label>
            <textarea value={formDesc} onChange={e => setFormDesc(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg" rows={2} placeholder="Fast → New Hit → Fast → Relax" />
          </div>
          <button type="submit" disabled={createTemplate.isPending}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50">
            Create playlist
          </button>
        </form>
      )}

      {/* Template list */}
      <div className="space-y-4">
        {templates?.length === 0 && <p className="text-gray-500 text-center py-8">No playlists yet. Press "New playlist" to make one.</p>}
        {templates?.map(template => (
          <div key={template.id} data-template-id={template.id} data-nav-id={`playlist-${template.id}`}
            className={`bg-white rounded-lg shadow-md overflow-hidden ${openId === template.id ? 'ring-2 ring-yellow-400' : ''}`}>
            <div className="p-4 flex justify-between items-center cursor-pointer hover:bg-gray-50"
              onClick={() => setExpandedTemplate(expandedTemplate === template.id ? null : template.id)}>
              <div>
                <h3 className="font-bold text-lg">{template.name}</h3>
                <p className="text-sm text-gray-600">
                  {template.slots.length} step{template.slots.length !== 1 ? 's' : ''}
                  {template.description && ` · ${template.description}`}
                  {template.station_id && ` · Station: ${stations.find((s: any) => s.id === template.station_id)?.name || 'Unknown'}`}
                  {!template.station_id && ' · Any station'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className={`px-2 py-1 rounded-full text-xs font-medium ${template.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                  {template.is_active ? 'Active' : 'Inactive'}
                </span>
                <button onClick={e => { e.stopPropagation(); handleDeleteTemplate(template.id); }}
                  className="px-2 py-1 bg-red-100 text-red-700 rounded text-sm hover:bg-red-200">Delete</button>
                <span className="text-gray-400">{expandedTemplate === template.id ? '▲' : '▼'}</span>
              </div>
            </div>

            {expandedTemplate === template.id && (
              <div className="border-t p-4 bg-gray-50">
                <div className="flex justify-between items-center mb-3">
                  <h4 className="text-sm font-bold text-gray-700 uppercase">Steps (played in order, then repeated)</h4>
                  <button onClick={() => setAddingSlotTo(addingSlotTo === template.id ? null : template.id)}
                    className="px-3 py-1 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700">
                    {addingSlotTo === template.id ? 'Done' : '+ Add step'}
                  </button>
                </div>

                {addingSlotTo === template.id && (
                  <div className="flex flex-wrap gap-2 mb-3 items-end">
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">Kind</label>
                      <select value={slotAssetType} onChange={e => { setSlotAssetType(e.target.value); setSlotCategory(''); }}
                        className="px-3 py-2 border rounded-lg text-sm">
                        {uniqueAssetTypes.length > 0 ? uniqueAssetTypes.map(t => (
                          <option key={t} value={t}>{t}</option>
                        )) : (
                          <>
                            <option value="music">music</option>
                            <option value="spot">spot</option>
                            <option value="shiur">shiur</option>
                            <option value="jingle">jingle</option>
                            <option value="zmanim">zmanim</option>
                          </>
                        )}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">Song type</label>
                      <select value={slotCategory} onChange={e => setSlotCategory(e.target.value)}
                        className="px-3 py-2 border rounded-lg text-sm">
                        <option value="">Any</option>
                        {filteredCategories.map(c => (
                          <option key={c} value={c!}>{c}</option>
                        ))}
                      </select>
                    </div>
                    <button onClick={() => handleAddSlot(template.id)}
                      className="px-3 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700">
                      Add
                    </button>
                  </div>
                )}

                {template.slots.length === 0 ? (
                  <p className="text-gray-400 text-sm">No steps yet. Press "+ Add step" and add the song types in the order they should play.</p>
                ) : (
                  <div className="space-y-1">
                    {[...template.slots]
                      .sort((a, b) => a.position - b.position)
                      .map((slot, idx, sorted) => {
                        // A step just added has no id until the server answers (a moment)
                        const saving = sorted.some(s => s.id.startsWith('pending-'));
                        return (
                        <div key={slot.id} data-testid={slot.id === stepId ? 'playlist-step-lit' : undefined}
                          className={`flex items-center justify-between p-2 rounded border ${slot.id === stepId ? 'bg-yellow-100 border-yellow-500' : 'bg-white'}`}>
                          <div className="flex items-center gap-3">
                            <span className="text-xs text-gray-400 w-6 text-right">{idx + 1}</span>
                            <span className="text-sm font-medium">{slot.asset_type}</span>
                            {slot.category && <span className="text-xs text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">{slot.category}</span>}
                            {!slot.category && <span className="text-xs text-gray-400">(any)</span>}
                          </div>
                          <div className="flex items-center gap-1">
                            <button onClick={() => moveSlot(template.id, idx, -1)} disabled={idx === 0 || saving}
                              aria-label="Move up" className="px-2 py-1 text-gray-600 hover:bg-gray-100 rounded disabled:opacity-30">↑</button>
                            <button onClick={() => moveSlot(template.id, idx, 1)} disabled={idx === template.slots.length - 1 || saving}
                              aria-label="Move down" className="px-2 py-1 text-gray-600 hover:bg-gray-100 rounded disabled:opacity-30">↓</button>
                            <button onClick={() => handleDeleteSlot(slot.id)} disabled={slot.id.startsWith('pending-')}
                              className="ml-1 text-red-500 hover:text-red-700 text-xs disabled:opacity-30">Remove</button>
                          </div>
                        </div>
                        );
                      })}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
