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
stream title; ffprobe of what a listener receives), track_level (replay_gain_db applied).
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
