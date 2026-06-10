# V2 UI Toggle

## What it is

A site-wide V1/V2 UI switch (pill in the navbar, both versions) so the new design can be tested before deleting V1. State in `frontend/src/stores/uiStore.ts` (`ui_version` in localStorage).

## How it works

- `App.tsx` — `Versioned` helper renders V1 or V2 page per route
- `Layout.tsx` — V2 shell: `.v2-root` dark gradient, sticky glass navbar, per-route fade-in
- **V2-native routes** (full-bleed dark): `/stations`, `/listen/*`, `/admin/dashboard` (dashboard reuses the V1 console — it's already a dark purpose-built UI; forking 936 lines was rejected as maintenance rot)
- **All other routes**: V1 pages render unchanged inside a light `.v2-sheet` card so they stay readable on the dark shell

## V2 pages

- `pages/public/StationListV2.tsx` — glass cards, staggered fade-up, ON AIR pulse
- `pages/public/ListenV2.tsx` — spinning disc hero, progress bar from WS `started_at`/`ends_at`, equalizer, up-next, full feature parity with V1 Listen (stream + engine fallback, requests, CRM, raffles)

## Design system

`index.css`: `.v2-root`, `.v2-glass`, `.v2-glass-strong`, `.v2-sheet`, `.v2-fade-up`, `.v2-spin-slow`, `.v2-eq-bar`, `.v2-range`. Font: Inter (loaded in `index.html`). Palette: deep navy `#07071a` + violet/fuchsia gradients.

## Deploy note (Vercel CLI)

Device login (`vercel login`) requires `npx vercel@latest` — the machine's cached CLI 50.18.2 uses a deprecated device-flow API and returns "Could not verify user code" for every code. Codes expire ~10 min. After auth, deploy/whoami must also use `vercel@latest` or they won't see the saved credentials. Prefer a long-lived token from vercel.com/account/settings/tokens to avoid the login dance.

## Dev-against-prod testing

`frontend/vite.config.ts` reads `VITE_PROXY_TARGET` from `.env.local` (gitignored) to proxy `/api` to the production backend — avoids CORS and needing a local backend. Smoke test: `node v2-smoke.mjs` (Playwright, screenshots to `screenshots/v2-smoke/`).

See also: [audio-engine](audio-engine.md), [dashboard-timing](dashboard-timing.md)
