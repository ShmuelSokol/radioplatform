# HS, Spots mode, Panic, Errors (KBR Studio's control panel)

The old program's right-hand column has Auto, HS, Loop, Go next, Fade, Soft fade, Jingles/Songs/Blocks/Files, Log, Errors, Spots mode, Panic. No manual for KBR Studio exists online (searched 2026-11-17; the Read All / Store / Test / Traffic Announcement panel beside it is Magic RDS, the RDS encoder program), and Shmuel asked us to work the meanings out. Readings chosen, from the photos and what the same names mean in other automation:

- **HS = hard start**, a station-wide switch, on by default. Blocks.db flags some blocks with a yellow dot (the hard ones); HS decides whether hard means hard. Off: every pending timed item (time check, weather, hard blocks, soft-with-limit) becomes soft, so it waits for the song on air. Shabbos/Yom Tov silence (asset_type `silence`), resumed shiurim (`resume@…`) and live-show entries are never softened. `services/station_modes.py`: `soften_hard_items` runs on every scheduler pass while HS is off (new timed items keep arriving) and saves each entry's old `timing_mode`/`hard_by` in `automation_config.hs_softened`; HS on puts exactly those back. Limit: the playout engine is handed hard items 45 s ahead (`TIMED_LEAD_SECONDS`) and has no cancel, so a hard item due within 45 s of switching HS off still starts on the second.
- **Spots mode = sponsorships on or off**, on by default (`automation_config.spots_on`). Off: `plan_for_station` plans nothing, so `reconcile_spot_slots` removes pending ad slots (except those inside the 2-minute safety margin) and sponsor places inside blocks stay open (skipped). On: rebuilt from the spot schedules.
- **Panic**: `POST /queue/panic` cuts whatever is on air with no fade, even a timed item that Next protects (`_skip_current_locked(force=True)`), ends Loop, and starts the next item (filling the queue if empty). Engine stations: stop loop, flush, `engine_skip`. Writes an alert "Panic pressed in the control room". The button needs a second tap within 4 s.
- **Errors**: a lower-pane tab listing open alerts for this station (managers; alerts are manager-only), with Done to resolve.
- Switches: `POST /queue/modes {hard_start?, spots?}` (DJ and up); the queue body carries `modes`.

Databank toolbar: **Mpeg** = only MPEG audio files (.mp3/.mp2/.mpg), **Prsnt** = only playable items (file set, not `do_not_play`), **123** = click Rec in the status bar to go to a record number.

Not built: **Auto** (the station is always automated here; switching automation off would mean dead air), **BLK PRE SPOT** (a jingle before the sponsors is already an item you put before the sponsors item in a program block), Reload DBs / Log ? (no meaning on a website).

Tests: `backend/tests/test_station_modes.py`; `frontend/e2e/station-modes.mjs` (real backend, real audio, meters sampled every 200 ms around Panic and the switches).
