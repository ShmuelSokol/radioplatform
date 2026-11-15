# Many stations, many listeners: load test and what it found

Tool: `backend/loadtests/multi_station.py`. Opens N now-playing WebSockets per station (what the Listen page uses), REST pollers (the page's fallback, every 3 s) and one operator per station polling the queue every 3 s (an open control room). Reports, per station, every song change, how many listeners got it, the spread between the first and last listener, and lag from the recorded start; plus REST latency. Exits non-zero on failure.

    cd backend && KBR_ADMIN_EMAIL=... KBR_ADMIN_PASSWORD=... uv run python loadtests/multi_station.py --base http://localhost:8000 --listeners 150 --seconds 120

Result 2026-11-15, local Postgres, one uvicorn process, load generator on the same 4-core machine: 6 stations x 150 listeners, all listeners switch within 17 ms of each other, last listener within ~0.65 s of the recorded start, REST p95 under 0.6 s. PASS.

What it found and fixed (same PR as the tool):
- The scheduler re-announced the song on air to every listener every 3 s tick (about 23 messages per listener per 90 s). `broadcast_now_playing_update` now sends only when the message changed, plus a heartbeat every 30 s (`UNCHANGED_RESEND_SECONDS`). Broadcasts go to listeners in parallel with a 5 s send timeout, so one stuck listener cannot delay the rest.
- A listener joining a station that plays from its queue got `data: null` (the WebSocket's first message read the old `now_playing` table, which queue stations never fill). It only learned the song from the next per-tick repeat. Now the first message is built by `SchedulerEngine.queue_entry_payload`, the same builder as the broadcast. The REST `GET /now-playing/{id}` (the page's polling fallback) answers with the same message for queue stations.
- That first message is cached per station for 5 s and cleared on every broadcast: after a release every listener reconnects at once and would otherwise each take one of the 15 database connections.
- The control room's `GET /stations/{id}/queue` (2000 entries, ~900 KB, every 3 s) was the main CPU cost: it now reads plain columns instead of ORM objects and encodes with `json.dumps`; gzip runs at level 5. 3 to 5 times cheaper; response unchanged.

Still true: everything runs in one process. The WebSocket listener list lives in that process, so a second web process (or replica) would leave its listeners without broadcasts. Scaling out needs a shared channel (Postgres LISTEN/NOTIFY or Redis) first.
