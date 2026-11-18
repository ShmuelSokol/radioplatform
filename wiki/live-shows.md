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
- **Pre-roll waits for a timed item** (`live_show_service.hold_for_timed_items`, `timed_item_ends`, `LiveAir.hold_until`): if the delay fills while a timed item is on air, or a hard one is due before it fills, the show is heard only when that item (and the rest of its group) has ended, 45 s longer at most. The host's words wait in the delay, which is then longer than its length and comes back by itself. Before, the take-over cut a time check after 3.4 of 6 s (reviewer's harness `live_show_delay_engine_preroll_item`). Status carries `hold` (seconds left) and the Host Console says so.
- **A show nobody heard still ends**: the hard stop used to run only for a station with `live_show_id`, which is set at first air, so a show started with no microphone stayed "live" for ever. `scheduler_engine._stop_unheard_live_show` looks every 10 s per station for LIVE shows past their end.
- **Host gone** (`scheduler_engine._host_gone`, station `automation_config.live_host_gone_seconds`, default 15, 0 = never): while the station is the show's and no host audio arrives (page closed and the delay played out, a frozen connection, or a no-delay show started with no microphone), the station goes back to its queue after that many seconds, with a critical alert and an ntfy push. The show stays live: a host who presses Start Broadcasting again is back on air through the delay. A muted microphone sends silent frames and counts as present. Before, listeners had silence until the hard stop and air watch said nothing (a live show owns the air; Jerusalem 2026-11-18, 38 s). The seconds are counted from the host's last frame, also while what they said plays out of the delay (those words are never cut): with a delay as long as the limit the station goes back the moment the delay is empty. It used to start counting when the delay had played out, so listeners got the delay and then the whole limit as silence. A timed item (the time check) that the take-over cuts before half of it was heard goes back to waiting and airs whole over the delay (`live_show_service._air_cut_item_over_the_delay`); only items the station's `live_timed_items` rule airs there. The host's page is closed with code 4000 and `useHostAudio` goes back to "Start Broadcasting" on any close it did not ask for.
- **Stream server goes quiet first** (`LiveAir.on_soon`, `SOON` = 1 s): an engine station is told `live on` one second before listeners first hear the host, and again with the flush at take-over. Told only at first air, the engine played music under the host's first 2.5 s. A show ended inside that second gets `live off` from `on_closed`.
- **Control room badge** (`controlroom/liveBadge.ts`, asks `GET /live-shows/{id}/air` every 2 s): "SHOW OPEN, NO HOST", "FILLING DELAY, n s" (amber) and only then "LIVE, n s delay" or "LIVE, no delay" (red). It said LIVE from the moment the show was started.
- A timed item cut by a take-over is not queued to resume after the show (a stale time check came back after End), and one aired over the delay is dropped from the engine's timed lane.
- **Dump** (`POST /live-shows/{id}/dump`, `{drop_caller}`; Host Console and Call Screener, Shift+D): throws away the newest audio in the delay with 10 ms fades. 409 with a plain reason when the show is not on air, has no delay, or nothing is waiting.
- **Build-back**: under the target the delay refills: pauses play at half speed, speech 3 % slower. Speech is NOT resampled (that lowers the voice; a 1000 Hz test tone came out at 970 Hz): one cycle of the voice is repeated (or dropped when shrinking), found by autocorrelation (`_voice_period`, `_join`). Non-stop talk gains about 0.03 s a second, normal talk about 0.15.
- **Exit / build / length** (`POST /live-shows/{id}/delay` `{action: exit|build, seconds}`): for this show only. Before the show's hard stop the delay winds down by itself so the last words air before the cut: the delay may never be longer than a tenth of the time left (`WIND_RATE`, less 5 s), so a 15 s delay starts shrinking 2.5 minutes before the stop and a 40 s one 7 minutes before, and a show shorter than that never builds its full delay. Speech is hurried by up to 12 %. (It was a fixed 3 minutes, which takes back 21 s only: a 30 or 40 s delay was cut with 8 to 18 s still waiting.) A hard stop does not wait for the delay.
- **End show**: the host is cut from the input at once, what is in the delay plays out (wound down), then `release_station` hands the station back. Until then `live_show_id` stays set with the show already `ended`; the scheduler's stale-reference cleanup leaves it alone while `live_relay.airs` still has the show.
- **Host drops and returns**: the same delay line carries on (`finish_reason == "host left"` is undone). If it had already played out, the host is heard at once and the delay builds while they talk (`heard_now`), no second silent pre-roll.
- **Time check during a show** (`services/live_inserts.py`, called from the scheduler's live-show branch): hard timed items due in the next 90 s are decoded with ffmpeg to the show's PCM format and aired at `preempt_at` from the output side of the delay. The host keeps talking and loses nothing: their words wait, the delay is longer by the clip and shrinks back. The queue entry is closed as played with a play log row. Before this every timed item under a live show was lost.
- **Status**: `air_status` on the show's events socket twice a second (`mode` off / pre-roll / steady / building / shrinking / exiting / no delay / insert, `delay`, `safe`, `next_insert`), `air_dump` when anyone dumps; `GET /live-shows/{id}/air` for a page that just opened. The panel is `pages/admin/live/DelayPanel.tsx`.
- **Cough and Mute** (Host Console, 2026-11-18): Cough is silent while held (button or the C key), Mute mic stays off until pressed again. The page keeps sending frames, filled with zeros (`useHostAudio` `setMuted`): the host's frames are the clock that carries the callers, so a muted host must not stop sending.
- **Callers and the delay**: `POST .../calls/{id}/on-air` answers 409 `{code: "delay_not_safe", message}` while the delay could not cut a caller (status `safe` false); `?force=true` goes ahead. Both screens ask through `putCallerOnAirAsking` (confirm, then force).
- **Dump log** (`LiveDump`, `GET /live-shows/{id}/dumps`, `.../dumps/{dump_id}/audio`): every Dump is a row (who, when, seconds cut, caller dropped) with a WAV stored at `live-dumps/<show>/<dump>.wav`: the last 6 s listeners heard (`LiveAir.aired`), half a second of silence, then what was cut. Staff only (DJ and up), fetched with the sign-in and played from a blob, never linked. The file is stored in the background after the response, so Play can 404 for a second.
- **The show for the stream server** (`GET /playout/{station_id}/live.mp3`, `X-Playout-Token` header only; agreed with the engine thread): what listeners hear, after the delay, with Dumps and time checks, as MP3 CBR 192 kbps 44.1 kHz stereo. 204 while no show is heard, a stream while one is, end of file when the delay has played out. It reads a tap on the delay line's output (`LiveAir.tap`, fed by `_remember`, which every path to listeners passes). Three things that held audio back and are switched off: ffmpeg's input probing (2 s before the first byte: `-probesize 32 -analyzeduration 0 -fflags nobuffer`), the gzip middleware (64 KB lumps for a client that accepts gzip: the response carries `Content-Encoding: identity`), and proxy buffering (`X-Accel-Buffering: no`). The engine side (radio.liq live lane, proto 5) is the engine thread's; until it is live the engine stream and FM still do not carry a show.
- The delay lives in the web process's memory. A backend restart mid-show loses what was waiting and the host's page must reconnect.

Checks: `tests/test_live_delay.py` (audio whose sample values say when it was spoken, so each test can say exactly what reached listeners) and `frontend/e2e/live-delay.cjs <station id>` against a real backend: part A sends a scripted host microphone (a 1000 Hz "curse", Dump, and the listener socket must never receive it), part B presses every control in a browser on desktop and phone sizes, part C is the station settings, part D (`ONLY=desk`, `PLAYOUT_TOKEN` for live.mp3) Cough and Mute, the dump log, Put On Air while not safe. `e2e/web-call-in.cjs` sets the delay to 0 for its run.

## The show on the stream (engine proto 5, 2026-11-18)
Since the engine plays `/playout/{id}/live.mp3` its stream carries the show, so a page that plays the stream and also the socket's frames would play the host twice, seconds apart. `live_state` now carries `on_stream` (`StationLiveRelay.on_stream`: engine station, public stream on, engine proto 5 or more).
- **Listen page** (plays the stream): `useLiveAudio.setUsesStream(true)`. With `on_stream` it does not play the frames, does not mute the stream, and sends `{"type":"audio","on":false}` so the server stops sending it show audio (`live_relay.no_audio`). Without `on_stream` (older engine, or the stream failed and the page fell back to its own player) it behaves as before: stream muted, frames played.
- **Control room**: keeps playing the frames (the operator hears the host at once) and mutes its stream element while a show is on air.
- FM (a local box) gets the show the same way, through the engine.
- Check: `e2e/listen-page.mjs` part `livestream`.

## Recording (2026-11-18)
`services/live_recording.py`: every live show is recorded as it aired and lands in Archives as a draft. The recorder taps the output of the delay line (`LiveAir.tap`, the same tap the engine's live.mp3 reads), so the file is what listeners heard: after the delay, without anything dumped, with callers and time checks. Mono MP3 at 32 kbps (about 14 MB an hour; the storage's file limit is 50 MB). When the air closes the file is uploaded to `show-recordings/<show>/<id>.mp3` and a `ShowArchive` row is made with `is_published` false and `live_show_id`: a manager listens and publishes it on the Archives page. Under 20 s aired nothing is kept. A host who drops and returns after the delay had played out makes "(part 2)". Station setting `automation_config.live_record_shows` (default on; Stations page, "Record every live show"). A failed save raises a warning alert. The recording is written to a temp file on the web server while the show runs, so a backend restart mid-show loses that part.
Checks: `tests/test_live_recording.py` (a dumped 1000 Hz "curse" must not be in the file), `e2e/live-delay.cjs` part G (`ONLY=record`).

## Caller desk (2026-11-18)
- **The caller's topic and notes** (`pages/admin/live/CallerNote.tsx`): the topic a web caller types goes into `CallInRequest.notes`. It was stored and shown nowhere, so the host put callers on air blind. Every caller card on the Call Screener and the Host Console now shows it, editable in place (`PATCH .../calls/{id}`, which already broadcast `caller_updated` with the notes).
- **Hold** (`POST .../calls/{id}/hold`): the caller on air goes back to Approved, still connected and hearing the show. 409 when not on air.
- **Next caller** (`POST /live-shows/{id}/calls/next`): one press ends the call on air and puts the approved caller who waited longest on air. Nobody approved: it only ends the call. Refused like Put On Air while the delay is not safe (`?force=true` goes ahead), and then nobody is ended.
- Checks: `tests/test_live_caller_desk.py`, `e2e/live-delay.cjs` part F (`ONLY=callers`). The call-in limit (5 per address per 10 minutes) is kept in memory: several runs in a row need a backend restart.
- Events are broadcast before the request's transaction commits, so a page can show the new state a moment before a fresh read does.

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

## Carts on the Host Console

`pages/admin/live/CartPanel.tsx` shows the station's instant buttons (the control room's sets and pages, `useHotButtons`, kept in this browser) on the Host Console. A press plays the recording into the show: `useHostAudio.playCart` decodes it in the host's browser and adds it to the bus that also carries the microphone, so it reaches listeners through the broadcast delay and a Dump cuts it like speech. The host hears it too. Nothing on the server changed.

- The microphone goes through its own gain into the bus; Mute and Cough close that gain. Before, the outgoing frames were zeroed, which would have silenced a cart too.
- One cart at a time: a second press replaces the first, pressing the playing one (or Stop cart) stops it. Stop Broadcasting stops it.
- Off until the microphone is open (the audio context exists only then).
- Buttons are edited in the control room, not here.
- Check: `ONLY=carts node e2e/live-delay.cjs <station id>` (recordings are served by the test; listeners must get the cart's tone 3 s later, also with the mic muted).

## Talkback (talking to a caller off the air)

The screener (Call Screener) or the host (Host Console) presses **Talk off air** on a waiting or approved web caller. `hooks/useTalkback.ts` opens a second microphone capture and its own socket, `/ws/live/{show}/talk/{call}` (same sign-in rule as the host's microphone). The server sets `CallerLine.talk`:

- what the staff member says goes to that caller's socket only; the caller's microphone frames go back to the staff socket instead of the mix. Nothing passes `StationLiveRelay.audio`, so nothing can reach listeners or the delay.
- while it is set the caller does not get the show (two streams in one player would stutter); the caller's page is told `{"type":"talk","on":true}` and shows "You are not on the air".
- it ends when the staff member presses End talk, the caller goes **on air** (`caller_status` ends it first, staff socket closed with 4000), the caller leaves (4004), or someone else starts talking to the same caller (the first is closed with 4000 and goes back silently). A caller on air cannot be taken into a talk (4009): Hold first.
- on the Host Console the host's on-air microphone is muted for the length of the talk (`setMuted(micOff || coughing || talking)`), the screen says TALKING OFF AIR, and a mute set before the talk stays.
- Phone (Twilio) callers have no page socket, so they get the "not connected from the website" message.
- Checks: `tests/test_live_talkback.py`, `ONLY=talk node e2e/live-delay.cjs <station id>` (listeners must receive nothing of the screener's 1000 Hz or the caller's 700 Hz).

The show also waits (`hold_for_timed_items`) for a hard timed item due up to 8 s after its delay fills, not only 2 s: the player may start such an item early, and a take-over at that moment left 0.8 s of the time check on the stream.

Without ffmpeg on the machine `live_recording.start` records nothing and says so in the log (the GitHub runner has none).

## A server restart during a show

A deploy restarts the backend: every socket drops and the delay line (in memory) is gone. What keeps the show on air:

- `useHostAudio` keeps the microphone and its audio graph and opens the socket again every second for up to 60 s (`reconnecting`, the Host Console says "Reconnecting… keep talking"). Not after a close the server explained: 4000 (handed back, host gone), 4001/4003 (sign-in), 4004 (no show). Frames are sent only on a socket that has sent its auth message.
- The station still has `live_show_id`, so the new socket is "the host came back on a station already theirs": heard at once, the delay builds back while they talk, no second pre-roll.
- `_host_gone` counts from the new process's first look, so the default 15 s is the time the host has to be back.
- Lost: what was in the delay at that moment (up to the delay length of speech), the dump count, and the recording so far (the part after the restart is recorded as a second archive draft when it is 20 s or longer).
- Listener pages and caller pages reconnect by themselves (they did before).
- Check: `ONLY=restart BACKEND_RESTART=<command> node e2e/live-delay.cjs <station id>` (local; measured 0.8 s from the server answering to the host being connected again).

## Several callers on air

Up to four callers can be on air together (`live_show_service.MAX_CALLERS_ON_AIR`; it was one, refused with 409). The mix in `StationLiveRelay.audio` always carried any number: listeners get host + all callers, the host hears the callers, each caller hears the host and the others but not themselves. Both screens list one card per caller on air, each with Hold and End Call. Put On Air stays enabled until four are on. **Next caller ends everyone on air** and brings on the approved caller who waited longest. Checks: `tests/test_live_caller_desk.py`, `ONLY=multi node e2e/live-delay.cjs <station id>`.

## The second before the host is heard (engine stations)

`on_soon` quiets the stream 1 s before first air (`quieted_early`). The engine then asks `/next` with `live=1` while the station is not the show's yet. `handle_next` used to answer that with "the show is over", which brought the song back for about 2 s where the show should start; now it hands out nothing while `_show_about_to_be_heard`. `hold_for_timed_items` no longer lengthens the hold once the stream was quieted.

When the last words of a host who left have played out, `_host_gone_now` runs the host-gone check at once (from the air's `on_closed`) instead of waiting for the scheduler's 3 s pass.


## Staff chat (host and screener)

A short text line between the people working a show, never heard on air: `ChatPanel` (`pages/admin/live/ChatPanel.tsx`) on the Host Console and the Call Screener. `POST /live-shows/{id}/chat` (DJ and up) stores a `LiveChatMessage` and sends a `chat` event on the show's socket; `GET /live-shows/{id}/chat` gives the last 200, so a page opened later (or a show with the socket down, polled every 10 s) still has them. Lines are 1 to 500 characters after trimming. Another person's new line is lit for 8 s. The lines stay with the show. Browser check: `ONLY=chat` in `e2e/live-delay.cjs`.

## Caller history (repeat, priority and blocked callers)

A caller is known by `CallInRequest.caller_key`: `tel:<number>` for a phone call, `web:<id>` for a browser, where the id is a random value the call-in page keeps in that browser (`kbr_caller_id` in localStorage; older pages send none and such a call has no history). `services/caller_history.py` adds `history` to every call the screens get (the calls list, `show_state`, `caller_queued`): earlier calls, times on air, last call, and the staff's standing and note from `CallerProfile`.

- **Priority**: a badge, and first in Waiting on the Call Screener.
- **Blocked**: `PUT /live-shows/{show}/calls/{call}/caller` with `blocked` also rejects that person's waiting and approved calls in the show; one on air stays (End Call and Dump are the tools there). Later calls get 403 "This show cannot take your call." (a phone caller hears the no-show message), and a refused call does not count against the rate limit.
- The note "about the caller" belongs to the person, not the call, and shows on their later calls.
- `GET /caller-profiles` and `PUT /caller-profiles/{id}`: the list at the foot of the Call Screener, where a block is lifted when the person is not calling.

A web caller who clears their browser data is a new caller. The block is a convenience for the screener, who still sees every call; it is not a wall. The Host Console shows the history read only. Browser check: `ONLY=history` in `e2e/live-delay.cjs`; tests in `tests/test_caller_history.py`.
