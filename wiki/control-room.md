# Control room (operator screen at /admin/dashboard)

`frontend/src/pages/admin/Dashboard.tsx` is the orchestrator; the panels live in
`frontend/src/pages/admin/controlroom/` (TransportBar, QueuePanel, LibraryPanel,
HotButtons, SidePanels = Played / Schedule / Go live).

Things the code alone will not tell you:

- **One file decides what "fire now" and "insert here" mean:** `frontend/src/api/liveControl.ts`.
- **Sends show up at once:** `send()` in `Dashboard.tsx` adds a placeholder row to the cached queue (`hooks/queueOptimistic.ts`, id prefix `optimistic-`) before calling `liveControl`, restores the old queue on error and refetches when the call settles. "Fire now" is not optimistic because the engine decides what is on air.
  Today they are composed from existing queue endpoints (fire now = `play-next` then `skip`;
  insert at a point = `add` then `reorder-dnd` to the target position). They are two calls,
  not atomic. When the playout engine ships real endpoints, change only that file.
- **Insert target is tracked by queue entry id, not position.** The position is looked up at
  send time, so the marker survives the queue advancing. If the marked entry has already
  started, the song is queued as next and the operator is told.
- **App-shell layout:** `Layout.tsx` renders `/admin/dashboard` in a full-viewport shell
  (`.app-shell`, `100dvh`, no max-width). Panels scroll internally; the page never scrolls,
  which is what keeps the nav + transport bar frozen. Add other full-screen tools to
  `isAppShell()` rather than fighting the default padded layout.
- **Desktop vs phone is a JS switch** (`useMediaQuery('(min-width: 1024px)')`), not CSS only:
  below 1024px there is one pane plus a bottom tab bar, so panels are not mounted twice.
- **Instant buttons ("My buttons") are stored in localStorage per station**
  (`kbr_hot_buttons_<stationId>`), so each device has its own set. Moving them to the
  server needs a backend table/endpoint; do NOT write them into `station.automation_config`
  from the browser, the scheduler rewrites that JSON and a full-object PUT can clobber it.
- **Library search is server-side** (`GET /assets?search=`), 50 per page. The old dashboard
  only searched the first 100 assets in the browser.
- **Test:** `frontend/e2e/control-room.mjs` drives the built site against an in-memory fake
  backend at desktop, iPhone and iPad sizes (Chromium emulation, not real Safari).

- **Track changes are pushed.** `Dashboard.tsx` watches the now-playing socket; when the asset or its start time changes it invalidates `queue`, `play-log` and `last-played` right away. The first message after opening a station is treated as the current state, not a change. Queue edits by another operator are not pushed, so the 10 s poll in `useQueue` is still needed. `e2e/control-room.mjs` fakes the socket with `routeWebSocket` and checks this.

## Queue list is windowed

`QueuePanel` draws only the rows on screen plus a margin, with spacer divs above and below, so scrolling costs the same at 50 items or 15,000. This depends on fixed heights: row 26px desktop / 40px on touch screens (phones, tablets, car screens; one line, title cut with an ellipsis, since 2026-11-18), insert marker 40px, action bar 61px. The constants at the top of `QueuePanel.tsx` must match the height classes on those elements; change one, change the other. `e2e/control-room.mjs` runs a 5,000-item queue and checks the scroll height to the pixel, so a mismatch fails there.

Consequences: a row far from the screen is not in the DOM (tests must scroll to it, and wait a beat after setting `scrollTop`). Date formatting goes through the cached formatters in `shared.ts` (`dayKey`, `clockTime`, `weekdayShort`); building an `Intl.DateTimeFormat` per row per render was a large part of the Safari stutter.

Not done: the queue request still returns up to 2,000 entries (about 930 KB at that size) every refresh. Paging it needs the day filter and insert-by-id to work on entries that are not loaded.

## The queue loads a few hours at a time

Each 10-second poll asks `/queue?limit=150` (`QUEUE_FIRST_ROWS` in `hooks/useQueue.ts`), a few hours ahead. A days-long queue (1,451 rows on KBR Jerusalem) was 708 KB a poll. Scrolling to the end of what is loaded, opening the day list, or picking a day switches that station to the whole queue (`QUEUE_ALL_ROWS`, 5000, the server's most); scrolling back to the top with no day picked drops back to the first rows. The query key stays `['queue', stationId]` so the optimistic updates are untouched; a change of limit just refetches.

## Instant buttons are a cart: named sets of ten pages

Operators come from KBR Studio 1.82 (the desktop program the station runs today), whose jingle cart has pages 1 to 10 and named sets. `useHotButtons` in `shared.ts` stores `{ sets: [{ name, pages: CtlAsset[][10] }], set, page }` under the same localStorage key as before (`kbr_hot_buttons_<stationId>`); an old flat array is read as page 1 of "Default". The ☆ in the library adds to, or removes from, the page on screen. Still per browser, not shared between operators.

Queue rows show projected air time to the second (`clockTimeSec`), because that is what the old running log shows.

HS, Spots mode, Panic and Errors: see [station-modes](station-modes.md). Still missing: blocks that expand into their items in the log. Its database window is the Databank page ([databank](databank.md)), opened from the DB button (right column, straight under Panic, so it stays on screen on a 1280x720 window).

## PFL (pre-listen) is one shared off-air player

`usePfl` in `controlroom/pfl.ts` is the only off-air player for assets: the ▶ on library rows and the cart's PFL mode both use it, so starting one item stops the other. In PFL mode a cart button calls `pfl.toggle` and never `onSend`, so nothing reaches the station (`e2e/control-room.mjs` asserts no write call is made). It needs no server: it plays the asset's `audio-url` on its own audio element.

The headphones output uses `setSinkId` where the browser has it (Chrome, Edge; not iPhone Safari, where the picker is hidden). Browsers only name the outputs after the microphone has been allowed once, so "Choose the headphones output" asks for the microphone and releases it at once. The choice is kept in localStorage `kbr_pfl_output`; if that device is gone the sound falls back to the default output instead of going silent. The time and weather preview plays through the same player: `pfl.playClips(WEATHER_PFL_ID, ...)` treats the time and weather clips as one item on one audio element (so an iPhone that allowed the first clip plays the second), with one timeline across both; seek maps to the clip and offset, and `pfl.position()` / `pfl.clipStarts()` feed the deck (no waveform, a ruler with a mark where each clip starts). It honours the headphones output too.

### The PFL deck (seek anywhere)

While an item is in the pre-listen player, `controlroom/PflDeck.tsx` docks above the cart (desktop) or above the bottom tabs (phone). It is a canvas waveform you click, tap or drag to jump; elapsed and remaining time; START / INTRO / MIX / END markers from `intro_seconds` and `metadata_extra.audio_analysis` (cue_in, cross_start, cue_out) with "Jump to" buttons (Mix and End jump 5 s before); keys when the wave has focus (←/→ 5 s, Shift 15 s, J/L 10 s, Space or K, 0-9 tenths, Home/End, Esc). Dashboard's global keys ignore events from inside the deck, so N there never skips on air. The waveform is decoded in the browser from the file (22.05 kHz offline context, cached per item), only for items up to 10 minutes; longer items get a time ruler, since decoding an hour-long shiur would take hundreds of MB. Seeking needs the storage to answer byte ranges (Supabase does; the e2e route serves 206 for that reason). `usePfl` gained `loadedId`, `paused`, `playPause`, `seek`, `getAudio`; at the end the deck stays open, paused.

A file the browser cannot play (MP2 audio saved as .mp3, which Chrome rejects; some old library files are like this) no longer closes the deck: `usePfl` sets `error` (`UNPLAYABLE_MESSAGE` for a media decode/format error or `NotSupportedError`, `NETWORK_MESSAGE` for a load failure) and the deck shows it in red in place of the waveform, with ▶ greyed out (on a network error ▶ loads it again). `NotAllowedError` (no click yet) just leaves it paused. The same files would fail in the in-browser on-air player too, so they must be converted (the PC is counting and transcoding them).

## Next, Fade, Soft fade, Intro, play now, insert

Server contract: [operator-actions](operator-actions.md). In the transport bar NEXT (and the N key) sends `skip?fade=hard`, Fade sends `fade`, Soft fade sends `soft`, matching KBR Studio's Go next / Fade / Soft fade. The Intro countdown shows only while `asset.intro_seconds - elapsed > 0` (nothing shows for items with no intro length set).

`liveControl.fireNow` is one `queue/fire-now` call with a `client_request_id`; a second tap on the same item within 1.5 s reuses the id, so the server answers it as a duplicate and the item plays once. `liveControl.insertAt` is one `queue/insert` call. The marker in the queue sits *before* a row, the API inserts *after* an entry, so Dashboard passes the pending row above the marker (null for the first row = top). The new insert call returns no scheduling-rule warnings, unlike the old add + reorder.

Loop is a toggle in the same group: `liveControl.loopCurrent` posts `queue/loop {on}`, and the button lights from `loop_asset_id` in the queue body, so every operator sees it.

The transport bar is one row from 1280 px (`xl`) and two rows below that (title and clock on top; countdown, Intro, sound and the Loop / Fade / Soft fade / NEXT group underneath). Both rows are in the DOM and one is hidden, so browser checks must pick the `:visible` button.

Mix over is the third cart mode (Play now / Play next / Mix over): a tap calls `liveControl.mixOver` (`queue/mix-over`, request id reused on a double tap like play now). The queue does not move, so the Dashboard makes no placeholder row. The browser path (socket `mix_over` message, `kbr-mix-over` event, `useAudioEngine`) is covered by `e2e/control-room.mjs`; it only plays once sound has been started on the page (Listen).

## Timed items: when the control-room player fades

In the browser player (no playout engine), the server decides when a timed item starts, but the control room fades the song's volume itself just before the cut. It must fade only when the server will really cut: a hard item at `preempt_at`, a soft item with a limit at `hard_by`. A soft item with no limit waits for the song to end, so no fade. `controlroom/timing.ts` `cutAt()` reads `hard_timed` and `hard_by` from the queue rows. Before 2026-11-15 it faded for every timed item, so a soft sponsorship at :45 left the song silent until it ended.

## No queue data is not "Stopped"

Until the queue has loaded, the bar says "Loading the queue…", and if the request keeps failing "Can't reach the station. Trying again…", in the title line and in the queue panel. Before 2026-11-16 a missing or failed queue showed "Stopped. Press Start to begin." and "The queue is empty", which an operator on a phone reads as the station being off the air.

## The KBR Studio look (classic.css)

**Sideways layout on car screens (2026-11-18).** `PHONE_LANDSCAPE` covers landscape windows up to 720 px tall and under 1024 px wide (it was 500 px): a car screen or small tablet held wide gets the transport column with the queue at full height beside it (11 rows at 960x540, was 2). On a phone held upright, Listen and the station picker share one row and the server/browser chip sits under PLAYING in short words (`PlayoutBadge short`). `e2e/control-room-compact.mjs` counts whole rows on screen, one-line titles, tap targets and moving meters at car, iPhone and desktop sizes on three stations.

The control room copies the station's old program (KBR Studio 1.82): grey bevelled buttons, a black LCD strip with green italic digits (Remaining, Intro, On air, Clock) and LEFT/RIGHT meters, a green "now" and yellow "next" title box, and the running log in bold yellow on black (current row blue with magenta text, timed rows orange). Desktop layout: log over the lower pane (Songs / other tabs), Go next / Fade / Soft fade and the pane tabs in a grey column on the right, cart pages 1 to 10 along the bottom. The styles are scoped under `.qn` in `controlroom/classic.css`; the virtualized log's row heights (`ROW_*` in QueuePanel) must match the CSS row heights or the scrollbar drifts.

The jingle wall and the Library's Jingle tab ask `/assets` for `hide_generated=true`, which leaves out the time and weather service's own recordings ("Time Announcement - …", "Weather Report - …"). Fired from the cart they would air the wrong hour or day.

## One tab holds the sound

The control room never starts sound by itself when it loads (`useAudioEngine(..., autoInit=false)`); sound starts only on a click in that tab. Tabs talk over the `kbr-control-room-sound` BroadcastChannel: the tab that takes the sound posts `taken`, and any other tab that had it goes quiet and says so. Action buttons (Next, Start, play now, mix) do not grab the sound while another tab holds it; only the speaker button moves it. The cart and the Library pass `hide_generated` so dated "Weather Report - …" and "Time Announcement - …" items stay out (they are type `spot`, not jingle).

Tap targets on public pages need a real 44 px box (`min-h-[44px]`): checkers measure the element's bounding box, so an invisible `::after` hit area does not count.

## Coming back from the background

Safari throttles timers in a hidden tab and can leave its socket dead with no close event, so a control room left behind a locked screen showed an item that had ended minutes earlier. On `visibilitychange` back to visible after more than 5 s, `useNowPlayingWS` reads `/now-playing` at once and opens a fresh socket, and the Dashboard refetches the queue. `lib/appUpdate.ts` also takes a waiting release on return after 10 minutes away if nothing is actually sounding, even while a player is switched on (otherwise a held player kept an old release for hours).

Native `<select>` on iPhone Safari ignores CSS height; the public station pickers use `.kbr-select` (`appearance: none`, 44 px, own arrow). The cloud test machine has no WebKit, so only the Mac can confirm it.

## Drag bar and dropdowns

On a computer, a drag bar (`data-testid="split-handle"`) sits between Up next and the lower pane (Songs, Played, Schedule, Live). Up next gets 15-85 % of the height. The choice is saved in localStorage `kbr-cr-split`; a double-click resets it to 60 %, and the arrow keys move it. Pointer events with `touch-action: none` make it work by touch (iPad). A phone shows one pane at a time, so there is nothing to split there.

Every `select.qn-field` in the control room is drawn grey with a raised arrow (`appearance: none`; Safari shows its own white or blue control otherwise). The floating 🐞 button is hidden on the control room because it covered the cart's buttons; Report a bug sits in the status bar (computer) and the menu (phone).

## Meters move without Listen

Since 2026-11-18 the meters show on every size, not only from 1024 px: phones, tablets and car screens get a small pair (`SmallMeters` in TransportBar: one-letter labels, no scale) in the LCD strip, and sideways in the transport column. Shmuel's Tesla browser is under 1024 px wide, so it never drew them; nothing was wrong with the audio. A 320 px phone (first iPhone SE) has no room beside the clock and shows none. Tests select the meters by `[data-testid=vu-meters]:visible [data-lit]`, since both sizes are in the DOM.

**Level meters show the air, with no Listen and no click (2026-11-19).** Shmuel: "It should always be showing the levels, even if you didn't hit listen, because that's part of running a station." On an engine station the meters come from the playout engine itself: `radio.liq` takes the RMS of the finished signal (after the limiter) every 0.1 s and posts ten values per side once a second to `POST /playout/{station}/levels` (token only, no database); the backend passes them to the station's now-playing sockets as `{"type":"levels"}` (`broadcast_levels`), and `hooks/useOnAirLevels.ts` plays them back on the meters about a second behind the air. Dead air is flat meters. With no levels for 3.5 s (a browser-player station such as All Day Stories, or an engine not yet running the new script) the meters fall back to this page's own player below, which needs one click; the meter box carries `data-meter-source` = `air`, `local` or `none`. Checked by `e2e/stream-keeps-playing.mjs meters`, `backend/tests/test_playout_levels.py` and the engine harness scenario `levels_feed`.

**The Listen button tells the truth (2026-11-19).** At 11:30 AM ET Shmuel opened the control room, saw PLAYING and a ticking clock, heard nothing and asked "why did stream stop?": the station was on air, his computer had never started the stream (no click yet), and nothing on screen said so. Now, when the station is on air and this device plays none of it, the button reads "Not playing here: Listen" (phone: "Silent here: Listen") and pulses; with listening remembered but no sound (after a reload, or the phone took the sound away) it reads "Sound stopped: tap to resume" (phone: "Tap to resume"); while the stream reconnects by itself, "Reconnecting…". The button keeps `data-testid="listen-here"`; `data-silent` says which case.

The audio engine always runs silently for the LEFT/RIGHT meters once the page has had any click or key (browsers need one gesture). Decks feed a bus; the bus feeds the meter analysers and an output gain to the speakers. Listen (`listenHere`, `setMonitor`) only opens that output gain on this computer; volume and mute act on it too, and deck gains carry replay gain only. In stream mode the engine still runs silently for the meters while the stream element makes the sound, so the meters lead the heard audio by the stream delay.

Listen is remembered across reloads (`kbr-cr-listen`, like Mute), and the first click or key anywhere turns the sound back on. Until sound really runs the header shows the Listen button, never Mute: `TransportAudio.audioReady` is "listening and the audio context runs (and, on a server-stream station, the stream element plays)", not just the remembered wish.

## Sound on an iPhone (2026-11-18)

iPhone Safari lets sound start only inside a real tap: `click`, `touchend` that was not a scroll, `keydown`. `touchstart` and `pointerdown` do not count. The Dashboard used to start the engine on the first `pointerdown`/`touchstart` and then stop listening for taps, so on an iPhone with listening remembered the one try was refused and no later tap gave sound (the Mute control showed over silence; Shmuel sat 3 minutes). Rules now:

- The page listens for `click` and `keydown` only, and keeps listening until sound is live (`soundLive`), and again whenever it stops being live (iOS sets the context to "interrupted" after a call or the lock screen; `initAudio` resumes any state that is not `running`).
- The Listen button (`data-listen-button`) handles its own tap: the window listener skips it, because starting there first re-draws the header before React's handler runs and the button's handler (which also lifts an old Mute) never fired.
- On a phone the control is `soundControl('phone' | 'land')` in `TransportBar.tsx`: a yellow 44 px "🔊 Listen" button, then "🔊 Sound on" / "🔇 Muted". Never an icon alone.
- Server stream: `takeSound` starts it inside the tap with `lib/liveStream.startLiveStream` (MSE, or the HLS copy on an iPhone), as the Listen page does. It used to be a plain `<audio src>` started from an effect, which Safari refuses, and the page then fell back to the built-in player without saying so. The stream runs only while this device listens. With no stream known at the tap, the stream element plays a moment of silence so it may start later without another tap.
- Joining mid-item: the engine gets the file address from the now-playing message (no `/audio-url` round trip), calls `play()` at once on the silent deck and goes live on `loadedmetadata`; an iPhone fetches nothing before `play()`, so waiting for `canplay` cost the 4 s limit. A deck started in the tap before its length is known is moved to the station's position at `loadedmetadata` (it started from the top and jumped at the next 10 s sync).

- "Starting…" (server stream on its way) shows for `STREAM_STARTING_MAX_MS` (8 s) at most, then the button says Listen again; a tap more than 4 s after a start that gave no sound starts the stream afresh. Whether the stream sounds is taken from the element's `playing`/`pause` events only, never from Mute: a `playing` event while muted (after a stall) once counted as "not sounding" and left the header on "Starting…" with no way to unmute.

Check: `e2e/control-room-iphone-sound.mjs`, Chromium with `IOS_SOUND_POLICY` (in `e2e/lib/fake-station.mjs`), which refuses `play()` and holds `resume()` outside a tap like Safari. It does not model "nothing loads before play()" or the HLS branch: a real iPhone is still the last word.

Meter scale: each side is the RMS level of the on-air bus in dBFS over about 46 ms (`getFloatTimeDomainData`, fftSize 2048), with a quick rise and a ~20 dB/s fall. 30 segments span -40 to 0 dBFS; red (last 6) is above -8 dBFS. The old meter averaged FFT bins times 2.5, which sat in the red on normal music. The e2e plays a -5.1 dBFS tone and expects 26 segments a side.

## Phone held sideways

When: `PHONE_LANDSCAPE` in `controlroom/shared.ts`, `(orientation: landscape) and
(max-height: 500px) and (max-width: 1023.98px)`, decided by `usePhoneLandscape()`. It
ignores the query while a text field is in use on a phone whose screen is upright
(Android shrinks the window for the keyboard, so an upright phone looks wide). The page
sets `html.cr-land` and `classic.css` follows that class, so there is no second copy of
the query in CSS. No `:has()` is needed for the layout: `Layout.tsx` gives the shell
`cr-shell`.

Layout (Shmuel, 2026-11-16 and 11-17: "unusable sideways"):
- Left column (42 %, 260-400 px) is `TransportBar` with `landscape`: one LCD row
  (status with elapsed / length, Remaining, Clock), on air and next, then a 4-column
  grid of 44 px buttons: Go next (2 cells), Fade, Soft fade / Loop, HS, Spots, Panic.
  Under that: the pre-listen deck when open (`PflDeck column`), then Listen, LIVE and
  the station. HS / Spots / Panic are not repeated under More. The column scrolls on
  its own; Go next and Panic are at the top.
- Right column: the queue (rows 50 px, one line of title, `ROW_LANDSCAPE`; the queue
  keeps the same top row when the phone turns) or the open pane, with 44 px tabs.
- Every control in the right column is at least 44 x 44 px (CSS under `html.cr-land`).
  The library search and type buttons share one row; while typing, the tabs hide so the
  keyboard leaves room for results.
- The page scrolls so the site menu sits just above the control room and starts
  scrolled to it. Safe areas: left and bottom on the transport, right on the queue.

Checked by `e2e/control-room-landscape.mjs` (iPhone SE / 14 / Pro Max with and without
Safari's bars, Pixel 7, Galaxy S21; tap sizes in every pane; every control; turning the
phone keeps state and sound; keyboard) and `e2e/control-room.mjs` `phoneLandscape`.

## Quick song actions (Song panel)

`controlroom/SongPanel.tsx`, opened from the Song button beside the on air line
(TransportBar `onNowSong`), a queue row's Song button, or a click on the playing row.
Shmuel, 2026-11-18: he wants to fix a song where he sees it, not in the Library.

- **Category** saves at once through `PATCH /assets/{id}`; queued copies are left alone.
- **Do not play** is `POST /assets/{id}/do-not-play` (`asset_service.set_do_not_play`):
  category `do_not_play` (already refused by every automatic pick, `models/asset.py`),
  the old category kept in `metadata_extra.category_before_do_not_play` so "Allow again"
  restores it, and the song's untimed waiting copies deleted on every station. It never
  deletes the copy on air, a copy in `playout_engine.held_entry_ids` (the engine's
  pre-loaded song) or a timed copy, so audio is never interrupted; the answer counts
  them in `still_to_play` and the toast says so. It does not go through the queue's
  own delete/Next paths and sends the engine nothing.
- **Not on this station** is `POST /assets/{id}/station-block` with the control room's
  station (`services/station_blocks.py`, table `station_blocked_assets`). The library is
  shared, so do-not-play takes a song off all three stations; this takes it off one.
  Shmuel was unsure which he wants (2026-11-18), so both are offered, this one first,
  each naming its reach on the button ("Only <station>" / "Every station") and each with
  its own undo. It removes that station's waiting copies with the same routine as
  do-not-play (`asset_service.drop_waiting_copies`), so the on air, engine-held and timed
  copies stay. Enforced where do-not-play is: every draw with a station (playlist
  generation, refill rule and fill draws, template slots, program block pools, last
  resort pick, request matching), the refill purge, and the play-time checks of both
  players (`_skip_placeholders`, `playout_engine.pick_next`, `playout_plan`). An
  operator's own queue entry still plays. A new pick path must add
  `Asset.id.notin_(blocked_asset_ids(station_id))` or it will draw blocked songs (they
  would still be skipped at play time). To drop the choice later: remove the button;
  rows in the table then only need deleting.
- Managers and admins change; DJs and viewers get the panel read-only (`isManager`).
- The transport's Song button spans the on air and next lines so it is a 44 px tap
  target on phones (the lines are 32 and 28 px); `e2e/control-room-landscape.mjs`
  fails any control under 44 x 44.
- The phone row action bar is fixed at 61 px (`ACTIONS_H`) and now holds five buttons;
  under 360 px "+ Insert before" shows as "+ Insert" (its aria-label stays).
- Checked by `e2e/control-room-song-actions.mjs`, `backend/tests/test_song_quick_actions.py`
  and `backend/tests/test_station_block.py`.
- The right-hand button column fits a 900 px high window (buttons below Soft fade are
  28 px under 1000 px of height); shorter windows scroll and a sticky "▼ more" strip
  (`cr-controls-more`) says so until the end is reached.
- The desktop row strip (Song, ▲, ▼, ✕) shows on hover, on a selected row and always on
  the playing row (Song only). Hover-only made it unreachable for a live browser check.
- The right-hand column (`aside[aria-label=Controls]`) is taller than the window at
  1366x900 and scrolls. Loop, Go next and the fades are `sticky top-0` inside it
  (`cr-transport-pinned`), so scrolling to the lower tabs no longer hides Loop.
- Hidden row strips are `pointer-events-none` only on devices without hover
  (`[@media(hover:hover)]:pointer-events-auto`): with a mouse the row is hovered before any
  click, so the buttons are there; on a touch screen an invisible ✕ must not take a tap.
- The desktop Time column is 124 px and never wraps: "12:02:47 AM Thu" wrapped at 92 px
  and the day name ran into the next 26 px row.

## Sponsor panel (tapping a sponsorship spot, 2026-11-18)

A queue row whose source is `ad_slot`, or an item of type `spot` that is not the weather or a time check (`isSponsorSpot` in `controlroom/shared.ts`), opens `controlroom/SponsorPanel.tsx` instead of the Song panel; the row's button reads "Spot". A row of the Sponsors list always opens it (`SongTarget.sponsorship`): the list also holds a sponsor's recording placed by a program block (source `block`, no spots category, only `sponsor_id` marks it), which opened the Song panel on Main until 2026-11-19; `GET /spot-schedules/station-day` now sends `asset.sponsor_id` too. It reads `GET /spot-schedules/for-asset/{asset_id}?station_id=` (`services/sponsor_item.py`, any staff role): the sponsor (from the contracts, else the recording's own `sponsor_id`), this station's contracts first and other stations' folded away, today's queue rows of the spot on this station, coming rows (12 at most), and the sponsor's missed plays for the last 7 days filtered to this station and recording (same report as the sponsor portal, [spot-schedules](spot-schedules.md)).

- **Skip / Put back** one coming play: `POST /stations/{id}/queue/{entry_id}/skip-sponsorship[?restore=true]` (DJ and up). The row is kept with status `cancelled`, not deleted: `reconcile_spot_slots` would plan a deleted play again at once. Only `ad_slot` rows due more than 2 minutes ahead (`SAFETY_MARGIN`); air watch's "skipped sponsorship" alert looks only at status `skipped`, so a skip by staff is quiet; the sponsor's report counts it as missed.
- **Pause / Resume** a contract is the existing `PUT /spot-schedules/{id}` with `is_active` (managers).
- "Recording options" opens the old Song panel for the same item (`SongTarget.itemOptions`).
- Links are shown only to roles that may open the page (`canAccess`). "Sponsor page" opens the client page `/admin/sponsors/<id>?tab=contracts`; with missed plays, "All of this sponsor's missed plays" opens `?tab=missed`. `/admin/spot-schedule?contract=<id>&station=<id>` marks that contract and opens the look-ahead on that station.
- Browser check: `e2e/control-room-sponsor-panel.mjs` (three stations, roles, phone sizes).
- **Finding it** (Shmuel asked "How do I navigate to the sponsorship panel?"): every sponsorship row carries an orange `SPONSOR ›` tag before its title (`row-sponsor`), one tap/click opens the panel without selecting the row. The tag is a `!min-h-0` button with an invisible taller hit area, so the phone 44 px rule never makes the row two lines (car screens, `control-room-compact.mjs`). The **Sponsors** tab (desktop side column; phone More → Sponsors) is `controlroom/SponsorsToday.tsx`: today's (station day) sponsorship plays on this station with time, sponsor, recording and status, from `GET /spot-schedules/station-day?station_id=` (`sponsor_item.station_day`, `is_sponsorship_clause`; same rule as `isSponsorSpot`); each row opens the panel. **What counts as a sponsorship**: an `ad_slot` row, or a type `spot` linked to a sponsor (`sponsor_id`, now in the queue payload), or one filed under a spots/sponsorship category; the weather report, time checks and zmanim are type spot too and never count unless a sponsor is linked (the "Weather Report" carried the tag live, 2026-11-18). The query uses plain ranges on `preempt_at` / `started_at` (a range on `coalesce(...)` read the whole queue history, 1-4 s live), and the Dashboard keeps the query alive (refetch every minute) so the tab paints at once. The panel's missed plays add today's `skipped` rows at once (with `local_time`); the delivery report counts a time of day only when it is over. Tab buttons are 24 px under 1000 px high so the side column still fits a 1366x900 window down to Errors. Checks: `ONLY=find` in the same script.

## Played list times

The Played list shows each start to the second on the station's own clock ("8:30:05 AM", `clockTimeSec` in `controlroom/shared.ts`), never the viewer's. The Databank Log tab shows the same moments to the millisecond. Check: `e2e/played-seconds.mjs` (also the RDS page's pickup log).

## Queue columns and "Why it is in the queue" (2026-11-19)

Shmuel, looking at Main's queue: drag column borders and double-click them to fit "like Excel", columns too far apart, "Type" not under its heading, and "what playlist is it playing from? ... click into the playlist and open up the reason why the song is playing".

- **Columns** (`controlroom/columns.tsx`, used by `QueuePanel` and `LibraryPanel`): every heading has a border to drag; a double-click fits the column to the widest entry drawn on screen (a hidden copy of each cell laid out at `max-content`; `scrollWidth` is never below the cell's width, so it cannot shrink a column). Widths are fixed (Title shrinks first when the panel is narrow) and the leftover room stays empty at the right, so Song type and Playing from sit next to the title. Kept in localStorage (`kbr-cr-queue-cols`, `kbr-cr-library-cols`), the same on every station; "Reset widths" appears once one was changed. Desktop layout only (>= 1024 px; phones have no headings).
- **Alignment**: the queue headings sit above the scroll area, so they get `padding-right` = the measured scrollbar width (17 px on Windows, 0 on a Mac) and the row cells have the headings' 4 px padding. That was "Type" sitting off its column.
- **"Song type"** (was "Type"): the song's category, the same thing the Playlists page calls "Song type" and playlist steps pick by; the kind (music, shiur) shows only when there is no category.
- **Playing from** (`controlroom/origin.ts`): the queue GET returns `block_id` on automatic songs (`QueueEntry.block_id`, the block the song was drawn for). The page looks the block up in `GET /schedules?station_id=` and its playlist in `GET /playlists`, and matches the song's kind/category to the playlist's steps. Rows without a block say what put them there by `source` and category: sponsorship, program block, time check / weather, added by hand, song request, loop, station ID, holiday list (`holiday` tag), rest of a paused item, last song before Shabbos, "Removed block", or "Station rules" (rule fill or no block). Timed items are named by what they are (`timedKind`: category `time_announcement` / `weather_spot` / `omer_count`, or the title), never "Station rules": the server-made "Time Announcement 20:30" says "Time check" and its Why points at the break settings. Order matters: a sponsorship or timed item is never labelled by a block.
- **Why panel** (`controlroom/WhyPanel.tsx`): opened from the Playing from cell (desktop; dotted underline and "›" show it is clickable) or the "Why" button among a hovered row's quick actions, the "Playing from: … Why ›" line above a tapped row's buttons (touch; `FROM_H` added to the windowed list's bar height), or the Song panel (viewers, who cannot select rows). It names schedule, block and hours, lights the step(s) the song filled, and links to `/admin/playlists?open=<id>&step=<slot>` and `/admin/schedules/<id>/blocks?block=<id>` (both pages open, scroll to and light that item). Links the role may not open are left out. Kept with `useNavState('why')`, so Back from the playlist reopens it.
- Test: `e2e/queue-columns.mjs` (fake backend, three stations, 17 px scrollbar, car screen, iPhone 13/SE, viewer).
