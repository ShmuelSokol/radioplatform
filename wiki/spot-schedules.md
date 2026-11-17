# Spot schedules (sponsor spots by daypart)

**Code**: `backend/app/services/spot_schedule_service.py`, `backend/app/api/v1/spot_schedules.py`, model `SpotSchedule`.

## What it does

A `SpotSchedule` row = "air this sponsor's asset N times in each chosen daypart, on these weekdays, between these dates, on this station (or all)". Dayparts are **station-local** hours: morning 6-12, afternoon 12-18, evening 18-24, overnight 0-6.

`plan_spots()` is a pure function that maps schedules onto the station's clock slots (`automation_config.ad_slot_minutes`, default :15/:30/:45 — see [hourly-slots](hourly-slots.md)). Plays are spread evenly through the daypart. Two spots wanting the same slot: the later-created schedule moves to the next free slot. Daypart full: reported as `unplaced`, never double-booked. A spot due inside the 2-minute safety margin is still added once (so an exact time set a minute ahead airs) but never removed. `plan_for_station` drops unplaced plays whose part of the day is already over (`daypart_over`), and the preview and Schedule Check show one line per sponsorship, day, daypart and reason with a `count`.

`reconcile_spot_slots()` makes the station's pending `source="ad_slot"` queue entries match the plan for the next 7 days (adds missing, deletes stale). It runs from `QueueReplenishService._schedule_ad_slots()` and right after any schedule create/update/delete.

**Saves stay under 1 s (S-6).** `api/v1/spot_schedules._refresh_queues` refreshes up to `INLINE_MAX_STATIONS` (3) stations inside the request; more (a schedule for every station, 80+ stations took 2-6 s) are returned and handed to `refresh_in_background` after the commit (own session per station, 4 at a time). Deleting an every-station schedule commits it switched off, and the background job deletes the row once the stations have dropped its slots. Each refresh re-softens rebuilt block items when HS is off. The Databank end-date PATCH uses the same path, and with no `station_id` it is 400 unless every schedule of the spot is station-less. Tests: `tests/test_spot_save_speed_and_end_dates.py` (set `spot_schedules.session_factory` to the test session maker to run the background part).

## Things you would not guess from the code

- **Legacy fallback is keyed on "no schedule rows at all".** A station with zero spot schedules can play the single asset titled `automation_config.ad_slot_asset_title` (default "KOL BRAMAH TEST SPONSOR") in every :15/:30/:45 slot, but only if it sets `ad_slot_legacy_spot = true`. As soon as one schedule row exists (even inactive), only scheduled spots air.
- **The plan must be time-independent.** It is computed per whole local day, then filtered to the future. If you plan only "slots from now", placements shift every run and the queue churns.
- **2-minute safety margin.** Reconcile never adds or deletes an entry within 2 minutes of air, so it cannot race the player.
- **Playback is untouched.** Entries are written exactly like the legacy ad slots (soft preempt via `preempt_at`), so the player/engine does not know the difference.
- **Sponsor portal matches plays by asset, not by `PlayLog.source`.** Nothing has ever written `PlaySource.AD`, so the old source filter always returned empty. A sponsor's assets = `Asset.sponsor_id`, the asset at `Sponsor.audio_file_path`, and assets used by their schedules. "Upcoming" reads real pending queue entries with `preempt_at`.
- Creating a schedule sets `Asset.sponsor_id` when it was empty.

## Not done yet

Spacing rules between sponsors are still to come.

## Edge rules (added after review, 2026-11-15)

- **Legacy test spot is off by default** (since 2026-11-15; it was airing on the live stations at night). `automation_config.ad_slot_legacy_spot = true` turns it on; otherwise each queue refill removes pending placeholder slots.
- **Clock change nights**: wall-clock times that do not exist on a spring-forward night (02:xx) are not slots. Before this they collided with 03:xx in UTC and were dropped silently.
- **Validation**: `start_date` after `end_date` is 422 (create and update, including against the stored other date). Explicit `null` for `asset_id`, `days_of_week`, `dayparts`, `plays_per_daypart`, `is_active` on PUT is 422; `name`, `station_id`, `start_date`, `end_date` may be cleared with null.
- **Audio ownership**: a schedule can only use audio that is unowned or already belongs to the same sponsor (422 otherwise).
- **Preview** lists only times still ahead.

## Shabbos and Yom Tov (blackouts)

The planner reads `HolidayWindow` rows with `is_blackout` for the station (`load_blackouts`). A slot inside a blackout is not a slot: plays in a partly covered daypart are spread over the slots that remain (Friday afternoon before candle lighting, Saturday after it ends); a daypart that is entirely off the air is skipped. Skipped plays come back as `Unplaced` with `blackout` set, and the preview reports them under `off_air` (per window, with a count) instead of `unplaced`, because they are intended, not a problem for a manager to fix. `reconcile_spot_slots` uses the same plan, so queued slots inside a new or changed window are removed on the next refill.

## Exact times and hard / soft timing

A `SpotSchedule` with `fixed_times` ("07:00,13:30", station time) airs at those clock times on its days instead of being spread over dayparts. Exact times are planned first; spread plays skip a slot an exact time already holds, and two schedules asking for the same minute leave the later one unplaced.

Every placement carries `timing_mode`, written to `QueueEntry.timing_mode` next to `preempt_at` (the playout engine and web player read it; see `wiki/operator-actions.md`): `hard` = the item on air fades and the spot starts on the second, `soft` = the item on air finishes first. The schedule's own `timing_mode` wins; otherwise the station default applies: `automation_config.spot_timing = {"on_the_hour": "hard", "other": "soft"}` (edit via `GET/PUT /spot-schedules/timing-defaults`, on the Spot Schedule page). Changing either re-stamps entries already queued, in place.
