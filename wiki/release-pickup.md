# Release pickup (open tabs update themselves)

Every frontend build writes `dist/version.json` and bakes the same id into the bundle as `__APP_VERSION__` (`frontend/vite.config.ts`; id = `VERCEL_GIT_COMMIT_SHA`, else a build timestamp).

`frontend/src/lib/appUpdate.ts` (started from `main.tsx`) polls `/version.json` every 60s and on tab focus. When the id differs, the tab reloads itself at the first free moment:

- **never** while any audio element is playing or a microphone is live (it wraps `HTMLMediaElement.prototype.play` and `getUserMedia` to know, so new players are covered without registering anything);
- **never** while a player is switched on, even in the gap between items: `holdUpdates()` returns a release function; Listen/ListenV2 hold from the Listen tap, `useAudioEngine` while `isPlaying`;
- not within 2 minutes of any audio (covers players that do not hold). Before 2026-11-16 a gap between items (decks paused while the 7 PM time/weather loaded) plus 30 s idle reloaded listener tabs, and the reloaded page stayed silent because browsers need a tap to start audio again;
- not while a form field has focus;
- then: tab hidden, or 30s without input, or the person changes route (`UpdateOnNavigation` in `App.tsx`).

A stale lazy chunk (`vite:preloadError`) also triggers a reload unless audio is playing.

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
