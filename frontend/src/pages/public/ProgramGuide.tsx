import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import apiClient from '../../api/client';
import type { StationListResponse, Station } from '../../types';

interface EpgBlock {
  id: string;
  name: string;
  description: string | null;
  start_time: string | null;
  end_time: string | null;
  playback_mode: string;
  schedule_name: string | null;
}

interface EpgResponse {
  station_id: string;
  date: string;
  blocks: EpgBlock[];
}

const playbackModeColors: Record<string, string> = {
  sequential: 'bg-blue-600/20 text-blue-300 border-blue-500/30',
  shuffle: 'bg-purple-600/20 text-purple-300 border-purple-500/30',
  weighted: 'bg-amber-600/20 text-amber-300 border-amber-500/30',
  rotation: 'bg-green-600/20 text-green-300 border-green-500/30',
};

function formatTime12h(time24: string): string {
  const [h, m] = time24.split(':').map(Number);
  const suffix = h >= 12 ? 'PM' : 'AM';
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${h12}:${m.toString().padStart(2, '0')} ${suffix}`;
}

function todayStr(): string {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

function isCurrentBlock(block: EpgBlock, selectedDate: string): boolean {
  if (!block.start_time || !block.end_time) return false;
  if (selectedDate !== todayStr()) return false;
  const now = new Date();
  const [sh, sm] = block.start_time.split(':').map(Number);
  const [eh, em] = block.end_time.split(':').map(Number);
  const nowMins = now.getHours() * 60 + now.getMinutes();
  const startMins = sh * 60 + sm;
  const endMins = eh * 60 + em;
  return nowMins >= startMins && nowMins < endMins;
}

export default function ProgramGuide() {
  const [selectedStation, setSelectedStation] = useState<string>('');
  const [selectedDate, setSelectedDate] = useState<string>(todayStr());

  // Fetch stations
  const { data: stationsData, isLoading: stationsLoading } = useQuery<StationListResponse>({
    queryKey: ['stations'],
    queryFn: async () => {
      const res = await apiClient.get<StationListResponse>('/stations');
      return res.data;
    },
  });

  const stations: Station[] = stationsData?.stations ?? [];

  // Auto-select first station
  useMemo(() => {
    if (!selectedStation && stations.length > 0) {
      setSelectedStation(stations[0].id);
    }
  }, [stations, selectedStation]);

  // Fetch EPG
  const { data: epgData, isLoading: epgLoading, isError: epgError } = useQuery<EpgResponse>({
    queryKey: ['epg', selectedStation, selectedDate],
    queryFn: async () => {
      const res = await apiClient.get<EpgResponse>(`/schedules/epg/${selectedStation}`, {
        params: { date: selectedDate },
      });
      return res.data;
    },
    enabled: !!selectedStation,
  });

  const blocks = epgData?.blocks ?? [];
  const dateLabel = selectedDate === todayStr() ? 'Today' : new Date(selectedDate + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });

  return (
    <div className="min-h-screen bg-[#0a0a28] text-white">
      <div className="max-w-3xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-cyan-300 mb-1">Program Guide</h1>
          <p className="text-gray-500 text-sm">Browse the daily schedule for any station</p>
        </div>

        {/* Controls */}
        <div className="flex flex-col sm:flex-row gap-3 mb-8">
          {/* Station selector */}
          <select
            value={selectedStation}
            onChange={(e) => setSelectedStation(e.target.value)}
            className="flex-1 bg-[#12123a] border border-[#2a2a5e] text-gray-200 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-cyan-500 transition"
          >
            {stationsLoading && <option value="">Loading stations...</option>}
            {!stationsLoading && stations.length === 0 && <option value="">No stations available</option>}
            {stations.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>

          {/* Date picker */}
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="bg-[#12123a] border border-[#2a2a5e] text-gray-200 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-cyan-500 transition"
          />
        </div>

        {/* Date label */}
        <div className="flex items-center gap-3 mb-6">
          <div className="h-px flex-1 bg-[#2a2a5e]" />
          <span className="text-xs font-bold uppercase tracking-wider text-gray-500">{dateLabel}</span>
          <div className="h-px flex-1 bg-[#2a2a5e]" />
        </div>

        {/* Loading state */}
        {epgLoading && (
          <div className="text-center py-16">
            <div className="inline-block w-8 h-8 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin mb-3" />
            <p className="text-gray-500 text-sm">Loading schedule...</p>
          </div>
        )}

        {/* Error state */}
        {epgError && !epgLoading && (
          <div className="text-center py-16">
            <p className="text-red-400 mb-1">Failed to load schedule</p>
            <p className="text-gray-600 text-sm">The station may not have a schedule configured yet.</p>
          </div>
        )}

        {/* Empty state */}
        {!epgLoading && !epgError && blocks.length === 0 && selectedStation && (
          <div className="text-center py-16">
            <div className="text-4xl mb-3 opacity-30">&#128251;</div>
            <p className="text-gray-500">No programs scheduled for this day</p>
          </div>
        )}

        {/* Timeline */}
        {!epgLoading && blocks.length > 0 && (
          <div className="relative">
            {/* Vertical line */}
            <div className="absolute left-[23px] top-0 bottom-0 w-px bg-[#2a2a5e]" />

            <div className="space-y-1">
              {blocks.map((block) => {
                const isCurrent = isCurrentBlock(block, selectedDate);
                const modeClass = playbackModeColors[block.playback_mode] || 'bg-gray-600/20 text-gray-400 border-gray-500/30';

                return (
                  <div
                    key={block.id}
                    className={`relative flex gap-4 group ${
                      isCurrent ? '' : ''
                    }`}
                  >
                    {/* Timeline dot */}
                    <div className="flex-shrink-0 w-[47px] flex items-start justify-center pt-4 relative z-10">
                      <div
                        className={`w-3 h-3 rounded-full border-2 ${
                          isCurrent
                            ? 'bg-cyan-400 border-cyan-400 shadow-lg shadow-cyan-400/50'
                            : 'bg-[#0a0a28] border-[#4a4a8e] group-hover:border-cyan-500'
                        } transition`}
                      />
                    </div>

                    {/* Block card */}
                    <div
                      className={`flex-1 rounded-lg px-4 py-3 mb-1 transition ${
                        isCurrent
                          ? 'bg-cyan-900/20 border border-cyan-500/30'
                          : 'bg-[#12123a]/60 border border-transparent hover:border-[#2a2a5e] hover:bg-[#12123a]'
                      }`}
                    >
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        {/* Time range */}
                        <span className={`text-sm font-mono ${isCurrent ? 'text-cyan-300' : 'text-gray-400'}`}>
                          {block.start_time ? formatTime12h(block.start_time) : '??:??'}
                          {' - '}
                          {block.end_time ? formatTime12h(block.end_time) : '??:??'}
                        </span>

                        {/* Current indicator */}
                        {isCurrent && (
                          <span className="text-[10px] uppercase font-bold tracking-wider text-cyan-400 bg-cyan-400/10 px-2 py-0.5 rounded-full">
                            Now
                          </span>
                        )}

                        {/* Playback mode badge */}
                        <span className={`text-[10px] uppercase font-medium px-2 py-0.5 rounded-full border ${modeClass}`}>
                          {block.playback_mode}
                        </span>
                      </div>

                      {/* Block name */}
                      <h3 className={`font-semibold ${isCurrent ? 'text-white' : 'text-gray-200'}`}>
                        {block.name}
                      </h3>

                      {/* Description */}
                      {block.description && (
                        <p className="text-sm text-gray-500 mt-0.5 line-clamp-2">{block.description}</p>
                      )}

                      {/* Schedule name */}
                      {block.schedule_name && (
                        <p className="text-[11px] text-gray-600 mt-1">
                          Schedule: {block.schedule_name}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* No station selected */}
        {!selectedStation && !stationsLoading && (
          <div className="text-center py-16">
            <p className="text-gray-500">Select a station to view its program guide</p>
          </div>
        )}
      </div>
    </div>
  );
}
