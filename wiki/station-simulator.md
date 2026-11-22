# Station simulator (virtual clock)

`backend/tests/sim/` runs the real backend decisions (scheduler pass `_check_all_stations`,
`playout_engine.tick`, `handle_next` / `handle_started`, queue fill, program blocks, the
sponsorship planner, blackout fill) against a **virtual clock**, with a stand-in for the
stream engine. A broadcast hour takes about a minute (about 60x real time), so a whole day
of one station takes about 25 minutes and nothing waits for real air time.

- **Clock**: time-machine freezes `datetime.now()`; the simulator moves it. Every `app.*`
  module's `time` (any name, e.g. `_time`) is swapped for a shim whose `monotonic()` and
  `time()` follow the virtual clock; asyncio keeps the real one.
- **Engine** (`engine.py`), copied from `infra/streaming/radio.liq`: music lane with one
  item pre-loaded (asks /next as an item starts, after /flush, every 2 s while empty);
  timed lane (`/timed` groups take over on their second, cut the music; a timed item on
  air is cut too unless it just started or is about to end, as `timed_take_over`);
  `/skip`, `/timed_drop`; nothing to play = `emergency` (counted as dead air). Crossfade
  overlap is left out.
- **Setup** (`stations.py`): an export of production (PC thread, Supabase REST, read only;
  trimmed copy in `tests/sim/fixtures/`). Switches the export blanked (`<removed>`) are set
  true. Time clips (per hour and half hour, Lakewood and Jerusalem) and daily weather spots
  are made, since the export has no region metadata. The Daf Yomi blocks are one-day blocks
  the PC job makes each day: `run.py` switches them on every day.
- **Checks** (`checks.py`) over the play log: hard items on their second, time check at
  :00/:30 and saying the right hour, no dead air, nothing inside a shiur, same sponsor 20 min
  apart, station IDs, nothing from another station, soft breaks wait for the song, no entry
  twice, Shabbos/Yom Tov silence and its window against candle lighting / shekia + 72.
- **Run**: `cd backend && uv run python -m tests.sim.run --export tests/sim/fixtures/stations-2026-11-19.json --start 2026-11-19T00:00 --hours 24 --stations main`
  (prints every check, writes the play log CSV). CI runs three short windows in
  `tests/test_station_sim.py`; `SIM_LONG=1` adds a whole day per station.

## Without simulating

- `tests/test_year_plan.py`: the planning code asked for a whole year directly (seconds):
  every Shabbos and Yom Tov window for Lakewood and Jerusalem against candle lighting and
  shekia + 72, Yom Tov next to Shabbos as one window, one hourly time check per local hour
  across both clock changes and in half-hour zones, the stations' real program blocks.
- `tests/sim/playlog.py`: the same rule checks on a REAL play log export
  (`uv run python -m tests.sim.playlog export.json [since] [station id]`). Rows closed late
  are capped at the item's length; a stretch over 30 min with nothing logged is reported
  as a hole, not as dead air. A gap can also be an item that was never logged.

## Local FM box offline

`tests/sim/offline.py` puts the real relay (`infra/local/kbr_relay.py`, its `_call`,
`have` and `_post_engine` swapped) between the simulated engine and the backend, cuts the
line for hours while the site's queue is changed, and reports what aired, whether timed
items and Shabbos silence held, and what the site's play log holds after the return
(`uv run python -m tests.sim.offline --start .. --cut .. --back .. --end ..`).

`--quiet-pass N` (default 12 from the command line): scheduler passes every N s when
nothing is near (no item ending, no quarter-hour mark, nothing in the timed lane), every
3 s otherwise; about 100x real time instead of 60x.

What it cannot show: real audio, the real stream, iPhone playback, network and database
slowness, the engine's file downloads. Those stay with the engine harness
([engine-harness](engine-harness.md)) and live checks.

Findings 2026-11-19 are in the PR that added it and in the review thread's checklist.

## Scenarios with a hand on the station

`simulate(..., events=[(when, async fn(sim))], blocks={block id: fields})` changes something
at a chosen moment or in the export before it loads. `backend/tests/test_station_sim_scenarios.py`
(SIM_LONG=1, about half a minute each): every waiting song removed before the hour (V3-7),
the item on air and the pre-loaded one deleted (V3-34, V3-52m), a 15-minute song paused for
the time check and resumed (V1-117, V3-52i, F-ENGINE2-22), a block whose season ends at
midnight (V3-64). `--restarts N --seed S` on the command line restarts the backend or the
engine at random moments, half of them within seconds of a quarter-hour mark.

The site writes a play-log row when the *next* item starts. The item on air at the end of
a run, and an item a restart cut at 0 s, have no row; the play-log check leaves them out.

Omer count: the 49 clips are seeded with the time clips, and the check "Omer count after each hour's time check, the right day" runs on every simulation (during the Omer). Scenarios in `tests/test_omer_sim.py`, see [omer-count](omer-count.md).

Holiday lists: `simulate(..., holiday_items=[{holiday, asset_id, station_id}])` (with the items' assets in `extra_assets`); `regenerate_windows=True` makes Shabbos and Yom Tov windows. Scenarios in `tests/test_holiday_sim.py`, see [holiday-music](holiday-music.md).

## Live shows

`tests/sim/live.py` puts a host, the broadcast delay, Dump and callers into the simulator;
scenarios in `backend/tests/test_live_show_sim.py` (one runs in CI, the rest with SIM_LONG=1,
20 to 45 s each). What runs is the real code: the host's audio socket handler with a stand-in
socket, the station relay and its caller mixing, `LiveAir`, the timed inserts,
`live_show_service`, the scheduler's live checks. Replaced: the delay's pump (`LiveAir.step`
is called on the virtual clock, 20 times a virtual second), ffmpeg (a time check over the
delay is a clip of its asset's length), the engine's live lane (`FakeEngine` copies radio.liq:
`/live` on, the wait for the feed, a song ending within 2 s or 5 s at most, lanes standing
still, 12 s of allowed quiet after `/live` off).

The host "speaks numbers": each 50 ms frame is one value naming the frame, with half a second
of silence every five. A caller's frame is a value no host frame has. What comes out of the
delay is read back, so a check says which words aired, which a Dump cut and how late each
was heard. Six checks run on every simulation that has a show (`live.checks`): music stops
when the host is first heard, no silence while the show has the air, a Dump cuts those words
and only those, never early, the last words air and the station carries on at once, nothing
of the host after the show.

In a scenario: `show = Show(minutes=20)`, events `show.start(planned)`, `show.dump`, `show.end`,
`show.leave`; a caller is `live.SimCaller(show.host)` with `call`, `approve`, `on_air`, `hold`,
`hang_up`. `sim.restart_backend()` in mid show drops the socket and the host's page comes back
3 s later, as `useHostAudio` does.

Scenarios: across the hour with a Dump, going live seconds before a time check, no delay,
the hard stop, a host who disappears and comes back, backend restart, engine restart, a show
that runs into Shabbos, a show during a shiur, two stations, a caller. Not simulated: the
stream server's lag, real audio, listener sockets, phone callers, video.

Found on the first run (2026-11-20), fixed in the same PR: see [live-shows](live-shows.md),
"What the simulator found".

## Every Yom Tov of the year as broadcast days

`uv run python -m tests.sim.yomtov --list` prints, per station, the stretches of the next
12 months that hold a Yom Tov (windows from the site's own generator, Shabbos and Yom Tov
joined where they touch; windows less than 36 h apart run as one stretch).
`--station main --index 0 --out /tmp/yomtov` plays one from 3 h before candle lighting to 3 h
after the station is back and writes `summary-<station>-<index>.json` (per window: last item
before, silence start against candle lighting, silence items, anything else inside, first
item back) and the play log. `simulate(..., regenerate_windows=True)` computes the windows
for the run's dates (the fixture holds one Shabbos and its stations have no coordinates;
`checks.PLACES` has them). Run 2026-11-19 for 5787/5788: 21 stretches, about 1,300
station-hours, 10 to 20 minutes each, four at a time.

What makes days of silence fast: deep inside a window (more than 5 min from either end)
scheduler passes come every `3 x quiet_pass` s and the stand-in engine asks for a next song
once a minute instead of every 2 s (`Simulator._deep_in_blackout`). Before that a 3-day
window ran at about 16x.

Two mistakes of the stand-in found on the first run, both fixed:
- `/skip` ended only a music item. The real `skip_on_air` ends a timed item too, which is
  how the silence file on air is cut when a window ends. Without it the station "came back"
  when the 5-minute file ran out (the 36 s late return reported for Main on 2026-11-19 was
  this, not the station).
- A run of one station drew another station's sponsorships as filler: `seed()` loads only
  the kept stations' contracts and the export has no `sponsor_id` on assets, so
  `not_contract_audio()` did not know them. `seed()` now ties every contract's audio to its
  sponsor, as the site does. Loose copies of that audio (same title, another id; the site
  knows them by title through the contract) get the tie too.

Check added: "weather report is today's, never drawn as filler" (a weather clip airs only
on its own local date, in a timed place or block).


The "paused shiur" check counts resume entries against pauses, not per item: the Daf Yomi
airs every night and is paused at the same second each night, so a run over several days
has one `resume@N` entry per night for the same shiur. Counting them per item reported
"2 resume entries" on Main's Shabbos/Yom Kippur run (2026-11-19) where each night had one.

Check "queue page times hold" (2026-11-19): every 30 simulated minutes the run reads the queue as the control room does (`_get_queue_impl`) and keeps each waiting item's `estimated_start` for the next hour. Items that air more than 3 min from that time are findings. Automatic station IDs are left out, because they move on purpose. With the stand-in fixed (below), estimates held within about 30 s over an hour on Main.

The stand-in engine now drops a flushed entry from its `given` list as radio.liq does (`/flush`: unless it is on air). Before, the backend took every flushed song for "aired without a start report" and marked it played. That emptied places in the queue: about a quarter of items aired 4 to 7 min off their estimate. The hold rule also counted the flushed song as pre-loaded, which caused the rare 4 to 6 s of emergency music between songs in the Yom Tov runs. Neither happens with the real engine.

CI: `tests/test_station_sim.py` runs in its own job, `backend-sim`, beside `backend-test` (2026-11-22). In one job the suite reached 22 of its 30 minutes and PR 491 was cut off. A new scenario in that file adds to backend-sim only; keep the job under about 25 min, and split the file in two (`-k` or a second file in its own job) before it gets there.
