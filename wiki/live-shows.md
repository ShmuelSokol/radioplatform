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

## Phone call-ins: which show (2026-11-17)
`POST /live-shows/twilio/inbound` routes by the number dialled (`To`): a station whose
`broadcast_config.call_in_number` matches it (numerals compared) gets the call, and only its
LIVE show with calls on; none live there = the no-show message. A number no station claims
(the shared `TWILIO_VOICE_NUMBER`) keeps the old rule: the most recently started live show
with calls on. Test: `tests/test_alerts_calls_twilio.py::test_phone_call_reaches_the_dialled_station`.

## Web call-ins (2026-11-16, Shmuel chose these over phone lines for now)
A listener calls from `/call/<station id>` (linked from the Listen page's live banner when the show takes calls). No Twilio.
1. **Join** (`POST /call-in/{station_id}`, public, 5 per address per 10 min): creates a normal `CallInRequest` (`caller_phone` "Web caller", name, topic in `notes`) for the station's LIVE show with `calls_enabled`, and sends the screener a `caller_queued` event carrying the whole call. It returns a token, an HMAC of the call id with the JWT secret (`services/call_in.py`), so nothing new is stored.
2. **Caller socket** `/ws/live/call/{call_id}`: auth message with that token first, then the caller's mic in the host's format (24 kHz 16-bit mono PCM). The caller hears the show on the same socket the whole time.
3. **Mixing** (`StationLiveRelay.audio`): the host's frames are the clock. Each one is mixed with the same number of samples from every caller on air (each caller has a small buffer: 0.12 s before mixing in, 0.6 s at most). Listeners get host plus callers, so listener pages and the protocol are unchanged. The host gets the callers only, back down the host's own audio socket (`useHostAudio` plays them). Each caller gets everyone but themselves. Waiting and approved callers are never mixed in. If the host stops sending, nobody hears the callers.
4. **Screener**: approve, on air, end call and reject also call `live_relay.caller_status`, which mixes the caller in or out and updates their page. Ending the show ends every web call. A caller whose socket drops has 15 s to reconnect; after that the call is marked abandoned, or completed if they were on air.
- Echo: callers and the host should use headphones. Both use the browser's echo cancellation, but a speaker next to the mic feeds the show back in.
- Check: `frontend/e2e/web-call-in.cjs` (two Chromium browsers with fake mics at different tones, plus a listener socket; 13 checks) and `tests/test_web_call_in.py`.
