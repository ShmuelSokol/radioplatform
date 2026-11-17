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
soft = the song finishes first), `latest_minutes` (soft only: wait for the
song at most this long, then fade it; queued as `QueueEntry.hard_by`), `items` (JSON list, in order):

- `{"kind": "asset", "asset_id"}`: that recording every time
- `{"kind": "category", "asset_type", "category"?}`: takes turns, the one that
  has rested longest (so "spot / sponsorship" rotates the sponsors, "spot / psa" the PSAs)
- `{"kind": "time"}` / `{"kind": "weather"}`: the hour's time clip and the day's
  weather from `get_or_create_weather_spot_assets`

- `{"kind": "sponsors", "count": N}`: N sponsor places, filled by the spot
  planner (below)

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

### Unless the station keeps them (`blocks_keep_hourly`)

Station setting `automation_config.blocks_keep_hourly = true` (off by default): the
station's blocks play **beside** the built-in hourly time and weather (and the :30
weather). `replenish()` then queues the hourly items first and the blocks after, so a
block due on the hour plays right after the time and weather in the same group; the
reconciler leaves the hourly entries alone; and a block entry at :00 does not make
`_schedule_hourly_announcements` think the hour is done. Used for KBR's hourly schedule
announcement and the 8 PM Daf Yomi (2026-11-16). `GET /program-blocks/capabilities`
returns `{"keep_hourly": true}` so scripts can tell a server that has this.
Recordings meant only for blocks use category `block_only` (in `NEVER_RANDOM_CATEGORIES`:
never drawn as filler). Test: `tests/test_blocks_keep_hourly.py`.

## Sponsor places

When any active block has a `sponsors` item, `spot_schedule_service` stops
using the :15/:30/:45 clock slots for spread sponsorships. Each airing of such
a block is a `Break` with as many places as its `sponsors` items add up to.
A sponsorship that wants N plays in a daypart aims at the break its share of
the daypart points to and takes the first free place there, else the next break
with room; with no room left it is unplaced ("no sponsor place left in this time
of day's blocks", shown on Schedule Check). An exact-time sponsorship whose time
is a block's start takes a place in that block; other exact times keep their
own `ad_slot` entry.

Placements in blocks (`Placement.block` set) are not written by
`reconcile_spot_slots`: the block reconciler asks the spot plan which
sponsorships fill each airing and queues them in the block's order. So a
spot schedule edit rebuilds the block entries too (`_refresh_queues` in
`api/v1/spot_schedules.py`), and a block edit re-runs the spot reconcile first.

## Soft with a limit (`hard_by`)

`QueueEntry.hard_by`: a soft entry that is still waiting at this time is
treated as hard (the song on air fades). The in-browser path checks it in
`api/v1/queue.py` (preempt check). The playout engine honours it too (`_enforce_hard_by` in
`services/playout_engine.py`): past `hard_by` it fades the item on air once, as soon as the
engine has the block's first item pre-loaded, and never cuts into a block already on air.

## Preview

`GET /program-blocks/preview?station_id&hours` plans without preparing time or
weather audio (that can call the voice service), so run lengths leave those out.
