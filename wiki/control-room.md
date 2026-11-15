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

## Instant buttons are a cart: named sets of ten pages

Operators come from KBR Studio 1.82 (the desktop program the station runs today), whose jingle cart has pages 1 to 10 and named sets. `useHotButtons` in `shared.ts` stores `{ sets: [{ name, pages: CtlAsset[][10] }], set, page }` under the same localStorage key as before (`kbr_hot_buttons_<stationId>`); an old flat array is read as page 1 of "Default". The ☆ in the library adds to, or removes from, the page on screen. Still per browser, not shared between operators.

Queue rows show projected air time to the second (`clockTimeSec`), because that is what the old running log shows.

KBR Studio features the control room does not have yet, and why: Intro countdown (assets have no intro length), Fade and Soft fade (skip has no fade option; engine work), Mix over, Loop, HS, Spots mode, blocks that expand into their items in the log.

## PFL (pre-listen) is one shared off-air player

`usePfl` in `controlroom/pfl.ts` is the only off-air player for assets: the ▶ on library rows and the cart's PFL mode both use it, so starting one item stops the other. In PFL mode a cart button calls `pfl.toggle` and never `onSend`, so nothing reaches the station (`e2e/control-room.mjs` asserts no write call is made). It needs no server: it plays the asset's `audio-url` on its own audio element.

The headphones output uses `setSinkId` where the browser has it (Chrome, Edge; not iPhone Safari, where the picker is hidden). Browsers only name the outputs after the microphone has been allowed once, so "Choose the headphones output" asks for the microphone and releases it at once. The choice is kept in localStorage `kbr_pfl_output`; if that device is gone the sound falls back to the default output instead of going silent. The time and weather preview still uses its own audio elements and the default output.

Agreed with the engine thread (2026-11-15), not built yet: `queue/skip?fade=hard|fade|soft`, `intro_seconds` on assets and queue rows, `queue/loop`, `queue/mix-over`, `queue/insert {asset_id, after_entry_id}`, `queue/fire-now {asset_id, mode}`. Level meters stay measured in the browser.
