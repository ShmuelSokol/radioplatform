import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUploadAsset } from '../../hooks/useAssets';

export default function AssetUpload() {
  const [title, setTitle] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadAsset();
  const navigate = useNavigate();

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
      await uploadMutation.mutateAsync({ file, title });
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
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Audio File</label>
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,video/*,.mpg,.mpeg,.mp4,.mkv,.avi,.mov,.webm,.flac,.ogg,.opus,.wma,.aac,.m4a,.wv,.ape,.aiff,.aif"
            onChange={handleFileChange}
            className="w-full border rounded px-3 py-2"
            required
          />
          {file && (
            <p className="text-sm text-gray-500 mt-1">
              {file.name} ({(file.size / 1024 / 1024).toFixed(1)} MB)
            </p>
          )}
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full border rounded px-3 py-2"
            required
          />
        </div>
        <button
          type="submit"
          disabled={uploadMutation.isPending}
          className="w-full bg-brand-600 hover:bg-brand-700 text-white font-medium py-2 rounded transition disabled:opacity-50"
        >
          {uploadMutation.isPending ? 'Uploading...' : 'Upload'}
        </button>
      </form>
    </div>
  );
}
