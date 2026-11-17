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

Even so, songs drawn hours ahead drift. `schedule_refresh_service.realign_before_block_change` runs on every refill (each song change): once per change, 15 minutes before the block-with-a-playlist on the air changes, it throws the automatic songs away and draws them again from the real time on air. `QueueEntry.block_id` records the block each automatic song was drawn for; `remove_automatic_songs` moves that block's cursor back by the songs removed, so the playlist carries on from the right step (used by schedule changes, regenerate and the redraw). A start-only ("runs until the next playlist starts") block holds until another start-only block starts: daily and weekly ones look back 8 days, a monthly one back to its previous day of the month (up to ~2 months, worked out directly, not day by day), and a one-time one with a date range holds until the range ends (before 2026-11-17 every kind dropped off after 8 days and plain music played; test `tests/test_monthly_running_playlist.py`). It counts only starts after its `created_at` (`SchedulingService.last_start`): before 2026-11-16 a block saved at 6:40 to start at 6:52 was "on" at once, because yesterday's 6:52 was in the past, and its playlist aired immediately. New queue entries are always placed after the item on air (position counters include `status="playing"`); the refill used to give the new first song the same position as the song on air. Test: `tests/test_block_waits_for_its_time.py`. Rule: an item belongs to the block on at its expected start, so the item that straddles a block's start comes from the previous block or filler. Test: `tests/test_block_on_time.py`.

## Songs added straight to a block

A block's own songs ("+ Add song" on /admin/schedules/:id/blocks, `PlaylistEntry` rows) were stored and never read until 2026-11-17: `SchedulingService.get_next_asset_for_block` has no caller. Now `generate` loads them (`block_songs`: enabled entries only, through `not_placeholder()`, so a placeholder or do_not_play entry is never drawn) and, while such a block is on, draws from them in the block's `playback_mode`: **sequential** walks them in position order with the block's cursor (so the redraw and `remove_automatic_songs` carry on from the right song), **shuffle** picks at random but leaves out the most recently used half (queued or aired), **weighted** draws by `weight`. The artist rule does not apply inside a block's own list (a one-song block repeats that song). **A block that names a working playlist keeps playing the playlist**; its own songs only take over a block with no playlist (so a block on air with stray songs listed does not change when this shipped). A block whose songs are all unfit falls back to plain music, so no silence. `uses_block_playlists` and the 15-minute redraw count blocks with songs, and adding, changing or removing an entry (or `set-prerecorded`) calls `refresh_station`, so the change is on the queue at once. Test: `tests/test_block_songs_reach_queue.py`.

## Songs added straight to a block

A block's own songs ("+ Add song" on /admin/schedules/:id/blocks, `PlaylistEntry` rows) were stored and never read until 2026-11-17: `SchedulingService.get_next_asset_for_block` has no caller. Now `generate` loads them (`block_songs`: enabled entries only, through `not_placeholder()`, so a placeholder or do_not_play entry is never drawn) and, while such a block is on, draws from them in the block's `playback_mode`: **sequential** walks them in position order with the block's cursor (so the redraw and `remove_automatic_songs` carry on from the right song), **shuffle** picks at random but leaves out the most recently used half (queued or aired), **weighted** draws by `weight`. The artist rule does not apply inside a block's own list (a one-song block repeats that song). **A block that names a working playlist keeps playing the playlist**; its own songs only take over a block with no playlist (so a block on air with stray songs listed does not change when this shipped). A block whose songs are all unfit falls back to plain music, so no silence. `uses_block_playlists` and the 15-minute redraw count blocks with songs, and adding, changing or removing an entry (or `set-prerecorded`) calls `refresh_station`, so the change is on the queue at once. Test: `tests/test_block_songs_reach_queue.py`.

API: `GET/PUT /playlists/generation`, `POST /playlists/generation/regenerate`.

Minimum vote: `Asset.vote` is a staff-set whole number (0 = unrated), edited on the song's page and shown in the library list beside "Last played". `playlist_generation.min_vote` (0 = off) keeps songs under that vote out of every draw. A type where NO song reaches the minimum (jingles, usually unrated) is drawn as if there were no minimum, and the preview says so; otherwise a minimum would silence the type. Listener ratings (`SongRating`) are a separate thing and do not feed this.

Not built yet: jingle groups with counts, seasonal and parsha types switched by the calendar, "reset sacks".

Test note: the draw is random, so `tests/test_playlist_generation.py` seeds `random`; in its tiny library some orders legitimately reuse a queued song to keep the artist rule.

## Items no automatic pick may use

`fit_for_random_pick()` (models/asset.py) skips categories do_not_play/test (any case) and one-moment clips. Placeholders listed by title in `NEVER_AUTO_TITLES` (the old "KOL BRAMAH TEST SPONSOR", category "Spots"; also "Kol bramah sponsor" and "Kol bramah ad", confirmed placeholders by Shmuel 2026-11-16; exact title match, any case) are skipped by **every** automatic pick through `not_placeholder()`, including rotations, rules and program blocks that name the category. Each refill (and each restart) deletes pending copies an automatic pick queued; operator, request and loop entries stay. Test: `tests/test_placeholder_never_auto.py`.

Category **do_not_play** (any case, `NEVER_AUTO_CATEGORIES`) is in `not_placeholder()` too since 2026-11-17, so a rule, block or playlist that names it gets nothing. At pick time `_skip_placeholders` (queue.py) marks pending automatic entries of either kind `skipped`; the playout engine's `pick_next` runs the same guard (before that it did not, so an engine station could air a queued placeholder). Only an operator's own pick (manual, request, loop) still plays one. Test: `tests/test_do_not_play_and_codec.py`.

Uploads are checked by codec, not extension: `convert_audio` probes the first audio stream and re-encodes when a file named `.mp3` is not MP3 (160 old files were MP2 inside `.mp3`; Safari and the engine refuse those). `.mp2`/`.mpg` uploads now become MP3 instead of being kept as MP2.

Since 2026-11-17 (library tests): the upload form's "MP2" choice is stored as MP3 too (before, the choice won and MP2 was stored). A file FFmpeg cannot read (PDF, image, broken file) raises `UnreadableAudioError` in `convert_audio` and `/assets/upload` answers 415 with nothing stored; FFmpeg failing on a readable file is refused the same way (the original used to be stored under a success message). Only a machine with no FFmpeg at all keeps the old store-as-is fallback. Uploads over `MAX_UPLOAD_MB` (600) get 413 before the file is read; an empty file 422. Open owner question: "Keep Original" of an MP2 file and the OGG choice still store formats Safari or the engine may not play (strict xfail in `tests/test_library_assets.py`).

## Editing steps is instant

Adding, removing or moving a step changes the cached playlists before the server answers (`usePlaylists.ts`, optimistic with rollback on error). The server answers at once too: the queue rebuild after a playlist edit runs 1.5 s later in its own session (`_schedule_refresh` in `api/v1/playlists.py`), and edits within that window share one rebuild per station. Tests set `REFRESH_INLINE = True` (conftest) to see the rebuilt queue in the response.
