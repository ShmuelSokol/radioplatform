# Spot schedules (sponsor spots by daypart)

**Code**: `backend/app/services/spot_schedule_service.py`, `backend/app/api/v1/spot_schedules.py`, model `SpotSchedule`.

## What it does

A `SpotSchedule` row = "air this sponsor's asset N times in each chosen daypart, on these weekdays, between these dates, on this station (or all)". Dayparts are **station-local** hours: morning 6-12, afternoon 12-18, evening 18-24, overnight 0-6.

`plan_spots()` is a pure function that maps schedules onto the station's clock slots (`automation_config.ad_slot_minutes`, default :15/:30/:45 — see [hourly-slots](hourly-slots.md)). Plays are spread evenly through the daypart. Two spots wanting the same slot: the later-created schedule moves to the next free slot. Daypart full: reported as `unplaced`, never double-booked.

`reconcile_spot_slots()` makes the station's pending `source="ad_slot"` queue entries match the plan for the next 7 days (adds missing, deletes stale). It runs from `QueueReplenishService._schedule_ad_slots()` and right after any schedule create/update/delete.

## Things you would not guess from the code

- **Legacy fallback is keyed on "no schedule rows at all".** A station with zero spot schedules keeps the old behaviour: the single asset titled `automation_config.ad_slot_asset_title` (default "KOL BRAMAH TEST SPONSOR") in every slot. As soon as one schedule row exists (even inactive), only scheduled spots air. Deleting the last schedule brings the test spot back.
- **The plan must be time-independent.** It is computed per whole local day, then filtered to the future. If you plan only "slots from now", placements shift every run and the queue churns.
- **2-minute safety margin.** Reconcile never adds or deletes an entry within 2 minutes of air, so it cannot race the player.
- **Playback is untouched.** Entries are written exactly like the legacy ad slots (soft preempt via `preempt_at`), so the player/engine does not know the difference.
- **Sponsor portal matches plays by asset, not by `PlayLog.source`.** Nothing has ever written `PlaySource.AD`, so the old source filter always returned empty. A sponsor's assets = `Asset.sponsor_id`, the asset at `Sponsor.audio_file_path`, and assets used by their schedules. "Upcoming" reads real pending queue entries with `preempt_at`.
- Creating a schedule sets `Asset.sponsor_id` when it was empty.

## Not done yet

Blackout (Shabbos/Yom Tov) awareness, spacing rules between sponsors, and auto-resolve reporting are planned as the conflict look-ahead step.
