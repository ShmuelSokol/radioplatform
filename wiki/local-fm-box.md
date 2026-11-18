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

## Known gaps (next stages; nothing is installed at BMG yet)

1. **Commands from the site** (Next, Play Now, flush, the online timed hand-over) are sent
   by the backend to `PLAYOUT_ENGINE_URL`, which needs an inbound line. The box will have
   none: the relay must hold an outbound connection and receive them. Until then
   `KBR_RELAY_TIMED=always` makes the relay do every timed hand-over itself.
2. **Sound card output** in radio.liq (`output.alsa` behind an env var), and the box's
   own audio sent up to Icecast for online listeners.
3. **Late start reports carry `at`**, but the backend still stamps them with the time
   they arrive: play-log times for the outage are wrong until `handle_started` uses it.
4. **Offline brain is simple:** the queue as it stood. No long-item rule (a shiur is cut
   by a hard item, not paused), no `hard_by`, no hour fitting, no refill past the plan.
5. **Updates without a gap** (second engine started alongside, switch between songs,
   automatic return to the old one), **power-loss start-up**, **alerts** (FM silent, box
   offline, disk full), **RDS** (the encoder model is not known yet), **setup script**.
6. Waiting on Shmuel: photos of the room (audio path, RDS encoder), who has access, UPS.
   He believes the room has wired internet.

Related: [streaming-architecture](streaming-architecture.md), [engine-harness](engine-harness.md),
[rds-feed](rds-feed.md).
