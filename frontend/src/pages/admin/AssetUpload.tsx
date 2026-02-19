import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUploadAsset } from '../../hooks/useAssets';
import Spinner from '../../components/Spinner';

const IMPORT_FORMATS = [
  { value: 'mp2', label: 'MP2 (default)' },
  { value: 'mp3', label: 'MP3' },
  { value: 'mp4', label: 'MP4 (AAC)' },
  { value: 'wav', label: 'WAV' },
  { value: 'flac', label: 'FLAC' },
  { value: 'ogg', label: 'OGG' },
  { value: 'aac', label: 'AAC' },
  { value: 'original', label: 'Keep Original' },
] as const;

const ASSET_TYPES = [
  { value: 'music', label: 'Music' },
  { value: 'spot', label: 'Spot' },
  { value: 'shiur', label: 'Shiur' },
  { value: 'jingle', label: 'Jingle' },
  { value: 'zmanim', label: 'Zmanim' },
] as const;

function getFileExtension(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot).toLowerCase() : '';
}

export default function AssetUpload() {
  const [title, setTitle] = useState('');
  const [artist, setArtist] = useState('');
  const [album, setAlbum] = useState('');
  const [assetType, setAssetType] = useState('music');
  const [category, setCategory] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState('mp2');
  const [error, setError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadAsset();
  const navigate = useNavigate();

  const fileExt = file ? getFileExtension(file.name) : '';

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) {
      setFile(selected);
      if (!title) {
        setTitle(selected.name.replace(/\.[^.]+$/, ''));
      }
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      setError('Please select a file');
      return;
    }
    setError('');
    try {
      await uploadMutation.mutateAsync({
        file,
        title,
        format,
        artist: artist || undefined,
        album: album || undefined,
        asset_type: assetType,
        category: category || undefined,
      });
      navigate('/admin/assets');
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? 'Upload failed';
      setError(typeof msg === 'string' ? msg : 'Upload failed');
    }
  };

  return (
    <div className="max-w-xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Upload Asset</h1>
      <form onSubmit={handleSubmit} className="bg-white shadow rounded-lg p-6 space-y-4">
        {error && (
          <div className="bg-red-50 text-red-600 p-3 rounded text-sm">{error}</div>
        )}

        {/* File */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Audio / Video File</label>
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,video/*,.mp2,.mpg,.mpeg,.mp4,.mkv,.avi,.mov,.webm,.flac,.ogg,.opus,.wma,.aac,.m4a,.wv,.ape,.aiff,.aif"
            onChange={handleFileChange}
            className="w-full border rounded px-3 py-2"
            required
          />
          {file && (
            <p className="text-sm text-gray-500 mt-1">
              {file.name} ({(file.size / 1024 / 1024).toFixed(1)} MB)
              <span className="ml-2 inline-block bg-gray-100 text-gray-600 text-xs font-mono px-1.5 py-0.5 rounded">
                {fileExt || 'unknown'}
              </span>
            </p>
          )}
        </div>

        {/* Title */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Title <span className="text-red-500">*</span></label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full border rounded px-3 py-2"
            required
          />
        </div>

        {/* Artist */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Artist</label>
          <input
            value={artist}
            onChange={(e) => setArtist(e.target.value)}
            placeholder="Optional"
            className="w-full border rounded px-3 py-2"
          />
        </div>

        {/* Album */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Album</label>
          <input
            value={album}
            onChange={(e) => setAlbum(e.target.value)}
            placeholder="Optional"
            className="w-full border rounded px-3 py-2"
          />
        </div>

        {/* Type + Category side by side */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Type</label>
            <select
              value={assetType}
              onChange={(e) => setAssetType(e.target.value)}
              className="w-full border rounded px-3 py-2 bg-white"
            >
              {ASSET_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Category</label>
            <input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="Optional"
              className="w-full border rounded px-3 py-2"
            />
          </div>
        </div>

        {/* Format */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Convert To</label>
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value)}
            className="w-full border rounded px-3 py-2 bg-white"
          >
            {IMPORT_FORMATS.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
          <p className="text-xs text-gray-400 mt-1">
            File will be converted server-side via FFmpeg before storage
          </p>
        </div>

        <button
          type="submit"
          disabled={uploadMutation.isPending}
          className="w-full bg-brand-600 hover:bg-brand-700 text-white font-medium py-2 rounded transition disabled:opacity-50"
        >
          {uploadMutation.isPending ? <><Spinner className="mr-2" />Processing...</> : 'Upload'}
        </button>
      </form>
    </div>
  );
}
