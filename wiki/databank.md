# Databank (KBR Studio's database window)

`/admin/databank` copies the "KBR Database 1.82" window of the station's old program: the library as plain black-and-yellow tables you sort by clicking a column, with Find, ALL, Edit mode, a headphones (pre-listen) button and a Page / Rec / View / Tot status bar. Tabs: Songs (Songs.db), Jingles, Spots (Spots.db), Blocks (Blocks.db), Log. Opened from the control room's **DB** button (phone: the Played pane's Databank button) and Content > Databank.

Backend: `api/v1/databank.py` (staff to read, managers to change).

- `GET /databank/items` pages the library (max 500 a page) sorted on the server by artist, title, type (= `category`), vote (V), length, intro, file, last_played or added. Last played is a grouped `max(play_log.start_utc)` per asset, for the chosen station. Dated time/weather recordings are left out, like the cart. Find is literal: `%`, `_` and `\` are escaped before LIKE ("track_01" finds only that).
- `GET /databank/spots` lists every `asset_type="spot"` with its category, length and an **end date** = the latest `end_date` of the spot schedules that air it (open-ended if any schedule has none). Expired (end before the station's today) is drawn red, like the old program. The old program kept the end date on the spot; here it lives on `SpotSchedule`, so `PATCH /databank/spots/{asset_id}/end-date` moves the end date of *every* schedule of that spot (on that station) and refreshes the queues the same way the Spot Schedule page does. A spot with no schedule answers 409. Without `station_id` the API answers 400 "Choose a station first" (unless every schedule of the spot is station-less); the page never asks that way: it sends the chosen station and says "Choose a station first." while none is chosen. Clearing the end date (empty box, or the iPhone wheel's Clear, which saves on leaving the box) asks first, since the spot then airs for ever.
- `GET /databank/types?asset_type=` lists every type (category) in the whole table, so the Type drop-down and the Edit-mode list offer types that only show past page 1.
- `GET /databank/log?station_id&day=YYYY-MM-DD` is one station-local day of `play_log`, oldest first, up to 5000 rows; the page shows each play as a Started and an Ended line to the millisecond (`controlroom/DayLog.tsx`), with the last 14 days in a list on the left (a drop-down on a phone). Lines are ordered by the moment to the millisecond (an end and a start in the same millisecond: Ended first). The list follows the station's today: a page left open over station midnight moves on to the new day within 30 s.

Blocks tab reads `/program-blocks` (managers) and shows one row per clock time. Week days are written the old way, **0 = Sunday … 6 = Saturday** ("012345" = Sunday to Friday), converted from our 0 = Monday by `kbrWeekdays()`. The yellow dot (Flg) means a hard block. Editing blocks stays on the Program Blocks page.

Dates on this page are day-first (14/12/2026), as the old program writes them; the rest of the site is US style.

Edit mode (managers): click or tap Artist, Title, Type, V or Intro on a song, Title, Category or End date on a spot; Enter saves (`PATCH /assets/{id}`), Esc cancels, leaving the box saves when changed, turning Edit mode off closes the box without saving. The edit box is 16 px on a phone (smaller makes iPhone Safari zoom). Rows still on screen while a new sort or page loads are dimmed and cannot be edited. A refused save shows the server's words (422 answers carry a list; `errText()` turns it into a sentence).

Mpeg / Prsnt filters and 123 (go to record): see [station-modes](station-modes.md). Not copied: BLK PRE SPOT, "blank" jingles of a set length.

Tests: `frontend/e2e/databank-ui.mjs` against the built site and a fake backend (every checklist row, desktop, iPhone 13/SE, landscape, iPad; `ONLY=<part>`), `frontend/e2e/databank-screen.mjs` (fake backend: end-date clear and no-station cases, phone tap sizes incl. iPhone SE), `frontend/e2e/databank.mjs` against a running site with data, `backend/tests/test_databank.py` `test_databank_rows.py` and `test_databank_more.py`.

Find matches what is typed: `%` and `_` (file names like track_01) are escaped, not LIKE wildcards. The Log tab follows a station change: if today was shown it moves to the new station's today (time zones differ), an earlier day is kept when the new list has it, and the highlighted line is cleared.

## Speed: "last played" is never joined in (2026-11-18)

`GET /databank/items` used to join a GROUP BY over the whole `play_logs` table into every
page. On production (9,046 items) one page took 7 to 12 s and the Databank sat on
"Loading... Rec 0 Tot 0". It could not be reproduced on a local Postgres with 1.5 million
plays (0.1 s there), so the exact production plan is not known; the play history was the only
thing the slow calls had and the fast `/databank/types` did not. Now the times come from
`services/last_played` (in memory per station, only new plays read), merged over the stations
when none is chosen; a last-played sort is worked out from that map and only the page's rows
are read, and only the columns the table shows. Do not join `play_logs` back in.
The page says "Still loading" after 4 s and shows a failure with "Try again".
Tests: `tests/test_databank_speed.py`, `e2e/display-findings.mjs` check 17.

## Tidy names

`Asset` trims title, artist and album on every write (`tidy_text` in `models/asset.py`):
tabs and line breaks become a space, ends trimmed, an empty artist or album becomes NULL.
Old rows: `backend/scripts/sql/tidy_asset_text.sql` (count first, then the update; by hand
after a backup).
