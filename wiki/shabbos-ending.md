# Last song before Shabbos (shabbos_ending)

Shmuel, 2026-11-19: "It's Time to Say Good Shabbos" is the last song before candle lighting
every Shabbos, ending exactly at candle lighting, followed by the shofar; then the Shabbos
silence (candle lighting to shekia + 72).

- **Setting**, per station: `automation_config.shabbos_ending` =
  `{enabled, song_asset_id, song_title, after_asset_id, after_title, shabbos (default true), yom_tov (default false)}`.
  Stations page, "Last song before Shabbos". Off on every station unless set.
- **Code**: `services/shabbos_ending.py`, `reconcile()` called every scheduler pass right after
  `fill_blackout_queue` (`scheduler_engine._check_station`).
- **Candle lighting = the blackout window's start** (`shabbos_service`: sunset - 18, - 40 for an
  Israel time zone). The ending reads the stored window, so a window not yet regenerated moves the
  song with it.
- **Song**: hard timed entry (`source="shabbos_end"`, `timing_mode="hard"`) at
  `start - (cue_out - cue_in)`; the engine plays a timed item between its analysed cues, so the
  audible end lands on candle lighting. Re-planned each pass (new analysis, regenerated window,
  changed setting) until 60 s before it starts (the engine is handed timed items 45 s ahead);
  after that it is left alone. Its other waiting copies in the queue go (it is a top hit), except
  one the engine already holds (or, on the browser player, the very next item).
- **Item after it (shofar)**: hard timed entry at the window's start, put *before* the blackout's
  silence entries in the same group (same `preempt_at`, lower position): the engine plays a group
  in position order. No item set = silence right after the song. Give it category
  `shabbos_ending` (in `NEVER_RANDOM_CATEGORIES`) or a jingle rotation slot can draw it.
- **Out of its way**: a hard group before the song keeps its items while they end 5 s before the
  song, the rest are deleted; hard items between the song and candle lighting are deleted; a soft
  break due in the last 5 min before the song moves to 10 min before it (deleted if that is past).
  `timed_groups.space_hard_groups` never moves the song.
- **Can end a few seconds early**: when the song on air before it ends 1-8 s before the
  ending song is due, the engine's hold lane starts the timed item early (radio.liq, "hold:
  timed item ... starts now") and the shofar and silence follow early too. hour_fit usually
  prevents it by reordering the songs before; the simulator's stand-in engine has no hold lane
  and logs those seconds as emergency music.
- **Windows**: Shabbos = reason "Shabbos", or a Yom Tov window starting on a Friday (station
  time). Erev Yom Tov only with `yom_tov`. Manual windows never.
- **Tests**: `tests/test_shabbos_ending.py`; simulator `tests/test_station_sim.py -k good_shabbos`
  (Main and Jerusalem, check "Erev Shabbos: song ends at candle lighting, shofar, then silence" in
  `tests/sim/checks.py`); `frontend/e2e/shabbos-ending-setting.mjs`.
- **Library (2026-11-19)**: song `e1ed49c2-10ad-411e-b58e-95c51d048a8e` (Lev Tahor, 279.48 s file,
  music from 0.23 s, silence from 276.52 s). No shofar recording yet (asked Shmuel).

## The queue around a blackout (`api/v1/queue.py`, every scheduler pass)

- **Inside**: timed items due inside the window are marked skipped (`_drop_timed_inside`),
  `shabbos_end` kept.
- **Last 20 minutes before**: a spoken item (spot, jingle, ID, time, weather...) whose estimated
  run on the queue's own timeline crosses the window's start is skipped (`_drop_spots_cut_by`):
  the silence would cut it mid-sentence. Songs still just fade.
- **Once it has begun**: every waiting row other than silence that sits before the window's
  first waiting silence, and every waiting row timed before the window, is skipped
  (`_drop_leftovers`), and so is the song the engine holds pre-loaded (it sits after the
  silences, where rows drawn for after the window also sit), followed by a flush. While a
  blackout lasts, `handle_next` hands the engine nothing but silence (None otherwise, as during
  a live show): a song handed then waited loaded and aired first after Shabbos (simulator,
  Jerusalem 2026-11-20). The blackout's end flushes and skips as before. The silences are hard
  timed, so they jump ahead of whatever still waited; those rows used to air first after
  Shabbos (2026-11-19 read: Main's Friday-afternoon block and a 21:15 sponsorship, Jerusalem's
  Erev Shabbos daf). Rows after the silences and rows timed after the window stay; what is
  drawn there (the Motzei Shabbos opening) is the playlist generator's job.
- Tests: `tests/test_blackout_drops_timed.py`, `tests/test_blackout_leftovers.py`.

Related: [station-simulator](station-simulator.md), [hourly-slots](hourly-slots.md), [streaming-architecture](streaming-architecture.md).
