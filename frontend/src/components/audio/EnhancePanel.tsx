import { useState, useRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useEnhancePresets, useEnhanceAsset, useEnhancePreview, useRestoreOriginal } from '../../hooks/useAssets';
import Spinner from '../Spinner';

interface EnhancePanelProps {
  assetId: string;
  onEnhanceComplete?: () => void;
}

const PRESET_LABELS: Record<string, string> = {
  broadcast_polish: 'Broadcast Polish',
  noise_reduction: 'Noise Reduction',
  clarity_boost: 'Clarity Boost',
  warm_up: 'Warm Up',
  de_hiss: 'De-Hiss',
};

export default function EnhancePanel({ assetId, onEnhanceComplete }: EnhancePanelProps) {
  const { data: presetsData } = useEnhancePresets();
  const enhanceMutation = useEnhanceAsset();
  const previewMutation = useEnhancePreview();
  const restoreMutation = useRestoreOriginal();

  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);
  const [enhancePending, setEnhancePending] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  // Cleanup blob URL on unmount
  useEffect(() => {
    return () => {
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    };
  }, []);

  const presetNames = presetsData ? Object.keys(presetsData.presets) : [];

  const handlePreview = (preset: string) => {
    setSelectedPreset(preset);
    setStatusMsg(null);
    previewMutation.mutate(
      { id: assetId, body: { preset, start_seconds: 0, duration_seconds: 15 } },
      {
        onSuccess: (blob) => {
          if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
          const url = URL.createObjectURL(blob);
          blobUrlRef.current = url;
          if (!audioRef.current) {
            audioRef.current = new Audio();
          }
          audioRef.current.src = url;
          audioRef.current.play();
          setStatusMsg(`Previewing "${PRESET_LABELS[preset] || preset}"...`);
        },
        onError: () => setStatusMsg('Preview failed'),
      }
    );
  };

  const handleApply = () => {
    if (!selectedPreset) return;
    enhanceMutation.mutate(
      { id: assetId, body: { preset: selectedPreset } },
      {
        onSuccess: () => {
          setEnhancePending(true);
          setStatusMsg(null);
          onEnhanceComplete?.();
        },
        onError: () => setStatusMsg('Enhancement failed'),
      }
    );
  };

  const handleUndo = () => {
    restoreMutation.mutate(assetId, {
      onSuccess: () => {
        setEnhancePending(false);
        setSelectedPreset(null);
        setStatusMsg('Original restored!');
        onEnhanceComplete?.();
      },
      onError: (err) => {
        const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        setStatusMsg(msg || 'Restore failed');
      },
    });
  };

  const handleSave = () => {
    setEnhancePending(false);
    setSelectedPreset(null);
    setStatusMsg('Enhancement saved.');
  };

  // Post-apply review state
  if (enhancePending) {
    return (
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-semibold mb-3">Review Enhancement</h3>
        <div className="bg-amber-50 border border-amber-200 rounded p-3 mb-4">
          <p className="text-sm text-amber-800 font-medium mb-1">Enhancement applied — review the result</p>
          <p className="text-xs text-amber-600">Play the audio to check. Undo to restore original, or Save to keep.</p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={handleUndo}
            disabled={restoreMutation.isPending}
            className="px-4 py-2 text-sm bg-gray-100 hover:bg-gray-200 text-gray-700 rounded transition disabled:opacity-50"
          >
            {restoreMutation.isPending ? <><Spinner className="mr-1" />Restoring...</> : 'Undo Enhancement'}
          </button>
          <button onClick={handleSave} className="px-4 py-2 text-sm bg-green-600 hover:bg-green-700 text-white rounded transition">
            Save
          </button>
        </div>
        {statusMsg && <div className="text-xs text-green-600 bg-green-50 rounded px-2 py-1 mt-3">{statusMsg}</div>}
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">Audio Enhancement</h3>
        <Link
          to={`/admin/audio-enhance/${assetId}`}
          className="text-xs text-brand-600 hover:text-brand-800"
        >
          Full Editor &rarr;
        </Link>
      </div>

      {/* Preset buttons */}
      <div className="flex flex-wrap gap-2 mb-3">
        {presetNames.map((key) => (
          <button
            key={key}
            onClick={() => setSelectedPreset(key)}
            className={`px-3 py-1.5 rounded text-xs transition ${
              selectedPreset === key
                ? 'bg-brand-600 text-white'
                : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
            }`}
          >
            {PRESET_LABELS[key] || key}
          </button>
        ))}
      </div>

      {/* Actions */}
      {selectedPreset && (
        <div className="flex flex-wrap gap-2 mb-3">
          <button
            onClick={() => handlePreview(selectedPreset)}
            disabled={previewMutation.isPending}
            className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm transition disabled:opacity-50"
          >
            {previewMutation.isPending ? <><Spinner className="mr-1" />Loading...</> : 'Preview 15s'}
          </button>
          <button
            onClick={handleApply}
            disabled={enhanceMutation.isPending}
            className="bg-brand-600 hover:bg-brand-700 text-white px-3 py-1.5 rounded text-sm transition disabled:opacity-50"
          >
            {enhanceMutation.isPending ? <><Spinner className="mr-1" />Applying...</> : 'Apply'}
          </button>
        </div>
      )}

      {/* Status */}
      {statusMsg && <div className="text-xs text-gray-600 bg-gray-50 rounded px-2 py-1">{statusMsg}</div>}
    </div>
  );
}
