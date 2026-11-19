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
- No `OPENWEATHERMAP_API_KEY` (2026-11-17): `get_current_weather` raises `WeatherNotConfiguredError` without a request, weather is not even attempted by `get_or_create_weather_spot_assets` (a failed attempt used to pause the time announcements too), and the weather readout pages answer 503 with the reason. Tests that fetch weather must set the key.
- Daily weather readout (`SchedulerEngine._maybe_generate_weather_readouts`): the day counts as done only once a readout for that date exists. A failed attempt (weather down) is retried the same day: next pass, then 1, 2, 4 ... minutes, at most 30 (`READOUT_RETRY_MAX_SECONDS`, in memory per process). Auto-queue (`_maybe_queue_weather_readouts`) queues a readout only on its own `readout_date` in the station's zone, so tomorrow's readout recorded tonight waits. Tests: `backend/tests/test_weather_without_key_and_readouts.py`.

## Weather per region (2026-11-16)

Each station plays its own city's weather. `automation_config["weather_region"]` names the region (`"jerusalem"` for KBR Jerusalem); unset means the default region `lakewood`. Weather spot assets carry `metadata_extra.region`; spots without it (everything made before regions) belong to `lakewood`.

- Upload/lookup: `PUT|GET /api/v1/weather-spots/{date}?region=jerusalem` (no region = lakewood). A non-default region's spot is titled `Weather Report - {date} (Jerusalem)` and stored under `weather/{date}_jerusalem_weather_…`.
- `get_or_create_weather_spot_assets(db, slot_key, region)`: only the default region is ever generated (ElevenLabs); another region has weather only when its own spot was uploaded, so a station never airs another city's report. Every caller passes the station's region (`station_weather_region(config)` / `weather_region_for_station(db, id)`).
- `QueueReplenishService._realign_weather_region` runs with the hourly pass: queued weather of another region is moved to the station's own spot for that day, or removed when there is none.
- The Kol B'ramah Jerusalem spot comes from the voice PC like Lakewood's (`daily_station.ps1`), uploaded with `region=jerusalem`.

### Time clips per region

The 24 reusable hourly time clips follow the same regions. `PUT|GET /api/v1/time-announcements/{hour}?region=jerusalem` (no region = lakewood; clips without `metadata_extra.region` belong to lakewood). Since 2026-11-19 (V1-120) a station never borrows another region's clip: the Lakewood set says "one oh seven nine, Lakewood". For an hour its own region has no clip it gets a generated one (ElevenLabs, "The time is 3 PM on Radio Kohl Baramah.", no call sign; cached per slot and region) or none. Only the default region's generated text names W M D I 107.9. Queued checks that already hold a Lakewood clip stay until `_realign_time_clips` finds the region's own. `_realign_time_clips` runs with the hourly pass and moves queued uploaded time checks to the clip the station should play now, so a new Jerusalem upload takes over checks already queued.

## Hard vs soft preempts

- **Hard** (`source != "ad_slot"`): Interrupts current track immediately at `preempt_at` time
- **Soft** (`source == "ad_slot"`): Plays after current song finishes

See also: [streaming-architecture](streaming-architecture.md)

## Never random filler
Weather reports and time checks are made for one moment. `fit_for_random_pick()` (models/asset.py) keeps categories `weather_spot` and `time_announcement` out of every random draw (rule fill, gap fill, template slots, playlist generator) unless the category is named. Before 2026-11-15 a fill of "any spot" could air a weather report from months earlier.

## On the second at :00, and at :30 when the station says so
Automatic time and weather entries get `timing_mode` from `spot_schedule_service.default_timing` (station `automation_config.spot_timing` = `{on_the_hour, half_hour, other}`, default hard at :00 and soft otherwise; `half_hour` falls back to `other` until a station chooses it). Spread sponsorships in a :30 clock slot follow the same choice; a program block keeps its own hard/soft.

Half hour (Shmuel 2026-11-18: "it should be exactly on the half hour ... so people know to come to hear the weather on the half hour"; :15/:45 stay after the song): Spot Schedules page → "On the half hour they play" (`spot_timing.half_hour`) and the checkbox "Time check on the half hour" (`automation_config.half_hour_time_check`, off by default). With the check on, `_schedule_hourly_announcements` queues the region's uploaded half-hour clip (`PUT /time-announcements/{hour}?minute=30`; metadata `time_minute: "30"`, title "Time Announcement - 10:30") at local hh:30, then the :30 weather. Uploaded clips only, never ElevenLabs: no clip for an hour, the weather plays alone. Clips without `time_minute` are hh:00 clips (`clip_minute`). Entries due at one minute play by position, and a block or sponsorship queued for :30 earlier has a lower position, so `_put_first` hands the group's positions out again with the new time check (and weather) first; the same runs for each new hh:00 group. Saving the timing choice re-times the time and weather already queued (`_retime_announcements`, not while HS is off). `_drop_wrong_hour_announcements` also replaces a clip queued at the wrong minute. The hour that has already begun still gets its :30: only coming hours were walked and a :30 is made with its hour, so after a blackout longer than the 48 h queued ahead (a two-day Yom Tov; nothing is filled during a blackout) or a start with an empty queue, a station back at 9:01 PM had no 9:30 time check or weather (station simulator, 2026-11-19). Test: `tests/test_half_hour_timing.py`. Null timing on anything but `ad_slot` still counts as hard (operator timed inserts rely on it), so `_schedule_hourly_announcements` also fills in timing for pending time/weather entries queued before 2026-11-16.

## A shiur is paused, not cut (2026-11-16)
On KBR Jerusalem the hard :00 time check cut a shiur 14 minutes in and the rest was lost. A hard timed item now treats a long item (asset type shiur/story/lecture/program, or 12 min or longer; `services/long_items.py`) by the station's `automation_config["timed_during_long_items"]`, set on Stations → Automation Config:

- `pause` (default): `_check_advance_locked` cuts as before, but first `_queue_resume` queues the same asset straight after the timed group (pending entries with the same `preempt_at`), with `source="resume@<seconds>"` (3 s before the stop point; not resumed with under 30 s left). The offset lives in `source` so the busy queue table needed no new column. `mark_started` sets `started_at = now - offset`, so every player's live position, `ends_at` and the queue times are right; `queue_entry_payload` gives the next item `cue_in = offset` so the local hand-over starts there; the playout engine's `build_uri` sets `liq_cue_in` the same way. Every place that starts an entry uses `mark_started`.
- `wait`: while the long item plays, hard items due before it ends (and soft ones with a `hard_by`) become plain soft, so they play after it and no control room fades it.
- `cut`: the old behaviour. Songs are always cut.

Also fixed then: the weather (same `preempt_at`, hard) used to cut the time check that had just started; `_same_timed_group` lets the group play in order. Due soft items with the same time now go by position (time check before weather).

Soft timed items (since 2026-11-17, Shmuel's rule): a soft group (:15/:30/:45 weather, sponsorships, soft blocks, with or without a limit) due while a shiur or story has more than `long_items.SOFT_PAUSE_MIN_LEFT_SECONDS` (5 min) left is made hard (`timing_mode="hard"`, `hard_by=None`) by `queue._soft_items_pause_long`, so the rule above applies (pause, or cut on a "cut" station); with 5 min or less left it keeps waiting. A "wait" station, and HS off, leave soft items alone. The browser player runs it in `_check_advance_locked` for items due within `SOFT_PAUSE_LOOKAHEAD_SECONDS` (90 s; time left measured at the due time), so they turn hard ahead and the control room pre-loads them and fades on time. Both this change and the "wait" station's softening (`_defer_hard_until_end`) are committed at once: the scheduler's 3 s poll rolls its session back after a check that changes nothing on air, so until 2026-11-17 the flip only lived inside that poll (the exact timer still saw it; pages never did) (done only at the due time, the live weather started 2.3 s late, cold, and its last seconds played under the resumed shiur); the engine runs it in `tick` (`playout_engine._soft_items_during_long`) for items due within the 45 s hand-over window, before `_apply_long_item_rule`. `_queue_resume` now puts the resume first after the group, ahead of every other pending item (before, a song queued at a lower position than the timed item played before the shiur carried on).

On an engine station (since 2026-11-17) `playout_engine._apply_long_item_rule` applies the same rule in `tick`, when the next hard group is about to be handed over (45 s early): `pause` queues the resume entry at the head of the music lane (the engine re-asks for its pre-loaded track because the queue head changed), `wait` turns the group soft via `_defer_hard_until_end` and hands nothing (but when the shiur itself is in the timed lane, part of an earlier hard block, the groups stay hard and move to the moment it ends, `_airs_until`: a soft group would wait for the music lane, 34 s late in the harness), `cut` does nothing extra. In `radio.liq`, a group that goes on air while an earlier timed item is still on air (a block's Daf Yomi, a Play Now) now skips that item; before, the new group sat in the same timed queue behind it (the 21:00 time check aired at 21:09 after the 20:00 block's Daf Yomi, 2026-11-16). A `radio.liq` change reaches the server only when `setup.sh` is run again there.

## A group never cuts the group before it (2026-11-17, V1-10b)

Two hard groups whose times are closer than the first group's length (a 6 s time check at hh:00:00, an announcement at hh:00:01) used to cut the first after 1 s on every player. `services/timed_groups.space_hard_groups` moves the later group to the moment the earlier one ends, ahead of time: in `queue._check_advance_locked` (90 s ahead, so pages pre-load the new time) and in the engine tick before hand-over. The engine is handed the moved group at the first group's time (`EngineState.follows`), so radio.liq treats it as the same take-over and plays it after it from the timed queue; handed at its own time, its take-over would `timed_queue.skip()` the first one. Not applied behind a shiur (the long-item rule decides), never for silence, never more than 2 min, never for entries already handed to the engine. Tests: `backend/tests/test_timed_groups_follow.py`, harness `two_groups_1s_apart`.

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


## Blackouts and the station's own clock (2026-11-17)

- Hourly time and weather are not queued inside a blackout (Shabbos, Yom Tov):
  `_schedule_hourly_announcements` skips hours covered by `load_blackouts`. During a
  blackout the browser-player advance starts only silence (`_pick_next_pending(...,
  silence_only=True)`, and a due timed item that is not silence does not preempt). Before,
  a time check queued on Friday came due on Shabbos morning and cut the silence. The
  engine path (`playout_engine.tick`/`pick_next`) has not been checked for the same.
- Hour marks come from the station's own clock (`local_hour_marks`): a half-hour zone
  (India) heard its time check at local :30, and the default hard/soft timing used the UTC
  minute. On the autumn DST day 1 AM gets two checks, in spring 2 AM none.
- Moving a station (new latitude/longitude in `PUT /stations/{id}`): `update_station` drops
  the Shabbos/Yom Tov windows generated for the old place (`drop_generated_holidays_for_location`:
  covering this station only, name and start matching what the generator makes for the old
  place) and generates the new place's at once. Hand-made windows, windows shared with
  other stations and a window in progress are kept. There is no "generated" flag on
  `HolidayWindow`; the match against the generator is how they are told apart.
  Test: `backend/tests/test_holiday_generation_endpoints.py::test_moving_a_station_moves_its_shabbos`.
- Tests: `backend/tests/test_scheduler_jobs.py`, `backend/tests/test_restart_holidays_timezones.py`.


## Rotation and interval rules in the rule-based fill (2026-11-17)

Stations without playlist generation fill 7 days from `ScheduleRule`s. After the daypart
fill, `_spread_rule_items` weaves rule items between the fill's songs and renumbers the
fill's positions: rotation (`songs_between=N`) puts one item after every N filled songs,
interval (`interval_minutes=N`) one each time N minutes of estimated air time pass. Both
only where the rule's `hour_start`/`hour_end` (a range like 22-2 wraps) and
`days_of_week` (0=Mon) cover the estimated station-local air time. Before, rotation items
went after the whole week, back to back, and interval rules were never applied. Legacy
global sponsors still go at the end of the fill (unchanged). Test:
`backend/tests/test_rule_fill_and_station_delete.py`.

## Songs fitted to the time before the hour (2026-11-17)

A hard item fades whatever is on air, and the fill never looked at the clock: on KBR Jerusalem a 5-minute song started at 20:58:59 and the 21:00 time check cut it after 61 s. `services/hour_fit.py` (`fit_before_hard_item`, called for every station in each scheduler pass, at most every 10 s, under the station's advance lock) now back-times the run-up:

- Within 20 min of the next pending hard item it plays the queue forward (item on air, soft timed items that come due, songs by position) and finds the song that would be on air at the hard item's time. If that song loses more than 15 s, it looks for a better last song, or a better last two (one long song or a pair), among the next 60 untimed entries, and applies the best one when it saves at least 20 s, or at least 5 s when that brings the loss within 15 s (a song losing 19 s could never be saved 20 s, so the 133 s song before Main's 10:30 time check on 2026-11-19 lost 19 s).
- It only **swaps `position` values**. Nothing is added or removed, so it cannot cause silence, and the pick rules (`_pick_next_pending`, `playout_engine.pick_next`) are untouched: both players follow the new order because both pick by position.
- Moved: only `source="auto"` untimed songs of the same `block_id`. Never: an operator's pick or request, an entry the engine holds (`held_entry_ids`), a song starting within 30 s (pages already announced it), songs across a soft timed item, two songs by one artist as the pair.
- Off when HS (hard start) is off (nothing is hard then) or `automation_config.fit_before_hard_items` is false.
- What "a song's length" means (three live misses, 2026-11-17/18): the fit wants the song before the hard item to hand over at least 5 s after it (`SAFE_PAST`); ending a few seconds before it costs like a 40 s cut (`EARLY_COST`), for the current order as well as for candidates. On an engine station a song holds the air from its analysed start to its analysed end less the engine's 3 s cross (`cue_out - cue_in - 3`), not for its file length. Songs are analysed just before they air, so the fit asks for the analysis of the songs it depends on and waits for it (it goes ahead on file lengths only with under 90 s to spare). It logs "reordered" or, once per hard item, "queue left as it is".
- The overlap is the engine's own figure (live 2026-11-18 04:00Z, Main): the engine moved from a fixed 3 s overlap to each item's own (`kbr_overlap` / `liq_cross_duration`: the station's song crossfade, 1 s unless set) while the fit still took off 3 s. Every song aired 2 s longer than counted, the last song started 9.5 s later than planned, and "loses 5 s" became 17 s. `_seconds(entry, engine=True)` now takes the overlap from `playout_engine._overlap(entry)`, the function `build_uri` uses, so a change on the engine side cannot leave the fit behind. If you change how the engine overlaps or trims, run `tests/test_hour_fit.py`.
- The pre-loaded song (live 2026-11-18 04:00Z, KBR Jerusalem): the engine asks for its next song as soon as one starts, so during a long item the song before the hard item is pre-loaded (`cued_entry_id`) for the whole length of that item. The fit used to treat it as untouchable like the timed lane, so it was never fitted. Now it is exchanged like any other song until 75 s before it starts (`PRELOADED_FROZEN`); the next engine tick sees a different queue head and flushes the pre-loaded copy (`_flush_if_head_changed`, refused in the last 45 s of the song on air, at most once per 15 s), and the engine asks again. Items in the engine's timed lane (`timed_sent`) never move. The "queue left as it is" log line also says how many songs were not measured yet, and is said again once measured songs appear.
- Limits: it can only pick from what is queued, so with the item on air ending under a minute before the hour (too late to re-plan the song before) a song still starts and is faded; the looks start 20 min ahead to avoid that. No short filler or station-ID jingles exist yet to fill a small gap.
- Steadier since the first live hour (Jerusalem 21:47Z fitted twice in ten seconds: "loses 0 s", then "219 s" again): each song is counted up to its own hand-over point (`queue.handover_seconds`, 3 to 12 s before the file ends here, not a flat 3 s), and a fit that ends a few seconds **before** the hard item counts as worse than one that runs a few seconds past it, because one small drift at a song change would let another song start. A second "reordered" line can still follow a queue redraw (the 15-minute redraw before a block change, or a schedule edit); that is the fit doing its job on the new songs.

## A resumed item and the "never announce an older item" guard (2026-11-18)
A paused shiur or story comes back with `started_at = now - offset` (`mark_started`), which lies before the timed item that paused it. `broadcast_now_playing_update` drops anything that started before the latest announcement, so every update for the resumed item was dropped until the next item (All Day Stories 02:30Z to 02:55Z: 353 "Dropped stale now_playing" lines after a soft sponsorship paused a story; open pages got no now-playing update for 25 minutes). The payload now carries `on_air_at` (`started_at + resume offset`, the moment it really went on air) and the guard orders by that. Anything else that orders items by `started_at` must do the same. The play log does since 2026-11-18: every `PlayLog` row is written from `queue_entry.aired_from(entry)`; before, the resumed part's row began inside the row of the first part (Jerusalem 06:00Z).
