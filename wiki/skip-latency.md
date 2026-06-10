# Skip / Now-Playing Latency

## The rule

Any endpoint that changes now-playing MUST broadcast over WebSocket immediately via `_broadcast_now_playing_entry()` (queue.py). Without it, clients keep playing the old track until the scheduler's 3s poll or the 10s REST refetch notices.

## Fixed 2026-06-10

- `POST /queue/skip` and `POST /queue/start` now broadcast instantly after commit (was: DB-only update, no WS push — the source of multi-second skip lag)
- Dashboard `audioAsset` now derives from `wsNowPlaying` (instant) instead of `queueData` (10s REST poll)

## Latency budget after fix

skip click → API (~200ms) → WS broadcast (instant) → client engine swap (instant if next track pre-loaded on inactive deck, ~0.5-2s cold load otherwise)

## Where things live

- Broadcast helper: `backend/app/api/v1/queue.py` `_broadcast_now_playing_entry` → `get_scheduler()._broadcast_queue_entry`
- Scheduler poll: `scheduler_engine.py` `check_interval = 3` + precise per-track timers
- Client reaction: `useAudioEngine` track-change effect (pre-loaded deck swap path)

See also: [audio-engine](audio-engine.md), [dashboard-timing](dashboard-timing.md), [hourly-slots](hourly-slots.md)
