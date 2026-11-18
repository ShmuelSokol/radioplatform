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
station of its own, never under Main's id. (The same mistake on `/next` would still
split a queue: `/next` trusts the token alone, as it does for any engine.)
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

## Known gaps (next stages; nothing is installed at BMG yet)


1. The box's own audio sent up to Icecast for online listeners (today the box's engine
   needs an Icecast beside it, as on the stream server). The sound card output is not
   yet tried on a real card: lag and drift over days need the real hardware.
2. For up to 40 s after a box's relay stops polling, its station's commands still go
   to the relay and fail before they fall back to HTTPS (`CONNECTED_SECONDS`). Air watch
   reports it as engine drift ("The local FM box does not answer") after its usual
   delay. Fine while a box has a station of its own; look again before it carries Main.
3. **Offline brain is simple:** the queue as it stood. No long-item rule (a shiur is cut
   by a hard item, not paused), no `hard_by`, no hour fitting, no refill past the plan.
4. **Updates without a gap** (second engine started alongside, switch between songs,
   automatic return to the old one), **power-loss start-up**, **alerts** (FM silent, box
   offline, disk full), **RDS** (the encoder model is not known yet), **setup script**.
5. Waiting on Shmuel: photos of the room (audio path, RDS encoder), who has access, UPS.
   He believes the room has wired internet.

Related: [streaming-architecture](streaming-architecture.md), [engine-harness](engine-harness.md),
[rds-feed](rds-feed.md).
