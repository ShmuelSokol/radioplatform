import { useUiStore } from '../../stores/uiStore';

export default function VersionToggle() {
  const version = useUiStore((s) => s.version);
  const toggleVersion = useUiStore((s) => s.toggleVersion);
  const isV2 = version === 'v2';

  return (
    <button
      onClick={toggleVersion}
      title={isV2 ? 'Switch back to classic UI' : 'Try the new V2 UI'}
      className={`relative flex items-center gap-1.5 rounded-full px-1 py-0.5 text-[11px] font-semibold transition-all duration-300 select-none ${
        isV2
          ? 'bg-gradient-to-r from-violet-600 to-fuchsia-600 text-white shadow-[0_0_12px_rgba(168,85,247,0.5)]'
          : 'bg-gray-700/60 text-gray-300 hover:bg-gray-600/60'
      }`}
    >
      <span
        className={`rounded-full px-2 py-0.5 transition-all duration-300 ${
          !isV2 ? 'bg-white text-gray-900 shadow' : 'opacity-60'
        }`}
      >
        V1
      </span>
      <span
        className={`rounded-full px-2 py-0.5 transition-all duration-300 ${
          isV2 ? 'bg-white text-violet-700 shadow' : 'opacity-60'
        }`}
      >
        V2
      </span>
    </button>
  );
}
