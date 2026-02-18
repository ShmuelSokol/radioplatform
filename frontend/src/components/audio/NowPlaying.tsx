import { useNowPlaying } from '../../hooks/useNowPlaying';

interface Props {
  stationId: string;
}

export default function NowPlaying({ stationId }: Props) {
  const { data } = useNowPlaying(stationId);

  if (!data?.now_playing) {
    return <span className="text-sm text-gray-400">Nothing playing</span>;
  }

  return (
    <div className="flex items-center gap-3 min-w-0">
      <div className="w-10 h-10 bg-gray-700 rounded flex items-center justify-center text-lg">
        &#9835;
      </div>
      <div className="min-w-0">
        <p className="text-sm font-medium truncate">{data.now_playing.title}</p>
        <p className="text-xs text-gray-400">
          {data.state === 'playing' ? 'Now Playing' : data.state}
        </p>
      </div>
    </div>
  );
}
