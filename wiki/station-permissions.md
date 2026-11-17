# Station permissions and the protected station

Who may operate (change) which station. Reading is not restricted by station.

- `User.station_ids` (JSON list of station ids, or null). Null = every station that is not protected. A list = only those stations. Admins are never limited.
- `Station.protected` (bool). A protected station (the FM feed) can be operated only by admins and by staff whose `station_ids` names it. "Every station" does NOT include it. Only an admin can set or clear the flag, or delete a protected station.
- The rule lives in `may_operate_station(user, station)` in `backend/app/core/dependencies.py`.
- Enforcement: `require_station_operator` is a router-level dependency on the queue router (`/stations/{id}/queue/...`) and the controls router (`/stations/{id}/controls/...`). It checks every non-GET request, on top of each route's role guard. Station edit and delete check it in `stations.py`.
- Screens: Users page (admin) has "Stations this person may operate"; Stations page has the Protected tick (admin). `/auth/me` returns `station_ids`. A refused action shows the app-wide "not allowed" notice.

- Routes that do not carry the station in the path (schedules, schedule blocks, playlist entries, playlists and generation, spot schedules, live shows) use `station_write_guard` in `backend/app/core/station_guard.py`: a router-level dependency that finds the station from `station_id` in the query or body, or from the object named in the path or body (schedule, block, template, slot, show), and applies the same rule. A new write route on those routers is covered automatically if it uses the same parameter names; a new parameter name needs a line in that router's guard.

Not restricted by station: holidays (one window can cover several stations, `affected_stations`), objects that belong to no station (shared playlist templates, spot schedules for all stations), the library, rules. Those are limited by role only.

Tests: `backend/tests/test_station_permissions.py`.


Queue entry routes (move-up/down, reorder, reorder-dnd, DELETE) look the entry up by id
AND the URL's station (`_station_entry` in `api/v1/queue.py`): by id alone a DJ allowed on
one station could change the protected station's queue through their own station's URL.
Test: `backend/tests/test_queue_endpoints.py::test_entries_of_another_station_cannot_be_changed_through_this_station`.
