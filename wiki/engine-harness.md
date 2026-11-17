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
song_spans_hour, soft_timed_waits_for_song, rapid_next, empty_queue. Exit code 0 when all
pass. Liquidsoap and Icecast refuse to run as root; the harness runs them as `nobody`.
Ports 8100, 8181, 8190, 8191, 8010: one run at a time per machine.

Findings: on main 41b33c8 block_shiur_spans_hour fails (time check never airs while the
shiur plays in the timed lane) and queued_shiur_spans_hour does not resume the shiur
(pause/resume was browser-player only).
