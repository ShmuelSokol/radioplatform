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
