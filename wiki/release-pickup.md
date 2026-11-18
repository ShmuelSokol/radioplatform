# Release pickup (open tabs update themselves)

Every frontend build writes `dist/version.json` and bakes the same id into the bundle as `__APP_VERSION__` (`frontend/vite.config.ts`; id = `VERCEL_GIT_COMMIT_SHA`, else a build timestamp).

`frontend/src/lib/appUpdate.ts` (started from `main.tsx`) polls `/version.json` every 60s and on tab focus. When the id differs, the tab reloads itself at the first free moment:

- **never** while any audio element is playing or a microphone is live (it wraps `HTMLMediaElement.prototype.play` and `getUserMedia` to know, so new players are covered without registering anything);
- **never** while a player is switched on, even in the gap between items: `holdUpdates()` returns a release function; Listen holds from the Listen tap, `useAudioEngine` while `isPlaying` and its audio context is running (on the control room the station is nearly always on air, so holding on `isPlaying` alone kept operator tabs on an old release forever; with no click the context stays suspended). Checked by `e2e/audio-engine.mjs release-hold`;
- not within 2 minutes of any audio (covers players that do not hold). Before 2026-11-16 a gap between items (decks paused while the 7 PM time/weather loaded) plus 30 s idle reloaded listener tabs, and the reloaded page stayed silent because browsers need a tap to start audio again;
- not while a form field has focus;
- then: tab hidden, or 30s without input, or the person changes route (`UpdateOnNavigation` in `App.tsx`).
- back after 10+ min hidden with nothing sounding: reloads even with a player switched on, but not while a form field has focus.

Checked end to end by `e2e/stale-release.mjs`: it builds the site twice, serves it from `e2e/lib/release-server.mjs` (missing files answered with the site page, like the host), publishes the second build under an open page and opens every lazy screen of App.tsx. Chrome logs "Failed to load module script: Expected a JavaScript-or-Wasm module script..." in the console each time; that line is the failed request that triggers the move, not a sign that it did not happen.

A stale lazy chunk (`vite:preloadError` with a missing-file error, `isStaleCodeError`) also triggers a reload unless audio is playing. The host answers a missing file with the site page (200, text/html), not a 404, and each browser words that failure differently; `isStaleCodeError` must know every wording. Safari's ("'text/html' is not a valid JavaScript MIME type.") was missing until 2026-11-18, so an iPhone tab opened before a release showed "Something went wrong" with a Reload button. Vite raises the same event when a screen's code throws while loading; that is a real bug, so it is left to the error box ("Something went wrong") instead of being hidden behind a reload. If the screen errors anyway, the error box (inside `Layout`, around the page only) reloads 300 ms later unless sound is playing; while it plays the box says the station keeps playing and the page reloads into the new release by itself 2 s after the sound stops (`waitToReloadForStaleCode`). Until 2026-11-18 the box wrapped the whole layout, took the player down and reloaded into silence on an iPhone.

## Admin control (Settings > Site Updates, `/admin/site-updates`)

Backend `app/api/v1/site_release.py`, one row in table `site_release`. Every tab keeps a WebSocket to `/api/v1/ws/site`: it reports `{client_id, version, page, playing, browser, user (role), waiting}` (every 30 s and on change) and receives `{"type":"release","data":{mode, push, server_time}}` on connect and the moment an admin changes anything. If the socket is down the tab reads `GET /site-release` every minute.

- `PUT /site-release {mode}` (admin): `auto` = the quiet-moment rules above; `manual` = no tab reloads on its own (navigation and long-away reloads too). A stale lazy chunk still reloads in both modes, or the screen would be broken.
- `POST /site-release/push {version, at, include_playing}` (admin): tabs take release `version` once it is really served at `/version.json` (so a push made right after a merge waits for Vercel) and the time `at` has come (server clock; tabs correct their own clock from `server_time`). No idle wait. Typing still holds a tab. Tabs playing audio wait until they stop, unless `include_playing`: then they reload within 8 s after the next item change (`noteItemChange()` from `useAudioEngine`), or after 5 min of one long item, and `takeResume()` switches the player back on (`components/audio/ResumeAfterRelease.tsx`, used by Listen and the control room). Chrome/Edge allow that without a tap (same-origin reload keeps the autoplay permission); Safari/iPhone refuse, so the page shows "Tap to keep listening". A push with no version means "whatever is published" and lasts one hour.
- `DELETE /site-release/push` cancels. `GET /site-release/tabs` (admin) lists open tabs (in-memory, per backend process).
- A tab never reloads for a release twice within 2 minutes (`kbr_release_reload_at` in sessionStorage; `window.name` when the browser refuses sessionStorage, e.g. site data blocked, which used to switch the guard off), so a CDN still serving the old index cannot cause a loop. A stored time in the future (device clock set back) is ignored.

A Claude thread can push with the admin login: `POST https://api.kbrlive.com/api/v1/site-release/push` with `{"version": "<sha from https://kbrlive.com/version.json>"}`.

Browser check: fake site with two builds (v1, v2), Chromium with and without the autoplay gesture rule: manual mode ignores a release; push reloads a silent tab within seconds; a playing listener is left alone unless included, then reloads after the item change and plays on (or shows the tap prompt); a scheduled push waits for its time.

Consequences to remember:
- A listener who never stops playing stays on the old frontend. **Backend changes must stay compatible with the previous frontend** for at least one release.
- If the playout engine moves listeners to one stream, the rule still holds: a reload would cut the stream, so it waits.
- If audio is ever played through Web Audio buffer sources instead of media elements, `isAudioActive()` will not see it; extend it then.

Tested with Playwright fake clock against `vite preview`: no new release → no reload; idle → reload; playing → no reload and audio continues; paused 100 s (a gap) → no reload; paused 200 s → reload; typing → no reload.

## Server restarts (backend releases)

Every merge to main restarts the backend. Two rules keep that inaudible:

- **A restart does not cut the song.** `_resume_playback_on_startup` (`backend/app/main.py`) used to mark whatever was playing as played and start the next item, on every station, on every deploy. It now leaves an entry alone when `started_at + duration` is still in the future and only cleans up entries that ended while the server was down.
- **One scheduler at a time.** Railway keeps the old process alive until the new one is healthy, so two schedulers overlap. `SchedulerEngine._hold_lease` (table `scheduler_leases`, 15s lease renewed every 3s tick, released on shutdown) lets only one advance queues. A lease row, not `pg_advisory_lock`: the Supabase transaction pooler does not keep session locks.

Tested locally: second backend started beside the first → nothing on air changed; first stopped → second took the lease and advanced the next song exactly once.

Time limits (2 min reload guard, 5 min playing push, 90 s resume, 30 s idle, 2 min audio grace, 10 min away, push at on the server clock, unnamed push lasts 1 h, offline check retried) are pinned by `frontend/e2e/release-timing.mjs`, which runs the built site under Playwright's fake clock.
