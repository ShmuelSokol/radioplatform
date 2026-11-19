# Local FM box (broadcasting on FM from the transmitter room)

## Why

The FM signal (WMDI 107.9, Lakewood) is fed today by a computer in the server room at BMG,
beside the rooftop antenna: KBR Studio 1.82 plus Magic RDS, all audio on its own drives,
no internet needed. KBR Live must feed FM the same way: a machine in that room that keeps
playing when the internet is down. Shmuel agreed the direction 2026-11-17: **the same
playout engine on a small local box**, not a separate desktop program (a second player to
keep identical is what caused the timing bugs of that week).

## Shape

    website / iPhone ── backend (Railway) ──────────────┐
                                                        │ internet (may drop)
    BMG box:  engine (radio.liq) ── relay (kbr_relay.py)┘
                 │ sound card (stage 2)        │ disk: plan.json, library/, spool.jsonl
                 ▼
          transmitter chain

The engine is unchanged. Its `KBR_BACKEND_URL` points at the relay
(`http://127.0.0.1:8090/api/v1`) instead of the backend.

## Stage 1 (built 2026-11-18): staying on air with the line cut

`infra/local/kbr_relay.py` (Python standard library only), `kbr-relay.service`,
`kbr-relay.env.example`.

- **Line up:** `/next`, `/started`, `/hello` are passed to the backend. In a `/next` answer
  the audio address is swapped for the copy on the box's disk when there is one.
- **Kept on disk:** `GET /playout/{station}/plan` every minute (the pending queue in order,
  each item's `uri_main` / `uri_timed` exactly as `/next` and the timed hand-over would
  give them, blackout windows); the audio of everything in the plan, timed items first;
  then the whole library (`GET /playout/{station}/library`, paged, every 6 h). Both
  endpoints need only the playout token, so a box can fill its disk before its station is
  switched to it. Files are stored as `library/<aa>/<sha1 of the address><ext>`.
- **Line down** (the backend unreachable or answering 5xx; a 403/404 is an answer and is
  passed on): `/next` is answered from the plan. Order as on the backend: timed items whose
  time has come first (by time, then position), then the next untimed item by position;
  future timed items wait. An item is served once: every id handed out, sent to the timed
  lane or reported on air is remembered (`state.json`, survives a relay restart). Items
  whose audio is not on the disk are passed over; with nothing left the relay answers 204
  and the engine plays its emergency folder.
- **Hard timed items offline:** handed to the engine's own `/timed` 45 s ahead, a group in
  position order with `seq`/`of`, so they start on the second from the engine's clock.
  The engine ignores an id it already holds, so one the backend handed over just before
  the cut airs once. A hard item more than 5 min late is dropped, as on the backend.
  Before a hard item (60 s) the relay fetches the plan again, so it knows the state of the
  line at that moment and not a minute earlier.
- **Blackout offline:** inside a blackout window `/next` answers a 60 s silence file and
  missed timed items stay off the air; music returns when the window ends.
- **Start reports** made while the line is down are kept in `spool.jsonl` and sent in
  order when it returns, after a `/hello` if the engine started meanwhile.
- `GET /relay/status` (no token, localhost): online, plan age, plan size, audio still
  missing, reports kept, in blackout.

Checked on the real engine: harness `--local-box local_box_offline` (line cut both ways
for 150 s: songs in order from the local disk, time check on its second, no emergency
file, no silence; after the return the site shows the time check as aired and a song on
air). Unit tests: `backend/tests/test_local_relay.py`.

## Stage 2a (2026-11-18): commands reach a box with no inbound line

The backend sends the stream server's engine its commands over HTTPS
(`PLAYOUT_ENGINE_URL`). A box at BMG cannot be reached from outside, so the relay calls
out: `POST /playout/{station}/commands/poll` (`{wait, answers}`) is held open by the
backend (up to 25 s, no database session) until a command is made, and returns it at
once; the relay posts it to the engine's control port and sends the engine's status and
JSON body with its next poll. `app/services/playout_tunnel.py` keeps this in memory.
`playout_engine._command` and `engine_status` use it **only while a relay is polling for
that station** (a poll open, or one within the last 40 s); otherwise HTTPS as before, so
the stream server's stations are untouched. **One engine per station.** While a relay polls for a station, all of that station's
commands go to the relay and none to the stream server: the box *replaces* the stream
server's engine for that station (its audio is sent up to Icecast for web listeners, gap 1
below); two engines must never pull one station's queue. So the path is closed unless the
station is named in `PLAYOUT_RELAY_STATION_ID` (empty by default): a box set up with a
wrong or copied station id gets 403 on the poll and takes nothing, and every switch of
routing is logged as a warning. Until the box can feed Icecast, try it only under a
station of its own, never under Main's id. (The same mistake on `/next` is caught too since
2026-11-18: each engine run sends its own id and a second engine with the station's id is
refused with 409 and an alert; see "One engine per station" in streaming-architecture.md.)
A command nobody fetched within its wait
(4 s unless the caller gives `wait=`) is withdrawn, never delivered late. `wait=` and
`slow_is_ok=` mean the same as by HTTPS: the relay is told how much of the wait is left
and waits that long for its engine; a command the relay fetched whose answer is not in
when the wait ends (or the relay's 202, "engine still working") is taken when the caller
said `slow_is_ok` (a `/timed` answers only once its file is loaded) and a failure
otherwise. The relay runs each command on its own thread and hands its answer back at
once in a short poll of its own (`wait: 0`), so a slow `/timed` (Next waits 6 s) does
not hold a flush or another hand-over behind it. Order is kept where it matters because
the backend awaits one command before it makes the next. With the tunnel up the backend does the timed
hand-over itself as on the stream server; the relay's own hand-over (`KBR_RELAY_TIMED=offline`)
takes over only when the backend is unreachable.

Harness: `--local-box` now sets the backend's `PLAYOUT_ENGINE_URL` to a dead address, so
every command in those runs can only travel through the relay; `local_box_commands`
checks Next and a backend-handed time check.

## Stage 2b (2026-11-18): the outage is logged when it aired

A start report the relay kept while the line was down goes up with `kept: true` and its
own `at`. `handle_started` then uses `at` as the time (only 2 s to 7 days in the past;
a box with a wrong clock is not believed), so start times and the play log of an outage
are the real ones. The engine's own reports also carry `at`; it is NOT used for them
(no `kept`), so nothing changes on the stream server. When several kept reports go up
together, all but the newest carry `more: true`: they are recorded (queue, play log)
without a now-playing broadcast or a refill, so pages do not flick through the outage. Harness `local_box_offline`
checks the time check is recorded at its second although it was reported later.

## Stage 3a (2026-11-18): sound card output

`KBR_ALSA_DEVICE` in the engine's env (as `aplay -L` names it; needs `alsa-utils`)
adds `output.alsa` to radio.liq. Empty on the stream server: none of it runs there.

- The card has its own clock behind `buffer(buffer=0.2, max=0.6)`. The engine's clock
  stays the computer's, so timed items, Icecast and HLS are as without a card. The card
  is 0.1 to 0.6 s behind the stream. With `max=2.` the lag once grew to 1.5 s and stayed
  there (a buffer never drains), so the cap is what keeps FM's time check within a second.
- **A card cannot be brought back inside a running Liquidsoap 2.2.4.** Tried: with
  `settings.clock.allow_streaming_errors` a failed output's clock stops for good
  (`out.start()` later "starts" it with no sound); `out.start()` on a missing device
  raises an OCaml failure that `try/catch` does not catch and kills the thread. Without
  the setting a failed output shuts the whole engine down.
- So the engine asks first (`aplay ... -d 1 /dev/zero` must succeed) and leans on
  systemd (`Restart=always`): card there at start = use it, and if it fails later the
  engine exits and is started again; card not there at start = stream only, probe every
  10 s, `shutdown()` once when it answers so the restart uses it. Measured in a loop
  standing in for systemd: missing at start, appears (in use 20 s later), dies (engine
  back in 8 s without it), returns (in use 18 s later).
- Not solved: long-run drift between the computer's clock and the card's (the buffer
  absorbs 0.4 s; `buffer.adaptative` follows drift by resampling, at a tiny pitch cost).
  Decide on the real card.

Testing without a sound card: PulseAudio with a null sink is a paced ALSA device and
its monitor can be recorded (ALSA's own `null` device does not pace):

```bash
apt-get install -y pulseaudio pulseaudio-utils alsa-utils libasound2-plugins
pulseaudio --daemonize=yes --exit-idle-time=-1 --disallow-exit -n \
  --load="module-native-protocol-unix auth-anonymous=1 socket=/tmp/kbr-pulse.sock" \
  --load="module-null-sink sink_name=fm rate=44100" --load="module-always-sink"
chmod 666 /tmp/kbr-pulse.sock   # the harness runs the engine as "nobody"
cd backend && PULSE_SERVER=unix:/tmp/kbr-pulse.sock KBR_HARNESS_ALSA_DEVICE=pulse \
  uv run python ../infra/streaming/harness/run_harness.py --local-box next_twice local_box_sound_card
```

`local_box_sound_card` records the sink and the stream side by side: same items in the
same order through a Next and a hard time check, card within 1.5 s of the stream, no gaps.

## Stage 3b (2026-11-18): power cut, and one command to set a box up

**Power cut.** What the relay keeps is written to disk before it replaces the old file
(`fsync`, then rename), for the plan, the state and every audio file, so a cut leaves the
old file or the new one. Unreadable files mean an empty plan (emergency folder), never a
crash. Found while testing a cut:

- The relay's note "time check already handed to the engine" survived the cut, the
  engine's timed lane did not: the time check never aired. Now every `/hello` from the
  engine (it has just started) clears that note and the item is handed again.
- Soft timed items (sponsorships, weather) had no "too late" rule offline: after three
  hours without power they would all air in a row. Offline, a soft item more than 60 min
  late is dropped (`MISSED_SOFT_SECONDS`); hard ones keep the 5 min rule. **The backend
  has no such rule**: after an engine was away for hours `/next` serves every overdue
  soft item (told to the engine thread 2026-11-18).
- Clock: with no internet and a flat clock battery the computer wakes in the past. If the
  clock is more than 2 min behind the plan's `generated_at` (`clock_ok` in
  `/relay/status`), nothing timed airs, only music, and blackouts are judged at the
  plan's time at least. A clock that is wrong but later than the plan cannot be seen.
- Harness `local_box_power_loss`: engine killed (SIGKILL) and relay dropped 10 s after
  the hand-over with the line down, a torn `.part` file left, both started again:
  station music 0.1 s after the engine wrote sound again (about 10 s after "power on" here),
  the song on air at the cut not replayed, time check +0.7 s, once, recorded once.
- By hand, once per box: BIOS "power on after AC loss".

**`infra/local/setup-box.sh <station id> [sound card] [backend-url]`** (Ubuntu 24.04, as
root): Liquidsoap, a local Icecast (the engine needs one beside it), alsa-utils, the
relay; `/etc/kbr-engine/{env,relay.env,stations/<id>.env}`; a drop-in that starts the
engine after the relay and the sound system. Reruns keep every setting (token, card),
restart the relay freely (not heard) and the engine only when its script or settings
changed. A new radio.liq is checked first; if the engine does not answer within 90 s
with it, the old script goes back and the engine restarts on it. An engine restart is
still 10 to 30 s of silence on FM (see the note on restart time below): the gapless update is not built.

Tested without systemd: a stand-in `systemctl` on PATH that really starts and stops
Icecast, the relay and the engine from the files the script wrote
(`KBR_SETUP_FROM=<checkout>/infra KBR_SETUP_SKIP_APT=1 KBR_PLAYOUT_TOKEN=x`): first run,
unchanged rerun (nothing restarted), adding a card (`pulse`), a script with a syntax
error (refused, nothing restarted), a script that hangs at start (old one put back).
Not yet run on a real machine with real systemd.

## Stage 4a (2026-11-18): the box reports its health

With every command poll the relay sends `health` (`Relay.health()`, recomputed at most
every 30 s): `clock_ok`, `plan_age_seconds`, `plan_entries`, `plan_audio_missing`,
`reports_kept`, `disk_free_gb`, `engine_ok` (the engine beside it answers `/status`),
`up_seconds`. `playout_tunnel.report_health` keeps the known fields in memory;
`GET /playout-status` shows them as `box` for a relay station (`seen_seconds_ago` with
it; `None` until the box has reported to this process). Air watch (`_check_box`, also
during a live show) raises one alert per spell: `box_clock` (critical + push),
`box_disk` (under 5 GB free), `box_audio` (items of the plan not on the disk for 15
min). A box that stopped reporting is the existing `engine_drift` alert ("The local FM
box does not answer"). Not in the report yet: whether the sound card is in use (the
engine knows, its `/status` does not say; radio.liq is the engine thread's) and
silence on the card itself. No page shows `box` yet; the alerts are the interface.

## Updates on the box: decided 2026-11-18 (engine thread, via the coordinator)

No standby engine for gap-free updates: a second engine sharing the sound card is one
more way for two engines to play on the transmitter at once. A new radio.liq costs
10 to 30 s of quiet on FM, so setup-box.sh should apply it only in a quiet window (a
blackout within the next day, else 3 AM local) unless forced, and restart at a song
boundary. Built in stage 4b, below. If Shmuel says 10 s at 3 AM is not acceptable, the engine
thread builds standby (flag, `/takeover`, relay stops the old engine at a song boundary).

## Stage 4b (2026-11-18): the box updates itself in a quiet time

`setup-box.sh` installs `kbr-box-update.timer` (hourly at :35) and its service, which
fetches `setup-box.sh` of the release in `/etc/kbr-engine/box.env` (`KBR_BOX_REF`,
default `main`; `KBR_SETUP_REF=<tag>` on a hand run pins it) and runs it with
`KBR_SETUP_TIMER=1`. That run does nothing at all unless it is a quiet time: the relay
says `in_blackout`, or it is 3:05 to 3:50 at the station (`KBR_BOX_TZ`, from the
station's time zone; never across hh:00). So a merge to main reaches the box the next
night, or sooner inside Shabbos. `KBR_BOX_AUTO_UPDATE=0` switches the timer off.

Run by hand the script does everything at once except restarting an engine that is on
air: that waits for the quiet time ("a change is waiting") unless `KBR_APPLY=now`. A
first install, or an engine that is down, starts at once. Before any restart of a
running engine it waits up to 20 min for `/status` `main_remaining` to fall under 1.5 s,
so no song is cut; the gap on FM is the engine's start between two items. How long that
is depends on the machine: about 10 s in this test container, 23 to 28 s from stop to
sound on the one-core stream server (four reruns, 2026-11-18). Measure it on the box.
The check, the 90 s wait and the return of the old script are as before.

Tested with the stand-in `systemctl` (`KBR_FAKE_HHMM=0330` sets the station's clock for
the test): first install at noon starts at once; a card added by hand at noon waits;
the hourly look at noon touches nothing; at 3:30 it restarts and the card is in use;
again at 3:30 nothing; `KBR_APPLY=now` at noon restarts; with a stand-in engine whose
song had 25 s left the restart came 23 s later. Not tested: the real systemd timer.

Open: the box follows `main` by default, so every merge that touches `infra/` reaches
the transmitter the next night. Pin a tag before FM is fed from it.

## Live shows on the box

Since engine proto 5 (PR 319) the engine plays a live show from the backend's
`/playout/{station}/live.mp3` (the show as listeners hear it, after the broadcast delay).
On a box the engine's backend is the relay, which answered that path with 404 until
2026-11-18, so FM would have been silent for the whole show. The relay now passes
live.mp3 through as it arrives (`Relay.pass_live`, no buffering): 204 while no show is
heard or the backend cannot be reached (the engine's live lane stays quiet and asks
again), a refusal (403, 409) back to the engine as it came, and a stall of
`LIVE_STALL_SECONDS` (10 s) ends the pass-through. A live show needs the internet line:
with the line down the lane is quiet, never music under the host.

Checked with the real engine: harness `--local-box live_show_on_the_stream` 7 of 7 (host
heard through the relay, quiet during a stall, music back 0.9 s after); on the old relay
the same run failed with 0.0 s of host heard.

## Days offline (2026-11-19): a week without the internet

Shmuel, 2026-11-19: "extend the offline to work for several days". His father: if the
internet goes down someone must otherwise drive to BMG and program the day by hand.
The queue holds about two days (time checks, weather and program blocks are queued 48 h
ahead, music about as far), so on its own the box ran out on day three.

**`GET /playout/{id}/plan?days=8`** (`playout_plan.build_extension`, cached 5 min per
station) adds `extension` to the plan:
- `timed`: what the site would queue after the queue's own reach (48 h), worked out the
  same way: program blocks (`plan_blocks(..., prepare=False)`, sponsorship places from
  `plan_for_station`) and the built-in hourly time check, :30 check and weather.
  **Only recordings that exist**: the hour's own time clip, and the day's weather report
  only for a date that has one (uploaded per date; the forecast reaches about 5 days).
  A day with none has no weather item rather than an old report, and nothing is
  generated for it (ElevenLabs would write a far day's report from today's weather).
  A block whose programme is a shiur already queued or earlier that week is left out
  (with what announces it): each day's Daf Yomi is put in the block by the PC job and
  arrives only with the internet. Ids are stable uuid5-style hashes, so the box knows
  across fetches what aired. A projected item is not a queue row.
- `music` + `songs`: the station's songs by hour of its own day (what it queued for that
  hour, or played at it over the last 8 days), each with an engine URI whose
  `kbr_entry` is `template_entry` (all zeros); the box writes its own id in.
- `blackouts`: Shabbos and Yom Tov for 30 days.

**Relay:** fetches `?days=` (`KBR_OFFLINE_DAYS`, default 8) every 10 min
(`extension.json`), the plain plan every minute as before. Offline it serves, in order:
a paused shiur's resume; timed items whose time has come from the queue **and** the
extension (an extension item at a minute the queue already has is ignored); the queue's
next untimed item; and when the queue is used up a song drawn for the hour, the one
that has rested longest (none within 6 h if the hour's list allows). Hard items from
the extension go to the engine's timed lane like the queue's.

**Logged on return:** every kept start report carries `asset_id` (`served`, plan or
local item). `handle_started(kept=True, asset_id=...)` puts an entry that is not in the
queue on air as a stand-in, so it is logged at its real time: songs removed on the site
while the box was offline (the 16 h run: 20 of 20 aired unlogged) and the box's own
picks. Two catch-ups at once (sync loop and an engine ask) sent every kept report
twice, so every offline play had two log rows: `catch_up` now runs one at a time.

**Shiur paused offline:** the relay follows what is on air from the start reports.
Before handing over a hard group while a long item plays (plan `long_items`, the
station's rule): pause = a local resume item (`liq_cue_in` = cue-in + elapsed - 3 s, not
when under 30 s are left), engine `/flush`, the pre-loaded song put back to play later;
wait = the group is not handed over and airs (as soft) after the item. On the 16 h run
the Daf Yomi cut by the 8:30 PM check never came back.

**Station IDs offline** (`station_id` in plan entries): only after a song, not within
3 min of a timed item, not within 12 min of the last ID; two waiting IDs in a row, the
earlier goes. Before this, the box put IDs next to sponsorship breaks. Past the queue
the extension's `ids` (the station's `automation_config.station_ids`: jingles of its
category, min..max minutes, hours) let the box put one between its drawn songs
(`draw_id`), same rules; the 7-day run had none for five days before this.

**Back on the second after Shabbos:** the silence file is a minute long; in the last
minute of a blackout it gets `liq_cue_out` at the blackout's end (the 7-day run was back
37 s late).

**Week-old reports:** `STARTED_AT_MAX_LATE` is 15 days (was 7: the first plays of a
7-day outage were logged at the return).

`/relay/status` and the health report add `offline_days_ahead`, `songs_for_rotation`,
`songs_on_disk`, `timed_until`.

What it still cannot do offline: new recordings (each day's Daf Yomi, new shiurim
such as Weinberger, Dr. Dave, Rabbi Freedman-Mir) arrive only with the internet; weather
past the last forecast day is dropped; a live show needs the line; the box's songs are
drawn from what the station played at that hour, not from the schedule's playlists
(close, not identical); no hour fitting or soft-item pause of a shiur offline.

Simulator: `uv run python -m tests.sim.offline --start 2026-11-19T17:30 --cut 2026-11-19T18:00
--back 2026-11-26T18:00 --end 2026-11-26T20:00 --windows --weather-days 5` (7 days, a
Shabbos; `--offline-days 0` for the old behaviour).

## Known gaps (next stages; nothing is installed at BMG yet)


1. The box's own audio sent up to Icecast for online listeners (today the box's engine
   needs an Icecast beside it, as on the stream server). The sound card output is not
   yet tried on a real card: lag and drift over days need the real hardware.
2. For up to 40 s after a box's relay stops polling, its station's commands still go
   to the relay and fail before they fall back to HTTPS (`CONNECTED_SECONDS`). Air watch
   reports it as engine drift ("The local FM box does not answer") after its usual
   delay. Fine while a box has a station of its own; look again before it carries Main.
3. **Offline brain is simpler than the site's:** no `hard_by`, no hour fitting, no
   soft-item pause of a shiur. Long items and refill past the plan: see "Days offline".
4. **Alert for silence on the card** and the card's
   state in the health report, **RDS** (the encoder model is not known yet), a way for
   us to reach the box for maintenance (it has no inbound line).
5. Waiting on Shmuel: photos of the room (audio path, RDS encoder), who has access, UPS.
   He believes the room has wired internet.

Related: [streaming-architecture](streaming-architecture.md), [engine-harness](engine-harness.md),
[rds-feed](rds-feed.md).

## Line back: reports before asks (2026-11-19)
While kept start reports (or an owed hello) wait in the spool, the relay answers the engine from its local plan and sends the reports in the background (`catch_up_soon`). Only when they are up does it ask the site for the next item again. Before, it asked the site first, and the site handed out again what had already aired offline: in the simulator the 6 PM time check aired a second time at 6:24 PM, and 17 songs were repeated after a 16-hour outage.
