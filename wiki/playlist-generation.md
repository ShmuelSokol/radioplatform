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

Blackouts (Shabbos, Yom Tov) and time with no block generate nothing and are reported in `Plan.notes`.

Off by default: `automation_config.playlist_generation = {enabled, hours, artist_distance}`; when on, `QueueReplenishService` fills from it instead of the rule-based fill. API: `GET/PUT /playlists/generation`, `POST /playlists/generation/regenerate`.

Minimum vote: `Asset.vote` is a staff-set whole number (0 = unrated), edited on the song's page and shown in the library list beside "Last played". `playlist_generation.min_vote` (0 = off) keeps songs under that vote out of every draw. A type where NO song reaches the minimum (jingles, usually unrated) is drawn as if there were no minimum, and the preview says so; otherwise a minimum would silence the type. Listener ratings (`SongRating`) are a separate thing and do not feed this.

Not built yet: jingle groups with counts, seasonal and parsha types switched by the calendar, "reset sacks".

Test note: the draw is random, so `tests/test_playlist_generation.py` seeds `random`; in its tiny library some orders legitimately reuse a queued song to keep the artist rule.
