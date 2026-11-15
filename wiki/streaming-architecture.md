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
