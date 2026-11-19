# Omer count on the hour

Shmuel, 2026-11-19: "we need to announce the Omer on the hour and it switches to the next
Omer day after Shekia"; counted as the siddur, days only ("Hayom yom echad ba'omer ... We're
not counting days and nights, it's days only"; an earlier "Tonight is the 8th night" example
was withdrawn). Code: `backend/app/services/omer_count.py`, queue pass
`QueueReplenishService._schedule_omer_counts`, API `api/v1/omer_count.py`.

## The rule
- 49 days: day 1 = 16 Nisan (its night = the second Seder night), day 49 = 5 Sivan (Erev
  Shavuos). Same civil dates in Israel and the diaspora.
- Each station by its own place and clock: shekia from the station's coordinates, or
  Lakewood / Jerusalem by its time zone when it has none (`station_place`; production
  stations have no coordinates in the export). Day N is said from shekia (+ `switch_minutes`)
  of the evening before until the next shekia: "Today is the eighth day, which is one week
  and one day of the Omer.", night and day alike.
- Clips: 49 (`omer-NN`), asset_type jingle, category `omer_count`, `metadata_extra.omer_day`
  ("08"); shared by every station. Upload: `PUT /omer-count/{day}`. Wording: `wording()`;
  nothing added on Lag BaOmer (Shmuel). No clip = nothing for that hour (never a stand-in voice).
- Setting: `automation_config.omer_count = {enabled (default true), switch_minutes (0-90,
  default 0 = at shekia)}`; Stations page section "Omer count on the hour", which also shows
  how many recordings are uploaded and what the station says each hour of a chosen day
  (`GET /omer-count/station/{id}?on=YYYY-MM-DD`).

## In the queue
- Each hh:00 for the next 48 h, in the hour's timed group right after the time check and
  before the weather, timed like the time check (`default_timing`). `_after_time_check`
  reorders the group; `_put_first` (time check queued later) keeps a count already queued
  right behind the new time check.
- The hourly time-and-weather pass ignores a queued count when deciding whether an hour
  already has its time check (otherwise a count queued first blocked the time check).
- Each pass removes future counts that no longer fit (setting off, switch moved, blackout,
  new clip) and adds the missing ones. Outside the Omer (more than a day away) the pass
  returns before any database work.
- Never inside a blackout window (Shabbos / Yom Tov, `load_blackouts`), like the time check.
  2027: Lakewood's two days of Pesach run into Shabbos, so its first count is Motzei Shabbos
  (day 3); Jerusalem's first is Thursday night April 22 (day 1).
- `omer_count` is a ONE_MOMENT category (never random filler, dropped if queued as a song),
  no station ID right after it, not named on RDS.

## Tests
- `backend/tests/test_omer_count.py`: calendar, shekia switch in both cities, wording, the
  queue (order, repeats, Shabbos gap, missing clip, setting off, later switch), API, and the
  whole 2027 Omer planned day by day for Lakewood and Jerusalem against the generated windows
  (every hour checked).
- `backend/tests/test_omer_sim.py` (SIM_LONG=1): simulator scenarios at the shekia switch in
  both cities, the switch setting, setting off, Erev Shabbos to Motzei Shabbos, the first
  night after Pesach, Lag BaOmer, Erev Shavuos. The simulator check "Omer count after each
  hour's time check, the right day" (`tests/sim/checks.py`) runs on every simulation; the
  simulator seeds all 49 clips.
- `frontend/e2e/omer-count-setting.mjs`: the Stations page section, desktop and iPhone.

Shmuel 2026-11-19: no Lag BaOmer line; time check, Omer, weather is right. Still open:
shekia vs tzeis, "the eighth day" vs the siddur's literal "eight days". Clips are made on the voice PC
(project files `omer-count/`).
