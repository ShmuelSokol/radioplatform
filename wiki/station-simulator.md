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

What it cannot show: real audio, the real stream, iPhone playback, network and database
slowness, the engine's file downloads. Those stay with the engine harness
([engine-harness](engine-harness.md)) and live checks.

Findings 2026-11-19 are in the PR that added it and in the review thread's checklist.
