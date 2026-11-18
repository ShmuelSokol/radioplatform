# Engine harness (real Liquidsoap, locally)

`infra/streaming/harness/run_harness.py` runs the production `radio.liq` (Liquidsoap 2.2,
Ubuntu 24.04 package, same as the stream server) with Icecast, the real backend (uvicorn,
scheduler on) and its own Postgres database `kbr_harness` (dropped and made again each run).
A small proxy stands in for nginx (`/<station>/<cmd>` to the harbor). Items are sine tones,
one frequency each; the engine also writes its output to a WAV (`KBR_DEBUG_WAV`), so each
scenario checks what was really heard and when (zero-crossing frequency per 100 ms) and the
longest silence, besides the start times the engine reported to the backend.

    sudo apt-get install -y liquidsoap icecast2 ffmpeg
    cd backend && uv run python ../infra/streaming/harness/run_harness.py [scenario ...] [--keep]

Scenarios: block_shiur_spans_hour[_wait|_cut] (the 2026-11-16 9 PM case: a shiur inside a
hard block when the next hour's time check is due), queued_shiur_spans_hour[_wait|_cut],
song_spans_hour, soft_timed_waits_for_song, soft_pauses_long_shiur, soft_waits_for_short_shiur,
rapid_next, empty_queue, slow_started, three_item_timed_group, two_groups_0.3s_apart,
two_groups_1s_apart, backend_unreachable (the proxy answers 502: emergency files, no silence,
back on the queue by itself), control_endpoints (wrong/missing token 403, /timed id dedupe), slow_timed_file (the
time check file arrives after its time), queue_change_drops_preloaded, engine_starts_backend_down
(restarts the engine, so it runs last), resume_position and cue_points (a shiur whose pitch
rises with time, so the pitch heard gives the position played), icecast_output (format, name,
stream title; ffprobe of what a listener receives), track_level (replay_gain_db applied exactly once), song_with_level_muted_under_time_check (a song with its own level is still muted under a time check), timed_and_mix_over_levels (the level applies once on the timed lane and on mix-over), engine_crash_restart (crash with the backend up: music again at once, a time check handed over before the crash airs on time), duplicate_timed_id (one airing), blackout_ended_early (silence at once, music back within seconds), block_with_slow_shiur / block_with_very_slow_shiur (Daf Yomi after the announcement even when its file arrives late; ends with a direct engine /skip, since the site leaves a timed item alone on Next).
A full run takes about 25 minutes. Exit code 0 when all
pass. Liquidsoap and Icecast refuse to run as root; the harness runs them as `nobody`.
Ports 8100, 8181, 8190, 8191, 8010: one run at a time per machine.

Findings: on main 41b33c8 block_shiur_spans_hour fails (time check never airs while the
shiur plays in the timed lane) and queued_shiur_spans_hour does not resume the shiur
(pause/resume was browser-player only).

2026-11-17: on main after PR 181, 54 of 55 checks passed; the failure (wait rule, time check
dropped when the shiur ended exactly at the group) is fixed in PR 191. Scenarios run in one
stack one after another, so the song rotation carries over: a check like "the cut song not
heard again" can fail when the next song happens to be the same one (three_item_timed_group
passed alone).

Added 2026-11-17: `output_limiter` (true-peak limiter), `next_after_idle` (the backend's kept connection after 35 s idle), `next_twice` (a second Next 0.5 to 5 s after a Next; OPEN: lost within about 3 s, the crossfade window). `engine_crash_restart` also presses Next right after the restart. `fit_before_hour` (PR 221: a long item that would start just before a hard time check is swapped with a short song while the engine holds the next one; the time check stays on its second).

Added 2026-11-17 (Main cutover): `next_skips_one` (every Next moves exactly one item, also right after a natural change and with nothing queued), `engine_restart_15s_before_hour`, `engine_restart_across_hour`, `backend_restart_before_hour` (a hard time check airs once whatever restarts around it). `h.rows()` returns every queue row since the run began: look entries up by the ids `h.queue` returned, not by asset. A scenario that takes the backend away must leave it at least 15 s before a timed item to hand it over again.

`--local-box` puts the local FM box's relay (`infra/local/kbr_relay.py`) between the engine and the backend proxy; `local_box_offline` (only with that flag) cuts the line both ways (`backend_down` + `control_down`) across a hard time check. `local_box_sound_card` needs `KBR_HARNESS_ALSA_DEVICE` and a PulseAudio null sink (recipe in the page). See [local-fm-box](local-fm-box.md).
Added 2026-11-18: `resume_without_old_song` (a long item is cut by a block's shiur, the time check pauses the shiur: nothing of the cut item may be heard at the resume; live it was 2.8 s, here 0.1 s or nothing). The backend log is now appended to across backend restarts, so a later scenario no longer wipes the log of an earlier failure. Known test artifact: a scenario that rewrites the queue in the database at the instant of a natural song change can leave the engine one more pre-loaded song than the flush drops.
