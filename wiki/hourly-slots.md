# Hourly and Half-Hour Slots

## How scheduling works

`queue_replenish_service.py` pre-schedules announcements 48 hours ahead using `preempt_at` timestamps on queue entries.

## Slot types

| Time | Type | Preempt | Source |
|------|------|---------|--------|
| :00 | Station ID + Time announcement + Weather | Hard (interrupts current track) | `queue_replenish_service.py:585-655` |
| :15 | Ad slot | Soft (waits for song to finish) | `queue_replenish_service.py:657-719` |
| :30 | Ad slot only | Soft | Same |
| :45 | Ad slot | Soft | Same |

## Station automation_config

```json
{
  "hourly_station_id": true,
  "hourly_time_announcement": true,
  "weather_enabled": true,
  "weather_interval_minutes": 30,
  "ad_slot_minutes": [15, 30, 45]
}
```

## FIXED 2026-06-10: announcement-masking bug + :30 weather

Two bugs fixed in `_schedule_hourly_announcements`:

1. **Masking (severe)**: the dedup set bucketed ALL pending preempts (including the 504 seven-day ad slots at :15/:30/:45) to the top of the hour — so once ads were scheduled, every hour looked "already scheduled" and time/weather/station-ID announcements silently stopped being created. Fix: dedup excludes `source == "ad_slot"` and compares exact minutes.
2. **:30 weather**: `weather_interval_minutes: 30` was ignored (weather only at :00). Fix: weather-only entry also scheduled at `hour + 30min` when interval == 30.

If announcements ever vanish again, check the dedup set in `_schedule_hourly_announcements` first.

## TTS generation

- Time + weather generated via ElevenLabs (`weather_spot_service.py`)
- Uploaded to Supabase as `weather/{slot}_time.mp3` and `weather/{slot}_weather.mp3`
- Stored as Assets (type: jingle/spot, category: time_announcement/weather_spot)

## Uploaded weather spots (Shprach Kol Berama voice) — added 2026-11-15

The weather spot is ONE asset per Eastern day (`asset_type="spot"`, `category="weather_spot"`, `metadata_extra.slot_key="YYYY-MM-DD"`). `get_or_create_weather_spot_assets` only calls ElevenLabs when that day has no such asset.

- `PUT /api/v1/weather-spots/{YYYY-MM-DD}` (manager auth, multipart: `file`, optional `script`, optional `source` default `shprach`) makes an uploaded recording that day's spot. `GET` on the same path shows what the day holds (404 if none). Code: `api/v1/weather_spots.py`, `store_uploaded_weather_spot()` in `weather_spot_service.py`.
- **It updates the existing asset in place** (same id, new `file_path`/`duration`, `metadata_extra` replaced). This matters: the scheduler pre-queues the day's asset up to 48h ahead, so an ElevenLabs spot usually already exists and is already in the queue. Creating a second asset would leave the queue playing the old one.
- Each upload gets a new storage path (`weather/{date}_weather_{source}_{HHMMSS}.mp3`) because the public Supabase URL is cached.
- Days with no upload keep the ElevenLabs fallback.
- **Time announcements**: each station announces its own time: slot keys come from `weather_spot_service.slot_key_for(at, announcement_zone(station.timezone))`; a station left at the default "UTC" uses New York (Jerusalem said New York's 1 AM at 8 AM before 2026-11-16). On each refill `_drop_wrong_hour_announcements` removes queued time checks whose clip hour is not the station's local hour (with the weather queued at the same time) so they are queued again. `PUT /api/v1/time-announcements/{hour}` (0-23 station hour, same multipart fields) stores ONE reusable clip per hour of the day (`category="time_announcement"`, `metadata_extra.time_of_day="15"`), played at that hour every day. `GET /api/v1/time-announcements` lists the hours that have one. An uploaded hour clip wins over the per-slot ElevenLabs clip; hours without one fall back to ElevenLabs. Entries queued before the first upload still point at the old per-slot ElevenLabs assets, so the switch takes up to 48h to show.
- ElevenLabs is now optional for the hourly scheduler: with no key, `get_or_create_weather_spot_assets` returns only what was uploaded (None for the rest) and never calls TTS or the forecast. The manual preview/force-insert endpoints in `api/v1/queue.py` (`_maybe_insert_weather_spot`, weather preview) still require an ElevenLabs key.
- FIXED 2026-11-15: the ElevenLabs time text used the generation time, not the slot's hour, so clips scheduled ahead all announced the hour they were made. It now speaks the slot's hour.
- The uploader lives on the voice PC, not in this repo: `C:\shprach\realness\kolberama\daily_station.ps1` (scheduled task "Kol Berama Daily Weather Spot") runs `weekly.py` then `station_upload.py`.

## Rate limits: weather cache and generation pause (2026-11-15)

The OpenWeatherMap key (free plan, 60 calls/min) was blocked on 2026-11-15. Cause: ElevenLabs had been refusing every request since July, so no announcement asset was ever saved; `_schedule_hourly_announcements` runs every few seconds and called `get_or_create_weather_spot_assets` for each of the 48 unfilled slots on every pass, and each call fetched the weather (2 API calls) before failing at TTS.

- `weather_service.get_current_weather` now caches per location for 30 min and remembers a failure (15 min, or 60 min for 401/429) instead of retrying.
- `get_or_create_weather_spot_assets` pauses all generation for 15 min after a failure; uploaded clips are still returned.
- Tests reset both through the autouse fixture in `tests/conftest.py`.

## Weather per region (2026-11-16)

Each station plays its own city's weather. `automation_config["weather_region"]` names the region (`"jerusalem"` for KBR Jerusalem); unset means the default region `lakewood`. Weather spot assets carry `metadata_extra.region`; spots without it (everything made before regions) belong to `lakewood`.

- Upload/lookup: `PUT|GET /api/v1/weather-spots/{date}?region=jerusalem` (no region = lakewood). A non-default region's spot is titled `Weather Report - {date} (Jerusalem)` and stored under `weather/{date}_jerusalem_weather_…`.
- `get_or_create_weather_spot_assets(db, slot_key, region)`: only the default region is ever generated (ElevenLabs); another region has weather only when its own spot was uploaded, so a station never airs another city's report. Every caller passes the station's region (`station_weather_region(config)` / `weather_region_for_station(db, id)`).
- `QueueReplenishService._realign_weather_region` runs with the hourly pass: queued weather of another region is moved to the station's own spot for that day, or removed when there is none.
- The Kol B'ramah Jerusalem spot comes from the voice PC like Lakewood's (`daily_station.ps1`), uploaded with `region=jerusalem`.

### Time clips per region

The 24 reusable hourly time clips follow the same regions. `PUT|GET /api/v1/time-announcements/{hour}?region=jerusalem` (no region = lakewood; clips without `metadata_extra.region` belong to lakewood). Unlike weather, a station falls back to the default set for an hour its own region has no clip (`find_time_clip_asset`), since the hour is local either way; `find_own_time_clip` is the exact lookup. `_realign_time_clips` runs with the hourly pass and moves queued uploaded time checks to the clip the station should play now, so a new Jerusalem upload takes over checks already queued.

## Hard vs soft preempts

- **Hard** (`source != "ad_slot"`): Interrupts current track immediately at `preempt_at` time
- **Soft** (`source == "ad_slot"`): Plays after current song finishes

See also: [streaming-architecture](streaming-architecture.md)

## Never random filler
Weather reports and time checks are made for one moment. `fit_for_random_pick()` (models/asset.py) keeps categories `weather_spot` and `time_announcement` out of every random draw (rule fill, gap fill, template slots, playlist generator) unless the category is named. Before 2026-11-15 a fill of "any spot" could air a weather report from months earlier.

## On the second only at :00
Automatic time and weather entries get `timing_mode` from `spot_schedule_service.default_timing` (station `automation_config.spot_timing`, default hard at :00 and soft otherwise). Null timing on anything but `ad_slot` still counts as hard (operator timed inserts rely on it), so `_schedule_hourly_announcements` also fills in timing for pending time/weather entries queued before 2026-11-16.

## A shiur is paused, not cut (2026-11-16)
On KBR Jerusalem the hard :00 time check cut a shiur 14 minutes in and the rest was lost. A hard timed item now treats a long item (asset type shiur/story/lecture/program, or 12 min or longer; `services/long_items.py`) by the station's `automation_config["timed_during_long_items"]`, set on Stations → Automation Config:

- `pause` (default): `_check_advance_locked` cuts as before, but first `_queue_resume` queues the same asset straight after the timed group (pending entries with the same `preempt_at`), with `source="resume@<seconds>"` (3 s before the stop point; not resumed with under 30 s left). The offset lives in `source` so the busy queue table needed no new column. `mark_started` sets `started_at = now - offset`, so every player's live position, `ends_at` and the queue times are right; `queue_entry_payload` gives the next item `cue_in = offset` so the local hand-over starts there; the playout engine's `build_uri` sets `liq_cue_in` the same way. Every place that starts an entry uses `mark_started`.
- `wait`: while the long item plays, hard items due before it ends (and soft ones with a `hard_by`) become plain soft, so they play after it and no control room fades it.
- `cut`: the old behaviour. Songs are always cut.

Also fixed then: the weather (same `preempt_at`, hard) used to cut the time check that had just started; `_same_timed_group` lets the group play in order. Due soft items with the same time now go by position (time check before weather).

The engine path (`playout_engine.py` timed commands) does not yet queue a resume itself; do that when the engine is switched on.

## The top-of-hour block plays as one (2026-11-16)

At 1 PM ET on Main a song played for 1.1 s between the time check and the weather, and
the time check played twice. Two causes:
- The weather is due at the same moment as the time check but is queued at the end of
  the queue. When the time check ended, the next pick went by position and started a
  song; the weather then cut it. Now any timed item whose time has come (hard or soft)
  is picked first, ordered by time then position (`_pick_next_pending` and its in-memory
  twin `_next_due`, which also gained the early-start step), so Up next shows the weather
  while the time check plays.
- Two refills raced and queued the 13:00 time check twice. When a timed item starts,
  `_drop_timed_twins` marks any pending copy (same item, same time) "skipped".
The control room marks timed items due at the same moment with a cyan bar and a
"BLOCK · N ITEMS" tag on the first. Test: `backend/tests/test_top_of_hour_block.py`.
