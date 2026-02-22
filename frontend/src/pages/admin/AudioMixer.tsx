import { useState, useMemo, useCallback } from 'react';
import { useAssets, useAssetAudioUrl } from '../../hooks/useAssets';
import { mixTracks } from '../../api/assets';
import type { MixRequest } from '../../api/assets';
import type { Asset } from '../../types';
import WaveformPlayer from '../../components/audio/WaveformPlayer';

function AssetDropdown({
  label,
  assets,
  value,
  onChange,
  filter,
}: {
  label: string;
  assets: Asset[];
  value: string;
  onChange: (id: string) => void;
  filter?: (a: Asset) => boolean;
}) {
  const [search, setSearch] = useState('');
  const filtered = useMemo(() => {
    let list = filter ? assets.filter(filter) : assets;
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (a) =>
          a.title.toLowerCase().includes(q) ||
          (a.artist && a.artist.toLowerCase().includes(q)),
      );
    }
    return list.slice(0, 100);
  }, [assets, search, filter]);

  return (
    <div className="flex-1 min-w-[200px]">
      <label className="text-xs text-gray-400 block mb-1">{label}</label>
      <input
        type="text"
        placeholder="Search assets..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full px-3 py-1.5 mb-1 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
      />
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
        size={5}
      >
        <option value="">-- select --</option>
        {filtered.map((a) => (
          <option key={a.id} value={a.id}>
            {a.title}
            {a.artist ? ` — ${a.artist}` : ''}
            {a.duration ? ` (${Math.round(a.duration)}s)` : ''}
          </option>
        ))}
      </select>
    </div>
  );
}

function WaveformToggle({ assetId, label }: { assetId: string; label: string }) {
  const [show, setShow] = useState(false);
  const { data: url } = useAssetAudioUrl(show ? assetId : undefined);

  return (
    <div>
      <button
        onClick={() => setShow(!show)}
        className="text-xs text-cyan-400 hover:text-cyan-300 mb-1"
      >
        {show ? `Hide ${label} Waveform` : `Show ${label} Waveform`}
      </button>
      {show && url && <WaveformPlayer url={url} />}
    </div>
  );
}

export default function AudioMixer() {
  const { data: assetData } = useAssets({ skip: 0, limit: 500 });
  const assets = assetData?.assets ?? [];

  // Selected assets
  const [btId, setBtId] = useState('');
  const [ovId, setOvId] = useState('');

  // Backtrack shaping
  const [btTrimStart, setBtTrimStart] = useState(0);
  const [btTrimEnd, setBtTrimEnd] = useState(0);
  const [btTargetDur, setBtTargetDur] = useState(0);

  // Volume
  const [btVolume, setBtVolume] = useState(0.2);
  const [ovVolume, setOvVolume] = useState(1.0);

  // Fades
  const [btFadeIn, setBtFadeIn] = useState(0);
  const [btFadeOut, setBtFadeOut] = useState(2);
  const [ovFadeIn, setOvFadeIn] = useState(0);
  const [ovFadeOut, setOvFadeOut] = useState(0);

  // Output
  const [outputTitle, setOutputTitle] = useState('');
  const [outputType, setOutputType] = useState('spot');

  // Status
  const [mixing, setMixing] = useState(false);
  const [success, setSuccess] = useState('');
  const [error, setError] = useState('');

  const btAsset = useMemo(() => assets.find((a) => a.id === btId), [assets, btId]);
  const ovAsset = useMemo(() => assets.find((a) => a.id === ovId), [assets, ovId]);

  // Compute effective backtrack duration for fade-out start
  const btEffectiveDur = useMemo(() => {
    if (btTargetDur > 0) return btTargetDur;
    if (btTrimEnd > 0) return btTrimEnd - btTrimStart;
    return btAsset?.duration ?? 0;
  }, [btTargetDur, btTrimEnd, btTrimStart, btAsset]);

  const ovEffectiveDur = useMemo(() => {
    return ovAsset?.duration ?? 0;
  }, [ovAsset]);

  const applyVoiceOverPreset = useCallback(() => {
    setBtVolume(0.2);
    setOvVolume(1.0);
    setBtFadeOut(2);
    setBtFadeIn(0);
    setOvFadeIn(0);
    setOvFadeOut(0);
  }, []);

  const handleMix = useCallback(async () => {
    if (!btId || !ovId || !outputTitle.trim()) return;
    setMixing(true);
    setError('');
    setSuccess('');

    const body: MixRequest = {
      backtrack_asset_id: btId,
      overlay_asset_id: ovId,
      output_title: outputTitle.trim(),
      output_asset_type: outputType,
      bt_trim_start: btTrimStart,
      bt_trim_end: btTrimEnd,
      bt_target_dur: btTargetDur,
      bt_volume: btVolume,
      ov_volume: ovVolume,
      bt_fade_in: btFadeIn,
      bt_fade_out: btFadeOut,
      bt_fade_out_start: btEffectiveDur > btFadeOut ? btEffectiveDur - btFadeOut : 0,
      ov_fade_in: ovFadeIn,
      ov_fade_out: ovFadeOut,
      ov_fade_out_start: ovEffectiveDur > ovFadeOut ? ovEffectiveDur - ovFadeOut : 0,
    };

    try {
      await mixTracks(body);
      setSuccess(`"${outputTitle}" saved to library.`);
      setOutputTitle('');
      setTimeout(() => setSuccess(''), 5000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Mix failed';
      setError(msg);
    } finally {
      setMixing(false);
    }
  }, [
    btId, ovId, outputTitle, outputType, btTrimStart, btTrimEnd,
    btTargetDur, btVolume, ovVolume, btFadeIn, btFadeOut,
    btEffectiveDur, ovFadeIn, ovFadeOut, ovEffectiveDur,
  ]);

  return (
    <div className="space-y-5">
      {/* Asset Selection */}
      <div className="flex gap-4 flex-wrap">
        <AssetDropdown
          label="Backtrack (music/bed)"
          assets={assets}
          value={btId}
          onChange={setBtId}
          filter={(a) => a.asset_type === 'music'}
        />
        <AssetDropdown
          label="Overlay (voice/spot)"
          assets={assets}
          value={ovId}
          onChange={setOvId}
        />
      </div>

      {/* Waveform Previews */}
      {btId && <WaveformToggle assetId={btId} label="Backtrack" />}
      {ovId && <WaveformToggle assetId={ovId} label="Overlay" />}

      {/* Backtrack Shaping */}
      <div>
        <h3 className="text-xs text-gray-400 uppercase font-bold mb-2">Backtrack Shaping</h3>
        <div className="flex gap-4 flex-wrap">
          <div>
            <label className="text-xs text-gray-500 block">Trim Start (s)</label>
            <input
              type="number"
              min={0}
              step={0.1}
              value={btTrimStart}
              onChange={(e) => setBtTrimStart(Number(e.target.value))}
              className="w-24 px-2 py-1 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block">Trim End (s)</label>
            <input
              type="number"
              min={0}
              step={0.1}
              value={btTrimEnd}
              onChange={(e) => setBtTrimEnd(Number(e.target.value))}
              className="w-24 px-2 py-1 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
            />
            <div className="text-[10px] text-gray-600">0 = full file</div>
          </div>
          <div>
            <label className="text-xs text-gray-500 block">Target Duration / Pad (s)</label>
            <input
              type="number"
              min={0}
              step={0.1}
              value={btTargetDur}
              onChange={(e) => setBtTargetDur(Number(e.target.value))}
              className="w-24 px-2 py-1 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
            />
            <div className="text-[10px] text-gray-600">0 = no pad</div>
          </div>
        </div>
        {btAsset?.duration && (
          <div className="text-xs text-gray-500 mt-1">
            Original: {Math.round(btAsset.duration)}s | Effective: {Math.round(btEffectiveDur)}s
          </div>
        )}
      </div>

      {/* Volume */}
      <div>
        <h3 className="text-xs text-gray-400 uppercase font-bold mb-2">Volume</h3>
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400 w-20">Backtrack:</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={btVolume}
              onChange={(e) => setBtVolume(Number(e.target.value))}
              className="flex-1 max-w-[200px]"
            />
            <span className="text-xs text-gray-300 w-12">{Math.round(btVolume * 100)}%</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400 w-20">Overlay:</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={ovVolume}
              onChange={(e) => setOvVolume(Number(e.target.value))}
              className="flex-1 max-w-[200px]"
            />
            <span className="text-xs text-gray-300 w-12">{Math.round(ovVolume * 100)}%</span>
          </div>
        </div>
        <button
          onClick={applyVoiceOverPreset}
          className="mt-2 px-3 py-1 text-xs bg-[#2a2a5e] hover:bg-[#3a3a7e] rounded text-cyan-300"
        >
          Apply Voice-Over Preset
        </button>
        <span className="text-[10px] text-gray-600 ml-2">BT 20%, OV 100%, fade out 2s</span>
      </div>

      {/* Fades */}
      <div>
        <h3 className="text-xs text-gray-400 uppercase font-bold mb-2">Fades (seconds)</h3>
        <div className="flex gap-4 flex-wrap">
          <div>
            <label className="text-xs text-gray-500 block">BT Fade In</label>
            <input
              type="number"
              min={0}
              step={0.5}
              value={btFadeIn}
              onChange={(e) => setBtFadeIn(Number(e.target.value))}
              className="w-20 px-2 py-1 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block">BT Fade Out</label>
            <input
              type="number"
              min={0}
              step={0.5}
              value={btFadeOut}
              onChange={(e) => setBtFadeOut(Number(e.target.value))}
              className="w-20 px-2 py-1 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block">OV Fade In</label>
            <input
              type="number"
              min={0}
              step={0.5}
              value={ovFadeIn}
              onChange={(e) => setOvFadeIn(Number(e.target.value))}
              className="w-20 px-2 py-1 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block">OV Fade Out</label>
            <input
              type="number"
              min={0}
              step={0.5}
              value={ovFadeOut}
              onChange={(e) => setOvFadeOut(Number(e.target.value))}
              className="w-20 px-2 py-1 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
            />
          </div>
        </div>
      </div>

      {/* Output */}
      <div>
        <h3 className="text-xs text-gray-400 uppercase font-bold mb-2">Output</h3>
        <div className="flex gap-3 flex-wrap">
          <input
            type="text"
            placeholder="Title *"
            value={outputTitle}
            onChange={(e) => setOutputTitle(e.target.value)}
            className="flex-1 min-w-[200px] px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
          />
          <select
            value={outputType}
            onChange={(e) => setOutputType(e.target.value)}
            className="px-3 py-2 bg-[#0a0a28] border border-[#2a2a5e] rounded text-white text-sm"
          >
            <option value="spot">Spot</option>
            <option value="jingle">Jingle</option>
            <option value="music">Music</option>
          </select>
        </div>
      </div>

      {/* Mix Button */}
      <button
        onClick={handleMix}
        disabled={mixing || !btId || !ovId || !outputTitle.trim()}
        className="px-6 py-2 bg-cyan-700 hover:bg-cyan-600 rounded text-white text-sm font-semibold disabled:opacity-50 transition"
      >
        {mixing ? 'Mixing...' : 'Mix & Save to Library'}
      </button>

      {success && <div className="text-sm text-green-400">{success}</div>}
      {error && <div className="text-sm text-red-400">{error}</div>}
    </div>
  );
}
