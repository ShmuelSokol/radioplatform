# Databank (KBR Studio's database window)

`/admin/databank` copies the "KBR Database 1.82" window of the station's old program: the library as plain black-and-yellow tables you sort by clicking a column, with Find, ALL, Edit mode, a headphones (pre-listen) button and a Page / Rec / View / Tot status bar. Tabs: Songs (Songs.db), Jingles, Spots (Spots.db), Blocks (Blocks.db), Log. Opened from the control room's **DB** button (phone: the Played pane's Databank button) and Content > Databank.

Backend: `api/v1/databank.py` (staff to read, managers to change).

- `GET /databank/items` pages the library (max 500 a page) sorted on the server by artist, title, type (= `category`), vote (V), length, intro, file, last_played or added. Last played is a grouped `max(play_log.start_utc)` per asset, for the chosen station. Dated time/weather recordings are left out, like the cart.
- `GET /databank/spots` lists every `asset_type="spot"` with its category, length and an **end date** = the latest `end_date` of the spot schedules that air it (open-ended if any schedule has none). Expired (end before the station's today) is drawn red, like the old program. The old program kept the end date on the spot; here it lives on `SpotSchedule`, so `PATCH /databank/spots/{asset_id}/end-date` moves the end date of *every* schedule of that spot (on that station) and refreshes the queues the same way the Spot Schedule page does. A spot with no schedule answers 409.
- `GET /databank/log?station_id&day=YYYY-MM-DD` is one station-local day of `play_log`, oldest first, up to 5000 rows; the page shows each play as a Started and an Ended line to the millisecond (`controlroom/DayLog.tsx`), with the last 14 days in a list on the left (a drop-down on a phone).

Blocks tab reads `/program-blocks` (managers) and shows one row per clock time. Week days are written the old way, **0 = Sunday … 6 = Saturday** ("012345" = Sunday to Friday), converted from our 0 = Monday by `kbrWeekdays()`. The yellow dot (Flg) means a hard block. Editing blocks stays on the Program Blocks page.

Dates on this page are day-first (14/12/2026), as the old program writes them; the rest of the site is US style.

Edit mode (managers): click or tap Artist, Title, Type, V or Intro on a song, Category or End date on a spot; Enter saves (`PATCH /assets/{id}`), Esc cancels.

Mpeg / Prsnt filters and 123 (go to record): see [station-modes](station-modes.md). Not copied: BLK PRE SPOT, "blank" jingles of a set length.

Test: `frontend/e2e/databank.mjs` against a running site with data (desktop and iPhone), `backend/tests/test_databank.py`.
