# Control room (operator screen at /admin/dashboard)

`frontend/src/pages/admin/Dashboard.tsx` is the orchestrator; the panels live in
`frontend/src/pages/admin/controlroom/` (TransportBar, QueuePanel, LibraryPanel,
HotButtons, SidePanels = Played / Schedule / Go live).

Things the code alone will not tell you:

- **One file decides what "fire now" and "insert here" mean:** `frontend/src/api/liveControl.ts`.
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
