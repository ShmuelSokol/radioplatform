# Dashboard Elapsed Timer

## The bug (fixed 2026-05-24)

Elapsed timer counted past the track's actual duration (e.g., 36s ad showing 45s elapsed).

## Root cause

Timer was driven by `queueData` (REST API polling every **10 seconds**). When a track ended, the old `started_at`/`duration` stayed stale for up to 10s.

## Fix

1. Timer now uses **WebSocket** `started_at` + `ends_at` (real-time) instead of REST poll data
2. `Math.min(elapsed, serverDuration)` caps elapsed at track duration
3. Listen page uses `ends_at` to compute max duration cap

## Where

- Dashboard: `Dashboard.tsx` lines ~132-157 — `updateCountdown()` uses `wsNowPlaying` data
- Listen: `Listen.tsx` — elapsed capped via `wsEndsAt`

## Data flow

- `queueData` (REST `/queue` endpoint) — polls every 10s, has full queue + now_playing with asset.duration
- `wsNowPlaying` (WebSocket) — real-time, has started_at + ends_at but no asset.duration directly
- Dashboard now prefers WS for timing, falls back to REST

See also: [audio-engine](audio-engine.md)
