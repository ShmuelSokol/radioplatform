# Holiday music

Shmuel, 2026-11-19: starting two weeks before each holiday the stations should sprinkle in
music and shiurim of that holiday; the normal schedule keeps running "with every so often a
holiday specific song"; he must see what is scheduled and control it ("starting it 4 weeks
before if he wants").

Page: `/admin/holiday-music` (Broadcast menu, "Holiday Music"). Code:
`services/holiday_music.py`, `api/v1/holiday_music.py`, `models/holiday_music.py`.

## Method

- **Lists** (`HolidayItem`, table `holiday_items`): per holiday, songs and shiurim/stories,
  for every station (`station_id` null) or one. Holidays: Rosh Hashana, Yom Kippur, Sukkos
  (through Simchas Torah), Chanukah, Tu B'Shvat, Purim, Pesach, Lag BaOmer, Shavuos
  (`HOLIDAYS`). Not built: the Three Weeks / Sefira (a cappella) — a different rule.
- **Holiday-only**: an item on any list with `all_year` false is kept out of every ordinary
  pick by `not_holiday_only()` (models/asset.py), beside `not_contract_audio()` in
  `fit_for_random_pick`, the playlist pool, rule fill, rule items, scheduling and program
  block categories. It is NOT in `not_placeholder()`, so songs added to a block by hand and
  an operator's own picks still play it. Adding an item takes its waiting automatic copies
  out of every queue. Note: holiday-only applies on all stations even when the item is
  listed for one station only.
- **Settings per station and holiday** (`HolidaySetting`, table `holiday_settings`; no row =
  `DEFAULTS`): on/off, `lead_days` (14), "1 in N" at the start (`start_every` 15, about one
  an hour), on the day before (`peak_every` 4), on the holiday's own days on the air
  (`during_every` 4: Chanukah, Chol Hamoed, Purim...), `include_shiurim`, `stop` end|start.
  The share (1/N) rises linearly per day from start to the day before (`share_on`).
  Own table, not automation_config: the scheduler pass rewrites that.
- **Where it acts**: `HolidayMix.pick(asset_type, at)` before each ordinary pick in
  `playlist_generator_service.generate` (template slots and the "any music" path; not a
  block's hand-added songs) and in `QueueReplenishService._holiday_instead` (rule fill, All
  Day Stories). Same kind only (`kind_of`): music for music, shiur/stories for
  shiur/stories; jingles, spots, time, weather, program blocks and sponsorships untouched.
- **Spacing**: a running credit adds the day's share per ordinary pick; at 1 a holiday item
  goes in, so 1 in 4 is every fourth pick, not a coin toss. The credit carries over between
  fills from the queue and the last 12 h of plays (`_carried_credit`): top-ups of one or two
  songs each started at random and the simulator heard four holiday songs in a row.
- **Redraw on change**: the queue is drawn about two days ahead, so a list or setting
  change that touches a day the queue holds (`touches_queue`, 4 days) draws the unplayed
  automatic songs again shortly after (`replace_automatic_songs`, background, one per
  station at a time). Without it, starting a mix "now" reached the air two days later.
- **Rest**: a holiday song not again within 3 h, a shiur/story within 24 h (`REST`; 6 h let Stories air its two Chanukah stories four times a day each). A short list
  then airs less often than the rate (credit capped at 1, no burst after).
- **Which holiday**: the one under way first, else the nearest coming (Yom Kippur's run-up
  overlaps Rosh Hashana's). Dates on the station's own calendar: Israel time zone = Israel
  (one-day Yom Tov, Pesach 7 days, Sukkos to 22 Tishrei, Shushan Purim); Purim in Adar II
  in a leap year. Days counted in the station's local date. Shabbos/Yom Tov silence as always.
- **Visibility**: the page lists each holiday's next dates, the mix's start and end, today's
  "1 in N", item counts, the day-by-day rates (opens on today), the list (scope, also all
  year, remove), Suggest from titles (`KEYWORDS`; skips Daf Yomi titles like "Pesachim 110"
  or "Megillah 7", categories daf_yomi/block_only, and "Chanukas Habayis") and library
  search. The queue API gives a waiting holiday item `holiday` (name) while in season; the
  control room shows a lilac tag on that row (`row-holiday`).
  The Playlists and Schedule blocks pages show `components/holiday/HolidayMixNote.tsx`
  while a mix is in season on a station ("Chanukah items are mixed in ... 1 in N", link to
  the page): the mix sits on top of every playlist, so it is no step in one.
- **Never two in a row**: `pick` refuses a holiday item right after one (`prev_holiday`,
  carried over a top-up), `hour_fit` never swaps a holiday item in, and after each top-up
  `space_out` parts two waiting holiday items with the next ordinary song of the same kind
  (the item on air, engine-held ones and the next two stay put).

## Tests

- `tests/test_holiday_music.py`: dates, ramp, spacing, rest, carried count, both fills,
  station scope, Israel calendar, queue tag, API.
- `tests/test_holiday_year.py` (~40 s): the stations' export with a list for every holiday
  and the year's real Shabbos/Yom Tov windows; six hours of every day of the coming year on
  all three stations through the real fills; rate per day, nothing out of season, kinds,
  nothing in the silence.
- `SIM_LONG=1 tests/test_holiday_sim.py`: played out in the simulator (time checks,
  sponsorships, Shabbos, overlaps): Erev Chanukah Friday into Shabbos on Main, after
  Chanukah, Jerusalem during Chanukah, Erev Pesach on Stories. `simulate(holiday_items=...)`
  seeds lists; `regenerate_windows=True` now also makes Yom Tov windows.
- `frontend/e2e/holiday-music.mjs`: the page and the queue tag, desktop and iPhone sizes,
  against a real backend.
