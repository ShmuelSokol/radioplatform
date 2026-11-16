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
feed the queue through replenish.

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
by `given`, timed duplicates by the engine's own id check. The one thing lost is "the last
change was a manual skip", so a Next pressed in the second before a restart may be logged as
played instead of skipped.

## Flags (backend)

`PLAYOUT_ENGINE_ENABLED`, `PLAYOUT_ENGINE_STATION_ID` (one station), `PLAYOUT_ENGINE_URL`,
`PLAYOUT_ENGINE_TOKEN`, `ICECAST_STREAM_URL`, and `PLAYOUT_STREAM_LIVE` (false = engine runs
and can be listened to at the stream URL, but the site keeps the in-browser player; true =
the site plays the stream). Turn `PLAYOUT_STREAM_LIVE` on last.

## Server

`infra/streaming/setup.sh [station-id] [domain]` (no id = the first station the backend lists) on a fresh Ubuntu 24.04 machine (apt has
Liquidsoap 2.2.4 there; 22.04 has 2.0 and will not run `radio.liq`). It generates the secrets
on the machine, prints the backend settings, and gets the certificate once the domain points at
the server. Safe to run again.

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
Test: `backend/tests/test_safe_migrations.py`.
