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

`QueuePanel` draws only the rows on screen plus a margin, with spacer divs above and below, so scrolling costs the same at 50 items or 15,000. This depends on fixed heights: row 40px desktop / 66px phone, insert marker 40px, action bar 61px. The constants at the top of `QueuePanel.tsx` must match the height classes on those elements; change one, change the other. `e2e/control-room.mjs` runs a 5,000-item queue and checks the scroll height to the pixel, so a mismatch fails there.

Consequences: a row far from the screen is not in the DOM (tests must scroll to it, and wait a beat after setting `scrollTop`). Date formatting goes through the cached formatters in `shared.ts` (`dayKey`, `clockTime`, `weekdayShort`); building an `Intl.DateTimeFormat` per row per render was a large part of the Safari stutter.

Not done: the queue request still returns up to 2,000 entries (about 930 KB at that size) every refresh. Paging it needs the day filter and insert-by-id to work on entries that are not loaded.

## The queue loads a few hours at a time

Each 10-second poll asks `/queue?limit=150` (`QUEUE_FIRST_ROWS` in `hooks/useQueue.ts`), a few hours ahead. A days-long queue (1,451 rows on KBR Jerusalem) was 708 KB a poll. Scrolling to the end of what is loaded, opening the day list, or picking a day switches that station to the whole queue (`QUEUE_ALL_ROWS`, 5000, the server's most); scrolling back to the top with no day picked drops back to the first rows. The query key stays `['queue', stationId]` so the optimistic updates are untouched; a change of limit just refetches.

## Instant buttons are a cart: named sets of ten pages

Operators come from KBR Studio 1.82 (the desktop program the station runs today), whose jingle cart has pages 1 to 10 and named sets. `useHotButtons` in `shared.ts` stores `{ sets: [{ name, pages: CtlAsset[][10] }], set, page }` under the same localStorage key as before (`kbr_hot_buttons_<stationId>`); an old flat array is read as page 1 of "Default". The ☆ in the library adds to, or removes from, the page on screen. Still per browser, not shared between operators.

Queue rows show projected air time to the second (`clockTimeSec`), because that is what the old running log shows.

KBR Studio features the control room does not have yet: HS, Spots mode, blocks that expand into their items in the log.

## PFL (pre-listen) is one shared off-air player

`usePfl` in `controlroom/pfl.ts` is the only off-air player for assets: the ▶ on library rows and the cart's PFL mode both use it, so starting one item stops the other. In PFL mode a cart button calls `pfl.toggle` and never `onSend`, so nothing reaches the station (`e2e/control-room.mjs` asserts no write call is made). It needs no server: it plays the asset's `audio-url` on its own audio element.

The headphones output uses `setSinkId` where the browser has it (Chrome, Edge; not iPhone Safari, where the picker is hidden). Browsers only name the outputs after the microphone has been allowed once, so "Choose the headphones output" asks for the microphone and releases it at once. The choice is kept in localStorage `kbr_pfl_output`; if that device is gone the sound falls back to the default output instead of going silent. The time and weather preview still uses its own audio elements and the default output.

### The PFL deck (seek anywhere)

While an item is in the pre-listen player, `controlroom/PflDeck.tsx` docks above the cart (desktop) or above the bottom tabs (phone). It is a canvas waveform you click, tap or drag to jump; elapsed and remaining time; START / INTRO / MIX / END markers from `intro_seconds` and `metadata_extra.audio_analysis` (cue_in, cross_start, cue_out) with "Jump to" buttons (Mix and End jump 5 s before); keys when the wave has focus (←/→ 5 s, Shift 15 s, J/L 10 s, Space or K, 0-9 tenths, Home/End, Esc). Dashboard's global keys ignore events from inside the deck, so N there never skips on air. The waveform is decoded in the browser from the file (22.05 kHz offline context, cached per item), only for items up to 10 minutes; longer items get a time ruler, since decoding an hour-long shiur would take hundreds of MB. Seeking needs the storage to answer byte ranges (Supabase does; the e2e route serves 206 for that reason). `usePfl` gained `loadedId`, `paused`, `playPause`, `seek`, `getAudio`; at the end the deck stays open, paused.

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

The audio engine always runs silently for the LEFT/RIGHT meters once the page has had any click or key (browsers need one gesture). Decks feed a bus; the bus feeds the meter analysers and an output gain to the speakers. Listen (`listenHere`, `setMonitor`) only opens that output gain on this computer; volume and mute act on it too, and deck gains carry replay gain only. In stream mode the engine still runs silently for the meters while the stream element makes the sound, so the meters lead the heard audio by the stream delay.

Listen is remembered across reloads (`kbr-cr-listen`, like Mute): the header keeps its speaker controls and the first click anywhere turns the sound back on.

Meter scale: each side is the RMS level of the on-air bus in dBFS over about 46 ms (`getFloatTimeDomainData`, fftSize 2048), with a quick rise and a ~20 dB/s fall. 30 segments span -40 to 0 dBFS; red (last 6) is above -8 dBFS. The old meter averaged FFT bins times 2.5, which sat in the red on normal music. The e2e plays a -5.1 dBFS tone and expects 26 segments a side.
