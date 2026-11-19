"""Station simulator: the real backend scheduling and playout code on a virtual clock.

See wiki/station-simulator.md. In short: `Simulator` builds a SQLite database, seeds the
stations (`stations.py`), freezes the wall clock with time-machine and moves it by hand,
runs the scheduler pass every few virtual seconds, and stands in for the playout engine
(`engine.py`, modelled on infra/streaming/radio.liq: a music lane that pre-loads one item
and a timed lane that takes over on the second). Everything the engine puts on air is
logged, and `checks.py` holds the station rules as checks over that log.
"""
