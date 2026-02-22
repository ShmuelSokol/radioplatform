import { useState, useRef, useEffect, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useAssetDetail, useAssetAudioUrl, useEnhancePresets, useEnhanceAsset, useEnhancePreview, useRestoreOriginal } from '../../hooks/useAssets';
import WaveformPlayer, { type WaveformPlayerHandle } from '../../components/audio/WaveformPlayer';
import Spinner from '../../components/Spinner';
import AudienceDetectionPanel from '../../components/audio/AudienceDetectionPanel';

interface FilterState {
  afftdn: { enabled: boolean; noise_floor: number };
  highpass: { enabled: boolean; frequency: number };
  lowpass: { enabled: boolean; frequency: number };
  bass: { enabled: boolean; gain: number; frequency: number };
  treble: { enabled: boolean; gain: number; frequency: number };
  acompressor: { enabled: boolean; threshold: number; ratio: number; attack: number; release: number };
  loudnorm: { enabled: boolean; i: number; tp: number; lra: number };
}

const defaultFilters: FilterState = {
  afftdn: { enabled: false, noise_floor: -25 },
  highpass: { enabled: false, frequency: 80 },
  lowpass: { enabled: false, frequency: 10000 },
  bass: { enabled: false, gain: 3, frequency: 200 },
  treble: { enabled: false, gain: 3, frequency: 3000 },
  acompressor: { enabled: false, threshold: -20, ratio: 4, attack: 5, release: 100 },
  loudnorm: { enabled: false, i: -16, tp: -1.5, lra: 11 },
};

const PRESET_LABELS: Record<string, string> = {
  broadcast_polish: 'Broadcast Polish',
  noise_reduction: 'Noise Reduction',
  clarity_boost: 'Clarity Boost',
  warm_up: 'Warm Up',
  de_hiss: 'De-Hiss',
};

function buildFiltersFromState(state: FilterState): Array<{ name: string; params: Record<string, number> }> {
  const filters: Array<{ name: string; params: Record<string, number> }> = [];
  if (state.afftdn.enabled) filters.push({ name: 'afftdn', params: { noise_floor: state.afftdn.noise_floor } });
  if (state.highpass.enabled) filters.push({ name: 'highpass', params: { frequency: state.highpass.frequency } });
  if (state.lowpass.enabled) filters.push({ name: 'lowpass', params: { frequency: state.lowpass.frequency } });
  if (state.bass.enabled) filters.push({ name: 'bass', params: { gain: state.bass.gain, frequency: state.bass.frequency } });
  if (state.treble.enabled) filters.push({ name: 'treble', params: { gain: state.treble.gain, frequency: state.treble.frequency } });
  if (state.acompressor.enabled) {
    filters.push({ name: 'acompressor', params: {
      threshold: state.acompressor.threshold,
      ratio: state.acompressor.ratio,
      attack: state.acompressor.attack,
      release: state.acompressor.release,
    }});
  }
  if (state.loudnorm.enabled) {
    filters.push({ name: 'loudnorm', params: {
      i: state.loudnorm.i,
      tp: state.loudnorm.tp,
      lra: state.loudnorm.lra,
    }});
  }
  return filters;
}

export default function AudioEnhance() {
  const { assetId } = useParams<{ assetId: string }>();
  const queryClient = useQueryClient();
  const { data: asset, isLoading } = useAssetDetail(assetId);
  const { data: audioUrl } = useAssetAudioUrl(assetId);
  const { data: presetsData } = useEnhancePresets();
  const enhanceMutation = useEnhanceAsset();
  const previewMutation = useEnhancePreview();
  const restoreMutation = useRestoreOriginal();
  const waveformRef = useRef<WaveformPlayerHandle>(null);

  const [filters, setFilters] = useState<FilterState>({ ...defaultFilters });
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);
  const [enhancePending, setEnhancePending] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [waveformKey, setWaveformKey] = useState(0);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    };
  }, []);

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['asset-audio-url', assetId] });
    queryClient.invalidateQueries({ queryKey: ['asset', assetId] });
    setWaveformKey((k) => k + 1);
  }, [queryClient, assetId]);

  const presetNames = presetsData ? Object.keys(presetsData.presets) : [];

  const handlePresetSelect = (preset: string) => {
    setSelectedPreset(preset);
    // Reset individual filters — user can toggle them manually in custom mode
    setFilters({ ...defaultFilters });
  };

  const handleCustomMode = () => {
    setSelectedPreset(null);
  };

  const getActiveFilters = () => {
    if (selectedPreset) return { preset: selectedPreset };
    const f = buildFiltersFromState(filters);
    return f.length > 0 ? { filters: f } : null;
  };

  const handlePreview = (mode: 'enhanced' | 'original') => {
    if (mode === 'original') {
      // Play from waveform
      waveformRef.current?.seekTo(0);
      waveformRef.current?.play();
      return;
    }
    const body = getActiveFilters();
    if (!body) {
      setStatusMsg('Enable at least one filter or select a preset');
      return;
    }
    setStatusMsg(null);
    previewMutation.mutate(
      { id: assetId!, body: { ...body, start_seconds: 0, duration_seconds: 15 } },
      {
        onSuccess: (blob) => {
          if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
          const url = URL.createObjectURL(blob);
          blobUrlRef.current = url;
          if (!audioRef.current) audioRef.current = new Audio();
          audioRef.current.src = url;
          audioRef.current.play();
          setStatusMsg('Playing enhanced preview...');
        },
        onError: () => setStatusMsg('Preview failed'),
      }
    );
  };

  const handleApply = () => {
    const body = getActiveFilters();
    if (!body) {
      setStatusMsg('Enable at least one filter or select a preset');
      return;
    }
    enhanceMutation.mutate(
      { id: assetId!, body },
      {
        onSuccess: () => {
          setEnhancePending(true);
          setStatusMsg(null);
          handleRefresh();
        },
        onError: () => setStatusMsg('Enhancement failed'),
      }
    );
  };

  const handleUndo = () => {
    restoreMutation.mutate(assetId!, {
      onSuccess: () => {
        setEnhancePending(false);
        setStatusMsg('Original restored!');
        handleRefresh();
      },
      onError: (err) => {
        const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        setStatusMsg(msg || 'Restore failed');
      },
    });
  };

  const handleSave = () => {
    setEnhancePending(false);
    setStatusMsg('Enhancement saved.');
  };

  const updateFilter = <K extends keyof FilterState>(key: K, updates: Partial<FilterState[K]>) => {
    setFilters((prev) => ({ ...prev, [key]: { ...prev[key], ...updates } }));
    setSelectedPreset(null);
  };

  if (isLoading) return <div className="text-center py-10">Loading...</div>;
  if (!asset) return <div className="text-center py-10 text-red-500">Asset not found</div>;

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        <Link to={`/admin/assets/${assetId}`} className="text-brand-600 hover:text-brand-800 text-sm">&larr; Back to Asset</Link>
        <h1 className="text-xl font-bold">Audio Enhance: {asset.title}</h1>
      </div>

      {/* Waveform */}
      {audioUrl && (
        <div className="mb-4">
          <WaveformPlayer key={waveformKey} ref={waveformRef} url={audioUrl} />
        </div>
      )}

      {/* Post-apply review */}
      {enhancePending && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-4">
          <p className="text-sm text-amber-800 font-medium mb-2">Enhancement applied — review the result</p>
          <div className="flex gap-3">
            <button onClick={handleUndo} disabled={restoreMutation.isPending} className="px-4 py-2 text-sm bg-gray-100 hover:bg-gray-200 text-gray-700 rounded transition disabled:opacity-50">
              {restoreMutation.isPending ? <><Spinner className="mr-1" />Restoring...</> : 'Undo Enhancement'}
            </button>
            <button onClick={handleSave} className="px-4 py-2 text-sm bg-green-600 hover:bg-green-700 text-white rounded transition">Save</button>
          </div>
          {statusMsg && <div className="text-xs text-green-600 bg-green-50 rounded px-2 py-1 mt-3">{statusMsg}</div>}
        </div>
      )}

      {!enhancePending && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          {/* Left sidebar: Presets */}
          <div className="lg:col-span-1">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <h3 className="text-sm font-semibold mb-3">Presets</h3>
              <div className="flex flex-col gap-2">
                {presetNames.map((key) => (
                  <button
                    key={key}
                    onClick={() => handlePresetSelect(key)}
                    className={`px-3 py-2 rounded text-sm text-left transition ${
                      selectedPreset === key
                        ? 'bg-brand-600 text-white'
                        : 'bg-gray-50 hover:bg-gray-100 text-gray-700'
                    }`}
                  >
                    {PRESET_LABELS[key] || key}
                  </button>
                ))}
                <hr className="my-1" />
                <button
                  onClick={handleCustomMode}
                  className={`px-3 py-2 rounded text-sm text-left transition ${
                    !selectedPreset
                      ? 'bg-brand-600 text-white'
                      : 'bg-gray-50 hover:bg-gray-100 text-gray-700'
                  }`}
                >
                  Custom Filters
                </button>
              </div>
            </div>
          </div>

          {/* Right panel: Filter controls */}
          <div className="lg:col-span-3">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <h3 className="text-sm font-semibold mb-4">
                {selectedPreset ? `Preset: ${PRESET_LABELS[selectedPreset] || selectedPreset}` : 'Custom Filters'}
              </h3>

              {selectedPreset ? (
                <p className="text-sm text-gray-500 mb-4">
                  Using preset filters. Switch to "Custom Filters" to manually configure individual effects.
                </p>
              ) : (
                <div className="space-y-4">
                  {/* Noise Reduction */}
                  <FilterSection
                    label="Noise Reduction"
                    description="Remove hiss and background noise"
                    enabled={filters.afftdn.enabled}
                    onToggle={(v) => updateFilter('afftdn', { enabled: v })}
                  >
                    <SliderControl label="Noise Floor" value={filters.afftdn.noise_floor} min={-40} max={-10} unit="dB"
                      onChange={(v) => updateFilter('afftdn', { noise_floor: v })} />
                  </FilterSection>

                  {/* High-Pass Filter */}
                  <FilterSection
                    label="High-Pass Filter"
                    description="Remove low-frequency rumble"
                    enabled={filters.highpass.enabled}
                    onToggle={(v) => updateFilter('highpass', { enabled: v })}
                  >
                    <SliderControl label="Frequency" value={filters.highpass.frequency} min={20} max={300} unit="Hz"
                      onChange={(v) => updateFilter('highpass', { frequency: v })} />
                  </FilterSection>

                  {/* Low-Pass Filter */}
                  <FilterSection
                    label="Low-Pass Filter"
                    description="Remove high-frequency hiss"
                    enabled={filters.lowpass.enabled}
                    onToggle={(v) => updateFilter('lowpass', { enabled: v })}
                  >
                    <SliderControl label="Frequency" value={filters.lowpass.frequency} min={5000} max={20000} unit="Hz" step={100}
                      onChange={(v) => updateFilter('lowpass', { frequency: v })} />
                  </FilterSection>

                  {/* Bass Boost */}
                  <FilterSection
                    label="Bass Boost"
                    description="Add warmth to thin recordings"
                    enabled={filters.bass.enabled}
                    onToggle={(v) => updateFilter('bass', { enabled: v })}
                  >
                    <SliderControl label="Gain" value={filters.bass.gain} min={-10} max={10} step={0.5} unit="dB"
                      onChange={(v) => updateFilter('bass', { gain: v })} />
                    <SliderControl label="Frequency" value={filters.bass.frequency} min={20} max={1000} unit="Hz"
                      onChange={(v) => updateFilter('bass', { frequency: v })} />
                  </FilterSection>

                  {/* Treble Boost */}
                  <FilterSection
                    label="Treble Boost"
                    description="Add clarity and brightness"
                    enabled={filters.treble.enabled}
                    onToggle={(v) => updateFilter('treble', { enabled: v })}
                  >
                    <SliderControl label="Gain" value={filters.treble.gain} min={-10} max={10} step={0.5} unit="dB"
                      onChange={(v) => updateFilter('treble', { gain: v })} />
                    <SliderControl label="Frequency" value={filters.treble.frequency} min={1000} max={16000} unit="Hz" step={100}
                      onChange={(v) => updateFilter('treble', { frequency: v })} />
                  </FilterSection>

                  {/* Compressor */}
                  <FilterSection
                    label="Compressor"
                    description="Even out dynamics"
                    enabled={filters.acompressor.enabled}
                    onToggle={(v) => updateFilter('acompressor', { enabled: v })}
                  >
                    <SliderControl label="Threshold" value={filters.acompressor.threshold} min={-60} max={0} unit="dB"
                      onChange={(v) => updateFilter('acompressor', { threshold: v })} />
                    <SliderControl label="Ratio" value={filters.acompressor.ratio} min={1} max={20} step={0.5} unit=":1"
                      onChange={(v) => updateFilter('acompressor', { ratio: v })} />
                    <SliderControl label="Attack" value={filters.acompressor.attack} min={0.1} max={200} step={0.1} unit="ms"
                      onChange={(v) => updateFilter('acompressor', { attack: v })} />
                    <SliderControl label="Release" value={filters.acompressor.release} min={1} max={2000} unit="ms"
                      onChange={(v) => updateFilter('acompressor', { release: v })} />
                  </FilterSection>

                  {/* Loudness Normalization */}
                  <FilterSection
                    label="Loudness Normalization"
                    description="Consistent broadcast level"
                    enabled={filters.loudnorm.enabled}
                    onToggle={(v) => updateFilter('loudnorm', { enabled: v })}
                  >
                    <SliderControl label="Target LUFS" value={filters.loudnorm.i} min={-70} max={-5} step={0.5} unit="LUFS"
                      onChange={(v) => updateFilter('loudnorm', { i: v })} />
                    <SliderControl label="True Peak" value={filters.loudnorm.tp} min={-9} max={0} step={0.1} unit="dBTP"
                      onChange={(v) => updateFilter('loudnorm', { tp: v })} />
                    <SliderControl label="LRA" value={filters.loudnorm.lra} min={1} max={20} step={0.5} unit="LU"
                      onChange={(v) => updateFilter('loudnorm', { lra: v })} />
                  </FilterSection>
                </div>
              )}

              {/* A/B Preview + Apply */}
              <div className="flex flex-wrap gap-2 mt-6 pt-4 border-t border-gray-200">
                <button
                  onClick={() => handlePreview('original')}
                  className="bg-gray-100 hover:bg-gray-200 text-gray-700 px-4 py-2 rounded text-sm transition"
                >
                  Play Original
                </button>
                <button
                  onClick={() => handlePreview('enhanced')}
                  disabled={previewMutation.isPending}
                  className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition disabled:opacity-50"
                >
                  {previewMutation.isPending ? <><Spinner className="mr-1" />Loading...</> : 'Preview Enhanced 15s'}
                </button>
                <button
                  onClick={handleApply}
                  disabled={enhanceMutation.isPending}
                  className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded text-sm transition disabled:opacity-50"
                >
                  {enhanceMutation.isPending ? <><Spinner className="mr-1" />Applying...</> : 'Apply Enhancement'}
                </button>
              </div>

              {statusMsg && <div className="text-xs text-gray-600 bg-gray-50 rounded px-2 py-1 mt-3">{statusMsg}</div>}
            </div>
          </div>
        </div>
      )}

      {/* Audience Detection */}
      {assetId && (
        <div className="mt-4">
          <AudienceDetectionPanel assetId={assetId} waveformRef={waveformRef} />
        </div>
      )}
    </div>
  );
}

/* ---- Reusable sub-components ---- */

function FilterSection({ label, description, enabled, onToggle, children }: {
  label: string;
  description: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <div className={`border rounded-lg p-3 transition ${enabled ? 'border-brand-300 bg-brand-50/30' : 'border-gray-200 bg-gray-50/50'}`}>
      <div className="flex items-center justify-between mb-2">
        <div>
          <span className="text-sm font-medium">{label}</span>
          <p className="text-xs text-gray-400">{description}</p>
        </div>
        <label className="relative inline-flex items-center cursor-pointer">
          <input type="checkbox" checked={enabled} onChange={(e) => onToggle(e.target.checked)} className="sr-only peer" />
          <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-brand-600"></div>
        </label>
      </div>
      {enabled && <div className="space-y-2 mt-2">{children}</div>}
    </div>
  );
}

function SliderControl({ label, value, min, max, step = 1, unit, onChange }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit: string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <label className="text-xs text-gray-500 w-24 shrink-0">{label}</label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1"
      />
      <span className="text-xs text-gray-600 w-20 text-right">{value} {unit}</span>
    </div>
  );
}
