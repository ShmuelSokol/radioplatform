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

Before 2026-11-16 the rule-based fill queued about 7 days ahead, so a new block waited behind ~1,450 old songs. Now every change to a schedule, block, playlist or playlist step calls `schedule_refresh_service.refresh_station`: pending `source="auto"` songs with no `preempt_at` are deleted and the queue is generated again from now. Songs a person queued, timed items and program-block entries stay. Only for stations that fill from blocks now or did before the change (`had_playlists`). The refresh calls `db.expire_all()` first: the route session just made the change and its cached playlist steps were stale (a deleted step was still drawn). Test: `tests/test_schedule_refresh.py`. API: `GET/PUT /playlists/generation`, `POST /playlists/generation/regenerate`.

Minimum vote: `Asset.vote` is a staff-set whole number (0 = unrated), edited on the song's page and shown in the library list beside "Last played". `playlist_generation.min_vote` (0 = off) keeps songs under that vote out of every draw. A type where NO song reaches the minimum (jingles, usually unrated) is drawn as if there were no minimum, and the preview says so; otherwise a minimum would silence the type. Listener ratings (`SongRating`) are a separate thing and do not feed this.

Not built yet: jingle groups with counts, seasonal and parsha types switched by the calendar, "reset sacks".

Test note: the draw is random, so `tests/test_playlist_generation.py` seeds `random`; in its tiny library some orders legitimately reuse a queued song to keep the artist rule.
