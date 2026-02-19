import { useNowPlayingWS } from '../../hooks/useNowPlayingWS';

interface Props {
  stationId: string;
}

export default function NowPlaying({ stationId }: Props) {
  const { nowPlaying, isConnected } = useNowPlayingWS(stationId);

  if (!nowPlaying?.asset) {
    return <span className="text-sm text-gray-400">Nothing playing</span>;
  }

  return (
    <div className="flex items-center gap-3 min-w-0">
      <div className="w-10 h-10 bg-gray-700 rounded flex items-center justify-center text-lg">
        &#9835;
      </div>
      <div className="min-w-0">
        <p className="text-sm font-medium truncate">{nowPlaying.asset.title}</p>
        <p className="text-xs text-gray-400">
          {nowPlaying.asset.artist || (isConnected ? 'Now Playing' : 'Connecting...')}
        </p>
      </div>
    </div>
  );
}
