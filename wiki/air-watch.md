# Air watch (on-air self-check)

`backend/app/services/air_watch.py`, started from `main.py` lifespan (`watch_loop`), runs
every 5 s in the process that holds the scheduler lease. Added 2026-11-16 after Main's
9 PM time check aired at 9:09 and only Shmuel noticed.

What it reports, per active station (none during a live show):

| kind | when | severity |
|---|---|---|
| late_timed | a hard timed item (time check, hard block, hard sponsorship) started more than 8 s after its time, or is still pending 8 s after it | critical + push |
| hour_missing | 40 s after each local hh:00, a station that aired a time check in the past 3 h has none that started within 20 s of the hour; also at hh:30 on a station with `half_hour_time_check` and the half hour on the second, when a half-hour time check was queued for that minute (see hourly-slots.md) | critical + push |
| dead_air | nothing on air for 20 s, outside blackouts | critical + push |
| engine_drift | engine station: the engine's /status entry differs from the site's playing entry for 20 s, or the engine does not answer | critical + push |
| overrun | the item shown on air is 30 s past its length (site and air parted) | warning |
| timed_skipped | a time check, weather or block/sponsorship entry marked skipped with no twin (same asset and time) that aired | warning |
| box_clock | a local FM box reports its clock behind its plan (it plays music only until set); also during a live show | critical + push |
| box_disk, box_audio | a local FM box has under 5 GB free, or items of its plan are not on its disk for 15 min (see [local-fm-box](local-fm-box.md)) | warning |

Soft items are never "late" (they wait for the song by design), and the long-item `wait`
policy turns hard items soft before their time, so it does not fire either. Early starts are
fine (a timed item due within 8 s of a song change starts at the change).

Each finding is reported once (keyed by entry, hour or spell): an alert on the Alerts page
through `create_alert` (repeats fold) and, for the critical kinds, one ntfy push per
station and kind per 15 min to `NTFY_TOPIC` (same topic as bug reports). `GET
/api/v1/air-watch` (staff) shows the last 200 findings and when it last ran.

Tests: `backend/tests/test_air_watch.py`. Stations on "UTC" are read as New York time,
like the hourly time checks.

With HS (hard start) off a station's time check waits for the song on air by design, so `hour_missing` is not checked then (`station_modes.hard_start_on`). Test: `test_no_hour_alarm_while_hard_start_is_off`.

Each pass asks every engine station's stream server for its status at once (`asyncio.gather`), so a hung server costs the pass one 3 s timeout in total, not 3 s per station (`test_hung_stream_servers_keep_a_pass_under_five_seconds`). An operator's Remove of a time check is quiet by design (the row is deleted); a Panic cut on air is reported as "cut short".

## Soft items behind a shiur (2026-11-17)

A soft item due while a shiur had more than 5 minutes left should have paused it (`long_items.SOFT_PAUSE_MIN_LEFT_SECONDS`, see `hourly-slots.md`). It is normally made hard 90 s ahead, so the hard-item check covers it. If it is still soft and waiting `SOFT_STUCK_SECONDS` (30 s) after its time, air watch reports a `late_timed` finding "<item> is waiting behind <shiur>". There is no report on a "wait" station, with HS off, or when the item belongs to the shiur's own block.

## Cost of the read (2026-11-18)

Each pass reads, per station, the entry on air plus what started or came due in the last
10 minutes. `queue_entries` keeps every entry ever played, and there was no index on
`started_at` or `preempt_at`, so each pass went through the station's whole history
(2.4 to 3.9 s in the Railway log; 6,188 pages against 30 on a 400,000 row copy). Fixed
with `ix_queue_entries_station_started` and `ix_queue_entries_station_preempt` (built in
the background by `CONCURRENT_INDEXES` in `app/main.py`) and one statement that joins
three indexed lookups. A new time-window read on `queue_entries` needs an index too.

The two indexes are built in the background after start (`CONCURRENT_INDEXES`). The build retries a refused connection or a failed build, and every start logs `Indexes in place: ...` (or `INDEXES MISSING ...` as an error), so the log answers whether they exist. An index that already exists logs no `Built index` line.

## Outside watch (off every Kol B'ramah machine)

Air watch runs inside the backend, so it cannot report the backend itself being down; the
PC's `kbr_247_watch.py` misses whatever happens while the PC is off, asleep or at the
login screen. `.github/workflows/outside-watch.yml` is the backstop: every 5 minutes
(GitHub may run it a few minutes late) `.github/scripts/outside_watch.py` checks
`/health` and, for each station `/playout-status` lists as `mode: "server"`, that the
engine answers and its stream (`stream.kbrlive.com/live-<id>`) sends audio. A failure is
rechecked 30 s later. State lives in an open issue titled "Outside watch: the station is
failing a check": opened with an ntfy push, a comment + push at most every 30 minutes
while failing, closed with one "OK again" push. It does not check silence (blackouts are
silent on purpose). The ntfy topic is the repo Actions secret `NTFY_TOPIC`; without it the
issue still opens but nothing is pushed. The run stays green on a failure so GitHub does
not email every 5 minutes.
