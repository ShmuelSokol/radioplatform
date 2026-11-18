# Schedule look-ahead and conflict resolution

`GET /schedules/look-ahead?station_id&days` (manager) walks the next N days in 15-minute steps of **station time** and reports findings: `overlap`, `gap`, `off_air`, `spot_unplaced`. Page: `/admin/schedule-check` (`ScheduleCheck.tsx`). Service: `schedule_lookahead_service.py`.

## It uses the engine's own resolver

Both the playout engine (`SchedulingService.get_active_block_for_station`) and the look-ahead call `SchedulingService.rank_matching_blocks`. Do not write a second matcher: the page is only worth having if it shows what the engine will actually do.

## Blocks are station wall-clock time

Before 2026-11-15 the engine compared block `start_time`/`end_time` against the **UTC** time of day, while sun-relative times were resolved in station time. A "6 to 9 AM" block on a New York station ran 1 to 4 AM. `station_local()` now converts first. If existing blocks were entered in UTC to compensate, they need re-entering.

## Date range on recurring blocks

A daily, weekly or monthly block with `start_date` and/or `end_date` only recurs on
**station-local** dates inside that range; either end may be empty (open-ended). The one
check is `block_in_date_range` in `scheduling.py`, called from `_recurs_on`, so playout,
the public guide (`GET /schedules/epg`), the look-ahead and the queue refresh all agree.
The recurrence is checked on the day a block starts, so an overnight block that starts on
its last date still runs past midnight. A start-only block stops once its `end_date` is
over (`last_start`). Before 2026-11-17 only one-time blocks checked dates, so a "summer
only" block kept airing and stayed in the guide (B-PUBLIC-5). Tests:
`test_block_resolution.py` (`test_recurring_block_*`, `test_start_only_block_stops_after_its_end_date`),
`test_public_listener_endpoints.py::test_epg_honours_recurring_block_dates`.

## Who wins an overlap (`block_rank`)

Schedule priority, then block priority, then the more specific recurrence (one-time > monthly > weekly > daily), then the shorter block, then the newer one. Before this, equal priorities fell to list order. An overlap settled only by the tie-break is flagged `needs_attention`; `POST /schedules/look-ahead/settle` records a manager's choice by raising the chosen block's priority above the others.

## Not flagged

Gaps (the automatic queue keeps playing) and off-air windows are listed but never `needs_attention`. Sponsor plays skipped for Shabbos are not findings; plays with no free slot are. See [spot-schedules](spot-schedules.md).

## Start-only blocks (`until_next`), the studio program's playlists

KBR Studio 1.82 schedules playlists by a start only ("6 am mix 01234 6:00", "blanks Candle+1", "motzei 72"); each runs until the next one starts. A `ScheduleBlock` with `until_next=true` works that way: `end_time` is ignored, `SchedulingService.running_playlist` picks the start-only block whose `last_start` (looking back up to 8 days, sun-relative starts resolved per day) is most recent. It joins the ranking as the base layer: a timed block over it wins a priority tie, and the look-ahead does not report that as an overlap.

Candle lighting is not its own sun event: use `sunset` with offset -18 (so "Candle+1" is -17), and "72 after Shabbos" is `sunset` +72 on Saturday. The block form has quick-start buttons for both. Sun times need the station's latitude and longitude; without them the code silently uses Jerusalem.

## What plays (`kind="plays"`)

One finding per run of the winning block (`rank_matching_blocks(...)[0]`, start-only blocks included), with the playlist name in `detail`, `needs_attention=False`. Added 2026-11-16 because a lone block that collided with nothing never appeared, so a Mac test could not tell a new block was scheduled. Schedule Check shows these under "What plays".


## Conflict alerts on save (`detect_schedule_conflicts`)

Saving a block raises a "Schedule conflict" alert only for blocks that air at the same time
on a shared day (`blocks_overlap` in `alert_service.py`). Days come from the recurrence
(weekly names, monthly day numbers, one-time date range; daily or no pattern = every day;
kinds that cannot be compared are assumed to meet). A block past midnight (22:00-02:00) is
two parts, the second on the next day. Blocks whose `start_date`..`end_date` ranges never
meet (moved a day on for the after-midnight part) do not conflict. Before 2026-11-17 days were ignored (false alerts
for Monday vs Tuesday shows) and overnight blocks never matched. Start-only blocks still
never conflict. Test: `backend/tests/test_block_conflict_alerts.py`.
