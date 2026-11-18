# Live shows

How a host goes on air (built 2026-11-16, after Shmuel's first test shows never reached listeners).

## Flow
1. **Start** (`POST /live-shows/{id}/start`, `live_show_service.start_show`): the show goes LIVE and the station gets `automation_config.live_show_id`. The item playing is stopped and queued again first as `resume@<seconds>` (see [hourly-slots](hourly-slots.md) for the resume mechanism), so nothing plays under the host. While `live_show_id` is set the scheduler skips dead-air recovery, so the queue stays quiet. A show started after its planned end gets its planned length from now (one hour when it has none).
2. **Host mic** (`useHostAudio`, Host Console "Start Broadcasting"): it captures the microphone, resamples it to 24 kHz mono 16-bit PCM and sends ~85 ms frames on `/ws/live/{show_id}/audio`. The first message must be a text `{"type":"auth","token":…}`. Never put the token in the URL: it lands in the access log, and the server drops the socket. Opening this socket also starts a show that is still "scheduled" (`start_if_scheduled`), because the Host Console's Start Broadcasting only opens the mic: on 2026-11-16 a live test left the show scheduled, nothing was paused, and the story under it came back 41 s further on instead of where it stopped.
3. **Relay** (`live_shows_ws.StationLiveRelay`): it passes each frame to every socket on `/ws/live/station/{station_id}/listen` (public), and sends `live_state` on connect and when the host connects or leaves. Everything lives in one web process, like now-playing.
4. **Listeners** (`useLiveAudio`, used by Listen and the control room): they play the frames with Web Audio (works in Safari and on iPhone) after a 0.35 s jitter buffer. While `onAir` is true the music player gets no item. After the show it waits for a fresh now-playing message rather than the stale one held when the show began.
5. **End** (`end_show`, or the scheduler's hard stop at `scheduled_end`): `live_show_id` is cleared, hard-timed items more than 2 min overdue are dropped (no stale time check), and `start_from_queue` restarts the station with the paused item. On an engine station the engine starts the next item itself, and every hard item whose time passed under the host is closed as `skipped` and dropped from the engine's timed lane (`playout_engine.close_timed_lost_to_live_show`), late by seconds or minutes, handed over or not: they never aired, no start report comes, and left `pending` the engine played them with the next hard group (a second time check at the next top of the hour).

## Broadcast delay, Dump and build-back (2026-11-18)
Shmuel: "live isn't live, it's 15 or 20 seconds delayed, so this way you could cut out curse words". Everything listeners hear of a show goes through `services/live_air.py` (`LiveAir`, one per station in `live_relay.airs`). Callers and the host still hear each other at once; only the listeners' copy is delayed (the mix is fed in after mixing, `StationLiveRelay._to_listeners`).
- **Settings** (station `automation_config`, Stations page, "Live shows"): `live_delay_seconds` (default 15, 0 to 40, 0 = straight through as before), `live_dump_seconds` (what one Dump cuts; 0 = everything waiting), `live_timed_items` (`time` default, `all`, `none`). Read by `live_air.settings_for`.
- **Pre-roll**: with a delay, starting a show does NOT pause the station. The music plays on while the delay fills; `live_state live:true` goes to listener pages and the station is taken over (`live_show_service.take_over_station`: `live_show_id`, pause the item, quiet the engine) only when the first delayed audio airs (`on_first_air` → `take_over_when_heard`). So `live_state` now means "listeners hear the show", not "a host is connected", and a show ended before the delay filled never touched the music.
- **Dump** (`POST /live-shows/{id}/dump`, `{drop_caller}`; Host Console and Call Screener, Shift+D): throws away the newest audio in the delay with 10 ms fades. 409 with a plain reason when the show is not on air, has no delay, or nothing is waiting.
- **Build-back**: under the target the delay refills: pauses play at half speed, speech 3 % slower. Speech is NOT resampled (that lowers the voice; a 1000 Hz test tone came out at 970 Hz): one cycle of the voice is repeated (or dropped when shrinking), found by autocorrelation (`_voice_period`, `_join`). Non-stop talk gains about 0.03 s a second, normal talk about 0.15.
- **Exit / build / length** (`POST /live-shows/{id}/delay` `{action: exit|build, seconds}`): for this show only. The delay also winds down by itself in the last 3 minutes before the show's hard stop (speech up to 12 % faster if it must), so the last words air before the cut. A hard stop does not wait for the delay.
- **End show**: the host is cut from the input at once, what is in the delay plays out (wound down), then `release_station` hands the station back. Until then `live_show_id` stays set with the show already `ended`; the scheduler's stale-reference cleanup leaves it alone while `live_relay.airs` still has the show.
- **Host drops and returns**: the same delay line carries on (`finish_reason == "host left"` is undone). If it had already played out, the host is heard at once and the delay builds while they talk (`heard_now`), no second silent pre-roll.
- **Time check during a show** (`services/live_inserts.py`, called from the scheduler's live-show branch): hard timed items due in the next 90 s are decoded with ffmpeg to the show's PCM format and aired at `preempt_at` from the output side of the delay. The host keeps talking and loses nothing: their words wait, the delay is longer by the clip and shrinks back. The queue entry is closed as played with a play log row. Before this every timed item under a live show was lost.
- **Status**: `air_status` on the show's events socket twice a second (`mode` off / pre-roll / steady / building / shrinking / exiting / no delay / insert, `delay`, `safe`, `next_insert`), `air_dump` when anyone dumps; `GET /live-shows/{id}/air` for a page that just opened. The panel is `pages/admin/live/DelayPanel.tsx`.
- The delay lives in the web process's memory. A backend restart mid-show loses what was waiting and the host's page must reconnect.
- Not yet: the engine stream and FM do not carry the show at all (listeners hear it in the page), so the delay is not on FM either.

Checks: `tests/test_live_delay.py` (audio whose sample values say when it was spoken, so each test can say exactly what reached listeners) and `frontend/e2e/live-delay.cjs <station id>` against a real backend: part A sends a scripted host microphone (a 1000 Hz "curse", Dump, and the listener socket must never receive it), part B presses every control in a browser on desktop and phone sizes, part C is the station settings. `e2e/web-call-in.cjs` sets the delay to 0 for its run.

## Gotchas
- `useLiveShowWS` reconnected after the page was left (the cleanup's `close()` fired `onclose`): a second socket listed every caller twice on the Host Console. The cleanup now clears `onclose`, and `caller_queued` ignores a call it already has.
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

Signed webhooks (2026-11-17): when `TWILIO_AUTH_TOKEN` is set, `/twilio/inbound` and `/twilio/status`
need a valid `X-Twilio-Signature` (403 otherwise). The URL checked is the one the app sees, the same
with https (Railway's proxy), and `BACKEND_PUBLIC_URL` + path, so `BACKEND_PUBLIC_URL` must be the
address the Twilio number's webhook is set to. Without the token nothing is checked (local use).
Test: `tests/test_auth_security.py::test_unsigned_twilio_webhook_is_refused`.
Phone callers on air are still not heard by anyone (V2-70, strict xfail
`tests/test_live_shows_websockets.py::test_phone_caller_on_air_is_heard`).

## Web call-ins (2026-11-16, Shmuel chose these over phone lines for now)
A listener calls from `/call/<station id>` (linked from the Listen page's live banner when the show takes calls). No Twilio.
1. **Join** (`POST /call-in/{station_id}`, public, 5 per address per 10 min): creates a normal `CallInRequest` (`caller_phone` "Web caller", name, topic in `notes`) for the station's LIVE show with `calls_enabled`, and sends the screener a `caller_queued` event carrying the whole call. It returns a token, an HMAC of the call id with the JWT secret (`services/call_in.py`), so nothing new is stored.
2. **Caller socket** `/ws/live/call/{call_id}`: auth message with that token first, then the caller's mic in the host's format (24 kHz 16-bit mono PCM). The caller hears the show on the same socket the whole time.
3. **Mixing** (`StationLiveRelay.audio`): the host's frames are the clock. Each one is mixed with the same number of samples from every caller on air (each caller has a small buffer: 0.12 s before mixing in, 0.6 s at most). Listeners get host plus callers, so listener pages and the protocol are unchanged. The host gets the callers only, back down the host's own audio socket (`useHostAudio` plays them). Each caller gets everyone but themselves. Waiting and approved callers are never mixed in. If the host stops sending, nobody hears the callers.
4. **Screener**: approve, on air, end call and reject also call `live_relay.caller_status`, which mixes the caller in or out and updates their page. Ending the show ends every web call. A caller whose socket drops has 15 s to reconnect; after that the call is marked abandoned, or completed if they were on air.
- Echo: callers and the host should use headphones. Both use the browser's echo cancellation, but a speaker next to the mic feeds the show back in.
- Check: `frontend/e2e/web-call-in.cjs` (two Chromium browsers with fake mics at different tones, plus a listener socket; screener and host console buttons; `API_URL`/`BASE_URL` point it at a local backend) and `tests/test_web_call_in.py`. The sockets themselves, end to end through the app: `tests/test_live_shows_websockets.py` (a small in-loop ASGI client, so the sockets share the test database session; the WS routes live under `/api/v1/ws/...`).
- Listener pages (and the control room) play host frames only between `live_state` live true and live false: a frame still in flight after the show ended used to play over the returning music. Check: `e2e/control-room-actions.mjs` part `live`.

## End of show cuts the host at once (2026-11-17)
`end_show` now takes the station off air in the relay (`live_relay.set_on_air(station, None)`), and the host socket loop drops frames once its show is no longer the one on air. Before, a host page left open kept sending the microphone to listeners for a minute after End show (Jerusalem live check). The Listen page also mutes the server stream element while `live.onAir` (the stream runs seconds behind). On an engine station the stream itself goes quiet: see [streaming-architecture](streaming-architecture.md).
