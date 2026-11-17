import apiClient from "./client";

export type FindingKind = "overlap" | "gap" | "spot_unplaced" | "off_air" | "block_item" | "plays";

export interface FindingBlock {
  id: string;
  name: string;
  schedule: string;
  schedule_id: string;
  priority: number;
  wins: boolean;
}

export interface Finding {
  kind: FindingKind;
  start: string;
  end: string;
  local_start: string;
  local_end: string;
  title: string;
  detail: string;
  resolution: string;
  needs_attention: boolean;
  blocks: FindingBlock[];
}

export interface ScheduleLookAhead {
  station_id: string;
  timezone: string;
  days: number;
  needs_attention: number;
  auto_resolved: number;
  /** Spots mode is off in the control room: sponsor spots are not planned */
  spots_off?: boolean;
  findings: Finding[];
}

export const getScheduleLookAhead = async (
  stationId: string,
  days = 7,
): Promise<ScheduleLookAhead> =>
  (
    await apiClient.get<ScheduleLookAhead>("/schedules/look-ahead", {
      params: { station_id: stationId, days },
    })
  ).data;

export const settleOverlap = async (
  winnerBlockId: string,
  otherBlockIds: string[],
): Promise<void> => {
  await apiClient.post("/schedules/look-ahead/settle", {
    winner_block_id: winnerBlockId,
    other_block_ids: otherBlockIds,
  });
};
