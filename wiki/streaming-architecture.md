# Streaming architecture: the playout engine

## State (2026-11-15)

Code is merged but **off**: `PLAYOUT_ENGINE_ENABLED=false`, no stream server exists yet. All
listening still goes through the in-browser player ([audio-engine](audio-engine.md)). Nothing
changes for a station until the flags below are set for it.

## Why

With the in-browser player every listener's browser plays its own copy, so Next can only be
as fast as each browser and listeners are never truly in sync. One engine playing one stream
is the only way "press Next, everyone hears it now" can hold.

## How it works

Liquidsoap 2.2 + Icecast on one small server (`infra/streaming/`). The backend stays the brain;
the engine owns the clock.

- **Engine asks** `GET /api/v1/playout/{station}/next?given=<ids>` when it needs a track. It
  keeps one track pre-loaded. `given` is the last few entry ids it was handed, newest first:
  the backend never hands those out again, and closes older ones still shown as pending (their
  start report was lost while the backend was down). This is what prevents a replay after an
  outage, and it means the "what has the engine got" memory lives in the engine, not in a
  backend process.
- **Engine tells** `POST /playout/{station}/started` when a track really goes on air. Only this
  moves `playing`/`played`, writes the play log and broadcasts now-playing. Nothing advances by
  database duration for an engine station (`_precise_advance`, `_advance_queue` and the startup
  resume all step aside via `settings.engine_drives(station_id)`).
- **Backend commands** the engine over HTTPS (`PLAYOUT_ENGINE_URL`, nginx `/control/` to the
  engine's localhost port): `/skip` (0.5 s fade, next track at full level), `/flush` (queue
  changed, drop the pre-loaded track), `/timed` (load now, air at an exact second, music fades
  under it; used for hard-timed items and Play Now), `/status`.
- Both directions carry `X-Playout-Token`. There is no telnet.
- **Groups in the timed lane**: items handed over for the same time (a program block: ID,
  weather, sponsors) play through together in the order received; the backend sends them by
  position. Until 2026-11-15 only the first item of such a group aired and the rest stayed
  stuck in the timed queue. The song the group interrupted is not reported again when the
  music lane comes back (it is skipped at once).
- **Soft with a limit (`hard_by`)**: see `program-blocks.md`.
- **Mix over** sits on top of the lanes (`/overlay`): a jingle added to whatever is on air.
  It sends no start report.
- **Crossfade order matters**: in the song-to-song transition the incoming song must be the
  first source of `add`, because `add` passes on only its first source's titles and the start
  report is made from them. With the outgoing song first, natural song changes were never
  reported (found on the rig 2026-11-15; Next was unaffected, which hid it).
- **Three lanes**: timed, main (3 s crossfade), emergency (local files in
  `/var/lib/kbr-engine/emergency`, then silence). If the backend is unreachable the engine
  plays what it has, then the emergency folder, and picks the queue up again by itself.
- `scheduler_engine` calls `playout_engine.tick` (leader only) to hand timed items over 45 s
  ahead. The engine ignores a timed id it already holds, so a backend restart cannot air one
  twice.
- What plays next is still decided by the queue and `SchedulingService`; the engine picks
  nothing itself.

## One player per station

The queue is the only player (the engine plays it when it drives a station). Only
`_check_advance` / the precise timer (or `playout_engine.tick`) advance playback; they keep
no `now_playing` row, the WebSocket payload is built from the playing queue entry.
When an item ends, is skipped (Next/Panic) or removed with nothing to follow it (a blackout with no
silence queued, a refill that found nothing), `websocket.broadcast_nothing_playing` sends one
`{"type":"now_playing","data":null}` so open pages drop the old item. A live show taking the air sends none.
`SchedulerEngine._check_station` plays nothing: it handles live-show hard stops, blackout
silence and dead air. Dead air (no `playing` entry past `SILENCE_DETECTION_SECONDS`) raises
one alert per spell and recovers through `queue.start_from_queue` (under `advance_lock`, starts
nothing if an item is already playing), the same path as Start. A leftover `now_playing` row
is deleted on the next check.

Until 2026-11-15 `_check_station` also ran an old block player whenever a schedule block was
active: it picked songs from the block, wrote `now_playing` (with `block_id`) and play_logs,
and broadcast now_playing messages that browsers obeyed. With the queue writing no
`now_playing` row it fired every song, so the station ran two sequences at once (KBR
Jerusalem, "Night Chill" block): stops after Fade and songs faded early. `_check_channel`
(a per-channel copy of it) and the emergency jingle fallback were removed for the same
reason. Test: `tests/test_one_player_per_station.py`. Schedule blocks still matter: they
feed the queue through replenish. The old `/controls/play-now` was a second player too
(ffmpeg HLS); since 2026-11-17 it goes through the queue (see [operator-actions](operator-actions.md)).

Generated Yom Tov windows (`shabbos_service.generate_yom_tov_windows`) follow the station's
time zone: Asia/Jerusalem (or Israel, Asia/Tel_Aviv) gets the 1-day Israel calendar (Rosh
Hashanah stays 2 days), anywhere else the 2-day diaspora one. Windows generated before
2026-11-17 for an Israel station still hold the second days; regenerate or delete them.
A sun-relative schedule block where the sun does not rise or set that day (polar) uses its
fixed clock time instead of raising (`SchedulingService._resolve_sun_time`).

## Timed items start on time

The 3 s poll and the song-end timer used to be the only things that advanced, so a hard
timed item (hard sponsorship, hourly time) started up to 3 s late while the control room had
already faded the song out: dead air. Each poll now arms an exact timer
(`SchedulerEngine._arm_timed_start`) for the next item due within 10 s: a hard item at
`preempt_at`, a soft one at `hard_by` (soft items with no limit wait for the song end). The
web player's lead-in fade ends 400 ms after the due time (`PREEMPT_FADE_OVERHANG_MS`) so the
announcement lands before silence. Test: `tests/test_timed_start_on_time.py`.

## "Next" names what the queue will really pick

Every now-playing message (scheduler broadcast, `/streams/.../now-playing`) carries `next_asset`,
which listeners pre-load and crossfade into at `cross_start`. It comes from
`queue.predict_next_entry`: the same `_pick_next_pending` rule, evaluated at the hand-over time
(`switch_time`: `started_at + cross_start`, default 3 s before the end), not at "now". Picking by
"now" with position order named the next song while a soft sponsorship had come due mid-song:
listeners heard 3 s of that song, then jumped into the sponsorship 3 s late (live check
2026-11-16). The scheduler rebuilds the message every poll, so a slot added mid-song reaches
listeners within 3 s. Test: `tests/test_next_follows_soft_slot.py`.

## A change that loses its item retries

Refills, schedule changes and operator deletes remove pending rows outside the advance lock
(the placeholder-sponsor purge in `queue_replenish_service._schedule_ad_slots`, for one). If one
removes the item an advance is starting, the advance's commit hits `StaleDataError`.
`_check_advance` now rolls back and starts over from the saved state (up to 3 tries), so the
next song plays instead. The scheduler also rolls back after any station's error and re-reads
each station: its one session is shared by every station in a pass, and an un-rolled-back
failure used to fail all the stations after it. Live case 2026-11-16: 29 s of silence until a
manual Next. Test: `tests/test_due_slot_deleted.py`.

## Items with no length

`Asset.duration` can be NULL (some KBR Jerusalem items). Every duration sum must use
`or DEFAULT_DURATION`: `GET /queue` once did `duration - elapsed` and failed for minutes after
a Next onto such an item, and a restart treated it as finished and cut it. Missing lengths are
measured in the background after each start (`services/duration_backfill.py`). A restart now
starts the next item through `start_from_queue`, so an item timed for later is not pulled
forward. Test: `tests/test_items_without_length.py`.

## Backend restarts and two processes

`EngineState` is in memory, per process. It is safe to lose: the pre-loaded entry is covered
by `given`, timed duplicates by the engine's own id check, and what was handed out (entry id
to asset, for a start report of an entry deleted meanwhile) is also written to the
`engine_handoffs` table (pruned after 6 h). The one thing lost is "the last change was a
manual skip", so a Next pressed in the second before a restart may be logged as played
instead of skipped.

## Flags (backend)

`PLAYOUT_ENGINE_ENABLED`, `PLAYOUT_ENGINE_STATION_ID` (comma-separated stations), `PLAYOUT_ENGINE_URL`,
`PLAYOUT_ENGINE_TOKEN`, `ICECAST_STREAM_URL`, and `PLAYOUT_STREAM_LIVE` (false = engine runs
and can be listened to at the stream URL, but the site keeps the in-browser player; true =
the site plays the stream). Turn `PLAYOUT_STREAM_LIVE` on last.

## Server

`infra/streaming/setup.sh [stations] [domain]` on a fresh Ubuntu 24.04 machine (apt has
Liquidsoap 2.2.4 there; 22.04 has 2.0 and will not run `radio.liq`). `stations` is `all`
(default: every station the backend lists) or comma-separated ids. It runs one engine per
station (systemd `kbr-engine@<id>`, env in `/etc/kbr-engine/stations/<id>.env`): control
port 8081, 8082, ... behind nginx at `/control/<id>/`, Icecast mount `/live-<id>`. The
backend addresses each one by station (`settings.engine_control_url`, `engine_stream_url`).
It generates the secrets on the machine, prints the backend settings (the station list for
`PLAYOUT_ENGINE_STATION_ID`), and gets the certificate once the domain points at the server.
Safe to run again: it rewrites the nginx site (certbot reinstalls the certificate), adds
engines for new stations and removes unlisted ones. A station added later needs a rerun
and the new list on Railway; never list a station that has no engine, because the backend
stops advancing an engine station itself.

Each engine uses about 150 MB and 6-7% of one CPU core in steady play (measured 2026-11-16,
three engines in the local rig), so a 1 GB server holds about four or five stations. The
stream is MP3 320 kbps, 44.1 kHz stereo, encoder quality 0 (best; Shmuel asked for top
quality); quality 2 saves only about 2% CPU. At 320 kbps a listener uses about 144 MB an
hour, so a 1 TB monthly transfer allowance covers about 7,000 listener-hours. In the rig, Next on one station moved only
that station. The backend always sends a JSON body; a bare empty POST through nginx once
got a 502 from harbor.

## Tested off-air

A local rig (Postgres + backend + Icecast + the engine writing a WAV of tones) ran: Next,
a timed spot on the second, Play Now, play-next, and a 50 s backend outage. Next is about
0.5 s to the new song; the timed item landed within 0.1 s; during the outage the pre-loaded
song and then the emergency folder played; afterwards nothing was replayed.
Not tested: real Icecast listeners at scale, real iPhone Safari on the stream.

## Old path

`liquidsoap_client.py` / `LIQUIDSOAP_*` (telnet push) is the earlier attempt and must stay off:
it re-pushed the current track every 3 s and ran two clocks.

## A change is announced before the queue is refilled (2026-11-16)

`_check_advance_locked` used to top the queue up (`_replenish_queue`) before returning,
so the caller's now-playing broadcast waited for the refill. At 09:00 on KBR Jerusalem
the refill was slow: the hourly time check started at 09:00:51 but was announced at
09:02:51; listeners played the next song from their pre-load meanwhile, then were
pulled back. Now the refill runs afterwards in its own session (`_replenish_soon`),
under a per-station refill lock, not the change lock (a slow refill must not hold the
next change), and the item on air is announced again when it finishes, so "next"
includes anything the refill added. Refills over 5 s log "queue refill took". Test:
`backend/tests/test_announce_before_refill.py`.

## Deploys must never freeze the live backend (2026-11-16)

The 09:00 freeze on 2026-11-16 (08:58:51 to 09:02:51) was the new container's startup
migrations, not only a slow refill. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` takes an
ACCESS EXCLUSIVE lock even when the column exists; it queued behind the bulk analysis's
open transactions on `assets`, every other query queued behind it, the pool ran out and
the deploy failed its health check (so that release never went live). Now:
`_run_migrations` in `backend/app/main.py` reads `information_schema.columns` and
`pg_indexes` once and does not send statements whose column or index already exists
(`migrations_to_run`), and every statement it does send runs with
`SET LOCAL lock_timeout = '1s'` (the enum connection uses the same timeout), so a busy
table means a logged "Migration skipped", never a frozen station. `analyze_audio`
commits its read before the range reads and FFmpeg and re-reads the row to write the
result, so no transaction is open during the slow part. When adding a migration, keep
the `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` form so the skip works.
The lock holder that day was `POST /assets/bulk-analyze`: it read the asset list in
the request's session and handed the job to FastAPI `BackgroundTasks`, which run before
that session is closed, so its transaction sat idle for the two-hour run. Long jobs now
start as detached tasks (`_run_detached` in `api/v1/assets.py`) after a commit, and the
scheduler opens a fresh session per pass (`async with async_session_factory()`, never
`async for db in get_db(): ... break`, which leaves the session open until garbage
collection). Do not use BackgroundTasks for anything long.
The first deploy with this (6092aa1, 10:02Z) still showed queries on assets and
play_logs taking about 15 s. The three statements that ran then (the holiday reason
UPDATE, `ALTER TYPE user_role ADD VALUE 'dj'` and `CREATE TABLE IF NOT EXISTS
audit_logs`) take no lock on either table, so that stall has another cause, not yet
found (candidates: two containers' pools overlapping on the Supabase pooler, or the
startup tasks). Existing tables are now skipped too and the timeout is 1 s.
The PC's capture of the next deploy found no lock or pooler stall but a heavy cold
start, now fixed: the restart's "close open play logs" read all of play history (now
recent rows per station, `STARTUP_OPEN_LOG_DAYS`), new connections cost ~2 s each on
the first burst (`_warm_connection_pool` opens the pool before the app serves, so the
health check waits for it), and `users.last_seen_at` was written every 5 s by every
request (now at most once a minute per user per process; the Users page shows
"Online" for 2 minutes). The `pg_timezone_names` query seen then is not ours (no code
issues it; the Supabase dashboard does). Test: `backend/tests/test_cold_start.py`.
The burst after that deploy was led by "last played per item": a GROUP BY over a
station's whole play history (3.3 s), now `services/last_played.py` (read in full
once per station per process, then only new plays), and the library's per-row last
played lookup by item, now answered by `ix_play_logs_asset_start (asset_id,
start_utc)`, built CONCURRENTLY in the background after startup
(`CONCURRENT_INDEXES` in main.py; put indexes on big tables there, not in the
migration list, where a plain CREATE INDEX blocks writes for the build).
Test: `backend/tests/test_safe_migrations.py`.

## Listener start time (2026-11-16)

A plain `<audio src=stream>` in Chrome waits for about 200 KB of the 320 kbps stream before it starts (measured on a local Icecast: 5.8 s with a 32 KB burst, 3.4 s with 128 KB, 0.15 s with 256 KB) and then plays 2.3 s behind what it has buffered. The listener page uses `frontend/src/lib/liveStream.ts` instead: it fetches the stream and feeds it to Media Source Extensions, so playback starts on the first data (0.15 s) and is held about 1 s behind the newest audio (it jumps forward after a stall). Browsers without MSE for `audio/mpeg` get the plain element. Icecast's burst (128 KB) gives the first data at once.

## The engine's item must survive a schedule redraw (2026-11-16)

Heard live on Main: the stream kept playing but the site showed nothing on air and the listener page said "Offline". A schedule refresh (`schedule_refresh_service.remove_automatic_songs` / redraw) deleted the unplayed automatic songs, including the one already handed to the engine. Its `/started` report then matched no queue entry, so nothing was marked playing and the queue drifted from what was heard.

- `playout_engine.held_entry_ids(station)` (the cued entry and timed items already sent) are excluded from the refresh.
- The link remembers the asset of the last 30 entries it handed out (`EngineState.handed`), and of every hand-over in the `engine_handoffs` table (model `EngineHandoff`), so it also works after a backend restart. A start report for one that was deleted some other way puts a fresh entry with that asset on air instead of leaving the station empty.
- The listener page shows "On Air" whenever the stream is playing, whatever the now-playing record says.

## Stop, Listen and the phone's own controls (2026-11-16)

Stop then Listen on the built-in player went on from where it was stopped: the click restarted the paused deck and the player took it as already on air. Stop now silences both decks and forgets what they hold, so Listen always joins at the station's current position (`e2e/audio-engine.mjs stop-then-listen`, which fails on the old player). The stream player already reconnects on Listen. Lock screen, CarPlay and headphone pause/play are routed to Stop and Listen through `navigator.mediaSession` action handlers; left to the browser they only paused the audio element, and play resumed old audio.

## Which player a station is on, and timing (2026-11-17)
`GET /playout-status` (public, cached 8 s per station) says per station: `mode` "server" (listeners hear the engine's stream) or "browser" (pages play the files). For an engine station it also asks the engine's `/status` what it has on air and compares that with the queue's playing entry (entry id, else title): `engine_ok` and `in_sync`. `components/audio/PlayoutBadge.tsx` shows it in the control room header, on the station list and on the Listen page: green "Server stream", yellow "out of step", red "not answering", grey "Browser player".

Timing on the server stream:
- Every listener of a station plays the same Icecast mount, so they all hear the same audio. They differ only by their own delay: about 1 s with the MSE player (Chrome, Edge, Firefox, Android). On iPhone Safari, which has no `MediaSource` (only `ManagedMediaSource`, not used yet), it is a few seconds more.
- Hard-timed items (time check, ID) are handed to the engine 45 s early with an absolute time. The engine starts the fade `skip_fade` before it, so the item starts on the second by the stream server's clock (Ubuntu's timesyncd keeps it on time).

## Engine gaps found by tests, fixed (2026-11-17)

`backend/tests/test_engine_backend.py` recorded these as strict xfail tests; all pass now:

- **Missed hand-over**: a hard item whose 45 s window was missed (restart, slow pass, created late) still goes to the timed lane and airs at once while it is at most `MISSED_HAND_OVER_SECONDS` (5 min) late; never during a blackout (silence excepted), never one the engine already holds in its music lane. Later than that, `pick_next` plays it next: it now takes every due timed item first, hard as well as soft (the browser rule), not by queue position.
- **Never delivered**: a hard item whose time passed while every `/timed` failed raises "Timed item did not reach the playout engine" (once per item).
- **Watchdog**: `_alert` treats `last_alert` 0 as "never" (it compared with `time.monotonic()`, so no alert for 30 min after the host booted); silence counts from the first tick until the engine first calls; no "not responding" while the item on air has not reached its end (the engine calls only between items). Past the end the overrun alert covers it.
- **Blackout silence in the timed lane**: at blackout end the silences still waiting there are closed and sent to the engine's new `/timed_drop` (proto 2 engines only) (`radio.liq` skips a dropped id the moment it would start), before `/flush` + `/skip`.
- **Live show**: `/next` answers 204 while `live_show_id` is set (the paused item's resume entry stays queued), the tick drops the pre-loaded track once, a start report without entry during the show raises no emergency alert, and `end_show` does not call `start_from_queue` on an engine station (the engine asks again by itself).
- **Hung engine**: the tick hands each group over in parallel (one command timeout per group, not per item). Each `/timed` carries `seq` and `of`; `radio.liq` holds a group until all of it is there (at most 1 s), then queues it in `seq` order. Groups still go one after another. Only for an engine that reports `proto` >= 2 (`ENGINE_PROTO_GROUPS`): the new `radio.liq` sends it with `/hello` and every `/next`, kept in `EngineState.engine_proto`. An engine that sends none (old `radio.liq`, which queues timed items in arrival order and has no `/timed_drop`) gets the serial hand-over and plain flush + skip at blackout end, so the backend is safe to deploy before `setup.sh` is rerun; the new behaviour turns on by itself after.
- **No playable audio**: `/next` that skipped entries without a fetchable URL and ends with nothing raises "Playout engine has nothing it can play".

Static checks of the stream-server files: `tests/test_engine_infra_files.py`.
## The same song handed out twice (2026-11-17 02:49Z, Main)

The engine asks for its next track the moment a track starts. Its start report for Shema
(02:49:04.79) was still being written when that ask came (02:49:04.8): the report
renumbered about 690 queue rows one UPDATE at a time (over 3 s) before committing, so
Shema still read as pending and was handed out again. The engine played it twice; it
reports a change only when the entry id changes, so nothing reached the backend and the
screen sat at 0:00 until Next. Now: `handle_next` never hands out an entry in
`state.recent_started` (set before the report commits); the renumber is one
`row_number()` UPDATE; `pick_next` is read-only (placeholders and do_not_play filtered in
the SELECT; an UPDATE there held row locks inside the scheduler's long pass); a song still
"on air" 30 s past its end raises "Playout engine did not move on"; automatic flushes of
the pre-loaded track wait 15 s between them and never happen in the last 45 s of an item, unless a timed item is due then.

Engine side (`radio.liq`): every track change is reported (`on_track` too), so untagged
emergency files raise the emergency alert; the emergency folder was empty on the server
(silence), so the engine now copies each song it airs there (newest 40, `kbr_kind=music`);
big songs get 120 s to download.

## iPhone Safari plays an HLS copy (2026-11-17)

iPhone Safari has no Media Source Extensions for MP3, and its plain `<audio>` on the
Icecast stream waited about 10 s before the first sound. Each engine also writes the same
programme as HLS (`output.file.hls`, 1 s MP3 segments with Apple's packed-audio ID3
timestamps) to `KBR_HLS_DIR` (`/var/lib/kbr-engine/hls/<station id>`); nginx serves it
uncached at `/hls/<station id>/mp3.m3u8`. `lib/liveStream.ts` uses it when the browser has
no MSE for MP3 but plays HLS natively (`canPlayType('application/vnd.apple.mpegurl')`),
derived from the Icecast URL (`/live-<id>` -> `/hls/<id>/mp3.m3u8`), and falls back to the
plain stream on an error or no sound within 8 s. In a local rig (hls.js in Chromium) the
first sound came 0.2-0.6 s after load. Needs a `setup.sh` rerun on the server. Real iPhone
and CarPlay not yet tested.

## A take-over never cuts its own first item (2026-11-17)

With the station set to wait, the backend moves the hour's group to the moment the
block's shiur ends. The engine's take-over (`go_on_air` in `radio.liq`) fires then, and it
skipped whatever the timed lane held, to end the shiur. At that instant the shiur had
just ended and the time check had just started, so the time check was skipped and never
aired (harness `block_shiur_spans_hour_wait`). Now it skips only an item that has played
more than 1 s and has more than 1 s left (`timed_queue.elapsed()` / `.remaining()`). Needs
`setup.sh` rerun on the droplet. Checklist V1-10c.

## The engine's hello is retried until the backend answers (2026-11-17)

`radio.liq` sent `/hello` once at start-up. An engine that restarted while the backend was
unreachable (a droplet reboot during a Railway deploy) left the backend believing the
engine still held the timed items handed over before; `timed_sent` kept them out of every
hand-over and `/next`, so the time check never aired (harness `engine_starts_backend_down`).
Now the hello is retried every 5 s until it answers 200. A late hello is safe: `/next`
sends `given` each time and `/timed` ignores an id it already holds. Needs `setup.sh`
rerun. Checklist V1-1.

## A time check whose file is late does not mute the music (2026-11-17)

The take-over muted the music lane at the item's time whether or not its file had arrived.
A time check whose download took 23 s (handed over about 20 s ahead) gave 16 s of silence,
and the timed queue's default 20 s download limit then dropped it, so it never aired
(harness `slow_timed_file`). Now `go_on_air` waits until `timed_queue.is_ready()` (polled
every 0.25 s, for up to 45 s) while the music plays on, then takes over; the timed queue
allows 90 s per download. Needs `setup.sh` rerun. Checklist V1-8.

## Only one amplify reads the track's level (2026-11-17)
Liquidsoap's `amplify` reads the `liq_amplify` metadata by default (`override="liq_amplify"`). radio.liq has three amplify operators in a row on the music lane (per-track level, the mute gain used at a take-over, the master gain used for Next fades), so a song's `replay_gain_db` was applied three times: a -6 dB song played 18 dB down and a +4 dB song 12 dB up into the limiter. Every library item with an analysis has a non-zero level, so this hit almost every song on an engine station. The mute and fade amplifiers now pass `override=null()`; only the per-track amplify (main lane, timed lane, mix-over) reads it. Harness: `track_level` (exactly 6 dB) and `song_with_level_muted_under_time_check`; `test_levels_and_output_processing` refuses any other amplify without `override=null()`.

## The timed lane stays open while its next item is ready (2026-11-17)
At 18:00Z on Jerusalem the engine played the time check and the Daf Yomi announcement, then music, not the shiur. The timed gate was `timed_armed() or (on_air_lane() == "timed" and timed_queue.length() > 0)`, and a `request.queue`'s `length()` counts only requests not yet fetched. With prefetch 1, the shiur was fetched while the announcement played, `length()` dropped to 0, and the switch went back to music when the announcement ended. Now: the timed queue fetches the whole group ahead (`prefetch=10`), the gate also stays open while `timed_queue.is_ready()`, and when the group's next item is still downloading as the one before it ends, music fills and `timed_go_on_air` takes over again as soon as it arrives (`timed_pending` counts items pushed but not yet started; it is raised before the push because a push can wait for the download). Harness: `block_with_slow_shiur` (15 s late), `block_with_very_slow_shiur` (45 s late).

## Loudness of items analysed only from ranges (2026-11-17)
`tail_analysis.analyze_by_range` keeps an earlier analysis's loudness. Items with no earlier full analysis used to get `-18 LUFS / replay_gain 0` without any measurement, so they aired unadjusted (Jerusalem: -13.5 to -19.3 LUFS). Now, when `has_real_loudness()` (models/asset.py) says the stored loudness was never measured, three 15 s pieces at 20/50/80 % are read and measured with loudnorm, power-averaged (`loudness_method: "sampled"`, within 1 dB of a full measurement in tests). `needs_analysis()` sends such old analyses back for analysis, so upcoming_analysis fixes them as they come up and `POST /assets/bulk-analyze` (the music batch) can fix the library. Output limiter lowered to -2 dB: it acts on sample peaks and the MP3 encoder adds about 1 dB between samples (true peaks reached -0.2 dBFS at -1).

## One kept connection to the engine (2026-11-17)
`playout_engine._command` and `engine_status` used to open a new `httpx.AsyncClient` per call: a TCP + TLS handshake to the engine server before every Next, Fade, flush and timed hand-over. They now share one client (`_client()`, one per event loop) with a 30 s keep-alive, below nginx's 75 s so our side closes first and a command never lands on a connection the server has just closed. `close_client()` runs at shutdown. Not measured on production yet (the engine is off for Main); expect the saving of one handshake per command.
