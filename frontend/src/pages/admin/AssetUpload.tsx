import { useState, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { uploadAsset } from '../../api/assets';
import { useQueryClient } from '@tanstack/react-query';
import { useCategories } from '../../hooks/useCategories';
import { useAssetTypes } from '../../hooks/useAssetTypes';
import Spinner from '../../components/Spinner';

const IMPORT_FORMATS = [
  { value: 'mp3', label: 'MP3 (default)' },
  { value: 'mp2', label: 'MP2' },
  { value: 'mp4', label: 'MP4 (AAC)' },
  { value: 'wav', label: 'WAV' },
  { value: 'flac', label: 'FLAC' },
  { value: 'ogg', label: 'OGG' },
  { value: 'aac', label: 'AAC' },
  { value: 'original', label: 'Keep Original' },
] as const;

const FILE_ACCEPT = 'audio/*,video/*,.mp2,.mpg,.mpeg,.mp4,.mkv,.avi,.mov,.webm,.flac,.ogg,.opus,.wma,.aac,.m4a,.wv,.ape,.aiff,.aif';

interface QueuedFile {
  id: string;
  file: File;
  title: string;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
}

function getFileExtension(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot).toLowerCase() : '';
}

function stripExtension(filename: string): string {
  return filename.replace(/\.[^.]+$/, '');
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function AssetUpload() {
  const queryClient = useQueryClient();
  const { data: categories } = useCategories();
  const { data: assetTypes } = useAssetTypes();

  // Global metadata
  const [artist, setArtist] = useState('');
  const [album, setAlbum] = useState('');
  const [assetType, setAssetType] = useState('music');
  const [category, setCategory] = useState('');
  const [format, setFormat] = useState('mp3');

  // File queue
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadIndex, setUploadIndex] = useState(0);
  const [done, setDone] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const addFiles = useCallback((files: FileList | File[]) => {
    const newFiles: QueuedFile[] = Array.from(files).map((f) => ({
      id: crypto.randomUUID(),
      file: f,
      title: stripExtension(f.name),
      status: 'pending' as const,
    }));
    setQueue((prev) => [...prev, ...newFiles]);
    setDone(false);
  }, []);

  const removeFile = (id: string) => {
    setQueue((prev) => prev.filter((f) => f.id !== id));
  };

  const updateTitle = (id: string, title: string) => {
    setQueue((prev) => prev.map((f) => (f.id === id ? { ...f, title } : f)));
  };

  const clearAll = () => {
    setQueue([]);
    setDone(false);
  };

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    },
    [addFiles],
  );

  const handleUploadAll = async () => {
    const pending = queue.filter((f) => f.status === 'pending' || f.status === 'error');
    if (!pending.length) return;

    setUploading(true);
    setDone(false);
    let idx = 0;

    for (const item of pending) {
      idx++;
      setUploadIndex(idx);
      setQueue((prev) =>
        prev.map((f) => (f.id === item.id ? { ...f, status: 'uploading' as const, error: undefined } : f)),
      );

      try {
        await uploadAsset(
          item.file,
          item.title,
          format,
          artist || undefined,
          album || undefined,
          assetType,
          category || undefined,
        );
        setQueue((prev) => prev.map((f) => (f.id === item.id ? { ...f, status: 'done' as const } : f)));
      } catch (err: any) {
        const msg = err?.response?.data?.detail ?? err?.message ?? 'Upload failed';
        setQueue((prev) =>
          prev.map((f) =>
            f.id === item.id
              ? { ...f, status: 'error' as const, error: typeof msg === 'string' ? msg : 'Upload failed' }
              : f,
          ),
        );
      }
    }

    queryClient.invalidateQueries({ queryKey: ['assets'] });
    setUploading(false);
    setDone(true);
  };

  const pendingCount = queue.filter((f) => f.status === 'pending' || f.status === 'error').length;
  const doneCount = queue.filter((f) => f.status === 'done').length;
  const errorCount = queue.filter((f) => f.status === 'error').length;
  const totalToUpload = queue.filter((f) => f.status !== 'done').length;

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Upload Assets</h1>

      {/* Drop zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition mb-6 ${
          dragOver
            ? 'border-brand-500 bg-brand-50'
            : 'border-gray-300 hover:border-gray-400 bg-gray-50'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept={FILE_ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) addFiles(e.target.files);
            e.target.value = '';
          }}
        />
        <div className="text-gray-500">
          <svg className="mx-auto h-10 w-10 text-gray-400 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16" />
          </svg>
          <p className="text-sm font-medium">Drop files here or click to browse</p>
          <p className="text-xs text-gray-400 mt-1">Audio and video files accepted</p>
        </div>
      </div>

      {/* Global metadata */}
      <div className="bg-white shadow rounded-lg p-6 mb-6">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">Apply to all files</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Artist</label>
            <input
              value={artist}
              onChange={(e) => setArtist(e.target.value)}
              placeholder="Optional"
              className="w-full border rounded px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Album</label>
            <input
              value={album}
              onChange={(e) => setAlbum(e.target.value)}
              placeholder="Optional"
              className="w-full border rounded px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Type</label>
            <select
              value={assetType}
              onChange={(e) => setAssetType(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm bg-white"
            >
              {(assetTypes ?? []).map((t) => (
                <option key={t.id} value={t.name}>{t.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Category</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm bg-white"
            >
              <option value="">None</option>
              {categories?.map((cat) => (
                <option key={cat.id} value={cat.name}>{cat.name}</option>
              ))}
            </select>
          </div>
          <div className="col-span-2">
            <label className="block text-xs text-gray-500 mb-1">Convert To</label>
            <select
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm bg-white"
            >
              {IMPORT_FORMATS.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </select>
            <p className="text-xs text-gray-400 mt-1">Files will be converted server-side via FFmpeg</p>
          </div>
        </div>
      </div>

      {/* File queue */}
      {queue.length > 0 && (
        <div className="bg-white shadow rounded-lg mb-6">
          <div className="px-6 py-3 border-b border-gray-100 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-700">
              Files ({queue.length})
            </h2>
            {!uploading && (
              <button onClick={clearAll} className="text-xs text-red-500 hover:text-red-700">
                Clear All
              </button>
            )}
          </div>
          <div className="divide-y divide-gray-100 max-h-80 overflow-y-auto">
            {queue.map((item) => (
              <div key={item.id} className="px-6 py-3 flex items-center gap-3">
                {/* Status icon */}
                <div className="flex-shrink-0 w-5 text-center">
                  {item.status === 'pending' && (
                    <span className="inline-block w-2.5 h-2.5 rounded-full bg-gray-300" title="Pending" />
                  )}
                  {item.status === 'uploading' && <Spinner />}
                  {item.status === 'done' && (
                    <svg className="w-4 h-4 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                  {item.status === 'error' && (
                    <span className="text-red-500 cursor-help" title={item.error || 'Upload failed'}>
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </span>
                  )}
                </div>

                {/* Editable title */}
                <input
                  value={item.title}
                  onChange={(e) => updateTitle(item.id, e.target.value)}
                  disabled={item.status === 'uploading' || item.status === 'done'}
                  className="flex-1 border rounded px-2 py-1 text-sm disabled:bg-gray-50 disabled:text-gray-400"
                />

                {/* File info */}
                <span className="text-xs text-gray-400 whitespace-nowrap">
                  {formatSize(item.file.size)}
                </span>
                <span className="inline-block bg-gray-100 text-gray-600 text-xs font-mono px-1.5 py-0.5 rounded">
                  {getFileExtension(item.file.name) || '?'}
                </span>

                {/* Remove button */}
                {item.status !== 'uploading' && item.status !== 'done' && (
                  <button
                    onClick={() => removeFile(item.id)}
                    className="text-gray-400 hover:text-red-500 text-sm"
                    title="Remove"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Action bar */}
      <div className="flex items-center gap-4">
        <button
          onClick={handleUploadAll}
          disabled={uploading || pendingCount === 0}
          className="bg-brand-600 hover:bg-brand-700 text-white font-medium py-2 px-6 rounded transition disabled:opacity-50"
        >
          {uploading ? (
            <>
              <Spinner className="mr-2" />
              Uploading {uploadIndex} of {totalToUpload + doneCount}...
            </>
          ) : (
            `Upload All (${pendingCount} file${pendingCount !== 1 ? 's' : ''})`
          )}
        </button>

        {done && (
          <div className="text-sm">
            <span className="text-green-600 font-medium">{doneCount} uploaded</span>
            {errorCount > 0 && (
              <span className="text-red-600 font-medium ml-2">{errorCount} failed</span>
            )}
            <span className="mx-2 text-gray-300">|</span>
            <Link to="/admin/assets" className="text-brand-600 hover:text-brand-800">
              View Assets
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
