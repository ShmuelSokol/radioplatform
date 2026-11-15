import apiClient from './client';

export type BlockTiming = 'hard' | 'soft';

export type BlockItem =
  | { kind: 'asset'; asset_id: string; label?: string }
  | { kind: 'category'; asset_type: string; category?: string; label?: string }
  | { kind: 'time'; label?: string }
  | { kind: 'weather'; label?: string };

export interface ProgramBlock {
  id: string;
  station_id: string;
  name: string;
  is_active: boolean;
  days_of_week: string; // "0,1,..." 0 = Monday
  times: string; // "07:00,08:00" station time
  timing_mode: BlockTiming;
  items: BlockItem[];
}

export type ProgramBlockInput = Omit<ProgramBlock, 'id'>;

export interface PreviewItem {
  kind: string;
  label: string;
  title: string | null;
  duration: number | null;
  problem: string | null;
}

export interface PreviewOccurrence {
  block_id: string;
  block_name: string;
  at: string;
  local_time: string;
  timing_mode: BlockTiming;
  seconds: number;
  items: PreviewItem[];
  problems: string[];
}

export interface BlockPreview {
  timezone: string;
  hours: number;
  occurrences: PreviewOccurrence[];
}

const BASE = '/program-blocks';

export const listProgramBlocks = async (stationId: string): Promise<ProgramBlock[]> =>
  (await apiClient.get(BASE, { params: { station_id: stationId } })).data;

export const previewProgramBlocks = async (stationId: string, hours = 24): Promise<BlockPreview> =>
  (await apiClient.get(`${BASE}/preview`, { params: { station_id: stationId, hours } })).data;

export const createProgramBlock = async (data: ProgramBlockInput): Promise<ProgramBlock> =>
  (await apiClient.post(BASE, data)).data;

export const updateProgramBlock = async (
  id: string,
  data: Partial<ProgramBlockInput>,
): Promise<ProgramBlock> => (await apiClient.put(`${BASE}/${id}`, data)).data;

export const deleteProgramBlock = async (id: string): Promise<void> => {
  await apiClient.delete(`${BASE}/${id}`);
};
