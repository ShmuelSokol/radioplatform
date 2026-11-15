# Schedule look-ahead and conflict resolution

`GET /schedules/look-ahead?station_id&days` (manager) walks the next N days in 15-minute steps of **station time** and reports findings: `overlap`, `gap`, `off_air`, `spot_unplaced`. Page: `/admin/schedule-check` (`ScheduleCheck.tsx`). Service: `schedule_lookahead_service.py`.

## It uses the engine's own resolver

Both the playout engine (`SchedulingService.get_active_block_for_station`) and the look-ahead call `SchedulingService.rank_matching_blocks`. Do not write a second matcher: the page is only worth having if it shows what the engine will actually do.

## Blocks are station wall-clock time

Before 2026-11-15 the engine compared block `start_time`/`end_time` against the **UTC** time of day, while sun-relative times were resolved in station time. A "6 to 9 AM" block on a New York station ran 1 to 4 AM. `station_local()` now converts first. If existing blocks were entered in UTC to compensate, they need re-entering.

## Who wins an overlap (`block_rank`)

Schedule priority, then block priority, then the more specific recurrence (one-time > monthly > weekly > daily), then the shorter block, then the newer one. Before this, equal priorities fell to list order. An overlap settled only by the tie-break is flagged `needs_attention`; `POST /schedules/look-ahead/settle` records a manager's choice by raising the chosen block's priority above the others.

## Not flagged

Gaps (the automatic queue keeps playing) and off-air windows are listed but never `needs_attention`. Sponsor plays skipped for Shabbos are not findings; plays with no free slot are. See [spot-schedules](spot-schedules.md).

## Start-only blocks (`until_next`), the studio program's playlists

KBR Studio 1.82 schedules playlists by a start only ("6 am mix 01234 6:00", "blanks Candle+1", "motzei 72"); each runs until the next one starts. A `ScheduleBlock` with `until_next=true` works that way: `end_time` is ignored, `SchedulingService.running_playlist` picks the start-only block whose `last_start` (looking back up to 8 days, sun-relative starts resolved per day) is most recent. It joins the ranking as the base layer: a timed block over it wins a priority tie, and the look-ahead does not report that as an overlap.

Candle lighting is not its own sun event: use `sunset` with offset -18 (so "Candle+1" is -17), and "72 after Shabbos" is `sunset` +72 on Saturday. The block form has quick-start buttons for both. Sun times need the station's latitude and longitude; without them the code silently uses Jerusalem.
