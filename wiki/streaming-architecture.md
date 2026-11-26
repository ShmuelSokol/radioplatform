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
- **Three lanes**: timed, main (songs overlap by the station's crossfade setting, 1 s by default; spoken items never overlap), emergency (local files in
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
quality), encoded once and copied to Icecast and HLS (see "One MP3 encode" below). At 320 kbps a listener uses about 144 MB an
hour, so a 1 TB monthly transfer allowance covers about 7,000 listener-hours. In the rig, Next on one station moved only
that station. The backend always sends a JSON body; a bare empty POST through nginx once
got a 502 from harbor.

## Tested off-air

A local rig (Postgres + backend + Icecast + the engine writing a WAV of tones) ran: Next,
a timed spot on the second, Play Now, play-next, and a 50 s backend outage. Next is about
0.5 s to the new song; the timed item landed within 0.1 s; during the outage the pre-loaded
song and then the emergency folder played; afterwards nothing was replayed.
Not tested: real Icecast listeners at scale, real iPhone Safari on the stream.

## Asks, short items and restarts (2026-11-19)
- `request.dynamic` calls `next_request` on every frame (about 25 a second) while a track plays and the queue is empty; `retry_delay` only holds while nothing plays. radio.liq keeps its own `ask_after`: an empty (204) or failed answer waits 1.5 s, and a flush, Next, live change or skip asks at once.
- An answer to an ask that was in flight across a flush is dropped and asked again (`flush_count`): it was how a parked station ID still aired at 03:46Z on Jerusalem.
- `/next` now returns `seconds`. For an item under 12 s (station IDs, time checks, sponsor tags) the engine fetches and downloads the item after it at once, in a thread, and adds it to the main lane. Before, the follow-up was asked only when the short item started, and a download longer than the item played emergency music (Jerusalem 03:46:43Z and 04:00:13Z).
- The follow-up is added to the music lane's queue only once the short item itself is queued or playing. The lane queues the short item only after its own download, and a follow-up that finished downloading first was added first and aired first (Main 15:34-15:45Z 2026-11-19: song, then the 1.9 s station ID right before the :45 sponsorships; the backend then logged the ID as "aired without a start report"). Harness case `slow_short_item_keeps_its_place`.
- If the follow-up is still downloading when the short item ends, the hold lane gives quiet (at most 6 s after the hand-over, `handed_loading()`) instead of emergency music. The harness case `short_item_before_slow_file` (2 s and 9 s items, 4 s download) checks it.
- An emergency-lane start is reported only if it is still on air 1.5 s later with the music and timed lanes empty (`emergency_confirm` in `send_started`). On Main after the :30 sponsorships (10:30:37Z and 16:30Z 2026-11-19, also 23:31, 03:30, 11:50 ET) the engine reported the song and 0.1 s later "emergency"; the song coming back counted as "not a new start", so the site showed nothing on air for the whole song with false emergency and "nothing on air" alerts. The emergency playlist is now re-read every 10 min instead of watching its folder, which reloaded it 4 times at every song start (each song is copied into it). The harness case `no_false_emergency_report` guards the site side; it did not reproduce the blip itself.
- Emergency copies are named after the asset (`<asset id>.mp3`), so the folder holds one copy per item.
- A shiur or story that starts after the time check was handed to the engine (in the last 45 s before it) is still paused and resumed: the pause rule looks at what is on air until the item's second, not only at hand-over (simulator, Stories 11:00, cut with 33 min left).
- Engine restart during a shiur: the first `/next` after a fresh start queues a resume entry at the point it reached (`_resume_after_restart`), so the shiur carries on instead of being lost. Timed items the engine missed raise one alert per pass, not one each.
- Two timed groups close together (the 8:30 time check, then sponsorships 36 s later) each paused the Daf Yomi and each queued a resume entry; only one aired and the other sat in the queue. The pause rule now skips a shiur that already has a waiting resume entry (simulator, Motzaei Shabbos Oct 9 2027, Main and Jerusalem).

## One station first (2026-11-19)
`KBR_RESTART_ONLY=<station id> bash setup.sh <both ids>` checks and installs the new script but restarts only the named engine; the other keeps the old script in memory until a later run without the setting (or its own restart). Always name every station in the first argument: one left out has its engine removed.

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

**Reconnecting (2026-11-19).** Every way of playing the stream reconnects by itself for as long as the page wants it: MSE fetches the mount again into the same SourceBuffer (sequence mode, so the timeline carries on) after a close, an error, or 6 s with no data; HLS and the plain element are loaded again on `ended`, `error`, or 6 s with the time not moving. Waits 1, 2, 4, 8, then every 10 s, back to 1 s after 10 s of steady sound, and at once on the `online` event. HLS goes to the plain stream after three reloads that bring no sound. Before, MSE switched to the plain element at the first drop and the plain element simply ended at the second: silent for good while the page showed the station playing (a test browser on Main, 15:58Z 2026-11-19). A play() refused later (no permission any more), or a pause from outside (lock screen, a call), makes the state `blocked` and the page asks for one tap. The state is on the shared element: `dataset.streamState` and the `kbr-stream-state` event (`starting`, `playing`, `reconnecting`, `blocked`, `stopped`). `lib/stationStream.ts` holds site releases (`holdUpdates`) while its stream is switched on and not blocked, since a reconnecting stream has no element playing and the audio rule alone let a release reload in. An element error no longer switches the page to the built-in player; `onFail` comes only when the stream cannot start at all, and the control room tries the stream again 30 s after such a failure (it used to stay on the built-in player for the rest of the session). Checked by `e2e/live-stream-unit.mjs` (R-1 to R-15) and `e2e/stream-keeps-playing.mjs`.

**Taken away just after the start (2026-11-20).** Shmuel on his iPhone, 10:40 AM ET: the stream "played for like a second, and then the audio stopped", and only Mute then Listen again brought it back. Two ways the code did that, both reproduced: (1) something other than the page paused the stream element right after it started (iPhone Safari pauses a page's player when it moves the sound between the page's players or takes the audio session for a moment), and any outside pause was `blocked`, waiting for a tap; (2) a moment of "nothing on air" from the server (`now_playing` `data: null`, sent between items and when a re-read finds no item) cleared the stream address, so the Listen page and the control room stopped the stream and started it again outside the tap, a gap and on an iPhone a restart that may be refused. Now an outside pause within `RESUME_WINDOW_MS` (20 s) of the sound starting is answered with `play()` on the same element 0.4 s later (no tap needed: the tap that started it unlocked that element for good, in WebKit as on the iPhone), at most 3 times a minute, then `blocked`; a refused `play()` is `blocked` at once. A pause after 20 s of steady sound (lock screen, headphones out) is still left alone and asks for a tap. Both pages keep the station's stream address through "nothing on air"; only an item on air with no address (a browser-player station) drops it. Which of the two hit Shmuel is not known (the page does not report it). Checked by `e2e/iphone-stream-start.mjs` (Chromium with the iPhone tap rule, no MSE, native HLS; parts taken, empty, later, again, stop on both pages) and `e2e/webkit/listener-webkit.py` ONLY=taken (real WebKit; fails on the old code).

## The engine's item must survive a schedule redraw (2026-11-16)

Heard live on Main: the stream kept playing but the site showed nothing on air and the listener page said "Offline". A schedule refresh (`schedule_refresh_service.remove_automatic_songs` / redraw) deleted the unplayed automatic songs, including the one already handed to the engine. Its `/started` report then matched no queue entry, so nothing was marked playing and the queue drifted from what was heard.

- `playout_engine.held_entry_ids(station)` (the cued entry and timed items already sent) are excluded from the refresh.
- **One start report at a time** (2026-11-18): `handle_started` holds a per-station lock (`EngineState.started_lock`), and a report repeating the last entry within `REPEAT_START_SECONDS` (10 s) changes nothing and answers `repeat: true`. Two reports taken at once both closed the item before and wrote its play log twice (Main 20:31Z: a sponsorship counted twice in plays and missed plays). Test: `test_a_start_reported_twice_logs_the_item_before_once`.
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

## Every item handed over with its level; true-peak limiter (2026-11-17, 20:00Z check)
Items were analysed when the item before them went on air (`queue_entry_payload` asks `upcoming_analysis`), which is the same moment the engine asks `/next` for them (it pre-loads one track). So an item with no analysis was handed over without `liq_amplify` and aired as loud as its file (Jerusalem: -19.9 to -10.6 LUFS, `liq_amplify` null on the items checked). Now `playout_engine.ensure_level` analyses the item before `/next` hands it over (waits at most `LEVEL_WAIT_SECONDS` = 6 s; a slower analysis carries on and serves the next airing), and each tick starts the analysis of the next 3 items and of timed items due within 15 min (`_analyse_ahead`). One analysis per item per process (`_level_jobs`). A sampled loudness louder than -5 LUFS is not believed (`LoudnessNotSampledError`: the whole file is measured instead), and turning down now goes to -20 dB (`replay_gain_for`), since a story mastered at -3 LUFS needs -15. The level handed over is worked out from the stored loudness at every read (`stored_analysis` in `models/asset.py` applies `replay_gain_for`), not taken from the stored `replay_gain_db`: items analysed under the old -12 dB limit kept it and one aired 3.5 LU loud (2026-11-18).

The output limiter is FFmpeg's `alimiter` (look-ahead brickwall, -3 dBFS, `level=false`) through `ffmpeg.filter.create`, wrapped in `mksafe` (a filter source counts as fallible). Liquidsoap's `limit()` is `compress` with ratio 20 and a 50 ms attack, so peaks passed it: -0.6 dBFS true peak with threshold -2. Harness `output_limiter`: full-scale file +6 dB comes out at -4.3 dBFS sample / -4.2 dBFS true peak in the stream, titles kept.

## A setup rerun restarts only what changed (2026-11-17)
The droplet has one core. Every Liquidsoap start checks the whole script at full CPU for about 20 s, and `setup.sh` used to restart Icecast and all engines at once on every rerun: Jerusalem was off air about 50 s and the journal showed "We must catchup" up to 7.6 s (each line is a likely stutter). Now a rerun downloads the script as `radio.liq.new`, runs `liquidsoap --check` on it at `nice 19` with `KBR_HLS_DIR` empty (the check creates output folders otherwise) and stops with nothing restarted if it fails; keeps Icecast's relay and admin passwords in the env file so an unchanged `icecast.xml` does not restart it; and restarts an engine only when the hash of its script, unit and env files in `/var/lib/kbr-engine/applied/<id>` differs or it is not running, one at a time, waiting until its `/status` answers. The first rerun after this change still restarts everything once (no hashes yet). Run only the engines that are routed: a second idle engine costs CPU and makes every restart worse.

## One kept connection to the engine (2026-11-17)
`playout_engine._command` and `engine_status` used to open a new `httpx.AsyncClient` per call: a TCP + TLS handshake to the engine server before every Next, Fade, flush and timed hand-over. They now share one client (`_client()`, one per event loop) with a 30 s keep-alive, below nginx's 75 s so our side closes first and a command never lands on a connection the server has just closed. `close_client()` runs at shutdown. Not measured on production yet (the engine is off for Main); expect the saving of one handshake per command.

## One MP3 encode for Icecast and HLS (2026-11-17)
Each engine used to run two `%mp3` encoders at quality 0, one for Icecast and one for the iPhone HLS copy. Measured here on the same input: processing alone about 1% of a core, each quality-0 encode about 5%; the whole output chain 14.9% with two encoders against 8.7% with one. `radio.liq` now encodes once with `ffmpeg.encode.audio` (libmp3lame, 320k, `compression_level=0`, which is LAME quality 0) and both outputs take `%ffmpeg(format="mp3", %audio.copy)`. Same quality setting, so nothing to hear. Things that differ with a copied stream: both outputs are `fallible=true`; Icecast is not told the bitrate, so `headers` sends `ice-bitrate` and `ice-audio-info`; HLS segments still start with Apple's timestamp ID3 tag and now begin on a whole MP3 frame (the old ones cut mid-frame). The harness `icecast_output` scenario checks all of this, including the HLS segments. On the droplet (2026-11-17 21:20Z, before this change) one engine used 20% of the one core and 207 MB; three engines need 2 vCPU / 2 GB at least.

## A skip is checked and given once more (2026-11-17)
For up to 3 s after a song change `cross` is still playing its transition source. `main.skip()` then only ends the transition and the same song carries on, so a second Next within about 3 s of a Next (or of a natural song change) was lost: the site answered "Skipped" and nothing changed. Time alone does not tell which state `cross` is in (a fixed "skip twice inside 3.2 s" sometimes skipped two songs). `transition()` now records `main_changed_at`, and `skip_main()` skips, waits 0.3 s, and skips once more only if no song change followed; the output stays faded down until the change. Harness `next_twice` (gaps 5, 3.5, 2 and 0.5 s): second change in 0.1 to 0.6 s.

## Emergency music is per station (2026-11-17)
`setup.sh` used to give every engine the one folder `/var/lib/kbr-engine/emergency`; on the droplet it held only Jerusalem's last 40 songs, so Main's engine would have fallen back to those. Each station env now sets `KBR_EMERGENCY_DIR=/var/lib/kbr-engine/emergency/<id>`; a new folder is seeded with the shared folder's files so it is never empty, then fills with the station's own songs (only `kbr_kind=music` from the main lane is copied, never time, weather or sponsorship clips).

## No song starts seconds before a hard item (2026-11-17)
Jerusalem 23:00Z: the song before the hour ended 1.9 s early, `/next` handed out a 266 s song and the time check chopped it after 2.4 s. Now `handle_next` answers 204 when the next song would start less than `HOLD_BEFORE_HARD_SECONDS` (8, the browser player's `TIMED_EARLY_START_SECONDS`) before a pending hard item (`_hold_before_hard_item`). The engine asks the moment its pre-loaded song starts, usually before that song's start report is in, so the rule counts the last handed entry while it is still pending. With nothing in the music lane the fallback would drop to emergency music at once, so `radio.liq` has a **hold lane** (silence) between music and emergency, open only while a timed item is loaded and due within 8 s; it is logged, not reported as a start. The backend holds only for an engine that says `proto >= 3` (`ENGINE_PROTO_HOLD`); an older script on the server keeps the old behaviour. The engine re-asks every 2 s, so the next song is handed out once the hard item is due and loads while it plays. Harness: `song_ends_before_hour` (about 1.5 s quiet, time check on its second). The hour fit (`hour_fit.py`) makes this case rare; this is the net under it.

## Live show on an engine station: the stream goes quiet (2026-11-17)
Jerusalem 23:06Z: with a host on air the song on the engine played on for the whole show (the backend only answered `/next` with 204). Now `radio.liq` has a **live lane** (silence) above every other lane, switched by `POST /live {"on": bool}`. On: what is on air fades out, the music lane is marked interrupted (the backend has queued the item again as `resume@N` where that applies) and the lanes below stand still. Off: the music lane skips the interrupted item and the hold lane keeps the stream quiet (at most 12 s) until the next item has loaded, so no emergency music fills in (harness `live_show_quiet`: quiet 1 s after the start, music back 2.9 s after the end). The backend sends it from `live_show_service.start_show` (at once) and from `playout_engine.tick` (`_tell_live`: again after an engine restart, and "off" on the first pass after the show). An engine left quiet by a show that ended while the backend restarted says `live=1` with its `/next`, and is told "off" then. Timed items are not handed over during a show (tick returns early). Gotcha: calling `timed_queue.skip()` straight inside the `/live` harbor handler made Liquidsoap hang at start with no error and `--check` passing; the code lives in `go_live()` beside `skip_on_air`. The harness catches a start hang; `--check` does not. The same hang came back from a `try ... catch` around the handler call in `control()`: the process spins at 100% CPU after "Adding handler" and never starts its outputs. So `setup.sh` now starts a new script once for real on a spare port (8990, no backend, no Icecast, low priority) and swaps it in only if `/status` answers. `/status` itself gave no answer on an engine that never had a music track (infinite `main.remaining()` cannot go into JSON; Main's unrouted engine, 23:17Z): it now reports `main_remaining: -1`.

## Start hang: the limiter must not sit under mksafe (2026-11-18)
Root cause of the start hangs above (gdb on the spinning process): `mksafe(true_peak_limit(radio))`. When the clock collects its sources, mksafe's switch asks the FFmpeg filter source whether it is ready; if that happens before the filter's input is set up, the filter runs its inner clock forever. Whether it happens first depends on the order Liquidsoap wakes sources in, which changed with the number of top-level definitions: adding one unused `ref` to a working script was enough. The limiter's output now stays fallible and every output takes a fallible source (7 of 7 variants start, including ones that hung). The trial start in `setup.sh` stays as a net.

## More than 1 s before a hard item: it starts early (2026-11-18)
Jerusalem 00:00Z: the song ended 6.6 s before the hour, too far for the 4 s hold of the first version, and the next song was cut after 6.8 s. The backend now holds for 8 s. In the hold lane the engine stays quiet when up to 1 s is left (first version: 3 s, which left 2.7 s of silence in the reviewer's `song_spans_hour`; the rule is no silence over 1 s); with more it opens the timed lane at once (`timed_armed`), so the time check starts when the song ends, a few seconds early, as the browser player does. `timed_early` keeps the group's own timer from taking over a second time (it would cut the time check). Harness `song_ends_6s_before_hour`: time check 5.8 s early, whole, once, no silence. The engine does not always ask at the moment a song starts (around a Next it loads further ahead), so the tick also takes back a pre-loaded song that would start inside the window (`_take_back_before_hard_item`, a flush); without it one run in six of `song_ends_before_hour` still played 1.5 s of the next song.

## Open: old-song fragment after a paused shiur's time check (2026-11-18)
Jerusalem 01:00:18Z: after the time check that paused a block's Daf Yomi, 2.8 s of the song the shiur had interrupted at 00:45 came back on air before the shiur resumed (the same family as the fragment after a blackout). The music lane's interrupted song is skipped when the lane returns, but with no next item loaded (`/next` was only asked then; the resume entry took 6 s to load) `cross` plays out what it had buffered of the old song. Not fixed yet: the next item has to be loaded before the group ends, or the lane kept muted until a new item starts (which would leave those seconds quiet).

## Task queues: the level came back late when the backend was slow (2026-11-18)
Everything quick the engine does off the audio thread is a Liquidsoap task: `ramp_master`'s steps, "level back up" after Next or a timed take-over, a timed group's take-over at its second. Its calls to the backend (`/started`, `/next`, `/hello`) and every download are tasks too. Liquidsoap 2.2's default is `generic_queues = 2`, `fast_queues = 0`: two slow calls fill both queues and every quick step waits. Jerusalem 05:30Z to 06:02Z: the backend took 3 to 4.5 s per start report and the stream was digital silence (-91 dB, no lane switch, no stall, no catch-up line) for 2.6 to 3.8 s after Next, after a time check and after a skip. Harness `slow_backend_no_gap` (every backend answer held 4 s): 4.2 s of silence per Next before, 0.1 s after. Fix: `settings.scheduler.fast_queues := 6` (only tasks not marked `fast=false` run there) and `generic_queues := 6`; the hello retry loop is now `fast=false` like the start report. Still true: `next_request` and downloads are "fast" tasks to Liquidsoap, so six of them stuck at once would starve the quick steps again; a timed group is at most a handful of files. How it was told from a stall: bytes per half second from the stream stayed steady, and `volumedetect` inside the gap read -91 dB (a pause in a recording reads -50 to -70).

## Start times come from the engine (2026-11-18)
`/started` carries `at`, the engine's clock when the item went on air. `handle_started` uses it for `started_at`, the play log and the previous item's end when it is 0 to 60 s old (`STARTED_AT_LIVE_MAX_LATE`); a kept report from a local box's relay keeps its own wider rule. Before, the start was stamped when the report got through, so a slow backend showed every item late (4 s with a 4 s backend: the time check aired on time and was recorded +4.1 s; air-watch would have alerted). A time in the future or older than a minute is ignored (wrong clock).

## Back to the music lane: muted until the next item starts, and loaded ahead (2026-11-18)
When the music lane comes back after a timed group or a live show, the interrupted song is skipped and the lane's level used to return 0.15 s later on a timer. If the next item was not loaded by then (the backend answering in 4 s), `cross` played out its buffered last second of the interrupted song, then nothing. Now the lane stays muted (`main_gain` 0, `main_interrupted` true) until the next item's start report passes through `send_started`, which brings the level back. And `/flush` (the backend took back the pre-loaded item) now makes the lane ask again at once when another lane is on air and no show is live: standing still, it only asked when it came back, so a shiur paused during a timed group was fetched after the group ended (4 s of emergency music with the slow backend). Harness with `stack.slow_backend = 4`: `live_show_quiet` (fragment + 3 s silence before; one quiet stretch after), `resume_without_old_song` (3.9 s EMERGENCY before; none after).

## Timed items wait outside the lane until their own time (2026-11-18)
The timed lane is a plain queue: it plays whatever is next and knows no times. The backend hands a hard item over 45 s ahead so its file is there in time, and until now that item went straight into the queue. Two things followed. A Play now in those 45 s (it uses the same lane) was followed at once by the time check, early (V3-52l). And of two groups handed over before the first started, the second took over the lane's single "group time", the first group's take-over gave up, and the first aired at the second's time (harness `live_show_over_hard_item`, `two_groups_20s_apart`). Now `schedule_timed` downloads an item due later (`request.resolve`) and keeps it in `timed_room`; its group enters the lane `timed_room_lead` (9 s) before its time, which is earlier than the hold lane (8 s) and the early start look for it, so those work as before. An item due now (Play now, a late hand-over) or whose group is already in the lane goes straight in. An item still downloading when its group enters (a big shiur file) counts as pending at once, so the lane waits for it as before, and is pushed the moment its file is here (harness `block_with_very_slow_shiur`). `/status` `timed_waiting` counts the room too. Still open: a Play now in the last 9 s before a hard item sits behind it in the queue. Harness: `two_groups_20s_apart`, `play_now_before_hard_item`.

## The skip back to music is checked (2026-11-18)
When a timed item took over while the music lane was inside a song change, the `main.skip()` on the way back only ended `cross`'s transition (as a second Next once did, see `skip_main`). The interrupted song played on. Before PR 280 it was heard again after the time check; since PR 280 keeps the lane muted until the next item starts, it played muted to its end: silence for the rest of the song, no start report, and the backend held back the next hard item ("follows the one before it"). The return branch of `send_started` now checks 0.5 s later: still interrupted, still the music lane, no song change since: skip again (not over an item already being read), up to 3 times, then let the old song through rather than stay silent. Harness: `timed_and_mix_over_levels` followed by `duplicate_timed_id` reproduces it.

## The start report answers before the queue is topped up (2026-11-18)
`handle_started` used to top the queue up (`_replenish_queue`, about 44 database round trips, 2 to 3 s from Railway to Supabase) before answering the engine: 15 of 36 start reports took 3 to 9 s. It now calls `_replenish_soon` (own session, the station's refill lock), as the browser player's song change does. The empty-queue top-up inside `/next` stays in the request: the engine needs the item it produces. The emergency-alert clearing that runs on the first start after emergency music (and once per process) is also in the background now (an alerts read of 0.87 s inside a start report, 10:45Z). Every start report logs its answer time at INFO (`Start report station=… answered in N ms`); before only those over 3 s were logged.

## The start report moves one row, not the whole queue (2026-11-18)
Each engine start report renumbered every playing and pending row (`compact_positions`): about 900 rows with the 48 h fill-ahead, 0.9 to 1.1 s of each report on production (12:07Z to 12:24Z). Now `put_playing_first` gives only the item on air the position just before the first waiting one. Waiting rows keep their positions and their order, so the numbers climb by one per item played (a few hundred a day) and a hole can stay where an out-of-order item (a timed one) used to sit. Nothing reads positions as 1..n: reads order by position, appends use max + 1, inserts shift rows at or after a place, Play next takes the place after the item on air. The browser player's song change and the blackout fill still use `compact_positions`.

## An engine start is quiet until its first item loads (2026-11-18)
After a restart the engine's output began before its first item had loaded, so about 2 s of an emergency song went on air (Main and Jerusalem at every setup.sh rerun; on a local FM box it would go out on FM). `radio.liq` now keeps the hold lane open for up to 8 s after a start that reached the backend (`start_quiet_until`, set only when `/hello` answered; with the backend down the emergency folder still plays at once). The first start from the music lane closes it. The backend side mattered too: after `/hello` the entry it still shows as playing was cut by the restart, but the hold rule (`_hold_before_hard_item`) waited for that entry's end and answered 204 for 20 s when it would have ended just before a hard item, so the quiet window ran out into emergency music. `EngineState.fresh_start` (set by `/hello`, cleared by the next start report) makes the rule treat the station as having nothing on air. Harness `engine_crash_restart` now fails on any EMERGENCY after the restart.

## The item after a live show loads during the show (2026-11-18)
Music came back about 5 s after a show ended on Main (2 s in the harness): the music lane stands still under the live lane and only asked `/next` once the show was over, then waited for the download, and the backend told the engine "show over" only on its next scheduler pass. Now (engine `proto` 4, `ENGINE_PROTO_LIVE_PRELOAD`) `start_show` quiets the engine first and then flushes; `/flush` asks again under the live lane, and `/next` hands an engine that says `live=1` the item that follows the show (the paused item's resume entry first) instead of 204. `EngineState.cued_for_after_show` keeps the tick's during-show flush off that item. `end_show` sends `/live off` at once. Harness `live_show_quiet`: music back 0.8 s after the end (0.7 s with a 4 s backend). Gotcha: a second ask from the `/live` handler crossed the flush's ask with a slow backend, two items loaded, and the backend's queue-head check flushed both when the show ended; only the flush asks now.

## A Next never falls into emergency music (2026-11-18)
Main 13:27:42Z: Shmuel pressed Next by hand; the next item was "prepared" with 0.00 s decoded (cross analysis -nan dB) on the busy one-CPU droplet, so the music lane ran dry and the emergency lane played for 1.7 s. `skip_main` now sets `start_quiet_until` 6 s ahead, so the hold lane keeps the stream quiet until the next item has audio; the next music-lane start closes it. Harness `next_on_a_busy_server` pins the engine to one CPU beside two busy processes and presses Next right after items start; it did not reproduce the empty prepare locally (no emergency music with or without the fix), so the guard is the text test `test_a_skip_holds_quiet_until_the_next_item_has_audio_instead_of_emergency_music`.

## The proto on every levels post (engine proto 7, 2026-11-25)
`EngineState.engine_proto` lives in the backend's memory and was learnt only from `/hello` and `/next`. After a backend restart in mid shiur (Main, 2026-11-24 20:20-21:02 ET, the deploy of PR 507) it stayed 0 until the shiur ended: `live_on_stream` false, and the hold, parallel timed groups and live preload were off for 42 minutes. `radio.liq` now sends `proto` in its levels post (every second), and `note_levels` fills an unknown proto from it, only from the station's own engine (`X-Playout-Instance`) or before that is known. A proto already known is never changed there; the hello stays the word. Takes effect once `setup.sh` is rerun; older engines send no proto and nothing changes.

## The live show is on the stream (engine proto 5, 2026-11-18)
The live lane used to be blank: the host was heard only through the listener pages' own socket. Now it plays the show from the backend's `GET /playout/{station}/live.mp3` (PR 317: after the broadcast delay, with Dumps and time checks, MP3 192 kbps; 204 when no show is heard; EOF when the delay has played out). `radio.liq` opens it with `input.http(start=false, ...)` on `/live on` and stops it on `/live off`; the token goes in ffmpeg's `headers` string arg, never in the URL (the endpoint refuses `?token=`). The lane is `fallback([live_feed, blank])` inside the `host_gate` switch, so before the first audio, during a stall and after EOF it is quiet, never music or emergency. `/playout-status` reports `engine_proto` and `live_on_stream` (proto >= `ENGINE_PROTO_LIVE_AUDIO`, 5), which listener pages use to switch to the stream for a show. Harness `live_show_on_the_stream` (the harness proxy serves a 1200 Hz "HOST" tone as live.mp3): host heard 1.5 s after the start, an 8 s stall is quiet (silence 2.4 s after the stall, host back 1.7 s after the feed returns), music back 1.0 s after the end, no emergency music. The harness station has `live_delay_seconds: 0`, since no host audio is sent there.

## One engine per station (instance id)

Every engine run makes a random id (`engine_instance` in radio.liq) and sends it as
`X-Playout-Instance` with every call to the backend (hello, next, started) and in its
`/status`. A local FM box's relay passes it on. The backend (`playout_engine.instance_ok`,
dependency `_one_engine` in `api/v1/playout.py`) plays a station to one engine only: a
caller whose id differs from the station's known engine is checked against the station's
control line (the stream server's URL, or the box's relay while it polls). If that line
answers with another id, the caller gets 409 on hello, next and started, takes nothing
from the queue and does not reset what the real engine holds; a critical alert
"Two playout engines for one station" is raised once, and the refused id is rechecked
only every 60 s. A restart is never refused (the old engine is gone; the line answers with
the new id or not yet at all), a control line that does not answer means the caller is
taken (a blip must not take a station off air), and after a backend restart the first
caller is checked the same way. An engine that sends no id is not checked. The refused
engine's log says `hello: REFUSED`. Tests: `test_a_second_engine_with_the_same_station_id_is_refused`,
relay `test_the_engine_instance_id_reaches_the_backend_and_a_refusal_reaches_the_engine`,
harness `second_engine_refused`.

## One start report per item

The engine reports a start when what is on air changes (`report_started`). An item seen
again within 3 s of its own start (another source blipped in between, e.g. a quiet hold
or a crossfade's metadata) is not a new start: it is logged as "came back within ... s;
not a new start", its lane is restored, and nothing is reported or counted. This followed
two start reports for one sponsorship at Main's 20:30Z break (2026-11-18), which wrote two
play log rows; the backend also ignores exact repeats within 10 s since PR 364. Emergency
files (no entry id) are still reported on every change. Also ignored: the item that
just ended showing again within 1.5 s in the same lane (a crossfade can show the
outgoing track's tags once more), logged as "showed again for a moment; ignored". Main's
journal for that break showed one plain crossfade and no hold or timed line, so this
crossfade path is the likely cause. Start reports are not logged by the engine unless the
backend is unreachable.

## The FM feed is the engine's stream (2026-11-19)

The FM feed must carry the whole station at all times: automation between shows,
host and callers during shows, and never drop. That is the playout engine's stream
(and, in the BMG room, the same engine on the local box: [local-fm-box](local-fm-box.md)).
The backend's own Icecast source (`icecast_service.py`, "Start the FM feed",
`ICECAST_HOST`) carried only the live host's microphone and dropped between shows;
it was never configured in production and is retired. `/icecast/{id}/start` and
`/stop` answer 410; `/icecast/status` names the engine's streams.

## Broadcast sound: leveler, three bands, limiter (2026-11-19)
Shmuel asked for "the best audio possible". Before: compressor -14 dB 3:1 and alimiter -3 dBFS only. The 8 PM recording of Main (2026-11-18 19:59 to 20:03 ET) measured -19.5 LUFS, items 6 LU apart (songs -17.6, the spoken run after the time check -24), peaks -5.7 dBFS: the limiter never worked.

With `KBR_PROCESSING=broadcast` (per engine, in its station env file; setup.sh writes it from `KBR_BROADCAST_SOUND=<id>,...|all|none`, unset = each engine keeps what it had, light when new) the whole output goes through:
1. **Leveler** (`leveler()` in radio.liq): a copy of Liquidsoap's `normalize()` with `amplify(override=null())`. Target `KBR_PROCESSING_TARGET` (-14 dB RMS), up 2 s, down 1 s, window 1 s, gain -10..+15 dB, frozen below -45 dB (pauses, the quiet hold, fade-outs). **Never use `normalize()` here**: it ends in a plain amplify, which takes each track's `liq_amplify` over its own gain, so the leveler stood still and each item's level applied twice (the same trap as above).
2. **FFmpeg graph**: highpass 35 Hz, `acrossover` 150 Hz / 4 kHz (LR 4th order), `acompressor` per band (low -24 dB 3:1 40/350 ms +3; mid -22 dB 2.5:1 12/220 ms +2; high -26 dB 2.5:1 5/150 ms +3), `amix` (`inputs=3` is required, the default is 2), `alimiter` -1.5 dBFS.
3. `amix` makes new frames, so frame metadata (titles) does not pass: `broadcast_sound` puts it back with `insert_metadata` from the leveler's `on_metadata`. Start reports come from `program`, before all of this.

Measured on the same recording: -15.4 LUFS, items within about 2 LU, LRA 10.2 -> 3.6, true peaks under -1 dBFS. CPU: about +1.5% of one core per engine over no processing (old chain +1%), measured offline on a 2.3 GHz Xeon. Samples for Shmuel in project files `audio-quality/`.

`light` (the script's default) is the old chain. radio.liq is one shared file on the droplet, so the switch is per engine env, not per script; a change restarts only the engines whose env changed. The harness runs with `light` by default (its level checks measure each lane's exact level, which the leveler evens out on purpose); scenario `broadcast_processing` restarts the engine with the broadcast chain; `KBR_HARNESS_PROCESSING=broadcast` runs any scenario with it.

Not covered: All Day Stories on the browser player (no processing at all); FM later needs its own pre-emphasis-aware clipper on the box, not this chain.

## The engine keeps the blackout itself; stalled engines restart (2026-11-21)

First engine Shabbos (2026-11-20): the blackout's "Silence (5 min)" had an analysis with its start cue at the end of the file (the analysis of a silent file), so every silence ended at once; the lanes ran dry and the emergency music played (Main 21:19:49Z and 21:37-21:49Z, Jerusalem 13:57Z), /next was asked every 1-2 s, the 1 GB droplet ran out of memory, Jerusalem's engine was OOM-killed and Main's output stalled (mount gone, 0.5% CPU) for a day. PR 483 stopped the cue; after Shabbos:

- **Blackouts in the engine** (radio.liq "Blackouts"): the engine asks `GET /playout/{id}/blackouts` at start and every 30 s (lines `<start> <end>`, unix seconds, an hour back to 10 days ahead) and keeps them on disk (`<emergency dir>.blackouts`), so a start with no backend knows them. While one is on: a blank lane sits above the emergency music, and everything below the alert lane is muted unless the item on air carries `kbr_blackout_ok=1` (build_uri sets it for silence and the Shabbos-ending item). The blank is not reported (lane "blackout"). An EAS alert is never muted. The backend sends `/blackouts` when a blackout ends while silence is on air, so an early end unmutes at once.
- **Cues on silent files**: `stored_analysis` drops a start cue at or past the end; the analysis and the range analysis never store one.
- **Backstop**: `/next`'s silence row for an empty blackout queue is made at most once a minute per station.
- **Stall watchdogs**: radio.liq exits when no audio frame went out for 30 s (`KBR_STALL_SECONDS`; it runs in a thread, proved with a blocked clock in `harness/stall_watchdog_check.liq`) and systemd restarts it; `kbr-engine-watch` (timer, every 20 s, from setup.sh) restarts an engine whose Icecast mount is missing or whose HLS media playlist (`mp3.m3u8`, not the master `live.m3u8`, which is written once) stopped changing for 60 s (not in its first 120 s; the first version watched the master and restarted Main at 14:09Z 2026-11-22); air watch raises `stream_stopped` when an engine's levels stop for 60 s.
- **Server room**: setup.sh adds a 1 GB swap file (swappiness 10), masks fwupd, and the engine unit has `MemoryMax=450M` and `OOMScoreAdjust=-500`.

Harness: `blackout_guard_silence` (silence file 404, emergency folder full), `blackout_guard_backend` (backend stopped and engine restarted inside the window), `blackout_ended_early_unmutes`; the harness silence carries production's bad analysis.

## A flush never leaves a download behind; a removed blackout takes its silence (2026-11-23)

- **Download on its way during a flush** (Main 14:19Z 2026-11-20, a sponsorship aired twice back to back): `/flush` empties the music lane's queue, but a download already on its way was added after it. The flush also forgot the entry on both sides (backend `cued_entry_id`, engine `given`), and a flush while another lane is on air asks again at once, so the backend handed the same entry again and both copies aired. Now `next_request` downloads the file itself (`request.resolve`) and drops it when a flush came meanwhile, then asks again; and only one ask (with its download) runs at a time (`fetching`), because with two the one whose file came first was queued first, out of order. Harness: `flush_during_download` (Play next during an 8 s download), `flush_during_download_timed` (the Friday shape: Fire now on air, then a flush). The old radio.liq aired the slow item twice there (48 s of a 40 s tone).
- **Removed or moved blackout** (KBR Jerusalem 14:15Z 2026-11-22: a test window deleted 2.5 min before it began still aired 5 s of silence in a story): a coming blackout's silence is hard timed at its start and goes to the timed lane ahead of time; deleting the window left those rows. `DELETE /holidays/{id}` and a `PUT` that changes the times, stations or blackout flag now call `queue.release_blackout`: the window's waiting silence leaves the queue on every station it covered (unless another blackout covers that time), the engine is told `/timed_drop` for all of them (not only the ones remembered, a restart forgets) and `/blackouts`; a moved window is filled again at its new time. Creating a window also sends `/blackouts`, so the engine knows it at once instead of on its next 30 s fetch.

## Back to music after a timed item: never skip the resume, never emergency while it loads (2026-11-23)

KBR Jerusalem 10:30Z 2026-11-23: Daf Yomi ("Succah 50", 54 min) paused for the :30 time check. Its resume (`resume@3006`) was handed out 40 s before, but the whole file was still downloading when the time check ended. The engine skipped the interrupted shiur. With no song change yet, it took that for "the old song is still there" and skipped again, and the second skip hit the resume the moment it arrived. The shiur lost its last 4 minutes; the backend closed the resume as aired (it was in `given`). The music lane was then empty while it asked again, so the emergency music played for under a second.

- The skip is given again only while the interrupted entry is still the music lane's current item (`main_current_entry()`, from `request.dynamic`'s `current()`).
- `hold_open()` is also true while the music lane is fetching its next item (`fetching_now()`, at most `fetch_hold` = 6 s): quiet, not emergency.

Harness: `resume_arrives_after_time_check` (the resume's file arrives `KBR_RESUME_LATE` s, default 1.4, after the time check ends). The old radio.liq played emergency music there at 1.0, 1.4 and 1.8 s. `music_not_ready_after_timed` covers an almost empty item after a time check.

Why the station simulator missed it: its fake engine starts every item the moment it is handed. It never models a download, the music lane's skip check, or the fallback to emergency, so every paused shiur "comes back" there. Timing faults between lanes need the real engine (harness).

## Nightly updates must not restart the engines (2026-11-26)

Main lost three songs at 1:01-1:05 AM ET on 2026-11-26 (and two on 11-25 at 1:49 and 1:53): unattended-upgrades installed library updates, and its needrestart step restarted `icecast2`. `kbr-engine@` has `Requires=icecast2`, so systemd restarted every engine, about 20 s of silence each, and the engine came back on the next queue entry. `setup.sh` (and `infra/local/setup-box.sh`) now write `/etc/needrestart/conf.d/kbr-no-auto-restart.conf`, which keeps needrestart off icecast2, every `kbr-*` unit and nginx. Updates still install; the engines take new libraries at the next setup run or a planned reboot (automatic reboot is off). A song that ends early with no `/skip` or `/flush` in the engine log: look for "Shutdown signal received" in the journal first.
