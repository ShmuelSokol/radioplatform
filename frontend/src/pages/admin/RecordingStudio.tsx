import { useState, useCallback } from 'react';
import AudioMixer from './AudioMixer';
import { useStations } from '../../hooks/useStations';
import {
  useWeatherReadouts,
  useCreateWeatherReadout,
  useUpdateWeatherReadout,
  useRegenerateWeatherReadout,
  useQueueWeatherReadout,
  useDeleteWeatherReadout,
  useWeatherTemplatePreview,
} from '../../hooks/useWeatherReadouts';
import { useStudioRecorder } from '../../hooks/useStudioRecorder';
import { uploadAsset } from '../../api/assets';
import type { WeatherReadout } from '../../api/weatherReadouts';

function formatDuration(sec: number) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-yellow-700 text-yellow-200',
  recorded: 'bg-green-700 text-green-200',
  queued: 'bg-blue-700 text-blue-200',
  skipped: 'bg-gray-700 text-gray-300',
};

// ── VU Meter ────────────────────────────────────────────────────────────
function VUMeter({ level }: { level: number }) {
  const pct = Math.round(level * 100);
  const color = pct > 80 ? 'bg-red-500' : pct > 50 ? 'bg-yellow-400' : 'bg-green-400';
  return (
    <div className="w-full h-3 bg-gray-800 rounded overflow-hidden">
      <div className={`h-full ${color} transition-all duration-75`} style={{ width: `${pct}%` }} />
    </div>
  );
}

// ── Inline Readout Recorder ─────────────────────────────────────────────
function ReadoutRecorder({
  readout,
  onSaved,
}: {
  readout: WeatherReadout;
  onSaved: () => void;
}) {
  const recorder = useStudioRecorder();
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedFile, setSavedFile] = useState<File | null>(null);
  const updateReadout = useUpdateWeatherReadout();

  const handleStop = useCallback(async () => {
    const file = await recorder.stopRecording();
    if (file) {
      setSavedFile(file);
      setPreviewUrl(URL.createObjectURL(file));
    }
  }, [recorder]);

  const handleSave = useCallback(async () => {
    if (!savedFile) return;
    setSaving(true);
    try {
      const asset = await uploadAsset(
        savedFile,
        `Weather Readout - ${readout.readout_date}`,
        'wav',
        undefined,
        undefined,
        'spot',
        'weather_readout',
      );
      await updateReadout.mutateAsync({
        id: readout.id,
        data: { asset_id: asset.id, status: 'recorded' },
      });
      setPreviewUrl(null);
      setSavedFile(null);
      onSaved();
    } catch {
      // error handled by mutation
    } finally {
      setSaving(false);
    }
  }, [savedFile, readout, updateReadout, onSaved]);

  const handleCancel = useCallback(() => {
    recorder.cancelRecording();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setSavedFile(null);
  }, [recorder, previewUrl]);

  if (previewUrl) {
    return (
      <div className="mt-2 space-y-2">
        <audio src={previewUrl} controls className="w-full" />
        <div className="flex gap-2">
          <button onClick={handleSave} disabled={saving}
            className="px-3 py-1 text-xs bg-green-700 hover:bg-green-600 rounded text-white disabled:opacity-50">
            {saving ? 'Saving...' : 'Save & Link'}
          </button>
          <button onClick={handleCancel}
            className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded text-gray-300">
            Discard
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-2 space-y-2">
      <VUMeter level={recorder.audioLevel} />
      {recorder.isRecording && (
        <div className="text-xs text-gray-400">
          {formatDuration(recorder.duration)}
          {recorder.isPaused && ' (paused)'}
        </div>
      )}
      {recorder.error && <div className="text-xs text-red-400">{recorder.error}</div>}
      <div className="flex gap-2">
        {!recorder.isRecording ? (
          <button onClick={recorder.startRecording}
            className="px-3 py-1 text-xs bg-red-700 hover:bg-red-600 rounded text-white">
            Record
          </button>
        ) : (
          <>
            {recorder.isPaused ? (
              <button onClick={recorder.resumeRecording}
                className="px-3 py-1 text-xs bg-yellow-700 hover:bg-yellow-600 rounded text-white">
                Resume
              </button>
            ) : (
              <button onClick={recorder.pauseRecording}
                className="px-3 py-1 text-xs bg-yellow-700 hover:bg-yellow-600 rounded text-white">
                Pause
              </button>
            )}
            <button onClick={handleStop}
              className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded text-white">
              Stop
            </button>
            <button onClick={handleCancel}
              className="px-3 py-1 text-xs bg-gray-800 hover:bg-gray-700 rounded text-gray-400">
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ── General-Purpose Recorder ────────────────────────────────────────────
function GeneralRecorder() {
  const recorder = useStudioRecorder();
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [savedFile, setSavedFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [title, setTitle] = useState('');
  const [assetType, setAssetType] = useState('spot');
  const [category, setCategory] = useState('');
  const [success, setSuccess] = useState('');

  const handleStop = useCallback(async () => {
    const file = await recorder.stopRecording();
    if (file) {
      setSavedFile(file);
      setPreviewUrl(URL.createObjectURL(file));
    }
  }, [recorder]);

  const handleSave = useCallback(async () => {
    if (!savedFile || !title.trim()) return;
    setSaving(true);
    try {
      await uploadAsset(savedFile, title.trim(), 'wav', undefined, undefined, assetType, category || undefined);
      setSuccess(`Saved "${title}" to library`);
      setPreviewUrl(null);
      setSavedFile(null);
      setTitle('');
      setCategory('');
      setTimeout(() => setSuccess(''), 3000);
    } catch {
      // handled by upload
    } finally {
      setSaving(false);
    }
  }, [savedFile, title, assetType, category]);

  const handleCancel = useCallback(() => {
    recorder.cancelRecording();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setSavedFile(null);
  }, [recorder, previewUrl]);

  return (
    <div className="space-y-4">
      {/* VU Meter */}
      <VUMeter level={recorder.audioLevel} />

      {/* Recording info */}
      {recorder.isRecording && (
        <div className="text-center">
          <span className="text-2xl font-mono text-white">{formatDuration(recorder.duration)}</span>
          {recorder.isPaused && <span className="ml-2 text-yellow-400 text-sm">(paused)</span>}
        </div>
      )}

      {recorder.error && <div className="text-sm text-red-400">{recorder.error}</div>}
      {success && <div className="text-sm text-green-400">{success}</div>}

      {/* Controls */}
      <div className="flex justify-center gap-3">
        {!recorder.isRecording && !previewUrl && (
          <button onClick={recorder.startRecording}
            className="w-16 h-16 rounded-full bg-red-600 hover:bg-red-500 flex items-center justify-center shadow-lg transition">
            <div className="w-6 h-6 rounded-full bg-white" />
          </button>
        )}
        {recorder.isRecording && (
          <>
            {recorder.isPaused ? (
              <button onClick={recorder.resumeRecording}
                className="px-4 py-2 bg-yellow-700 hover:bg-yellow-600 rounded text-white text-sm">
                Resume
              </button>
            ) : (
              <button onClick={recorder.pauseRecording}
                className="px-4 py-2 bg-yellow-700 hover:bg-yellow-600 rounded text-white text-sm">
                Pause
              </button>
            )}
            <button onClick={handleStop}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-white text-sm">
              Stop
            </button>
            <button onClick={handleCancel}
              className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-gray-400 text-sm">
              Cancel
            </button>
          </>
        )}
      </div>

      {/* Preview & Save */}
      {previewUrl && (
        <div className="space-y-3 border border-[#2a2a5e] rounded-lg p-4">
          <audio src={previewUrl} controls className="w-full" />
          <input
            type="text" placeholder="Title *" value={title}
            onChange={e => setTitle(e.target.value)}
            className="w-full px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
          />
          <div className="flex gap-2">
            <select value={assetType} onChange={e => setAssetType(e.target.value)}
              className="flex-1 px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm">
              <option value="music">Music</option>
              <option value="spot">Spot</option>
              <option value="jingle">Jingle</option>
              <option value="voicetrack">Voice Track</option>
            </select>
            <input
              type="text" placeholder="Category (optional)" value={category}
              onChange={e => setCategory(e.target.value)}
              className="flex-1 px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
            />
          </div>
          <div className="flex gap-2">
            <button onClick={handleSave} disabled={saving || !title.trim()}
              className="px-4 py-2 bg-green-700 hover:bg-green-600 rounded text-white text-sm disabled:opacity-50">
              {saving ? 'Saving...' : 'Save to Library'}
            </button>
            <button onClick={handleCancel}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-gray-300 text-sm">
              Discard
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Template Config Panel ────────────────────────────────────────────────
function TemplateConfig({ stationId }: { stationId: string }) {
  const [customTemplate, setCustomTemplate] = useState('');
  const { data: preview, isLoading } = useWeatherTemplatePreview(
    stationId,
    customTemplate || undefined,
  );

  return (
    <div className="space-y-3">
      <label className="text-xs text-gray-400 uppercase font-bold">Template Preview</label>
      <textarea
        rows={3}
        placeholder="Custom template (leave blank for default)... Variables: {city}, {temp_f}, {description}, {wind_speed_mph}, {wind_direction}, {humidity}, {forecast_text}, {brand_name}, {date}, {day_of_week}, {day1_name}, {day1_high}, {day1_low}, {day1_desc}"
        value={customTemplate}
        onChange={e => setCustomTemplate(e.target.value)}
        className="w-full px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm font-mono"
      />
      {isLoading && <div className="text-xs text-gray-500">Loading preview...</div>}
      {preview && (
        <div className="p-3 bg-[#0a0a28] border border-[#1a1a4e] rounded text-sm text-gray-200 leading-relaxed">
          {preview.rendered}
        </div>
      )}
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────
export default function RecordingStudio() {
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const [selectedStation, setSelectedStation] = useState('');
  const [dateFilter, setDateFilter] = useState(() => new Date().toISOString().slice(0, 10));
  const [showTemplate, setShowTemplate] = useState(false);
  const [recordingReadoutId, setRecordingReadoutId] = useState<string | null>(null);

  const { data: readoutsData, refetch } = useWeatherReadouts(
    selectedStation ? { station_id: selectedStation, date_from: dateFilter, date_to: dateFilter } : undefined,
  );
  const createReadout = useCreateWeatherReadout();
  const regenerate = useRegenerateWeatherReadout();
  const queueReadout = useQueueWeatherReadout();
  const updateReadout = useUpdateWeatherReadout();
  const deleteReadout = useDeleteWeatherReadout();

  const readouts = readoutsData?.readouts ?? [];

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      <h1 className="text-2xl font-bold text-white">Recording Studio</h1>

      {/* ── Section 1: Weather Readouts ───────────────────────────────── */}
      <section className="bg-[#12123a] border border-[#2a2a5e] rounded-xl p-6 space-y-4">
        <h2 className="text-lg font-semibold text-cyan-300">Weather Readouts</h2>

        {/* Controls */}
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="text-xs text-gray-400 block mb-1">Station</label>
            <select value={selectedStation} onChange={e => setSelectedStation(e.target.value)}
              className="px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm min-w-[200px]">
              <option value="">Select station...</option>
              {stations.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-400 block mb-1">Date</label>
            <input type="date" value={dateFilter}
              onChange={e => setDateFilter(e.target.value)}
              className="px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm" />
          </div>
          {selectedStation && (
            <>
              <button
                onClick={() => createReadout.mutate({ station_id: selectedStation, readout_date: dateFilter })}
                disabled={createReadout.isPending}
                className="px-3 py-2 bg-cyan-800 hover:bg-cyan-700 rounded text-white text-sm disabled:opacity-50">
                {createReadout.isPending ? 'Generating...' : 'Generate Readout'}
              </button>
              <button onClick={() => setShowTemplate(!showTemplate)}
                className="px-3 py-2 bg-[#1a1a4e] hover:bg-[#2a2a5e] rounded text-gray-300 text-sm">
                {showTemplate ? 'Hide Template' : 'Template Config'}
              </button>
            </>
          )}
        </div>

        {/* Template panel */}
        {showTemplate && selectedStation && (
          <div className="border border-[#2a2a5e] rounded-lg p-4">
            <TemplateConfig stationId={selectedStation} />
          </div>
        )}

        {/* Readout cards */}
        {readouts.length === 0 && selectedStation && (
          <div className="text-center text-gray-500 py-6 text-sm">
            No readouts for this date. Click "Generate Readout" to create one.
          </div>
        )}
        {readouts.map(r => (
          <div key={r.id} className="border border-[#2a2a5e] rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-sm text-gray-400">{r.readout_date}</span>
              <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${STATUS_COLORS[r.status] || 'bg-gray-700 text-gray-300'}`}>
                {r.status}
              </span>
              <span className="text-[10px] text-gray-600">by {r.generated_by}</span>
              {r.asset_id && <span className="text-[10px] text-green-500">has recording</span>}
            </div>

            {/* Script text — large readable font */}
            <div className="p-4 bg-[#0a0a28] rounded border border-[#1a1a3e] text-base text-gray-100 leading-relaxed whitespace-pre-wrap">
              {r.script_text}
            </div>

            {/* Actions */}
            <div className="flex gap-2 flex-wrap">
              {r.status === 'pending' && (
                <button
                  onClick={() => setRecordingReadoutId(recordingReadoutId === r.id ? null : r.id)}
                  className="px-3 py-1 text-xs bg-red-700 hover:bg-red-600 rounded text-white">
                  {recordingReadoutId === r.id ? 'Close Recorder' : 'Record'}
                </button>
              )}
              {r.status === 'recorded' && !r.asset_id && (
                <button
                  onClick={() => setRecordingReadoutId(recordingReadoutId === r.id ? null : r.id)}
                  className="px-3 py-1 text-xs bg-red-700 hover:bg-red-600 rounded text-white">
                  Record
                </button>
              )}
              <button
                onClick={() => regenerate.mutate(r.id)}
                disabled={regenerate.isPending}
                className="px-3 py-1 text-xs bg-[#2a2a5e] hover:bg-[#3a3a7e] rounded text-gray-300 disabled:opacity-50">
                Regenerate
              </button>
              {r.status !== 'skipped' && (
                <button
                  onClick={() => updateReadout.mutate({ id: r.id, data: { status: 'skipped' } })}
                  className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded text-gray-400">
                  Skip
                </button>
              )}
              {r.status === 'recorded' && r.asset_id && (
                <button
                  onClick={() => queueReadout.mutate(r.id)}
                  disabled={queueReadout.isPending}
                  className="px-3 py-1 text-xs bg-blue-700 hover:bg-blue-600 rounded text-white disabled:opacity-50">
                  Queue
                </button>
              )}
              <button
                onClick={() => { if (confirm('Delete this readout?')) deleteReadout.mutate(r.id); }}
                className="px-3 py-1 text-xs bg-red-900 hover:bg-red-800 rounded text-red-400">
                Delete
              </button>
            </div>

            {/* Inline recorder */}
            {recordingReadoutId === r.id && (
              <ReadoutRecorder readout={r} onSaved={() => { setRecordingReadoutId(null); refetch(); }} />
            )}
          </div>
        ))}
      </section>

      {/* ── Section 2: General Recorder ─────────────────────────────── */}
      <section className="bg-[#12123a] border border-[#2a2a5e] rounded-xl p-6 space-y-4">
        <h2 className="text-lg font-semibold text-rose-300">General Recorder</h2>
        <p className="text-xs text-gray-500">
          Record any audio and save it directly to the library. Captures 48kHz 16-bit WAV (broadcast quality).
        </p>
        <GeneralRecorder />
      </section>

      {/* ── Section 3: Audio Mixer ──────────────────────────────────── */}
      <section className="bg-[#12123a] border border-[#2a2a5e] rounded-xl p-6 space-y-4">
        <h2 className="text-lg font-semibold text-amber-300">Audio Mixer</h2>
        <p className="text-xs text-gray-500">
          Select a backtrack and overlay, configure volume and fades, then mix into a new asset.
        </p>
        <AudioMixer />
      </section>
    </div>
  );
}
