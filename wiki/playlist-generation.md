# Playlist generation (rotation by song type)

Models the station's legacy program (KBR Studio "Automatic playlist generation"): a playlist is a repeating sequence of song types, started by a schedule rule, generated N hours ahead.

Mapping, no new tables:
- song type = `Asset.category` (with `Asset.asset_type`)
- playlist sequence = `PlaylistTemplate` + ordered `TemplateSlot`s
- start rule = the `ScheduleBlock` that names the template

`backend/app/services/playlist_generator_service.py::generate` walks forward from the end of the queue. For each moment it asks `SchedulingService.rank_matching_blocks` which block is on, takes the next slot in that block's template (cursor per block, saved in `station.automation_config.playlist_generation.cursor`) and draws a song:

1. Artist rule first: no artist within `artist_distance` songs. If nothing is clear, the rule gives way and the pick is flagged `artist_rule_relaxed`.
2. Longest-rested first (last `PlayLog.start_utc` on this station; never aired = most rested). Songs already waiting in the queue sort last.
3. Random among the most-rested third, but only songs that have rested at least half as long as the most rested one. Without that limit a song aired an hour ago was drawn ahead of one never aired.

Blackouts (Shabbos, Yom Tov) generate nothing and are reported in `Plan.notes`. Time with no block plays any music (pick `block_name` "No block"), so the station never goes quiet between blocks.

`automation_config.playlist_generation = {enabled, hours, artist_distance}`; when on, `QueueReplenishService` fills from it instead of the rule-based fill. It is also on, whatever the switch says, for any station whose active schedule has a block with an active playlist (`schedule_refresh_service.uses_block_playlists`); the first refill then throws away a week of old rule-based fill (more than 2 x `hours` of automatic songs).

## Schedule changes take over on time

Before 2026-11-16 the rule-based fill queued about 7 days ahead, so a new block waited behind ~1,450 old songs. Now every change to a schedule, block, playlist or playlist step calls `schedule_refresh_service.refresh_station`: pending `source="auto"` songs with no `preempt_at` are deleted and the queue is generated again from now. Songs a person queued, timed items and program-block entries stay. Only for stations that fill from blocks now or did before the change (`had_playlists`). The refresh calls `db.expire_all()` first: the route session just made the change and its cached playlist steps were stale (a deleted step was still drawn). Test: `tests/test_schedule_refresh.py`.

Generation starts where the queue really ends on the air (`QueueReplenishService._air_timeline`): the song on the air until its crossfade point, each waiting item for `on_air_seconds` (duration minus the crossfade, the same rule as `queue.switch_time`, default 3 s), and timed items where they fall (`Clock`: a hard one cuts the item on air at its time, a soft one goes on after it). The generator walks the same `Clock`, so each pick's expected start, and so its block, allows for crossfades, time checks and sponsor blocks. Counting full lengths put a block's first song early by about 3 s per song queued before it (live tests: 1.5 min, then 39 s early).

Even so, songs drawn hours ahead drift. `schedule_refresh_service.realign_before_block_change` runs on every refill (each song change): once per change, 15 minutes before the block-with-a-playlist on the air changes, it throws the automatic songs away and draws them again from the real time on air. `QueueEntry.block_id` records the block each automatic song was drawn for; `remove_automatic_songs` moves that block's cursor back by the songs removed, so the playlist carries on from the right step (used by schedule changes, regenerate and the redraw). A start-only ("runs until the next playlist starts") block counts only starts after its `created_at` (`SchedulingService.last_start`): before 2026-11-16 a block saved at 6:40 to start at 6:52 was "on" at once, because yesterday's 6:52 was in the past, and its playlist aired immediately. New queue entries are always placed after the item on air (position counters include `status="playing"`); the refill used to give the new first song the same position as the song on air. Test: `tests/test_block_waits_for_its_time.py`. Rule: an item belongs to the block on at its expected start, so the item that straddles a block's start comes from the previous block or filler. Test: `tests/test_block_on_time.py`.

API: `GET/PUT /playlists/generation`, `POST /playlists/generation/regenerate`.

Minimum vote: `Asset.vote` is a staff-set whole number (0 = unrated), edited on the song's page and shown in the library list beside "Last played". `playlist_generation.min_vote` (0 = off) keeps songs under that vote out of every draw. A type where NO song reaches the minimum (jingles, usually unrated) is drawn as if there were no minimum, and the preview says so; otherwise a minimum would silence the type. Listener ratings (`SongRating`) are a separate thing and do not feed this.

Not built yet: jingle groups with counts, seasonal and parsha types switched by the calendar, "reset sacks".

Test note: the draw is random, so `tests/test_playlist_generation.py` seeds `random`; in its tiny library some orders legitimately reuse a queued song to keep the artist rule.

## Items no automatic pick may use

`fit_for_random_pick()` (models/asset.py) skips categories do_not_play/test (any case) and one-moment clips. Placeholders listed by title in `NEVER_AUTO_TITLES` (the old "KOL BRAMAH TEST SPONSOR", category "Spots") are skipped by **every** automatic pick through `not_placeholder()`, including rotations, rules and program blocks that name the category. Each refill (and each restart) deletes pending copies an automatic pick queued; operator, request and loop entries stay. Test: `tests/test_placeholder_never_auto.py`.
