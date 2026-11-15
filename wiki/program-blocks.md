# Program blocks

A program block is one named event that plays a run of items in order at set
clock times. It is the studio program's "Weather Sponsors" line: station ID,
weather, a PSA, a sponsor spot, a programme. Page: `/admin/program-blocks`.
Not to be confused with a *schedule block* (`ScheduleBlock`), which says which
playlist runs during a stretch of the day.

## Shape

`ProgramBlock` (`program_blocks`): `station_id`, `name`, `is_active`,
`days_of_week` ("0,1,..", 0 = Monday), `times` ("HH:MM" CSV, station time),
`timing_mode` (hard = the song on air fades and the block starts on the second,
soft = the song finishes first), `items` (JSON list, in order):

- `{"kind": "asset", "asset_id"}`: that recording every time
- `{"kind": "category", "asset_type", "category"?}`: takes turns, the one that
  has rested longest (so "spot / sponsorship" rotates the sponsors, "spot / psa" the PSAs)
- `{"kind": "time"}` / `{"kind": "weather"}`: the hour's time clip and the day's
  weather from `get_or_create_weather_spot_assets`

Items may carry a `label` (the page stores the recording's title there).

## How it reaches the air

`program_block_service.reconcile_block_entries(db, station_id)` runs at the top
of every queue refill (`QueueReplenishService.replenish`) and after every edit
(`rebuild=True`, which drops the pending entries first). For each occurrence in
the next 48 hours that is not queued yet it adds one `QueueEntry` per item with
`source="block"`, the same `preempt_at` (the occurrence's minute), the block's
`timing_mode`, and rising `position`. That is the group convention the hourly
time and weather always used, so playout needs nothing new.

- Occurrences inside a Shabbos or Yom Tov blackout are not queued.
- Nothing closer than 2 minutes (`SAFETY_MARGIN`) is added or removed.
- An item with nothing to play is left out; the rest of the block still plays.
  The Program Blocks preview and the Schedule Check page (`block_item` finding,
  once per block and item, not once per hour) both say so.
- Already queued occurrences are never reshuffled by the periodic run.

## It replaces the built-in hourly time and weather

Once a station has at least one active block, `replenish()` skips
`_schedule_hourly_announcements()` and the reconciler deletes the pending
entries that code queued (source "auto", no timing mode, category
`time_announcement` or `weather_spot`). A station with no blocks behaves as
before. So a station moving to blocks must put the time and weather in a block
or it loses them.

## Preview

`GET /program-blocks/preview?station_id&hours` plans without preparing time or
weather audio (that can call the voice service), so run lengths leave those out.
