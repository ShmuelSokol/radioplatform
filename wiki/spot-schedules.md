# Spot schedules (sponsor spots by daypart)

**Code**: `backend/app/services/spot_schedule_service.py`, `backend/app/api/v1/spot_schedules.py`, model `SpotSchedule`.

## What it does

A `SpotSchedule` row = "air this sponsor's asset N times in each chosen daypart, on these weekdays, between these dates, on this station (or all)". Dayparts are **station-local** hours: morning 6-12, afternoon 12-18, evening 18-24, overnight 0-6.

**Time slots (2026-11-18, Shmuel: "a zone of a couple hours", not an exact minute).** A contract can also name three-hour slots: `early_morning` 6-9, `late_morning` 9-12, `early_afternoon` 12-15, `late_afternoon` 15-18, `early_evening` 18-21, `late_evening` 21-24 (`TIME_SLOTS`). `DAYPARTS` holds every name a contract may use (overnight, the slots, then the broad three) and the planner runs them in that order, narrowest first. `daypart_of(hour)` still returns the broad part (`BROAD_DAYPARTS`), so reports and the seat's `Placement.daypart` stay broad; an `Unplaced` row carries the contract's own name. New contracts in the form start on the six daytime slots; the broad parts are kept for older contracts. Frontend list and labels: `TIME_SLOTS`, `WIDE_DAYPARTS`, `daypartText()` in `frontend/src/api/spotSchedules.ts`.

**Leveling.** With program-block breaks, each play goes to the break of its window with the fewest sponsorships already seated (exact-time ones count), then one not already holding the same contract, then the break its share of the window points to (keeps a contract's own plays apart). So every break gets 3 before any gets a 4th, and the next contract lands in the break that has 2 (`tests/test_slot_leveling.py`). Before this a play aimed at its share point and took the first free place, which stacked 4 in some breaks and 0 in others. **Keeping one sponsor apart.** After the count, a break closer than `SPONSOR_GAP` (60 min, or the slot's even share when a contract has more plays than hours) to the same sponsor's other plays that day loses the tie (exact times count). Levelling comes first, with one exception: a break next to the same sponsor's own play (within `NEIGHBOUR`, 20 min) counts as holding one more, so a sponsor is never heard back to back while a far break is at most one fuller (Main 2026-11-18: Bright Smile at 7:00 and 7:15 PM).

**Last check before the queue.** `program_block_service._SponsorGuard` runs on every sponsorship a block occurrence is about to queue, rebuild or not: not inside a talk window, not within `NEIGHBOUR` (20 min) of the same spot queued, on air or played. Plans made a minute apart can disagree; on Main 2026-11-18 a non-rebuild run queued the 8:45 occurrence (empty until then) during the Daf's 8:30 pause and took Glatt Bite and Main Street Hardware, already queued at 9:00, so both aired twice and inside the shiur.

**One sponsor 20 minutes apart is a rule**, not a preference, in the planner too: a break within 20 minutes of the sponsor's own play is not used.

**Shiurim in blocks count before they are queued.** `program_block_service.block_talk_windows` reads each block's items (asset length, a category's longest recording, ~30 s per time/weather/sponsor place) and gives the talk windows of every occurrence, queued or not. The planner and `_SponsorGuard` add them to the queue's own windows. Without it a Daf block queued in the same pass as the sponsor breaks around it (a fresh fill, the daily Daf job adding the day's block) got sponsorships at :15, :30, :45 (simulator and a PC staged run on Jerusalem, 2026-11-19).

**Around a shiur.** `talk_windows` reads the shiurim and stories queued for set times (pending entries share their group's `preempt_at` and play in position order, so a talk starts after the time check, weather, places and intro before it; a playing one from `started_at`). Spread plays and today's make-ups never use a break inside one, hard or soft (`talk_tier` must be 0); a play with no other room in its slot is left out and warned about (Shmuel 2026-11-18: no sponsorships inside a shiur). A soft break in the last 5 minutes (`TALK_TAIL`) waits for the end, so it counts as outside. A shiur paused for a timed item waits as a `resume@` entry with no set time; `talk_windows` counts it from now for its remaining length plus `RESUME_WAIT`, and a resumed shiur on air only for what is left of it. Coordinator decision 2026-11-18 after Main's 8 PM Daf Yomi had sponsorships at 8:15, 8:30 and 8:45. A block rebuild (`reconcile_block_entries` with rebuild) deletes every pending block entry, the shiur too, before planning again, so it reads the talk windows first and hands them over in `TALKS_BEFORE_REBUILD`; without that a rebuild queued a sponsorship at 8:15 inside Main's Daf Yomi (2026-11-18, queue entry 5185) while the preview showed none.

**Edits mid-day (`settle_today`).** The plan is the same whatever the time, so after a contract or block change mid-day it no longer matches the breaks already over (seen 2026-11-18: Hatzalah moved from 5:45 PM to a 3 PM break when places were added at :00 and :15; review thread found a raised contract re-planning others that had aired, so they would air twice). `plan_for_station` reads today's play log (`aired_today`) and, per contract and slot, keeps promised minus aired on the coming breaks: extra coming plays are dropped, plays on gone breaks move to the emptiest coming break of the same slot. A slot that is over is not made up later (a contract made at 5 PM does not get its morning plays in the evening). Only block places are settled; clock-slot plans are unchanged. Tests: `tests/test_make_up_today.py`, `tests/test_slot_leveling_midday_edit.py`.

Without breaks (clock slots hold one each) a slot taken by a narrower window earlier that day is not reused (`day_taken`).

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


## Hours with no sponsorships (2026-11-22)

Main follows 107.9: no sponsorships overnight, and none in the last minutes before Shabbos. Two station settings, both on Break settings (Sponsorship board, All contracts) and in `PUT /spot-schedules/timing-defaults`:

- `automation_config.sponsor_quiet_hours` `{"start": "00:35", "end": "06:00"}`: every night; a start after the end runs past midnight.
- `automation_config.sponsors_stop_before_blackout_minutes` (e.g. 45): the minutes before each Shabbos or Yom Tov blackout.

`sponsor_rest_windows` turns them into `Blackout(..., sponsors_only=True)` and `load_sponsor_blackouts` adds them to the real blackouts for the planner (`plan_for_station`), the board (`sponsor_room`) and missed plays (`sponsor_delivery`). Nothing else (time checks, blocks, songs) sees them, so the station stays on the air; a block's sponsor places there simply stay empty. Plays that fall there are reported like Shabbos ones (`blackout` set, "no sponsorships then (quiet hours)"), not as missed. Tests: `tests/test_main_follows_1079.py`.

A soft break waits for the song on air, so a break planned just before the quiet hours could air inside them behind a long song (Main 2026-11-23: the 12:30 AM sponsorships aired at 12:33:40, quiet hours from 12:35). `cap_sponsor_waits` (run at the end of both reconcilers, so on every top-up and edit) gives every waiting soft group that holds a sponsorship `hard_by` at the start of the next stretch with no sponsorships (quiet hours, minutes before Shabbos, Shabbos itself) when it begins within 30 min (`SPONSOR_WAIT_CAP`) of the break's time; an earlier `hard_by` stays. Both players then fade the song there, as for any soft block with a limit. Test: `tests/test_carlebach_half_hour.py`.

## Exact times and hard / soft timing

A `SpotSchedule` with `fixed_times` ("07:00,13:30", station time) airs at those clock times on its days instead of being spread over dayparts. Exact times are planned first; spread plays skip a slot an exact time already holds, and two schedules asking for the same minute leave the later one unplaced.

Every placement carries `timing_mode`, written to `QueueEntry.timing_mode` next to `preempt_at` (the playout engine and web player read it; see `wiki/operator-actions.md`): `hard` = the item on air fades and the spot starts on the second, `soft` = the item on air finishes first. The schedule's own `timing_mode` wins; otherwise the station default applies: `automation_config.spot_timing = {"on_the_hour": "hard", "other": "soft"}` (edit via `GET/PUT /spot-schedules/timing-defaults`, on the Spot Schedule page). Changing either re-stamps entries already queued, in place.

Deleting a library item that a spot schedule or a program block uses is refused with 409 naming them (`asset_service.delete_asset`, 2026-11-17); before, the schedule vanished by CASCADE and the block item pointed at nothing. Queued entries and block playlist lines still go with the item.

## Contracts hold (2026-11-17)

- **Contract audio airs only through its contract.** Any asset a `SpotSchedule` uses, or tied to a sponsor (`Asset.sponsor_id`: set when a schedule first uses it or a spot recording is approved, and kept when a contract moves to other audio), is excluded from every rotation, playlist, category or block pool (`models/asset.not_contract_audio()`, part of `fit_for_random_pick()` and added to every `in_category` pick). Before this a rule like "a spot every 3 songs" or a block naming category Spots drew sponsor audio at random, outside its dates and caps. An operator can still queue it by hand. The sponsor tie was added 2026-11-18: 30 stand-in recordings swapped out of their contracts for the real voice were drawn as filler minutes later on all three stations, and two aired on Jerusalem.
- **A waiting slot never airs after the last day.** A soft slot can wait behind a long shiur, or a station can be stopped, and nothing expired overdue `ad_slot` rows: a 23:45 slot of a contract ending today aired at 00:05 the next day. `lapsed_contract_assets()` = contract audio with no active schedule covering *today in station time* on this station. The engine's `pick_next` leaves those out (read-only), the browser path and `reconcile_spot_slots` mark them `skipped`. Lateness within a covered day is still allowed.
- Tests: `tests/test_sponsorship_contracts.py` (end today/tomorrow in Jerusalem and New York time, start dates, one-day contracts, weekdays, exact caps per daypart, overbooking, Shabbos, edit/cancel/delete on the queue, lapsed slot on both players, operator override, rotations).

## Missed plays in the sponsor portal

Shmuel 2026-11-17: sponsors should see what they missed. `services/sponsor_delivery.py`
(`GET /sponsor-portal/missed-plays`, Dashboard "Missed Plays"):

- **Past (14 days)**: per active sponsorship, station day and time of day, the plays
  promised (plays per time of day, or one per exact time) against play-log rows of
  its recording on that station. A play counts once; one starting up to 15 min after
  its time of day ended still counts for it (a soft play waited for a song).
- Not promised, so never "missed": a time of day touched by a blackout (Shabbos,
  Yom Tov), one still running, one that began before the contract was entered, and
  anything of a cancelled (inactive) contract.
- **Coming (7 days)**: the planner's unplaced plays ("No room left") and plays it
  rolled to a later block the same day (`Placement.moved_from`).
- Nothing is stored: it is worked out on each read, so editing a contract changes
  what its past days were "promised". No make-good is planned automatically.
- Tests: `tests/test_sponsor_missed_plays.py`, `frontend/e2e/sponsor-missed-plays.mjs`.

## A recording that may not air is not planned (2026-11-18)

`plan_for_station` leaves out every schedule whose recording no automatic pick may use
(`Asset` `not_placeholder()`: category do_not_play, a placeholder title, rejected in
review), through `airable_schedules`. Before, the planner made its slots and the queue
refill's `_purge_placeholders` deleted them again on every queue read: "removed 19
placeholder entries" then "Spot slots reconciled +19 -0 (kept 0)", about 12 queue rows
a second for ever (review thread's walk-through; do-not-play is one tap in the control
room since PR 251). Spots mode off alone did not cause it (the plan is empty). The
sponsor's Missed Plays still lists the plays that did not air. Taking the mark off plans
it again on the next refill. Test: `backend/tests/test_spot_slot_churn.py`.

Tests and the clock: a slot due within the 2-minute safety margin is left alone by every replan (edit, pause, Spots off), so it still airs. Tests that count slots therefore depend on the minute they run (a run at hh:13 to hh:15 saw the hh:15 slot stay); `test_spot_schedules.py` and `test_spot_slot_churn.py` pin "now" to minute 5 of the hour with the `clock_off_the_slots` fixture in `tests/conftest.py`. Use it for any new slot-counting test.

## Plays skipped by staff (2026-11-18)

The control room's Sponsor panel can skip one coming `ad_slot` play: the queue row becomes status `cancelled` and `reconcile_spot_slots` treats a cancelled row at that minute for that recording as kept, so the play is not planned again. Put back sets it to `pending`. See [control-room](control-room.md#sponsor-panel-tapping-a-sponsorship-spot-2026-11-18).


**Sponsorships wait for stories (per station).** `automation_config.sponsorships_wait_for_talks: true` (`long_items.SPONSORSHIPS_WAIT`): on that station a timed group made only of sponsorships (clock-slot `ad_slot` plays or contract audio) due while a shiur or story is on air is made soft and waits for its end, so it plays between stories; it never pauses or cuts one. Groups with a time check or weather are unchanged. Done in `queue._soft_items_pause_long` (both players). Meant for All Day Stories (coordinator 2026-11-19: hard :15/:30/:45 sponsorships cut into stories 25 times a day in the simulator); off elsewhere, where Shmuel's 2026-11-17 soft rule (pause with more than 5 min left) still holds. Simulator test: `test_sponsorships_wait_for_stories_on_all_day_stories`. A soft item that is due while a paused story waits to carry on (the hourly time check paused it) plays after the story resumes, not in the pause (`queue._resume_before_soft`; both players since PR 428, the engine's `pick_next` asks it too); the simulator caught a 10:45 sponsorship airing in the 11:00 time check's pause of a story, first on the browser path, then on the engine path, which the simulator drives. Two waiting breaks that carry the same recording would then play back to back (Bagel Nosh twice 21 s apart in the simulator, CI on PR 460): a due automatic sponsorship whose recording started on the station in the last 20 min is passed over (`spot_schedule_service.close_repeat_ids`; the engine's `pick_next` excludes it read-only, the browser pick and each spot reconcile mark it `skipped` via `skip_close_repeats`); make-up counts the play log, so the play is placed again later.
**Loose copies of contract audio.** `asset.not_contract_audio()` also leaves out any asset with the same title as a contract's audio (case ignored). The library held untied duplicates (category "Spots", no sponsor); the old interval rules "Sponsor Ad Every 30min" (Main, Jerusalem; asset_type spot, no category) drew them, so a sponsor aired minutes from its block play and spots contracted for another station aired on Main (2026-11-18).
**Contract totals (2026-11-19).** Shmuel asked to see clearly how many times a day a spot plays and for how long. `frontend/src/components/sponsors/contractTotals.ts` works out plays a day (slots × plays in each, or the set times), the days it plays between its dates, total plays and plays left from today, and the spot's length. It shows at the top of the Edit contract dialog (sticky, live as fields change, with × n on each ticked slot and a "Plays a day" picker that sets an equal share per slot), on the client page's contract rows and on Spot Schedule. These are the contract's promise; plays the planner leaves out (no room) show as warnings and in missed plays.
