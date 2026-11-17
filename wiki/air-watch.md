# Air watch (on-air self-check)

`backend/app/services/air_watch.py`, started from `main.py` lifespan (`watch_loop`), runs
every 5 s in the process that holds the scheduler lease. Added 2026-11-16 after Main's
9 PM time check aired at 9:09 and only Shmuel noticed.

What it reports, per active station (none during a live show):

| kind | when | severity |
|---|---|---|
| late_timed | a hard timed item (time check, hard block, hard sponsorship) started more than 8 s after its time, or is still pending 8 s after it | critical + push |
| hour_missing | 40 s after each local hh:00, a station that aired a time check in the past 3 h has none that started within 20 s of the hour | critical + push |
| dead_air | nothing on air for 20 s, outside blackouts | critical + push |
| engine_drift | engine station: the engine's /status entry differs from the site's playing entry for 20 s, or the engine does not answer | critical + push |
| overrun | the item shown on air is 30 s past its length (site and air parted) | warning |
| timed_skipped | a time check, weather or block/sponsorship entry marked skipped with no twin (same asset and time) that aired | warning |

Soft items are never "late" (they wait for the song by design), and the long-item `wait`
policy turns hard items soft before their time, so it does not fire either. Early starts are
fine (a timed item due within 8 s of a song change starts at the change).

Each finding is reported once (keyed by entry, hour or spell): an alert on the Alerts page
through `create_alert` (repeats fold) and, for the critical kinds, one ntfy push per
station and kind per 15 min to `NTFY_TOPIC` (same topic as bug reports). `GET
/api/v1/air-watch` (staff) shows the last 200 findings and when it last ran.

Tests: `backend/tests/test_air_watch.py`. Stations on "UTC" are read as New York time,
like the hourly time checks.
