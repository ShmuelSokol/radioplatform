# Station permissions and the protected station

Who may operate (change) which station. Reading is not restricted by station, except analytics (below).

- `User.station_ids` (JSON list of station ids, or null). Null = every station that is not protected. A list = only those stations. Admins are never limited.
- `Station.protected` (bool). A protected station (the FM feed) can be operated only by admins and by staff whose `station_ids` names it. "Every station" does NOT include it. Only an admin can set or clear the flag, or delete a protected station.
- The rule lives in `may_operate_station(user, station)` in `backend/app/core/dependencies.py`.
- Enforcement: `require_station_operator` is a router-level dependency on the queue router (`/stations/{id}/queue/...`) and the controls router (`/stations/{id}/controls/...`). It checks every non-GET request, on top of each route's role guard. Station edit and delete check it in `stations.py`.
- Screens: Users page (admin) has "Stations this person may operate"; Stations page has the Protected tick (admin). `/auth/me` returns `station_ids`. A refused action shows the app-wide "not allowed" notice.

- Routes that do not carry the station in the path (schedules, schedule blocks, playlist entries, playlists and generation, spot schedules, live shows) use `station_write_guard` in `backend/app/core/station_guard.py`: a router-level dependency that finds the station from `station_id` in the query or body (JSON, multipart or url-encoded form), or from the object named in the path or body (schedule, block, template, slot, show), and applies the same rule. A new write route on those routers is covered automatically if it uses the same parameter names; a new parameter name needs a line in that router's guard.

- Routes that check the station inside the handler with `may_operate_station`: program blocks, now-playing PATCH/DELETE (`require_station_operator` on the route), song-request approval, weather readouts (create, edit, regenerate, queue, delete), archives (create, edit, delete), live-show sockets (manager or host must also be allowed on the show's station).
- Queue entry lookups (move-up/down, reorder, reorder-dnd, DELETE) are filtered by the station in the URL, so another station's entry id cannot be changed through a station the login may operate.
- Rules that cover several stations: a blackout (holiday window) may be created, edited or deleted only by a login allowed on EVERY station it covers (`affected_stations` null = every active station, the protected one included). A weather spot or hourly time clip upload for a region needs every station of that region (default-region time clips are every station's fallback, so they need all stations).

- A spot schedule for all stations (`station_id` null) airs on the protected FM station too, so creating, changing or deleting one, widening one to all stations, or moving its end date from the Databank needs `may_operate_every_station` (admins, or staff allowed on every station including the protected ones). Routes: `spot_schedules.py` (`_check_every_station`) and `databank.py` end-date. Test: `tests/test_every_station_spots.py` (review finding F-DATABANK-58).

Not restricted by station: objects that belong to no station (shared playlist templates), the library, rules. Those are limited by role only. Reading is not restricted by station, with one exception: analytics (`api/v1/analytics.py::_stations_in_scope`) shows a login only the stations `may_operate_station` allows; asking for another station is 403, and "all stations" means all of theirs (test: `tests/test_analytics_queries.py::test_station_limited_manager_cannot_read_other_station`).

Role matrix for every write route: `backend/tests/test_write_role_matrix.py` (a new write route must be added to its policy table); frontend `auth/access.ts` is checked against it by `test_access_mirror.py`. `POST /assets/upload` is DJ-allowed (owner: DJs save Recording Studio takes); the Upload screen stays managers-only. The program clock's rule buttons are admin-only inside the screen (`canEditRules`); managers keep its Automation settings (station PUT).

Twilio webhooks (`/live-shows/twilio/inbound`, `/status`) check `X-Twilio-Signature` when `TWILIO_AUTH_TOKEN` is set (403 otherwise); the signed URL may be the forwarded https host or `BACKEND_PUBLIC_URL`. With no token set (local, tests) they stay open.

Tests: `backend/tests/test_station_permissions.py`, `backend/tests/test_station_write_permissions.py`, `backend/tests/test_websocket_auth.py`.

Queue entry routes (move-up/down, reorder, reorder-dnd, DELETE) look the entry up by id
AND the URL's station (`_station_entry` in `api/v1/queue.py`): by id alone a DJ allowed on
one station could change the protected station's queue through their own station's URL.
Test: `backend/tests/test_queue_endpoints.py::test_entries_of_another_station_cannot_be_changed_through_this_station`.
