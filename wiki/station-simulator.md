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

Holiday lists: `simulate(..., holiday_items=[{holiday, asset_id, station_id}])` (with the items' assets in `extra_assets`); `regenerate_windows=True` makes Shabbos and Yom Tov windows. Scenarios in `tests/test_holiday_sim.py`, see [holiday-music](holiday-music.md).
