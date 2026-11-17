# Live listeners count

The Listen page heartbeats every 30 s (`/listeners/heartbeat`). A listener counts as live when their last heartbeat is under 90 s old and `ended_at` is empty.

- Stopping, or closing the page, ends the session at once: Stop calls `/listeners/disconnect`; `pagehide` sends a `navigator.sendBeacon` to `/listeners/leave` as text/plain (a beacon cannot make the CORS preflight that JSON needs). A heartbeat on an ended session (Stop then Listen on the same page, or a page restored from the back/forward cache) starts a new session: the ended row keeps its duration and its key gets a `~<8 chars>` suffix, so the gap is never counted. Duplicate rows for one key (two first heartbeats at once; no unique constraint) are tolerated: the newest is used, disconnect ends all. Malformed station id 422, unknown station 404.
- Admin pages keep `/api/v1/listeners/ws` open. The server sends `{"type":"listeners"}` on every join and leave (no listener data on the socket) and the page refetches `/listeners/live` and `/listeners/today`. Polling every 30 s stays as the fallback.
- A phone that vanishes without a beacon still drops out after 90 s without a heartbeat.
- A new session's location (ip-api.com, up to 3 s) is waited for only 0.1 s (`GEO_WAIT_SECONDS`); after that the heartbeat answers and `_fill_geo_later` writes the location into the row when the lookup ends. The address is from `app/core/client_ip.py` (see [client-ip](client-ip.md)).
- `/listeners/today` counts from each station's own local midnight (`Station.timezone`), not UTC midnight. `/listeners/history` days are still UTC.
