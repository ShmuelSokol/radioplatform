# Station permissions and the protected station

Who may operate (change) which station. Reading is not restricted by station.

- `User.station_ids` (JSON list of station ids, or null). Null = every station that is not protected. A list = only those stations. Admins are never limited.
- `Station.protected` (bool). A protected station (the FM feed) can be operated only by admins and by staff whose `station_ids` names it. "Every station" does NOT include it. Only an admin can set or clear the flag, or delete a protected station.
- The rule lives in `may_operate_station(user, station)` in `backend/app/core/dependencies.py`.
- Enforcement: `require_station_operator` is a router-level dependency on the queue router (`/stations/{id}/queue/...`) and the controls router (`/stations/{id}/controls/...`). It checks every non-GET request, on top of each route's role guard. Station edit and delete check it in `stations.py`.
- Screens: Users page (admin) has "Stations this person may operate"; Stations page has the Protected tick (admin). `/auth/me` returns `station_ids`. A refused action shows the app-wide "not allowed" notice.

Not covered yet (station id arrives in the query or body, not the path): schedules, schedule blocks, playlists/generation, spot schedules, live shows, holidays. Those are still limited by role only. When adding a write route for one station, call `may_operate_station`.

Tests: `backend/tests/test_station_permissions.py`.
