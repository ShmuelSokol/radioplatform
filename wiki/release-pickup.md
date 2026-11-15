# Release pickup (open tabs update themselves)

Every frontend build writes `dist/version.json` and bakes the same id into the bundle as `__APP_VERSION__` (`frontend/vite.config.ts`; id = `VERCEL_GIT_COMMIT_SHA`, else a build timestamp).

`frontend/src/lib/appUpdate.ts` (started from `main.tsx`) polls `/version.json` every 60s and on tab focus. When the id differs, the tab reloads itself at the first free moment:

- **never** while any audio element is playing or a microphone is live (it wraps `HTMLMediaElement.prototype.play` and `getUserMedia` to know, so new players are covered without registering anything);
- not while a form field has focus;
- then: tab hidden, or 30s without input, or the person changes route (`UpdateOnNavigation` in `App.tsx`).

A stale lazy chunk (`vite:preloadError`) also triggers a reload unless audio is playing.

Consequences to remember:
- A listener who never stops playing stays on the old frontend. **Backend changes must stay compatible with the previous frontend** for at least one release.
- If the playout engine moves listeners to one stream, the rule still holds: a reload would cut the stream, so it waits.
- If audio is ever played through Web Audio buffer sources instead of media elements, `isAudioActive()` will not see it; extend it then.

Tested with Playwright fake clock against `vite preview`: no new release → no reload; idle → reload; playing → no reload and audio continues; after pause → reload; typing → no reload.
