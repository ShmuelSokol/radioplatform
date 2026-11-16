# Live listeners count

The Listen page heartbeats every 30 s (`/listeners/heartbeat`). A listener counts as live when their last heartbeat is under 90 s old and `ended_at` is empty.

- Stopping, or closing the page, ends the session at once: Stop calls `/listeners/disconnect`; `pagehide` sends a `navigator.sendBeacon` to `/listeners/leave` as text/plain (a beacon cannot make the CORS preflight that JSON needs). A heartbeat on an ended session (page restored from the back/forward cache) reopens it.
- Admin pages keep `/api/v1/listeners/ws` open. The server sends `{"type":"listeners"}` on every join and leave (no listener data on the socket) and the page refetches `/listeners/live` and `/listeners/today`. Polling every 30 s stays as the fallback.
- A phone that vanishes without a beacon still drops out after 90 s without a heartbeat.
