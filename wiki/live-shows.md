# Live shows

How a host goes on air (built 2026-11-16, after Shmuel's first test shows never reached listeners).

## Flow
1. **Start** (`POST /live-shows/{id}/start`, `live_show_service.start_show`): the show goes LIVE and the station gets `automation_config.live_show_id`. The item playing is stopped and queued again first as `resume@<seconds>` (see [hourly-slots](hourly-slots.md) for the resume mechanism), so nothing plays under the host. While `live_show_id` is set the scheduler skips dead-air recovery, so the queue stays quiet. A show started after its planned end gets its planned length from now (one hour when it has none).
2. **Host mic** (`useHostAudio`, Host Console "Start Broadcasting"): it captures the microphone, resamples it to 24 kHz mono 16-bit PCM and sends ~85 ms frames on `/ws/live/{show_id}/audio`. The first message must be a text `{"type":"auth","token":…}`. Never put the token in the URL: it lands in the access log, and the server drops the socket. Opening this socket also starts a show that is still "scheduled" (`start_if_scheduled`), because the Host Console's Start Broadcasting only opens the mic: on 2026-11-16 a live test left the show scheduled, nothing was paused, and the story under it came back 41 s further on instead of where it stopped.
3. **Relay** (`live_shows_ws.StationLiveRelay`): it passes each frame to every socket on `/ws/live/station/{station_id}/listen` (public), and sends `live_state` on connect and when the host connects or leaves. Everything lives in one web process, like now-playing.
4. **Listeners** (`useLiveAudio`, used by Listen and the control room): they play the frames with Web Audio (works in Safari and on iPhone) after a 0.35 s jitter buffer. While `onAir` is true the music player gets no item. After the show it waits for a fresh now-playing message rather than the stale one held when the show began.
5. **End** (`end_show`, or the scheduler's hard stop at `scheduled_end`): `live_show_id` is cleared, hard-timed items more than 2 min overdue are dropped (no stale time check), and `start_from_queue` restarts the station with the paused item.

## Gotchas
- Each scheduler pass ends with a rollback. Anything `_check_station` changes must be committed there. The hard stop was undone this way and repeated every 3 s.
- Icecast is still pushed to when `ICECAST_HOST` is set (the playout-engine server), but nothing depends on it.
- If the host closes the page without ending the show, listeners hear silence until the show is ended or reaches its hard stop.

Checks: `tests/test_live_show_on_air.py` and `tests/test_live_show_lifecycle.py`. A browser check (fake mic → Listen page) was run by hand on 2026-11-16.
